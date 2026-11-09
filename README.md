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

## Email digest (Google Sheet + Gmail)
`collector/digest.py` compares the two latest snapshot days and lists trial status changes, newly posted results, new Phase 3 trials, new SEC filings, and milestones due in the next 14 days. It saves `digests/<date>.md` and, on days with news, sends it to the mailer in `apps-script/Code.gs`, which emails every active subscriber in your Google Sheet from your Gmail. Every email has a one-click unsubscribe link.

One-time setup (about 5 minutes):
1. Create a Google Sheet (for example "Pipeline Watch subscribers"). Open Extensions, Apps Script, delete the sample code and paste in `apps-script/Code.gs`. Save.
2. In Apps Script: Project Settings, Script properties, add `SEND_SECRET` with a long random value.
3. Deploy, New deployment, type Web app. Execute as: Me. Who has access: Anyone. Authorize when asked. Copy the web app URL.
4. In this GitHub repo: Settings, Secrets and variables, Actions. Add variable `MAILER_URL` (the web app URL) and secret `MAILER_SECRET` (same value as `SEND_SECRET`).
5. Run the "Daily collect and publish" workflow once from the Actions tab. The sign-up form on the public page goes live and writes to the "Subscribers" tab.

Gmail sends about 100 emails a day on a free account (1,500 on Google Workspace); the mailer stops at the limit.
