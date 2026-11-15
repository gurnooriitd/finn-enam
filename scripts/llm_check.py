"""Reliability check for the LLM gateway: 3 tiny calls, latency per call.
1. JSON extraction (pydantic-validated)
2. 2-sentence summary
3. repeat of call 1 -> proves cache hit
"""
import sys
import time
from pydantic import BaseModel

sys.path.insert(0, ".")
from backend.llm.gateway import call_llm  # noqa: E402

TEXT = (
    "Suzlon Energy announced that its board approved a capex plan of Rs 1,200 crore "
    "to expand wind turbine manufacturing capacity by 1.5 GW, effective 1 April 2027. "
    "Ramesh Kulkarni was appointed Chief Financial Officer with effect from 15 January 2027."
)


class Extract(BaseModel):
    capex_crore: float
    capacity_gw: float
    person: str
    role: str


def main():
    ok = True

    t0 = time.time()
    try:
        ex = call_llm(
            [{"role": "user", "content": f"From the text, extract the fields.\n\nText: {TEXT}"}],
            tier="cheap", step="llm_check.extract", response_schema=Extract,
        )
        print(f"1. extract     PASS  {time.time()-t0:6.1f}s  capex={ex.capex_crore} cr, gw={ex.capacity_gw}, {ex.person}/{ex.role}")
    except Exception as e:
        ok = False
        print(f"1. extract     FAIL  {time.time()-t0:6.1f}s  {type(e).__name__}: {str(e)[:120]}")

    t0 = time.time()
    try:
        s = call_llm(
            [{"role": "user", "content": f"Summarise in exactly 2 sentences.\n\nText: {TEXT}"}],
            tier="cheap", step="llm_check.summary",
        )
        n_sent = s.count(".") + s.count("!") + s.count("?")
        print(f"2. summary     PASS  {time.time()-t0:6.1f}s  sentences~{n_sent}: {s[:80]}...")
    except Exception as e:
        ok = False
        print(f"2. summary     FAIL  {time.time()-t0:6.1f}s  {type(e).__name__}: {str(e)[:120]}")

    t0 = time.time()
    try:
        ex2 = call_llm(
            [{"role": "user", "content": f"From the text, extract the fields.\n\nText: {TEXT}"}],
            tier="cheap", step="llm_check.extract", response_schema=Extract,
        )
        dt = time.time() - t0
        hit = "cache" if dt < 1.0 else "NO-CACHE?"
        print(f"3. repeat      PASS  {dt:6.2f}s  ({hit}) capex={ex2.capex_crore}")
    except Exception as e:
        ok = False
        print(f"3. repeat      FAIL  {time.time()-t0:6.1f}s  {type(e).__name__}: {str(e)[:120]}")

    print("OVERALL:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
