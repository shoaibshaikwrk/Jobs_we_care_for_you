#!/usr/bin/env python3
"""
job_checker.py

Checks several free/legitimate job sources for new Data Engineering / AI Engineer
postings that match keywords in config.json, filters them down to USA-only
listings, and appends any NEW matches (not seen on a previous run) to a local
CSV file that doubles as your application checklist.

Sources used (no scraping of LinkedIn/Indeed — those block bots and it violates
their Terms of Service):
  - RemoteOK public API      (https://remoteok.com/api)
  - Arbeitnow public API     (https://www.arbeitnow.com/api/job-board-api)
  - Greenhouse job boards    (per-company public API, no key needed)
  - Lever job boards         (per-company public API, no key needed)
  - Ashby job boards         (per-company public API, no key needed)
  - Adzuna                   (broad job aggregator, free API key, US-scoped search)
  - USAJobs                  (official US federal government jobs, free API key)

Usage:
    python3 job_checker.py [path/to/config.json]

Designed to be run manually or on a schedule (cron / Windows Task Scheduler).
See README.md for scheduling instructions and how to get free Adzuna/USAJobs keys.
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobChecker/1.0; +local-script)"
}

CSV_FIELDNAMES = ["id", "found_at", "status", "title", "company", "location", "source", "url"]
DEFAULT_STATUS = "To Apply"

# ---------------------------------------------------------------------------
# USA location detection
# ---------------------------------------------------------------------------

US_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}

US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "washington dc", "washington d.c.",
}

# Word-boundary safe country-level indicators (checked on lowercased text)
US_COUNTRY_PATTERNS = [
    re.compile(r"\bunited states\b"),
    re.compile(r"\bu\.s\.a\.?\b"),
    re.compile(r"\bu\.s\.?\b"),
    re.compile(r"\busa\b"),
    re.compile(r"\bus\b"),
]

# Comma-then-uppercase-abbreviation pattern, e.g. "Austin, TX" — checked on the
# ORIGINAL (non-lowered) string so we only match real "City, ST" formatting and
# don't accidentally treat the word "or"/"in" as Oregon/Indiana.
US_ABBR_AFTER_COMMA = re.compile(r",\s*([A-Z]{2})\b")


def is_usa_location(location):
    """Heuristic check for whether a free-text location string is US-based.

    Job boards format locations inconsistently, so this combines a few safe
    signals rather than a single naive substring match:
      1. Full US state names (safe as substrings, e.g. "california").
      2. "USA" / "United States" / "U.S." / standalone "US" as whole words.
      3. A two-letter US state abbreviation immediately after a comma,
         matched case-sensitively on the original text (e.g. "Austin, TX"),
         which avoids false positives like the word "or" matching Oregon.
    """
    if not location:
        return False

    loc_lower = location.lower()

    for name in US_STATE_NAMES:
        if name in loc_lower:
            return True

    for pattern in US_COUNTRY_PATTERNS:
        if pattern.search(loc_lower):
            return True

    for match in US_ABBR_AFTER_COMMA.finditer(location):
        if match.group(1).lower() in US_STATE_ABBR:
            return True

    return False


def log(msg, log_path=None):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {msg}"
    print(line)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_config(config_path):
    if not os.path.exists(config_path):
        print(f"ERROR: config.json not found at {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_seen(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()


def save_seen(path, seen_ids):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, indent=2)


def matches_keywords(title, keywords, exclude_keywords):
    title_lower = title.lower()
    if any(ex.lower() in title_lower for ex in exclude_keywords):
        return False
    return any(kw.lower() in title_lower for kw in keywords)


def matches_location(location, location_includes):
    if not location_includes:
        return True
    location_lower = (location or "").lower()
    return any(loc.lower() in location_lower for loc in location_includes)


# ---------------------------------------------------------------------------
# Source fetchers. Each returns a list of dicts:
# {id, title, company, location, url, source, us_confirmed}
# "us_confirmed" = True means the source itself guarantees US-only results
# (e.g. Adzuna's /us/ endpoint, USAJobs), so the USA location heuristic is
# skipped for those rows to avoid dropping valid jobs with ambiguous location
# text. Every fetcher is wrapped so a failure in one source doesn't stop others.
# ---------------------------------------------------------------------------

def fetch_remoteok():
    jobs = []
    try:
        resp = requests.get("https://remoteok.com/api", headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for item in data:
            if not isinstance(item, dict) or "id" not in item:
                continue
            jobs.append({
                "id": f"remoteok_{item.get('id')}",
                "title": item.get("position", ""),
                "company": item.get("company", ""),
                "location": item.get("location", "Remote"),
                "url": item.get("url", ""),
                "source": "RemoteOK",
                "us_confirmed": False,
            })
    except Exception as e:
        print(f"  [remoteok] fetch failed: {e}")
    return jobs


def fetch_arbeitnow():
    jobs = []
    try:
        resp = requests.get(
            "https://www.arbeitnow.com/api/job-board-api", headers=HEADERS, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("data", []):
            jobs.append({
                "id": f"arbeitnow_{item.get('slug')}",
                "title": item.get("title", ""),
                "company": item.get("company_name", ""),
                "location": item.get("location", ""),
                "url": item.get("url", ""),
                "source": "Arbeitnow",
                "us_confirmed": False,
            })
    except Exception as e:
        print(f"  [arbeitnow] fetch failed: {e}")
    return jobs


def fetch_greenhouse(company):
    jobs = []
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs",
            headers=HEADERS,
            timeout=20,
        )
        if resp.status_code == 404:
            print(f"  [greenhouse:{company}] no board found (skipping)")
            return jobs
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("jobs", []):
            jobs.append({
                "id": f"greenhouse_{company}_{item.get('id')}",
                "title": item.get("title", ""),
                "company": company,
                "location": (item.get("location") or {}).get("name", ""),
                "url": item.get("absolute_url", ""),
                "source": "Greenhouse",
                "us_confirmed": False,
            })
    except Exception as e:
        print(f"  [greenhouse:{company}] fetch failed: {e}")
    return jobs


def fetch_lever(company):
    jobs = []
    try:
        resp = requests.get(
            f"https://api.lever.co/v0/postings/{company}?mode=json",
            headers=HEADERS,
            timeout=20,
        )
        if resp.status_code == 404:
            print(f"  [lever:{company}] no board found (skipping)")
            return jobs
        resp.raise_for_status()
        data = resp.json()
        for item in data:
            jobs.append({
                "id": f"lever_{company}_{item.get('id')}",
                "title": item.get("text", ""),
                "company": company,
                "location": (item.get("categories") or {}).get("location", ""),
                "url": item.get("hostedUrl", ""),
                "source": "Lever",
                "us_confirmed": False,
            })
    except Exception as e:
        print(f"  [lever:{company}] fetch failed: {e}")
    return jobs


def fetch_ashby(company):
    jobs = []
    try:
        resp = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{company}",
            headers=HEADERS,
            timeout=20,
        )
        if resp.status_code == 404:
            print(f"  [ashby:{company}] no board found (skipping)")
            return jobs
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("jobs", []):
            jobs.append({
                "id": f"ashby_{company}_{item.get('id')}",
                "title": item.get("title", ""),
                "company": company,
                "location": item.get("location", ""),
                "url": item.get("jobUrl", ""),
                "source": "Ashby",
                "us_confirmed": False,
            })
    except Exception as e:
        print(f"  [ashby:{company}] fetch failed: {e}")
    return jobs


def fetch_smartrecruiters(company, max_pages=3, page_size=100):
    """SmartRecruiters is used by many mid-size/enterprise companies. Some boards
    have thousands of postings across all departments, so we page through a
    bounded number of pages (default up to 300 jobs) rather than pulling
    everything — the keyword filter downstream discards the vast majority anyway.
    """
    jobs = []
    try:
        for page in range(max_pages):
            resp = requests.get(
                f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                params={"limit": page_size, "offset": page * page_size},
                headers=HEADERS,
                timeout=20,
            )
            if resp.status_code == 404:
                print(f"  [smartrecruiters:{company}] no board found (skipping)")
                return jobs
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", [])
            if not content:
                break
            for item in content:
                loc = item.get("location") or {}
                location_str = loc.get("fullLocation") or ", ".join(
                    p for p in [loc.get("city"), loc.get("region"), loc.get("country")] if p
                )
                job_id = item.get("id")
                jobs.append({
                    "id": f"smartrecruiters_{company}_{job_id}",
                    "title": item.get("name", ""),
                    "company": company,
                    "location": location_str,
                    "url": f"https://jobs.smartrecruiters.com/{company}/{job_id}",
                    "source": "SmartRecruiters",
                    "us_confirmed": False,
                })
            if len(content) < page_size:
                break  # last page
    except Exception as e:
        print(f"  [smartrecruiters:{company}] fetch failed: {e}")
    return jobs


def fetch_workable(company):
    jobs = []
    try:
        resp = requests.get(
            f"https://apply.workable.com/api/v1/widget/accounts/{company}",
            headers=HEADERS,
            timeout=20,
        )
        if resp.status_code == 404:
            print(f"  [workable:{company}] no board found (skipping)")
            return jobs
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("jobs", []):
            location_str = ", ".join(
                p for p in [item.get("city"), item.get("state"), item.get("country")] if p
            )
            jobs.append({
                "id": f"workable_{company}_{item.get('shortcode')}",
                "title": item.get("title", ""),
                "company": company,
                "location": location_str,
                "url": item.get("url", ""),
                "source": "Workable",
                "us_confirmed": False,
            })
    except Exception as e:
        print(f"  [workable:{company}] fetch failed: {e}")
    return jobs


def fetch_recruitee(company):
    jobs = []
    try:
        resp = requests.get(
            f"https://{company}.recruitee.com/api/offers/",
            headers=HEADERS,
            timeout=20,
        )
        if resp.status_code == 404:
            print(f"  [recruitee:{company}] no board found (skipping)")
            return jobs
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("offers", []):
            location_str = ", ".join(
                p for p in [item.get("city"), item.get("state_name"), item.get("country")] if p
            )
            jobs.append({
                "id": f"recruitee_{company}_{item.get('id')}",
                "title": item.get("title", ""),
                "company": company,
                "location": location_str,
                "url": item.get("careers_url", ""),
                "source": "Recruitee",
                "us_confirmed": False,
            })
    except Exception as e:
        print(f"  [recruitee:{company}] fetch failed: {e}")
    return jobs


# ---------------------------------------------------------------------------
# FAANG-adjacent sources. These don't sit on any shared ATS platform, so each
# needed its own research:
#   - Amazon: genuinely public JSON search API (amazon.jobs) — stable.
#   - Netflix: genuinely public JSON search API (explore.jobs.netflix.net,
#     a white-labeled Eightfold instance) — stable.
#   - Google: no public API, but job data is server-rendered directly into the
#     search results page inside an "AF_initDataCallback(...)" block. We fetch
#     the HTML and parse that embedded structure. More fragile than a real API
#     (Google could change this internal format anytime) but works today and
#     needs no browser/JS execution.
#   - Apple: same idea — no public API, but job listings are server-rendered
#     as plain HTML in the search results page, so we parse it with a regex.
#     Same fragility caveat as Google.
#   - Meta was investigated too but isn't included: its job search runs
#     entirely client-side against a GraphQL endpoint gated by session-bound
#     anti-CSRF tokens, which can't be replicated from a stateless script
#     without running a full headless browser.
# ---------------------------------------------------------------------------

def fetch_amazon(keywords, results_per_keyword=50):
    jobs = []
    for kw in keywords:
        try:
            resp = requests.get(
                "https://www.amazon.jobs/en/search.json",
                params={
                    "base_query": kw,
                    "country": "USA",
                    "result_limit": results_per_keyword,
                },
                headers=HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("jobs", []):
                location_str = item.get("normalized_location") or item.get("location", "")
                job_path = item.get("job_path", "")
                jobs.append({
                    "id": f"amazon_{item.get('id_icims') or item.get('id')}",
                    "title": item.get("title", ""),
                    "company": item.get("company_name", "Amazon"),
                    "location": location_str,
                    "url": f"https://www.amazon.jobs{job_path}" if job_path else "",
                    "source": "Amazon",
                    # We explicitly filtered country=USA above, so this is a hard
                    # filter from Amazon's side, not just a text heuristic.
                    "us_confirmed": True,
                })
        except Exception as e:
            print(f"  [amazon:{kw}] fetch failed: {e}")
    return jobs


def fetch_netflix(keywords, results_per_keyword=50):
    jobs = []
    for kw in keywords:
        try:
            resp = requests.get(
                "https://explore.jobs.netflix.net/api/apply/v2/jobs/",
                params={"domain": "netflix.com", "query": kw, "start": 0, "num": results_per_keyword},
                headers=HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("positions", []):
                locations = item.get("locations") or [item.get("location", "")]
                jobs.append({
                    "id": f"netflix_{item.get('id')}",
                    "title": item.get("name", ""),
                    "company": "Netflix",
                    "location": ", ".join(locations),
                    "url": item.get("canonicalPositionUrl", ""),
                    "source": "Netflix",
                    "us_confirmed": False,
                })
        except Exception as e:
            print(f"  [netflix:{kw}] fetch failed: {e}")
    return jobs


def fetch_google(keywords, results_per_keyword=50):
    """Google doesn't expose a public jobs API. Job data is server-rendered
    into the search results HTML inside an AF_initDataCallback(...) block —
    we fetch that page and parse the embedded array structure directly rather
    than guess at an API endpoint. This is inherently more fragile than a real
    API since it depends on Google's internal page structure, but it's fully
    stateless (no cookies/session needed) and works as of this writing.
    """
    jobs = []
    for kw in keywords:
        try:
            resp = requests.get(
                "https://www.google.com/about/careers/applications/jobs/results/",
                params={"q": kw, "location": "United States"},
                headers=HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            html = resp.text

            for block in re.findall(r"AF_initDataCallback\((\{.*?\})\);", html, re.DOTALL):
                if "data:" not in block:
                    continue
                start = block.index("data:") + len("data:")
                depth, started, end = 0, False, None
                for i in range(start, len(block)):
                    c = block[i]
                    if c == "[":
                        depth += 1
                        started = True
                    elif c == "]":
                        depth -= 1
                        if started and depth == 0:
                            end = i + 1
                            break
                if end is None:
                    continue
                try:
                    data = json.loads(block[start:end])
                except (json.JSONDecodeError, ValueError):
                    continue
                # Heuristic: the jobs block is a list whose first element is a
                # list of job entries, each starting with [id_str, title_str, url_str, ...]
                try:
                    listings = data[0]
                    if not isinstance(listings, list) or not listings:
                        continue
                    first = listings[0]
                    if not (isinstance(first, list) and len(first) > 9
                            and isinstance(first[0], str) and first[0].isdigit()
                            and isinstance(first[1], str)):
                        continue
                except (IndexError, TypeError):
                    continue

                for entry in listings[:results_per_keyword]:
                    try:
                        job_id, title = entry[0], entry[1]
                        locations_raw = entry[9] if len(entry) > 9 else []
                        location_names = []
                        if isinstance(locations_raw, list):
                            for loc in locations_raw:
                                if isinstance(loc, list) and loc and isinstance(loc[0], str):
                                    location_names.append(loc[0])
                        location_str = "; ".join(location_names)
                        jobs.append({
                            "id": f"google_{job_id}",
                            "title": title,
                            "company": "Google",
                            "location": location_str,
                            "url": f"https://www.google.com/about/careers/applications/jobs/results/{job_id}",
                            "source": "Google",
                            "us_confirmed": False,
                        })
                    except (IndexError, TypeError):
                        continue
                break  # found and processed the jobs block, no need to check other blocks
        except Exception as e:
            print(f"  [google:{kw}] fetch failed: {e}")
    return jobs


# Apple's search results only show a bare city name (no state/country), so the
# shared is_usa_location() heuristic can't recognize them as US-based — "Cupertino"
# alone has no country/state signal. Rather than loosen that shared heuristic
# (which could cause false positives for every other source), we scope a fix to
# just Apple: a known-city allowlist of Apple's real US office locations, used
# only to set us_confirmed for jobs from this fetcher.
APPLE_US_CITIES = {
    "cupertino", "sunnyvale", "santa clara", "san jose", "san francisco",
    "san diego", "culver city", "los angeles", "austin", "seattle",
    "bellevue", "new york", "boston", "cambridge", "miami", "atlanta",
    "chicago", "denver", "herndon", "reston", "aliso viejo", "elk grove",
    "sacramento", "orlando", "raleigh", "pittsburgh", "beaverton",
}


def fetch_apple(keywords, results_per_keyword=50):
    """Apple doesn't expose a public jobs API either. Job listings are
    server-rendered as plain HTML in the search results page, so we fetch that
    page and regex out each listing's id/title/location. Same fragility
    caveat as Google — this depends on Apple's page markup staying stable.
    """
    jobs = []
    # Each listing card has two <a> tags pointing at the same job (one visible
    # title link, one "See full role description" accessibility link) — matching
    # on the visible link text is ambiguous. The aria-label ("Title 123456789")
    # right before the href is unique per listing, so anchor on that instead.
    pattern = re.compile(
        r'aria-label="([^"]+?)\s+(\d+)"\s+href="(/en-us/details/[^"]+)"[^>]*data-discover="true">'
        r'.*?job-title-location[^>]*>\s*<span class="a11y">Location</span>'
        r'\s*<span[^>]*>([^<]+)</span>',
        re.DOTALL,
    )
    for kw in keywords:
        try:
            resp = requests.get(
                "https://jobs.apple.com/en-us/search",
                params={"search": kw, "sort": "relevance"},
                headers=HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            html = resp.text
            matches = pattern.findall(html)
            for title, job_id, path, location in matches[:results_per_keyword]:
                location = location.strip()
                jobs.append({
                    "id": f"apple_{job_id}",
                    "title": title.strip(),
                    "company": "Apple",
                    "location": location,
                    "url": f"https://jobs.apple.com{path}",
                    "source": "Apple",
                    "us_confirmed": location.lower() in APPLE_US_CITIES,
                })
        except Exception as e:
            print(f"  [apple:{kw}] fetch failed: {e}")
    return jobs


def fetch_adzuna(app_id, app_key, keywords, results_per_keyword=50):
    """Adzuna aggregates postings from many boards. We hit their US-scoped
    endpoint directly (country code 'us' in the URL) and search per-keyword,
    so results are US jobs by construction — no extra location filtering needed.
    Requires a free key from https://developer.adzuna.com/
    """
    jobs = []
    if not app_id or not app_key:
        return jobs
    for kw in keywords:
        try:
            resp = requests.get(
                "https://api.adzuna.com/v1/api/jobs/us/search/1",
                params={
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": kw,
                    "results_per_page": results_per_keyword,
                    "content-type": "application/json",
                },
                headers=HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("results", []):
                jobs.append({
                    "id": f"adzuna_{item.get('id')}",
                    "title": item.get("title", ""),
                    "company": (item.get("company") or {}).get("display_name", ""),
                    "location": (item.get("location") or {}).get("display_name", ""),
                    "url": item.get("redirect_url", ""),
                    "source": "Adzuna",
                    "us_confirmed": True,
                })
        except Exception as e:
            print(f"  [adzuna:{kw}] fetch failed: {e}")
    return jobs


def fetch_usajobs(email, api_key, keywords, results_per_keyword=50):
    """Official US federal government jobs. Inherently US-only.
    Requires a free key from https://developer.usajobs.gov/APIRequest/Index
    """
    jobs = []
    if not email or not api_key:
        return jobs
    headers = dict(HEADERS)
    headers.update({
        "Host": "data.usajobs.gov",
        "User-Agent": email,
        "Authorization-Key": api_key,
    })
    for kw in keywords:
        try:
            resp = requests.get(
                "https://data.usajobs.gov/api/search",
                params={"Keyword": kw, "ResultsPerPage": results_per_keyword},
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("SearchResult", {}).get("SearchResultItems", [])
            for item in items:
                descriptor = item.get("MatchedObjectDescriptor", {})
                locations = descriptor.get("PositionLocation", [])
                location_str = ", ".join(
                    loc.get("LocationName", "") for loc in locations
                ) or "USA"
                jobs.append({
                    "id": f"usajobs_{item.get('MatchedObjectId')}",
                    "title": descriptor.get("PositionTitle", ""),
                    "company": descriptor.get("OrganizationName", ""),
                    "location": location_str,
                    "url": descriptor.get("PositionURI", ""),
                    "source": "USAJobs",
                    "us_confirmed": True,
                })
        except Exception as e:
            print(f"  [usajobs:{kw}] fetch failed: {e}")
    return jobs


def generate_html(rows, output_path):
    """Build a single self-contained HTML checklist page from the full job
    history (every row ever appended to the CSV, oldest included). The job
    data is embedded directly as inline JSON so the page works both as a
    local file:// document and when hosted (e.g. GitHub Pages) — no separate
    fetch() call that could be blocked by file:// CORS rules.

    Each visitor's application status (To Apply / Applied / Interview / ...)
    is stored in that browser's localStorage, keyed by job id, so marking
    jobs as applied never requires editing or committing any file.
    """
    rows_sorted = sorted(rows, key=lambda r: r.get("found_at", ""), reverse=True)
    jobs_json = json.dumps(rows_sorted, ensure_ascii=False)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Data Engineer / AI Engineer Job Checklist</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; line-height: 1.4; }
  h1 { font-size: 1.5rem; margin-bottom: 4px; }
  .meta { color: #666; font-size: 0.85rem; margin-bottom: 20px; }
  .stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
  .stat { border: 1px solid #ccc; border-radius: 8px; padding: 8px 14px; font-size: 0.85rem; }
  .stat b { display: block; font-size: 1.2rem; }
  .controls { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }
  input[type=text] { padding: 7px 10px; border: 1px solid #ccc; border-radius: 6px; min-width: 220px; }
  select.filter { padding: 7px 10px; border: 1px solid #ccc; border-radius: 6px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e2e2e2; vertical-align: top; }
  th { position: sticky; top: 0; background: Canvas; cursor: default; }
  tr:hover { background: rgba(127,127,127,0.08); }
  a.job-link { text-decoration: none; font-weight: 600; }
  a.job-link:hover { text-decoration: underline; }
  .company { color: #555; font-size: 0.85rem; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.75rem; border: 1px solid #ccc; }
  select.status { padding: 5px 6px; border-radius: 6px; border: 1px solid #ccc; font-size: 0.85rem; }
  .status-To\\ Apply { border-color: #999; }
  .status-Applied { border-color: #3b82f6; color: #3b82f6; }
  .status-Interview\\ Scheduled, .status-Interviewed { border-color: #a855f7; color: #a855f7; }
  .status-Offer { border-color: #16a34a; color: #16a34a; }
  .status-Rejected { border-color: #dc2626; color: #dc2626; opacity: 0.7; }
  .hidden { display: none; }
  .found-at, .source { color: #777; font-size: 0.8rem; white-space: nowrap; }
</style>
</head>
<body>
<h1>Data Engineer / AI Engineer Job Checklist</h1>
<div class="meta">Last updated __GENERATED_AT__ &middot; regenerated automatically once a day</div>

<div class="stats" id="stats"></div>

<div class="controls">
  <input type="text" id="search" placeholder="Search title or company...">
  <select class="filter" id="statusFilter">
    <option value="">All statuses</option>
    <option>To Apply</option>
    <option>Applied</option>
    <option>Interview Scheduled</option>
    <option>Interviewed</option>
    <option>Offer</option>
    <option>Rejected</option>
  </select>
  <select class="filter" id="sourceFilter">
    <option value="">All sources</option>
  </select>
</div>

<table>
  <thead>
    <tr>
      <th>Status</th>
      <th>Role</th>
      <th>Location</th>
      <th>Source</th>
      <th>Found</th>
    </tr>
  </thead>
  <tbody id="jobRows"></tbody>
</table>

<script>
const JOBS = __JOBS_JSON__;
const STATUS_OPTIONS = ["To Apply", "Applied", "Interview Scheduled", "Interviewed", "Offer", "Rejected"];
const STORAGE_PREFIX = "jobchecklist_status_";

function getStatus(job) {
  return localStorage.getItem(STORAGE_PREFIX + job.id) || job.status || "To Apply";
}
function setStatus(jobId, status) {
  localStorage.setItem(STORAGE_PREFIX + jobId, status);
}

function renderStats(jobs) {
  const counts = {};
  STATUS_OPTIONS.forEach(s => counts[s] = 0);
  jobs.forEach(j => { const s = getStatus(j); counts[s] = (counts[s] || 0) + 1; });
  const statsEl = document.getElementById("stats");
  statsEl.innerHTML = `<div class="stat"><b>${jobs.length}</b>Total</div>` +
    STATUS_OPTIONS.map(s => `<div class="stat"><b>${counts[s] || 0}</b>${s}</div>`).join("");
}

function populateSourceFilter(jobs) {
  const sources = [...new Set(jobs.map(j => j.source))].sort();
  const sel = document.getElementById("sourceFilter");
  sources.forEach(s => {
    const opt = document.createElement("option");
    opt.value = s; opt.textContent = s;
    sel.appendChild(opt);
  });
}

function render() {
  const search = document.getElementById("search").value.toLowerCase();
  const statusFilter = document.getElementById("statusFilter").value;
  const sourceFilter = document.getElementById("sourceFilter").value;
  const tbody = document.getElementById("jobRows");
  tbody.innerHTML = "";

  let visibleJobs = [];
  JOBS.forEach(job => {
    const status = getStatus(job);
    if (statusFilter && status !== statusFilter) return;
    if (sourceFilter && job.source !== sourceFilter) return;
    const haystack = (job.title + " " + job.company).toLowerCase();
    if (search && !haystack.includes(search)) return;
    visibleJobs.push(job);

    const tr = document.createElement("tr");

    const statusTd = document.createElement("td");
    const select = document.createElement("select");
    select.className = "status status-" + status.replace(/ /g, "\\\\ ");
    STATUS_OPTIONS.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s; opt.textContent = s;
      if (s === status) opt.selected = true;
      select.appendChild(opt);
    });
    select.addEventListener("change", () => {
      setStatus(job.id, select.value);
      select.className = "status status-" + select.value.replace(/ /g, "\\\\ ");
      renderStats(JOBS);
    });
    statusTd.appendChild(select);
    tr.appendChild(statusTd);

    const roleTd = document.createElement("td");
    const link = document.createElement("a");
    link.href = job.url; link.target = "_blank"; link.rel = "noopener";
    link.className = "job-link"; link.textContent = job.title;
    const companyDiv = document.createElement("div");
    companyDiv.className = "company"; companyDiv.textContent = job.company;
    roleTd.appendChild(link); roleTd.appendChild(companyDiv);
    tr.appendChild(roleTd);

    const locTd = document.createElement("td");
    locTd.textContent = job.location || "";
    tr.appendChild(locTd);

    const sourceTd = document.createElement("td");
    sourceTd.className = "source"; sourceTd.textContent = job.source;
    tr.appendChild(sourceTd);

    const foundTd = document.createElement("td");
    foundTd.className = "found-at"; foundTd.textContent = (job.found_at || "").replace(" UTC", "");
    tr.appendChild(foundTd);

    tbody.appendChild(tr);
  });

  renderStats(JOBS);
}

populateSourceFilter(JOBS);
document.getElementById("search").addEventListener("input", render);
document.getElementById("statusFilter").addEventListener("change", render);
document.getElementById("sourceFilter").addEventListener("change", render);
render();
</script>
</body>
</html>
"""
    html = html.replace("__GENERATED_AT__", generated_at)
    html = html.replace("__JOBS_JSON__", jobs_json)

    folder = os.path.dirname(output_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def generate_html_auth(rows, output_path, resume_url=""):
    """Like generate_html(), but gates the checklist behind Firebase passwordless
    email-link sign-in and stores each job's status in Firestore (keyed by job id
    + signed-in email) instead of localStorage. This means:
      - "Who has applied to what" is visible to every signed-in user, not just
        whoever's browser made the change.
      - Firebase config itself is NOT written by this function — it's loaded from
        a separate docs/firebase-config.js file that you fill in once and that
        survives daily regeneration (this function only ever rewrites index.html).
      - Each signed-in user has their own resume: a file in Firebase Storage
        (path resumes/{uid}/...) plus plain resume text, both stored in the
        Firestore doc user_resumes/{uid}. The topbar "My Resume" / "Upload
        Resume" button always reflects the currently signed-in user's own
        upload — there is no shared/default resume shown to everyone.
        (The `resume_url` parameter is accepted for backwards compatibility
        but is no longer used by this function.)
      - A "Tailor" button per job calls a Firebase Cloud Function (set up
        separately — see "Set up AI resume tailoring" in DEPLOY.md) that uses
        the signed-in user's own OpenAI API key (kept in their browser only,
        never stored server-side) to rewrite their resume text for that job.

    Requires a Firebase project with Email/Password provider → "Email link
    (passwordless sign-in)" enabled, a Firestore database, and Firebase Storage,
    all with the security rules documented in DEPLOY.md.
    """
    rows_sorted = sorted(rows, key=lambda r: r.get("found_at", ""), reverse=True)
    jobs_json = json.dumps(rows_sorted, ensure_ascii=False)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Data Engineer / AI Engineer Job Checklist</title>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-storage-compat.js"></script>
<script src="firebase-config.js"></script>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; line-height: 1.4; }
  h1 { font-size: 1.5rem; margin-bottom: 4px; }
  .meta { color: #666; font-size: 0.85rem; margin-bottom: 20px; }
  .stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
  .stat { border: 1px solid #ccc; border-radius: 8px; padding: 8px 14px; font-size: 0.85rem; }
  .stat b { display: block; font-size: 1.2rem; }
  .controls { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }
  input[type=text], input[type=email] { padding: 7px 10px; border: 1px solid #ccc; border-radius: 6px; min-width: 220px; }
  select.filter { padding: 7px 10px; border: 1px solid #ccc; border-radius: 6px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e2e2e2; vertical-align: top; }
  th { position: sticky; top: 0; background: Canvas; cursor: default; }
  tr:hover { background: rgba(127,127,127,0.08); }
  a.job-link { text-decoration: none; font-weight: 600; }
  a.job-link:hover { text-decoration: underline; }
  .company { color: #555; font-size: 0.85rem; }
  select.status { padding: 5px 6px; border-radius: 6px; border: 1px solid #ccc; font-size: 0.85rem; }
  .applied-by { font-size: 0.75rem; color: #888; margin-top: 4px; max-width: 240px; }
  .found-at, .source { color: #777; font-size: 0.8rem; white-space: nowrap; }
  .topbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
  .topbar-right { display: flex; align-items: center; gap: 10px; font-size: 0.85rem; }
  .btn { display: inline-block; padding: 8px 16px; border-radius: 6px; border: 1px solid #888;
         background: transparent; cursor: pointer; font-size: 0.9rem; text-decoration: none; color: inherit; }
  .btn:hover { background: rgba(127,127,127,0.12); }
  .btn-primary { border-color: #2563eb; color: #2563eb; font-weight: 600; }
  #loginScreen { max-width: 420px; margin: 15vh auto 0; text-align: center; }
  #loginScreen input { width: 100%; box-sizing: border-box; margin-bottom: 10px; }
  #loginStatus { font-size: 0.85rem; color: #666; margin-top: 12px; min-height: 1.2em; }
  .hidden { display: none; }
  .btn-small { padding: 4px 10px; font-size: 0.8rem; }
  .modal { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex;
           align-items: flex-start; justify-content: center; z-index: 100; padding: 5vh 16px; overflow-y: auto; }
  .modal-content { background: Canvas; color: CanvasText; border-radius: 10px; padding: 24px;
                    max-width: 560px; width: 100%; box-shadow: 0 10px 40px rgba(0,0,0,0.3); }
  .modal-content h2 { margin-top: 0; font-size: 1.2rem; }
  .modal-section { margin-bottom: 16px; }
  .modal-section label { display: block; font-size: 0.85rem; font-weight: 600; margin-bottom: 6px; }
  .modal-section input[type=file], .modal-section input[type=password] { width: 100%; box-sizing: border-box;
    padding: 7px 10px; border: 1px solid #ccc; border-radius: 6px; }
  .modal-hint { font-size: 0.78rem; color: #777; margin-top: 6px; }
  .modal-subtitle { font-size: 0.9rem; color: #555; margin-bottom: 10px; }
  .modal-status { font-size: 0.85rem; color: #666; margin-top: 10px; min-height: 1.2em; }
  .modal-actions { display: flex; gap: 10px; }
  textarea { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #ccc;
             border-radius: 6px; font-family: inherit; font-size: 0.85rem; resize: vertical; }
  .drawer-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 99; }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(440px, 100vw);
            background: Canvas; color: CanvasText; z-index: 100; box-shadow: -6px 0 24px rgba(0,0,0,0.25);
            padding: 24px; overflow-y: auto; box-sizing: border-box; }
  .drawer h2 { margin-top: 0; font-size: 1.2rem; }
  .drawer-close { float: right; }
  .api-key-inline-btn { margin-top: 8px; }
  .optional-tag { font-size: 0.75rem; font-weight: 400; color: #888; }
</style>
</head>
<body>

<div id="loginScreen">
  <h1>Data Engineer / AI Engineer Job Checklist</h1>
  <p>Sign in with your email to view the checklist and track who's applied where.
     No password — we'll email you a one-time sign-in link.</p>
  <input type="email" id="emailInput" placeholder="you@example.com">
  <button class="btn btn-primary" id="sendLinkBtn" style="width:100%">Send me a sign-in link</button>
  <div id="loginStatus"></div>
</div>

<div id="appScreen" class="hidden">
  <div class="topbar">
    <div>
      <h1>Data Engineer / AI Engineer Job Checklist</h1>
      <div class="meta">Last updated __GENERATED_AT__ &middot; regenerated automatically once a day</div>
    </div>
    <div class="topbar-right">
      <button class="btn" id="resumeBtn">My Resume</button>
      <button class="btn" id="settingsBtn" title="Upload your resume, add an OpenAI key, and tailor resumes per job">&#9881; Settings</button>
      <span id="whoami"></span>
      <button class="btn" id="signOutBtn">Sign out</button>
    </div>
  </div>

  <div class="stats" id="stats"></div>

  <div class="controls">
    <input type="text" id="search" placeholder="Search title or company...">
    <select class="filter" id="statusFilter">
      <option value="">All statuses</option>
      <option>To Apply</option>
      <option>Applied</option>
      <option>Interview Scheduled</option>
      <option>Interviewed</option>
      <option>Offer</option>
      <option>Rejected</option>
    </select>
    <select class="filter" id="sourceFilter">
      <option value="">All sources</option>
    </select>
  </div>

  <table>
    <thead>
      <tr>
        <th>Your Status</th>
        <th>Role</th>
        <th>Location</th>
        <th>Source</th>
        <th>Found</th>
        <th>AI Resume <span class="optional-tag">(optional)</span></th>
      </tr>
    </thead>
    <tbody id="jobRows"></tbody>
  </table>
</div>

<div id="settingsBackdrop" class="drawer-backdrop hidden"></div>
<div id="settingsModal" class="drawer hidden">
  <button class="btn drawer-close" id="closeSettingsBtn">Close</button>
  <h2>Settings</h2>
  <p class="modal-hint">Everything here is tied to your own signed-in account (__DEFAULT_RESUME_HINT__).
     Your resume file and text are only ever visible to you.</p>

  <div class="modal-section">
    <label for="resumeFileInput">Your resume file (PDF or Word)</label>
    <input type="file" id="resumeFileInput" accept=".pdf,.doc,.docx">
    <div class="modal-hint" id="resumeFileCurrent"></div>
  </div>

  <div class="modal-section">
    <label for="resumeTextInput">Resume text (plain text — used as the starting point for AI tailoring)</label>
    <textarea id="resumeTextInput" rows="8" placeholder="Paste the text of your resume here..."></textarea>
  </div>

  <div class="modal-section">
    <label for="openaiKeyInput">OpenAI API key <span class="optional-tag">(optional — only needed for the "Tailor" AI feature)</span></label>
    <input type="password" id="openaiKeyInput" placeholder="Paste your key here, e.g. sk-...">
    <div class="modal-hint">Stored only in your browser (localStorage) — it is never saved to Firestore
      or seen by anyone else. Get one at platform.openai.com/api-keys. Everything else in Settings
      works fine without this — it's only used when you click "Tailor" on a job.</div>
  </div>

  <div class="modal-actions">
    <button class="btn btn-primary" id="saveSettingsBtn">Save</button>
  </div>
  <div id="settingsStatus" class="modal-status"></div>
</div>

<div id="tailorBackdrop" class="drawer-backdrop hidden"></div>
<div id="tailorModal" class="drawer hidden">
  <button class="btn drawer-close" id="closeTailorBtn">Close</button>
  <h2>AI-Tailored Resume <span class="optional-tag">(optional)</span></h2>
  <div id="tailorJobTitle" class="modal-subtitle"></div>
  <div id="tailorStatus" class="modal-status"></div>
  <button class="btn btn-primary api-key-inline-btn hidden" id="openSettingsFromTailorBtn">Add my OpenAI API key</button>
  <textarea id="tailorOutput" rows="18" placeholder="This is optional — add your OpenAI API key in Settings, then click &quot;Tailor&quot; on a job to generate a version of your resume rewritten for that role. Generated text will appear here." readonly></textarea>
  <div class="modal-actions">
    <button class="btn" id="copyTailorBtn">Copy to clipboard</button>
  </div>
</div>

<script>
const JOBS = __JOBS_JSON__;
const STATUS_OPTIONS = ["To Apply", "Applied", "Interview Scheduled", "Interviewed", "Offer", "Rejected"];

// firebaseConfig comes from firebase-config.js, loaded before this script.
firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const db = firebase.firestore();
const storage = firebase.storage();

const loginScreen = document.getElementById("loginScreen");
const appScreen = document.getElementById("appScreen");
const loginStatus = document.getElementById("loginStatus");
const emailInput = document.getElementById("emailInput");

let currentEmail = null;
let currentUid = null;
// jobId -> array of {email, status, updatedAt}
let statusByJob = {};
// This signed-in user's own uploaded resume + AI settings.
let myResumeUrl = null;
let myResumeFileName = "";
let myResumeText = "";
let myApiKey = "";

function docIdFor(jobId, email) {
  return jobId + "__" + email.replace(/[^a-zA-Z0-9]/g, "_");
}

// ---- Per-user resume (Firebase Storage + Firestore) & AI tailoring settings ----

function updateResumeButton() {
  const btn = document.getElementById("resumeBtn");
  if (myResumeUrl) {
    btn.textContent = "My Resume";
    btn.onclick = () => window.open(myResumeUrl, "_blank", "noopener");
  } else {
    btn.textContent = "Upload Resume";
    btn.onclick = () => document.getElementById("settingsBtn").click();
  }
}

async function loadUserResume() {
  if (!currentUid) return;
  try {
    const doc = await db.collection("user_resumes").doc(currentUid).get();
    if (doc.exists) {
      const d = doc.data();
      myResumeUrl = d.fileUrl || null;
      myResumeFileName = d.fileName || "";
      myResumeText = d.resumeText || "";
    }
  } catch (err) {
    console.error("Failed to load your resume info:", err);
  }
  document.getElementById("resumeTextInput").value = myResumeText;
  document.getElementById("resumeFileCurrent").textContent =
    myResumeFileName ? ("Current file: " + myResumeFileName) : "No resume uploaded yet.";
  updateResumeButton();
}

function loadApiKeyFromBrowser() {
  myApiKey = window.localStorage.getItem("openaiApiKey") || "";
  document.getElementById("openaiKeyInput").value = myApiKey;
}

// Tracks an in-progress Storage upload so the Close button can actually
// interrupt it instead of leaving it running (and the UI stuck) forever.
let activeUploadTask = null;
let uploadWasCancelled = false;

function openSettings(focusApiKey) {
  closeTailorDrawer();
  document.getElementById("settingsBackdrop").classList.remove("hidden");
  document.getElementById("settingsModal").classList.remove("hidden");
  if (focusApiKey) document.getElementById("openaiKeyInput").focus();
}
function closeSettings() {
  if (activeUploadTask) {
    uploadWasCancelled = true;
    activeUploadTask.cancel();
    activeUploadTask = null;
  }
  document.getElementById("settingsBackdrop").classList.add("hidden");
  document.getElementById("settingsModal").classList.add("hidden");
  // Always reset, so reopening Settings later never shows a stuck
  // "Uploading..."/"Saving..." message from a previous attempt.
  document.getElementById("saveSettingsBtn").disabled = false;
  document.getElementById("settingsStatus").textContent = "";
}

document.getElementById("settingsBtn").addEventListener("click", () => openSettings(false));
document.getElementById("closeSettingsBtn").addEventListener("click", closeSettings);
document.getElementById("settingsBackdrop").addEventListener("click", closeSettings);

document.getElementById("saveSettingsBtn").addEventListener("click", async () => {
  const statusEl = document.getElementById("settingsStatus");
  const saveBtn = document.getElementById("saveSettingsBtn");
  saveBtn.disabled = true;
  statusEl.textContent = "Saving...";
  uploadWasCancelled = false;
  try {
    // API key: browser-only, never written to Firestore or any server.
    const apiKey = document.getElementById("openaiKeyInput").value.trim();
    window.localStorage.setItem("openaiApiKey", apiKey);
    myApiKey = apiKey;

    const resumeText = document.getElementById("resumeTextInput").value;
    myResumeText = resumeText;

    let fileUrl = myResumeUrl;
    let fileName = myResumeFileName;
    const file = document.getElementById("resumeFileInput").files[0];
    if (file) {
      const path = "resumes/" + currentUid + "/" + file.name;
      const ref = storage.ref().child(path);
      const task = ref.put(file);
      activeUploadTask = task;

      // Firebase's put() can hang indefinitely (silent retries) if Storage
      // hasn't been enabled for this project yet or the security rules
      // haven't been published — show real progress and give up with a
      // clear, actionable message after 30s instead of spinning forever.
      await new Promise((resolve, reject) => {
        const timeoutId = setTimeout(() => {
          task.cancel();
          reject(new Error(
            "Upload timed out after 30s. This usually means Firebase Storage " +
            "isn't set up yet for this project (see \\"Set up Firebase Storage\\" " +
            "in DEPLOY.md) or its security rules haven't been published. Open " +
            "the browser console (F12 → Console) to see the exact error."
          ));
        }, 30000);

        task.on("state_changed",
          (snapshot) => {
            const pct = snapshot.totalBytes
              ? Math.round((snapshot.bytesTransferred / snapshot.totalBytes) * 100)
              : 0;
            statusEl.textContent = "Uploading resume file... " + pct + "%";
          },
          (err) => {
            clearTimeout(timeoutId);
            reject(err);
          },
          () => {
            clearTimeout(timeoutId);
            resolve();
          }
        );
      });

      activeUploadTask = null;
      fileUrl = await ref.getDownloadURL();
      fileName = file.name;
      myResumeUrl = fileUrl;
      myResumeFileName = fileName;
    }

    statusEl.textContent = "Saving...";
    await db.collection("user_resumes").doc(currentUid).set({
      email: currentEmail,
      resumeText: resumeText,
      fileUrl: fileUrl || null,
      fileName: fileName || null,
      updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
    }, { merge: true });

    document.getElementById("resumeFileCurrent").textContent =
      fileName ? ("Current file: " + fileName) : "No resume uploaded yet.";
    updateResumeButton();
    statusEl.textContent = "Saved.";
    setTimeout(() => { statusEl.textContent = ""; }, 2500);
  } catch (err) {
    activeUploadTask = null;
    if (!uploadWasCancelled) {
      statusEl.textContent = "Error: " + err.message;
    }
  } finally {
    saveBtn.disabled = false;
  }
});

function openTailorDrawer() {
  closeSettings();
  document.getElementById("tailorBackdrop").classList.remove("hidden");
  document.getElementById("tailorModal").classList.remove("hidden");
}
function closeTailorDrawer() {
  document.getElementById("tailorBackdrop").classList.add("hidden");
  document.getElementById("tailorModal").classList.add("hidden");
}

document.getElementById("closeTailorBtn").addEventListener("click", closeTailorDrawer);
document.getElementById("tailorBackdrop").addEventListener("click", closeTailorDrawer);
document.getElementById("openSettingsFromTailorBtn").addEventListener("click", () => openSettings(true));

document.getElementById("copyTailorBtn").addEventListener("click", async () => {
  const ta = document.getElementById("tailorOutput");
  const btn = document.getElementById("copyTailorBtn");
  const text = ta.value;
  if (!text) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      // Fallback for browsers/contexts without the async Clipboard API.
      ta.removeAttribute("readonly");
      ta.select();
      document.execCommand("copy");
      ta.setAttribute("readonly", "readonly");
    }
    const original = btn.textContent;
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = original; }, 1500);
  } catch (err) {
    btn.textContent = "Copy failed — select the text manually";
    setTimeout(() => { btn.textContent = "Copy to clipboard"; }, 2500);
  }
});

async function tailorResumeForJob(job) {
  openTailorDrawer();
  document.getElementById("tailorJobTitle").textContent = job.title + " — " + job.company + " (" + (job.location || "") + ")";
  const statusEl = document.getElementById("tailorStatus");
  const outEl = document.getElementById("tailorOutput");
  const apiKeyBtn = document.getElementById("openSettingsFromTailorBtn");
  outEl.value = "";
  apiKeyBtn.classList.add("hidden");

  if (!myApiKey) {
    statusEl.textContent = "This feature is optional. Add your OpenAI API key to generate a tailored resume for this job.";
    apiKeyBtn.classList.remove("hidden");
    return;
  }
  if (!myResumeText) {
    statusEl.textContent = "Paste your resume text in Settings first — it's the starting point the AI edits.";
    return;
  }
  if (!firebaseConfig.cloudFunctionsBaseUrl) {
    statusEl.textContent = "AI tailoring isn't set up on this site yet — see \\"Set up AI resume tailoring\\" in DEPLOY.md.";
    return;
  }

  statusEl.textContent = "Generating a tailored version (10-20 seconds)...";
  try {
    const resp = await fetch(firebaseConfig.cloudFunctionsBaseUrl + "/tailorResume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        apiKey: myApiKey,
        resumeText: myResumeText,
        job: { title: job.title, company: job.company, location: job.location, source: job.source },
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || ("Request failed (" + resp.status + ")"));
    outEl.value = data.tailoredResume || "";
    statusEl.textContent = "Done — review carefully before using; AI output can be wrong.";
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
  }
}

document.getElementById("sendLinkBtn").addEventListener("click", () => {
  const email = emailInput.value.trim();
  if (!email || !email.includes("@")) {
    loginStatus.textContent = "Enter a valid email address.";
    return;
  }
  const actionCodeSettings = { url: window.location.href, handleCodeInApp: true };
  loginStatus.textContent = "Sending...";
  auth.sendSignInLinkToEmail(email, actionCodeSettings).then(() => {
    window.localStorage.setItem("emailForSignIn", email);
    loginStatus.textContent = "Check " + email + " for your sign-in link.";
  }).catch(err => {
    loginStatus.textContent = "Error: " + err.message;
  });
});

document.getElementById("signOutBtn").addEventListener("click", () => {
  auth.signOut().then(() => window.location.reload());
});

async function loadStatuses() {
  const snapshot = await db.collection("job_status").get();
  statusByJob = {};
  snapshot.forEach(doc => {
    const d = doc.data();
    if (!statusByJob[d.jobId]) statusByJob[d.jobId] = [];
    statusByJob[d.jobId].push(d);
  });
}

function getMyStatus(jobId) {
  const entries = statusByJob[jobId] || [];
  const mine = entries.find(e => e.email === currentEmail);
  return mine ? mine.status : "To Apply";
}

function getOthersText(jobId) {
  const entries = statusByJob[jobId] || [];
  const others = entries.filter(e => e.email !== currentEmail && e.status !== "To Apply");
  if (others.length === 0) return "";
  return "Also: " + others.map(e => e.email + " (" + e.status + ")").join(", ");
}

function renderStats() {
  const counts = {};
  STATUS_OPTIONS.forEach(s => counts[s] = 0);
  JOBS.forEach(j => { const s = getMyStatus(j.id); counts[s] = (counts[s] || 0) + 1; });
  const statsEl = document.getElementById("stats");
  statsEl.innerHTML = `<div class="stat"><b>${JOBS.length}</b>Total</div>` +
    STATUS_OPTIONS.map(s => `<div class="stat"><b>${counts[s] || 0}</b>${s}</div>`).join("");
}

function populateSourceFilter() {
  const sources = [...new Set(JOBS.map(j => j.source))].sort();
  const sel = document.getElementById("sourceFilter");
  sources.forEach(s => {
    const opt = document.createElement("option");
    opt.value = s; opt.textContent = s;
    sel.appendChild(opt);
  });
}

function render() {
  const search = document.getElementById("search").value.toLowerCase();
  const statusFilter = document.getElementById("statusFilter").value;
  const sourceFilter = document.getElementById("sourceFilter").value;
  const tbody = document.getElementById("jobRows");
  tbody.innerHTML = "";

  JOBS.forEach(job => {
    const status = getMyStatus(job.id);
    if (statusFilter && status !== statusFilter) return;
    if (sourceFilter && job.source !== sourceFilter) return;
    const haystack = (job.title + " " + job.company).toLowerCase();
    if (search && !haystack.includes(search)) return;

    const tr = document.createElement("tr");

    const statusTd = document.createElement("td");
    const select = document.createElement("select");
    select.className = "status";
    STATUS_OPTIONS.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s; opt.textContent = s;
      if (s === status) opt.selected = true;
      select.appendChild(opt);
    });
    select.addEventListener("change", async () => {
      const docId = docIdFor(job.id, currentEmail);
      await db.collection("job_status").doc(docId).set({
        jobId: job.id,
        email: currentEmail,
        status: select.value,
        updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
      });
      const entries = statusByJob[job.id] || (statusByJob[job.id] = []);
      const existing = entries.find(e => e.email === currentEmail);
      if (existing) existing.status = select.value;
      else entries.push({ email: currentEmail, status: select.value });
      renderStats();
      othersDiv.textContent = getOthersText(job.id);
    });
    statusTd.appendChild(select);
    const othersDiv = document.createElement("div");
    othersDiv.className = "applied-by";
    othersDiv.textContent = getOthersText(job.id);
    statusTd.appendChild(othersDiv);
    tr.appendChild(statusTd);

    const roleTd = document.createElement("td");
    const link = document.createElement("a");
    link.href = job.url; link.target = "_blank"; link.rel = "noopener";
    link.className = "job-link"; link.textContent = job.title;
    const companyDiv = document.createElement("div");
    companyDiv.className = "company"; companyDiv.textContent = job.company;
    roleTd.appendChild(link); roleTd.appendChild(companyDiv);
    tr.appendChild(roleTd);

    const locTd = document.createElement("td");
    locTd.textContent = job.location || "";
    tr.appendChild(locTd);

    const sourceTd = document.createElement("td");
    sourceTd.className = "source"; sourceTd.textContent = job.source;
    tr.appendChild(sourceTd);

    const foundTd = document.createElement("td");
    foundTd.className = "found-at"; foundTd.textContent = (job.found_at || "").replace(" UTC", "");
    tr.appendChild(foundTd);

    const aiTd = document.createElement("td");
    const tailorBtn = document.createElement("button");
    tailorBtn.className = "btn btn-small";
    tailorBtn.textContent = "Tailor";
    tailorBtn.title = "Generate a version of your resume tailored to this specific job";
    tailorBtn.addEventListener("click", () => tailorResumeForJob(job));
    aiTd.appendChild(tailorBtn);
    tr.appendChild(aiTd);

    tbody.appendChild(tr);
  });

  renderStats();
}

async function showApp() {
  loginScreen.classList.add("hidden");
  appScreen.classList.remove("hidden");
  document.getElementById("whoami").textContent = currentEmail;
  await loadStatuses();
  loadApiKeyFromBrowser();
  await loadUserResume();
  populateSourceFilter();
  document.getElementById("search").addEventListener("input", render);
  document.getElementById("statusFilter").addEventListener("change", render);
  document.getElementById("sourceFilter").addEventListener("change", render);
  render();
}

// Handle arriving via the emailed sign-in link
if (auth.isSignInWithEmailLink(window.location.href)) {
  let email = window.localStorage.getItem("emailForSignIn");
  if (!email) {
    email = window.prompt("Confirm your email to complete sign-in:");
  }
  auth.signInWithEmailLink(email, window.location.href).then(result => {
    window.localStorage.removeItem("emailForSignIn");
    window.history.replaceState({}, document.title, window.location.pathname);
    currentEmail = result.user.email;
    currentUid = result.user.uid;
    showApp();
  }).catch(err => {
    loginStatus.textContent = "Sign-in failed: " + err.message;
  });
} else {
  auth.onAuthStateChanged(user => {
    if (user) {
      currentEmail = user.email;
      currentUid = user.uid;
      showApp();
    }
  });
}
</script>
</body>
</html>
"""
    html = html.replace("__GENERATED_AT__", generated_at)
    html = html.replace("__JOBS_JSON__", jobs_json)
    html = html.replace(
        "__DEFAULT_RESUME_HINT__",
        "not a shared default — each signed-in user uploads and sees only their own",
    )

    folder = os.path.dirname(output_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
    config = load_config(config_path)

    keywords = config.get("keywords", [])
    exclude_keywords = config.get("exclude_keywords", [])
    location_includes = config.get("location_includes", [])
    remote_only = config.get("remote_only", False)
    usa_only = config.get("usa_only", True)
    sources_cfg = config.get("sources", {})

    script_dir = os.path.dirname(os.path.abspath(config_path))

    # NOTE: os.path.join discards script_dir automatically if the config value
    # is already an absolute path, so this works whether output_csv/seen_jobs_file/
    # log_file are relative (kept next to the script) or absolute (e.g. a Desktop folder).
    output_csv = os.path.join(script_dir, config.get("output_csv", "jobs_found.csv"))
    seen_path = os.path.join(script_dir, config.get("seen_jobs_file", "seen_jobs.json"))
    log_path = os.path.join(script_dir, config.get("log_file", "run_log.txt"))

    for path in (output_csv, seen_path, log_path):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)

    log("Starting job check run...", log_path)

    all_jobs = []

    if sources_cfg.get("remoteok"):
        print("Fetching RemoteOK...")
        all_jobs.extend(fetch_remoteok())

    if sources_cfg.get("arbeitnow"):
        print("Fetching Arbeitnow...")
        all_jobs.extend(fetch_arbeitnow())

    for company in sources_cfg.get("greenhouse_companies", []):
        print(f"Fetching Greenhouse board: {company}...")
        all_jobs.extend(fetch_greenhouse(company))

    for company in sources_cfg.get("lever_companies", []):
        print(f"Fetching Lever board: {company}...")
        all_jobs.extend(fetch_lever(company))

    for company in sources_cfg.get("ashby_companies", []):
        print(f"Fetching Ashby board: {company}...")
        all_jobs.extend(fetch_ashby(company))

    for company in sources_cfg.get("smartrecruiters_companies", []):
        print(f"Fetching SmartRecruiters board: {company}...")
        all_jobs.extend(fetch_smartrecruiters(company))

    for company in sources_cfg.get("workable_companies", []):
        print(f"Fetching Workable board: {company}...")
        all_jobs.extend(fetch_workable(company))

    for company in sources_cfg.get("recruitee_companies", []):
        print(f"Fetching Recruitee board: {company}...")
        all_jobs.extend(fetch_recruitee(company))

    if sources_cfg.get("amazon"):
        print("Fetching Amazon...")
        all_jobs.extend(fetch_amazon(keywords))

    if sources_cfg.get("netflix"):
        print("Fetching Netflix...")
        all_jobs.extend(fetch_netflix(keywords))

    if sources_cfg.get("google"):
        print("Fetching Google...")
        all_jobs.extend(fetch_google(keywords))

    if sources_cfg.get("apple"):
        print("Fetching Apple...")
        all_jobs.extend(fetch_apple(keywords))

    # Credentials can come from config.json OR environment variables (e.g. GitHub
    # Actions secrets) — env vars take precedence so keys never need to be committed
    # to the repo. Having either the env var pair or "enabled": true + filled-in
    # config values is enough to turn a source on.
    adzuna_cfg = sources_cfg.get("adzuna", {})
    adzuna_app_id = os.environ.get("ADZUNA_APP_ID") or adzuna_cfg.get("app_id", "")
    adzuna_app_key = os.environ.get("ADZUNA_APP_KEY") or adzuna_cfg.get("app_key", "")
    if adzuna_cfg.get("enabled") or (adzuna_app_id and adzuna_app_key):
        print("Fetching Adzuna (US)...")
        all_jobs.extend(fetch_adzuna(adzuna_app_id, adzuna_app_key, keywords))

    usajobs_cfg = sources_cfg.get("usajobs", {})
    usajobs_email = os.environ.get("USAJOBS_EMAIL") or usajobs_cfg.get("email", "")
    usajobs_api_key = os.environ.get("USAJOBS_API_KEY") or usajobs_cfg.get("api_key", "")
    if usajobs_cfg.get("enabled") or (usajobs_email and usajobs_api_key):
        print("Fetching USAJobs (federal)...")
        all_jobs.extend(fetch_usajobs(usajobs_email, usajobs_api_key, keywords))

    print(f"\nTotal jobs pulled from all sources: {len(all_jobs)}")

    # Filter by keyword / location / remote / USA-only
    filtered = []
    usa_filtered_out = 0
    for job in all_jobs:
        if not matches_keywords(job["title"], keywords, exclude_keywords):
            continue
        if not matches_location(job["location"], location_includes):
            continue
        if remote_only and "remote" not in (job["location"] or "").lower():
            continue
        if usa_only and not job.get("us_confirmed") and not is_usa_location(job["location"]):
            usa_filtered_out += 1
            continue
        filtered.append(job)

    print(f"Jobs matching your keywords/filters: {len(filtered)}"
          f" (excluded {usa_filtered_out} non-USA/ambiguous-location jobs)")

    # Dedup against previously seen jobs
    seen_ids = load_seen(seen_path)
    new_jobs = [j for j in filtered if j["id"] not in seen_ids]

    print(f"NEW jobs since last run: {len(new_jobs)}")

    if new_jobs:
        file_exists = os.path.exists(output_csv)
        with open(output_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            found_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            for job in new_jobs:
                writer.writerow({
                    "id": job["id"],
                    "found_at": found_at,
                    "status": DEFAULT_STATUS,
                    "title": job["title"],
                    "company": job["company"],
                    "location": job["location"],
                    "source": job["source"],
                    "url": job["url"],
                })
                seen_ids.add(job["id"])
                print(f"  + {job['title']} @ {job['company']} ({job['source']}) - {job['url']}")

        save_seen(seen_path, seen_ids)
        log(f"Added {len(new_jobs)} new job(s) to {output_csv}", log_path)
    else:
        log("No new jobs found this run.", log_path)

    output_html = config.get("output_html")
    if output_html:
        html_path = os.path.join(script_dir, output_html)
        if os.path.exists(output_csv):
            with open(output_csv, "r", newline="", encoding="utf-8") as f:
                all_rows = list(csv.DictReader(f))
        else:
            all_rows = []
        if config.get("enable_auth"):
            generate_html_auth(all_rows, html_path, config.get("resume_url", ""))
        else:
            generate_html(all_rows, html_path)
        log(f"Regenerated checklist page at {html_path} ({len(all_rows)} total jobs)", log_path)

    log("Run complete.\n", log_path)


if __name__ == "__main__":
    main()
