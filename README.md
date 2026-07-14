# Job Checker — Data Engineering / AI Engineer (USA)

A small local Python script that checks **twelve** free, legitimate job sources for new
**Data Engineer** / **AI Engineer** / **ML Engineer** / **Analytics Engineer** postings,
filters them down to **USA-only** listings, and appends any *new* matches to a CSV
file that doubles as your **application checklist**. Nothing is scraped from LinkedIn
or Indeed — those actively block bots and it would violate their Terms of Service.
Instead this uses:

- **RemoteOK** — public API, remote jobs across categories, no key needed
- **Arbeitnow** — public API, mostly Europe-focused, no key needed (low USA yield, kept for breadth)
- **Greenhouse** — per-company public API, no key needed. 35 companies by default (Stripe, Airbnb, Coinbase, Databricks, Instacart, Robinhood, Anthropic, Scale AI, and more — full list in `config.json`)
- **Lever** — per-company public API, no key needed. Defaults: Netflix, Palantir, Wealthfront, Plaid
- **Ashby** — per-company public API, no key needed. Defaults: OpenAI, Ramp, Notion, Replit, Vercel, ClickHouse, Confluent, Zapier, Linear, Cohere, and more
- **SmartRecruiters** — per-company public API, 18 companies by default (Visa, Experian, LinkedIn, and more)
- **Workable** — per-company public API, 5 companies by default
- **Recruitee** — per-company public API, 5 companies by default
- **Amazon** — public `amazon.jobs` search API, US-scoped directly
- **Netflix** — public careers API
- **Google** — parsed directly from `google.com/about/careers`, US-scoped
- **Apple** — parsed directly from `jobs.apple.com`, filtered to known US office cities
- **Adzuna** — broad aggregator covering many boards at once, US-scoped search. *Optional, needs a free key*
- **USAJobs** — official US federal government postings, inherently US-only. *Optional, needs a free key*

(Meta was evaluated but skipped — its job search requires session-bound
anti-bot tokens that regenerate per browser session, which can't be
replicated from a stateless scheduled script without running a full headless
browser daily.)

All company lists live in `config.json` — add, remove, or swap any of them. With the
current defaults across all these sources, a test run pulled over 13,000 raw postings
and matched 672 real USA-based openings — enabling Adzuna and USAJobs on top of that
will push it higher still.

The hosted website version (see below) also adds:
- **Sign-in with email, no password** — a one-time link, powered by Firebase.
- **Shared tracking** — everyone who signs in sees the same checklist and can see
  who's applied to what ("Also: alice@x.com (Applied)").
- **Your own resume, per account** — each signed-in user uploads their own resume
  (Firebase Storage), private to them — there's no shared/default resume.
- **AI resume tailoring (optional)** — paste your own OpenAI API key in Settings
  and click "Tailor" on any job to get a version of your resume rewritten for
  that specific role. Requires a small one-time Cloud Functions setup — see
  [DEPLOY.md](DEPLOY.md).
- **Now also checks Amazon, Netflix, Google, and Apple** careers pages directly,
  on top of the original 8 sources (Meta was evaluated and skipped — its job
  search requires session-bound tokens that can't be replicated from a
  scheduled script; see DEPLOY.md/commit history for details).

## Two ways to run this

1. **Locally on your Mac** (this README) — `config.json` writes to a CSV on your
   Desktop, run manually or via cron.
2. **As a live website, updated daily automatically** — see **[DEPLOY.md](DEPLOY.md)**.
   Uses `config.web.json` + GitHub Actions + GitHub Pages, so it keeps running even
   if your laptop is off, and gives you a checklist page with clickable status
   dropdowns (To Apply / Applied / Interview / Offer / Rejected) you can check from
   any browser.

Both use the exact same `job_checker.py` — just pointed at different config files.

## Note on existing tools

Before building this, I checked whether something like it already existed on GitHub.
Two notable projects came up:

- **JobFunnel** (2.2k★) — archived by its author in Dec 2025 because job boards now
  aggressively block automated scraping; it no longer reliably works.
- **ai-job-scraper** — actively maintained, but requires an RTX 4090 GPU (16GB VRAM),
  CUDA, Docker, and a locally-hosted LLM just to run. Overkill for checking job listings.

Neither was a good fit for "run it locally, see results" with no special hardware, so
this script was built from scratch — it's plain Python, runs anywhere, and has one
dependency (`requests`).

## Setup

1. Make sure you have Python 3.8+ installed (`python3 --version`).
2. Install the one dependency:
   ```
   pip install -r requirements.txt
   ```
   (On some systems you may need `pip install -r requirements.txt --break-system-packages`.)
3. Edit `config.json` to tune it to what you want (see below).

## config.json reference

| Field | What it does |
|---|---|
| `keywords` | Job titles must contain at least one of these (case-insensitive). |
| `exclude_keywords` | Titles containing any of these are skipped (e.g. `"intern"`). |
| `usa_only` | `true` (default) keeps only jobs whose location clearly indicates the USA — full state names, "United States"/"USA"/"US", or a `"City, ST"` pattern. Ambiguous locations like a bare `"Remote"` with no country are excluded to stay strict. Set to `false` to see everything again. |
| `remote_only` | Set `true` to additionally require the word "remote" in the location. |
| `location_includes` | Leave empty (`[]`) for no extra narrowing, or add strings like `"California"`, `"New York"` to further restrict within the USA. |
| `sources.greenhouse_companies` / `lever_companies` / `ashby_companies` | Company board "slugs" — the string in the company's careers URL, e.g. `https://boards.greenhouse.io/anthropic` → slug is `anthropic`. Not every company uses every platform — an unmatched slug is logged as "no board found" and skipped, it won't crash the run. |
| `sources.adzuna` | Set `enabled: true` and fill in `app_id`/`app_key` to turn this on — see below. |
| `sources.usajobs` | Set `enabled: true` and fill in `email`/`api_key` to turn this on — see below. |

### Turning on Adzuna (recommended — biggest coverage boost)

Adzuna aggregates postings from many boards behind one API, searched directly against
its US-only endpoint, so it's a fast way to see far more listings:

1. Register free at https://developer.adzuna.com/ (takes ~2 minutes).
2. Copy your `app_id` and `app_key` into `config.json` under `sources.adzuna`.
3. Set `"enabled": true`.

### Turning on USAJobs (official federal postings)

1. Register free at https://developer.usajobs.gov/APIRequest/Index.
2. Copy your registered email and API key into `config.json` under `sources.usajobs`.
3. Set `"enabled": true`.

## Running it

```
python3 job_checker.py
```

You can also point it at a different config file (handy for testing):
`python3 job_checker.py path/to/other_config.json`

Each run:
1. Pulls current listings from every enabled source.
2. Filters to your keywords, then to USA-only locations, then any extra location/remote settings.
3. Compares against `seen_jobs.json` (jobs already shown to you).
4. Appends only the **new** matches to `jobs_found.csv`, each starting with status `To Apply`.
5. Updates `seen_jobs.json` and `run_log.txt`.

Output is saved to **`/Users/shoaibshaik/Desktop/job_applcation/`** (the folder is
created automatically the first time you run the script if it doesn't already exist).

## Using jobs_found.csv as your application checklist

Every new job lands in the CSV with a `status` column defaulting to **To Apply**.
Open it in Excel/Numbers/Google Sheets and update that column as you work through it —
the script only ever *appends* new rows, so it will never overwrite or reorder edits
you've made to existing ones. A simple workflow:

`To Apply` → `Applied` → `Interview Scheduled` → `Interviewed` → `Offer` / `Rejected`

Since the goal is applying to and interviewing at as many places as possible, it's
worth re-running the script daily (see scheduling below) and working straight down
the `To Apply` rows each time — with 7 sources feeding it, this list should grow
substantially every day.

To start totally fresh (forget what's been seen), delete `seen_jobs.json` and/or
`jobs_found.csv` from that folder.

## Running it automatically on your machine

### macOS / Linux — cron

1. Open your crontab:
   ```
   crontab -e
   ```
2. Add a line to run it every day at 8am (adjust the path to wherever you put this folder):
   ```
   0 8 * * * cd /full/path/to/job-checker && /usr/bin/python3 job_checker.py >> cron_output.log 2>&1
   ```
3. Save and exit. Run `crontab -l` to confirm it's scheduled.

### Windows — Task Scheduler

1. Open **Task Scheduler** → **Create Basic Task**.
2. Name it "Job Checker", set trigger to **Daily** at your preferred time.
3. Action: **Start a program**.
   - Program/script: `python` (or full path to `python.exe`)
   - Add arguments: `job_checker.py`
   - Start in: the full path to this `job-checker` folder
4. Finish. You can right-click the task and choose "Run" to test it immediately.

## Files

| File | Purpose |
|---|---|
| `job_checker.py` | Main script — run this |
| `config.json` | Local (Mac) run settings — saves to your Desktop |
| `config.web.json` | Website deployment settings — saves to `data/` and `docs/` for GitHub Pages, see [DEPLOY.md](DEPLOY.md) |
| `jobs_found.csv` | Growing application checklist — every new job found, with a status column you update |
| `seen_jobs.json` | Internal memory of what's already been shown to you (don't need to touch it) |
| `run_log.txt` | Simple log of each run and how many new jobs it found |
| `docs/index.html` | The website version of the checklist (only present/updated if using `config.web.json`) — has clickable status dropdowns saved per-browser |
| `.github/workflows/daily-job-check.yml` | GitHub Actions workflow that runs the check daily and publishes the site |

## Extending it

- Want to add more companies? Find their careers page and check which platform they
  use (`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, or `jobs.ashbyhq.com/<slug>`)
  and add the slug to the matching list in `config.json`.
- Want a desktop notification instead of/alongside the CSV? Install `plyer`
  (`pip install plyer`) and call `plyer.notification.notify(...)` for each new job
  inside the `if new_jobs:` block in `job_checker.py`.
