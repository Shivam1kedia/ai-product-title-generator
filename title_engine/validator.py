"""Step 3 of the pipeline: deterministic checks on what the LLM wrote.

The LLM is good at language but can invent details. This module is plain code
that traces every word of a title back to the supplier's own input. It is the
reason the product can promise "no invented details" without per-category rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .lexicon import (
    ABBREV_PARTS, ABBREVIATIONS, ATTRIBUTE_ROLE_HINTS, COLOUR_WORDS, CONNECTORS, CONTACT_PATTERNS, KEEP_UPPER,
    MATERIAL_WORDS, PATTERN_WORDS, PROMO_WORDS, SUBJECTIVE_WORDS, SYNONYM_CLASSES, UNIT_CASE, WORD_TO_CLASS,
)
from .preprocess import Listing, stem_candidates, tokenize

_CONTACT_RE = [(reason, re.compile(p, re.IGNORECASE)) for reason, p in CONTACT_PATTERNS]

# General English modifier order, enforced only where it is unambiguous:
# colour/pattern before material ("Black Leather", "Printed Cotton"), material before the
# purpose word ("Cotton Bag Fabric", not "Bag Cotton Fabric"). The brand goes first.
ORDER_RULES = [("colour", "material"), ("pattern", "material"), ("material", "purpose")]
_LLM_ROLE = {"brand": "brand", "colour": "colour", "pattern_or_finish": "pattern", "material": "material",
             "purpose_or_use": "purpose", "size_or_spec": "spec", "shape": "shape", "style_or_type": "style",
             "audience": "audience", "other": "other"}
_AFTER_PRODUCT_OK = {"for", "with", "and", "in", "of", "&"}

# Title policy (a product decision, not a category rule). The brief's own reference
# titles are 28-43 characters, so the target is 25-75 with a hard limit of 100.
MIN_CHARS = 12
TARGET_MIN_CHARS = 25
TARGET_MAX_CHARS = 75
HARD_MAX_CHARS = 100
MAX_WORDS = 14
MAX_COMMAS = 2

# Severities, in increasing order of seriousness
INFO, REVIEW, MAJOR, CRITICAL = "info", "review", "major", "critical"
_RANK = {INFO: 0, REVIEW: 1, MAJOR: 2, CRITICAL: 3}

# How a title word is grounded in the input (used for the UI word chips)
GROUNDED_KINDS = {"exact", "stem", "synonym", "abbreviation", "compound", "spelling", "connector"}


@dataclass
class Issue:
    severity: str
    code: str
    message: str


@dataclass
class WordCheck:
    word: str                 # as shown in the title
    kind: str                 # exact | stem | synonym | abbreviation | compound | spelling | connector | ungrounded | number
    detail: str = ""

    @property
    def grounded(self) -> bool:
        return self.kind in GROUNDED_KINDS


@dataclass
class Report:
    title: str
    words: list[WordCheck] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    spec_coverage: float = 1.0
    omitted_specs: list[str] = field(default_factory=list)

    @property
    def worst(self) -> str:
        return max((i.severity for i in self.issues), key=_RANK.get, default=INFO)

    def has(self, severity: str) -> bool:
        return any(_RANK[i.severity] >= _RANK[severity] for i in self.issues)

    @property
    def blocking(self) -> list[Issue]:
        """Issues the LLM should be asked to fix."""
        return [i for i in self.issues if _RANK[i.severity] >= _RANK[MAJOR]]

    @property
    def score(self) -> tuple:
        """Lower is better. Used to pick the best attempt."""
        return (sum(i.severity == CRITICAL for i in self.issues),
                sum(i.severity == MAJOR for i in self.issues),
                sum(i.severity == REVIEW for i in self.issues))


# ---------------------------------------------------------------- evidence

class Evidence:
    """Everything the supplier wrote, indexed for fast grounding lookups."""

    def __init__(self, listing: Listing):
        self.tokens: set[str] = set()
        self.stems: set[str] = set()
        self.classes: dict[str, str] = {}      # class_id -> supplier word
        self.joined: set[str] = set()          # adjacent pairs joined: "day light" -> "daylight"
        self.alpha: set[str] = set()
        for text in listing.evidence_strings():
            toks = tokenize(text)
            for i, t in enumerate(toks):
                self.tokens.add(t)
                for c in stem_candidates(t):
                    self.stems.add(c)
                    if c in WORD_TO_CLASS:
                        self.classes.setdefault(WORD_TO_CLASS[c][0], t)
                if t.isalpha():
                    self.alpha.add(t)
                if i + 1 < len(toks):
                    self.joined.add(t + toks[i + 1])
            # hyphenated words also appear joined: "semi-automatic" -> "semiautomatic"
            for m in re.finditer(r"([A-Za-z]+)-([A-Za-z]+)", text):
                self.joined.add((m.group(1) + m.group(2)).lower())

    def has_word(self, word: str) -> bool:
        return bool(stem_candidates(word) & self.stems)


def _class_of(word: str):
    for c in stem_candidates(word):
        if c in WORD_TO_CLASS:
            return WORD_TO_CLASS[c]
    return None


def _ground_token(tok: str, prev: str | None, nxt: str | None, ev: Evidence,
                  declared_fixes: dict[str, str], declared_implied: set[str]) -> WordCheck:
    if tok[0].isdigit():
        return WordCheck(tok, "exact" if tok in ev.tokens else "number",
                         "" if tok in ev.tokens else "number not in supplier input")
    if tok in CONNECTORS:
        return WordCheck(tok, "connector")
    if tok in ev.tokens:
        return WordCheck(tok, "exact")
    if ev.has_word(tok):
        return WordCheck(tok, "stem")
    cls = _class_of(tok)
    if cls and cls[0] in ev.classes:
        return WordCheck(tok, "synonym", f"supplier wrote '{ev.classes[cls[0]]}'")
    if tok in ABBREVIATIONS and all(ev.has_word(p) for p in ABBREVIATIONS[tok]):
        return WordCheck(tok, "abbreviation", "short for " + " ".join(ABBREVIATIONS[tok]))
    for abbr in ABBREV_PARTS.get(tok, ()):
        if abbr in ev.tokens:
            return WordCheck(tok, "abbreviation", f"expanded from '{abbr.upper()}'")
    if tok in ev.joined:
        return WordCheck(tok, "compound", "joined from two supplier words")
    if (nxt and tok + nxt in ev.tokens) or (prev and prev + tok in ev.tokens):
        return WordCheck(tok, "compound", "split from one supplier word")
    if cls is None and len(tok) >= 4:
        # spelling correction: only for words that are not in the lexicon, so e.g. "women"
        # can never be "grounded" by a fuzzy match on "woven"
        best, ratio = None, 0.0
        for e in ev.alpha:
            if len(e) >= 3 and e[0] == tok[0]:
                r = SequenceMatcher(None, tok, e).ratio()
                if r > ratio:
                    best, ratio = e, r
        declared = declared_fixes.get(tok)
        if best and (ratio >= 0.86 or (ratio >= 0.72 and declared and declared[0] == tok[0])):
            return WordCheck(tok, "spelling", f"corrected from '{best}'")
    if tok in PROMO_WORDS:
        return WordCheck(tok, "ungrounded", "marketing/subjective word not in supplier input")
    if tok in declared_implied:
        return WordCheck(tok, "ungrounded", "inferred by the model (declared as implied)")
    return WordCheck(tok, "ungrounded", "not found in supplier input")


# ---------------------------------------------------------------- checks

def check_title(title: str, listing: Listing, product_type: str = "",
                spelling_fixes: list[dict] | None = None,
                implied_additions: list[dict] | None = None,
                evidence: Evidence | None = None,
                modifier_roles: list[dict] | None = None,
                explained: str = "") -> Report:
    """`explained` is the model's own omitted_details/conflicts text: a supplier word the model
    explicitly chose to leave out (with a reason) is not reported as silently dropped."""
    ev = evidence or Evidence(listing)
    rep = Report(title=title)
    explained = explained.lower()
    # words that only appeared in text removed for safety (an instruction to the AI, a sales pitch)
    quarantined = {t for r in listing.removed for t in tokenize(r.text)} - ev.tokens
    declared_fixes = {}
    for f in spelling_fixes or []:
        to_toks, from_toks = tokenize(f.get("to", "")), tokenize(f.get("from", ""))
        for t, fr in zip(to_toks, from_toks):
            declared_fixes[t] = fr
    declared_implied = {t for a in (implied_additions or []) for t in tokenize(a.get("word", ""))}

    if not title or not title.strip():
        rep.issues.append(Issue(CRITICAL, "empty", "Title is empty."))
        return rep

    # --- grounding, word by word -------------------------------------------------
    display_words = title.split()
    flat: list[tuple[int, str]] = []
    for wi, w in enumerate(display_words):
        for t in tokenize(w):
            flat.append((wi, t))
    checks_by_word: dict[int, list[WordCheck]] = {i: [] for i in range(len(display_words))}
    for k, (wi, tok) in enumerate(flat):
        prev = flat[k - 1][1] if k > 0 else None
        nxt = flat[k + 1][1] if k + 1 < len(flat) else None
        checks_by_word[wi].append(_ground_token(tok, prev, nxt, ev, declared_fixes, declared_implied))

    order = ["number", "ungrounded", "spelling", "compound", "abbreviation", "synonym", "stem", "exact", "connector"]
    for wi, w in enumerate(display_words):
        cs = checks_by_word[wi]
        if not cs:      # pure punctuation such as "-" or ","
            rep.words.append(WordCheck(w, "connector"))
            continue
        worst = min(cs, key=lambda c: order.index(c.kind))
        detail = "; ".join(f"{c.word}: {c.detail}" for c in cs if c.detail) if len(cs) > 1 else worst.detail
        rep.words.append(WordCheck(w, worst.kind, detail))

    for c in (c for cs in checks_by_word.values() for c in cs):
        if c.kind in ("number", "ungrounded") and c.word in quarantined:
            rep.issues.append(Issue(CRITICAL, "from_removed_text",
                                    f"'{c.word}' only appears in text that was removed for safety "
                                    f"(an instruction to the AI or contact/price details)."))
        elif c.kind == "number":
            rep.issues.append(Issue(CRITICAL, "invented_number", f"'{c.word}' is a number the supplier never gave."))
        elif c.kind == "ungrounded" and c.word in PROMO_WORDS:
            rep.issues.append(Issue(CRITICAL, "promo_word", f"'{c.word}' is a marketing/subjective word the supplier never used."))
        elif c.kind == "ungrounded" and c.word in declared_implied:
            rep.issues.append(Issue(REVIEW, "implied_word", f"'{c.word}' is not in the input; the model says it is implied. Needs a human check."))
        elif c.kind == "ungrounded":
            rep.issues.append(Issue(MAJOR, "ungrounded_word", f"'{c.word}' does not appear in the supplier input."))
        elif c.kind == "spelling":
            rep.issues.append(Issue(INFO, "spelling_fixed", f"'{c.word}' {c.detail}."))

    # grounded but subjective (the supplier wrote "best quality"): allowed, but flagged
    for tok in sorted({t for _, t in flat if t in SUBJECTIVE_WORDS and t in ev.tokens}):
        rep.issues.append(Issue(REVIEW, "subjective_word", f"'{tok}' is a subjective word (the supplier did write it)."))

    # --- redundancy: the same idea twice ("Men Male", "Bottle Bottle", "SS ... Steel") ------
    content = [t for _, t in flat if not t[0].isdigit() and t not in CONNECTORS and len(t) > 1
               and (_class_of(t) or ("", ""))[1] != "unit"]
    seen: list[tuple[str, set]] = []
    for t in content:
        keys = set(stem_candidates(t, plurals_only=True))
        cls = _class_of(t)
        if cls:
            keys.add("class:" + cls[0])
        if t in ABBREVIATIONS:          # "SS" carries "stainless" and "steel"
            keys |= set(ABBREVIATIONS[t])
        for prev_t, prev_keys in seen:
            if keys & prev_keys:
                rep.issues.append(Issue(MAJOR, "redundant", f"'{prev_t}' and '{t}' say the same thing."))
                break
        seen.append((t, keys))

    # "Shampoo and Conditioner" must not become "Shampoo Conditioner": two things the supplier joined with "and"
    name_toks = tokenize(listing.name)
    title_toks = [t for _, t in flat]
    for i in range(len(name_toks) - 2):
        x, conj, y = name_toks[i:i + 3]
        if conj == "and" and x.isalpha() and y.isalpha():
            for j in range(len(title_toks) - 1):
                if (stem_candidates(title_toks[j]) & stem_candidates(x)
                        and stem_candidates(title_toks[j + 1]) & stem_candidates(y)):
                    rep.issues.append(Issue(MAJOR, "dropped_and",
                                            f"The supplier wrote '{x} and {y}'; the title fused them into "
                                            f"'{x.title()} {y.title()}'. Keep the 'and'."))
                    break

    # --- length & shape -----------------------------------------------------------------
    n = len(title)
    if n < MIN_CHARS:
        rep.issues.append(Issue(MAJOR, "too_short", f"Title is only {n} characters."))
    elif n > HARD_MAX_CHARS:
        rep.issues.append(Issue(MAJOR, "too_long", f"Title is {n} characters (limit {HARD_MAX_CHARS})."))
    elif n > TARGET_MAX_CHARS:
        rep.issues.append(Issue(INFO, "long", f"Title is {n} characters (target ≤ {TARGET_MAX_CHARS})."))
    if len(display_words) > MAX_WORDS:
        rep.issues.append(Issue(MAJOR, "too_many_words", f"Title has {len(display_words)} words (limit {MAX_WORDS})."))
    if title.count(",") > MAX_COMMAS:
        rep.issues.append(Issue(MAJOR, "spec_dump", f"Title has {title.count(',')} commas; it reads like a spec list. "
                                                    f"Keep only the 2-4 details buyers need most."))

    # the core product must be in the title (most of its words: "CNC Lathe" is fine for "CNC Lathe Machine")
    head_idx = None
    if product_type:
        head = [t for t in tokenize(product_type) if not t[0].isdigit() and t not in CONNECTORS]
        title_stems = set().union(*(stem_candidates(t) for _, t in flat)) if flat else set()
        found = sum(bool(stem_candidates(h) & title_stems) for h in head)
        if head and found * 2 < len(head):
            rep.issues.append(Issue(MAJOR, "missing_product", f"The product type '{product_type}' is not in the title."))
        if head:
            idxs = [k for k, (_, t) in enumerate(flat) if stem_candidates(t) & stem_candidates(head[-1])]
            head_idx = idxs[-1] if idxs else None

    # readability: specs must not run together ("Press Machine 50 Ton Semi-Automatic 3 HP")
    stacked = _stacked_spec_segment(title)
    if stacked:
        rep.issues.append(Issue(MAJOR, "specs_run_together",
                                f"Specs run together in '{stacked}'. Separate them with commas or labels, "
                                f"or keep only the most important one."))

    # buyers search by audience ("kids school bag"): if the supplier stated one, the title keeps it
    stated_aud = {cid for cid, w in ev.classes.items() if SYNONYM_CLASSES[cid][0] == "audience"}
    title_aud = {(_class_of(t) or ("",))[0] for _, t in flat}
    if stated_aud and not (stated_aud & title_aud):
        names = ", ".join(sorted(ev.classes[c] for c in stated_aud))
        rep.issues.append(Issue(MAJOR, "dropped_audience",
                                f"The supplier stated who it is for ('{names}'), but the title dropped it. Add it back: "
                                f"adjectives such as 'Unisex' or 'Kids' go before the product ('Unisex Sunglasses'); "
                                f"men/women go after it with 'for' ('... for Men') or as a possessive before it."))

    # grammar: an audience word after the product needs "for" ("Kurti Women" -> "Kurti for Women")
    if head_idx is not None:
        for k in range(head_idx + 1, len(flat)):
            cls = _class_of(flat[k][1])
            if cls and cls[0] == "aud_unisex":
                # "Unisex" is an adjective: "Unisex Sunglasses", never "Sunglasses for Unisex"
                rep.issues.append(Issue(MAJOR, "audience_placement",
                                        "'Unisex' must come before the product (e.g. 'Unisex Sunglasses'), not after it."))
                break
            if cls and cls[1] == "audience" and flat[k - 1][1] not in ("for", "and", "or"):
                rep.issues.append(Issue(MAJOR, "audience_placement",
                                        f"'{flat[k][1]}' follows the product without 'for' (write 'for {flat[k][1].title()}' "
                                        f"or put the possessive before the product)."))
                break

    # a marketplace title never carries a phone number, link, e-mail or price
    for reason, pat in _CONTACT_RE:
        if m := pat.search(title):
            rep.issues.append(Issue(CRITICAL, "contact_or_price", f"The title contains a {reason} ('{m.group(0).strip()}')."))

    # --- word order and number meaning (category-agnostic) ---------------------------------
    if head_idx is not None:
        rep.issues.extend(_check_word_order(title, flat, head_idx, product_type, listing, modifier_roles))
    rep.issues.extend(_check_number_meaning(title, listing, product_type))
    if product_type:
        rep.issues.extend(_check_product_phrase(listing, product_type, title, explained))

    # --- coverage (informational) ----------------------------------------------------------
    title_ev = set()
    for _, t in flat:
        title_ev |= stem_candidates(t)
        cls = _class_of(t)
        if cls:
            title_ev.add("class:" + cls[0])
        if t in ABBREVIATIONS:
            title_ev |= set(ABBREVIATIONS[t])
    covered = 0
    for s in listing.specs:
        toks = [t for t in tokenize(s.value) if t not in CONNECTORS]
        hit = any((stem_candidates(t) & title_ev) or (_class_of(t) and "class:" + _class_of(t)[0] in title_ev)
                  for t in toks)
        if hit:
            covered += 1
        else:
            rep.omitted_specs.append(f"{s.attribute}: {s.value}" if s.attribute else s.value)
    rep.spec_coverage = covered / len(listing.specs) if listing.specs else 1.0
    return rep


_JOINERS = {"x", "-", "to", "by", "/"}
_CLAUSE_BREAKS = {"with", "for", "and", "of"}
_AREA_UNIT = re.compile(r"^[a-z]+(?:/[a-z]+)?[23]$")     # cm2, mm2, m3, kg/cm2: a unit, not a second value


def _stacked_spec_segment(title: str) -> str | None:
    """Return the first clause that holds two or more separate spec values, else None.

    A clause is a comma-separated part, also split at with/for/and/of ("Bag with 3 Compartments" is fine).
    A value is a number plus its unit or code; numbers joined by x/-/to ("40 x 40 x 5 mm",
    "250-300", "S to XXL") count as one value."""
    for segment in title.split(","):
        clause_words: list[list[str]] = [[]]
        for w in segment.split():
            if w.lower() in _CLAUSE_BREAKS:
                clause_words.append([])
            else:
                clause_words[-1].append(w)
        for words in clause_words:
            values, prev_numeric, prev_joiner = 0, False, False
            for j, w in enumerate(words):
                has_digit = any(ch.isdigit() for ch in w)
                if has_digit and prev_numeric and _AREA_UNIT.match(w.lower().strip(",:;")):
                    continue                  # "6 kg/cm2": the unit belongs to the number before it
                # codes are names, not measurements: "SS 304", "IS 2062", "B22", "UV400", "IP65"
                # (but a unit is not a code prefix: in "10 HP 300 L" the 300 is a second measurement)
                core = w.strip(",:;()")
                prev_word = words[j - 1].strip(",:;()") if j > 0 else ""
                prev_is_unit = bool(prev_word) and (_class_of(prev_word.lower()) or ("", ""))[1] == "unit"
                next_word = words[j + 1].strip(",:;()").lower() if j + 1 < len(words) else ""
                next_is_unit = bool(next_word) and (_class_of(next_word) or ("", ""))[1] == "unit"
                if has_digit and (re.fullmatch(r"[A-Za-z]+\d+[A-Za-z]?", core)
                                  or (prev_word.isalpha() and prev_word.isupper() and len(prev_word) <= 4
                                      and not prev_is_unit and not next_is_unit)):
                    prev_numeric, prev_joiner = False, False
                    continue
                if has_digit:
                    if not (prev_numeric and prev_joiner):
                        values += 1
                    prev_numeric, prev_joiner = True, False
                elif w.lower() in _JOINERS:
                    prev_joiner = True
                elif prev_numeric and (_class_of(w.lower().strip(":")) or ("", ""))[1] == "unit":
                    pass                      # unit after a number belongs to that value
                else:
                    prev_numeric, prev_joiner = False, False
            if values >= 2:
                return " ".join(words)
    return None


def _keys(word: str) -> set[str]:
    ks = set(stem_candidates(word))
    cls = _class_of(word)
    if cls:
        ks.add("class:" + cls[0])
    return ks


def _token_roles(flat: list[tuple[int, str]], listing: Listing, modifier_roles: list[dict] | None) -> dict[int, str]:
    """Role of each title token. Sources in order of trust: the supplier's own spec labels
    ("Colour: Brown"), general English word lists, then the model's own labels."""
    spec_role: dict[str, str] = {}
    for s in listing.specs:
        a = s.attribute.lower()
        # a value under another kind of label ("Insulation: PVC") is not the product's main material
        role = next((r for r, hints in ATTRIBUTE_ROLE_HINTS if any(h in a for h in hints)), "other" if a else None)
        if role:
            for t in tokenize(s.value):
                if not t[0].isdigit() and t not in CONNECTORS:
                    spec_role.setdefault(t, role)
    llm_role: dict[str, str] = {}
    for m in modifier_roles or []:
        role = _LLM_ROLE.get(m.get("role", ""), "other")
        for t in tokenize(m.get("text", "")):
            if not t[0].isdigit() and t not in CONNECTORS:
                llm_role.setdefault(t, role)
    roles = {}
    for k, (_, t) in enumerate(flat):
        if t[0].isdigit() or t in CONNECTORS or (_class_of(t) or ("", ""))[1] == "unit":
            continue
        role = spec_role.get(t)
        if not role:
            role = ("colour" if t in COLOUR_WORDS else "material" if t in MATERIAL_WORDS
                    else "pattern" if t in PATTERN_WORDS else None)
        roles[k] = role or llm_role.get(t, "other")
    return roles


def _check_word_order(title: str, flat: list[tuple[int, str]], head_idx: int, product_type: str,
                      listing: Listing, modifier_roles: list[dict] | None) -> list[Issue]:
    issues = []
    product_tokens = {t for t in tokenize(product_type) if not t[0].isdigit() and t not in CONNECTORS}
    product_keys = set().union(*(stem_candidates(t) for t in product_tokens)) if product_tokens else set()

    def is_product(t):
        return bool(stem_candidates(t) & product_keys)

    # the product's own name runs from phrase_start to head_idx ("Carry Bag Fabric")
    phrase_start = head_idx
    while phrase_start > 0 and (is_product(flat[phrase_start - 1][1]) or flat[phrase_start - 1][1] in CONNECTORS):
        phrase_start -= 1
    while phrase_start < head_idx and flat[phrase_start][1] in CONNECTORS:
        phrase_start += 1
    pre = list(range(phrase_start))

    # 1. nothing may be wedged inside the product's name: "Bag Cotton Fabric" -> "Cotton Bag Fabric"
    stray = [k for k in pre if is_product(flat[k][1]) and not flat[k][1] in CONNECTORS]
    if stray and len(product_tokens) > 1:
        between = [flat[k][1] for k in range(stray[-1] + 1, phrase_start) if not is_product(flat[k][1])
                   and flat[k][1] not in CONNECTORS and not flat[k][1][0].isdigit()]
        if between:
            issues.append(Issue(MAJOR, "word_order",
                                f"'{' '.join(between)}' splits the product name '{product_type}'. Keep the product "
                                f"name together and put describing words before it (e.g. '{between[0].title()} "
                                f"{product_type.title()}')."))

    # 2. general modifier order before the product
    roles = _token_roles(flat, listing, modifier_roles)
    pre_roles = [(k, roles[k]) for k in pre if k in roles and not is_product(flat[k][1])]
    for first, second in ORDER_RULES:
        a = [k for k, r in pre_roles if r == first]
        b = [k for k, r in pre_roles if r == second]
        if a and b and min(b) < max(a):
            w_b, w_a = flat[min(b)][1], flat[max(a)][1]
            issues.append(Issue(MAJOR, "word_order",
                                f"'{w_b.title()}' ({second}) should come after '{w_a.title()}' ({first}): in English the "
                                f"{first} goes before the {second} (e.g. '{w_a.title()} {w_b.title()} ...')."))
            break
    words = title.split()
    # a size goes before the material, not between material and product: "PVC 4 Inch Pipe" -> "4 Inch PVC Pipe".
    # A size is a number followed by a unit ("4 Inch"); a number without one is a grade or code ("SS 304").
    def is_size(k):
        return (flat[k][1][0].isdigit() and k + 1 < len(flat)
                and (_class_of(flat[k + 1][1]) or ("", ""))[1] == "unit")
    materials = [k for k, r in pre_roles if r == "material"]
    sizes = [k for k in pre if is_size(k)]
    if materials and sizes and max(sizes) > min(materials) and not issues:
        size_txt = f"{words[flat[max(sizes)][0]].strip(',')} {words[flat[max(sizes) + 1][0]].strip(',')}"
        mat_txt = words[flat[min(materials)][0]].strip(',')
        size_txt = size_txt if flat[max(sizes)][0] != flat[max(sizes) + 1][0] else words[flat[max(sizes)][0]].strip(',')
        issues.append(Issue(MAJOR, "word_order",
                            f"Put the size '{size_txt}' before the material '{mat_txt}' "
                            f"(e.g. '{size_txt} {mat_txt} ...')."))

    brands = [k for k, r in pre_roles if r == "brand"]
    if brands:
        before = [k for k in range(brands[0]) if roles.get(k) not in ("brand", "audience")
                  and flat[k][1] not in CONNECTORS and flat[k][0] != flat[brands[0]][0]]
        if before:
            issues.append(Issue(MAJOR, "word_order",
                                f"Put the brand '{words[flat[brands[0]][0]].strip(',')}' first, before "
                                f"'{words[flat[before[0]][0]].strip(',')}'."))

    # 3. a labelled spec belongs after the product: "Size S to XXL Printed Cotton Kurti" -> "..., Size S to XXL".
    #    A label is a word from a spec name ("Size", "Grade") that the supplier never used as a describing word.
    described = {t for t in tokenize(listing.name)} | {t for s in listing.specs for t in tokenize(s.value)}
    label_words = {t for s in listing.specs for t in tokenize(s.attribute)
                   if not t[0].isdigit() and t not in CONNECTORS} - described
    labels_before = [flat[k][1] for k in pre if flat[k][1] in label_words and not is_product(flat[k][1])]
    if labels_before:
        issues.append(Issue(MAJOR, "label_before_product",
                            f"'{labels_before[0].title()} ...' is a labelled spec placed before the product. Put it after "
                            f"the product, after a comma (e.g. '..., {labels_before[0].title()} ...')."))
    # a range belongs after the product too: "S to XXL Printed Kurti" -> "Printed Kurti, Size S to XXL"
    pre_words = range(flat[phrase_start][0])
    rng = next((words[i].strip(",") for i in pre_words
                if words[i].lower() == "to" or re.fullmatch(r"\d+(\.\d+)?-\d+(\.\d+)?", words[i].strip(","))), None)
    if rng and not labels_before:
        issues.append(Issue(MAJOR, "label_before_product",
                            "A range (such as 'S to XXL' or '250-300') is placed before the product. Put it after the "
                            "product, after a comma, with its label (e.g. '..., Size S to XXL')."))

    # 4. no commas before the product: "2 Inch, 90 Degree, SS 304 Elbow" reads as a list, not a name
    if any("," in words[i] for i in range(min(flat[phrase_start][0], len(words)))):
        issues.append(Issue(MAJOR, "comma_before_product",
                            "There is a comma before the product name. Keep at most one size or spec before the "
                            "product (no commas there) and move the other specs after the product, each after a comma."))

    # 5. English usage: "Female/Male" are not used as product adjectives ("Female Black Wallet" -> "... for Women"),
    #    and "Each" belongs to a quantity after the product ("200 ml Each Combo" -> "Combo, 200 ml Each")
    for k in pre:
        t = flat[k][1]
        if t in ("female", "females", "male", "males"):
            who = "Women" if t.startswith("fe") else "Men"
            issues.append(Issue(MAJOR, "audience_placement",
                                f"Do not use '{t.title()}' as an adjective before the product; write 'for {who}' after "
                                f"the product or '{who}'s' before it."))
            break
    if any(flat[k][1] == "each" for k in pre):
        issues.append(Issue(MAJOR, "word_order", "'... Each' describes a quantity: put it after the product, after a "
                                                 "comma (e.g. '..., 200 ml Each')."))

    # 6. after the product: "for <audience>" comes right after it, not after a spec ("Kurti, Size S to XXL for Women"),
    #    and a lone colour/pattern/material does not trail behind a comma ("Cotton Fabric, Plain" -> "Plain Cotton Fabric")
    head_word_idx = flat[head_idx][0]
    comma_after_head = False
    for k in range(head_idx + 1, len(flat)):
        wi, t = flat[k]
        if any(words[j].endswith(",") for j in range(head_word_idx, wi)):
            comma_after_head = True
        cls = _class_of(t)
        if (comma_after_head and cls and cls[1] == "audience" and k > 0 and flat[k - 1][1] == "for"):
            issues.append(Issue(MAJOR, "audience_placement",
                                f"Put 'for {t.title()}' directly after the product, before the specs that follow a comma."))
            break
    segments, current = [], []
    for wi in range(head_word_idx + 1, len(words)):
        current.append(wi)
        if words[wi].endswith(","):
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    in_list = False
    if not words[head_word_idx].endswith(",") and segments:
        # the first segment continues the product clause ("for Women", "with ..."); a list started
        # there continues through the following single words ("for Men, Gym, Office")
        in_list = any(words[wi].lower().strip(",") in ("for", "with") for wi in segments[0])
        segments = segments[1:]
    for seg in segments:
        ks = [k for k, (wi, t) in enumerate(flat) if wi in seg and not t[0].isdigit() and t not in CONNECTORS]
        seg_text = " ".join(words[wi].strip(",") for wi in seg)
        all_descriptors = ks and all(roles.get(k) in ("colour", "pattern", "material") for k in ks)
        # a single unlabelled word after a comma ("..., Automatic", "..., Strip") reads as a stray tag
        lone_word = (len(seg) == 1 and seg_text.isalpha() and not _class_of(seg_text.lower())
                     and seg_text.lower() not in CONNECTORS and not in_list)
        if len(seg) > 1 or any(ch.isdigit() for ch in seg_text):
            in_list = False
        if all_descriptors or lone_word:
            label = next((s.attribute for s in listing.specs
                          if s.attribute and seg_text.lower() in s.value.lower()), "")
            if all_descriptors:
                fix = f"Put it before the product name (e.g. '{seg_text} {words[head_word_idx].strip(',')}')."
            elif label:
                # v4.4 told the model to move "Strip" before the product and got "500 mg Strip Paracetamol
                # Tablet": a word that is not a colour/pattern/material is better kept after, with its label
                fix = (f"Keep it after the product but join it to its label or a neighbouring spec "
                       f"(e.g. '{label}: {seg_text}' or '10 x 10 {seg_text} Pack'); do not move it in front.")
            else:
                fix = "Join it to a neighbouring spec or drop it."
            issues.append(Issue(MAJOR, "descriptor_after_product", f"'{seg_text}' stands alone after a comma. {fix}"))
            break

    # 7. after the product only "for ...", "with ..." or a comma may follow: not "PVC Pipe 4 Inch"
    head_word = flat[head_idx][0]
    if head_word + 1 < len(words) and not words[head_word].rstrip().endswith((",", ")", ":")):
        nxt = words[head_word + 1].lower().strip(",;:")
        if nxt not in _AFTER_PRODUCT_OK and not nxt.startswith("("):
            issues.append(Issue(MAJOR, "trailing_words",
                                f"'{words[head_word + 1].strip(',')}' comes right after the product "
                                f"'{words[head_word].strip(',')}' without a comma or 'for'/'with'. Move it before "
                                f"the product, or put a comma before it."))
    return issues


_ADJ_SUFFIXES = ("ic", "al", "ed", "ful", "ous", "ive", "less", "able", "ible", "ish")


def _present(tok: str, title_tokens: list[str]) -> bool:
    """Is a supplier word represented in the title (same word, plural, synonym, abbreviation, spelling fix)?"""
    keys = _keys(tok) | set(ABBREVIATIONS.get(tok, ()))
    for t in title_tokens:
        if keys & (_keys(t) | set(ABBREVIATIONS.get(t, ()))):
            return True
        if len(tok) >= 4 and t[:1] == tok[:1] and SequenceMatcher(None, tok, t).ratio() >= 0.8:
            return True
    return False


def _plain_word(w: str) -> bool:
    cls = _class_of(w)
    return (w.isascii() and w.isalpha() and len(w) >= 2 and w not in CONNECTORS and w not in PROMO_WORDS
            and not (cls and cls[1] in ("unit", "audience")))


def _check_product_phrase(listing: Listing, product_type: str, title: str, explained: str) -> list[Issue]:
    """Compare the model's product with how the SUPPLIER phrased it. Two silent failures seen in testing:
    'Cotton cream cool Carry bag Fabrics' titled as a carry bag (the last noun, Fabrics, is the product),
    and 'CNC lathe machine' titled without 'CNC'. Only checked when the product phrase occurs once in
    the name, because keyword-stuffed names ('Gloves Safety Gloves Work Gloves') say nothing reliable."""
    pt = [t for t in tokenize(product_type) if t not in CONNECTORS and not t[0].isdigit()]
    if not pt or not listing.name:
        return []
    # work clause by clause: "Leather office bag. Call ..." must not make "Call" the next word after "bag"
    clauses = [tokenize(c) for c in re.split(r"[.,;:!?()]+(?:\s|$)|\s-\s", listing.name) if c.strip()]
    pt_keys = set().union(*(stem_candidates(p) for p in pt))
    spec_words = {t for s in listing.specs for t in tokenize(s.value)}
    title_tokens = tokenize(title)

    def match_end(name, i):
        j, k = i, 0
        while k < len(pt) and j < len(name):
            if name[j] in CONNECTORS and k > 0:
                j += 1
                continue
            if not (stem_candidates(name[j]) & stem_candidates(pt[k])):
                return None
            j, k = j + 1, k + 1
        return j if k == len(pt) else None

    matches = [(name, i, end) for name in clauses for i in range(len(name))
               if (end := match_end(name, i)) is not None]
    if len(matches) != 1:
        return []
    name, i, end = matches[0]
    issues = []
    if end < len(name):
        nxt = name[end]
        if (_plain_word(nxt) and len(nxt) >= 3 and not nxt.endswith(_ADJ_SUFFIXES)
                and nxt not in COLOUR_WORDS | MATERIAL_WORDS | PATTERN_WORDS | spec_words
                and not stem_candidates(nxt) & pt_keys and not _present(nxt, title_tokens) and nxt not in explained):
            phrase = " ".join(name[i:end]).title()
            head = (nxt[:-1] if nxt.endswith("s") and not nxt.endswith("ss") else nxt).title()
            issues.append(Issue(MAJOR, "wrong_product",
                                f"The supplier wrote '{' '.join(name[i:end + 1])}'. In English the last noun is the "
                                f"product, so this is a '{head}' for '{phrase}': keep the whole name together as the "
                                f"product, e.g. '{phrase} {head}' (or explain in omitted_details)."))
    if i > 0:
        prev = name[i - 1]
        if (_plain_word(prev) and prev not in COLOUR_WORDS and not stem_candidates(prev) & pt_keys
                and not _present(prev, title_tokens) and prev not in explained):
            label = prev.upper() if len(prev) <= 3 else prev.title()
            issues.append(Issue(MAJOR, "dropped_product_word",
                                f"The supplier calls the product '{prev} {' '.join(name[i:end])}', but the title dropped "
                                f"'{label}', which is part of the name buyers search for. Keep it (or explain in "
                                f"omitted_details)."))
    return issues


def _number_facts(listing: Listing) -> list[tuple[str, str | None, set[str]]]:
    """(number, unit class, label keys) for every number the supplier gave.
    'Swing Over Bed: 400 mm' -> ('400', 'u_mm', {swing, over, bed})."""
    facts = []

    def scan(text: str, labels: set[str]):
        toks = tokenize(text)
        for i, t in enumerate(toks):
            if t[0].isdigit():
                cls = _class_of(toks[i + 1]) if i + 1 < len(toks) else None
                facts.append((t, cls[0] if cls and cls[1] == "unit" else None, labels))

    scan(listing.name, set())
    for s in listing.specs:
        labels = set().union(*(_keys(a) for a in tokenize(s.attribute) if not a[0].isdigit() and a not in CONNECTORS)) \
            if s.attribute else set()
        scan(s.value, labels)
    return facts


def _check_number_meaning(title: str, listing: Listing, product_type: str = "") -> list[Issue]:
    """Presence is checked elsewhere; this checks MEANING: a number keeps its unit
    (750 ml must not become 750 L) and its label (1000 mm is the centre distance, not the swing)."""
    issues = []
    facts = _number_facts(listing)
    all_labels = set().union(*(f[2] for f in facts)) if facts else set()
    product_keys = set().union(*(_keys(t) for t in tokenize(product_type))) if product_type else set()

    def is_label_word(w):
        return not (w[0].isdigit() or w in CONNECTORS or (_class_of(w) or ("", ""))[1] == "unit"
                    or _keys(w) & product_keys)

    clauses = re.split(r",|\bfor\b|\bwith\b", title, flags=re.IGNORECASE)
    for clause in clauses:
        ct = tokenize(clause)
        for i, t in enumerate(ct):
            if not t[0].isdigit():
                continue
            own = [f for f in facts if f[0] == t]
            if not own:
                continue                       # an invented number is caught by the grounding check
            cls = _class_of(ct[i + 1]) if i + 1 < len(ct) else None
            unit = cls[0] if cls and cls[1] == "unit" else None
            if unit and all(f[1] for f in own) and not any(f[1] == unit for f in own):
                issues.append(Issue(CRITICAL, "unit_changed",
                                    f"'{t} {ct[i + 1]}' changes the unit: the supplier gave {t} in a different unit."))
            # the label is the word directly before the number, or directly after the number + unit
            after = i + 2 if unit else i + 1
            near = ([ct[i - 1]] if i > 0 else []) + ([ct[after]] if after < len(ct) else [])
            near_labels = set()
            for w in near:
                if is_label_word(w):
                    near_labels |= _keys(w) & all_labels
            if near_labels:
                own_labels = set().union(*(f[2] for f in own))
                if not (near_labels & own_labels):
                    right = next((f for f in facts if f[2] & near_labels), None)
                    issues.append(Issue(MAJOR, "number_label_mismatch",
                                        f"'{t}' is placed next to the wrong label: the supplier gave {t} for a different "
                                        f"attribute" + (f" (that label goes with {right[0]})." if right else ".")))
    return issues


def explain_removals(listing: Listing, title: str) -> list[tuple[str, str]]:
    """Which words of the supplier's product name did not make it into the title, and why.
    Computed by code, not self-reported by the model (the model's own list is often empty)."""
    def keys_of(tok):
        ks = set(stem_candidates(tok, plurals_only=True))
        cls = _class_of(tok)
        if cls:
            ks.add("class:" + cls[0])
        if tok in ABBREVIATIONS:
            ks |= set(ABBREVIATIONS[tok])
        return ks

    def word_keys(tok):
        return {k for k in keys_of(tok) if not k.startswith("class:")}

    title_tokens = tokenize(title)
    title_keys: dict[str, str] = {}
    for t in title_tokens:
        for k in keys_of(t):
            title_keys.setdefault(k, t)

    def match(tok):
        # prefer a direct word match over a synonym-class match (sets have no stable order)
        return next((k for k in sorted(keys_of(tok), key=lambda k: (k.startswith("class:"), k)) if k in title_keys),
                    None)

    pairs = [(w.strip(",;:.()"), t) for w in listing.name.split() for t in tokenize(w)]
    used: dict[str, str] = {}          # key -> supplier token that first used it
    out = []
    i = 0
    while i < len(pairs):
        label, tok = pairs[i]
        i += 1
        if tok in CONNECTORS or tok[0].isdigit():
            continue
        hit = match(tok)
        if hit is None and i < len(pairs) and match(tok + pairs[i][1]):     # "sun glasses" -> "Sunglasses"
            tok = tok + pairs[i][1]
            hit = match(tok)
            label = f"{label} {pairs[i][0]}"
            i += 1
        if hit is None:
            out.append((label, "left out"))
        elif hit in used:
            prev = used[hit]
            same_word = bool(word_keys(prev) & word_keys(tok))
            out.append((label, "repeated" if same_word else f"same meaning as '{title_keys[hit]}'"))
        else:
            for k in keys_of(tok):
                used.setdefault(k, tok)
            is_unit = (_class_of(tok) or ("", ""))[1] == "unit"
            if tok not in title_tokens and title_keys[hit] != tok and not is_unit:
                if " " in label:
                    out.append((label, f"joined into '{title_keys[hit]}'"))
                elif hit.startswith("class:"):
                    out.append((label, f"replaced by the synonym '{title_keys[hit]}'"))
    return out


def check_citations(attributes_used: list[dict], ev: Evidence) -> list[Issue]:
    """The model cites the supplier text behind each attribute. A citation that is not
    in the input is a strong hallucination signal."""
    issues = []
    for a in attributes_used or []:
        src = a.get("supplier_text") or ""
        toks = [t for t in tokenize(src) if t not in CONNECTORS]
        if not toks:
            continue
        missing = [t for t in toks if not (t in ev.tokens or ev.has_word(t))
                   and not any(SequenceMatcher(None, t, e).ratio() >= 0.75 for e in ev.alpha)]
        if len(missing) > len(toks) / 2:
            issues.append(Issue(MAJOR, "bad_citation",
                                f"The model cited '{src}' for '{a.get('value_in_title', '')}', which is not in the input."))
    return issues


# ---------------------------------------------------------------- formatting

_LOWER_CONNECTORS = {"for", "with", "and", "of", "in", "on", "to", "the", "a", "an", "by", "per", "or", "x", "from", "cum"}


def format_title(title: str) -> str:
    """Consistent formatting for every category: spacing, punctuation and casing.
    Conservative: never changes a word that already has deliberate mixed casing (e.g. "iPhone")."""
    t = (title or "").strip().strip("\"'`")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([,;:])", r"\1", t)
    t = re.sub(r",(?=\S)", ", ", t)
    t = re.sub(r"[\s,;:.\-|/]+$", "", t)
    # "Cotton Bag Fabric, for Lining" -> "Cotton Bag Fabric for Lining" (kept after a number/unit: "1 L, for Gym")
    t = re.sub(r"(?<=[A-Za-z]{3}),\s+(for|with)\b", lambda m: " " + m.group(1).lower(), t, flags=re.IGNORECASE)
    words = t.split(" ")
    out = []
    for i, w in enumerate(words):
        core = w.strip(",;:()")
        lw = core.lower()
        if not core:
            new = core
        elif i > 0 and lw in _LOWER_CONNECTORS:
            new = lw
        elif lw in UNIT_CASE and i > 0 and (re.search(r"\d$", words[i - 1].strip(",;:()"))
                                            or words[i - 1].strip(",;:()").lower() == "sq"):
            new = UNIT_CASE[lw]     # "9 w" -> "9 W", "1.5 sq mm" -> "1.5 sq mm"
        elif re.fullmatch(r"\d+(?:\.\d+)?[a-zA-Z]+", core):
            m = re.fullmatch(r"(\d+(?:\.\d+)?)([a-zA-Z]+)", core)
            unit = m.group(2).lower()
            new = m.group(1) + UNIT_CASE.get(unit, m.group(2))
        else:
            # capitalise each lower-case part ("semi-automatic" -> "Semi-Automatic", "men's" -> "Men's");
            # shout-case words of 4+ letters are softened ("PIPE" -> "Pipe"), acronyms stay ("PVC", "SS", "HDPE");
            # compound units keep unit casing ("kg/cm2")
            parts = re.split(r"([-/])", core)
            fixed = []
            for p in parts:
                if "/" in core and p.lower() in UNIT_CASE:
                    p = UNIT_CASE[p.lower()]
                elif p and p[0].isalpha() and p == p.lower():
                    p = p[0].upper() + p[1:]
                elif p.isalpha() and p.isupper() and len(p) >= 4 and p not in KEEP_UPPER:
                    p = p.capitalize()
                fixed.append(p)
            new = "".join(fixed)
        out.append(w.replace(core, new, 1) if core else w)
    return " ".join(out)
