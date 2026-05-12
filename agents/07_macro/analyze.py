"""Agent 7 RLM analyzer — macro / cycle positioning."""
from __future__ import annotations
import argparse, json, pathlib, sqlite3, sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path: sys.path.insert(0, str(_REPO_ROOT))
from shared import tokens
from shared.freshness import stamp_data_as_of
from shared.rlm import run_rlm
from shared.schemas import MacroOutput

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "macro.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
REPORTS_DIR = _REPO_ROOT / "reports"

_TASK = """\
Score macro / cycle positioning. Tables: macro_snapshot (global),
token_cycle_metric, macro_research_note.

HARD 14-turn budget. THIS AGENT SHOULD BE FAST. The data is small and
deterministic — two SELECTs and a classification. Aim for FINAL by turn 4.

PROCESS (don't deviate):
  1. Turn 1: SELECT fear_greed_index, altcoin_season_index, btc_dominance_pct,
     fed_funds_rate, m2_yoy_pct FROM macro_snapshot ORDER BY snapshot_at DESC
     LIMIT 1.
  2. Turn 2: SELECT funding_rate_8h, btc_correlation_30d FROM
     token_cycle_metric WHERE token_symbol=? ORDER BY snapshot_at DESC LIMIT 1.
  3. Turn 3: classify cycle_phase using the heuristic below. Set FINAL.

cycle_phase HEURISTIC (apply in order, first match wins):
  • fear_greed > 75 AND altcoin_season > 75       → DISTRIBUTION
  • fear_greed > 75                               → LATE_BULL
  • fear_greed 60-75 AND funding_rate > 0.0003    → MID_BULL
  • fear_greed 40-60                              → EARLY_BULL
  • fear_greed 25-40                              → ACCUMULATION
  • fear_greed < 25                               → BEAR
  • all metrics null                              → ACCUMULATION (default)

leverage_warning = (funding_rate_8h > 0.0005)
entry_timing_risk = round(11 - fear_greed/10), clamped to 1..10
macro_rating: STRONG if entry_timing_risk ≥ 7, NEUTRAL if 4-6, WEAK if ≤ 3
composite_score: max(20, min(80, entry_timing_risk * 8 - (15 if leverage_warning else 0)))

If macro_snapshot is empty (no global data): emit FINAL by turn 2 with
cycle_phase="ACCUMULATION", macro_rating="NEUTRAL", entry_timing_risk=5,
composite_score=50. Rationale: "No macro snapshot available; emit conservative."

DO NOT call sub_lm — this agent doesn't need narrative synthesis. Just SQL
+ heuristic + emit.
"""

_SCHEMA_DOCS = {
    "token_symbol": "string",
    "cycle_phase": '"EARLY_BULL"|"MID_BULL"|"LATE_BULL"|"DISTRIBUTION"|"BEAR"|"ACCUMULATION"',
    "macro_rating": '"STRONG"|"NEUTRAL"|"WEAK"',
    "entry_timing_risk": "int 1-10 (10 = great entry)",
    "leverage_warning": "bool",
    "btc_correlation_30d": "float -1..1 or null",
    "rationale": "≤800 chars",
    "composite_score": "int 0-100",
}


def _fallback_output(symbol: str, conn: sqlite3.Connection, why: str) -> dict[str, Any]:
    """Schema-valid degraded output for macro agent."""
    g = conn.execute(
        "SELECT snapshot_at, fear_greed_index, altcoin_season_index, btc_dominance_pct "
        "FROM macro_snapshot ORDER BY snapshot_at DESC LIMIT 1"
    ).fetchone()
    t = conn.execute(
        "SELECT funding_rate_8h, btc_correlation_30d FROM token_cycle_metric "
        "WHERE token_symbol=? ORDER BY snapshot_at DESC LIMIT 1", (symbol,),
    ).fetchone()

    fg = g["fear_greed_index"] if g else None
    if fg is None:
        cycle, rating = "ACCUMULATION", "NEUTRAL"
    elif fg >= 75:
        cycle, rating = "LATE_BULL", "WEAK"
    elif fg >= 60:
        cycle, rating = "MID_BULL", "NEUTRAL"
    elif fg >= 40:
        cycle, rating = "EARLY_BULL", "STRONG"
    elif fg >= 25:
        cycle, rating = "ACCUMULATION", "STRONG"
    else:
        cycle, rating = "BEAR", "NEUTRAL"

    funding = t["funding_rate_8h"] if t else None
    leverage_warning = funding is not None and funding > 0.0005
    entry = 5
    if fg is not None:
        entry = max(1, min(10, int(11 - (fg / 10))))  # fear → high entry score

    btc_corr = t["btc_correlation_30d"] if t else None
    composite = max(20, min(80, entry * 8 - (15 if leverage_warning else 0)))

    return {
        "token_symbol": symbol,
        "cycle_phase": cycle,
        "macro_rating": rating,
        "entry_timing_risk": entry,
        "leverage_warning": bool(leverage_warning),
        "btc_correlation_30d": btc_corr,
        "rationale": (
            f"RLM did not converge ({why}); fallback applied. "
            f"Fear/Greed={fg}, BTC dom={g['btc_dominance_pct'] if g else 'NA'}, "
            f"funding rate (8h)={funding}, BTC corr 30d={btc_corr}. "
            f"Cycle phase '{cycle}' from heuristic; rerun for narrative."
        ),
        "composite_score": composite,
        "data_as_of": g["snapshot_at"] if g else None,
    }


def analyze(symbol: str, *, max_iters: int = 14, verbose: bool = False) -> dict[str, Any]:
    symbol = symbol.upper(); tokens.get(symbol)
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Run collect first — {DB_PATH} missing.")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    sidecar = SIDECAR_DIR / symbol
    env = {"token_symbol": symbol, "macro_db": conn, "sidecar_dir": str(sidecar),
           "sidecar_files": [p.name for p in sidecar.glob("*.json")] if sidecar.exists() else []}
    raw = run_rlm(agent_name=f"07_macro::{symbol}", environment=env, task=_TASK,
                  output_schema=_SCHEMA_DOCS, max_iters=max_iters, verbose=verbose)
    if raw.get("error") == "max_iters_reached":
        raw = _fallback_output(symbol, conn, f"max_iters={raw.get('iters')}")
    # Always populate data_as_of from the DB row the agent should have consumed
    stamp_data_as_of(raw, conn, table="macro_snapshot")
    out_dir = REPORTS_DIR / symbol; out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent_07_macro.json"
    err_path = out_dir / "agent_07_macro.error.json"
    stale_path = out_dir / "agent_07_macro.stale.json"
    is_fallback = "RLM did not converge" in str(raw.get("rationale", ""))
    if is_fallback:
        err_path.write_text(json.dumps({
            "reason": "max_iters_reached",
            "fallback_used": True,
        }, indent=2))
    try:
        v = MacroOutput(**{**raw, "token_symbol": symbol})
        out_path.write_text(json.dumps(v.model_dump(), indent=2))
        # Successful run — clean up any prior .stale.json marker
        if stale_path.exists():
            stale_path.unlink()
        return {"ok": True, "path": str(out_path)}
    except Exception as e:
        # Validation failed — move the previous successful output aside so the
        # orchestrator can't silently load it.
        if out_path.exists():
            out_path.rename(stale_path)
        payload = {"error": str(e), "raw": raw}
        if is_fallback:
            payload["reason"] = "max_iters_reached"
            payload["fallback_used"] = True
        err_path.write_text(json.dumps(payload, indent=2, default=str))
        return {"ok": False, "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("symbol", nargs="?"); p.add_argument("--all", action="store_true"); p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    syms = tokens.all_symbols() if args.all else [args.symbol.upper()] if args.symbol else None
    if not syms: p.error("provide a symbol or --all")
    for s in syms:
        print(f"\n=== Agent 7 :: {s} ==="); print(json.dumps(analyze(s, verbose=args.verbose), indent=2, default=str)[:2000])
    return 0

if __name__ == "__main__": raise SystemExit(main())
