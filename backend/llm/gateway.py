"""
FINN — LLM Gateway
Single entry point for all LLM calls. Plain OpenAI-compatible /chat/completions.
- No response_format, no reasoning_effort (plain payloads only).
- JSON contracts enforced by prompt + client-side parse/pydantic validation.
- IPv4 forced (mobile hotspots / proxies break on v6).
- SQLite response cache (hash of model+messages) + cost ledger.
- 90s timeout, exponential backoff on 429/5xx, 4s throttle between sequential
  calls (free tier).
"""
import hashlib
import json
import re
import sqlite3
import time
import datetime

import requests
import urllib3.util.connection as _urlconn
_urlconn.HAS_IPV6 = False  # force IPv4 in this process

from pydantic import BaseModel, ValidationError

from backend.config import LLM_BASE_URL, LLM_API_KEY, CHEAP_MODEL, STRONG_MODEL, DB_PATH

JSON_INSTRUCTION = (
    "Return ONLY a JSON object. No prose, no markdown, no code fences. "
    "It must validate against this JSON Schema:\n{schema}"
)

_LAST_REQ_TS = 0.0
_THROTTLE_S = 4.0


def _throttle():
    global _LAST_REQ_TS
    wait = _THROTTLE_S - (time.time() - _LAST_REQ_TS)
    if wait > 0:
        time.sleep(wait)
    _LAST_REQ_TS = time.time()


def _cache_key(model: str, messages: list) -> str:
    raw = json.dumps({"model": model, "messages": messages}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> str | None:
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT response FROM llm_cache WHERE hash=?", (key,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _cache_set(key: str, model: str, response: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache(hash, model, response, created_at) VALUES (?,?,?,?)",
            (key, model, response, datetime.datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _ledger(step: str, model: str, tokens_in: int, tokens_out: int, cached: bool):
    try:
        est_cost = (tokens_in * 0.075 + tokens_out * 0.3) / 1_000_000
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO llm_ledger(ts,step,model,tokens_in,tokens_out,est_cost,cached) VALUES(?,?,?,?,?,?,?)",
            (datetime.datetime.utcnow().isoformat(), step, model, tokens_in, tokens_out, est_cost, int(cached))
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    return text


def _extract_json(text: str):
    text = _strip_fences(text)
    return json.loads(text)


def _do_request(model: str, messages: list) -> tuple[str, int, int]:
    """Single POST to /chat/completions. Returns (content, tokens_in, tokens_out)."""
    global _LAST_REQ_TS
    _throttle()
    url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0.1}
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    _LAST_REQ_TS = time.time()
    resp = requests.post(url, json=payload, headers=headers, timeout=90)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise requests.HTTPError(f"{resp.status_code} {resp.text[:200]}", response=resp)
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"] or {}
    content = msg.get("content") or ""
    usage = data.get("usage", {})
    return content, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def _with_json_rule(messages: list, schema: dict | None) -> list:
    sys_text = JSON_INSTRUCTION.format(schema=json.dumps(schema or {}))
    out = []
    for m in messages:
        if m.get("role") == "system":
            out.append({**m, "content": m["content"] + "\n\n" + sys_text})
        else:
            out.append(m)
    if not any(m.get("role") == "system" for m in messages):
        out = [{"role": "system", "content": sys_text}] + out
    return out


def _transport_call(model: str, messages: list, step: str, max_retries: int = 5) -> tuple[str, int, int]:
    delay = 2.0
    last_err = None
    for _ in range(max_retries):
        try:
            content, t_in, t_out = _do_request(model, messages)
            if not content.strip():
                raise ValueError("empty completion")
            return content, t_in, t_out
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError, ValueError) as e:
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", 0) or 0
            if status and status not in (429,) and status < 500:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)
    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_err}")


def call_llm(
    messages: list[dict],
    tier: str = "cheap",
    step: str = "unknown",
    response_schema: "type[BaseModel] | None" = None,
    max_retries: int = 5,
) -> "str | BaseModel":
    """
    Call LLM with caching, throttling and retries.
    Returns parsed pydantic model if response_schema provided, else raw string.
    JSON mode = prompt instruction + fence stripping + pydantic validation,
    one retry with the validation error fed back. Never uses response_format.
    """
    model = CHEAP_MODEL if tier == "cheap" else STRONG_MODEL
    schema = response_schema.model_json_schema() if response_schema else None
    wire_messages = _with_json_rule(messages, schema) if schema else list(messages)
    cache_key = _cache_key(model, wire_messages)

    cached_resp = _cache_get(cache_key)
    if cached_resp is not None:
        _ledger(step, model, 0, 0, cached=True)
        if response_schema:
            try:
                return response_schema.model_validate_json(cached_resp)
            except (ValidationError, Exception):
                pass  # stale/bad cache entry -> live call
        else:
            return cached_resp

    content, t_in, t_out = _transport_call(model, wire_messages, step, max_retries)
    _cache_set(cache_key, model, content)
    _ledger(step, model, t_in, t_out, cached=False)

    if response_schema is None:
        return content

    # one validation retry with the error fed back
    for attempt in range(2):
        try:
            obj = _extract_json(content)
            return response_schema.model_validate(obj)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            if attempt == 0:
                retry_msgs = wire_messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content":
                        f"Your reply failed validation: {e}. Return ONLY the corrected JSON object, nothing else."},
                ]
                content, t_in2, t_out2 = _transport_call(model, retry_msgs, step, max_retries)
                _cache_set(_cache_key(model, retry_msgs), model, content)
                _ledger(step, model, t_in2, t_out2, cached=False)
    raise RuntimeError(f"Schema validation failed after retry: {e}")
