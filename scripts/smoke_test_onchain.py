"""Smoke test for Agent 4 — On-Chain Intelligence."""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from shared import rlm                                          # noqa: E402
from shared.schemas import OnChainOutput                        # noqa: E402

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "agents/04_onchain/data/onchain.db"
SIDECAR_DIR = pathlib.Path(__file__).resolve().parent.parent / "agents/04_onchain/data/sidecars"


def main() -> int:
    print("=== Agent 4 (On-Chain) smoke test ===\n")
    assert DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    n_act = conn.execute("SELECT COUNT(*) FROM activity_metric").fetchone()[0]
    n_hold = conn.execute("SELECT COUNT(*) FROM holder_cohort").fetchone()[0]
    n_notes = conn.execute("SELECT COUNT(*) FROM onchain_research_note").fetchone()[0]
    print(f"✓ Data: {n_act} activity rows, {n_hold} holder cohorts, {n_notes} notes")
    assert n_act >= 9 and n_notes >= 5

    # Use LINK — has the cleanest captured signals (net outflow + ACCUMULATING + LTH 65%)
    env = {
        "token_symbol": "LINK",
        "onchain_db": conn,
        "sidecar_dir": str(SIDECAR_DIR / "LINK"),
        "sidecar_files": [p.name for p in (SIDECAR_DIR / "LINK").glob("*.json")],
    }
    print(f"✓ Environment: keys={list(env.keys())} sidecars={env['sidecar_files']}")

    fake_root = '''```python
# 1. Activity (LINK has no DAU — typical for token contracts vs L1 chains)
a = onchain_db.execute("SELECT * FROM activity_metric WHERE token_symbol='LINK'").fetchone()
print("activity:", dict(a))

# 2. Net exchange flow — outflow = bullish self-custody signal
flow = onchain_db.execute(
    "SELECT inflow_usd, outflow_usd, net_usd FROM exchange_flow WHERE token_symbol='LINK'"
).fetchone()
print("flow:", dict(flow) if flow else None)

# 3. Holder cohort
h = onchain_db.execute(
    "SELECT lth_supply_pct, smart_money_stance FROM holder_cohort WHERE token_symbol='LINK'"
).fetchone()
print("holder:", dict(h))

# 4. Research note (sub_lm could synthesize this if longer)
note = onchain_db.execute(
    "SELECT body FROM onchain_research_note WHERE token_symbol='LINK' AND topic='summary' LIMIT 1"
).fetchone()
print("note (first 200):", (note['body'] if note else '')[:200])

# 5. Decision
net = (flow['net_usd'] if flow else 0) or 0
flow_dir = 'OUTFLOW' if net < 0 else ('INFLOW' if net > 0 else 'FLAT')
FINAL = {
    "token_symbol": "LINK",
    "organic_activity_score": 8,
    "capital_flow_direction": flow_dir,
    "holder_quality_rating": 8,
    "growth_authenticity_verdict": "STRONG",
    "retention_health_grade": "A",
    "smart_money_stance": h['smart_money_stance'],
    "rationale": f"LINK shows clean accumulation: net 30d exchange flow of ${net/1e6:.1f}M (negative = self-custody), LTH supply at {(h['lth_supply_pct'] or 0)*100:.0f}%, smart money labeled {h['smart_money_stance']}. Top 1% concentration at 81% is the main offset. CCIP volume up 319% YoY indicates organic network growth.",
    "composite_score": 75,
}
print("DONE")
```'''

    with mock.patch("shared.rlm._root_turn", return_value=fake_root):
        result = rlm.run_rlm(
            agent_name="04_onchain::LINK",
            environment=env, task="Smoke test.",
            output_schema={"composite_score": "int"},
            max_iters=3,
        )
    print(f"✓ RLM loop ran. FINAL keys: {list(result.keys())}")

    validated = OnChainOutput(**{**result, "token_symbol": "LINK"})
    print(f"✓ Schema validated: composite={validated.composite_score} "
          f"flow={validated.capital_flow_direction}  "
          f"smart={validated.smart_money_stance}  retention={validated.retention_health_grade}")

    print("\n=== AGENT 4 SMOKE TEST PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
