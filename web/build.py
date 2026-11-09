#!/usr/bin/env python3
"""Build web/index.html from web/template.html, curated/drugs.json and curated/competition.json."""
import datetime, json, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
drugs = json.loads((root / "curated/drugs.json").read_text())
comp = json.loads((root / "curated/competition.json").read_text())
mkt = (root / "curated/market.json").read_text()
briefs = (root / "curated/briefs.json").read_text()
html = (root / "web/template.html").read_text()
html = (html.replace("/*DATA*/[]", json.dumps(drugs))
            .replace("/*COMP*/{markets:[]}", json.dumps(comp))
            .replace("/*MKT*/{rows:[]}", mkt).replace("/*BRIEFS*/{}", briefs)
            .replace("/*ASOF*/", datetime.date.today().isoformat()))
(root / "web/index.html").write_text(html)
print("wrote web/index.html:", len(drugs), "drugs,", len(comp["markets"]), "markets")
