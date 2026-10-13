"""Step 1 of the pipeline: turn raw supplier input into a clean, structured listing.

Category-agnostic: it only cleans text and parses "attribute: value" pairs. It
never decides what the title should say.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field

from .lexicon import CONTACT_PATTERNS, EMPTY_VALUES, INSTRUCTION_PATTERNS, NUMBER_WORDS

MAX_NAME_CHARS = 300
MAX_VALUE_CHARS = 150
MAX_SPECS = 25

_INSTRUCTION_RE = [re.compile(p, re.IGNORECASE) for p in INSTRUCTION_PATTERNS]
_CONTACT_RE = [(reason, re.compile(p, re.IGNORECASE)) for reason, p in CONTACT_PATTERNS]


@dataclass
class Spec:
    attribute: str
    value: str


@dataclass
class Removal:
    """Text taken out of the supplier input before the AI sees it."""
    text: str
    reason: str          # "instruction to the AI", "phone number", "price", ...
    where: str           # "product name" or "spec: <attribute>"


@dataclass
class Listing:
    raw_name: str
    name: str
    specs: list[Spec]
    dropped_specs: list[tuple[Spec, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    removed: list[Removal] = field(default_factory=list)

    @property
    def injection_detected(self) -> bool:
        return any(r.reason == "instruction to the AI" for r in self.removed)

    def evidence_strings(self) -> list[str]:
        """Every piece of text the supplier gave, used for grounding checks."""
        out = [self.name] if self.name else []
        for s in self.specs:
            if s.attribute:
                out.append(s.attribute)
            out.append(s.value)
        return out

    def to_prompt_block(self) -> str:
        lines = [f"Product name: {self.name or '(not given)'}", "Specifications:"]
        if self.specs:
            for s in self.specs:
                lines.append(f"- {s.attribute}: {s.value}" if s.attribute else f"- {s.value}")
        else:
            lines.append("- (none given)")
        return "\n".join(lines)

    def cache_key(self) -> str:
        payload = json.dumps([self.name.lower(), [(s.attribute.lower(), s.value.lower()) for s in self.specs]])
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    @property
    def is_empty(self) -> bool:
        return not self.name and not self.specs


# ---------------------------------------------------------------- cleaning

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)


def clean_text(text: str, limit: int | None = None) -> str:
    if text is None:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).translate(_ZERO_WIDTH)
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    # drop emoji / symbols / control characters but keep letters (any script), digits and normal punctuation
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] in "LNMPZ" or ch in "+-/&%°\"'.,:;()x×#")
    t = t.replace("×", " x ")
    t = re.sub(r"([!?*#~_=])\1+", r"\1", t)          # "!!!" -> "!"
    t = re.sub(r"[!?*#~_=]+", " ", t)                 # those characters never belong in a title input
    t = re.sub(r"\s+", " ", t).strip(" ,;:-|")
    if limit and len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0]
    return t


# Split "A: x; B: y", "A: x | B: y", newline-separated, or "A: x, B: y"
# (a comma only splits when it is followed by another "Key:"; "Usage: Gym, Office" stays whole).
_SPEC_SPLIT = re.compile(r"\s*(?:;|\n|\r|\||,(?=\s*[A-Za-z][\w ()/.-]{0,30}:))\s*")


def sanitize(text: str, where: str) -> tuple[str, list[Removal]]:
    """Layer 1 of the prompt-injection defence: remove instructions aimed at the AI, and
    contact details / prices, before the AI ever sees the text."""
    if not text:
        return "", []
    t = unicodedata.normalize("NFKC", str(text))
    removed: list[Removal] = []
    for pat in _INSTRUCTION_RE:
        while (m := pat.search(t)):
            start = m.start()
            # a sentence that STARTS with the instruction ("... for men. Please ignore ...") goes entirely
            boundary = max(t.rfind(ch, 0, start) for ch in ".!?;\n")
            if boundary >= 0 and len(t[boundary + 1:start].split()) <= 3:
                start = boundary + 1
            # cut to the end of the sentence, or past a quoted payload ("write the title as 'X. Y.'")
            rest = t[m.end():]
            q = re.search(r"(?:^|\s)(['\"])", rest)
            if q and q.start() < 40 and (close := rest.find(q.group(1), q.end())) != -1:
                stop = re.search(r"[.!?;\n]", rest[close + 1:])
                end = m.end() + close + 1 + (stop.end() if stop else len(rest) - close - 1)
            else:
                stop = re.search(r"[.!?;\n]", rest)
                end = m.end() + (stop.end() if stop else len(rest))
            removed.append(Removal(t[start:end].strip(" .;"), "instruction to the AI", where))
            t = t[:start] + " " + t[end:]
    for reason, pat in _CONTACT_RE:
        for m in pat.finditer(t):
            removed.append(Removal(m.group(0).strip(), reason, where))
        t = pat.sub(" ", t)
    if removed:     # tidy what the removal left behind: "( )", a dangling "(" or a trailing "for"
        t = re.sub(r"\(\s*\)", " ", t)
        t = re.sub(r"\(\s*$", " ", t)
        t = re.sub(r"(?:\s+(?:for|and|with|at|on|to|or|in|@|-))+\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip(" ,;:-|.")
    return t, removed


def _raw_pairs(specs) -> list[tuple[str, str]]:
    if specs is None:
        return []
    pairs: list[tuple[str, str]] = []
    if isinstance(specs, dict):
        pairs = [(str(k), str(v)) for k, v in specs.items()]
    elif isinstance(specs, (list, tuple)):
        for item in specs:
            if isinstance(item, dict):
                pairs.append((str(item.get("attribute", item.get("name", ""))), str(item.get("value", ""))))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((str(item[0]), str(item[1])))
            else:
                pairs.append(("", str(item)))
    else:
        for part in _SPEC_SPLIT.split(str(specs)):
            if not part.strip():
                continue
            for sep in (":", "="):
                if sep in part:
                    k, v = part.split(sep, 1)
                    pairs.append((k, v))
                    break
            else:
                pairs.append(("", part))
    return pairs


def parse_specs(specs) -> list[Spec]:
    return [Spec(clean_text(k, 60), clean_text(v, MAX_VALUE_CHARS)) for k, v in _raw_pairs(specs)]


def build_listing(product_name: str, specs) -> Listing:
    safe_name, removed = sanitize(product_name or "", "product name")
    name = clean_text(safe_name, MAX_NAME_CHARS)
    listing = Listing(raw_name=product_name or "", name=name, specs=[], removed=removed)
    if safe_name and len(clean_text(safe_name)) > MAX_NAME_CHARS:
        listing.notes.append(f"Product name truncated to {MAX_NAME_CHARS} characters.")

    cleaned_specs = []
    for k, v in _raw_pairs(specs):
        where = f"spec: {clean_text(k, 60) or 'unlabelled'}"
        if any(p.search(f"{k}: {v}") for p in _INSTRUCTION_RE):
            listing.removed.append(Removal(clean_text(f"{k}: {v}"), "instruction to the AI", where))
            continue
        safe_v, rem = sanitize(v, where)
        listing.removed.extend(rem)
        cleaned_specs.append(Spec(clean_text(k, 60), clean_text(safe_v, MAX_VALUE_CHARS)))

    seen: dict[tuple[str, str], bool] = {}
    for spec in cleaned_specs:
        key = (spec.attribute.lower(), spec.value.lower())
        if spec.value.lower().strip(" .") in EMPTY_VALUES:
            listing.dropped_specs.append((spec, "empty or placeholder value"))
        elif key in seen:
            listing.dropped_specs.append((spec, "exact duplicate"))
        elif len(listing.specs) >= MAX_SPECS:
            listing.dropped_specs.append((spec, f"more than {MAX_SPECS} specifications"))
        else:
            seen[key] = True
            listing.specs.append(spec)

    attrs = [s.attribute.lower() for s in listing.specs if s.attribute]
    repeated = sorted({a for a in attrs if attrs.count(a) > 1})
    if repeated:
        listing.notes.append("Same attribute given more than once with different values: " + ", ".join(repeated))
    return listing


# ---------------------------------------------------------------- tokens

_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[^\W\d_]+", re.UNICODE)


def _prepare(text: str) -> str:
    t = text.lower().replace("’", "'").replace("`", "'")
    t = re.sub(r"(\d)\s*(?:\"|''|″)", r"\1 inch ", t)   # 2" -> 2 inch
    t = t.replace("°", " degree ")
    t = re.sub(r"(\d),(?=\d{3}\b)", r"\1", t)               # 1,000 -> 1000
    t = re.sub(r"'s\b", "", t)                              # men's -> men
    t = t.replace("&", " and ")
    return t


def normalize_number(tok: str) -> str:
    if "." in tok:
        tok = tok.rstrip("0").rstrip(".")
    return tok.lstrip("0") or "0"


def tokenize(text: str) -> list[str]:
    """Lower-case word and number tokens; digits and letters are split apart ("9W" -> "9", "w")."""
    out = []
    for tok in _TOKEN_RE.findall(_prepare(text or "")):
        if tok[0].isdigit():
            out.append(normalize_number(tok))
        else:
            out.append(NUMBER_WORDS.get(tok, tok))
    return out


def stem_candidates(word: str, plurals_only: bool = False) -> set[str]:
    """Light, language-level inflection handling: plurals, possessives, -ed, -ing.
    plurals_only is used for redundancy checks, where "Wire ... Wiring" is not a repeat."""
    cands = {word}
    if word[0].isdigit() or len(word) <= 3:
        return cands
    if word.endswith("ies") and len(word) > 4:
        cands.add(word[:-3] + "y")
    if word.endswith("es") and len(word) > 4:
        cands.add(word[:-2])
    if word.endswith("s") and not word.endswith("ss"):
        cands.add(word[:-1])
    if plurals_only:
        return cands
    if word.endswith("ing") and len(word) > 5:
        cands.add(word[:-3])
        cands.add(word[:-3] + "e")
    if word.endswith("ed") and len(word) > 4:
        cands.add(word[:-2])
        cands.add(word[:-1])
    return cands
