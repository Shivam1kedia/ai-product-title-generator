"""Like-for-like comparison of saved evaluation runs.

"pub30" is measured on the original 30 listings only, so versions tested on 30 and on 38 listings compare fairly.

    python scripts/compare_runs.py
"""
from pathlib import Path

import pandas as pd

RES = Path(__file__).resolve().parent.parent / "eval" / "results"
RUNS = [("v1", "v1_cascade_rejudged"), ("v2", "v2_cascade"), ("v3", "v3_cascade"), ("v4", "v4_cascade"),
        ("v4.2", "v4.2_cascade"), ("v4.3", "v4.3_cascade_run1"), ("v4.4", "v4.4_cascade_run1"),
        ("v4.5 Groq", "v4.5_cascade_run1"), ("v4.5 Groq r2", "v4.5_cascade_run2"), ("v4.5 Gemini", "v4.5_gemini_run1")]

original = set(pd.read_csv(RES / "v3_cascade.csv").id)
rows = []
for version, stem in RUNS:
    path = RES / f"{stem}.csv"
    if not path.exists():
        continue
    d = pd.read_csv(path).fillna("")
    pub = d.judge_publishable.astype(str) == "True"
    orig = d.id.isin(original)
    auto = d.status == "Auto-approved"
    lat = d.llm_latency_s.astype(float)
    rows.append({
        "version": version,
        "listings": len(d),
        "publishable % (30 original)": round(100 * pub[orig].mean(), 1),
        "publishable % (all)": round(100 * pub.mean(), 1),
        "publishable % (8 new)": round(100 * pub[~orig].mean(), 1) if (~orig).any() else None,
        "invented details in auto-approved": int(((d.judge_invented != "") & auto).sum()),
        "human review %": round(100 * (~auto).mean(), 1),
        "repaired %": round(100 * (d.attempts > 1).mean(), 1),
        "cost per title $": round(d.cost_usd.astype(float).mean(), 6),
        "avg latency s": round(lat.mean(), 2),
        "p95 latency s": round(lat.quantile(0.95), 2),
    })
print(pd.DataFrame(rows).set_index("version").T.to_string())

stab, base = RES / "v4.4_cascade_subset_stability.csv", RES / "v4.4_cascade_run1.csv"
if stab.exists() and base.exists():
    a = pd.read_csv(stab).set_index("id").title
    b = pd.read_csv(base).set_index("id").title
    same = sum(a[i] == b[i] for i in a.index)
    print(f"\nstability (temperature 0, different API key): {same}/{len(a)} titles identical")
    for i in a.index:
        if a[i] != b[i]:
            print(f"  {i}: '{b[i]}'  vs  '{a[i]}'")
