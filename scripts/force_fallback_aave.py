"""
For each agent that has a max-iters error file (or no report) for AAVE,
generate a fallback report directly from the agent's DB. NO API CALLS.

This rebuilds the missing reports/AAVE/agent_0X_*.json files using each
agent's _fallback_output, lets us re-run the orchestrator to get a
complete 7-agent conviction.

    python3 scripts/force_fallback_aave.py
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

SYMBOL = "AAVE"
REPORTS_DIR = _REPO_ROOT / "reports" / SYMBOL

AGENTS = [
    # (agent_module, db_path_attr, schema_name, report_filename)
    ("agents.01_tokenomics.analyze", "TokenomicsOutput", "agent_01_tokenomics.json"),
    ("agents.02_revenue.analyze",    "RevenueOutput",    "agent_02_revenue.json"),
    ("agents.03_security.analyze",   "SecurityOutput",   "agent_03_security.json"),
    ("agents.04_onchain.analyze",    "OnChainOutput",    "agent_04_onchain.json"),
    ("agents.05_team.analyze",       "TeamOutput",       "agent_05_team.json"),
    ("agents.06_moat.analyze",       "MoatOutput",       "agent_06_moat.json"),
    ("agents.07_macro.analyze",      "MacroOutput",      "agent_07_macro.json"),
]


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    schemas = importlib.import_module("shared.schemas")
    n_written = 0
    for mod_name, schema_cls, fname in AGENTS:
        report = REPORTS_DIR / fname
        err = REPORTS_DIR / fname.replace(".json", ".error.json")
        if report.exists():
            print(f"  SKIP  {fname}  (exists)")
            continue
        mod = importlib.import_module(mod_name)
        if not hasattr(mod, "_fallback_output"):
            print(f"  SKIP  {fname}  (no fallback in {mod_name})")
            continue
        if not mod.DB_PATH.exists():
            print(f"  SKIP  {fname}  (DB missing — run collect first)")
            continue
        conn = sqlite3.connect(mod.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            fb = mod._fallback_output(SYMBOL, conn, why="forced_offline_fallback")
            cls = getattr(schemas, schema_cls)
            validated = cls(**{**fb, "token_symbol": SYMBOL})
            report.write_text(json.dumps(validated.model_dump(), indent=2))
            # Move the error file aside so it's clear it's been superseded.
            if err.exists():
                err.rename(err.with_suffix(".json.superseded"))
            print(f"  WROTE {fname}  composite={fb.get('composite_score')}")
            n_written += 1
        except Exception as e:
            print(f"  FAIL  {fname}  {type(e).__name__}: {e}")
    print(f"\nWrote {n_written} fallback report(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
