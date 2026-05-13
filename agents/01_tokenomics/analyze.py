"""
Agent 1 analyzer — runs an RLM over the tokenomics database and sidecars to
produce a TokenomicsOutput JSON for one token.

The root LLM (Opus) never sees the raw whitepaper / unlock CSV in its context.
Instead, the RLM scaffold loads:
  - a sqlite3 connection to data/tokenomics.db
  - the token's sidecar dir as a path
  - convenience DataFrames (unlocks, snapshots, mechanism, notes)
into a Python REPL. The root then writes pandas/SQL/regex code to navigate,
and delegates dense reads (e.g. "summarize this whitepaper section in 3
bullets") to sub_lm() — which runs Sonnet.

Output validates against shared.schemas.TokenomicsOutput before being written
to reports/{symbol}/agent_01_tokenomics.json.

Run:
  python -m agents.01_tokenomics.analyze LINK
  python -m agents.01_tokenomics.analyze --all
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.freshness import stamp_data_as_of                      # noqa: E402
from shared.rlm import run_rlm                                     # noqa: E402
from shared.schemas import TokenomicsOutput                        # noqa: E402

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "tokenomics.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
REPORTS_DIR = _REPO_ROOT / "reports"


_TASK = """\
Produce a Token Economics analysis for the given token. Score:
  1. FDV risk (circulating / max — higher = better)
  2. Inflation pressure (annual supply growth)
  3. Value accrual (mechanism real vs cosmetic; emission-funded yield = WEAK)
  4. Concentration risk (top-10 own >50% if known; do NOT guess)
  5. Unlock pressure (sum next 90d as % of supply)

HARD 14-turn budget. Use it efficiently. Set FINAL as soon as you have
enough signal — do NOT keep probing for fields that aren't in the DB.

EMIT-EARLY rules (apply if hit; aim to set FINAL by turn 5):

  • EASY CASE — fully circulating + low inflation:
    If supply_snapshot.circulating == max_supply (or max_supply is null AND
    no unlock_event rows for this token) AND inflation_point.annualized_rate
    < 0.02 (or no inflation_point row): there's effectively zero supply-side
    risk. Emit FINAL with fdv_risk_rating=9, inflation_pressure_score=10,
    concentration_risk_flag=False, unlock_pressure_next_90d_pct=0.0, and
    composite_score in 70-85 depending on value_accrual_verdict from the
    mechanism row.

  • SPARSE DATA — minimal token info:
    If supply_snapshot has no row for this symbol, OR mechanism is null AND
    no research_notes exist: emit FINAL by turn 4 with conservative defaults
    (5/10 across the board, concentration_risk_flag=True, composite_score=40)
    and explain in rationale.

  • UNLOCK SPIKE — concrete numeric trigger:
    If SUM(pct_of_supply) BETWEEN now AND now+90d > 0.10 (>10% supply unlock
    inbound): set fdv_risk_rating ≤ 4, unlock_pressure_next_90d_pct = that
    sum, mention "MAJOR UPCOMING UNLOCK" in rationale, emit FINAL by turn 4.

Strategy:
  - Always start with `tokenomics_db.execute('SELECT * FROM sqlite_master')`
    to see tables. Then peek the row(s) for THIS token only.
  - For value accrual: read `mechanism` row, pass to sub_lm with
    "Real or cosmetic value accrual? Reply STRONG/NEUTRAL/WEAK + 1 sentence."
  - Unlock pressure: SELECT SUM(pct_of_supply) FROM unlock_event WHERE
    token_symbol=? AND unlock_date BETWEEN date('now') AND date('now','+90 days').
  - If a field is missing in DB, use the 'unknown' value (top10_holding_pct=0.0)
    and note it in rationale. Do NOT fabricate.

Composite weighting: ~30% FDV, ~25% inflation, ~25% value accrual,
~10% concentration, ~10% unlock pressure.
"""


_SCHEMA_DOCS = {
    "token_symbol": "string, uppercase",
    "fdv_risk_rating": "int 1-10 (10 = low risk: most supply already circulating)",
    "inflation_pressure_score": "int 1-10 (10 = no inflation, 1 = >25% annual)",
    "value_accrual_verdict": 'one of "STRONG" | "NEUTRAL" | "WEAK"',
    "concentration_risk_flag": "bool — true if any concentration concern",
    "top10_holding_pct": "float 0..1, or 0.0 if unknown (note in rationale)",
    "unlock_pressure_next_90d_pct": "float 0..1 of current circulating",
    "next_unlock_date": "ISO date string or null",
    "next_unlock_pct_of_supply": "float 0..1 or null",
    "rationale": "string, ≤1500 chars, cite specific numbers from the DB",
    "composite_score": "int 0-100, your overall tokenomics conviction",
}


def _fallback_output(symbol: str, conn: sqlite3.Connection, why: str) -> dict[str, Any]:
    """Schema-valid output when the RLM didn't converge. Pulls real numbers
    from the DB where possible; uses neutral defaults otherwise."""
    snap = conn.execute(
        "SELECT market_cap_usd, fdv_usd, circulating, max_supply "
        "FROM supply_snapshot WHERE token_symbol=? "
        "ORDER BY snapshot_at DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    fdv_ratio = None
    if snap:
        circ = snap["circulating"]; mx = snap["max_supply"]
        if circ and mx and mx > 0:
            fdv_ratio = circ / mx
    # 10 = circulating == max_supply (no overhang); 1 = <10% in circulation.
    if fdv_ratio is None:
        fdv_score = 5
    elif fdv_ratio >= 0.95:
        fdv_score = 9
    elif fdv_ratio >= 0.7:
        fdv_score = 7
    elif fdv_ratio >= 0.4:
        fdv_score = 5
    else:
        fdv_score = 3

    infl = conn.execute(
        "SELECT annualized_rate FROM inflation_point WHERE token_symbol=? "
        "ORDER BY as_of_date DESC LIMIT 1", (symbol,),
    ).fetchone()
    if infl and infl["annualized_rate"] is not None:
        r = infl["annualized_rate"]
        infl_score = 10 if r <= 0.005 else 8 if r <= 0.05 else 6 if r <= 0.10 else 4 if r <= 0.25 else 2
    else:
        infl_score = 5

    next_unlock = conn.execute(
        "SELECT unlock_date, pct_of_supply FROM unlock_event "
        "WHERE token_symbol=? AND unlock_date >= date('now') "
        "ORDER BY unlock_date ASC LIMIT 1", (symbol,),
    ).fetchone()
    next_date = next_unlock["unlock_date"] if next_unlock else None
    next_pct = next_unlock["pct_of_supply"] if next_unlock and next_unlock["pct_of_supply"] is not None else None

    pressure_90d = conn.execute(
        "SELECT SUM(COALESCE(pct_of_supply, 0)) FROM unlock_event "
        "WHERE token_symbol=? AND unlock_date BETWEEN date('now') AND date('now','+90 days')",
        (symbol,),
    ).fetchone()[0] or 0.0
    if pressure_90d > 1:
        pressure_90d = min(pressure_90d, 1.0)

    return {
        "token_symbol": symbol,
        "fdv_risk_rating": fdv_score,
        "inflation_pressure_score": infl_score,
        "value_accrual_verdict": "NEUTRAL",
        "concentration_risk_flag": True,        # conservative default
        "top10_holding_pct": 0.0,                # honest "unknown" sentinel — note in rationale
        "unlock_pressure_next_90d_pct": float(pressure_90d),
        "next_unlock_date": next_date,
        "next_unlock_pct_of_supply": float(next_pct) if next_pct is not None else None,
        "rationale": (
            f"RLM did not converge ({why}); fallback applied. "
            f"FDV score {fdv_score}/10 derived from circulating/max-supply ratio "
            f"({fdv_ratio:.2f} if known)." if fdv_ratio is not None else
            f"RLM did not converge ({why}); fallback applied. "
            f"FDV score {fdv_score}/10 (supply data incomplete). "
        ) + (
            f" 90-day unlock pressure: {pressure_90d*100:.1f}% of supply. "
            "Top-10 concentration unknown (free-tier holder data unavailable); "
            "concentration_risk_flag set to True conservatively."
        ),
        "composite_score": int(0.30*fdv_score*10 + 0.25*infl_score*10 + 0.45*40),
    }


def _load_environment(symbol: str) -> dict[str, Any]:
    """Construct the REPL globals for this token. Lazy — DataFrames built on first use."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"{DB_PATH} not found. Run collect.py first."
        )
    # Open a fresh connection — RLM REPL owns it for the run.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sidecar_path = SIDECAR_DIR / symbol
    sidecar_files = sorted(p.name for p in sidecar_path.glob("*.json")) if sidecar_path.exists() else []

    # Pre-built helpers — keep heavy data OUT of root context. The root must
    # write code to query these.
    return {
        "token_symbol": symbol,
        "tokenomics_db": conn,
        "db_path": str(DB_PATH),
        "sidecar_dir": str(sidecar_path),
        "sidecar_files": sidecar_files,
        # Helper the root can read but does NOT have the contents loaded into
        # its context. It must `Path(sidecar_dir, fname).read_text()[...]` to
        # peek.
    }


def analyze(symbol: str, *, max_iters: int = 14, verbose: bool = False) -> dict[str, Any]:
    symbol = symbol.upper()
    tokens.get(symbol)  # raises if unknown

    env = _load_environment(symbol)
    raw = run_rlm(
        agent_name=f"01_tokenomics::{symbol}",
        environment=env,
        task=_TASK,
        output_schema=_SCHEMA_DOCS,
        max_iters=max_iters,
        verbose=verbose,
    )

    # If the RLM stalled, build a conservative DB-backed fallback rather than
    # crashing schema validation downstream.
    if raw.get("error") == "max_iters_reached":
        raw = _fallback_output(symbol, env["tokenomics_db"],
                               f"max_iters={raw.get('iters')}")

    stamp_data_as_of(raw, env["tokenomics_db"], table="supply_snapshot", symbol=symbol)

    # Validate before persisting. If invalid, write the raw + the validation
    # error so the user can debug.
    out_dir = REPORTS_DIR / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent_01_tokenomics.json"
    err_path = out_dir / "agent_01_tokenomics.error.json"
    stale_path = out_dir / "agent_01_tokenomics.stale.json"
    is_fallback = "RLM did not converge" in str(raw.get("rationale", ""))
    if is_fallback:
        err_path.write_text(json.dumps({
            "reason": "max_iters_reached",
            "fallback_used": True,
        }, indent=2))
    try:
        validated = TokenomicsOutput(**{**raw, "token_symbol": symbol})
        payload = validated.model_dump()
        out_path.write_text(json.dumps(payload, indent=2))
        if stale_path.exists():
            stale_path.unlink()
        return {"ok": True, "path": str(out_path), "output": payload}
    except Exception as e:
        if out_path.exists():
            out_path.rename(stale_path)
        payload = {
            "error": f"{type(e).__name__}: {e}",
            "raw_output": raw,
        }
        if is_fallback:
            payload["reason"] = "max_iters_reached"
            payload["fallback_used"] = True
        err_path.write_text(json.dumps(payload, indent=2, default=str))
        return {"ok": False, "error": str(e), "path": str(err_path)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbol", nargs="?", help="token symbol; omit with --all")
    p.add_argument("--all", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--max-iters", type=int, default=14)
    args = p.parse_args(argv)

    if args.all:
        syms = tokens.all_symbols()
    elif args.symbol:
        syms = [args.symbol.upper()]
    else:
        p.error("provide a symbol or --all")
        return 2

    for s in syms:
        print(f"\n=== Agent 1 :: {s} ===")
        result = analyze(s, max_iters=args.max_iters, verbose=args.verbose)
        print(json.dumps(result, indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
