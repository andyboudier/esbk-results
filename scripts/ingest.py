"""Ingest pipeline: inventory -> download PDFs -> parse -> data/sessions/*.json.

Incremental: data/manifest.json records every processed URL; already-processed
URLs are skipped, so periodic runs only handle newly published PDFs.

Usage:
  python scripts/ingest.py                # process all new result PDFs
  python scripts/ingest.py --year 2026    # restrict to one year
  python scripts/ingest.py --limit 50     # cap number of PDFs this run
  python scripts/ingest.py --force        # reprocess everything
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PDF_CACHE = DATA / "pdfs"
SESSIONS = DATA / "sessions"
MANIFEST = DATA / "manifest.json"
REPORT = DATA / "ingest_report.json"
INDEX = DATA / "index.json"

sys.path.insert(0, str(Path(__file__).parent))
from parse_pdf import parse_pdf  # noqa: E402


def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def normalize_session(name):
    """Group raw session labels into a coarse kind."""
    s = name.lower()
    if "carrera" in s or "race" in s:
        return "race"
    if "crono" in s or "superpole" in s or re.search(r"\bq[12]\b", s):
        return "qualifying"
    if "warm" in s:
        return "warmup"
    return "practice"


def download(rec, timeout=90):
    url = rec["url"]
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf"
    path = PDF_CACHE / name
    if not path.exists() or path.stat().st_size == 0:
        r = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (esbk-results ingest)"})
        r.raise_for_status()
        if not r.content.startswith(b"%PDF"):
            raise ValueError("not a PDF")
        path.write_bytes(r.content)
    return path


def session_id(rec):
    return "-".join(filter(None, [
        rec["event_slug"], slugify(rec["class"] or "all"),
        slugify(rec["session"])]))


def process(rec):
    try:
        path = download(rec)
    except Exception as e:  # noqa: BLE001
        return rec, None, f"download: {e}"
    try:
        parsed = parse_pdf(str(path))
    except Exception as e:  # noqa: BLE001
        return rec, None, f"parse: {type(e).__name__}: {e}"
    return rec, parsed, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--retry-failed", action="store_true",
                    help="also reprocess URLs whose last status was not ok")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    inventory = load_json(DATA / "inventory.json", [])
    manifest = {} if args.force else load_json(MANIFEST, {})
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    SESSIONS.mkdir(parents=True, exist_ok=True)

    todo = [r for r in inventory if r["kind"] == "result"]
    if args.year:
        todo = [r for r in todo if r["year"] == args.year]
    if args.retry_failed:
        todo = [r for r in todo if manifest.get(r["url"], {}).get("status") != "ok"]
    else:
        todo = [r for r in todo if r["url"] not in manifest]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} PDFs to process")

    report = load_json(REPORT, {"failures": []})
    report["failures"] = [f for f in report["failures"]
                          if f["url"] not in {r["url"] for r in todo}]
    done = ok = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rec, parsed, err in pool.map(process, todo):
            done += 1
            entry = {"status": "error", "error": err}
            if err is None:
                sid = session_id(rec)
                doc = {
                    "id": sid,
                    "year": rec["year"],
                    "round": rec["round"],
                    "circuit": rec["circuit"],
                    "event_slug": rec["event_slug"],
                    "class": rec["class"],
                    "session": rec["session"],
                    "session_kind": normalize_session(rec["session"]),
                    "date": parsed["date"] or rec.get("date_start"),
                    "date_start": rec.get("date_start"),
                    "date_end": rec.get("date_end"),
                    "pdf_url": rec["url"],
                    "layout": parsed["layout"],
                    "warnings": parsed["warnings"],
                    "results": parsed["results"],
                    "laps": parsed["laps"],
                    "top_speeds": parsed["top_speeds"],
                }
                (SESSIONS / f"{sid}.json").write_text(
                    json.dumps(doc, ensure_ascii=False))
                nres = len(parsed["results"])
                entry = {"status": "ok", "session_id": sid, "results": nres,
                         "lap_riders": len(parsed["laps"]),
                         "warnings": parsed["warnings"]}
                if nres == 0:
                    entry["status"] = "empty"
                    report["failures"].append(
                        {"url": rec["url"], "error": "no results",
                         "warnings": parsed["warnings"],
                         "layout": parsed["layout"], "meta": {
                             "year": rec["year"], "class": rec["class"],
                             "session": rec["session"], "circuit": rec["circuit"]}})
                else:
                    ok += 1
            else:
                report["failures"].append(
                    {"url": rec["url"], "error": err, "meta": {
                        "year": rec["year"], "class": rec["class"],
                        "session": rec["session"], "circuit": rec["circuit"]}})
            manifest[rec["url"]] = entry
            if done % 25 == 0:
                print(f"  {done}/{len(todo)} ({ok} ok)")
                MANIFEST.write_text(json.dumps(manifest, indent=0))

    MANIFEST.write_text(json.dumps(manifest, indent=0))
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    build_index()
    print(f"done: {done} processed, {ok} ok, "
          f"{len(report['failures'])} total failures recorded")


def build_index():
    """Light search index: one row per session incl. rider summary."""
    idx = []
    for f in sorted(SESSIONS.glob("*.json")):
        doc = json.loads(f.read_text())
        idx.append({
            "id": doc["id"], "year": doc["year"], "round": doc["round"],
            "circuit": doc["circuit"], "class": doc["class"],
            "session": doc["session"], "session_kind": doc["session_kind"],
            "date": doc["date"], "layout": doc["layout"],
            "riders": [
                {"no": r["no"], "rider": r["rider"], "pos": r["pos"],
                 "best_lap_s": r["best_lap_s"], "best_lap": r["best_lap"],
                 "top_speed": r["top_speed"], "status": r["status"]}
                for r in doc["results"]],
        })
    INDEX.write_text(json.dumps(idx, ensure_ascii=False))
    print(f"index: {len(idx)} sessions -> {INDEX}")


if __name__ == "__main__":
    main()
