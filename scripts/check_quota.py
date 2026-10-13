"""Check that the API key works and show the remaining rate limits for each model in use.

    python scripts/check_quota.py
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from title_engine.config import JUDGE_MODEL, MODES, PROVIDERS, api_key, load_env  # noqa: E402

load_env()
models = sorted({m for p, m in MODES["cascade"]["chain"]} | {JUDGE_MODEL[1]})
key = api_key("groq")
if not key:
    sys.exit("No GROQ_API_KEY configured.")
for model in models:
    r = requests.post(PROVIDERS["groq"]["base_url"] + "/chat/completions",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "messages": [{"role": "user", "content": "Say OK"}],
                            "max_completion_tokens": 20}, timeout=30)
    h = r.headers
    status = "OK" if r.ok else f"HTTP {r.status_code}: {r.text[:120]}"
    print(f"{model:<24} {status:<10} requests left today: {h.get('x-ratelimit-remaining-requests', '?'):>5}"
          f"   tokens left this minute: {h.get('x-ratelimit-remaining-tokens', '?')}")
