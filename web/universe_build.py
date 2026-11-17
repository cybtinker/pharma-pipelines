#!/usr/bin/env python3
"""Turn the newest raw/universe snapshot into the files the web page reads.

Inputs (all in the lake, no network):
  raw/universe/<latest>/{companies,financials,trials}.json.gz   from collector/universe.py
  config/universe_extra.json   drugmakers listed outside the US
  config/sponsor_aliases.json  subsidiary and acquired-company sponsor names
  curated/private.json         private companies with sourced facts
  curated/competition.json     hand-checked race boards (gets an "auto" rival list per market)
  config/watchlist.json        the hand-curated companies

Outputs:
  web/data/universe.json       small index of every company (embedded in the page for search)
  web/data/co/<id>.json        one file per company: profile, financials, pipeline by drug, rivals
  web/data/competition_auto.json   per curated market, every other industry trial sponsor in the same illness
"""
import collections, datetime as dt, gzip, json, pathlib, re, sys

root = pathlib.Path(__file__).resolve().parents[1]
OUT = root / "web" / "data"
TODAY = dt.date.today().isoformat()

LEGAL = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "lp", "plc", "sa", "ag",
         "nv", "bv", "se", "gmbh", "kgaa", "as", "ab", "oyj", "spa", "sas", "kk", "holdings", "holding", "group", "the",
         "adr", "de", "cv", "pte", "pty", "srl", "asa", "a/s", "s.a.", "n.v."}
GENERIC = {"pharmaceuticals", "pharmaceutical", "pharma", "therapeutics", "therapeutic", "biosciences", "bioscience",
           "biotherapeutics", "biopharma", "biopharmaceuticals", "biopharmaceutical", "biologics", "biotech",
           "biotechnology", "sciences", "science", "medicines", "medical", "oncology", "research", "development", "and",
           "us", "usa", "international", "global", "laboratories", "labs", "bio", "life", "health", "healthcare"}

def norm(s):
    s = (s or "").lower().replace("&", " and ").replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = s.split()
    while words and words[-1] in LEGAL:
        words.pop()
    return " ".join(words)

def core(n):
    return " ".join(w for w in n.split() if w not in GENERIC and w not in LEGAL)

US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split())
KEEP_UPPER = {"PLC", "AG", "SA", "NV", "SE", "ASA", "AB", "LLC", "LP", "II", "III", "USA", "US", "UK", "KGAA", "BV", "CO", "AS", "ADR", "MD", "RNA", "DNA", "AI"}

SUFFIX = re.compile(r",?\s+(inc\.?|incorporated|corp\.?|corporation|ltd\.?|limited|plc\.?|&|n\.?v\.?|s\.?a\.?|ag|se|a/s|ab|asa|co\.?|co\.?,? ltd\.?|holdings,? inc\.?|holdings|/[a-z]{2,3}/?)$", re.I)

def pretty(name):
    name = (name or "").strip()
    for _ in range(3):
        name = SUFFIX.sub("", name).strip(" ,")
    allcaps = name.isupper() or name.islower()
    name = " ".join(w if w.strip(".,") in KEEP_UPPER or (allcaps and len(w) <= 3 and w.isupper()) or not (allcaps or (w.isupper() and len(w) > 4)) else w.capitalize()
                    for w in name.split())
    return name.strip(" ,")

def latest_snapshot():
    base = root / "raw" / "universe"
    days = sorted(p for p in base.glob("*") if (p / "trials.json.gz").exists()) if base.exists() else []
    return days[-1] if days else None

def load(p):
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)

PH_RANK = {"1": 1, "1/2": 2, "2": 3, "2/3": 4, "3": 5}
PH_LABEL = {"1": "Phase 1", "1/2": "Phase 1/2", "2": "Phase 2", "2/3": "Phase 2/3", "3": "Phase 3", "": "n/a"}
ACTIVE = {"RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"}
STATUS = {"RECRUITING": "Recruiting", "NOT_YET_RECRUITING": "Not yet recruiting", "ACTIVE_NOT_RECRUITING": "Active, not recruiting",
          "ENROLLING_BY_INVITATION": "Enrolling by invitation", "COMPLETED": "Completed", "TERMINATED": "Stopped early",
          "WITHDRAWN": "Withdrawn", "SUSPENDED": "Suspended", "UNKNOWN": "Status unknown"}
GENERIC_DRUGS = re.compile(r"^(chemotherapy|standard|best|docetaxel|paclitaxel|carboplatin|cisplatin|pemetrexed|gemcitabine|"
                           r"dexamethasone|prednisone|metformin|insulin|rituximab|fulvestrant|letrozole|capecitabine|"
                           r"oxaliplatin|fluorouracil|5-fu|leucovorin|taxane|nab-paclitaxel|doxorubicin|irinotecan|etoposide|temozolomide|bendamustine|azacitidine|cytarabine|folfox|folfiri|bevacizumab|cyclophosphamide|lenalidomide|"
                           r"methotrexate|acetaminophen|paracetamol|midazolam|itraconazole|rifampin|omeprazole)\b", re.I)

DOSE = re.compile(r"\s*(\(?\b(dose|tablets?|capsules?|injection|infusion|cohort|arm|part|regimen|oral|iv|sc)\b.*|\s\d+(\.\d+)?\s*(mg|mcg|µg|ug|g|ml|mg/kg|iu)\b.*)$", re.I)
SALT = re.compile(r"\s+(sodium|hydrochloride|hcl|potassium|mesylate|tartrate|acetate|maleate|citrate|besylate|tosylate)\b", re.I)
NOT_A_CONDITION = {"Healthy", "Healthy Volunteers", "Healthy Participants", "Healthy Subjects", "Recurrence", "Neoplasms", "Disease"}

def clean_drug(d):
    d = SALT.sub("", DOSE.sub("", d or "")).strip(" ,;:-")
    return d or None

def drug_key(d):
    words = re.sub(r"\(.*?\)", " ", d.lower()).split()[:2]
    return re.sub(r"[^a-z0-9]+", "", "".join(words))[:40]

def build():
    snap = latest_snapshot()
    if not snap:
        print("universe: no snapshot yet, skipping"); return None
    comp_raw = load(snap / "companies.json.gz")
    fin = load(snap / "financials.json.gz").get("by_cik", {})
    tr = load(snap / "trials.json.gz")
    trials = tr["trials"]
    extra = json.loads((root / "config/universe_extra.json").read_text())["companies"]
    aliases = json.loads((root / "config/sponsor_aliases.json").read_text())["aliases"]
    private = json.loads((root / "curated/private.json").read_text())["companies"]
    wl = json.loads((root / "config/watchlist.json").read_text())["companies"]
    watch = {c["ticker"] for c in wl}
    wl_names = {c["ticker"]: c["name"] for c in wl}

    # ---- company table ----
    cos = {}
    overrides = json.loads((root / "config/universe_exclude.json").read_text()).get("display_names", {})
    exclude = set(json.loads((root / "config/universe_exclude.json").read_text())["tickers"])
    for c in comp_raw["companies"]:
        cid = c["ticker"].upper()
        if cid in exclude:
            continue
        f = fin.get(c["cik"], {})
        st = (c.get("state") or "").upper()
        country = "United States" if st in US_STATES else ((c.get("country") or "").title() or None)
        cos[cid] = {"id": cid, "name": wl_names.get(cid) or overrides.get(cid) or pretty(c.get("name")), "legal_name": c.get("name"), "ticker": c["ticker"],
                    "exchange": c["exchange"], "country": country, "kind": "public",
                    "sic": c.get("sic"), "sic_desc": c.get("sic_desc"), "cik": c["cik"], "website": c.get("website"),
                    "filings": c.get("filings", [])[:8], "fin": f, "sic_basis": c.get("sic_basis"),
                    "sponsors": aliases.get(cid, [])}
    for e in extra:
        cos[e["id"]] = {"id": e["id"], "name": e["name"], "ticker": e["ticker"], "exchange": e["exchange"],
                        "country": e["country"], "kind": "public_intl", "ir": e.get("ir"), "sponsors": e["sponsors"], "fin": None}
    for p in private:
        cos[p["id"]] = {"id": p["id"], "name": p["name"], "ticker": None, "exchange": None, "country": p.get("country"),
                        "kind": p["kind"], "ownership": p.get("ownership"), "focus": p.get("focus"), "lead": p.get("lead"),
                        "stage": p.get("stage"), "website": p.get("website"), "pfin": p.get("financials"),
                        "sponsors": p.get("sponsors", []), "fin": None}

    # ---- sponsor -> company matcher ----
    prefix = sorted(((norm(a), cid) for cid, c in cos.items() for a in c["sponsors"] if norm(a)), key=lambda x: -len(x[0]))
    exact, cores = {}, collections.defaultdict(set)
    for cid, c in cos.items():
        n = norm(c["name"])
        exact.setdefault(n, cid)
        k = core(n)
        if len(k) >= 4:
            cores[k].add(cid)
    by_len = sorted(((n, cid) for n, cid in exact.items() if len(n) >= 8 and core(n)), key=lambda x: -len(x[0]))
    cache = {}
    def match(sponsor):
        if sponsor in cache:
            return cache[sponsor]
        n = norm(sponsor)
        hit = None
        for a, cid in prefix:
            if n == a or n.startswith(a + " "):
                hit = cid; break
        if not hit:
            hit = exact.get(n)
        if not hit:  # "Harmony Biosciences Management" -> Harmony Biosciences
            for cn, cid in by_len:
                if n.startswith(cn + " "):
                    hit = cid; break
        if not hit:
            k = core(n)
            if len(k) >= 4 and len(cores.get(k, ())) == 1:
                hit = next(iter(cores[k]))
        cache[sponsor] = hit
        return hit

    by_co = collections.defaultdict(list)          # cid -> [(trial, role)]
    by_mesh = collections.defaultdict(list)        # mesh term -> [trial]
    for t in trials:
        t["_lead"] = match(t.get("sp") or "")
        if t["_lead"]:
            by_co[t["_lead"]].append((t, "lead"))
        for col in t.get("co") or []:
            m = match(col)
            if m and m != t["_lead"]:
                by_co[m].append((t, "partner"))
        for m in set((t.get("m") or []) or (t.get("c") or [])[:2]) - NOT_A_CONDITION:
            by_mesh[m].append(t)

    def phase_of(t):
        return PH_RANK.get(t.get("ph", ""), 0)

    owner = collections.defaultdict(collections.Counter)  # drug key -> lead company -> trials
    for t in trials:
        for d in t.get("d") or []:
            cd = clean_drug(d)
            if cd and t["_lead"]:
                owner[drug_key(cd)][t["_lead"]] += 1

    def own_names(t, cid):
        names = [clean_drug(d) for d in (t.get("d") or []) if not GENERIC_DRUGS.match(d)]
        names = [n for n in names if n]
        mine = []
        for n in names:
            o = owner.get(drug_key(n))
            top = o.most_common(1)[0] if o else None
            # skip another company's drug used in combination (e.g. Keytruda in a Pfizer trial)
            if top and top[0] != cid and o[cid] * 3 < top[1]:
                continue
            mine.append(n)
        return mine or names[:1]

    def drug_rows(items, cid):
        drugs = {}
        for t, role in items:
            names = own_names(t, cid) or ["(not named)"]
            for d in names[:3]:
                k = drug_key(d)
                r = drugs.setdefault(k, {"drug": d, "ph": "", "conds": collections.Counter(), "trials": [], "next": None,
                                         "active": 0, "results": 0, "partner": role == "partner"})
                if phase_of(t) > PH_RANK.get(r["ph"], 0):
                    r["ph"] = t.get("ph", "")
                for c in [c for c in (t.get("m") or t.get("c") or []) if c not in NOT_A_CONDITION][:3]:
                    r["conds"][c] += 1
                act = t.get("s") in ACTIVE
                r["active"] += act
                r["results"] += bool(t.get("res"))
                if role == "lead":
                    r["partner"] = False
                if act and t.get("pcd") and t["pcd"] >= TODAY[:len(t["pcd"])] and (not r["next"] or t["pcd"] < r["next"]):
                    r["next"] = t["pcd"]
                r["trials"].append({"id": t["id"], "t": t["t"], "ph": PH_LABEL.get(t.get("ph", ""), t.get("ph")),
                                    "s": STATUS.get(t.get("s"), t.get("s")), "pcd": t.get("pcd"), "est": t.get("pcd_type") == "ESTIMATED",
                                    "res": t.get("res"), "n": t.get("n"), "role": role})
        out = []
        for r in drugs.values():
            r["trials"].sort(key=lambda x: (x["pcd"] or "9999"), reverse=True)
            out.append({"drug": r["drug"], "phase": PH_LABEL.get(r["ph"], r["ph"]), "rank": PH_RANK.get(r["ph"], 0),
                        "conds": [c for c, _ in r["conds"].most_common(4)], "next": r["next"], "active": r["active"],
                        "results": r["results"], "partner": r["partner"], "trials": r["trials"][:12], "n_trials": len(r["trials"])})
        out.sort(key=lambda r: (-r["rank"], -r["active"], r["drug"].lower()))
        return out

    def sponsor_label(t):
        cid = t["_lead"]
        return (cos[cid]["name"], cid) if cid else (t.get("sp"), None)

    def rivals(cid, conds, limit=14):
        out = {}
        for cond in conds:
            seen = {}
            for t in by_mesh.get(cond, []):
                if t["_lead"] == cid or t.get("s") not in ACTIVE:
                    continue
                name, rid = sponsor_label(t)
                drug = (own_names(t, t["_lead"]) or [None])[0]
                if not drug:
                    continue
                k = (name, drug_key(drug))
                if k not in seen or phase_of(t) > PH_RANK.get(seen[k]["ph_code"], 0):
                    seen[k] = {"co": name, "id": rid, "drug": drug, "ph_code": t.get("ph", ""), "nct": t["id"]}
            rows = sorted(seen.values(), key=lambda r: -PH_RANK.get(r["ph_code"], 0))[:limit]
            for r in rows:
                r["phase"] = PH_LABEL.get(r.pop("ph_code"), "")
            if rows:
                out[cond] = {"total_sponsors": len({r["co"] for r in seen.values()}), "rows": rows}
        return out

    # ---- write per-company files and the index ----
    (OUT / "co").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "co").glob("*.json"):
        old.unlink()
    index = []
    def money(f, k):
        v = (f or {}).get(k)
        return {"val": v["val"], "cur": v["cur"], "period": v["period"]} if v else None
    for cid, c in cos.items():
        items = by_co.get(cid, [])
        if c["kind"] == "public":
            # SIC 8731 research firms only count when they actually sponsor drug trials
            if c.get("sic_basis") == "8731" and not items and cid not in watch:
                continue
        drugs = drug_rows(items, cid)
        top_conds = [cnd for cnd, _ in collections.Counter(cn for d in drugs for cn in d["conds"][:2]).most_common(8)]
        fin = c.get("fin") or {}
        rec = {"id": cid, "name": c["name"], "ticker": c.get("ticker"), "exchange": c.get("exchange"), "country": c.get("country"),
               "kind": c["kind"], "curated": cid in watch,
               "profile": {k: c.get(k) for k in ("sic", "sic_desc", "website", "ir", "ownership", "focus", "lead", "stage", "cik")},
               "financials": ({"revenue": money(fin, "revenue"), "revenue_prev": money(fin, "revenue_prev"),
                               "rnd": money(fin, "rnd"), "net_income": money(fin, "net_income"), "cash": money(fin, "cash"),
                               "source": "SEC XBRL filings"} if fin else None),
               "private_financials": c.get("pfin"), "filings": c.get("filings", []),
               "drugs": drugs, "rivals": rivals(cid, top_conds), "as_of": tr.get("fetched_at", "")[:10]}
        (OUT / "co" / f"{re.sub(r'[^A-Za-z0-9._-]', '_', cid)}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
        rev = (fin.get("revenue") or {}) if fin else {}
        ph3 = sum(1 for d in drugs if d["rank"] >= 4)
        index.append([cid, c["name"], c.get("ticker") or "", c.get("exchange") or "", c.get("country") or "", c["kind"],
                      sum(d["active"] for d in drugs), ph3, round(rev["val"] / 1e9, 2) if rev and rev.get("cur") == "USD" else None,
                      1 if cid in watch else 0])
    index.sort(key=lambda r: (-(r[8] or 0), -r[6], r[1]))
    meta = {"as_of": tr.get("fetched_at", "")[:10], "trials": len(trials), "since": tr.get("since"),
            "fields": ["id", "name", "ticker", "exchange", "country", "kind", "active_trials", "late_stage_drugs", "revenue_usd_b", "curated"],
            "cutoff": "Public: every company listed on NYSE, Nasdaq or NYSE American that the SEC classifies as pharmaceutical preparations (SIC 2834) or biological products (SIC 2836), plus SIC 8731 research companies that sponsor drug trials, plus major drugmakers listed only outside the US. Private: notable privately held drugmakers and well-funded private biotechs, with sourced figures. OTC and shell companies are left out.",
            "rows": index}
    (OUT / "universe.json").write_text(json.dumps(meta, separators=(",", ":"), ensure_ascii=False))

    # ---- curated race boards: every other trial sponsor in the same illness ----
    comp = json.loads((root / "curated/competition.json").read_text())
    auto = {}
    for m in comp["markets"]:
        terms = [t.lower() for t in m.get("ct_terms", [])]
        if not terms:
            continue
        mine = {re.sub(r"[^a-z0-9]", "", p["drug"].split(" ")[0].lower()) for p in m["players"]}
        seen = {}
        for t in trials:
            if t.get("s") not in ACTIVE or phase_of(t) < 3:
                continue
            hay = " ".join((t.get("m") or []) + (t.get("c") or [])).lower()
            if not any(term in hay for term in terms):
                continue
            if m.get("ct_must") and not re.search(m["ct_must"], " ".join(t.get("d") or []) + " " + t["t"], re.I):
                continue
            drug = (own_names(t, t["_lead"]) or [None])[0]
            if not drug or re.sub(r"[^a-z0-9]", "", drug.split(" ")[0].lower()) in mine:
                continue
            name, rid = sponsor_label(t)
            k = (name, drug_key(drug))
            if k not in seen or phase_of(t) > PH_RANK.get(seen[k]["ph_code"], 0):
                seen[k] = {"co": name, "id": rid, "drug": drug, "ph_code": t.get("ph", ""), "nct": t["id"]}
        rows = sorted(seen.values(), key=lambda r: (-PH_RANK.get(r["ph_code"], 0), r["co"] or ""))
        for r in rows:
            r["phase"] = PH_LABEL.get(r.pop("ph_code"), "")
        auto[m["id"]] = {"n": len(rows), "sponsors": len({r["co"] for r in rows}), "rows": rows[:60]}
    (OUT / "competition_auto.json").write_text(json.dumps(auto, separators=(",", ":"), ensure_ascii=False))
    unmatched = collections.Counter(t.get("sp") for t in trials if not t["_lead"])
    (OUT / "unmatched_sponsors.json").write_text(json.dumps(unmatched.most_common(300), ensure_ascii=False, indent=0))
    print(f"universe: {len(index)} companies, {len(trials)} trials, matched {sum(1 for t in trials if t['_lead'])} to a company")
    return meta

if __name__ == "__main__":
    build()
