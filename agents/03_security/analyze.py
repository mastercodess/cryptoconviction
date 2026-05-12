"""Agent 3 RLM analyzer."""
from __future__ import annotations

import argparse, json, pathlib, sqlite3, sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.freshness import stamp_data_as_of                      # noqa: E402
from shared.rlm import run_rlm                                     # noqa: E402
from shared.schemas import SecurityOutput                          # noqa: E402

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "security.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
REPORTS_DIR = _REPO_ROOT / "reports"

_TASK = """\
Score security posture for the given token. Tables in security_db: audit,
exploit_history, code_health, dependency.

Strategy:
  1. List exploits — any 'major' or 'catastrophic' = automatic security_tier
     ≤ 2 unless ≥3 years have passed without recurrence.
  2. Count audits and severity_high counts. Multiple top auditors with low
     high-severity = strong audit_coverage_score.
  3. code_health.upgrade_mechanism: 'immutable' is best, 'multisig_only'
     without timelock is concerning.
  4. Build single_points_of_failure list from dependency table (oracle,
     bridge, sequencer) where risk_level = 'high'.
  5. For long audit findings, sub_lm() the audit summaries — don't read
     them in your context.

security_tier scale:
  5 = battle-tested, no major incidents, immutable or timelocked, multiple
      top-tier audits, mature bug bounty.
  4 = solid, minor incidents only or fully recovered.
  3 = moderate risk, some concerns but well-known.
  2 = real concerns: recent exploit, weak upgrade controls, or single
      audit only.
  1 = high risk, severe historical exploit or known critical issues.

For tokens that are L1 chains (XMR, SUI), score the chain's security
record and validator decentralization, not contract audits.
"""

_SCHEMA_DOCS = {
    "token_symbol": "string",
    "security_tier": "int 1-5 (5 = best)",
    "audit_coverage_score": "int 1-10",
    "single_points_of_failure": "list[str]",
    "centralization_risks": "list[str]",
    "incident_history_severity": '"NONE"|"MINOR"|"MODERATE"|"MAJOR"|"CATASTROPHIC"',
    "upgrade_mechanism": "string",
    "rationale": "≤800 chars",
    "composite_score": "int 0-100",
}


def _fallback_output(symbol: str, conn: sqlite3.Connection, why: str) -> dict[str, Any]:
    """Schema-valid degraded output. Pulls real DB facts where available."""
    n_audits = conn.execute(
        "SELECT COUNT(*) FROM audit WHERE token_symbol=?", (symbol,)
    ).fetchone()[0]
    sev_high = conn.execute(
        "SELECT COALESCE(SUM(severity_high),0) FROM audit WHERE token_symbol=?", (symbol,)
    ).fetchone()[0] or 0
    exploits = conn.execute(
        "SELECT severity FROM exploit_history WHERE token_symbol=?", (symbol,)
    ).fetchall()
    sev_rank = {"info": 0, "low": 1, "minor": 1, "moderate": 2, "high": 3, "major": 3, "catastrophic": 4}
    worst = max((sev_rank.get((r["severity"] or "").lower(), 0) for r in exploits), default=0)
    incident_severity = ["NONE", "MINOR", "MODERATE", "MAJOR", "CATASTROPHIC"][worst]

    code_health = conn.execute(
        "SELECT upgrade_mechanism FROM code_health WHERE token_symbol=?", (symbol,)
    ).fetchone()
    upgrade = (code_health["upgrade_mechanism"] if code_health else None) or "UNKNOWN"

    deps = conn.execute(
        "SELECT provider FROM dependency WHERE token_symbol=? AND risk_level='high'",
        (symbol,),
    ).fetchall()
    spofs = [r["provider"] for r in deps if r["provider"]]

    # Tier heuristic from real data.
    # Carveout: immutable L1s (BTC, LTC, XMR-style) without audits and
    # without major exploits get tier=5. Otherwise the audit-count heuristic
    # would mis-tier them as 2 ("no audits = risky") and trigger the
    # orchestrator's reject_if_security_below=5 AUTO-REJECT for the wrong
    # frame — audit isn't the right lens for monolithic L1s; the design
    # being immutable + the historical track record IS the security signal.
    if upgrade == "immutable" and worst < 3 and n_audits == 0:
        tier = 5
    elif worst >= 3:
        tier = 2
    elif worst == 2:
        tier = 3
    elif n_audits >= 3 and sev_high == 0:
        tier = 4
    elif n_audits >= 1:
        tier = 3
    else:
        tier = 2
    coverage = min(10, max(1, 4 + n_audits - sev_high))
    composite = max(20, min(80, tier * 18 - worst * 8))

    return {
        "token_symbol": symbol,
        "security_tier": tier,
        "audit_coverage_score": coverage,
        "single_points_of_failure": spofs,
        "centralization_risks": [],
        "incident_history_severity": incident_severity,
        "upgrade_mechanism": upgrade,
        "rationale": (
            (
                "Immutable-design L1 — battle-tested by track record, "
                "not by audit (audit isn't the right frame). "
                if (upgrade == "immutable" and worst < 3 and n_audits == 0)
                else ""
            )
            + f"RLM did not converge ({why}); fallback applied. "
            f"DB shows {n_audits} audits ({sev_high} unresolved high), "
            f"{len(exploits)} exploit(s) (worst severity={incident_severity}), "
            f"upgrade_mechanism={upgrade}, "
            f"{len(spofs)} high-risk dependencies. "
            "Tier derived from heuristic; rerun analyze for narrative judgement."
        ),
        "composite_score": composite,
    }


def analyze(symbol: str, *, max_iters: int = 12, verbose: bool = False) -> dict[str, Any]:
    symbol = symbol.upper()
    tokens.get(symbol)
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Run collect first — {DB_PATH} missing.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sidecar_path = SIDECAR_DIR / symbol
    env = {
        "token_symbol": symbol,
        "security_db": conn,
        "sidecar_dir": str(sidecar_path),
        "sidecar_files": [p.name for p in sidecar_path.glob("*")] if sidecar_path.exists() else [],
    }
    raw = run_rlm(agent_name=f"03_security::{symbol}", environment=env,
                  task=_TASK, output_schema=_SCHEMA_DOCS, max_iters=max_iters, verbose=verbose)
    if raw.get("error") == "max_iters_reached":
        raw = _fallback_output(symbol, conn, f"max_iters={raw.get('iters')}")
    stamp_data_as_of(raw, conn, table="audit", ts_col="audit_date", symbol=symbol)
    out_dir = REPORTS_DIR / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent_03_security.json"
    err_path = out_dir / "agent_03_security.error.json"
    is_fallback = "RLM did not converge" in str(raw.get("rationale", ""))
    if is_fallback:
        err_path.write_text(json.dumps({
            "reason": "max_iters_reached",
            "fallback_used": True,
        }, indent=2))
    try:
        validated = SecurityOutput(**{**raw, "token_symbol": symbol})
        out_path.write_text(json.dumps(validated.model_dump(), indent=2))
        return {"ok": True, "path": str(out_path)}
    except Exception as e:                                  # noqa: BLE001
        payload = {"error": str(e), "raw": raw}
        if is_fallback:
            payload["reason"] = "max_iters_reached"
            payload["fallback_used"] = True
        err_path.write_text(json.dumps(payload, indent=2, default=str))
        return {"ok": False, "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbol", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    syms = tokens.all_symbols() if args.all else [args.symbol.upper()] if args.symbol else None
    if not syms: p.error("provide a symbol or --all")
    for s in syms:
        print(f"\n=== Agent 3 :: {s} ===")
        print(json.dumps(analyze(s, verbose=args.verbose), indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
