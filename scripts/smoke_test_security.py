"""
Smoke test for Agent 3 — confirms the Security RLM pipeline runs without
hitting the Anthropic API. Mirror of scripts/smoke_test.py for Agent 1.

What it does:
  1. Confirms data is loaded in security.db.
  2. Builds the Agent 3 environment for AAVE.
  3. Patches the root model with a hand-crafted SQL-driven trajectory.
  4. Runs run_rlm and verifies FINAL passes SecurityOutput validation.

Run:
    python scripts/smoke_test_security.py
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from shared import rlm                                          # noqa: E402
from shared.schemas import SecurityOutput                       # noqa: E402

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "agents/03_security/data/security.db"
SIDECAR_DIR = pathlib.Path(__file__).resolve().parent.parent / "agents/03_security/data/sidecars"


def main() -> int:
    print("=== Agent 3 (Security) smoke test ===\n")

    assert DB_PATH.exists(), f"Run ingest_seed_data first — {DB_PATH} missing"
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    n_audit = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    n_exp = conn.execute("SELECT COUNT(*) FROM exploit_history").fetchone()[0]
    n_dep = conn.execute("SELECT COUNT(*) FROM dependency").fetchone()[0]
    n_ch = conn.execute("SELECT COUNT(*) FROM code_health").fetchone()[0]
    print(f"✓ Data: {n_audit} audits, {n_exp} exploits, {n_dep} deps, {n_ch} code_health")
    assert n_audit > 30 and n_ch == 10

    env = {
        "token_symbol": "AAVE",
        "security_db": conn,
        "db_path": str(DB_PATH),
        "sidecar_dir": str(SIDECAR_DIR / "AAVE"),
        "sidecar_files": [p.name for p in (SIDECAR_DIR / "AAVE").glob("*.json")],
    }
    print(f"✓ Environment: {list(env.keys())}, sidecars={env['sidecar_files']}")

    # Hand-crafted root trajectory — runs real SQL against the loaded DB.
    fake_root_response = '''```python
# 1. How many audits and what's the highest-severity audit finding?
audits = security_db.execute("""
    SELECT COUNT(*), COALESCE(SUM(severity_high),0), COALESCE(SUM(severity_med),0)
    FROM audit WHERE token_symbol='AAVE'
""").fetchone()
print("audits:", tuple(audits))

# 2. Worst exploit
worst = security_db.execute("""
    SELECT incident_date, severity, funds_lost_usd, description
    FROM exploit_history WHERE token_symbol='AAVE'
    ORDER BY CASE severity WHEN 'catastrophic' THEN 4 WHEN 'major' THEN 3
                          WHEN 'moderate' THEN 2 ELSE 1 END DESC LIMIT 1
""").fetchone()
print("worst exploit:", tuple(worst) if worst else None)

# 3. Code health — use column NAMES, not positional indices (schema has bus_factor between)
ch = security_db.execute(
    "SELECT upgrade_mechanism, multisig_threshold, multisig_signers, bug_bounty_max_usd "
    "FROM code_health WHERE token_symbol='AAVE'"
).fetchone()
print("upgrade:", ch[0], "multisig:", f"{ch[1]}/{ch[2]}", "bounty:", ch[3])

# 4. Dependencies marked high risk
hi_deps = security_db.execute("""
    SELECT dep_type, provider FROM dependency
    WHERE token_symbol='AAVE' AND risk_level='high'
""").fetchall()
print("high-risk deps:", [(d[0],d[1]) for d in hi_deps])

# 5. Decision
FINAL = {
    "token_symbol": "AAVE",
    "security_tier": 5 if (worst is None or worst[1] != "catastrophic") else 1,
    "audit_coverage_score": 9,
    "single_points_of_failure": ["Chainlink oracle (high risk per dep table)"],
    "centralization_risks": ["5-of-9 governance multisig", "proxy_timelock with finite delay"],
    "incident_history_severity": (worst[1].upper() if worst else "NONE"),
    "upgrade_mechanism": ch[0] or "proxy_timelock",
    "rationale": f"AAVE: {audits[0]} audits with {audits[1]} high findings (resolved). Worst exploit was the Nov-2022 CRV bad-debt event ($1.6M, covered by Safety Module). 5-of-9 governance multisig + proxy_timelock. $1M Immunefi bounty. Battle-tested across multiple V3 deploys.",
    "composite_score": 82,
}
print("DONE")
```'''

    with mock.patch("shared.rlm._root_turn", return_value=fake_root_response):
        result = rlm.run_rlm(
            agent_name="03_security::AAVE",
            environment=env,
            task="Smoke test trajectory.",
            output_schema={"composite_score": "int"},
            max_iters=3,
        )
    print(f"✓ RLM loop ran. FINAL keys: {list(result.keys())}")

    validated = SecurityOutput(**{**result, "token_symbol": "AAVE"})
    print(f"✓ Schema validation passed: tier={validated.security_tier} "
          f"audit_cov={validated.audit_coverage_score} "
          f"composite={validated.composite_score}")
    print(f"  incident_history: {validated.incident_history_severity}")
    print(f"  upgrade: {validated.upgrade_mechanism}")
    print(f"  SPOFs: {validated.single_points_of_failure}")

    print("\n=== AGENT 3 SMOKE TEST PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
