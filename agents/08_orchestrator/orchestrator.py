"""
Agent 8 — Investment Conviction Synthesizer.

Reads the seven specialist JSONs from reports/{SYMBOL}/agent_0X_*.json,
applies the weighted scoring + red-flag rules from config.yaml, and writes
the final conviction report to reports/{SYMBOL}/conviction.json (and a
human-readable Markdown twin at reports/{SYMBOL}/conviction.md).

This agent does NOT need an RLM: every input is structured and small.
We only call the LLM once at the end to write the bull/bear narrative.
That keeps the orchestrator deterministic and cheap.

Run:
    python -m agents.08_orchestrator.orchestrator LINK
    python -m agents.08_orchestrator.orchestrator --all

Behavior on missing inputs: if any specialist's JSON is missing or
errored, the orchestrator records this in the report and proceeds with
reduced weighting (rebalances among the agents that DID produce output).
The user is told which agents are missing via the `missing_agents` field
in the output JSON and a "Trust signals" section in the markdown.

Two fail-closed rules (from config.yaml `red_flags`) prevent silent
trust failures:
  - `require_security_agent: true` — if agent_03_security.json is missing,
    auto-reject with reason "security agent missing". Stops the security
    threshold gate from being silently bypassed by a falsy-dict check.
  - `min_agent_coverage_pct: 0.70` — if loaded-agent coverage (sum of
    used weights / total config weight) is below this fraction, auto-reject
    with reason "coverage X% < threshold". Prevents 2-of-7 or 3-of-7
    syntheses from emitting an actionable verdict.

Agents whose rationale contains markers like "RLM did not converge",
"forced_offline_fallback", or "max_iters_reached" are recorded in
`fallback_agents`. The orchestrator does not auto-drop them (a future
patch may), but the field flags that their composite_score is heuristic
rather than researched.

Red-flag rules evaluated by `_check_red_flags` (all from config.yaml,
all concatenate into a single auto_reject_reason when multiple fire):
  1. `require_security_agent`: missing agent_03 → AVOID (fail-closed).
  2. `reject_if_security_below`: agent_03.security_tier < N → AVOID.
  3. `min_agent_coverage_pct`: loaded-weight fraction < N → AVOID.
  4. `max_data_age_hours`: any agent's data_as_of > N hours → AVOID.
  5. `reject_if_holder_concentration_above`: top-10 share > N → AVOID.
  6. `reject_if_unlock_pressure_next_90d_above`: > N share unlocking → AVOID.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Any

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.freshness import classify_agents                       # noqa: E402
from shared.schemas import FinalVerdict                            # noqa: E402

REPORTS_DIR = _REPO_ROOT / "reports"
CONFIG_PATH = _REPO_ROOT / "config.yaml"

AGENT_FILE_MAP = {
    "tokenomics": "agent_01_tokenomics.json",
    "revenue":    "agent_02_revenue.json",
    "security":   "agent_03_security.json",
    "onchain":    "agent_04_onchain.json",
    "team":       "agent_05_team.json",
    "moat":       "agent_06_moat.json",
    "macro":      "agent_07_macro.json",
}


# ─── Loading + normalization ────────────────────────────────────────────

def _load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _load_specialist_outputs(symbol: str) -> tuple[dict[str, dict], list[str]]:
    """Returns (loaded, missing). loaded[agent_name] = the agent's JSON dict."""
    out_dir = REPORTS_DIR / symbol
    loaded: dict[str, dict] = {}
    missing: list[str] = []
    for agent, fname in AGENT_FILE_MAP.items():
        path = out_dir / fname
        if path.exists():
            try:
                loaded[agent] = json.loads(path.read_text())
            except Exception as e:                             # noqa: BLE001
                missing.append(f"{agent} (parse error: {e})")
        else:
            missing.append(agent)
    return loaded, missing


# ─── Scoring ────────────────────────────────────────────────────────────

# Markers that indicate an agent's rationale was produced by a fallback
# heuristic rather than a converged RLM run. Lowercased substring match.
_FALLBACK_MARKERS = (
    "rlm did not converge",
    "forced_offline_fallback",
    "forced offline fallback",
    "max_iters_reached",
    "max iters reached",
    "fallback applied",
)


def _detect_fallback_agents(loaded: dict[str, dict]) -> list[str]:
    """List agents whose rationale flags themselves as fallback heuristics."""
    out: list[str] = []
    for agent, output in loaded.items():
        rationale = str(output.get("rationale", "")).lower()
        if any(marker in rationale for marker in _FALLBACK_MARKERS):
            out.append(agent)
    return out


def _weighted_score(
    loaded: dict[str, dict],
    weights: dict[str, float],
) -> tuple[int, dict[str, int], dict[str, float], float]:
    """
    Compute the weighted 0-100 conviction.

    If some agents are missing, redistribute their weight proportionally
    among the agents that DID return a composite_score. This matters: with
    7 agents, losing one shouldn't cap conviction at 86% — that would
    distort the verdict.

    Returns (weighted_score, category_scorecard, rebalanced_weights, coverage_pct)
    where coverage_pct = sum(used_weights) / sum(all_weights) — the fraction
    of intended weight that actually contributed to the score.
    """
    cats: dict[str, int] = {}
    used_weights: dict[str, float] = {}
    for agent, w in weights.items():
        if agent in loaded and loaded[agent].get("composite_score") is not None:
            cats[agent] = int(loaded[agent]["composite_score"])
            used_weights[agent] = w
    total_weight = sum(weights.values())
    used_sum = sum(used_weights.values())
    coverage_pct = (used_sum / total_weight) if total_weight else 0.0
    if not used_weights:
        return 0, cats, {}, coverage_pct
    rebalanced = {a: w / used_sum for a, w in used_weights.items()}
    weighted = sum(cats[a] * rebalanced[a] for a in cats)
    return int(round(weighted)), cats, rebalanced, coverage_pct


def _check_red_flags(
    loaded: dict[str, dict],
    rules: dict,
    coverage_pct: float,
    stale_agents: list[str],
) -> tuple[bool, str | None]:
    """Hard veto rules. See module docstring for the full list."""
    reasons: list[str] = []
    sec = loaded.get("security") or {}

    if rules.get("require_security_agent") and not sec:
        reasons.append(
            "security agent did not run — cannot verify "
            f"security_tier ≥ {rules.get('reject_if_security_below', '?')}"
        )

    if "reject_if_security_below" in rules and sec:
        thr = rules["reject_if_security_below"]
        if (sec.get("security_tier") or 99) < thr:
            reasons.append(f"security_tier={sec.get('security_tier')} < {thr}")

    if "min_agent_coverage_pct" in rules:
        thr = rules["min_agent_coverage_pct"]
        if coverage_pct < thr:
            reasons.append(
                f"agent coverage {coverage_pct:.0%} < {thr:.0%} threshold "
                "(too few specialist agents contributed)"
            )

    if rules.get("max_data_age_hours") and stale_agents:
        reasons.append(
            f"stale data: {', '.join(stale_agents)} exceed "
            f"{rules['max_data_age_hours']}h freshness threshold"
        )

    tok = loaded.get("tokenomics") or {}
    if "reject_if_holder_concentration_above" in rules and tok:
        thr = rules["reject_if_holder_concentration_above"]
        if (tok.get("top10_holding_pct") or 0) > thr:
            reasons.append(f"top10 concentration {tok.get('top10_holding_pct')} > {thr}")
    if "reject_if_unlock_pressure_next_90d_above" in rules and tok:
        thr = rules["reject_if_unlock_pressure_next_90d_above"]
        if (tok.get("unlock_pressure_next_90d_pct") or 0) > thr:
            reasons.append(f"90d unlock {tok.get('unlock_pressure_next_90d_pct')} > {thr}")

    if reasons:
        return True, "; ".join(reasons)
    return False, None


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
    # Linear scale: score 50 -> 50% of target, score 100 -> 100%.
    return round(max(0.0, (score - 50) / 50.0) * target_pct, 2)


# ─── Narrative (single LLM call) ────────────────────────────────────────

_NARRATIVE_PROMPT = """\
You are the Investment Conviction Synthesizer. You will be given the 7
specialist agent outputs as JSON. Produce the bull case, bear case,
invalidation conditions, and monitoring checklist for {symbol}.

Constraints:
  - Bull case: 3 bullets, each ≤ 25 words, each citing a number from the data.
  - Bear case: 3 bullets, each ≤ 25 words, each citing a specific risk.
  - Invalidation: 3 conditions that would force you to sell — concrete,
    measurable, time-bounded.
  - Monitoring: 3 on-chain or fundamental signals to watch post-entry.
  - DO NOT recommend a position size — that's computed elsewhere.
  - Reply with ONLY this JSON (no preamble):
    {{
      "bull_case": ["...", "...", "..."],
      "bear_case": ["...", "...", "..."],
      "invalidation_conditions": ["...", "...", "..."],
      "monitoring_checklist": ["...", "...", "..."]
    }}

Specialist outputs:
{outputs_json}
"""


def _generate_narrative(symbol: str, loaded: dict[str, dict]) -> dict[str, list[str]]:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {
            "bull_case": ["[run with ANTHROPIC_API_KEY set to generate]"],
            "bear_case": ["[same — Sonnet narrative requires API key]"],
            "invalidation_conditions": ["[same]"],
            "monitoring_checklist": ["[same]"],
        }
    from shared.llm_client import sub_lm, research_json
    payload = json.dumps(loaded, indent=2, default=str)
    # Truncate per agent if total too large
    if len(payload) > 60000:
        slim = {k: {kk: vv for kk, vv in v.items() if kk in
                    ("composite_score", "rationale", "value_accrual_verdict",
                     "growth_trend", "security_tier", "trust_tier",
                     "competitive_threat", "cycle_phase",
                     "concentration_risk_flag", "unlock_pressure_next_90d_pct",
                     "growth_authenticity_verdict", "moat_strength_score",
                     "macro_rating", "entry_timing_risk")}
                for k, v in loaded.items()}
        payload = json.dumps(slim, indent=2, default=str)
    raw = sub_lm(
        _NARRATIVE_PROMPT.format(symbol=symbol, outputs_json=payload),
        max_tokens=2048,
    )
    parsed = research_json(raw) if raw.strip().startswith("{") else None
    if not parsed:
        # fallback parse via the same brace-walk used in research_json
        parsed = research_json("Echo this JSON exactly:\n" + raw)
    return parsed or {
        "bull_case": ["[narrative parse failed — check raw LLM reply]"],
        "bear_case": ["[same]"],
        "invalidation_conditions": ["[same]"],
        "monitoring_checklist": ["[same]"],
    }


# ─── Markdown rendering ─────────────────────────────────────────────────

def _render_markdown(verdict: FinalVerdict, weights_used: dict[str, float]) -> str:
    v = verdict
    lines = [
        f"# {v.token_symbol} — Conviction Report",
        f"_Generated {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} UTC_",
        "",
        f"## Verdict: **{v.final_verdict}**",
        f"- **Weighted conviction**: {v.weighted_conviction}/100",
        f"- **Recommended position**: {v.recommended_position_pct}% of portfolio",
        f"- **Agent coverage**: {v.coverage_pct:.0%} of intended config weight loaded",
    ]
    if v.auto_reject_triggered:
        lines.append(f"- **AUTO-REJECTED**: {v.auto_reject_reason}")

    if v.missing_agents or v.fallback_agents or v.coverage_pct < 1.0 or v.stale_agents:
        lines += ["", "## ⚠ Trust signals"]
        if v.missing_agents:
            lines.append(
                f"- **Missing agents** ({len(v.missing_agents)}): "
                f"{', '.join(v.missing_agents)}. "
                "Score was computed on the remaining agents with rebalanced weights."
            )
        if v.fallback_agents:
            lines.append(
                f"- **Fallback-scored agents** ({len(v.fallback_agents)}): "
                f"{', '.join(v.fallback_agents)}. "
                "These agents' RLM did not converge; their composite_score is a "
                "heuristic default, not researched output."
            )
        if v.stale_agents:
            lines.append(
                f"- **Stale agents** ({len(v.stale_agents)}): "
                f"{', '.join(v.stale_agents)}. "
                "Data older than the configured freshness threshold; rerun collect for these."
            )
        if v.coverage_pct < 1.0:
            absent = (1.0 - v.coverage_pct) * 100
            lines.append(
                f"- Coverage is {v.coverage_pct:.1%}; {absent:.1f}% of original "
                "config weight is absent and was redistributed."
            )

    if v.data_as_of_per_agent:
        lines += ["", "## Data freshness"]
        for agent, ts in sorted(v.data_as_of_per_agent.items()):
            lines.append(f"- {agent}: {ts}")

    lines += ["", "## Category scorecard", "", "| Agent | Score | Weight |", "|---|---:|---:|"]
    for agent, score in v.category_scorecard.items():
        w = weights_used.get(agent, 0)
        lines.append(f"| {agent} | {score} | {w:.0%} |")
    lines += [
        "",
        "## Bull case",
        *[f"- {b}" for b in v.bull_case],
        "",
        "## Bear case",
        *[f"- {b}" for b in v.bear_case],
        "",
        "## Invalidation conditions",
        *[f"- {b}" for b in v.invalidation_conditions],
        "",
        "## Post-entry monitoring",
        *[f"- {b}" for b in v.monitoring_checklist],
    ]
    return "\n".join(lines) + "\n"


# ─── Public entry point ─────────────────────────────────────────────────

def run(symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    tokens.get(symbol)

    cfg = _load_config()
    weights = cfg["agent_weights"]
    rules = cfg.get("red_flags", {})
    target_pct = float(cfg.get("target_position_pct", 5))

    loaded, missing = _load_specialist_outputs(symbol)
    score, scorecard, weights_used, coverage_pct = _weighted_score(loaded, weights)
    max_age = rules.get("max_data_age_hours")
    if max_age:
        _fresh, stale_agents, data_as_of_per_agent = classify_agents(
            loaded, max_hours=max_age
        )
    else:
        stale_agents, data_as_of_per_agent = [], {}
    auto_reject, reason = _check_red_flags(loaded, rules, coverage_pct, stale_agents)
    fallback = _detect_fallback_agents(loaded)

    narrative = _generate_narrative(symbol, loaded)

    final = FinalVerdict(
        token_symbol=symbol,
        weighted_conviction=score,
        final_verdict=_verdict_band(score, auto_reject),
        bull_case=narrative.get("bull_case", []),
        bear_case=narrative.get("bear_case", []),
        invalidation_conditions=narrative.get("invalidation_conditions", []),
        recommended_position_pct=_position_size(score, target_pct, auto_reject),
        monitoring_checklist=narrative.get("monitoring_checklist", []),
        category_scorecard=scorecard,
        auto_reject_triggered=auto_reject,
        auto_reject_reason=reason,
        missing_agents=missing,
        fallback_agents=fallback,
        coverage_pct=round(coverage_pct, 4),
        stale_agents=stale_agents,
        data_as_of_per_agent=data_as_of_per_agent,
    )

    out_dir = REPORTS_DIR / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "conviction.json"
    md_path = out_dir / "conviction.md"
    json_path.write_text(json.dumps(final.model_dump(), indent=2))
    md_path.write_text(_render_markdown(final, weights_used))
    return {
        "ok": True,
        "json": str(json_path),
        "markdown": str(md_path),
        "missing_agents": missing,
        "score": score,
        "verdict": final.final_verdict,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbol", nargs="?")
    p.add_argument("--all", action="store_true")
    args = p.parse_args(argv)
    syms = tokens.all_symbols() if args.all else [args.symbol.upper()] if args.symbol else None
    if not syms:
        p.error("provide a symbol or --all")
    for s in syms:
        try:
            r = run(s)
            print(f"\n=== {s}: {r['verdict']} (score={r['score']}) ===")
            if r["missing_agents"]:
                print(f"  missing agents: {r['missing_agents']}")
            print(f"  json: {r['json']}")
            print(f"  md:   {r['markdown']}")
        except Exception as e:                                 # noqa: BLE001
            print(f"{s}: ERROR — {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
