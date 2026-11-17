#!/usr/bin/env python3
"""Build web/index.html (and web/data/) from web/template.html and the curated + universe data."""
import datetime, json, os, pathlib, sys
root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "web"))
import universe_build  # noqa: E402  writes web/data/{universe.json, co/*.json, competition_auto.json}

drugs = json.loads((root / "curated/drugs.json").read_text())
comp = json.loads((root / "curated/competition.json").read_text())
mkt = (root / "curated/market.json").read_text()
briefs = (root / "curated/briefs.json").read_text()
appr = (root / "curated/approved.json").read_text()
launch = (root / "curated/launches.json").read_text()
uni = universe_build.build() or {"rows": []}
html = (root / "web/template.html").read_text()
html = (html.replace("/*DATA*/[]", json.dumps(drugs))
            .replace("/*COMP*/{markets:[]}", json.dumps(comp))
            .replace("/*UNI*/{rows:[]}", json.dumps(uni, separators=(",", ":"), ensure_ascii=False))
            .replace("/*MKT*/{rows:[]}", mkt).replace("/*BRIEFS*/{}", briefs).replace("/*APPR*/{products:[],erosion:{}}", appr).replace("/*LAUNCH*/{launches:[],ramp:[1]}", launch)
            .replace("/*SIGNUP_URL*/", os.environ.get("SIGNUP_URL", ""))
            .replace("/*ASOF*/", datetime.date.today().isoformat()))
(root / "web/index.html").write_text(html)
print("wrote web/index.html:", len(drugs), "drugs,", len(comp["markets"]), "markets,", len(uni["rows"]), "companies in search")
