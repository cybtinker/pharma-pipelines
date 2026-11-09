#!/usr/bin/env python3
"""Repeatable collector for the pharma pipeline data lake.

Pulls from free public sources for every company in config/watchlist.json and
writes immutable snapshots to raw/<source>/<YYYY-MM-DD>/<ticker>.json.
Re-running on the same day overwrites that day's snapshot; earlier days are kept,
so the lake holds a full history and changes can be diffed for alerts.

Sources:
  - ClinicalTrials.gov API v2 (trials, phases, status, completion dates, results flag)
  - openFDA drugsfda (approvals and application history)
  - SEC EDGAR submissions (8-K/10-Q/10-K filings, where press releases and decks land)

Standard library only. Usage: python3 collector/collect.py [--lake PATH]
"""
import argparse, datetime as dt, json, pathlib, time, urllib.parse, urllib.request

UA = "pharma-pipeline-tracker/0.1 (research tool)"

def get_json(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # retry with backoff on transient errors
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))

def clinical_trials(company, phases):
    studies, token = [], None
    while True:
        params = {
            "query.spons": company["sponsor_query"],
            "filter.advanced": "AREA[Phase](" + " OR ".join(phases) + ")",
            "pageSize": 200,
            "fields": ",".join([
                "NCTId", "BriefTitle", "Acronym", "OverallStatus", "Phase", "Condition",
                "InterventionName", "InterventionType", "LeadSponsorName",
                "StartDate", "PrimaryCompletionDate", "CompletionDate",
                "LastUpdatePostDate", "HasResults", "EnrollmentCount"]),
        }
        if token:
            params["pageToken"] = token
        page = get_json("https://clinicaltrials.gov/api/v2/studies", params)
        studies += page.get("studies", [])
        token = page.get("nextPageToken")
        if not token:
            return studies

def fda_approvals(company):
    try:
        return get_json("https://api.fda.gov/drug/drugsfda.json", {
            "search": f'sponsor_name:"{company["name"]}"', "limit": 100}).get("results", [])
    except Exception as e:  # openFDA returns 404 when nothing matches
        return {"error": str(e)}

def sec_filings(company, forms):
    data = get_json(f"https://data.sec.gov/submissions/CIK{company['sec_cik']}.json")
    recent = data.get("filings", {}).get("recent", {})
    rows = zip(recent.get("form", []), recent.get("filingDate", []),
               recent.get("accessionNumber", []), recent.get("primaryDocument", []),
               recent.get("primaryDocDescription", []))
    cik = int(company["sec_cik"])
    return [{"form": f, "date": d, "description": desc,
             "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{a.replace('-', '')}/{doc}"}
            for f, d, a, doc, desc in rows if f in forms][:60]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default=str(pathlib.Path(__file__).resolve().parents[1]))
    args = ap.parse_args()
    lake = pathlib.Path(args.lake)
    cfg = json.loads((lake / "config/watchlist.json").read_text())
    today = dt.date.today().isoformat()
    for c in cfg["companies"]:
        for source, fn in [("clinicaltrials", lambda: clinical_trials(c, cfg["trial_phases"])),
                           ("openfda", lambda: fda_approvals(c)),
                           ("sec", lambda: sec_filings(c, cfg["sec_forms"]))]:
            out = lake / "raw" / source / today / f"{c['ticker']}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                payload = {"fetched_at": dt.datetime.utcnow().isoformat() + "Z", "data": fn()}
            except Exception as e:
                payload = {"fetched_at": dt.datetime.utcnow().isoformat() + "Z", "error": str(e)}
            out.write_text(json.dumps(payload, indent=1))
            n = len(payload.get("data", [])) if isinstance(payload.get("data"), list) else "?"
            print(f"{c['ticker']:5} {source:15} {n} records -> {out.relative_to(lake)}")

if __name__ == "__main__":
    main()
