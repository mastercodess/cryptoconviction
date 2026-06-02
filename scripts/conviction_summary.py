"""
Ranked summary of all token conviction scores.

Reads `reports/{SYMBOL}/conviction.json` and (if it exists)
`reports/{SYMBOL}/conviction_reconciled.json`, then emits a single sorted
table by score, plus a CSV for downstream filtering.

This is the "wide view" — when you're triaging 31 tokens and want to see
where everything stands by raw conviction number, irrespective of the
verdict-band (AVOID/CONDITIONAL/STRONG) classification.

Run:
  python3 -m scripts.conviction_summary           # print to stdout + write summary.md/.csv
  python3 -m scripts.conviction_summary --md      # markdown only
  python3 -m scripts.conviction_summary --csv     # CSV only
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens  # noqa: E402

REPORTS_DIR = _REPO_ROOT / "reports"
SUMMARY_DIR = _REPO_ROOT / "reports"


def collect_token_row(symbol: str) -> dict[str, Any] | None:
    sdir = REPORTS_DIR / symbol
    if not sdir.exists():
        return None
    baseline_path = sdir / "conviction.json"
    reconciled_path = sdir / "conviction_reconciled.json"

    baseline: dict[str, Any] = {}
    if baseline_path.exists():
        try:
            baseline = json.loads(baseline_path.read_text())
        except Exception:                                          # noqa: BLE001
            pass

    reconciled: dict[str, Any] | None = None
    if reconciled_path.exists():
        try:
            reconciled = json.loads(reconciled_path.read_text())
        except Exception:                                          # noqa: BLE001
            pass

    base_score = baseline.get("weighted_conviction")
    rec_score = reconciled.get("revised_conviction") if reconciled else None
    final_score = rec_score if rec_score is not None else base_score

    base_verdict = baseline.get("final_verdict")
    rec_verdict = reconciled.get("revised_verdict") if reconciled else None

    missing = baseline.get("missing_agents") or []
    stale = baseline.get("stale_agents") or []
    fallback = baseline.get("fallback_agents") or []
    coverage = baseline.get("coverage_pct")

    try:
        t = tokens.get(symbol)
        category = t.category
    except KeyError:
        category = "?"

    return {
        "symbol": symbol,
        "category": category,
        "score": final_score,
        "baseline_score": base_score,
        "reconciled_score": rec_score,
        "delta": (rec_score - base_score) if (rec_score is not None and base_score is not None) else 0,
        "verdict": rec_verdict or base_verdict or "?",
        "auto_reject_reason": baseline.get("auto_reject_reason"),
        "missing_agents": missing,
        "stale_agents": stale,
        "fallback_agents": fallback,
        "coverage_pct": coverage,
        "agents_loaded": 7 - len(missing),
        "scorecard": baseline.get("category_scorecard", {}),
    }


def collect_all() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sym in tokens.all_symbols():
        row = collect_token_row(sym)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: (r["score"] if r["score"] is not None else -1), reverse=True)
    return rows


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Conviction Scores — Ranked Summary",
        "",
        "Score = reconciled where available, else orchestrator baseline.",
        "Verdict shown for reference but ranking is by raw score.",
        "",
        "| Rank | Token | Category | Score | Δ | Verdict | Coverage | Notes |",
        "|---:|---|---|---:|---:|---|---|---|",
    ]
    for i, r in enumerate(rows, start=1):
        score = r["score"] if r["score"] is not None else "—"
        delta = r["delta"]
        delta_str = f"{delta:+d}" if delta else "·"
        notes = []
        if r["coverage_pct"] is not None and r["coverage_pct"] < 1.0:
            notes.append(f"{r['agents_loaded']}/7 agents")
        if r["fallback_agents"]:
            notes.append(f"fb:{','.join(r['fallback_agents'])[:30]}")
        if r["stale_agents"]:
            notes.append(f"stale:{len(r['stale_agents'])}")
        if r["auto_reject_reason"]:
            short = r["auto_reject_reason"].split(";")[0][:60]
            notes.append(short)
        cov = f"{int((r['coverage_pct'] or 0)*100)}%" if r["coverage_pct"] is not None else "?"
        lines.append(
            f"| {i} | {r['symbol']} | {r['category']} | {score} | {delta_str} | "
            f"{r['verdict']} | {cov} | {' / '.join(notes) or '—'} |"
        )
    lines += [
        "",
        "## Tier breakdown by raw score",
        "",
        "(ignoring auto-reject states; pure score band)",
        "",
    ]
    bands = [(75, "≥75 — STRONG fundamentals"),
             (65, "65–74 — solid"),
             (55, "55–64 — neutral"),
             (45, "45–54 — weak"),
             (0,  "<45 — very weak / no data")]
    seen_below = False
    for cutoff, label in bands:
        in_band = [r for r in rows
                   if r["score"] is not None and r["score"] >= cutoff
                   and not any(r["score"] >= c for c, _ in bands[:bands.index((cutoff, label))])]
        if in_band:
            lines.append(f"### {label}")
            for r in in_band:
                lines.append(f"- **{r['symbol']}** ({r['score']}, {r['category']})")
            lines.append("")
        elif cutoff == 0:
            no_score = [r for r in rows if r["score"] is None]
            if no_score:
                lines.append("### No score available")
                for r in no_score:
                    lines.append(f"- **{r['symbol']}** ({r['category']}) — no conviction.json or all agents missing")
                lines.append("")
    return "\n".join(lines) + "\n"


def render_csv_rows(rows: list[dict[str, Any]]) -> list[list[str]]:
    header = [
        "rank", "symbol", "category", "score", "baseline_score", "reconciled_score",
        "delta", "verdict", "coverage_pct", "agents_loaded",
        "tokenomics", "revenue", "security", "onchain", "team", "moat", "macro",
        "missing", "stale", "fallback", "auto_reject_reason",
    ]
    out: list[list[str]] = [header]
    for i, r in enumerate(rows, start=1):
        sc = r["scorecard"] or {}
        out.append([
            str(i), r["symbol"], r["category"],
            str(r["score"]) if r["score"] is not None else "",
            str(r["baseline_score"]) if r["baseline_score"] is not None else "",
            str(r["reconciled_score"]) if r["reconciled_score"] is not None else "",
            str(r["delta"]),
            r["verdict"],
            f"{r['coverage_pct']:.2f}" if r["coverage_pct"] is not None else "",
            str(r["agents_loaded"]),
            str(sc.get("tokenomics", "")),
            str(sc.get("revenue", "")),
            str(sc.get("security", "")),
            str(sc.get("onchain", "")),
            str(sc.get("team", "")),
            str(sc.get("moat", "")),
            str(sc.get("macro", "")),
            ";".join(r["missing_agents"] or []),
            ";".join(r["stale_agents"] or []),
            ";".join(r["fallback_agents"] or []),
            (r["auto_reject_reason"] or "")[:200],
        ])
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--md-only", action="store_true")
    p.add_argument("--csv-only", action="store_true")
    p.add_argument("--stdout-only", action="store_true",
                   help="Print to stdout, don't write files")
    args = p.parse_args(argv)

    rows = collect_all()
    md = render_markdown(rows)
    csv_rows = render_csv_rows(rows)

    if not args.stdout_only:
        if not args.csv_only:
            (SUMMARY_DIR / "conviction_summary.md").write_text(md)
        if not args.md_only:
            with (SUMMARY_DIR / "conviction_summary.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerows(csv_rows)

    print(md)
    if not args.stdout_only:
        print(f"\nWrote: {SUMMARY_DIR}/conviction_summary.md")
        print(f"Wrote: {SUMMARY_DIR}/conviction_summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
