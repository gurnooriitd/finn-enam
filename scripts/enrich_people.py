"""Enrich management_change filings: LLM facts + ddgs-sourced bios, cached in
people_enrichment. Idempotent: skips announcement ids already in the table
(use --force to re-run). Local only — needs LLM_ENABLED=1 and network."""
import argparse
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend.push.people_enrich import run  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    rows = run(days=args.days, force=args.force)
    print(f"\n{len(rows)} management-change items processed")
    for r in rows:
        print(f"{r['symbol']:<11} {str(r['person'])[:34]:<34} {str(r['action']):<12} "
              f"conf={str(r['confidence']):<6} sources={r['n_sources']}{'  (cached)' if r['cached'] else ''}")
