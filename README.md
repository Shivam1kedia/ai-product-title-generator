# AI Product Title Generator for B2B Listings

Turns messy supplier input (a product name plus attribute/value specs) into a clean, accurate,
search-friendly title for **any category**, **without adding details the supplier never gave**.

**Core idea:** the LLM writes the title, and plain code checks it. Every word of the title is traced back to the
supplier's own words before the title is accepted. Titles that fail are repaired by a bigger model;
anything still risky goes to a human.

```
supplier input ─► 1. normalise (code) ─► 2. generate (GPT-OSS 20B, strict JSON)
                                              │
                                              ▼
            5. route ◄── 3. verify (code: grounding, repeats, fluff, length) ──fail──► 4. repair (GPT-OSS 120B)
     auto-approve │ human review │ safe fallback
```

The full write-up for the evaluator (root cause analysis, platform choice, metrics, limitations) is in
[docs/SUBMISSION.md](docs/SUBMISSION.md). Every prompt, verbatim, is in [docs/PROMPTS.md](docs/PROMPTS.md).

## Quick start

Requires Python 3.10+ and a **free** Groq API key (no card): <https://console.groq.com/keys>

```bash
pip install -r requirements.txt
```

```bash
copy .env.example .env
```

Put your key in `.env` (`GROQ_API_KEY=gsk_...`), then start the demo:

```bash
streamlit run app.py
```

Open <http://localhost:8501>. If no key is configured, the app asks for one in the sidebar.

## What's in the app

| Tab | What it does |
|---|---|
| **Generate** | Type a listing or load one of 30 test listings. Shows the title, its status, colour-coded **word-by-word grounding** (hover a word to see where it came from), alternates, what was removed or fixed, search keywords, every attempt and repair, cost and latency. |
| **Batch & test set** | Runs the built-in test set or your own CSV (`product_name`, `specifications`), with optional LLM-judge scoring, and lets you download the results. |
| **Scorecard** | Saved evaluation runs, compared side by side (prompt v1 vs v2, model strategies). |
| **How it works** | Pipeline diagram, product decisions, exact prompts and the prompt changelog. |

## Evaluation

```bash
python eval/run_eval.py
```

That runs all 38 test listings (cascade mode, with the judge). Other options:

```bash
python eval/run_eval.py --mode quality --no-judge
```

```bash
python eval/run_eval.py --ids s1,h01,h05
```

```bash
python eval/run_eval.py --rejudge eval/results/v1_cascade.csv
```

Results are written to `eval/results/` (`<version>_<mode>.csv` and `_summary.json`).
The test set is in [data/test_cases.json](data/test_cases.json): the brief's 3 examples and 6 samples, 21 hard
cases (conflicts, prompt injection, keyword stuffing, Hindi text, services, pharma, empty specs, nonsense), and 8 cases
added for number meaning, word order and untrusted input (phone numbers, links, prices, English and Hinglish
instructions to the AI).

Unit tests for the deterministic parts (no API calls):

```bash
python -m pytest tests -q
```

To inspect a case attempt by attempt:

```bash
python scripts/debug_case.py ex3 h10
```

To check that the API key works and see each model's remaining requests:

```bash
python scripts/check_quota.py
```

To re-run the free code checks on a saved run after changing the verifier:

```bash
python scripts/recheck_titles.py eval/results/v4.3_cascade_run1.csv
```

## Project layout

```
app.py                     Streamlit demo
title_engine/
  preprocess.py            1. parse and clean supplier input; removes instructions to the AI, phones, links, prices
  prompts.py               all prompts + JSON schema + changelog (source of docs/PROMPTS.md)
  llm.py                   OpenAI-compatible client: strict JSON, throttling, retries, cost
  validator.py             3. verifier: grounding, word order, number meaning, readability; formatter
  lexicon.py               general language data: synonyms, units, colours, materials, injection patterns
  pipeline.py              2-5. generate -> verify -> repair/escalate -> route
  evaluation.py            metrics, LLM judge
  config.py                providers, models, prices, run modes
eval/run_eval.py           batch evaluation CLI
data/test_cases.json       38 test listings
tests/                     unit tests (no API calls)
scripts/                   debug_case.py, check_quota.py, recheck_titles.py, export_prompts.py
docs/                      SUBMISSION.md, PROMPTS.md
```

## Deploy a public demo (free)

**Streamlit Community Cloud:** push this folder to a GitHub repo (`.env` is git-ignored, so your key is not
uploaded). Then at <https://share.streamlit.io> choose *New app* → pick the repo → main file `app.py`. Under
*Advanced settings → Secrets*, add:

```toml
GROQ_API_KEY = "gsk_..."
# optional: visitors must enter this before using the app (protects your API quota)
APP_PASSWORD = "choose-a-password"
# optional: enables the Gemini comparison mode
GEMINI_API_KEY = "..."
```

Under *Advanced settings*, choose Python 3.12.

**Hugging Face Spaces** works the same way: create a Streamlit Space, upload the files, and add `GROQ_API_KEY` as a
secret.

> On the free Groq tier each model allows about 1,000 requests per day and 8,000 tokens per minute, which is
> roughly 5 titles a minute. That is enough for a live demo, not for bulk runs.
