"""LLM-judge per project.

For each project with >=1 matched post:
  - Aggregate posts (newest first)
  - Send to Sonnet via shared.llm_client.research_json
  - Get back JSON with score 1-10, rationale, thesis_type, top_posts, etc.
  - Persist all results to data/maa/scores.json

Cost cap: <= $1.55 (109 projects * ~$0.014 worst case Sonnet).

Usage:
    python -m scripts.maa.score_projects \
        --in data/maa/posts_matched.jsonl \
        --xlsx MasterAnanda_Watchlist.xlsx \
        --out data/maa/scores.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict
from typing import Any

from scripts.maa.match_projects import load_xlsx_rows, build_xlsx_index
from shared.llm_client import research_json

JUDGE_SYSTEM = """\
You are scoring MasterAnanda's current bullish conviction on a single crypto \
project, based ONLY on his recent posts. Be skeptical:
  - Charismatic language ("extreme buy", "9X long PP", "last chance") is \
signal but ALSO often a leveraged trade thesis, not necessarily an \
investment thesis. Note when the case is leverage-driven vs. \
fundamentals-driven.
  - Weight the most recent posts. A post 35 days ago counts roughly \
one-third of one in the last 7 days.
  - Treat boost and comment counts as crowd sanity-check on quality \
(high engagement = the call resonated), but don't let them override your \
reading of the language.
Score 1-10 (forward-looking conviction). Return JSON only.
"""


def group_by_symbol(posts: list) -> dict:
    g: dict = defaultdict(list)
    for p in posts:
        g[p["matched_symbol"]].append(p)
    # Sort newest first for each project
    for sym in g:
        g[sym].sort(key=lambda p: p["effective_date"], reverse=True)
    return dict(g)


def build_judge_prompt(*, symbol: str, name: str, category: str,
                       posts: list) -> str:
    lines = [
        JUDGE_SYSTEM,
        "",
        f"Project: {symbol} ({name}, {category}).",
        "Posts (newest first; effective_date is the anchor for recency):",
        "",
    ]
    for p in posts:
        lines.append(
            f"- {p['effective_date']}: \"{p['title']}\""
            f" [boosts={p.get('boosts',0)}, comments={p.get('comments',0)},"
            f" updated={p.get('is_updated', False)}]"
        )
        lines.append(f"  {p.get('description','')}")
    lines += [
        "",
        "Output JSON:",
        '{',
        '  "score": <int 1-10>,',
        '  "rationale": "<2-3 sentences plain language>",',
        '  "thesis_type": "leverage" | "fundamental" | "mixed",',
        '  "latest_post": "<ISO date of newest post>",',
        '  "post_count": <int>,',
        '  "language_signals": [<verbatim strong phrases>],',
        '  "price_targets": [<explicit targets like "$3.15" if any>],',
        '  "leverage_mentions": [<like "5X","10X" if any>],',
        '  "skepticism_flags": [<caveats: '
        '"leverage-only thesis","no fundamental basis given","single post in window","engagement-light">],',
        '  "top_posts": [<{ "date": iso, "title": str } for the 3 most recent>]',
        '}',
    ]
    return "\n".join(lines)


def score_one_project(
    *,
    symbol: str,
    name: str,
    category: str,
    posts: list,
) -> dict:
    prompt = build_judge_prompt(
        symbol=symbol, name=name, category=category, posts=posts
    )
    resp = research_json(prompt)
    if not isinstance(resp, dict):
        return {
            "score": 0,
            "rationale": "LLM judge returned no parseable JSON.",
            "thesis_type": "mixed",
            "latest_post": posts[0]["effective_date"] if posts else None,
            "post_count": len(posts),
            "language_signals": [],
            "price_targets": [],
            "leverage_mentions": [],
            "skepticism_flags": ["llm_no_response"],
            "top_posts": [
                {"date": p["effective_date"], "title": p["title"]}
                for p in posts[:3]
            ],
        }
    # Defensive defaults - model may omit optional fields
    resp.setdefault("post_count", len(posts))
    resp.setdefault("latest_post", posts[0]["effective_date"] if posts else None)
    resp.setdefault("language_signals", [])
    resp.setdefault("price_targets", [])
    resp.setdefault("leverage_mentions", [])
    resp.setdefault("skepticism_flags", [])
    resp.setdefault("top_posts", [
        {"date": p["effective_date"], "title": p["title"]}
        for p in posts[:3]
    ])
    return resp


def run(
    *,
    in_path: pathlib.Path,
    xlsx_path: pathlib.Path,
    out_path: pathlib.Path,
) -> int:
    rows = load_xlsx_rows(xlsx_path)
    idx = build_xlsx_index(rows)
    posts = [json.loads(l) for l in open(in_path) if l.strip()]
    grouped = group_by_symbol(posts)

    out: dict = {}
    for sym, sym_posts in sorted(grouped.items()):
        row = idx.by_symbol.get(sym)
        if row is None:
            continue
        print(f"Scoring {sym} ({row.name}) - {len(sym_posts)} posts...", file=sys.stderr)
        out[sym] = score_one_project(
            symbol=sym, name=row.name, category=row.category, posts=sym_posts,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    return len(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", default="data/maa/posts_matched.jsonl",
                   type=pathlib.Path)
    p.add_argument("--xlsx", default="MasterAnanda_Watchlist.xlsx", type=pathlib.Path)
    p.add_argument("--out", dest="out_path", default="data/maa/scores.json",
                   type=pathlib.Path)
    args = p.parse_args(argv)

    if not args.in_path.exists() or not args.xlsx.exists():
        print(f"Missing input: {args.in_path} or {args.xlsx}", file=sys.stderr)
        return 2

    n = run(in_path=args.in_path, xlsx_path=args.xlsx, out_path=args.out_path)
    print(f"Scored {n} projects -> {args.out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
