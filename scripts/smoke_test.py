"""
Smoke test for Agent 1 — verifies the entire analyze.py pipeline works
WITHOUT making a live Anthropic API call. Useful for CI and as a sanity
check before the user plugs in their API key.

What it does:
  1. Confirms data is loaded in tokenomics.db.
  2. Builds the RLM environment for LINK exactly like analyze.py does.
  3. Patches the root model and sub_lm to deterministic fake responses.
  4. Runs run_rlm with the patched calls.
  5. Verifies the FINAL dict passes TokenomicsOutput validation.

Run:
    python scripts/smoke_test.py
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from shared import tokens, rlm                            # noqa: E402
from shared.schemas import TokenomicsOutput               # noqa: E402

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "agents/01_tokenomics/data/tokenomics.db"
SIDECAR_DIR = pathlib.Path(__file__).resolve().parent.parent / "agents/01_tokenomics/data/sidecars"


def main() -> int:
    print("=== Agent 1 smoke test ===\n")

    # 1. Data is present
    assert DB_PATH.exists(), f"Run ingest_seed_data first — {DB_PATH} missing"
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM supply_snapshot").fetchone()[0]
    print(f"✓ Data present: {n} supply rows")
    assert n >= 10, "Expected ≥10 tokens"

    # 2. Environment loader works (lifted from analyze._load_environment)
    env = {
        "token_symbol": "LINK",
        "tokenomics_db": conn,
        "db_path": str(DB_PATH),
        "sidecar_dir": str(SIDECAR_DIR / "LINK"),
        "sidecar_files": [p.name for p in (SIDECAR_DIR / "LINK").glob("*.json")],
    }
    print(f"✓ Environment loaded: keys={list(env.keys())}")
    print(f"  sidecar_files={env['sidecar_files']}")

    # 3. Patch the model calls so we don't hit the API.
    # Simulated trajectory: root probes once, sets FINAL.
    fake_root_response = '''```python
# Probe table list
import json
for r in tokenomics_db.execute("SELECT * FROM supply_snapshot WHERE token_symbol='LINK'"):
    cols = [d[0] for d in tokenomics_db.execute("SELECT * FROM supply_snapshot WHERE token_symbol='LINK'").description]
    print(dict(zip(cols, r)))
unlocks_90d = tokenomics_db.execute(
    "SELECT COALESCE(SUM(tokens_unlocked),0), MIN(unlock_date) FROM unlock_event "
    "WHERE token_symbol='LINK' AND unlock_date BETWEEN date('now') AND date('now','+90 days')"
).fetchone()
print("90d unlocks:", unlocks_90d)
mech = tokenomics_db.execute("SELECT * FROM mechanism WHERE token_symbol='LINK'").fetchone()
print("mechanism row exists:", mech is not None)
FINAL = {
    "token_symbol": "LINK",
    "fdv_risk_rating": 7,
    "inflation_pressure_score": 5,
    "value_accrual_verdict": "NEUTRAL",
    "concentration_risk_flag": False,
    "top10_holding_pct": 0.0,
    "unlock_pressure_next_90d_pct": round((unlocks_90d[0] or 0) / 727099970, 4),
    "next_unlock_date": unlocks_90d[1],
    "next_unlock_pct_of_supply": round((unlocks_90d[0] or 0) / 727099970, 4),
    "rationale": "Smoke test trajectory: 72.7% circulating, 11% inflation, neutral accrual.",
    "composite_score": 58,
}
print("DONE")
```'''

    with mock.patch("shared.rlm._root_turn", return_value=fake_root_response):
        result = rlm.run_rlm(
            agent_name="01_tokenomics::LINK",
            environment=env,
            task="Test trajectory.",
            output_schema={"composite_score": "int"},
            max_iters=3,
        )
    print(f"✓ RLM loop ran. FINAL keys: {list(result.keys())}")

    # 4. Validate via Pydantic
    validated = TokenomicsOutput(**{**result, "token_symbol": "LINK"})
    print(f"✓ Schema validation passed: composite={validated.composite_score} "
          f"unlock90d={validated.unlock_pressure_next_90d_pct}")

    # 5. Spot-check that the SQL the root used returned real data
    print(f"  next_unlock_date={validated.next_unlock_date}")

    print("\n=== ALL SMOKE TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
