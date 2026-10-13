"""Thin OpenAI-compatible chat client with structured output, throttling and cost tracking."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass

import requests

from .config import PROVIDERS, api_key, model_info


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    data: dict
    content: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    latency_s: float
    cost_usd: float | None
    retries: int
    cached_tokens: int = 0


_session = requests.Session()
_limits: dict[tuple[str, str], dict] = {}   # (provider, model) -> {"tokens": remaining, "reset_at": t}
_lock = threading.Lock()


def _parse_duration(text: str | None) -> float:
    """Groq reset headers look like '3.457s', '1m26.4s' or '120ms'."""
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)(ms|h|m|s)", text):
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total


def _throttle(key: tuple[str, str], needed: int) -> None:
    with _lock:
        state = _limits.get(key)
    if not state:
        return
    wait = state["reset_at"] - time.time()
    if state["tokens"] < needed and wait > 0:
        time.sleep(min(wait + 0.2, 60))


def _record_limits(key: tuple[str, str], headers) -> None:
    rem = headers.get("x-ratelimit-remaining-tokens")
    if rem is None:
        return
    with _lock:
        _limits[key] = {"tokens": int(float(rem)),
                        "reset_at": time.time() + _parse_duration(headers.get("x-ratelimit-reset-tokens"))}


def chat_json(provider: str, model: str, messages: list[dict], schema: dict, schema_name: str = "result",
              temperature: float = 0.2, max_tokens: int = 1400, timeout: float = 60, max_retries: int = 4,
              extra: dict | None = None) -> LLMResult:
    key = api_key(provider)
    if not key:
        raise LLMError(f"No API key for {PROVIDERS[provider]['label']}. Set {PROVIDERS[provider]['key_env']}.")
    info = model_info(provider, model)
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema_name, "strict": bool(info.get("strict")), "schema": schema}},
        **info.get("extra", {}),
    }
    if extra:   # override only parameters this model supports (e.g. reasoning_effort for GPT-OSS)
        body.update({k: v for k, v in extra.items() if k in info.get("extra", {})})
    url = PROVIDERS[provider]["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    est_tokens = int(len(json.dumps(messages)) / 3.5) + 600

    retries = schema_retries = 0
    while True:
        _throttle((provider, model), est_tokens)
        t0 = time.time()
        try:
            resp = _session.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:
            if retries >= max_retries:
                raise LLMError(f"Network error calling {model}: {e}") from e
            retries += 1
            time.sleep(2 ** retries)
            continue
        latency = time.time() - t0
        _record_limits((provider, model), resp.headers)

        if resp.status_code == 429 or resp.status_code >= 500:
            wait = _parse_duration(resp.headers.get("retry-after"))
            if not wait:   # Gemini puts the delay in the body: "retryDelay": "17s"
                m = re.search(r'retryDelay"?\s*:\s*"?([\d.]+)s', resp.text)
                wait = float(m.group(1)) if m else 2 ** (retries + 1)
            if resp.status_code == 429 and wait > 90:
                # a long wait means a daily cap, not a per-minute one: fail fast so the pipeline
                # can move to the next model instead of making the user wait
                raise LLMError(f"Free-tier quota reached for {model}; it resets in about {int(wait // 60)} min.")
            if retries >= max_retries:
                raise LLMError(f"{model} is rate-limited or unavailable (HTTP {resp.status_code}). Try again shortly.")
            retries += 1
            time.sleep(min(wait + 0.5, 60))
            continue
        if not resp.ok:
            try:
                msg = resp.json().get("error", {}).get("message", resp.text)
            except ValueError:
                msg = resp.text
            # the model occasionally produces JSON that fails schema validation; a fresh sample usually passes
            if resp.status_code == 400 and "does not match the expected schema" in msg and schema_retries < 2:
                schema_retries += 1
                retries += 1
                continue
            raise LLMError(f"{model} returned HTTP {resp.status_code}: {msg[:300]}")

        payload = resp.json()
        choice = payload["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length":
            raise LLMError(f"{model} ran out of output tokens before finishing the JSON.")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"{model} returned invalid JSON: {content[:200]}") from e

        usage = payload.get("usage") or {}
        pt, ct = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        rt = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0)
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        price = info.get("price")
        # cached input tokens are billed at 50% on Groq (console.groq.com/docs/prompt-caching)
        cost = ((pt - cached) * price[0] + cached * price[0] * 0.5 + ct * price[1]) / 1e6 if price else None
        return LLMResult(data, content, provider, model, pt, ct, rt, latency, cost, retries, cached)


def list_models(provider: str) -> list[str]:
    key = api_key(provider)
    if not key:
        return []
    url = PROVIDERS[provider]["base_url"].rstrip("/") + "/models"
    r = _session.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))
