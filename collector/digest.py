#!/usr/bin/env python3
"""Build a daily change digest from the data lake and email it to subscribers.

Compares the two most recent snapshot days under raw/ and reports:
  - trials whose status, primary completion date or results flag changed, and new late-stage trials
  - new SEC filings (8-K, 10-Q, 10-K)
  - drug milestones coming up in the next 14 days (curated/drugs.json)

The digest is saved to digests/<YYYY-MM-DD>.md. When MAILER_URL and MAILER_SECRET are set and
there is something to report, it is posted to the Google Apps Script mailer (apps-script/Code.gs),
which emails every active subscriber in the Google Sheet from Gmail.

Usage: python3 collector/digest.py [--lake PATH] [--dry-run]
"""
import argparse, datetime as dt, json, os, pathlib, re, urllib.parse, urllib.request

NAMES = {"LLY": "Eli Lilly", "NVO": "Novo Nordisk", "PFE": "Pfizer", "ABBV": "AbbVie", "MRNA": "Moderna", "CRSP": "CRISPR Therapeutics"}
PAGE_URL = os.environ.get("PAGE_URL", "https://cybtinker.github.io/pharma-pipelines/")


def snapshot_days(lake, source):
    base = lake / "raw" / source
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", p.name))


def load(lake, source, day, ticker):
    f = lake / "raw" / source / day / f"{ticker}.json"
    if not f.exists():
        return None
    data = json.loads(f.read_text()).get("data")
    return data if isinstance(data, list) else None


def trial_key(s):
    p = s.get("protocolSection", {})
    ident = p.get("identificationModule", {})
    status = p.get("statusModule", {})
    design = p.get("designModule", {})
    return ident.get("nctId"), {
        "title": ident.get("acronym") or ident.get("briefTitle", ""),
        "status": status.get("overallStatus"),
        "primary_completion": (status.get("primaryCompletionDateStruct") or {}).get("date"),
        "has_results": s.get("hasResults", False),
        "phases": ", ".join(design.get("phases", []) or []),
    }


def trial_changes(old, new):
    o = dict(trial_key(s) for s in old or [])
    n = dict(trial_key(s) for s in new or [])
    out = []
    for nct, t in n.items():
        link = f"https://clinicaltrials.gov/study/{nct}"
        if nct not in o:
            if "PHASE3" in t["phases"]:
                out.append(f"New Phase 3 trial: [{t['title']}]({link}) ({t['status']})")
            continue
        prev = o[nct]
        if t["has_results"] and not prev["has_results"]:
            out.append(f"Results posted: [{t['title']}]({link})")
        if t["status"] != prev["status"]:
            out.append(f"Status {prev['status']} to {t['status']}: [{t['title']}]({link})")
        if t["primary_completion"] != prev["primary_completion"] and "PHASE3" in t["phases"]:
            out.append(f"Primary completion moved {prev['primary_completion']} to {t['primary_completion']}: [{t['title']}]({link})")
    return out


def filing_changes(old, new):
    seen = {f["url"] for f in old or []}
    return [f"{f['form']} filed {f['date']}: [{f.get('description') or f['form']}]({f['url']})"
            for f in new or [] if f["url"] not in seen]


def upcoming(lake, today, days=14):
    drugs = json.loads((lake / "curated/drugs.json").read_text())
    end = today + dt.timedelta(days=days)
    out = []
    for d in drugs:
        for m in d.get("milestones", []):
            if re.match(r"\d{4}-\d{2}-\d{2}$", m["date"]):
                when = dt.date.fromisoformat(m["date"])
                if today <= when <= end:
                    out.append((when, f"{when:%b %d}: {d['drug']} ({d['ticker']}), {m['label']}"))
    return [t for _, t in sorted(out)]


def build(lake, today):
    sections = []
    for ticker, name in NAMES.items():
        lines = []
        for source, fn in [("clinicaltrials", trial_changes), ("sec", filing_changes)]:
            days = snapshot_days(lake, source)
            if len(days) >= 2:
                lines += fn(load(lake, source, days[-2], ticker), load(lake, source, days[-1], ticker))
        if lines:
            sections.append(f"## {name} ({ticker})\n" + "\n".join(f"- {l}" for l in lines[:25]))
    coming = upcoming(lake, today)
    if coming:
        sections.insert(0, "## Coming up in the next 14 days\n" + "\n".join(f"- {l}" for l in coming))
    if not sections:
        return None
    body = (f"Pipeline Watch daily digest, {today:%B %d, %Y}\n\n" + "\n\n".join(sections) +
            f"\n\nSee the full timeline and competitor view: {PAGE_URL}\n\n"
            "Research only, not investment advice.")
    return body


def send(subject, body, url, secret):
    data = urllib.parse.urlencode({"action": "send", "secret": secret, "subject": subject, "body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:  # Apps Script redirects once; urllib follows it
        result = json.loads(r.read().decode() or "{}")
    print("Mailer:", result)
    if not result.get("ok"):
        raise SystemExit("Mailer refused the send: " + str(result))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default=str(pathlib.Path(__file__).resolve().parents[1]))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    lake, today = pathlib.Path(args.lake), dt.date.today()
    body = build(lake, today)
    if not body:
        print("Nothing to report today.")
        return
    out = lake / "digests" / f"{today.isoformat()}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(body)
    print(body)
    url, secret = os.environ.get("MAILER_URL"), os.environ.get("MAILER_SECRET")
    if url and secret and not args.dry_run:
        send(f"Pipeline Watch: {today:%b %d} update", body, url, secret)
    else:
        print("(not emailed: MAILER_URL/MAILER_SECRET not set, or dry run)")


if __name__ == "__main__":
    main()
