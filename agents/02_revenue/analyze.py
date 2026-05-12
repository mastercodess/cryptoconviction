"""Agent 2 RLM analyzer — same pattern as Agent 1."""
from __future__ import annotations

import argparse, json, pathlib, sqlite3, sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.freshness import stamp_data_as_of                      # noqa: E402
from shared.rlm import run_rlm                                     # noqa: E402
from shared.schemas import RevenueOutput                           # noqa: E402

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "revenue.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
REPORTS_DIR = _REPO_ROOT / "reports"

_TASK = """\
Score the protocol revenue / fundamentals for the given token.

Tables in revenue_db:
  - revenue_snapshot: latest annualized_revenue_usd, tvl_usd, p_s_ratio,
    p_tvl_ratio, real_yield_apr, inflationary_yield_apr, seasonality_note.
  - revenue_history: daily series (may be sparse on free tier).
  - peer_comparison: peer P/S, P/TVL multiples.
  - revenue_research_note: prose summaries.

Strategy:
  1. SELECT * FROM revenue_snapshot WHERE token_symbol=? — read latest.
  2. NON-PROTOCOL FAST PATH: If annualized_revenue_usd, tvl_usd, AND
     p_s_ratio are ALL null after step 1, this token is not a fee-accruing
     protocol (oracles like LINK, privacy chains like XMR, ML-tournament
     tokens like NMR). DO NOT keep probing for data that isn't there. By
     turn 3 at the latest, set FINAL with:
       revenue_quality_score = 4
       growth_trend           = "STEADY"
       valuation_vs_peers     = "NEUTRAL"
       real_yield_apr         = null
       inflationary_yield_apr = null
       annualized_revenue_usd = null
       p_s_ratio              = null
       composite_score        = 35   (conservative — neither bullish nor bearish)
       rationale              = "Token is not a fee-accruing protocol; revenue
                                 fundamentals don't apply. <explain WHY in 1-2
                                 sentences using the research_note context>"
  3. PROTOCOL PATH: revenue is non-null. Compute P/S relative to peer
     median; classify trend.
  4. CRUCIAL: real_yield_apr (from real fees) vs inflationary_yield_apr
     (emission-funded). Fake yield is penalized in composite_score.
  5. sub_lm() the prose research_note to extract growth narrative if you
     can't infer it from numbers alone.

Composite weighting suggestion (PROTOCOL PATH only):
  ~30% real-yield magnitude, ~25% growth trend, ~25% valuation vs peers,
  ~20% revenue durability (seasonality/concentration).

You have a HARD 12-turn budget. Use it efficiently. Set FINAL early when the
data is unambiguous; don't burn turns re-querying the same null fields.
"""

_SCHEMA_DOCS = {
    "token_symbol": "string",
    "revenue_quality_score": "int 1-10",
    "growth_trend": '"ACCELERATING"|"STEADY"|"DECELERATING"|"DECLINING"',
    "valuation_vs_peers": '"STRONG"|"NEUTRAL"|"WEAK" (cheap vs peers = STRONG)',
    "real_yield_apr": "float % or null",
    "inflationary_yield_apr": "float % or null",
    "annualized_revenue_usd": "float or null",
    "p_s_ratio": "float or null",
    "rationale": "≤800 chars",
    "composite_score": "int 0-100",
}


def _fallback_output(symbol: str, conn: sqlite3.Connection, why: str) -> dict[str, Any]:
    """Build a conservative, schema-valid output when the RLM didn't converge.

    Inspects the DB to figure out whether this is a non-protocol token
    (all-null revenue fields) and emits the appropriate documented defaults.
    """
    row = conn.execute(
        "SELECT annualized_revenue_usd, tvl_usd, p_s_ratio, p_tvl_ratio, "
        "real_yield_apr, inflationary_yield_apr "
        "FROM revenue_snapshot WHERE token_symbol=? "
        "ORDER BY snapshot_at DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    # Non-protocol = the three revenue-defining fields are all null.
    # We deliberately ignore inflationary_yield_apr here — XMR has emission
    # inflation but isn't a fee protocol.
    if row is None:
        is_non_protocol = True
    else:
        is_non_protocol = (
            row["annualized_revenue_usd"] is None
            and row["tvl_usd"] is None
            and row["p_s_ratio"] is None
        )
    if is_non_protocol:
        return {
            "token_symbol": symbol,
            "revenue_quality_score": 4,
            "growth_trend": "STEADY",
            "valuation_vs_peers": "NEUTRAL",
            "real_yield_apr": None,
            "inflationary_yield_apr": None,
            "annualized_revenue_usd": None,
            "p_s_ratio": None,
            "rationale": (
                f"RLM did not converge ({why}); fallback applied. Token's "
                "revenue_snapshot is all-null, indicating this is not a "
                "fee-accruing protocol (oracle/privacy chain/utility token). "
                "Conservative score reflects uninformed-prior on revenue dim."
            ),
            "composite_score": 35,
        }
    # Protocol path with a partial DB — emit something safer than crashing.
    return {
        "token_symbol": symbol,
        "revenue_quality_score": 5,
        "growth_trend": "STEADY",
        "valuation_vs_peers": "NEUTRAL",
        "real_yield_apr": row["real_yield_apr"] if row else None,
        "inflationary_yield_apr": row["inflationary_yield_apr"] if row else None,
        "annualized_revenue_usd": row["annualized_revenue_usd"] if row else None,
        "p_s_ratio": row["p_s_ratio"] if row else None,
        "rationale": (
            f"RLM did not converge ({why}); fallback applied. Numeric fields "
            "echoed from latest revenue_snapshot row. Run analyze again with "
            "richer sidecar data for a real verdict."
        ),
        "composite_score": 50,
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
        "revenue_db": conn,
        "sidecar_dir": str(sidecar_path),
        "sidecar_files": [p.name for p in sidecar_path.glob("*.json")] if sidecar_path.exists() else [],
    }
    raw = run_rlm(
        agent_name=f"02_revenue::{symbol}",
        environment=env,
        task=_TASK,
        output_schema=_SCHEMA_DOCS,
        max_iters=max_iters, verbose=verbose,
    )
    out_dir = REPORTS_DIR / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent_02_revenue.json"

    # If the RLM failed to converge, build a conservative fallback rather
    # than crashing schema validation downstream.
    if raw.get("error") == "max_iters_reached":
        why = f"max_iters={raw.get('iters')}"
        raw = _fallback_output(symbol, conn, why)

    stamp_data_as_of(raw, conn, table="revenue_snapshot", symbol=symbol)

    try:
        validated = RevenueOutput(**{**raw, "token_symbol": symbol})
        out_path.write_text(json.dumps(validated.model_dump(), indent=2))
        return {"ok": True, "path": str(out_path), "fallback": "fallback applied" in raw.get("rationale", "")}
    except Exception as e:                                  # noqa: BLE001
        (out_dir / "agent_02_revenue.error.json").write_text(
            json.dumps({"error": str(e), "raw": raw}, indent=2, default=str))
        return {"ok": False, "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbol", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    syms = tokens.all_symbols() if args.all else [args.symbol.upper()] if args.symbol else None
    if not syms:
        p.error("provide a symbol or --all")
    for s in syms:
        print(f"\n=== Agent 2 :: {s} ===")
        print(json.dumps(analyze(s, verbose=args.verbose), indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
