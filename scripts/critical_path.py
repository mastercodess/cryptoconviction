"""
Generate a critical-path TODO list of the highest-impact unfilled gaps.

For each token's worksheet, computes per-gap impact:
    impact = max(|delta_points|) across rules  ×  agent_weight (from config.yaml)

E.g., a gap with rules ±10 on security (weight 18%) has impact = 1.8, while
a gap with rules ±5 on macro (weight 12%) has impact = 0.6. The first is
3x more useful to fill.

Surfaces the top N gaps per token (default 2). Output is a single markdown
TODO file: `data/manual_research/critical_path.md`. Tokens are grouped by
current conviction score band so you can prioritize triage:
  - Solid (65–74): pushing into STRONG territory
  - Neutral (55–64): swing tokens worth resolving
  - Weak (45–54): need positive surprises to be actionable
  - Bottom (<45): unlikely to clear, low priority for manual work
  - No data: needs collect run first

The file is meant to be the worksheet for ~30-60 minutes of focused manual
work. Once filled (set `value` in the per-token worksheet), re-run the
reconciler to see the impact on scores.

Run:
  python3 -m scripts.critical_path
  python3 -m scripts.critical_path --top 1     # one gap per token
  python3 -m scripts.critical_path --top 3
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens  # noqa: E402

WORKSHEETS_DIR = _REPO_ROOT / "data" / "manual_research"
REPORTS_DIR = _REPO_ROOT / "reports"
CONFIG_PATH = _REPO_ROOT / "config.yaml"
OUTPUT_PATH = WORKSHEETS_DIR / "critical_path.md"


def gap_impact(gap: dict, weights: dict[str, float]) -> float:
    """Estimate the score swing this gap could produce, in weighted-conviction points."""
    rules = gap.get("delta_rules", []) or []
    if not rules:
        return 0.0
    max_swing = max(abs(int(r.get("delta_points", 0))) for r in rules)
    # Use the agent named in the first rule as the canonical target (rules usually share an agent)
    agent = rules[0].get("agent_score") or rules[0].get("agent")
    weight = weights.get(agent, 0.0)
    return round(max_swing * weight, 2)


def collect_token_critical_gaps(symbol: str, weights: dict[str, float], top_n: int) -> dict[str, Any]:
    """Return {symbol, score, verdict, top_gaps[]} for one token."""
    ws_path = WORKSHEETS_DIR / f"{symbol}.json"
    conv_path = REPORTS_DIR / symbol / "conviction.json"
    if not ws_path.exists():
        return None
    ws = json.loads(ws_path.read_text())

    score = None
    verdict = None
    if conv_path.exists():
        try:
            c = json.loads(conv_path.read_text())
            score = c.get("weighted_conviction")
            verdict = c.get("final_verdict")
        except Exception:                                  # noqa: BLE001
            pass

    unfilled = [g for g in ws.get("schema_overlay_gaps", []) if g.get("value") is None]
    scored = [
        (gap_impact(g, weights), g)
        for g in unfilled
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [
        {
            "field": g["field"],
            "impact_pts": imp,
            "what_it_measures": g.get("what_it_measures", ""),
            "where_to_retrieve": g.get("where_to_retrieve", []),
            "verdict_impact_if_filled": g.get("verdict_impact_if_filled", ""),
            "target_agent": (
                (g.get("delta_rules") or [{}])[0].get("agent_score")
                or (g.get("delta_rules") or [{}])[0].get("agent")
            ),
        }
        for imp, g in scored[:top_n]
    ]

    return {
        "symbol": symbol,
        "category": ws.get("category"),
        "score": score,
        "verdict": verdict,
        "filled_count": len(ws.get("schema_overlay_gaps", [])) - len(unfilled),
        "total_count": len(ws.get("schema_overlay_gaps", [])),
        "top_gaps": top,
    }


def score_band(score: int | None) -> str:
    if score is None or score == 0:
        return "z_nodata"
    if score >= 75:
        return "a_strong"
    if score >= 65:
        return "b_solid"
    if score >= 55:
        return "c_neutral"
    if score >= 45:
        return "d_weak"
    return "e_bottom"


BAND_LABELS = {
    "a_strong":  "## STRONG (≥75) — push to full conviction",
    "b_solid":   "## SOLID (65–74) — within reach of STRONG",
    "c_neutral": "## NEUTRAL (55–64) — swing tokens; biggest leverage from manual work",
    "d_weak":    "## WEAK (45–54) — need positive surprises to be actionable",
    "e_bottom":  "## BOTTOM (<45) — large hill to climb; deprioritize",
    "z_nodata":  "## NO DATA — needs `collect` run before manual research is useful",
}


def render_markdown(rows: list[dict[str, Any]], top_n: int) -> str:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    weights = config["agent_weights"]
    lines = [
        "# Critical Path — Manual Research TODO",
        "",
        f"Top {top_n} highest-impact unfilled gap(s) per token. Impact = "
        "max(|delta_points|) × agent_weight, in weighted-conviction points.",
        "",
        "Process for each token:",
        "1. Open `data/manual_research/{SYMBOL}.json`",
        "2. Locate the gap by `field` name",
        "3. Retrieve the value from one of the `where_to_retrieve` sources",
        "4. Set `value` (and optionally `notes` / `retrieved_at`) in the worksheet",
        "5. Re-run `python3 -m scripts.reconcile_manual_research SYMBOL`",
        "",
        f"Agent weights from config.yaml: " + ", ".join(f"{a}={w:.0%}" for a, w in weights.items()),
        "",
    ]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        b = score_band(r["score"])
        grouped.setdefault(b, []).append(r)

    for band in sorted(BAND_LABELS):
        if band not in grouped:
            continue
        lines.append(BAND_LABELS[band])
        lines.append("")
        # Sort tokens within band by descending top-gap impact
        ranked = sorted(
            grouped[band],
            key=lambda r: (r["top_gaps"][0]["impact_pts"] if r["top_gaps"] else 0),
            reverse=True,
        )
        for r in ranked:
            score_str = r["score"] if r["score"] is not None else "—"
            lines.append(
                f"### {r['symbol']} — score {score_str}, "
                f"{r['filled_count']}/{r['total_count']} gaps filled, "
                f"category `{r['category']}`"
            )
            if not r["top_gaps"]:
                lines.append("- _All gaps already filled or no rule-bearing gaps remain._")
                lines.append("")
                continue
            for g in r["top_gaps"]:
                lines.append(
                    f"- **`{g['field']}`** "
                    f"(impact {g['impact_pts']:.1f}pt → {g['target_agent']})"
                )
                lines.append(f"  - _{g['what_it_measures']}_")
                if g["where_to_retrieve"]:
                    lines.append(f"  - **Retrieve from:** " +
                                 "; ".join(f"`{u}`" for u in g["where_to_retrieve"][:3]))
                lines.append(f"  - **Verdict impact:** {g['verdict_impact_if_filled']}")
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--top", type=int, default=2,
                   help="Number of top gaps per token (default: 2)")
    args = p.parse_args(argv)

    config = yaml.safe_load(CONFIG_PATH.read_text())
    weights = config["agent_weights"]

    rows: list[dict[str, Any]] = []
    for sym in tokens.all_symbols():
        row = collect_token_critical_gaps(sym, weights, args.top)
        if row is not None:
            rows.append(row)

    md = render_markdown(rows, args.top)
    OUTPUT_PATH.write_text(md)
    print(f"Wrote {OUTPUT_PATH}")
    print(f"  ({sum(len(r['top_gaps']) for r in rows)} total critical-path items across {len(rows)} tokens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
