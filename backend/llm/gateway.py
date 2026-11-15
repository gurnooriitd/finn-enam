"""
FINN — LLM Gateway
Single entry point for all LLM calls. Uses Gemini OpenAI-compatible REST API.
- Flash-class models only (CHEAP_MODEL = STRONG_MODEL = gemini-2.5-flash)
- SQLite response cache (by hash of model+messages+schema)
- Exponential backoff on 429/rate-limit
- Cost ledger (est. cost per call)
- Pydantic schema validation with one retry on failure
"""
import hashlib
import json
import sqlite3
import time
import datetime
import requests
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from backend.config import LLM_BASE_URL, LLM_API_KEY, CHEAP_MODEL, STRONG_MODEL, DB_PATH

T = TypeVar("T", bound=BaseModel)


def _cache_key(model: str, messages: list, schema_name: str = "") -> str:
    raw = json.dumps({"model": model, "messages": messages, "schema": schema_name}, sort_keys=True)
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
        # Gemini 2.5 Flash pricing (approx): $0.075/1M input, $0.30/1M output
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


def _do_request(model: str, messages: list, json_mode: bool = False) -> tuple[str, int, int]:
    """Make a single REST call. Returns (content, tokens_in, tokens_out).

    gemini-2.5-flash is a thinking model: send reasoning_effort=none to cut
    latency/tokens. If the endpoint rejects the field (400), retry once
    without it and remember to omit it for the rest of the process.
    """
    global _REASONING_EFFORT_OK
    url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if _REASONING_EFFORT_OK:
        payload["reasoning_effort"] = "none"

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.status_code == 400 and _REASONING_EFFORT_OK and "reasoning_effort" in resp.text:
        _REASONING_EFFORT_OK = False
        payload.pop("reasoning_effort", None)
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"] or {}
    content = msg.get("content") or ""
    # Thinking models may put JSON in the reasoning field if content is empty
    if not content and msg.get("reasoning_content"):
        content = msg["reasoning_content"]
    usage = data.get("usage", {})
    return content, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


_REASONING_EFFORT_OK = True


def call_llm(
    messages: list[dict],
    tier: str = "cheap",
    step: str = "unknown",
    response_schema: Type[T] | None = None,
    max_retries: int = 5,
) -> "str | T":
    """
    Call LLM with caching and retry.
    Returns parsed pydantic model if response_schema provided, else raw string.
    tier: 'cheap' | 'strong' — both use Flash-class (gemini-2.5-flash).
    """
    model = CHEAP_MODEL if tier == "cheap" else STRONG_MODEL
    schema_name = response_schema.__name__ if response_schema else ""
    cache_key = _cache_key(model, messages, schema_name)

    cached_resp = _cache_get(cache_key)
    if cached_resp is not None:
        _ledger(step, model, 0, 0, cached=True)
        if response_schema:
            try:
                return response_schema.model_validate_json(cached_resp)
            except (ValidationError, Exception):
                pass  # fall through to live call
        return cached_resp

    delay = 2.0
    last_err = None
    current_messages = list(messages)

    for attempt in range(max_retries):
        try:
            content, tokens_in, tokens_out = _do_request(
                model, current_messages, json_mode=(response_schema is not None)
            )
            _cache_set(cache_key, model, content)
            _ledger(step, model, tokens_in, tokens_out, cached=False)

            if response_schema:
                try:
                    return response_schema.model_validate_json(content)
                except (ValidationError, json.JSONDecodeError) as e:
                    if attempt < max_retries - 1:
                        current_messages = current_messages + [
                            {"role": "assistant", "content": content},
                            {"role": "user", "content": f"Validation failed: {e}. Return valid JSON only matching the schema."},
                        ]
                        time.sleep(delay)
                        delay = min(delay * 2, 60)
                        continue
                    raise RuntimeError(f"Schema validation failed: {e}")
            return content

        except requests.HTTPError as e:
            last_err = e
            status = e.response.status_code if e.response is not None else 0
            if status == 429 or status >= 500:
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str or "quota" in err_str or "timeout" in err_str:
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise

    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_err}")
