"""Print aggregate parse-quality stats for the dataset."""
import json
from collections import Counter
from pathlib import Path

SESSIONS = Path(__file__).resolve().parent.parent / "data" / "sessions"

layouts = Counter()
years = Counter()
total = with_results = with_laps = with_sectors = with_vmax = 0
riders_total = laps_total = mismatch = 0

for f in SESSIONS.glob("*.json"):
    d = json.loads(f.read_text())
    total += 1
    layouts[d["layout"]] += 1
    years[d["year"]] += 1
    if d["results"]:
        with_results += 1
        riders_total += len(d["results"])
    if d["laps"]:
        with_laps += 1
        laps_total += sum(len(lr["laps"]) for lr in d["laps"])
    if any(l.get("sectors") for lr in d["laps"] for l in lr["laps"]):
        with_sectors += 1
    if d["top_speeds"] or any(r.get("top_speed") for r in d["results"]):
        with_vmax += 1
    for lr in d["laps"]:
        for l in lr["laps"]:
            if l.get("time_s") and l.get("sectors") and all(l["sectors"]):
                if abs(sum(l["sectors"]) - l["time_s"]) > 0.05:
                    mismatch += 1

print(f"sessions: {total}")
print(f"  with classification: {with_results} ({with_results/total:.0%})")
print(f"  with lap times:      {with_laps} ({with_laps/total:.0%})")
print(f"  with sector times:   {with_sectors} ({with_sectors/total:.0%})")
print(f"  with top speeds:     {with_vmax} ({with_vmax/total:.0%})")
print(f"rider result rows: {riders_total}")
print(f"individual laps:   {laps_total}  (sector/time mismatches: {mismatch})")
print("by layout:", dict(layouts.most_common()))
print("by year:  ", dict(sorted(years.items())))
