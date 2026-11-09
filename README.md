# Pharma pipeline data lake

Repeatable collection of public pipeline data for a watchlist of drug companies.

## Layout
- `config/watchlist.json`: companies to track (ticker, trial sponsor name, SEC CIK). Add a company here and it is picked up on the next run.
- `collector/collect.py`: pulls from free public sources and saves dated snapshots. Standard-library Python, no installs.
- `raw/<source>/<YYYY-MM-DD>/<TICKER>.json`: untouched snapshots, one folder per run day. Never edited, so history is kept and day-to-day changes can trigger alerts.
  Sources: ClinicalTrials.gov (trials, phases, dates, results), openFDA (approvals), SEC EDGAR (8-K, 10-Q, 10-K filings, where press releases and investor decks are filed).
- `curated/drugs.json`: the cleaned, readable table the web page reads. One row per drug with illness, drug type, stage, next event and date, latest result, competitors and source links.

## Run
    python3 collector/collect.py

## Status (2026-11-09)
- `curated/drugs.json` is seeded by hand from Q3 2026 earnings coverage and company releases (25 drugs across MRNA, PFE, ABBV, CRSP). Every row has source links. Rows with `needs_verification` set rest on secondary or summarized sources.
- Competitor names are a first pass from general knowledge and will be checked in the competitor mapping step.
- The collector runs daily on GitHub Actions (see Automation).

## Automation
`.github/workflows/daily.yml` runs every day at 11:30 UTC (and on demand from the Actions tab). It runs the collector, commits the new snapshot under `raw/`, rebuilds the web page and publishes it to GitHub Pages.

To turn on the page once: repository Settings, Pages, Source: GitHub Actions.

## Web page
`web/template.html` plus `curated/drugs.json` are built into `web/index.html` by `python3 web/build.py`. Filters: ticker chips, free-text search (ticker, drug, illness, competitor), stage, drug type, and an upcoming-dates window.

## Email digest
`collector/digest.py` compares the two latest snapshot days and lists trial status changes, newly posted results, new Phase 3 trials, new SEC filings, and milestones due in the next 14 days. It saves `digests/<date>.md` and, when there is news, emails it through Buttondown.

Setup (one time):
1. Create a Buttondown account and copy the API key (Settings, API).
2. In this repo: Settings, Secrets and variables, Actions. Add secret `BUTTONDOWN_API_KEY`, and add variable `BUTTONDOWN_USERNAME` (your Buttondown username).
3. The sign-up form on the page then posts to your Buttondown list. Phone numbers are stored as the subscriber field `phone`.
