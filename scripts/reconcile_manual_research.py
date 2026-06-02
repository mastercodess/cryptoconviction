"""
Reconcile a token's manually-researched values into a revised conviction score.

Reads:
  - data/manual_research/{SYMBOL}.json  — the filled worksheet
  - reports/{SYMBOL}/conviction.json    — orchestrator's baseline scores
  - config.yaml                          — agent weights

For each gap with `value` populated, evaluates the gap's `delta_rules`:
  - For each rule whose `when` expression evaluates True, applies the
    `delta_points` to the named agent's composite_score.
  - Per-agent aggregate is clamped to [-PER_AGENT_MAX, +PER_AGENT_MAX] so
    a single worksheet can't trivially swing 50→100.

Also honors `manual_adjustment` blocks if the user adds them:
  "manual_adjustment": {"agent": "moat", "delta_points": -5, "reason": "..."}

Output:
  reports/{SYMBOL}/conviction_reconciled.json — revised verdict + per-gap audit
  reports/{SYMBOL}/conviction_reconciled.md   — human-readable diff vs baseline

Run:
  python3 -m scripts.reconcile_manual_research AAVE
  python3 -m scripts.reconcile_manual_research --all  # all worksheets with ≥1 filled value
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REPORTS_DIR = _REPO_ROOT / "reports"
WORKSHEETS_DIR = _REPO_ROOT / "data" / "manual_research"
CONFIG_PATH = _REPO_ROOT / "config.yaml"

PER_AGENT_MAX_DELTA = 25  # max ± points any single agent can move per reconciliation
SAFE_EVAL_NAMES = {"len": len, "max": max, "min": min, "sum": sum, "abs": abs}


def _safe_eval(expr: str, value: Any) -> bool:
    """Evaluate a delta-rule `when` expression with `value` bound. Returns False on any failure."""
    try:
        return bool(eval(  # noqa: S307 — restricted eval namespace
            expr,
            {"__builtins__": {}, **SAFE_EVAL_NAMES},
            {"value": value},
        ))
    except Exception:
        return False


def _verdict_band(score: int, auto_reject: bool) -> str:
    if auto_reject:
        return "AVOID"
    if score >= 75:
        return "STRONG_CONVICTION"
    if score >= 55:
        return "CONDITIONAL"
    return "AVOID"


def _position_size(score: int, target_pct: float, auto_reject: bool) -> float:
    if auto_reject:
        return 0.0
    return round(max(0.0, (score - 50) / 50.0) * target_pct, 2)


def reconcile_token(
    symbol: str,
    *,
    keep_auto_reject: bool = True,
    apply_manual_adjustments: bool = False,
) -> dict[str, Any]:
    """Reconcile one token. Returns the revised verdict dict."""
    symbol = symbol.upper()

    worksheet_path = WORKSHEETS_DIR / f"{symbol}.json"
    if not worksheet_path.exists():
        raise FileNotFoundError(f"No worksheet at {worksheet_path}")
    ws = json.loads(worksheet_path.read_text())

    conviction_path = REPORTS_DIR / symbol / "conviction.json"
    if not conviction_path.exists():
        raise FileNotFoundError(f"No conviction at {conviction_path}")
    baseline = json.loads(conviction_path.read_text())

    config = yaml.safe_load(CONFIG_PATH.read_text())
    weights = config["agent_weights"]
    target_pct = float(config.get("target_position_pct", 5))

    baseline_scores: dict[str, int] = dict(baseline.get("category_scorecard", {}))
    # Only agents that actually ran (have a baseline score) participate in the
    # reconciliation. Agents in `missing_agents` aren't given a phantom score
    # by manual research — that would be hallucinating a number we don't have.
    loaded_agents = set(baseline_scores)
    deltas_per_agent: dict[str, int] = {a: 0 for a in loaded_agents}
    skipped_rules_for_missing_agents: list[dict[str, Any]] = []
    audit_log: list[dict[str, Any]] = []
    filled_count = 0
    skipped_count = 0

    for gap in ws.get("schema_overlay_gaps", []):
        field = gap.get("field")
        value = gap.get("value")
        if value is None:
            skipped_count += 1
            continue
        filled_count += 1

        rules = gap.get("delta_rules", []) or []
        matched_rules: list[dict[str, Any]] = []
        for rule in rules:
            when = rule.get("when", "")
            if _safe_eval(when, value):
                agent = rule.get("agent_score") or rule.get("agent")
                pts = int(rule.get("delta_points", 0))
                if agent in loaded_agents:
                    deltas_per_agent[agent] += pts
                    matched_rules.append({
                        "agent": agent,
                        "delta_points": pts,
                        "when_matched": when,
                        "reason": rule.get("reason", ""),
                    })
                else:
                    # Rule targets an agent that didn't run for this token.
                    # Record it but don't apply — would be inventing a score.
                    matched_rules.append({
                        "agent": agent,
                        "delta_points": 0,
                        "when_matched": when + " (skipped — agent not loaded)",
                        "reason": rule.get("reason", ""),
                    })
                    skipped_rules_for_missing_agents.append({
                        "field": field, "agent": agent, "pts": pts,
                    })

        manual = gap.get("manual_adjustment")
        if manual:
            if apply_manual_adjustments:
                agent = manual.get("agent")
                pts = int(manual.get("delta_points", 0))
                if agent in loaded_agents:
                    deltas_per_agent[agent] += pts
                matched_rules.append({
                    "agent": agent,
                    "delta_points": pts,
                    "when_matched": "manual_adjustment",
                    "reason": manual.get("reason", "user override"),
                })
            else:
                # Recorded but not applied — preserved for human review
                matched_rules.append({
                    "agent": manual.get("agent"),
                    "delta_points": 0,
                    "when_matched": "manual_adjustment (unweighted, see --apply-manual-adjustments)",
                    "reason": manual.get("reason", "user override deferred"),
                })

        audit_log.append({
            "field": field,
            "value": value,
            "matched_rules": matched_rules,
            "needs_judgment": not matched_rules and not manual,
            "verdict_impact_text": gap.get("verdict_impact_if_filled", ""),
            "notes": gap.get("notes", ""),
        })

    # Clamp per-agent deltas
    clamped_deltas: dict[str, int] = {}
    for a, d in deltas_per_agent.items():
        clamped_deltas[a] = max(-PER_AGENT_MAX_DELTA, min(PER_AGENT_MAX_DELTA, d))

    # Apply to baseline scores, bounded [0, 100]. Only loaded agents.
    revised_scores: dict[str, int] = {}
    for a in loaded_agents:
        base = baseline_scores[a]
        revised_scores[a] = max(0, min(100, base + clamped_deltas.get(a, 0)))

    # Recompute weighted conviction using the same rebalancing the orchestrator
    # does: divide each loaded agent's weight by sum-of-loaded-weights.
    used_weights = {a: w for a, w in weights.items() if a in revised_scores}
    used_sum = sum(used_weights.values()) or 1.0
    rebalanced = {a: w / used_sum for a, w in used_weights.items()}
    weighted = sum(revised_scores[a] * rebalanced[a] for a in revised_scores)
    revised_conviction = int(round(weighted))

    # Auto-reject inheritance: by default the reconciler doesn't override
    # the orchestrator's red-flag rules (security tier, freshness, etc.) —
    # those gates are about data quality, which manual research doesn't
    # change. So if baseline was auto-rejected, the reconciled verdict
    # remains AVOID unless --no-keep-auto-reject is passed.
    auto_reject = bool(baseline.get("auto_reject_triggered")) and keep_auto_reject
    auto_reject_reason = baseline.get("auto_reject_reason") if auto_reject else None

    revised_verdict = _verdict_band(revised_conviction, auto_reject)
    position = _position_size(revised_conviction, target_pct, auto_reject)

    return {
        "token_symbol": symbol,
        "reconciliation_run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "baseline_conviction": baseline.get("weighted_conviction"),
        "baseline_verdict": baseline.get("final_verdict"),
        "baseline_scorecard": baseline_scores,
        "revised_conviction": revised_conviction,
        "revised_verdict": revised_verdict,
        "revised_scorecard": revised_scores,
        "deltas_per_agent": clamped_deltas,
        "raw_unclamped_deltas": deltas_per_agent,
        "auto_reject_inherited": auto_reject,
        "auto_reject_reason": auto_reject_reason,
        "recommended_position_pct": position,
        "filled_gaps_count": filled_count,
        "unfilled_gaps_count": skipped_count,
        "audit_log": audit_log,
        "weights_used": {a: w for a, w in weights.items() if a in loaded_agents},
        "rules_skipped_for_missing_agents": skipped_rules_for_missing_agents,
    }


def render_markdown(report: dict[str, Any]) -> str:
    base = report["baseline_conviction"]
    rev = report["revised_conviction"]
    delta = (rev or 0) - (base or 0)
    arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
    lines = [
        f"# {report['token_symbol']} — Reconciled Conviction",
        f"_Reconciled {report['reconciliation_run_at']} UTC_",
        "",
        f"## Baseline (orchestrator) vs. Reconciled (manual research)",
        "",
        f"- **Baseline conviction**: {base}/100 ({report['baseline_verdict']})",
        f"- **Revised conviction**: {rev}/100 {arrow} {abs(delta):+d} ({report['revised_verdict']})",
        f"- **Recommended position**: {report['recommended_position_pct']}% of portfolio",
    ]
    if report["auto_reject_inherited"]:
        lines.append(
            f"- **Note**: baseline auto-reject inherited (`{report['auto_reject_reason']}`). "
            "Manual research changes scores but doesn't override data-quality gates. "
            "Re-collect to fix freshness."
        )
    lines += [
        "",
        f"- **Gaps filled**: {report['filled_gaps_count']} / "
        f"{report['filled_gaps_count'] + report['unfilled_gaps_count']}",
        "",
        "## Per-agent score deltas",
        "",
        "| Agent | Baseline | Δ | Revised |",
        "|---|---:|---:|---:|",
    ]
    for agent, w in report["weights_used"].items():
        b = report["baseline_scorecard"].get(agent, "-")
        d = report["deltas_per_agent"].get(agent, 0)
        r = report["revised_scorecard"].get(agent, "-")
        sign = "+" if d > 0 else ""
        lines.append(f"| {agent} ({w:.0%}) | {b} | {sign}{d} | {r} |")

    needs_judgment = [a for a in report["audit_log"] if a["needs_judgment"]]
    auto_applied = [a for a in report["audit_log"] if not a["needs_judgment"]]

    if auto_applied:
        lines += ["", "## Auto-applied rules", ""]
        for a in auto_applied:
            lines.append(f"### `{a['field']}` = {a['value']!r}")
            if a.get("notes"):
                lines.append(f"_{a['notes']}_")
            for r in a["matched_rules"]:
                pts = r["delta_points"]
                sign = "+" if pts >= 0 else ""
                lines.append(
                    f"- `{r['when_matched']}` → {r['agent']} {sign}{pts} ({r['reason']})"
                )
            lines.append("")

    if needs_judgment:
        lines += ["## Filled gaps needing human judgment (no rule matched)", ""]
        for a in needs_judgment:
            lines.append(f"### `{a['field']}` = {a['value']!r}")
            lines.append(f"_{a['verdict_impact_text']}_")
            lines.append(
                "Add a `manual_adjustment` block to this gap in the worksheet to encode your "
                "judgment, e.g.:"
            )
            lines.append('```json')
            lines.append('"manual_adjustment": {"agent": "moat", "delta_points": -5, "reason": "..."}')
            lines.append('```')
            lines.append("")

    return "\n".join(lines) + "\n"


def write_outputs(symbol: str, report: dict[str, Any]) -> tuple[pathlib.Path, pathlib.Path]:
    out_dir = REPORTS_DIR / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "conviction_reconciled.json"
    md_path = out_dir / "conviction_reconciled.md"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    md_path.write_text(render_markdown(report))
    return json_path, md_path


def find_worksheets_with_filled_values() -> list[str]:
    syms: list[str] = []
    for p in sorted(WORKSHEETS_DIR.glob("*.json")):
        try:
            ws = json.loads(p.read_text())
        except Exception:
            continue
        if any(g.get("value") is not None for g in ws.get("schema_overlay_gaps", [])):
            syms.append(p.stem)
    return syms


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("symbol", nargs="?")
    p.add_argument("--all", action="store_true",
                   help="Reconcile every worksheet with ≥1 filled value")
    p.add_argument("--ignore-auto-reject", action="store_true",
                   help="Don't inherit orchestrator auto-reject; let manual score stand")
    p.add_argument("--apply-manual-adjustments", action="store_true",
                   help="Include manual_adjustment blocks in delta computation "
                        "(default: blocks are preserved but unweighted)")
    p.add_argument("--include-empty", action="store_true",
                   help="Reconcile even worksheets with zero filled values "
                        "(produces baseline-only reports for completeness)")
    args = p.parse_args(argv)

    if args.all:
        if args.include_empty:
            syms = [p.stem for p in sorted(WORKSHEETS_DIR.glob("*.json"))]
        else:
            syms = find_worksheets_with_filled_values()
        if not syms:
            print("No worksheets with filled values found.")
            return 0
    elif args.symbol:
        syms = [args.symbol.upper()]
    else:
        p.error("provide a symbol or --all")

    for s in syms:
        try:
            report = reconcile_token(
                s,
                keep_auto_reject=not args.ignore_auto_reject,
                apply_manual_adjustments=args.apply_manual_adjustments,
            )
        except FileNotFoundError as e:
            print(f"{s}: {e}", file=sys.stderr)
            continue
        json_path, md_path = write_outputs(s, report)
        b = report["baseline_conviction"]
        r = report["revised_conviction"]
        delta = (r or 0) - (b or 0)
        sign = "+" if delta >= 0 else ""
        print(f"=== {s}: {b} → {r} ({sign}{delta}, {report['revised_verdict']}) ===")
        print(f"  json: {json_path}")
        print(f"  md:   {md_path}")
        if report["filled_gaps_count"] == 0:
            print("  (no gaps filled — re-run after populating values)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
