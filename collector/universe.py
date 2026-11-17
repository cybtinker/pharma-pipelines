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

# SEC's fair-access policy asks automated clients to give a name and a contact address.
UA = "pharma-pipeline-tracker/0.1 (research tool)"  # same identification as collector/collect.py
SEC_SICS = {"2834": "Pharmaceutical preparations", "2836": "Biological products",
            "8731": "Commercial biological research"}
LISTED = {"Nasdaq", "NYSE", "NYSE American", "NYSE MKT", "CBOE", "NYSE Arca"}

_last = [0.0]
def get(url, params=None, sec=False, raw=False):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    for attempt in range(5):
        if sec:  # SEC asks for at most 10 requests a second
            wait = 0.13 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
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
# Only data.sec.gov is used (www.sec.gov refuses GitHub's runners). Every active filer reports
# shares outstanding, so the dei "frames" give the full list of CIKs; each filer's submissions
# file then gives its SIC code, tickers and exchanges. Non-drug filers are cached in
# config/sec_sic_cache.json so later runs only look up new filers.
def candidate_ciks():
    today = dt.date.today()
    y, q = today.year, (today.month - 1) // 3
    ciks = {}
    for _ in range(4):
        if q == 0:
            y, q = y - 1, 4
        d = get(f"https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json", sec=True)
        for r in (d or {}).get("data", []):
            ciks.setdefault(str(r["cik"]).zfill(10), r.get("entityName"))
        q -= 1
    # filers that skip the cover-page share count in frames: catch them through their financial statements
    for tax, concept, curs in [("us-gaap", "Assets", ["USD"]),
                               ("ifrs-full", "Assets", ["USD", "EUR", "DKK", "GBP", "CHF", "JPY", "CNY", "AUD", "CAD", "SEK"])]:
        for cur in curs:
            for period in (f"CY{today.year - 1}Q4I", f"CY{today.year}Q2I"):
                d = get(f"https://data.sec.gov/api/xbrl/frames/{tax}/{concept}/{cur}/{period}.json", sec=True)
                for r in (d or {}).get("data", []):
                    ciks.setdefault(str(r["cik"]).zfill(10), r.get("entityName"))
    seed = pathlib.Path(__file__).resolve().parents[1] / "config" / "universe_seed_ciks.json"
    if seed.exists():
        for c in json.loads(seed.read_text())["ciks"]:
            ciks.setdefault(str(c).zfill(10), None)
    return ciks

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
    return {"tickers": s.get("tickers") or [], "exchanges_all": s.get("exchanges") or [], "sic": s.get("sic"), "sic_desc": s.get("sicDescription"), "name": s.get("name"),
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

    cache_path = lake / "config" / "sec_sic_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    cands = candidate_ciks()
    print("active SEC filers:", len(cands), "cached:", len(cache.keys() & cands.keys()), flush=True)
    wl = json.loads((lake / "config/watchlist.json").read_text())
    wl_ciks = {c.get("sec_cik") for c in wl["companies"]}
    profiles = {}
    todo = [c for c in sorted(cands.keys() | wl_ciks) if c and (c not in cache or cache[c][0] in SEC_SICS or c in wl_ciks)]
    t0 = time.time()
    for i, cik in enumerate(todo):
        if time.time() - t0 > 40 * 60:
            print("time budget reached; the rest is picked up next run", flush=True); break
        try:
            pr = profile(cik)
        except Exception as e:
            print("profile failed", cik, e, flush=True); continue
        if not pr:
            continue
        cache[cik] = [pr.get("sic"), pr.get("tickers")[:3], pr.get("exchanges_all")[:3]]
        listed = [t for t, x in zip(pr["tickers"], pr["exchanges_all"]) if x in LISTED]
        if (pr.get("sic") in SEC_SICS or cik in wl_ciks) and listed:
            ex = next(x for t, x in zip(pr["tickers"], pr["exchanges_all"]) if x in LISTED)
            pr.update({"cik": cik, "ticker": listed[0], "exchange": ex,
                       "sic_basis": "watchlist" if pr.get("sic") not in SEC_SICS else pr.get("sic")})
            profiles[cik] = pr
        if i % 500 == 0:
            print("profiles", i, "/", len(todo), "members so far", len(profiles), flush=True)
    cache_path.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
    members = set(profiles)
    companies = [profiles[c] for c in sorted(profiles)]
    print("pharma/biotech companies listed on US exchanges:", len(companies), flush=True)
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
