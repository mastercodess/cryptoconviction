"""Convert relative or absolute date strings to absolute datetimes; filter to window.

Anchor: 2026-05-06 17:00 (PDF export timestamp). We accept up to +-12h date
drift if the anchor's timezone differs from local — the 37-day window is
wide enough to absorb that without corrupting the day-level filter.

Handles both date forms emitted by extract_posts.py:
  - Relative: "9 minutes ago", "Updated 6 hours ago", "2 weeks ago"
  - Absolute: "May 4", "Apr 25", "Updated Apr 16", "Apr 25, 2025"
For absolute dates without an explicit year, the anchor's year (2026) is used.

Usage:
    python -m scripts.maa.parse_dates \
        --in data/maa/posts_raw.jsonl \
        --out data/maa/posts_filtered.jsonl \
        --anchor 2026-05-06T17:00 \
        --window-start 2026-03-30
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from typing import Optional

ANCHOR_DEFAULT = dt.datetime(2026, 5, 6, 17, 0)
WINDOW_START_DEFAULT = dt.datetime(2026, 3, 30, 0, 0)

_RELATIVE = re.compile(
    r"(?:Updated\s+)?(\d+)\s+(minute|hour|day|week|month)s?\s+ago",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ABSOLUTE = re.compile(
    r"^(?:Updated\s+)?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(\d{1,2})(?:,\s*(\d{4}))?$",
    re.IGNORECASE,
)


class UnparseableDateError(ValueError):
    pass


def parse_date_string(date_str: str, *, anchor: dt.datetime) -> dt.datetime:
    """Convert a relative or absolute date string to a datetime.

    Tries relative first ("9 minutes ago"), then absolute ("May 4", "Apr 25, 2025").
    Year for absolute dates without explicit year defaults to anchor's year.
    """
    s = date_str.strip()

    # Relative form
    m = _RELATIVE.search(s)
    if m is not None:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "minute":
            delta = dt.timedelta(minutes=n)
        elif unit == "hour":
            delta = dt.timedelta(hours=n)
        elif unit == "day":
            delta = dt.timedelta(days=n)
        elif unit == "week":
            delta = dt.timedelta(weeks=n)
        elif unit == "month":
            delta = dt.timedelta(days=30 * n)
        else:
            raise UnparseableDateError(date_str)
        return anchor - delta

    # Absolute form
    m = _ABSOLUTE.match(s)
    if m is not None:
        month = _MONTHS[m.group(1).lower()[:3]]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else anchor.year
        return dt.datetime(year, month, day, 0, 0, 0)

    raise UnparseableDateError(date_str)


def filter_to_window(
    posts: list,
    *,
    anchor: dt.datetime,
    window_start: dt.datetime,
) -> list:
    """Annotate each post with effective_date ISO; keep only those in window."""
    kept = []
    for p in posts:
        try:
            eff = parse_date_string(p["date_string"], anchor=anchor)
        except UnparseableDateError:
            continue
        if eff < window_start:
            continue
        p2 = dict(p)
        p2["effective_date"] = eff.isoformat()
        kept.append(p2)
    return kept


def run(
    *,
    in_path: pathlib.Path,
    out_path: pathlib.Path,
    anchor: dt.datetime,
    window_start: dt.datetime,
) -> int:
    """Read in_path, filter, write out_path. Returns count kept."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    posts = [json.loads(l) for l in open(in_path) if l.strip()]
    kept = filter_to_window(posts, anchor=anchor, window_start=window_start)
    with open(out_path, "w") as f:
        for p in kept:
            f.write(json.dumps(p) + "\n")
    return len(kept)


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", default="data/maa/posts_raw.jsonl",
                   type=pathlib.Path)
    p.add_argument("--out", dest="out_path", default="data/maa/posts_filtered.jsonl",
                   type=pathlib.Path)
    p.add_argument("--anchor", default=ANCHOR_DEFAULT.isoformat(),
                   help="ISO datetime, e.g. 2026-05-06T17:00")
    p.add_argument("--window-start", default=WINDOW_START_DEFAULT.date().isoformat(),
                   help="ISO date, e.g. 2026-03-30")
    args = p.parse_args(argv)

    anchor = dt.datetime.fromisoformat(args.anchor)
    window_start = dt.datetime.fromisoformat(args.window_start) \
        if "T" in args.window_start else \
        dt.datetime.combine(dt.date.fromisoformat(args.window_start), dt.time())

    if not args.in_path.exists():
        print(f"Input not found: {args.in_path}", file=sys.stderr)
        return 2
    n = run(in_path=args.in_path, out_path=args.out_path,
            anchor=anchor, window_start=window_start)
    print(f"Kept {n} posts in window ({window_start.date()} -> {anchor.date()})")
    print(f"  out: {args.out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
