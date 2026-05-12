"""Agent 5 RLM analyzer — team / VC / legal."""
from __future__ import annotations
import argparse, json, pathlib, sqlite3, sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path: sys.path.insert(0, str(_REPO_ROOT))
from shared import tokens
from shared.freshness import stamp_data_as_of
from shared.rlm import run_rlm
from shared.schemas import TeamOutput

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "team.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
REPORTS_DIR = _REPO_ROOT / "reports"

_TASK = """\
Score team + investor diligence. Tables: team_member, investor, legal_event,
team_research_note.

HARD 14-turn budget. Set FINAL early — don't burn turns probing for facts
that aren't in the DB.

EMIT-EARLY rules:

  • FAIR LAUNCH FAST PATH:
    If team_member is empty for this symbol AND investor is empty (typical
    for XMR or any pre-2017 fair-launch chain), emit FINAL by turn 3 with:
      trust_tier="TIER_2", founder_credibility_score=6, alignment_score=8,
      vc_overhang_risk="LOW", doxxed=False, legal_exposure_flag=False,
      composite_score=60. Rationale: "Fair-launch project, no team or VC
      table — scoring on absence of insider concentration."

  • CLEAR TIER_1 FAST PATH:
    If majority of team_member rows have doxxed=1 AND no legal_event rows
    have severity in ('moderate','severe','high'): emit FINAL by turn 4
    with trust_tier="TIER_1", founder_credibility_score 7-9, alignment 7-9,
    legal_exposure_flag=False, composite_score 65-80.

  • RED FLAG FAST PATH:
    If any legal_event has severity in ('severe','high'): emit FINAL by
    turn 3 with trust_tier="TIER_3", legal_exposure_flag=True,
    vc_overhang_risk="HIGH", composite_score ≤ 35.

Otherwise (mixed signal):
  1. SELECT name, doxxed, prior_projects FROM team_member.
  2. SELECT investor_name, ownership_pct, unlock_status FROM investor.
  3. Read research_note topics ('founder_credibility', 'vc_overhang',
     'alignment') and pass to sub_lm if synthesis needed.
  4. Tier scale: TIER_1 = doxxed + top-tier VCs + no legal; TIER_2 =
     credible track record with some flags; TIER_3 = anonymous OR unknown
     OR legal exposure; UNKNOWN = insufficient data.
"""

_SCHEMA_DOCS = {
    "token_symbol": "string",
    "founder_credibility_score": "int 1-10",
    "vc_overhang_risk": '"LOW"|"MODERATE"|"HIGH"|"EXTREME"',
    "alignment_score": "int 1-10",
    "legal_exposure_flag": "bool",
    "trust_tier": '"TIER_1"|"TIER_2"|"TIER_3"|"UNKNOWN"',
    "doxxed": "bool — majority of core team identified",
    "rationale": "≤800 chars",
    "composite_score": "int 0-100",
}


def _fallback_output(symbol: str, conn: sqlite3.Connection, why: str) -> dict[str, Any]:
    """Schema-valid degraded output for team agent."""
    n_team = conn.execute(
        "SELECT COUNT(*) FROM team_member WHERE token_symbol=?", (symbol,)
    ).fetchone()[0]
    n_doxxed = conn.execute(
        "SELECT COUNT(*) FROM team_member WHERE token_symbol=? AND doxxed=1", (symbol,)
    ).fetchone()[0]
    n_inv = conn.execute(
        "SELECT COUNT(*) FROM investor WHERE token_symbol=?", (symbol,)
    ).fetchone()[0]
    legal_serious = conn.execute(
        "SELECT COUNT(*) FROM legal_event WHERE token_symbol=? "
        "AND severity IN ('moderate','high','severe')", (symbol,),
    ).fetchone()[0]

    doxxed_majority = (n_team > 0 and n_doxxed >= n_team / 2)
    if doxxed_majority and n_inv > 0 and legal_serious == 0:
        tier, fc, vc, align = "TIER_1", 8, "LOW", 7
    elif doxxed_majority and legal_serious == 0:
        tier, fc, vc, align = "TIER_2", 6, "MODERATE", 6
    elif legal_serious > 0:
        tier, fc, vc, align = "TIER_3", 3, "HIGH", 3
    elif n_team == 0:
        tier, fc, vc, align = "UNKNOWN", 5, "MODERATE", 5
    else:
        tier, fc, vc, align = "TIER_3", 4, "MODERATE", 4

    composite = max(20, min(80, fc*5 + align*5 - legal_serious*10))

    return {
        "token_symbol": symbol,
        "founder_credibility_score": fc,
        "vc_overhang_risk": vc,
        "alignment_score": align,
        "legal_exposure_flag": bool(legal_serious),
        "trust_tier": tier,
        "doxxed": doxxed_majority,
        "rationale": (
            f"RLM did not converge ({why}); fallback applied. "
            f"DB shows {n_team} team members ({n_doxxed} doxxed), "
            f"{n_inv} investors, {legal_serious} serious legal events. "
            f"Trust tier {tier} from heuristic; rerun for narrative."
        ),
        "composite_score": composite,
    }


def analyze(symbol: str, *, max_iters: int = 14, verbose: bool = False) -> dict[str, Any]:
    symbol = symbol.upper(); tokens.get(symbol)
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Run collect first — {DB_PATH} missing.")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    sidecar = SIDECAR_DIR / symbol
    env = {"token_symbol": symbol, "team_db": conn, "sidecar_dir": str(sidecar),
           "sidecar_files": [p.name for p in sidecar.glob("*.json")] if sidecar.exists() else []}
    raw = run_rlm(agent_name=f"05_team::{symbol}", environment=env, task=_TASK,
                  output_schema=_SCHEMA_DOCS, max_iters=max_iters, verbose=verbose)
    if raw.get("error") == "max_iters_reached":
        raw = _fallback_output(symbol, conn, f"max_iters={raw.get('iters')}")
    stamp_data_as_of(raw, conn, table="team_research_note", ts_col="collected_at", symbol=symbol)
    out_dir = REPORTS_DIR / symbol; out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent_05_team.json"
    try:
        v = TeamOutput(**{**raw, "token_symbol": symbol})
        out_path.write_text(json.dumps(v.model_dump(), indent=2))
        return {"ok": True, "path": str(out_path)}
    except Exception as e:
        (out_dir / "agent_05_team.error.json").write_text(json.dumps({"error": str(e), "raw": raw}, indent=2, default=str))
        return {"ok": False, "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("symbol", nargs="?"); p.add_argument("--all", action="store_true"); p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    syms = tokens.all_symbols() if args.all else [args.symbol.upper()] if args.symbol else None
    if not syms: p.error("provide a symbol or --all")
    for s in syms:
        print(f"\n=== Agent 5 :: {s} ==="); print(json.dumps(analyze(s, verbose=args.verbose), indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
