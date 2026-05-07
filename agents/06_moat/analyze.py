"""Agent 6 RLM analyzer — competitive moat."""
from __future__ import annotations
import argparse, json, pathlib, sqlite3, sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path: sys.path.insert(0, str(_REPO_ROOT))
from shared import tokens
from shared.rlm import run_rlm
from shared.schemas import MoatOutput

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "moat.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
REPORTS_DIR = _REPO_ROOT / "reports"

_TASK = """\
Score competitive moat. Tables: competitor, market_share, dev_ecosystem,
moat_research_note.

HARD 14-turn budget. Set FINAL early — don't loop trying to perfect every
field if data is sparse.

EMIT-EARLY rules:

  • DOMINANT LEADER FAST PATH:
    If market_share.share_pct >= 0.40 AND competitor table has ≥ 3 entries:
    emit FINAL by turn 4 with category_rank=1, moat_strength_score 7-9,
    competitive_threat="LOW", network_effect_type derived from token
    category, composite_score 65-80.

  • COMMODITY / NO-MOAT FAST PATH:
    If competitor is empty AND market_share is empty (typical for fair-
    launch L1s like XMR where 'moat' is non-applicable): emit FINAL by
    turn 3 with moat_strength_score=5, category_rank=3, network_effect=
    "NONE", competitive_threat="MODERATE", composite_score=45.

  • UNDER-PRESSURE FAST PATH:
    If a competitor's market_cap_usd > THIS token's MC AND has TVL/dau
    superiority: emit FINAL by turn 4 with category_rank=2-3, threat=
    "HIGH" or "SEVERE", composite_score ≤ 40.

Otherwise:
  1. Rank by competitor table; market_share latest = direction signal.
  2. dev_ecosystem high active devs + integrations = developer-side moat.
  3. network_effect_type: LIQUIDITY (DEXes/oracles), DEVELOPER (L1s/L2s),
     USER (consumer apps), DATA (NMR-like), NONE (commodity).
  4. sub_lm the moat_summary note for switching-cost narrative.

competitive_threat: SEVERE if a funded competitor gaining share, MODERATE
if status quo, LOW if entrenched leader.
"""

_SCHEMA_DOCS = {
    "token_symbol": "string",
    "moat_strength_score": "int 1-10",
    "category_rank": "int (1 = leader)",
    "network_effect_type": '"LIQUIDITY"|"DEVELOPER"|"USER"|"DATA"|"NONE"',
    "competitive_threat": '"LOW"|"MODERATE"|"HIGH"|"SEVERE"',
    "regulatory_relative_risk": '"LOWER"|"SIMILAR"|"HIGHER"',
    "rationale": "≤800 chars",
    "composite_score": "int 0-100",
}


def _fallback_output(symbol: str, conn: sqlite3.Connection, why: str) -> dict[str, Any]:
    """Schema-valid degraded output for moat agent."""
    n_comp = conn.execute(
        "SELECT COUNT(*) FROM competitor WHERE token_symbol=?", (symbol,)
    ).fetchone()[0]
    share = conn.execute(
        "SELECT share_pct FROM market_share WHERE token_symbol=? "
        "ORDER BY snapshot_at DESC LIMIT 1", (symbol,),
    ).fetchone()
    de = conn.execute(
        "SELECT monthly_active_devs FROM dev_ecosystem WHERE token_symbol=? "
        "ORDER BY snapshot_at DESC LIMIT 1", (symbol,),
    ).fetchone()

    s = share["share_pct"] if share else None
    if s is not None and s >= 0.4:
        moat, threat, rank = 8, "LOW", 1
    elif s is not None and s >= 0.15:
        moat, threat, rank = 6, "MODERATE", 2
    elif s is not None:
        moat, threat, rank = 4, "HIGH", 3
    else:
        moat, threat, rank = 5, "MODERATE", 3

    devs = de["monthly_active_devs"] if de else None
    if devs and devs >= 100:
        net_effect = "DEVELOPER"
    elif n_comp >= 3:
        net_effect = "USER"
    else:
        net_effect = "NONE"

    composite = max(25, min(75, moat * 8))

    return {
        "token_symbol": symbol,
        "moat_strength_score": moat,
        "category_rank": rank,
        "network_effect_type": net_effect,
        "competitive_threat": threat,
        "regulatory_relative_risk": "SIMILAR",
        "rationale": (
            f"RLM did not converge ({why}); fallback applied. "
            f"Market share: {f'{s*100:.1f}%' if s else 'unknown'}, "
            f"competitors tracked: {n_comp}, "
            f"monthly active devs: {devs or 'unknown'}. "
            f"Heuristic placed at rank {rank}; rerun for narrative."
        ),
        "composite_score": composite,
    }


def analyze(symbol: str, *, max_iters: int = 14, verbose: bool = False) -> dict[str, Any]:
    symbol = symbol.upper(); tokens.get(symbol)
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Run collect first — {DB_PATH} missing.")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    sidecar = SIDECAR_DIR / symbol
    env = {"token_symbol": symbol, "moat_db": conn, "sidecar_dir": str(sidecar),
           "sidecar_files": [p.name for p in sidecar.glob("*.json")] if sidecar.exists() else []}
    raw = run_rlm(agent_name=f"06_moat::{symbol}", environment=env, task=_TASK,
                  output_schema=_SCHEMA_DOCS, max_iters=max_iters, verbose=verbose)
    if raw.get("error") == "max_iters_reached":
        raw = _fallback_output(symbol, conn, f"max_iters={raw.get('iters')}")
    out_dir = REPORTS_DIR / symbol; out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent_06_moat.json"
    try:
        v = MoatOutput(**{**raw, "token_symbol": symbol})
        out_path.write_text(json.dumps(v.model_dump(), indent=2))
        return {"ok": True, "path": str(out_path)}
    except Exception as e:
        (out_dir / "agent_06_moat.error.json").write_text(json.dumps({"error": str(e), "raw": raw}, indent=2, default=str))
        return {"ok": False, "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("symbol", nargs="?"); p.add_argument("--all", action="store_true"); p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    syms = tokens.all_symbols() if args.all else [args.symbol.upper()] if args.symbol else None
    if not syms: p.error("provide a symbol or --all")
    for s in syms:
        print(f"\n=== Agent 6 :: {s} ==="); print(json.dumps(analyze(s, verbose=args.verbose), indent=2, default=str)[:2000])
    return 0

if __name__ == "__main__": raise SystemExit(main())
