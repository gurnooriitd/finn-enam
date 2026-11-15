"""
Spike: LLM Gateway skeleton.
Tests one cheap-tier LLM call (Gemini Flash via AI Studio / Google) with:
  - Pydantic schema validation
  - Cost ledger entry
  - Caching by input hash
Reads API key from .env (GEMINI_API_KEY or GOOGLE_API_KEY).
"""
import json
import hashlib
import os
import sys
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, ValidationError

# Try to load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)

SPIKES_DIR = Path(__file__).parent.parent / "data" / "spikes"
SPIKES_DIR.mkdir(parents=True, exist_ok=True)

# --- Schema for test ---
class AnnouncementCategory(BaseModel):
    category: str
    confidence: float
    reasoning: str


# --- Simple gateway skeleton ---
class LLMGateway:
    """Minimal gateway: routes, validates, caches, ledgers."""

    CHEAP_MODEL = os.getenv("CHEAP_MODEL", "gemini/gemini-1.5-flash")
    STRONG_MODEL = os.getenv("STRONG_MODEL", "gemini/gemini-1.5-pro")

    VALID_CATEGORIES = [
        "results", "M&A", "fund_raise", "management_change", "litigation",
        "insider_trading", "earnings_call", "investor_meetings", "credit_rating",
        "dividend", "bonus_split", "capex", "press_release", "postal_ballot",
        "agm_egm", "pledging", "unclassified"
    ]

    def __init__(self):
        self.cache = {}  # In-memory for spike; production uses SQLite
        self.ledger = []

    def _cache_key(self, model: str, prompt: str, input_text: str) -> str:
        h = hashlib.sha256(f"{model}|{prompt}|{input_text}".encode()).hexdigest()
        return h[:16]

    def call(self, prompt: str, input_text: str, schema: type, model: str = None, tier: str = "cheap") -> tuple:
        """Call LLM, validate with pydantic, log to ledger."""
        model = model or (self.CHEAP_MODEL if tier == "cheap" else self.STRONG_MODEL)
        cache_key = self._cache_key(model, prompt, input_text)

        if cache_key in self.cache:
            print(f"  [cache HIT] {cache_key}")
            self.ledger.append({"step": "classify", "model": model, "cached": True, "tokens_in": 0, "tokens_out": 0, "est_cost": 0})
            return self.cache[cache_key], True

        # Build full prompt
        full_prompt = f"{prompt}\n\nInput:\n{input_text}\n\nRespond with valid JSON only matching this schema: {schema.model_json_schema()}"

        # Try Google AI (Gemini) via REST
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None, False, "No GEMINI_API_KEY in env"

        try:
            import requests
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}
            }
            r = requests.post(endpoint, json=payload, timeout=30)
            r.raise_for_status()
            resp = r.json()

            # Extract text
            raw_text = resp["candidates"][0]["content"]["parts"][0]["text"]
            tokens_in = resp.get("usageMetadata", {}).get("promptTokenCount", len(full_prompt.split()))
            tokens_out = resp.get("usageMetadata", {}).get("candidatesTokenCount", len(raw_text.split()))

            # Parse JSON
            parsed_json = json.loads(raw_text)
            validated = schema(**parsed_json)

            self.cache[cache_key] = validated
            est_cost = (tokens_in * 0.075 + tokens_out * 0.30) / 1_000_000  # Flash pricing
            self.ledger.append({
                "step": "classify",
                "model": model,
                "cached": False,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "est_cost_usd": round(est_cost, 8),
            })
            return validated, False, None

        except ValidationError as e:
            return None, False, f"Pydantic validation failed: {e}"
        except Exception as e:
            return None, False, f"LLM call failed: {e}"


# --- Run test ---
print("=" * 60)
print("PROBE: LLM Gateway (Gemini Flash)")
print("=" * 60)

gateway = LLMGateway()

test_input = "Board of Directors - Appointment of Shri Rajesh Kumar as Managing Director & CEO of the Company w.e.f. 01-Jan-2025"
prompt = (
    "Classify this Indian stock exchange announcement into exactly one of these categories: "
    "results, M&A, fund_raise, management_change, litigation, insider_trading, earnings_call, "
    "investor_meetings, credit_rating, dividend, bonus_split, capex, press_release, postal_ballot, "
    "agm_egm, pledging, unclassified. "
    "Return JSON with fields: category (string), confidence (0.0-1.0), reasoning (string <=30 words)."
)

print(f"\nTest input: '{test_input}'")
result, cached, err = gateway.call(prompt, test_input, AnnouncementCategory, tier="cheap")

if result:
    print(f"\n✓ LLM Response:")
    print(f"  category    = {result.category}")
    print(f"  confidence  = {result.confidence}")
    print(f"  reasoning   = {result.reasoning}")
    print(f"  cached      = {cached}")
    print(f"\nLedger entry: {gateway.ledger[-1]}")

    # Test cache hit
    print("\n--- Testing cache hit ---")
    result2, cached2, err2 = gateway.call(prompt, test_input, AnnouncementCategory, tier="cheap")
    print(f"  Second call cached: {cached2}")

    # Save results
    output = {
        "status": "OK",
        "model": gateway.CHEAP_MODEL,
        "result": result.model_dump(),
        "ledger": gateway.ledger,
    }
    (SPIKES_DIR / "spike_llm_results.json").write_text(json.dumps(output, indent=2))
    print(f"\nSaved: data/spikes/spike_llm_results.json")
else:
    print(f"\n✗ LLM call failed: {err}")
    output = {"status": "FAIL", "error": err}
    (SPIKES_DIR / "spike_llm_results.json").write_text(json.dumps(output, indent=2))

    # Check if it's a key issue
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("\n  → Set GEMINI_API_KEY in finn/.env to enable LLM calls")
        print("  → Get free key at: https://aistudio.google.com/app/apikey")
    else:
        print(f"\n  → Key found ({api_key[:8]}...), but API call failed")

print("\n" + "=" * 60)
