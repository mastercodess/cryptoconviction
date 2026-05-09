"""Build the side-by-side MAA-vs-conviction summary report.

Reads:
  reports/_maa_top20_2026-05-06.json
  reports/{SYMBOL}/conviction.json   for each top-20 entry that completed
  data/maa/run_log.jsonl              for batch cost / wall-time totals

Writes:
  reports/_maa_top20_2026-05-06/summary.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any


def classify_divergence(*, maa, conviction):
    if maa >= 7 and conviction >= 60:
        return "ALIGNED"
    if maa >= 8 and conviction <= 40:
        return "HIGH-MAA-LOW-CONVICTION"
    if maa < 5 and conviction >= 70:
        return "LOW-MAA-HIGH-CONVICTION"
    return ""


def _safe(s, default=""):
    if not isinstance(s, str):
        return default
    return s.replace("\n", " ").replace("|", "\\|") or default


def build_summary_md(*, top20, convictions, run_log):
    lines = []
    lines.append("# MasterAnanda Top-20 vs Conviction System — 2026-05-06")
    lines.append("")
    lines.append(f"- **Generated:** {dt.date.today().isoformat()}")
    lines.append(f"- **Total spend:** ${run_log.get('total_cost_usd', 0):.2f}")
    lines.append(f"- **Tokens completed:** {run_log.get('tokens_completed', 0)} / {len(top20)}")
    skipped = run_log.get('tokens_skipped', 0)
    if skipped:
        lines.append(f"- **Tokens skipped (cap fired or registry-dropped):** {skipped}")
    if run_log.get("wall_time_s"):
        lines.append(f"- **Wall time:** {run_log['wall_time_s'] / 60:.1f} min")
    lines.append("")

    # Sort by conviction desc; missing conviction goes to the bottom
    rows = []
    for entry in top20:
        sym = entry["symbol"]
        cv = convictions.get(sym)
        rows.append({**entry, "_conviction": cv})
    rows.sort(
        key=lambda r: -(r["_conviction"]["weighted_conviction"]
                        if r["_conviction"] else -1)
    )

    lines.append("| Rank | Symbol | Category | MAA score (/10) | MAA thesis | "
                 "Conviction (/100) | Verdict | Bull factor | Bear factor | Divergence |")
    lines.append("|---:|---|---|---:|---|---:|---|---|---|---|")
    for r in rows:
        cv = r["_conviction"]
        if cv is None:
            lines.append(
                f"| {r.get('rank','-')} | **{r['symbol']}** | {r['category']} | "
                f"{r['score']} | {r['thesis_type']} | "
                f"— | NOT_RUN | — | — | (no conviction.json) |"
            )
            continue
        bull = _safe((cv.get("bull_case") or [""])[0])
        bear = _safe((cv.get("bear_case") or [""])[0])
        div = classify_divergence(maa=r["score"],
                                  conviction=cv["weighted_conviction"])
        lines.append(
            f"| {r.get('rank','-')} | **{r['symbol']}** | {r['category']} | "
            f"{r['score']} | {r['thesis_type']} | "
            f"{cv['weighted_conviction']} | {cv['final_verdict']} | "
            f"{bull} | {bear} | {div} |"
        )
    lines.append("")
    lines.append("## Per-token synthesis")
    lines.append("")
    for r in rows:
        cv = r["_conviction"]
        lines.append(f"### {r['symbol']} — {r['name']} ({r['category']})")
        lines.append("")
        lines.append(f"- **MAA:** score {r['score']}/10 — {r['thesis_type']}. "
                     f"{r['rationale']}")
        if r.get("top_posts"):
            tp = r["top_posts"][0]
            lines.append(f"  - Most recent post: {tp.get('date','')[:10]} — {tp.get('title','')}")
        if cv is None:
            lines.append("- **System:** no conviction.json (not run or aborted).")
        else:
            lines.append(f"- **System:** {cv['weighted_conviction']}/100 — {cv['final_verdict']}.")
            if cv.get("bull_case"):
                lines.append(f"  - Bull: {'; '.join(cv['bull_case'][:3])}")
            if cv.get("bear_case"):
                lines.append(f"  - Bear: {'; '.join(cv['bear_case'][:3])}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _aggregate_run_log(path):
    if not path.exists():
        return {"total_cost_usd": 0.0, "tokens_completed": 0,
                "tokens_skipped": 0, "wall_time_s": None}
    total = 0.0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("cost_usd") is not None:
            total += r["cost_usd"]
    return {"total_cost_usd": total, "tokens_completed": 0,
            "tokens_skipped": 0, "wall_time_s": None}


def run(*, top20_path, reports_dir, run_log_path, out_path):
    top20 = json.loads(top20_path.read_text())
    convictions = {}
    completed = 0
    for entry in top20:
        cv_path = reports_dir / entry["symbol"] / "conviction.json"
        if cv_path.exists():
            convictions[entry["symbol"]] = json.loads(cv_path.read_text())
            completed += 1

    run_log = _aggregate_run_log(run_log_path)
    run_log["tokens_completed"] = completed
    run_log["tokens_skipped"] = len(top20) - completed

    md = build_summary_md(top20=top20, convictions=convictions, run_log=run_log)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--top20", default="reports/_maa_top20_2026-05-06.json",
                   type=pathlib.Path)
    p.add_argument("--reports-dir", default="reports", type=pathlib.Path)
    p.add_argument("--run-log", default="data/maa/run_log.jsonl",
                   type=pathlib.Path)
    p.add_argument("--out", default="reports/_maa_top20_2026-05-06/summary.md",
                   type=pathlib.Path)
    args = p.parse_args(argv)

    if not args.top20.exists():
        print(f"Missing: {args.top20}", file=sys.stderr)
        return 2

    run(top20_path=args.top20, reports_dir=args.reports_dir,
        run_log_path=args.run_log, out_path=args.out)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
