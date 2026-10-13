"""Run the generator over the test set and write a scorecard.

    python eval/run_eval.py                          # cascade mode, with the LLM judge
    python eval/run_eval.py --mode quality           # compare the 120B-only mode
    python eval/run_eval.py --ids s1,h01 --no-judge
    python eval/run_eval.py --rejudge eval/results/v1_cascade.csv   # re-score saved titles with today's judge
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from title_engine import config, generate  # noqa: E402
from title_engine.config import MODES  # noqa: E402
from title_engine import evaluation  # noqa: E402
from title_engine.evaluation import judge, load_cases, result_row, summarize  # noqa: E402
from title_engine.preprocess import build_listing  # noqa: E402
from title_engine.prompts import PROMPT_VERSION  # noqa: E402

JUDGE_FIELDS = ("judge_accuracy", "judge_fluency", "judge_search_fit", "judge_redundancy_free", "judge_publishable",
                "judge_invented", "judge_editor_version", "judge_comment", "judge_model", "judge_cost_usd",
                "judge_error")


def _write(rows: list[dict], summary: dict, out: Path, stem: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(out / f"{stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (out / f"{stem}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n=== summary ===")
    for k, v in summary.items():
        print(f"{k:<55} {v}")
    print(f"\nwrote {out / (stem + '.csv')}")


def _coerce(row: dict) -> dict:
    """CSV round-trip turns everything into strings; restore the types summarize() needs."""
    for k in ("grounded", "redundancy_free", "english_only", "review_routed", "has_implied_word"):
        row[k] = str(row.get(k)) == "True"
    row["review_expected"] = True if str(row.get("review_expected")) == "True" else None
    for k in ("attempts", "chars", "key_terms_found", "key_terms_total", "prompt_tokens", "completion_tokens"):
        row[k] = int(float(row.get(k) or 0))
    for k in ("cost_usd", "llm_latency_s"):
        row[k] = float(row[k]) if row.get(k) not in ("", None) else ""
    return row


def rejudge(path: Path, ids: set[str]) -> None:
    with open(path, encoding="utf-8-sig") as f:
        rows = [_coerce(r) for r in csv.DictReader(f)]
    if ids:
        rows = [r for r in rows if r["id"] in ids]
    for i, r in enumerate(rows, 1):
        for k in JUDGE_FIELDS:
            r.pop(k, None)
        r.update(judge(build_listing(r["product_name"], r["specifications"]), r["title"]))
        print(f"[{i:>2}/{len(rows)}] {r['id']:<4} pub={r.get('judge_publishable')!s:<5} "
              f"acc={r.get('judge_accuracy')} flu={r.get('judge_fluency')} fit={r.get('judge_search_fit')}  "
              f"{r['title']}  ->  {r.get('judge_editor_version', '')}")
        if r.get("judge_error"):
            print("        ERROR", r["judge_error"])
    summary = summarize(rows)
    summary["prompt_version"] = rows[0].get("prompt_version", "") if rows else ""
    summary["mode"] = rows[0].get("mode", "") if rows else ""
    summary["judge_model"] = evaluation.JUDGE_MODEL[1]
    summary["run_at"] = datetime.now().isoformat(timespec="seconds")
    _write(rows, summary, path.parent, path.stem + ("_rejudged" if not ids else "_rejudged_subset"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="cascade", choices=list(MODES))
    ap.add_argument("--ids", default="", help="comma-separated case ids to run (default: all)")
    ap.add_argument("--no-judge", action="store_true", help="skip the LLM judge (saves tokens)")
    ap.add_argument("--rejudge", default="", help="re-score the titles in a saved results CSV")
    ap.add_argument("--judge-model", default="", help="override the judge, e.g. groq:qwen/qwen3.8-27b")
    ap.add_argument("--tag", default="", help="suffix for the output files, e.g. run1 / run2 for repeat runs")
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = ap.parse_args()
    ids = set(args.ids.split(",")) if args.ids else set()

    if args.judge_model:
        prov, model = args.judge_model.split(":", 1)
        evaluation.JUDGE_MODEL = config.JUDGE_MODEL = (prov, model)

    if args.rejudge:
        rejudge(Path(args.rejudge), ids)
        return

    cases = [c for c in load_cases() if not ids or c["id"] in ids]
    rows = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        res = generate(case["product_name"], case.get("specifications"), mode=args.mode, use_cache=False)
        row = result_row(case, res, with_judge=not args.no_judge)
        rows.append(row)
        flag = "OK " if row["grounded"] and row["redundancy_free"] else "!! "
        print(f"[{i:>2}/{len(cases)}] {flag}{case['id']:<4} {row['status']:<24} {row['title']}")
        for reason in res.reasons[:3]:
            print(f"        - {reason}")
        if res.error:
            print(f"        ERROR: {res.error}")
        if row.get("judge_editor_version") and not row.get("judge_publishable"):
            print(f"        judge: {row.get('judge_comment', '')}  ->  {row['judge_editor_version']}")

    summary = summarize(rows)
    summary["prompt_version"] = PROMPT_VERSION
    summary["mode"] = args.mode
    summary["judge_model"] = evaluation.JUDGE_MODEL[1] if not args.no_judge else ""
    summary["run_at"] = datetime.now().isoformat(timespec="seconds")
    summary["wall_time_s"] = round(time.time() - t0, 1)
    _write(rows, summary, Path(args.out), f"{PROMPT_VERSION}_{args.mode}" + ("_subset" if ids else "")
           + (f"_{args.tag}" if args.tag else ""))


if __name__ == "__main__":
    main()
