"""Streamlit demo: AI product title generator for B2B listings.

    streamlit run app.py
"""
from __future__ import annotations

import html
import io
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="AI Title Generator", page_icon="🏷️", layout="wide")

# Streamlit Cloud: copy secrets into the environment before the engine reads them
try:
    for _k in ("GROQ_API_KEY", "GEMINI_API_KEY", "APP_PASSWORD"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = st.secrets[_k]
except Exception:
    pass

# Optional gate for a public deployment: anyone with the link would otherwise spend the owner's API quota.
# Set APP_PASSWORD in the Streamlit secrets to turn it on; without it the app is open.
if os.environ.get("APP_PASSWORD") and not st.session_state.get("unlocked"):
    st.title("🏷️ AI Product Title Generator")
    pw = st.text_input("This demo is password-protected. Enter the password to continue.", type="password")
    if pw:
        if pw == os.environ["APP_PASSWORD"]:
            st.session_state["unlocked"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()

from title_engine import STATUS_LABEL, generate  # noqa: E402
from title_engine.config import MODES, ROOT, api_key, available_modes  # noqa: E402
from title_engine.evaluation import load_cases, result_row, summarize  # noqa: E402
from title_engine.preprocess import parse_specs  # noqa: E402
from title_engine.validator import explain_removals  # noqa: E402
from title_engine.prompts import CHANGELOG, PROMPT_VERSION, REPAIR_TEMPLATE, SYSTEM_PROMPT, USER_TEMPLATE  # noqa: E402

CSS = """
<style>
.title-card {border:1px solid rgba(128,128,128,.35); border-radius:12px; padding:18px 20px; margin:4px 0 14px;}
.title-text {font-size:1.55rem; font-weight:650; line-height:1.3; margin:6px 0 2px;}
.before {opacity:.65; font-size:.92rem;}
.pill {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.8rem; font-weight:600;}
.pill-ok {background:rgba(34,197,94,.18); color:#15803d; border:1px solid rgba(34,197,94,.55);}
.pill-review {background:rgba(234,179,8,.18); color:#a16207; border:1px solid rgba(234,179,8,.6);}
.pill-fallback {background:rgba(239,68,68,.15); color:#b91c1c; border:1px solid rgba(239,68,68,.55);}
@media (prefers-color-scheme: dark) {
  .pill-ok {color:#4ade80;} .pill-review {color:#facc15;} .pill-fallback {color:#f87171;}
}
.chips {line-height:2.3;}
.chip {display:inline-block; padding:1px 8px; margin:0 3px 0 0; border-radius:6px; font-size:.95rem; border:1px solid transparent; cursor:help;}
.k-exact, .k-stem {background:rgba(34,197,94,.16); border-color:rgba(34,197,94,.5);}
.k-synonym, .k-abbreviation, .k-compound {background:rgba(59,130,246,.16); border-color:rgba(59,130,246,.5);}
.k-spelling {background:rgba(234,179,8,.18); border-color:rgba(234,179,8,.6);}
.k-connector {opacity:.7;}
.k-ungrounded, .k-number {background:rgba(239,68,68,.16); border-color:rgba(239,68,68,.6);}
.legend span {margin-right:12px; font-size:.8rem;}
.small {font-size:.85rem; opacity:.8;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

KIND_HELP = {
    "exact": "Supplier wrote this word", "stem": "Supplier wrote this word (another form)",
    "synonym": "Same meaning as a supplier word", "abbreviation": "Abbreviation of supplier words",
    "compound": "Joined or split supplier words", "spelling": "Spelling corrected",
    "connector": "Connecting word", "ungrounded": "NOT in supplier input", "number": "Number NOT in supplier input",
}
PILL = {"auto_approved": "pill-ok", "needs_review": "pill-review", "fallback": "pill-fallback"}

SCORECARD_ROWS = [
    ("Publishable as-is, % (judge)", "judge_publishable_as_is_pct"),
    ("Invented details, % (judge)", "judge_invented_detail_pct"),
    ("Grounded, % (verifier)", "grounded_pct (verifier: every word traced to input)"),
    ("Redundancy-free, % (verifier)", "redundancy_free_pct (verifier)"),
    ("Key-term recall, %", "key_term_recall_pct"),
    ("Cases with forbidden terms", "cases_with_forbidden_terms"),
    ("Risky cases sent to review, %", "risky_cases_routed_to_review_pct"),
    ("Auto-approved, %", "auto_approved_pct"),
    ("Needs review, %", "needs_review_pct"),
    ("Fallback, %", "fallback_pct"),
    ("Escalated to 2nd attempt, %", "escalated_to_second_attempt_pct"),
    ("Avg characters", "avg_chars"),
    ("Within 25-75 chars, %", "within_25_75_chars_pct"),
    ("Distinct title structures, % (variety)", "distinct_structures_pct (variety)"),
    ("Avg AI latency, s", "avg_llm_latency_s"),
    ("p95 AI latency, s", "p95_llm_latency_s"),
    ("Cost per title, $", "avg_cost_per_title_usd"),
    ("Cost per 1M titles, $", "projected_cost_per_1M_titles_usd"),
    ("Judge accuracy (1-5)", "judge_avg_accuracy_1to5"),
    ("Judge fluency (1-5)", "judge_avg_fluency_1to5"),
    ("Judge search fit (1-5)", "judge_avg_search_fit_1to5"),
    ("Judge model", "judge_model"),
    ("Prompt version", "prompt_version"),
    ("Mode", "mode"),
    ("Run at", "run_at"),
]


@st.cache_data
def cases() -> list[dict]:
    return load_cases()


def specs_to_lines(specs: str) -> str:
    return "\n".join(f"{s.attribute}: {s.value}" if s.attribute else s.value for s in parse_specs(specs))


def chips_html(report) -> str:
    out = []
    for w in report.words:
        tip = KIND_HELP.get(w.kind, w.kind) + (f" - {w.detail}" if w.detail else "")
        out.append(f'<span class="chip k-{w.kind}" title="{html.escape(tip)}">{html.escape(w.word)}</span>')
    return '<div class="chips">' + "".join(out) + "</div>"


def legend_html() -> str:
    items = [("k-exact", "from supplier"), ("k-synonym", "synonym / abbreviation"), ("k-spelling", "spelling fixed"),
             ("k-ungrounded", "not in input")]
    return '<div class="legend">' + "".join(
        f'<span><span class="chip {c}">&nbsp;</span>{t}</span>' for c, t in items) + "</div>"


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Engine")
    if not api_key("groq"):
        st.warning("No Groq API key configured.")
        typed = st.text_input("Groq API key (free at console.groq.com/keys)", type="password")
        if typed:
            os.environ["GROQ_API_KEY"] = typed.strip()
            st.rerun()
    modes = available_modes()
    if modes:
        mode = st.radio("Model strategy", list(modes), format_func=lambda m: MODES[m]["label"],
                        help="\n\n".join(f"**{MODES[m]['label']}**: {MODES[m]['help']}" for m in modes))
    else:
        mode = None
    with st.expander("Advanced"):
        temperature = st.slider("Temperature", 0.0, 1.0, 0.0, 0.1,
                                help="0 gives the same title for the same listing. Variety comes from the alternates.")
        use_cache = st.checkbox("Reuse results for identical input", value=True)
    st.caption(f"Prompt version: **{PROMPT_VERSION}**")
    st.divider()
    st.markdown(
        "**Status**\n\n"
        "🟢 **Auto-approved**: every word traced to the input, no conflicts, model confident.\n\n"
        "🟡 **Needs review**: a conflict, an inferred word, or low confidence. Goes to a human.\n\n"
        "🔴 **Fallback**: the AI broke a hard rule twice. A safe rule-based title is used and a human reviews it.")

st.title("🏷️ AI Product Title Generator")
st.caption("Turns messy supplier input into a clean, search-friendly title for any category, without adding details "
           "the supplier never gave. The LLM writes the title and plain code checks every word against the input.")

tab_gen, tab_batch, tab_score, tab_how = st.tabs(["✍️ Generate", "📦 Batch & test set", "📊 Scorecard", "⚙️ How it works"])

# ---------------------------------------------------------------- generate
with tab_gen:
    all_cases = cases()
    labels = ["Type your own"] + [f"[{c['id']}] {c['product_name'][:70]}" for c in all_cases]
    by_label = dict(zip(labels[1:], all_cases))

    def _load_example():
        case = by_label.get(st.session_state["example"])
        if case:
            st.session_state["in_name"] = case["product_name"]
            st.session_state["in_specs"] = specs_to_lines(case.get("specifications", ""))
            st.session_state.pop("result", None)

    left, right = st.columns([5, 7], gap="large")
    with left:
        st.selectbox("Load an example (from the brief or my hard cases)", labels, key="example", on_change=_load_example)
        st.text_input("Product name, as the supplier typed it", key="in_name",
                      placeholder="e.g. Mens gents Steel water bottle bottle 1 litre")
        st.text_area("Specifications: one 'Attribute: Value' per line (or separated by ;)", key="in_specs",
                     height=150, placeholder="Material: Stainless Steel\nCapacity: 1 L\nUsage: Gym, Office")
        c1, c2 = st.columns(2)
        go = c1.button("Generate title", type="primary", width="stretch", disabled=not mode)
        again = c2.button("Regenerate (skip cache)", width="stretch", disabled=not mode)
        if go or again:
            with st.spinner("Writing and verifying the title..."):
                st.session_state["result"] = generate(st.session_state.get("in_name", ""),
                                                      st.session_state.get("in_specs", ""),
                                                      mode=mode, use_cache=use_cache and not again,
                                                      temperature=temperature)
        case = by_label.get(st.session_state.get("example"))
        if case:
            st.info(f"**What this case tests:** {case.get('challenge', '')}"
                    + (f"\n\n**Reference title in the brief:** {case['reference_title']}" if case.get("reference_title") else ""))

    with right:
        res = st.session_state.get("result")
        if res is None:
            st.markdown("#### Result appears here")
            st.markdown("Pick an example on the left or type a listing, then click **Generate title**.")
        else:
            label = STATUS_LABEL[res.status]
            st.markdown(
                f'<div class="title-card"><span class="pill {PILL[res.status]}">{label}</span>'
                f'<div class="title-text">{html.escape(res.title) or "(no title)"}</div>'
                f'<div class="before">Supplier wrote: {html.escape(res.listing.raw_name)}</div></div>',
                unsafe_allow_html=True)
            if res.report and res.title:
                st.markdown("**Where every word came from** (hover a word)")
                st.markdown(chips_html(res.report) + legend_html(), unsafe_allow_html=True)
            if res.reasons:
                st.markdown("**Why it needs review**" if res.status != "auto_approved" else "**Notes**")
                for r in res.reasons:
                    st.markdown(f"- {r}")
            if res.error:
                st.error(res.error)

            pt, ct = res.tokens
            cost = f"${res.cost_usd:.5f}" if res.cost_usd is not None else "n/a"
            attempts_note = " (first title failed a check and was repaired)" if len(res.attempts) > 1 else ""
            st.markdown(
                f'<div class="small">Model <b>{html.escape((res.final_model or "-").split("/")[-1])}</b> · '
                f'Attempts <b>{len(res.attempts)}</b>{attempts_note} · '
                f'AI latency <b>{res.llm_latency_s:.2f}s</b>{" (cached)" if res.cached else ""} · '
                f'Cost <b>{cost}</b> ({pt} input + {ct} output tokens)</div>', unsafe_allow_html=True)

            if res.alternates:
                st.markdown("**Alternates** (other wordings, each checked the same way)")
                for t, r in res.alternates:
                    ok = not r.blocking
                    st.markdown(f"- {'✅' if ok else '⚠️'} {html.escape(t)}"
                                + ("" if ok else f"  <span class='small'>({'; '.join(i.message for i in r.blocking)})</span>"),
                                unsafe_allow_html=True)

            d = res.data
            with st.expander("What changed from the supplier's text", expanded=True):
                rows = []
                for r in res.listing.removed:
                    rows.append(("Removed for safety", f"{r.text} ({r.reason}, in {r.where})"))
                # computed by code from the final title (the model's own change list is often incomplete)
                for word, why in explain_removals(res.listing, res.title):
                    rows.append(("Removed from name", f"{word}: {why}"))
                if res.report:
                    for w in res.report.words:
                        if w.kind == "spelling":
                            rows.append(("Spelling fixed", f"{w.word} ({w.detail})"))
                for x in d.get("omitted_details", []):
                    rows.append(("Left out", f"{x['detail']}: {x['reason']}"))
                for x in d.get("conflicts", []):
                    rows.append(("Conflict", x))
                for x in d.get("implied_additions", []):
                    rows.append(("Inferred (not stated)", f"{x['word']} (from '{x['implied_by']}')"))
                for s, why in res.listing.dropped_specs:
                    rows.append(("Ignored spec", f"{s.attribute}: {s.value} ({why})"))
                if rows:
                    st.dataframe(pd.DataFrame(rows, columns=["Change", "Detail"]), hide_index=True,
                                 width="stretch")
                else:
                    st.write("Nothing to report.")
            if d.get("search_keywords"):
                with st.expander("Search keywords: for the search index, not shown in the title"):
                    st.write(", ".join(d["search_keywords"]))
            with st.expander(f"Attempts and repairs ({len(res.attempts)})"):
                for i, a in enumerate(res.attempts, 1):
                    st.markdown(f"**Attempt {i}: {a.model}**: {html.escape(a.title) if a.title else ''}")
                    if a.note:
                        st.caption(a.note)
                    if a.error:
                        st.error(a.error)
                    elif a.report and a.report.blocking:
                        for iss in a.report.blocking:
                            st.markdown(f"- `{iss.severity}` {iss.message}")
                    elif a.report:
                        st.markdown("- passed all blocking checks")
            with st.expander("Raw model output (JSON)"):
                st.json(d)
            with st.expander("Exact input sent to the model"):
                st.code(USER_TEMPLATE.format(listing_block=res.listing.to_prompt_block()), language="text")

# ---------------------------------------------------------------- batch
with tab_batch:
    st.markdown("Run many listings at once: the built-in test set (3 brief examples, 6 brief samples, "
                "21 hard cases, 8 cases for numbers, word order and injection) or your own CSV with columns "
                "`product_name` and `specifications`.")
    src = st.radio("Source", ["Built-in test set", "Upload CSV"], horizontal=True)
    batch_cases = None
    if src == "Built-in test set":
        groups = sorted({c["group"] for c in cases()})
        pick = st.multiselect("Groups", groups, default=groups)
        batch_cases = [c for c in cases() if c["group"] in pick]
    else:
        up = st.file_uploader("CSV file", type=["csv"])
        if up is not None:
            df_in = pd.read_csv(up).fillna("")
            if "product_name" not in df_in.columns:
                st.error("The CSV needs a 'product_name' column (and optionally 'specifications').")
            else:
                batch_cases = [{"id": f"row{i + 1}", "group": "upload", "category": "",
                                "product_name": r["product_name"], "specifications": r.get("specifications", ""),
                                "must_include": [], "must_not_include": []}
                               for i, r in df_in.iterrows()]
                st.caption(f"{len(batch_cases)} rows loaded. Free-tier limits allow about 5 listings a minute per model.")
    with_judge = st.checkbox("Also score each title with an independent LLM judge (uses more free-tier tokens)", value=False)
    if st.button("Run batch", type="primary", disabled=not (mode and batch_cases)):
        rows, bar = [], st.progress(0.0)
        status = st.empty()
        for i, c in enumerate(batch_cases, 1):
            status.write(f"{i}/{len(batch_cases)}: {c['product_name'][:80]}")
            r = generate(c["product_name"], c.get("specifications"), mode=mode, use_cache=use_cache,
                         temperature=temperature)
            rows.append(result_row(c, r, with_judge=with_judge))
            bar.progress(i / len(batch_cases))
        status.empty()
        st.session_state["batch_rows"] = rows
    rows = st.session_state.get("batch_rows")
    if rows:
        summ = summarize(rows)
        k = st.columns(5)
        k[0].metric("Grounded", f"{summ['grounded_pct (verifier: every word traced to input)']}%")
        k[1].metric("Redundancy-free", f"{summ['redundancy_free_pct (verifier)']}%")
        k[2].metric("Auto-approved", f"{summ['auto_approved_pct']}%")
        k[3].metric("Avg AI latency", f"{summ['avg_llm_latency_s']}s")
        k[4].metric("Cost / 1M titles", f"${summ['projected_cost_per_1M_titles_usd']}"
                    if summ["projected_cost_per_1M_titles_usd"] is not None else "n/a")
        df = pd.DataFrame(rows)
        show = [c for c in ["id", "product_name", "title", "status", "review_reasons", "alternates", "chars",
                            "key_terms_missing", "forbidden_terms_found", "judge_accuracy", "judge_fluency",
                            "judge_search_fit", "judge_invented", "final_model", "cost_usd"] if c in df.columns]
        st.dataframe(df[show], width="stretch", hide_index=True)
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        st.download_button("Download results (CSV)", buf.getvalue(), "titles.csv", "text/csv")
        with st.expander("All metrics"):
            st.json(summ)

# ---------------------------------------------------------------- scorecard
with tab_score:
    st.markdown("Saved evaluation runs (`python eval/run_eval.py`), oldest to newest. Runs up to v3 cover 30 "
                "listings; from v4 on, 38 (8 new cases for numbers, word order and untrusted input). Each run is "
                "scored by the code verifier plus an independent judge from a different model family (Qwen 3.8 27B, "
                "calibrated against hand labels). The headline metric is **publishable as-is**: the share of titles "
                "a demanding editor would publish without any edit. For a like-for-like table run "
                "`python scripts/compare_runs.py`.")
    res_dir = ROOT / "eval" / "results"
    summaries = sorted(res_dir.glob("*_summary.json"), key=lambda p: p.stat().st_mtime) if res_dir.exists() else []
    summaries = [p for p in summaries if "_subset" not in p.name]
    if not summaries:
        st.info("No saved runs yet.")
    else:
        table = {}
        for p in summaries:
            s = json.loads(p.read_text(encoding="utf-8"))
            col = p.name.replace("_summary.json", "")
            table[col] = {label: "" if s.get(key) is None else str(s.get(key)) for label, key in SCORECARD_ROWS}
        st.dataframe(pd.DataFrame(table), width="stretch", height=36 * (len(SCORECARD_ROWS) + 1))
        st.caption("'_rejudged' = the titles of that run re-scored by the current judge, so versions are compared "
                   "with the same yardstick.")
        pick = st.selectbox("Per-listing results", [p.name.replace("_summary.json", "") for p in summaries][::-1])
        csv_path = res_dir / f"{pick}.csv"
        if csv_path.exists():
            dfr = pd.read_csv(csv_path).fillna("")
            cols = [c for c in ["id", "product_name", "title", "status", "review_reasons", "judge_accuracy",
                                "judge_fluency", "judge_search_fit", "judge_invented", "judge_comment"] if c in dfr.columns]
            st.dataframe(dfr[cols], width="stretch", hide_index=True)

# ---------------------------------------------------------------- how it works
with tab_how:
    st.markdown("### Pipeline")
    st.graphviz_chart("""
digraph {
  rankdir=LR; node [shape=box, style="rounded", fontname="Helvetica", fontsize=11];
  in [label="Supplier input\\nname + specs"];
  pre [label="1. Normalise (code)\\nparse specs, drop NA,\\nstrip noise"];
  gen [label="2. Generate (LLM)\\nGPT-OSS 20B, JSON schema:\\ntitle + sources + changes"];
  ver [label="3. Verify (code)\\nevery word traced to input,\\nno repeats, no fluff, length"];
  rep [label="4. Repair (LLM)\\nGPT-OSS 120B gets the\\nexact problems"];
  route [label="5. Route"];
  ok [label="Auto-approve", shape=ellipse];
  rev [label="Human review", shape=ellipse];
  fb [label="Safe fallback\\n+ review", shape=ellipse];
  in -> pre -> gen -> ver -> route;
  ver -> rep [label="fails"]; rep -> ver;
  route -> ok [label="clean + confident"];
  route -> rev [label="conflict / inferred / low conf."];
  route -> fb [label="still breaks a hard rule"];
}""")
    st.markdown("""
### Product decisions
| Question | Decision | Why |
|---|---|---|
| Title length | 25-75 characters, hard limit 100; never padded | The brief's own reference titles are 28-43 characters; short titles fit a mobile search card |
| Attribute order | "How a buyer would type it": details that name the product, then the product, then *for* use/audience, then a key technical spec | A principle, not a template, so it works for kurtis and hydraulic presses alike |
| Missing info | Never filled in | The constraint: no invented details |
| Conflicts (name vs. specs) | Use the spec value, flag the listing, send to review | Specs are structured fields, more deliberate than free text |
| Synonyms | One in the title, the rest in a separate **search keywords** field | Keeps titles clean but keeps search recall |
| Readability | At most one number-with-unit before the product, other specs after commas; audience kept, with "for" or as an adjective before the product | Checked by code, so a run-on title is repaired, not auto-approved |
| Word order | Brand, size, colour, pattern/finish, material, purpose, product; product name never split; nothing bare after the product | General English order, checked by code; the model labels word roles, spec labels and word lists override it |
| Number meaning | Every number keeps the unit and label the supplier gave it | "1000 mm Swing" or "750 L" (from 750 ml) is caught even though the digits exist in the input |
| Untrusted input | Instructions to the AI, phone numbers, links and prices are removed before the AI sees the text; removed words cannot ground a title word | Defence in layers that does not depend on the model obeying; injection attempts go to human review |
| Human review | Only when the verifier or model raises a flag | Review effort goes where risk is |
| Variety | No fixed template; 2 checked alternates per listing | Natural phrasing per category without losing accuracy |
| Prompt injection | Supplier text is wrapped and treated as data | Supplier text is untrusted input |
""")
    st.markdown("### System prompt (exact text)")
    st.code(SYSTEM_PROMPT, language="text")
    st.markdown("### Repair prompt")
    st.code(REPAIR_TEMPLATE, language="text")
    st.markdown("### Prompt changelog")
    for v, note in CHANGELOG:
        st.markdown(f"- **{v}**: {note}")
