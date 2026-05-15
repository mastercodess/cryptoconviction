"""Agent 4 RLM analyzer — on-chain intelligence."""
from __future__ import annotations
import argparse, json, pathlib, sqlite3, sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path: sys.path.insert(0, str(_REPO_ROOT))
from shared import tokens
from shared.freshness import stamp_data_as_of
from shared.rlm import run_rlm
from shared.schemas import OnChainOutput

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "onchain.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
REPORTS_DIR = _REPO_ROOT / "reports"

_TASK = """\
Score on-chain activity authenticity. Tables: activity_metric, exchange_flow,
holder_cohort, onchain_research_note.

HARD 14-turn budget. Use it efficiently. Set FINAL as soon as you have
enough signal — do NOT keep probing for fields that aren't in the DB.

Read the manifest first: if `data_quality_hint` is UNAVAILABLE or PARTIAL,
trust it and apply the matching EMIT-EARLY rule on turn 1-2. Don't waste
turns re-validating what the collector already flagged.

EMIT-EARLY rules (apply if hit; aim to set FINAL by turn 5):

  • UNAVAILABLE FAST PATH (protocol-class, no Dune chain queries):
    If activity_metric, exchange_flow, AND holder_cohort all have zero
    rows for this token (typical for protocols like AAVE/UNI/MORPHO
    where Dune chain queries don't apply), OR data_quality_hint is
    UNAVAILABLE: emit FINAL by turn 3 with all scores=5,
    growth_authenticity_verdict="NEUTRAL", retention_health_grade="C",
    smart_money_stance="UNKNOWN", composite_score=50. Rationale:
    "On-chain chain-level data not collected for this protocol-class
    token; UNAVAILABLE."

  • DAU-ONLY CHAIN-CLASS FAST PATH (Dune-fed chain, no MAU populated):
    If activity_metric has dau populated AND dau_mau_ratio IS NULL
    AND exchange_flow has no fresh rows (most recent >60 days old),
    OR data_quality_hint is PARTIAL: emit FINAL by turn 4 with:
      organic_activity_score: 9 if dau>=5M, 7 if dau>=1M, 5 if dau>=100K,
                              else 3
      capital_flow_direction: "FLAT"
      holder_quality_rating: 5
      growth_authenticity_verdict: "NEUTRAL"
      retention_health_grade: "C"
      smart_money_stance: "UNKNOWN"
    Cite the DAU value in rationale. composite_score in 40-60 range.

  • STALE FLOW FAST PATH (applies as a modifier to any other path):
    If the most recent exchange_flow row is >60 days old, treat
    capital_flow_direction as "FLAT" — do not synthesize a directional
    verdict from a stale row. Continue to score other dimensions from
    fresh data, but cap capital_flow at FLAT.

Strategy (full path, only if no EMIT-EARLY rule fits):
  1. SELECT * FROM activity_metric — read dau, dau_mau_ratio. If
     dau_mau_ratio is null but dau is populated, score from DAU
     alone — don't probe a separate retention table; the project
     doesn't have one.
  2. SELECT SUM(net_usd) FROM exchange_flow last 30d — only if the
     latest row is <60 days old. Negative net = outflow = bullish;
     positive = exchange-bound = bearish.
  3. SELECT lth_supply_pct, smart_money_stance FROM holder_cohort latest.
  4. For wash-trade concerns, sub_lm() the research_note 'summary' rows.
  5. growth_authenticity_verdict: STRONG if real DAU growth + LTH
     increasing + outflows; WEAK if incentive farming or wash-trading
     suspected.

For privacy chains (XMR) most fields will be UNAVAILABLE — score on the
basis of network usage proxies only and document UNKNOWNs.
"""

_SCHEMA_DOCS = {
    "token_symbol": "string",
    "organic_activity_score": "int 1-10",
    "capital_flow_direction": '"INFLOW"|"OUTFLOW"|"MIXED"|"FLAT"',
    "holder_quality_rating": "int 1-10",
    "growth_authenticity_verdict": '"STRONG"|"NEUTRAL"|"WEAK"',
    "retention_health_grade": '"A"|"B"|"C"|"D"|"F"',
    "smart_money_stance": '"ACCUMULATING"|"DISTRIBUTING"|"NEUTRAL"|"UNKNOWN"',
    "rationale": "≤1500 chars",
    "composite_score": "int 0-100",
}


def _fallback_output(symbol: str, conn: sqlite3.Connection, why: str) -> dict[str, Any]:
    """Schema-valid degraded output for on-chain agent."""
    flow = conn.execute(
        "SELECT inflow_usd, outflow_usd, net_usd FROM exchange_flow "
        "WHERE token_symbol=? ORDER BY date DESC LIMIT 1", (symbol,),
    ).fetchone()
    cohort = conn.execute(
        "SELECT lth_supply_pct, smart_money_stance FROM holder_cohort "
        "WHERE token_symbol=? ORDER BY snapshot_at DESC LIMIT 1", (symbol,),
    ).fetchone()
    activity = conn.execute(
        "SELECT dau, mau, dau_mau_ratio FROM activity_metric "
        "WHERE token_symbol=? ORDER BY snapshot_at DESC LIMIT 1", (symbol,),
    ).fetchone()

    # Capital flow direction from net_usd if available
    if flow and flow["net_usd"] is not None:
        n = flow["net_usd"]
        flow_dir = "OUTFLOW" if n < 0 else "INFLOW" if n > 0 else "FLAT"
    else:
        flow_dir = "FLAT"

    smart = (cohort["smart_money_stance"] if cohort else None) or "UNKNOWN"
    lth = cohort["lth_supply_pct"] if cohort else None

    # Score holder quality from LTH%
    if lth is not None:
        holder_score = 9 if lth >= 0.7 else 7 if lth >= 0.5 else 5 if lth >= 0.3 else 3
    else:
        holder_score = 5

    # Score organic activity. Prefer dau_mau_ratio (more informative) when
    # populated; fall back to DAU magnitude when only DAU is available — the
    # typical post-Dune chain-class case (CHAIN_DAU query gives DAU, no MAU).
    dau_value = activity["dau"] if activity else None
    ratio_value = activity["dau_mau_ratio"] if activity else None
    if ratio_value is not None:
        activity_score = 9 if ratio_value >= 0.4 else 7 if ratio_value >= 0.2 else 5 if ratio_value >= 0.1 else 3
    elif dau_value is not None:
        activity_score = 9 if dau_value >= 5_000_000 else 7 if dau_value >= 1_000_000 else 5 if dau_value >= 100_000 else 3
    else:
        activity_score = 5

    if dau_value is not None:
        dau_str = f"DAU={dau_value:,}" + (f" (ratio {ratio_value:.2f})" if ratio_value is not None else "")
    else:
        dau_str = "DAU/MAU: unknown"

    composite = max(25, min(75, holder_score*5 + activity_score*4 + (15 if flow_dir == "OUTFLOW" else 0)))

    return {
        "token_symbol": symbol,
        "organic_activity_score": activity_score,
        "capital_flow_direction": flow_dir,
        "holder_quality_rating": holder_score,
        "growth_authenticity_verdict": "NEUTRAL",
        "retention_health_grade": "C",
        "smart_money_stance": smart,
        "rationale": (
            f"RLM did not converge ({why}); fallback applied. "
            f"Net flow: {flow_dir} (net_usd={flow['net_usd'] if flow else 'NA'}), "
            f"LTH%: {f'{lth*100:.1f}' if lth else 'unknown'}, "
            f"smart money: {smart}, "
            f"{dau_str}. "
            "Conservative scores; rerun analyze for narrative judgement."
        ),
        "composite_score": composite,
    }


def analyze(symbol: str, *, max_iters: int = 14, verbose: bool = False) -> dict[str, Any]:
    symbol = symbol.upper()
    tokens.get(symbol)
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Run collect first — {DB_PATH} missing.")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    sidecar = SIDECAR_DIR / symbol
    env = {"token_symbol": symbol, "onchain_db": conn,
           "sidecar_dir": str(sidecar),
           "sidecar_files": [p.name for p in sidecar.glob("*.json")] if sidecar.exists() else []}
    raw = run_rlm(agent_name=f"04_onchain::{symbol}", environment=env,
                  task=_TASK, output_schema=_SCHEMA_DOCS, max_iters=max_iters, verbose=verbose)
    if raw.get("error") == "max_iters_reached":
        raw = _fallback_output(symbol, conn, f"max_iters={raw.get('iters')}")
    stamp_data_as_of(raw, conn, table="activity_metric", symbol=symbol)
    out_dir = REPORTS_DIR / symbol; out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent_04_onchain.json"
    err_path = out_dir / "agent_04_onchain.error.json"
    stale_path = out_dir / "agent_04_onchain.stale.json"
    is_fallback = "RLM did not converge" in str(raw.get("rationale", ""))
    if is_fallback:
        err_path.write_text(json.dumps({
            "reason": "max_iters_reached",
            "fallback_used": True,
        }, indent=2))
    try:
        v = OnChainOutput(**{**raw, "token_symbol": symbol})
        out_path.write_text(json.dumps(v.model_dump(), indent=2))
        if stale_path.exists():
            stale_path.unlink()
        return {"ok": True, "path": str(out_path)}
    except Exception as e:
        if out_path.exists():
            out_path.rename(stale_path)
        payload = {"error": str(e), "raw": raw}
        if is_fallback:
            payload["reason"] = "max_iters_reached"
            payload["fallback_used"] = True
        err_path.write_text(json.dumps(payload, indent=2, default=str))
        return {"ok": False, "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbol", nargs="?"); p.add_argument("--all", action="store_true"); p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    syms = tokens.all_symbols() if args.all else [args.symbol.upper()] if args.symbol else None
    if not syms: p.error("provide a symbol or --all")
    for s in syms:
        print(f"\n=== Agent 4 :: {s} ==="); print(json.dumps(analyze(s, verbose=args.verbose), indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
