"""Offline evaluation: per-case checks, an independent LLM judge, and summary metrics."""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from .config import JUDGE_MODEL, ROOT
from .lexicon import CONNECTORS
from .llm import LLMError, chat_json
from .pipeline import FALLBACK, NEEDS_REVIEW, TitleResult
from .preprocess import Listing, stem_candidates, tokenize
from .prompts import JUDGE_PROMPT, JUDGE_SCHEMA, JUDGE_USER_TEMPLATE

TEST_CASES_PATH = ROOT / "data" / "test_cases.json"
JUDGE_REASONING = "medium"   # at "low" the judge rubber-stamped visibly flawed titles


def load_cases(path: Path = TEST_CASES_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _title_stems(title: str) -> set[str]:
    out = set()
    for t in tokenize(title):
        out |= stem_candidates(t)
    return out


def case_checks(case: dict, result: TitleResult) -> dict:
    stems = _title_stems(result.title)
    groups = case.get("must_include", [])
    missing = [g for g in groups if not any(stem_candidates(tokenize(w)[0]) & stems for w in g if tokenize(w))]
    forbidden = [w for w in case.get("must_not_include", []) if tokenize(w) and tokenize(w)[0] in stems]
    rep = result.report
    issue_codes = {i.code for i in rep.issues} if rep else set()
    expect_review = case.get("expect_review")
    return {
        "key_terms_found": len(groups) - len(missing),
        "key_terms_total": len(groups),
        "key_terms_missing": "; ".join("/".join(g) for g in missing),
        "forbidden_terms_found": ", ".join(forbidden),
        "grounded": not (issue_codes & {"invented_number", "promo_word", "ungrounded_word", "bad_citation"}),
        "has_implied_word": "implied_word" in issue_codes,
        "redundancy_free": "redundant" not in issue_codes,
        "english_only": not re.search(r"[^\x00-\x7F°]", result.title),
        "review_expected": expect_review,
        "review_routed": result.status in (NEEDS_REVIEW, FALLBACK),
        "skeleton": skeleton(result.title),
    }


def skeleton(title: str) -> str:
    """Structure of a title with content words removed: 'W for W, N W'. Used to measure variety."""
    parts = []
    for w in re.findall(r"[\w.'-]+|,", title):
        lw = w.lower()
        if w == ",":
            tok = ","
        elif lw in CONNECTORS:
            tok = lw
        elif re.match(r"^\d", w):
            tok = "N"
        else:
            tok = "W"
        if not (tok == "W" and parts and parts[-1] == "W"):
            parts.append(tok)
    return " ".join(parts).replace(" ,", ",")


def judge(listing: Listing, title: str) -> dict:
    """Independent check by a model from a different family than the generator.
    Eval-only; it never changes the title."""
    if not title:
        return {}
    messages = [{"role": "system", "content": JUDGE_PROMPT},
                {"role": "user", "content": JUDGE_USER_TEMPLATE.format(
                    listing_block=listing.to_prompt_block(), title=title)}]
    try:
        r = chat_json(*JUDGE_MODEL, messages, JUDGE_SCHEMA, "title_judgement", temperature=0.0, max_tokens=3000,
                      extra={"reasoning_effort": JUDGE_REASONING})
    except LLMError as e:
        return {"judge_error": str(e)}
    d = r.data
    return {
        "judge_accuracy": d.get("accuracy"),
        "judge_fluency": d.get("fluency"),
        "judge_search_fit": d.get("search_fit"),
        "judge_redundancy_free": d.get("redundancy_free"),
        "judge_publishable": d.get("publishable_as_is"),
        "judge_invented": "; ".join(d.get("invented_details") or []),
        "judge_editor_version": d.get("editor_version", ""),
        "judge_comment": d.get("comment", ""),
        "judge_model": JUDGE_MODEL[1],
        "judge_cost_usd": r.cost_usd or 0.0,
    }


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    ok = [r for r in rows if not r.get("error")]
    costs = [r["cost_usd"] for r in rows if isinstance(r.get("cost_usd"), (int, float))]
    lat = sorted(r["llm_latency_s"] for r in rows if r.get("llm_latency_s"))
    review_cases = [r for r in rows if r.get("review_expected") is True]
    judged = [r for r in rows if isinstance(r.get("judge_accuracy"), (int, float))
              and str(r.get("judge_accuracy")) not in ("", "nan")]
    chars = [r["chars"] for r in rows if r.get("chars")]
    m = {
        "titles": n,
        "errors": n - len(ok),
        "grounded_pct (verifier: every word traced to input)": _pct(sum(r["grounded"] for r in rows), n),
        "redundancy_free_pct (verifier)": _pct(sum(r["redundancy_free"] for r in rows), n),
        "key_term_recall_pct": _pct(sum(r["key_terms_found"] for r in rows), sum(r["key_terms_total"] for r in rows)),
        "cases_with_forbidden_terms": sum(bool(r["forbidden_terms_found"]) for r in rows),
        "english_only_pct": _pct(sum(r["english_only"] for r in rows), n),
        "auto_approved_pct": _pct(sum(r["status"] == "Auto-approved" for r in rows), n),
        "needs_review_pct": _pct(sum(r["status"] == "Needs review" for r in rows), n),
        "fallback_pct": _pct(sum(r["status"].startswith("Fallback") for r in rows), n),
        "risky_cases_routed_to_review_pct": _pct(sum(r["review_routed"] for r in review_cases), len(review_cases)),
        "escalated_to_second_attempt_pct": _pct(sum(r["attempts"] > 1 for r in rows), n),
        "avg_chars": round(statistics.mean(chars), 1) if chars else None,
        "within_25_75_chars_pct": _pct(sum(25 <= c <= 75 for c in chars), n),
        "within_100_chars_pct": _pct(sum(c <= 100 for c in chars), n),
        "distinct_structures_pct (variety)": _pct(len({r["skeleton"] for r in rows}), n),
        "avg_llm_latency_s": round(statistics.mean(lat), 2) if lat else None,
        "p95_llm_latency_s": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2) if lat else None,
        "avg_cost_per_title_usd": round(statistics.mean(costs), 6) if costs else None,
        "projected_cost_per_1M_titles_usd": round(statistics.mean(costs) * 1e6) if costs else None,
    }
    if judged:
        m.update({
            "judge_publishable_as_is_pct": _pct(sum(r.get("judge_publishable") is True for r in judged), len(judged)),
            "judge_avg_accuracy_1to5": round(statistics.mean(r["judge_accuracy"] for r in judged), 2),
            "judge_avg_fluency_1to5": round(statistics.mean(r["judge_fluency"] for r in judged), 2),
            "judge_avg_search_fit_1to5": round(statistics.mean(r["judge_search_fit"] for r in judged), 2),
            "judge_invented_detail_pct": _pct(sum(bool(r["judge_invented"]) for r in judged), len(judged)),
            "judge_redundancy_free_pct": _pct(sum(bool(r["judge_redundancy_free"]) for r in judged), len(judged)),
        })
    return m


def result_row(case: dict, result: TitleResult, with_judge: bool = False) -> dict:
    row = {"id": case.get("id", ""), "group": case.get("group", ""), "category": case.get("category", ""),
           "challenge": case.get("challenge", ""), "reference_title": case.get("reference_title", "")}
    row.update(result.to_row())
    row.update(case_checks(case, result))
    if with_judge:
        row.update(judge(result.listing, result.title))
    return row
