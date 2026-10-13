"""Re-run the (free, deterministic) verifier on the titles of a saved run. No API calls.
Useful after a verifier change, to see which titles it would now flag.

    python scripts/recheck_titles.py eval/results/v3_cascade.csv
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from title_engine.preprocess import build_listing  # noqa: E402
from title_engine.validator import check_title, format_title  # noqa: E402

with open(sys.argv[1], encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))
flagged = 0
for r in rows:
    title = format_title(r["title"])
    rep = check_title(title, build_listing(r["product_name"], r["specifications"]), r.get("product_type", ""))
    blocking = [i for i in rep.issues if i.severity in ("major", "critical") and i.code != "missing_product"]
    changed = " (reformatted)" if title != r["title"] else ""
    if blocking or changed:
        flagged += bool(blocking)
        print(f"{r['id']:<4} {title}{changed}")
        for i in blocking:
            print(f"      {i.severity}: {i.message}")
print(f"\n{flagged}/{len(rows)} titles would now be sent for repair")
