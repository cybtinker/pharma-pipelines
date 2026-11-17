#!/usr/bin/env python3
"""Weekly collector for the full company universe and industry pipeline.

Writes one compressed snapshot per run to raw/universe/<YYYY-MM-DD>/:
  companies.json.gz   every exchange-listed SEC filer classified as pharma/biotech
                      (SIC 2834 pharmaceutical preparations, 2836 biological products,
                      plus SIC 8731 research companies that sponsor drug trials), with
                      address, website and recent 8-K/10-K/20-F links
  financials.json.gz  latest annual revenue, R&D, net income and cash from SEC XBRL
                      "frames" (one call per concept covers every filer)
  trials.json.gz      every industry-sponsored Phase 1-3 drug trial on ClinicalTrials.gov
                      that is still running or finished from mid-2024 on, in compact form

Companies listed outside the US (config/universe_extra.json) and private companies
(curated/private.json) are matched to trials by sponsor name at build time
(web/universe_build.py), so no extra calls are needed for them.

Standard library only. Usage: python3 collector/universe.py [--lake PATH] [--skip-trials]
"""
import argparse, datetime as dt, gzip, json, pathlib, re, sys, time, urllib.error, urllib.parse, urllib.request

UA = "pharma-pipelines research tool (github.com/cybtinker/pharma-pipelines) bot@users.noreply.github.com"
SEC_SICS = {"2834": "Pharmaceutical preparations", "2836": "Biological products",
            "8731": "Commercial biological research"}
LISTED = {"Nasdaq", "NYSE", "NYSE American", "NYSE MKT", "CBOE", "NYSE Arca"}

_last = [0.0]
def get(url, params=None, sec=False, raw=False):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html"})
    for attempt in range(5):
        if sec:  # SEC asks for at most 10 requests a second
            wait = 0.13 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
                return body.decode("utf-8", "replace") if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 4:
                raise
        except Exception:
            if attempt == 4:
                raise
        time.sleep(2 ** (attempt + 1))

# ---------- SEC: who is listed and in the drug business ----------
def listed_companies():
    d = get("https://www.sec.gov/files/company_tickers_exchange.json", sec=True)
    out = {}
    for cik, name, ticker, exch in d["data"]:
        if exch not in LISTED or not ticker:
            continue
        cik = str(cik).zfill(10)
        out.setdefault(cik, {"cik": cik, "name": name, "ticker": ticker, "exchange": exch, "tickers": []})
        out[cik]["tickers"].append(ticker)
    return out

def sic_members(sic):
    """All CIKs EDGAR files under one SIC code (includes long-gone companies; we intersect later)."""
    ciks, start = set(), 0
    while True:
        html = get("https://www.sec.gov/cgi-bin/browse-edgar", {
            "action": "getcompany", "SIC": sic, "type": "", "dateb": "", "owner": "include",
            "start": start, "count": 100}, sec=True, raw=True) or ""
        found = set(re.findall(r"CIK=(\d{10})", html))
        ciks |= found
        if len(found) < 100 or start > 20000:
            return ciks
        start += 100

def profile(cik):
    s = get(f"https://data.sec.gov/submissions/CIK{cik}.json", sec=True)
    if not s:
        return None
    biz = (s.get("addresses") or {}).get("business") or {}
    rec = s.get("filings", {}).get("recent", {})
    rows = zip(rec.get("form", []), rec.get("filingDate", []), rec.get("accessionNumber", []),
               rec.get("primaryDocument", []), rec.get("primaryDocDescription", []))
    n = int(cik)
    filings = []
    for f, d, a, doc, desc in rows:
        if f in ("8-K", "6-K", "10-K", "10-Q", "20-F", "40-F") and len(filings) < 12:
            filings.append({"form": f, "date": d, "desc": desc,
                            "url": f"https://www.sec.gov/Archives/edgar/data/{n}/{a.replace('-', '')}/{doc}"})
    return {"sic": s.get("sic"), "sic_desc": s.get("sicDescription"), "name": s.get("name"),
            "former_names": [x.get("name") for x in s.get("formerNames", [])][:3],
            "country": biz.get("stateOrCountryDescription"), "state": biz.get("stateOrCountry"),
            "city": biz.get("city"), "website": s.get("website") or None,
            "fy_end": s.get("fiscalYearEnd"), "category": s.get("category"),
            "exchanges": s.get("exchanges"), "filings": filings}

# ---------- SEC XBRL frames: one call returns a value for every filer ----------
ANNUAL = {"revenue": [("us-gaap", "Revenues"), ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                      ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"), ("ifrs-full", "Revenue")],
          "rnd": [("us-gaap", "ResearchAndDevelopmentExpense"),
                  ("us-gaap", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
                  ("ifrs-full", "ResearchAndDevelopmentExpense")],
          "net_income": [("us-gaap", "NetIncomeLoss"), ("ifrs-full", "ProfitLoss")]}
INSTANT = {"cash": [("us-gaap", "CashAndCashEquivalentsAtCarryingValue"), ("ifrs-full", "CashAndCashEquivalents")]}
CURRENCIES = {"us-gaap": ["USD"], "ifrs-full": ["USD", "EUR", "DKK", "GBP", "CHF", "JPY", "SEK", "CNY", "AUD", "CAD"]}

def frames(wanted):
    today = dt.date.today()
    years = [today.year - 1, today.year - 2]
    quarters = []
    y, q = today.year, (today.month - 1) // 3  # last finished quarter
    for _ in range(4):
        if q == 0:
            y, q = y - 1, 4
        quarters.append(f"CY{y}Q{q}I")
        q -= 1
    out = {}  # cik -> metric -> {val, cur, period, end, concept}
    def take(metric, tax, concept, cur, period):
        d = get(f"https://data.sec.gov/api/xbrl/frames/{tax}/{concept}/{cur}/{period}.json", sec=True)
        for r in (d or {}).get("data", []):
            cik = str(r["cik"]).zfill(10)
            if cik not in wanted:
                continue
            m = out.setdefault(cik, {})
            prev = m.get(metric)
            # keep the newest period; within a period keep the larger revenue concept (a total beats a subset)
            better = (prev is None or prev["period"] < period or
                      (prev["period"] == period and metric == "revenue" and r["val"] > prev["val"]))
            if better:
                m[metric] = {"val": r["val"], "cur": cur, "period": period, "end": r.get("end"), "concept": concept}
    for metric, concepts in ANNUAL.items():
        for yr in reversed(years):
            for tax, concept in concepts:
                for cur in CURRENCIES[tax]:
                    take(metric, tax, concept, cur, f"CY{yr}")
    # previous-year revenue for growth
    prev = {}
    for tax, concept in ANNUAL["revenue"]:
        for cur in CURRENCIES[tax]:
            d = get(f"https://data.sec.gov/api/xbrl/frames/{tax}/{concept}/{cur}/CY{years[1]}.json", sec=True)
            for r in (d or {}).get("data", []):
                cik = str(r["cik"]).zfill(10)
                if cik in wanted and r["val"] > prev.get(cik, {}).get("val", -1e30):
                    prev[cik] = {"val": r["val"], "cur": cur, "period": f"CY{years[1]}"}
    for cik, v in prev.items():
        out.setdefault(cik, {})["revenue_prev"] = v
    for metric, concepts in INSTANT.items():
        for period in reversed(quarters):
            for tax, concept in concepts:
                for cur in CURRENCIES[tax]:
                    take(metric, tax, concept, cur, period)
    return out

# ---------- ClinicalTrials.gov: the whole industry pipeline in ~30 calls ----------
CT_FIELDS = ["NCTId", "BriefTitle", "Acronym", "OverallStatus", "Phase", "Condition", "ConditionMeshTerm",
             "InterventionName", "InterventionType", "LeadSponsorName", "CollaboratorName", "StartDate",
             "PrimaryCompletionDate", "HasResults", "EnrollmentCount", "LastUpdatePostDate"]
PLACEBO = re.compile(r"placebo|vehicle|sham|saline|standard of care|best supportive|usual care|no intervention|"
                     r"investigator'?s choice|physician'?s choice|observation", re.I)

def compact(st):
    p = st.get("protocolSection", {})
    ident, status = p.get("identificationModule", {}), p.get("statusModule", {})
    spons = p.get("sponsorCollaboratorsModule", {})
    design, arms = p.get("designModule", {}), p.get("armsInterventionsModule", {})
    meshes = st.get("derivedSection", {}).get("conditionBrowseModule", {}).get("meshes", [])
    ivs = [i.get("name", "").strip() for i in arms.get("interventions", [])
           if i.get("type") in ("DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT", "RADIATION", "OTHER")
           and i.get("name") and not PLACEBO.search(i.get("name", ""))]
    seen, uniq = set(), []
    for v in ivs:
        if v.lower() not in seen:
            seen.add(v.lower()); uniq.append(v)
    return {"id": ident.get("nctId"), "t": (ident.get("acronym") or ident.get("briefTitle") or "")[:160],
            "s": status.get("overallStatus"), "ph": "/".join(x.replace("PHASE", "") for x in design.get("phases", [])),
            "c": p.get("conditionsModule", {}).get("conditions", [])[:6], "m": [m.get("term") for m in meshes][:6],
            "d": uniq[:6], "sp": spons.get("leadSponsor", {}).get("name"),
            "co": [c.get("name") for c in spons.get("collaborators", [])][:4],
            "start": (status.get("startDateStruct") or {}).get("date"),
            "pcd": (status.get("primaryCompletionDateStruct") or {}).get("date"),
            "pcd_type": (status.get("primaryCompletionDateStruct") or {}).get("type"),
            "res": bool(st.get("hasResults")), "n": (design.get("enrollmentInfo") or {}).get("count"),
            "upd": (status.get("lastUpdatePostDateStruct") or {}).get("date")}

def industry_trials(since):
    adv = (f"AREA[LeadSponsorClass]INDUSTRY AND AREA[Phase](PHASE1 OR PHASE2 OR PHASE3) "
           f"AND AREA[StudyType]INTERVENTIONAL "
           f"AND (AREA[PrimaryCompletionDate]RANGE[{since},MAX] OR AREA[OverallStatus](RECRUITING OR "
           f"NOT_YET_RECRUITING OR ACTIVE_NOT_RECRUITING OR ENROLLING_BY_INVITATION))")
    out, token, page = [], None, 0
    while True:
        params = {"filter.advanced": adv, "fields": ",".join(CT_FIELDS), "pageSize": 1000, "countTotal": "true"}
        if token:
            params["pageToken"] = token
        d = get("https://clinicaltrials.gov/api/v2/studies", params)
        if page == 0:
            print("clinicaltrials.gov total:", d.get("totalCount"), flush=True)
        page += 1
        out += [compact(s) for s in d.get("studies", [])]
        token = d.get("nextPageToken")
        if not token:
            return out

def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)", flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default=str(pathlib.Path(__file__).resolve().parents[1]))
    ap.add_argument("--skip-trials", action="store_true")
    args = ap.parse_args()
    lake = pathlib.Path(args.lake)
    today = dt.date.today()
    outdir = lake / "raw" / "universe" / today.isoformat()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    listed = listed_companies()
    print("exchange-listed SEC filers:", len(listed), flush=True)
    members = {}
    for sic in SEC_SICS:
        try:
            got = sic_members(sic)
        except Exception as e:
            print("SIC list failed", sic, e, flush=True); got = set()
        print(f"SIC {sic}: {len(got)} filers ever, {len(got & listed.keys())} listed now", flush=True)
        for c in got & listed.keys():
            members.setdefault(c, sic)
    # watchlist companies always belong, whatever their SIC (e.g. JNJ is filed under 2834? not guaranteed)
    wl = json.loads((lake / "config/watchlist.json").read_text())
    for c in wl["companies"]:
        if c.get("sec_cik") in listed:
            members.setdefault(c["sec_cik"], "watchlist")
    companies = []
    for i, cik in enumerate(sorted(members)):
        row = dict(listed[cik])
        try:
            pr = profile(cik) or {}
        except Exception as e:
            pr = {"error": str(e)}
        row.update(pr)
        row["sic_basis"] = members[cik]
        companies.append(row)
        if i % 100 == 0:
            print("profiles", i, "/", len(members), flush=True)
    write(outdir / "companies.json.gz", {"fetched_at": stamp, "sics": SEC_SICS, "companies": companies})

    try:
        fin = frames(set(members))
    except Exception as e:
        print("frames failed", e, flush=True); fin = {"error": str(e)}
    write(outdir / "financials.json.gz", {"fetched_at": stamp, "source": "SEC XBRL frames API", "by_cik": fin})

    if not args.skip_trials:
        since = (today - dt.timedelta(days=500)).isoformat()
        trials = industry_trials(since)
        write(outdir / "trials.json.gz", {"fetched_at": stamp, "since": since,
                                          "query": "industry-sponsored interventional Phase 1-3, active or primary completion since 'since'",
                                          "trials": trials})

if __name__ == "__main__":
    sys.exit(main())
