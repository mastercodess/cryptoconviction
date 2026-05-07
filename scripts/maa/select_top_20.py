"""Final top-20 ranking + Markdown rendering.

Filters Asset Type == 'Crypto', sorts by score → recency → post_count,
takes top 20 (or fewer per the no-backfill rule).

Usage:
    python -m scripts.maa.select_top_20 \
        --scores data/maa/scores.json \
        --xlsx MasterAnanda_Watchlist.xlsx \
        --out-json reports/_maa_top20_2026-05-06.json \
        --out-md   reports/_maa_top20_2026-05-06.md
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from scripts.maa.match_projects import load_xlsx_rows, XlsxRow


_FUNDAMENTAL_TERMS = {
    "revenue", "tokenomic", "tokenomics", "adoption", "ecosystem",
    "partnership", "listing", "fundamental",
}


def is_leverage_only(score_record: dict) -> bool:
    """⚠ flag: thesis_type==leverage AND no fundamental term in language_signals."""
    if score_record.get("thesis_type") != "leverage":
        return False
    sigs = " ".join(score_record.get("language_signals", [])).lower()
    if any(term in sigs for term in _FUNDAMENTAL_TERMS):
        return False
    return True


def rank_scores(
    scores: dict,
    xlsx_rows: list,
    *,
    top_n: int = 20,
) -> list:
    """Filter to crypto, sort, take top_n. Returns ranked list of records."""
    by_sym = {r.symbol: r for r in xlsx_rows}
    candidates: list = []
    for sym, sc in scores.items():
        row = by_sym.get(sym)
        if row is None or row.asset_type != "Crypto":
            continue
        candidates.append({
            "symbol": sym,
            "name": row.name,
            "category": row.category,
            "score": sc.get("score", 0),
            "thesis_type": sc.get("thesis_type", "mixed"),
            "rationale": sc.get("rationale", ""),
            "latest_post": sc.get("latest_post", ""),
            "post_count": sc.get("post_count", 0),
            "skepticism_flags": sc.get("skepticism_flags", []),
            "language_signals": sc.get("language_signals", []),
            "top_posts": sc.get("top_posts", []),
        })
    candidates.sort(
        key=lambda r: (-r["score"], _neg_iso(r["latest_post"]), -r["post_count"]),
    )
    selected = candidates[:top_n]
    for i, r in enumerate(selected, start=1):
        r["rank"] = i
    return selected


def _neg_iso(s: str) -> float:
    """Sort key trick: invert ISO date so the natural ASC sort behaves like DESC.

    Returns negative timestamp; missing/invalid dates sort last (largest value).
    """
    if not s:
        return 0.0
    import datetime as _dt
    try:
        return -_dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def runners_up(
    scores: dict,
    xlsx_rows: list,
    *,
    top_ranked_symbols: set,
    n_runners: int = 5,
) -> list:
    """Same logic as rank_scores but takes the next n after the top."""
    everything = rank_scores(scores, xlsx_rows, top_n=10_000)
    rest = [r for r in everything if r["symbol"] not in top_ranked_symbols]
    base = len(top_ranked_symbols)
    for i, r in enumerate(rest[:n_runners], start=base + 1):
        r["rank"] = i
    return rest[:n_runners]


def render_markdown(
    *,
    ranked: list,
    runners_up: list,
    window_start: str,
    window_end: str,
) -> str:
    lines: list = []
    lines.append(f"# MasterAnanda Top {len(ranked)} — {window_start} → {window_end}")
    lines.append("")
    lines.append("Source: `MasterAnanda.pdf`, scored via Sonnet LLM judge with skepticism prompts.")
    lines.append("")
    lines.append("| Rank | Symbol | Category | MAA score (/10) | Thesis | ⚠ | Latest | Posts | Rationale |")
    lines.append("|---:|---|---|---:|---|:-:|---|---:|---|")
    for r in ranked:
        flag = "⚠" if is_leverage_only(r) else " "
        latest = r["latest_post"][:10] if r["latest_post"] else ""
        rat = (r["rationale"] or "").replace("\n", " ").replace("|", "\\|")
        lines.append(
            f"| {r['rank']} | **{r['symbol']}** | {r['category']} | "
            f"{r['score']} | {r['thesis_type']} | {flag} | {latest} | "
            f"{r['post_count']} | {rat} |"
        )
    lines.append("")
    lines.append("## Per-project rationale + source posts")
    lines.append("")
    for r in ranked:
        lines.append(f"### {r['rank']}. {r['symbol']} — {r['name']} ({r['category']})")
        lines.append("")
        lines.append(f"- **Score:** {r['score']}/10 — {r['thesis_type']}")
        if r["skepticism_flags"]:
            lines.append(f"- **Skepticism flags:** {', '.join(r['skepticism_flags'])}")
        lines.append(f"- **Rationale:** {r['rationale']}")
        if r["top_posts"]:
            lines.append("- **Top posts:**")
            for tp in r["top_posts"]:
                d = tp.get("date", "")[:10]
                lines.append(f"  - {d} — {tp.get('title','')}")
        lines.append("")
    if runners_up:
        lines.append("## Runners-up (rank 21-25)")
        lines.append("")
        for r in runners_up:
            lines.append(f"- **{r['rank']}. {r['symbol']}** ({r['name']}) — score {r['score']}/10, "
                         f"{r['thesis_type']}, {r['post_count']} posts")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scores", default="data/maa/scores.json", type=pathlib.Path)
    p.add_argument("--xlsx", default="MasterAnanda_Watchlist.xlsx", type=pathlib.Path)
    p.add_argument("--out-json", default="reports/_maa_top20_2026-05-06.json",
                   type=pathlib.Path)
    p.add_argument("--out-md", default="reports/_maa_top20_2026-05-06.md",
                   type=pathlib.Path)
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--window-start", default="2026-03-30")
    p.add_argument("--window-end", default="2026-05-06")
    args = p.parse_args(argv)

    if not args.scores.exists() or not args.xlsx.exists():
        print(f"Missing input: {args.scores} or {args.xlsx}", file=sys.stderr)
        return 2

    scores = json.loads(args.scores.read_text())
    rows = load_xlsx_rows(args.xlsx)
    ranked = rank_scores(scores, rows, top_n=args.top_n)
    rups = runners_up(scores, rows,
                      top_ranked_symbols={r["symbol"] for r in ranked},
                      n_runners=5)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(ranked, indent=2))
    args.out_md.write_text(render_markdown(
        ranked=ranked, runners_up=rups,
        window_start=args.window_start, window_end=args.window_end,
    ))
    print(f"Wrote {len(ranked)} ranked + {len(rups)} runners-up")
    print(f"  json: {args.out_json}")
    print(f"  md:   {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
