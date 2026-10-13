"""Show every attempt, its issues and the word-level grounding for test cases.

    python scripts/debug_case.py ex3 h10
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from title_engine import generate  # noqa: E402
from title_engine.evaluation import load_cases  # noqa: E402

mode = "cascade"
ids = [a for a in sys.argv[1:] if not a.startswith("--")]
for a in sys.argv[1:]:
    if a.startswith("--mode="):
        mode = a.split("=", 1)[1]
for case in load_cases():
    if case["id"] not in ids:
        continue
    r = generate(case["product_name"], case.get("specifications"), mode=mode, use_cache=False)
    print(f"\n=== {case['id']}: {case['product_name']}")
    for i, att in enumerate(r.attempts, 1):
        print(f"  attempt {i} [{att.model}] {att.title!r}  product_type={att.data.get('product_type')!r}")
        if att.error:
            print("     ERROR", att.error)
        for iss in (att.report.issues if att.report else []):
            print(f"     {iss.severity:<8} {iss.code:<16} {iss.message}")
    print(f"  FINAL [{r.status}] {r.title!r}")
    print("  words:", " ".join(f"{w.word}<{w.kind}>" for w in (r.report.words if r.report else [])))
    print("  keywords:", r.data.get("search_keywords"))
    print("  alternates:", [t for t, _ in r.alternates])
