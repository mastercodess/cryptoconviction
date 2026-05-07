"""
Smoke test for Agent 2 — confirms the Revenue RLM pipeline runs without
hitting the Anthropic API. Mirrors smoke_test.py + smoke_test_security.py.

Run:
    python scripts/smoke_test_revenue.py
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from shared import rlm                                          # noqa: E402
from shared.schemas import RevenueOutput                        # noqa: E402

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "agents/02_revenue/data/revenue.db"
SIDECAR_DIR = pathlib.Path(__file__).resolve().parent.parent / "agents/02_revenue/data/sidecars"


def main() -> int:
    print("=== Agent 2 (Revenue) smoke test ===\n")

    assert DB_PATH.exists(), f"Run ingest_seed_data first — {DB_PATH} missing"
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    n_snap = conn.execute("SELECT COUNT(*) FROM revenue_snapshot").fetchone()[0]
    n_peer = conn.execute("SELECT COUNT(*) FROM peer_comparison").fetchone()[0]
    n_with_rev = conn.execute(
        "SELECT COUNT(*) FROM revenue_snapshot WHERE annualized_revenue_usd IS NOT NULL"
    ).fetchone()[0]
    print(f"✓ Data: {n_snap} snapshots, {n_peer} peers, {n_with_rev} with revenue")
    assert n_snap == 10
    assert n_with_rev >= 5

    # Use OGN — it's the most interesting case (1.2x P/S, 40% real yield from buybacks)
    env = {
        "token_symbol": "OGN",
        "revenue_db": conn,
        "db_path": str(DB_PATH),
        "sidecar_dir": str(SIDECAR_DIR / "OGN"),
        "sidecar_files": [p.name for p in (SIDECAR_DIR / "OGN").glob("*.json")],
    }
    print(f"✓ Environment: keys={list(env.keys())} sidecars={env['sidecar_files']}")

    # Hand-crafted root trajectory using real SQL.
    fake_root_response = '''```python
# 1. Pull the latest snapshot
r = revenue_db.execute("""
    SELECT annualized_revenue_usd, tvl_usd, p_s_ratio, p_tvl_ratio,
           real_yield_apr, inflationary_yield_apr, seasonality_note
    FROM revenue_snapshot WHERE token_symbol='OGN'
""").fetchone()
print("OGN snapshot:", dict(r))

# 2. Peer multiples
peers = revenue_db.execute("""
    SELECT peer_symbol, metric, peer_value FROM peer_comparison
    WHERE token_symbol='OGN' AND metric IN ('p_s','p_s_ratio')
""").fetchall()
print("peer P/S:", [dict(p) for p in peers])

# 3. Real vs inflationary yield decomposition — KEY signal
real_y = r["real_yield_apr"] or 0
infl_y = r["inflationary_yield_apr"] or 0
yield_quality = "real-yield only" if infl_y == 0 and real_y > 0 else "mixed"
print("yield quality:", yield_quality)

# 4. Decision
FINAL = {
    "token_symbol": "OGN",
    "revenue_quality_score": 9,
    "growth_trend": "ACCELERATING",
    "valuation_vs_peers": "STRONG",
    "real_yield_apr": real_y,
    "inflationary_yield_apr": infl_y,
    "annualized_revenue_usd": r["annualized_revenue_usd"],
    "p_s_ratio": r["p_s_ratio"],
    "rationale": f"OGN: $13.2M annualized revenue (P/S {r['p_s_ratio']}x — extremely cheap vs DeFi peers), 100% buyback-funded real yield to xOGN at ~40% APR with zero token emissions. Revenue from OETH/OUSD/super-OETH yield aggregation flows entirely to OGN holders via market buybacks. Valuation looks structurally undervalued relative to revenue capture model.",
    "composite_score": 78,
}
print("DONE")
```'''

    with mock.patch("shared.rlm._root_turn", return_value=fake_root_response):
        result = rlm.run_rlm(
            agent_name="02_revenue::OGN",
            environment=env,
            task="Smoke test trajectory.",
            output_schema={"composite_score": "int"},
            max_iters=3,
        )
    print(f"✓ RLM loop ran. FINAL keys: {list(result.keys())}")

    validated = RevenueOutput(**{**result, "token_symbol": "OGN"})
    print(f"✓ Schema validation passed: composite={validated.composite_score} "
          f"P/S={validated.p_s_ratio}x  real_yield={validated.real_yield_apr}%  "
          f"infl_yield={validated.inflationary_yield_apr}%")
    print(f"  growth: {validated.growth_trend}  vs_peers: {validated.valuation_vs_peers}")

    print("\n=== AGENT 2 SMOKE TEST PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
