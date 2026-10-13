"""Write docs/PROMPTS.md from title_engine/prompts.py so the submitted text is exactly what runs.

    python scripts/export_prompts.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from title_engine import prompts as p  # noqa: E402

doc = f"""# Prompts (version {p.PROMPT_VERSION})

Generated from `title_engine/prompts.py`; do not edit by hand. Every prompt the prototype sends is below.

## 1. System prompt: title generation (production path)

Model: `openai/gpt-oss-20b` on Groq (repairs on `openai/gpt-oss-120b`), temperature 0 (the same listing gets the
same title), `reasoning_effort=low`, strict JSON schema (section 4). Before this prompt runs, code removes
instructions to the AI, phone numbers, links and prices from the supplier text.

```text
{p.SYSTEM_PROMPT}
```

## 2. User message template

The cleaned listing goes inside tags so that supplier text is treated as data (prompt-injection defence).

```text
{p.USER_TEMPLATE}
```

Example of the filled block:

```text
<supplier_input>
Product name: Mens gents Steel water bottle bottle 1 litre
Specifications:
- Material: Stainless Steel
- Capacity: 1 L
- Usage: Gym, Office
</supplier_input>
```

## 3. Repair prompt (sent only when the code verifier finds a blocking problem)

The previous JSON is sent back as the assistant turn, followed by this message with the verifier's findings.

```text
{p.REPAIR_TEMPLATE}
```

## 4. Output schema (enforced with Groq strict structured outputs)

The analysis fields come before `title` on purpose: the model writes down sources, spelling fixes,
redundancies and conflicts before it writes the title.

```json
{json.dumps(p.TITLE_SCHEMA, indent=2)}
```

## 5. Judge prompt (evaluation only, never in the production path)

Model: `qwen/qwen3.8-27b` (a different model family from the generator), temperature 0.

```text
{p.JUDGE_PROMPT}
```

```text
{p.JUDGE_USER_TEMPLATE}
```

## 6. What changed and why

""" + "\n".join(f"- **{v}**: {note}" for v, note in p.CHANGELOG) + "\n"

out = ROOT / "docs" / "PROMPTS.md"
out.parent.mkdir(exist_ok=True)
out.write_text(doc, encoding="utf-8")
print(f"wrote {out}")
