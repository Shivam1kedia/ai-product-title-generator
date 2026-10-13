"""Providers, models, prices and run modes."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (avoids an extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if val and not os.environ.get(key):
            os.environ[key] = val


# Prices are USD per 1M tokens, from console.groq.com/docs/models (checked 2026-10-13).
# Gemini prices are left as None: not verified for this prototype.
PROVIDERS = {
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "models": {
            "openai/gpt-oss-20b": {"label": "GPT-OSS 20B", "price": (0.075, 0.30), "strict": True,
                                   "extra": {"reasoning_effort": "low", "include_reasoning": False}},
            "openai/gpt-oss-120b": {"label": "GPT-OSS 120B", "price": (0.15, 0.60), "strict": True,
                                    "extra": {"reasoning_effort": "low", "include_reasoning": False}},
            "qwen/qwen3.8-27b": {"label": "Qwen 3.8 27B (preview)", "price": (0.80, 4.00), "strict": True,
                                 "extra": {}},
        },
    },
    # Prices from ai.google.dev/gemini-api/docs/pricing (checked 2026-10-13, paid Standard tier).
    # gemini-2.5-flash-lite is retired for new users; gemini-3.8-flash returned 503 "high demand" on the free tier.
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        "models": {
            "gemini-3.5-flash-lite": {"label": "Gemini 3.5 Flash-Lite", "price": (0.30, 2.50), "strict": True,
                                      "extra": {"reasoning_effort": "low"}},
            "gemini-3.8-flash": {"label": "Gemini 3.8 Flash", "price": (0.75, 3.75), "strict": True,
                                 "extra": {"reasoning_effort": "low"}},
        },
    },
}

# A mode is an ordered chain of (provider, model). Attempt 1 uses the first entry; if the
# verifier finds blocking problems, the repair attempt moves to the next entry.
MODES = {
    "cascade": {
        "label": "Cascade: 20B first, 120B repairs",
        "help": "Cheapest. The small model writes the title; only listings that fail verification go to the big "
                "model (up to two repairs).",
        "chain": [("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-120b"), ("groq", "openai/gpt-oss-120b")],
    },
    "quality": {
        "label": "Quality: 120B only",
        "help": "About twice the cost per title. Use it to compare quality against the cascade.",
        "chain": [("groq", "openai/gpt-oss-120b"), ("groq", "openai/gpt-oss-120b"), ("groq", "openai/gpt-oss-120b")],
    },
    "small": {
        "label": "Small only: 20B",
        "help": "Lowest cost and fastest, with no escalation. A baseline for the evaluation.",
        "chain": [("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-20b")],
    },
    "gemini": {
        "label": "Gemini 3.5 Flash-Lite (needs GEMINI_API_KEY)",
        "help": "Head-to-head against Groq: Gemini writes and repairs; the same code verifier checks it.",
        "chain": [("gemini", "gemini-3.5-flash-lite"), ("gemini", "gemini-3.5-flash-lite"),
                  ("gemini", "gemini-3.5-flash-lite")],
    },
}

# Eval-only judge. A different model family from the generator avoids self-preference.
# On 6 hand-labelled v1 titles, Qwen agreed with the human label 5/6 times; GPT-OSS-120B
# approved every title, including a 5-comma spec dump (1/6).
JUDGE_MODEL = ("groq", "qwen/qwen3.8-27b")


def api_key(provider: str) -> str | None:
    return os.environ.get(PROVIDERS[provider]["key_env"]) or None


def model_info(provider: str, model: str) -> dict:
    return PROVIDERS[provider]["models"].get(model, {"label": model, "price": None, "strict": False, "extra": {}})


def available_modes() -> dict:
    return {k: v for k, v in MODES.items() if all(api_key(p) for p, _ in v["chain"])}
