"""Build and query a compact H-1B sponsor index from official DOL LCA data."""

import gzip
import json
import os
import re
from collections import Counter, defaultdict

import requests
from openpyxl import load_workbook


DEFAULT_URL = "https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx"
CORPORATE_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "company", "co", "plc", "lp", "llp", "pbc", "usa", "us",
}


def normalize_company(value):
    words = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split()
    while words and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words)


def normalize_title(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split())


def _download(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".download"
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(temp, "wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    output.write(chunk)
    os.replace(temp, path)


def build_index(url=DEFAULT_URL, cache_dir=".cache/h1b"):
    """Download the cumulative fiscal-year workbook and aggregate certified H-1B LCAs."""
    os.makedirs(cache_dir, exist_ok=True)
    workbook_path = os.path.join(cache_dir, os.path.basename(url))
    index_path = os.path.join(cache_dir, "dol_index.json.gz")
    if os.path.exists(index_path):
        return index_path
    if not os.path.exists(workbook_path):
        print(f"Downloading official DOL LCA data: {url}")
        _download(url, workbook_path)

    totals = defaultdict(lambda: {"lca_count": 0, "worker_positions": 0})
    titles = defaultdict(Counter)
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    header = [str(value or "").strip() for value in next(rows)]
    columns = {name: header.index(name) for name in (
        "CASE_STATUS", "VISA_CLASS", "JOB_TITLE", "TOTAL_WORKER_POSITIONS", "EMPLOYER_NAME"
    )}
    for row in rows:
        if str(row[columns["VISA_CLASS"]] or "").upper() != "H-1B":
            continue
        if not str(row[columns["CASE_STATUS"]] or "").upper().startswith("CERTIFIED"):
            continue
        employer = normalize_company(row[columns["EMPLOYER_NAME"]])
        if not employer:
            continue
        title = normalize_title(row[columns["JOB_TITLE"]])
        positions = row[columns["TOTAL_WORKER_POSITIONS"]]
        try:
            positions = int(positions or 1)
        except (TypeError, ValueError):
            positions = 1
        totals[employer]["lca_count"] += 1
        totals[employer]["worker_positions"] += positions
        if title:
            titles[employer][title] += 1
    workbook.close()

    result = {
        employer: {
            **summary,
            "titles": dict(titles[employer]),
        }
        for employer, summary in totals.items()
    }
    with gzip.open(index_path, "wt", encoding="utf-8") as output:
        json.dump(result, output, separators=(",", ":"))
    print(f"Built DOL H-1B index for {len(result):,} employers")
    return index_path


def load_index(url=DEFAULT_URL, cache_dir=".cache/h1b"):
    path = build_index(url, cache_dir)
    with gzip.open(path, "rt", encoding="utf-8") as source:
        return json.load(source)


def _match_employer(company, index):
    query = normalize_company(company)
    if query in index:
        return query, index[query]
    if len(query) < 5:
        return None, None
    candidates = [
        (name, data) for name, data in index.items()
        if query in name or name in query
    ]
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[1]["lca_count"])


def lookup(company, job_title, index):
    matched_name, data = _match_employer(company, index)
    if not data:
        return {
            "dol_h1b_sponsor": "No match",
            "dol_h1b_lca_count": 0,
            "dol_h1b_worker_positions": 0,
            "dol_h1b_role_lca_count": 0,
            "dol_h1b_employer": "",
        }
    query = normalize_title(job_title)
    query_tokens = {word for word in query.split() if len(word) > 2}
    role_count = 0
    for filing_title, count in data.get("titles", {}).items():
        filing_tokens = set(filing_title.split())
        if query in filing_title or (query_tokens and len(query_tokens & filing_tokens) >= min(2, len(query_tokens))):
            role_count += count
    return {
        "dol_h1b_sponsor": "Yes",
        "dol_h1b_lca_count": data["lca_count"],
        "dol_h1b_worker_positions": data["worker_positions"],
        "dol_h1b_role_lca_count": role_count,
        "dol_h1b_employer": matched_name.title(),
    }

