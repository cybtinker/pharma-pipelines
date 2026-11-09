#!/usr/bin/env python3
"""Build web/index.html from web/template.html and curated/drugs.json."""
import datetime, json, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
data = json.loads((root / "curated/drugs.json").read_text())
html = (root / "web/template.html").read_text()
html = html.replace("/*DATA*/[]", json.dumps(data)).replace("/*ASOF*/", datetime.date.today().isoformat())
(root / "web/index.html").write_text(html)
print("wrote web/index.html with", len(data), "drugs")
