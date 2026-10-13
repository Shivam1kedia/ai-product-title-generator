"""The pipeline: normalise -> generate (LLM) -> verify (code) -> repair/escalate -> route."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import MODES, load_env
from .lexicon import CONNECTORS, PROMO_WORDS
from .llm import LLMError, LLMResult, chat_json
from .preprocess import Listing, build_listing, stem_candidates, tokenize
from .prompts import PROMPT_VERSION, REPAIR_TEMPLATE, SYSTEM_PROMPT, TITLE_SCHEMA, USER_TEMPLATE
from .validator import (
    CRITICAL, MAJOR, REVIEW, Evidence, Report, _class_of, check_citations, check_title, format_title,
)

load_env()

AUTO_APPROVED, NEEDS_REVIEW, FALLBACK = "auto_approved", "needs_review", "fallback"
STATUS_LABEL = {AUTO_APPROVED: "Auto-approved", NEEDS_REVIEW: "Needs review", FALLBACK: "Fallback - needs review"}


@dataclass
class Attempt:
    provider: str
    model: str
    title: str = ""
    data: dict = field(default_factory=dict)
    report: Report | None = None
    llm: LLMResult | None = None
    error: str | None = None
    note: str = ""


@dataclass
class TitleResult:
    listing: Listing
    title: str
    status: str
    reasons: list[str]
    report: Report | None
    data: dict
    alternates: list[tuple[str, Report]]
    attempts: list[Attempt]
    mode: str
    prompt_version: str = PROMPT_VERSION
    fallback_used: bool = False
    error: str | None = None
    wall_time_s: float = 0.0
    cached: bool = False

    @property
    def cost_usd(self) -> float | None:
        costs = [a.llm.cost_usd for a in self.attempts if a.llm]
        return sum(c for c in costs if c is not None) if costs and all(c is not None for c in costs) else None

    @property
    def tokens(self) -> tuple[int, int]:
        return (sum(a.llm.prompt_tokens for a in self.attempts if a.llm),
                sum(a.llm.completion_tokens for a in self.attempts if a.llm))

    @property
    def llm_latency_s(self) -> float:
        return sum(a.llm.latency_s for a in self.attempts if a.llm)

    @property
    def final_model(self) -> str:
        ok = [a for a in self.attempts if a.llm and a.title == self.title]
        return ok[-1].model if ok else ("fallback" if self.fallback_used else "")

    @property
    def escalated(self) -> bool:
        return len([a for a in self.attempts if a.llm or a.error]) > 1

    def to_row(self) -> dict:
        pt, ct = self.tokens
        return {
            "product_name": self.listing.raw_name,
            "specifications": "; ".join(f"{s.attribute}: {s.value}" if s.attribute else s.value for s in self.listing.specs),
            "title": self.title,
            "product_type": self.data.get("product_type", ""),
            "status": STATUS_LABEL[self.status],
            "review_reasons": " | ".join(self.reasons),
            "alternates": " | ".join(t for t, r in self.alternates if not r.blocking),
            "search_keywords": ", ".join(self.data.get("search_keywords", [])),
            "confidence": self.data.get("confidence", ""),
            "final_model": self.final_model,
            "attempts": len(self.attempts),
            "chars": len(self.title),
            "spec_coverage": round(self.report.spec_coverage, 2) if self.report else "",
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "cost_usd": round(self.cost_usd, 7) if self.cost_usd is not None else "",
            "llm_latency_s": round(self.llm_latency_s, 2),
            "prompt_version": self.prompt_version,
            "mode": self.mode,
            "error": self.error or "",
        }


_cache: dict[tuple, TitleResult] = {}


def fallback_title(listing: Listing) -> str:
    """Last resort when the LLM output cannot be trusted: the supplier's own name with
    exact repeats, synonym repeats and marketing words removed. Never adds anything."""
    kept, seen = [], []
    for w in listing.name.split():
        toks = tokenize(w)
        if not toks:
            continue
        if all(t in PROMO_WORDS for t in toks):
            continue
        keys = set()
        for t in toks:
            if t[0].isdigit() or t in CONNECTORS:
                continue
            keys |= stem_candidates(t)
            cls = _class_of(t)
            if cls and cls[1] != "unit":
                keys.add("class:" + cls[0])
        if keys and any(keys & s for s in seen):
            continue
        seen.append(keys)
        kept.append(w.strip(",;:"))
    title = format_title(" ".join(kept))
    if not title and listing.specs:
        title = format_title(" ".join(s.value for s in listing.specs[:3]))
    return title[:100].rsplit(" ", 1)[0] if len(title) > 100 else title


def _problems_text(report: Report) -> str:
    return "\n".join(f"- {i.message}" for i in report.blocking)


def _evaluate(data: dict, listing: Listing, ev: Evidence) -> tuple[str, Report]:
    title = format_title(data.get("title", ""))
    explained = " ".join([f"{d.get('detail', '')} {d.get('reason', '')}" for d in data.get("omitted_details") or []]
                         + list(data.get("conflicts") or []))
    rep = check_title(title, listing, data.get("product_type", ""), data.get("spelling_fixes"),
                      data.get("implied_additions"), ev, data.get("modifier_roles"), explained)
    rep.issues.extend(check_citations(data.get("attributes_used"), ev))
    return title, rep


def generate(product_name: str, specs=None, mode: str = "cascade", use_cache: bool = True,
             temperature: float = 0.0) -> TitleResult:
    # temperature 0: the same listing gets the same title (at 0.2 one eval run swung by several points);
    # variety across listings comes from the content, and alternates give wording choices
    t_start = time.time()
    listing = build_listing(product_name, specs)
    if listing.is_empty:
        return TitleResult(listing, "", NEEDS_REVIEW, ["No product name or specifications were given."],
                           None, {}, [], [], mode, error="empty input")

    key = (PROMPT_VERSION, mode, temperature, listing.cache_key())
    if use_cache and key in _cache:
        cached = _cache[key]
        cached.cached = True
        return cached

    ev = Evidence(listing)
    chain = MODES[mode]["chain"]
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(listing_block=listing.to_prompt_block())}]
    attempts: list[Attempt] = []

    for provider, model in chain:
        att = Attempt(provider, model)
        attempts.append(att)
        try:
            att.llm = chat_json(provider, model, messages, TITLE_SCHEMA, "product_title", temperature=temperature)
        except LLMError as e:
            att.error = str(e)
            continue
        att.data = att.llm.data
        att.title, att.report = _evaluate(att.data, listing, ev)
        if not att.report.blocking:
            break
        # free fix: if one of the model's own alternates passes every check, use it instead of a repair call
        for alt in (att.data.get("alternates") or [])[:3]:
            a_title, a_rep = _evaluate({**att.data, "title": alt}, listing, ev)
            if a_title and not a_rep.blocking and not a_rep.has(REVIEW):
                att.note = f"Main title failed a check ({att.report.blocking[0].code}); used a passing alternate."
                att.title, att.report = a_title, a_rep
                att.data = {**att.data, "title": alt,
                            "alternates": [t for t in att.data.get("alternates", []) if t != alt]
                            + [att.llm.data.get("title", "")]}
                break
        if not att.report.blocking:
            break
        messages = messages + [
            {"role": "assistant", "content": att.llm.content},
            {"role": "user", "content": REPAIR_TEMPLATE.format(problems=_problems_text(att.report))},
        ]

    good = [a for a in attempts if a.report is not None]
    if not good:
        errors = "; ".join(a.error for a in attempts if a.error)
        title = fallback_title(listing)
        rep = check_title(title, listing, evidence=ev)
        return TitleResult(listing, title, FALLBACK, ["The AI service failed, so a safe rule-based title was used."],
                           rep, {}, [], attempts, mode, fallback_used=True, error=errors,
                           wall_time_s=time.time() - t_start)

    # best attempt: fewest critical, then major, then review issues; later attempts win ties
    best = min(reversed(good), key=lambda a: a.report.score)
    data, title, rep = best.data, best.title, best.report
    reasons: list[str] = []
    fallback_used = False
    status = AUTO_APPROVED

    if rep.has(CRITICAL):
        reasons += [i.message for i in rep.issues if i.severity == CRITICAL]
        title = fallback_title(listing)
        rep = check_title(title, listing, evidence=ev)
        fallback_used, status = True, FALLBACK
        reasons.insert(0, "The AI title still broke a hard rule after repair, so a safe rule-based title was used.")
    else:
        reasons += [i.message for i in rep.issues if i.severity in (REVIEW, MAJOR)]
        if data.get("conflicts"):
            reasons += ["Conflict: " + c for c in data["conflicts"]]
        if data.get("confidence") != "high":
            note = data.get("review_note") or ""
            reasons.append(f"Model confidence is {data.get('confidence', 'unknown')}." + (f" {note}" if note else ""))
        if listing.injection_detected:
            removed = "; ".join(r.text for r in listing.removed if r.reason == "instruction to the AI")
            reasons.insert(0, f"The supplier text contained an instruction to the AI, which was removed: '{removed}'.")
        if reasons:
            status = NEEDS_REVIEW

    alternates = []
    for alt in (data.get("alternates") or [])[:3]:
        a_title, a_rep = _evaluate({**data, "title": alt}, listing, ev)
        if a_title and a_title.lower() != title.lower():
            alternates.append((a_title, a_rep))

    result = TitleResult(listing, title, status, reasons, rep, data, alternates, attempts, mode,
                         fallback_used=fallback_used, wall_time_s=time.time() - t_start)
    if use_cache:
        _cache[key] = result
    return result
