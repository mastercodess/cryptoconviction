"""Extract MasterAnanda TradingView posts from the on-disk PDF.

Each PDF page (after page 1, the profile header) contains 1-3 idea entries
in a regular shape:

    <title line>
    <2-3 line description>
    by MasterAnanda           <comments_int>   <boosts_int>
    <date_string e.g. "Updated 6 hours ago" or "11 hours ago">

Page footer is the URL + page number. The 5/6/26 5:00 PM header gives the
export anchor (used by parse_dates.py, not here).

Usage:
    python -m scripts.maa.extract_posts \
        --pdf MasterAnanda.pdf \
        --out data/maa/posts_raw.jsonl \
        --unparsed data/maa/posts_unparsed.jsonl
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

# Boundary marker — case-insensitive, tolerant of whitespace
_BY_LINE = re.compile(r"^\s*by\s+MasterAnanda\b(.*)$", re.IGNORECASE)

# Engagement counts: two integers on the byline (comments, boosts)
_TWO_INTS = re.compile(r"(\d+)\s+(\d+)\s*$")

# Date string: "Updated X ago" or "X (units) ago"
_DATE_LINE = re.compile(
    r"^\s*((?:Updated\s+)?\d+\s+(?:minutes?|hours?|days?|weeks?|months?)\s+ago)\s*$",
    re.IGNORECASE,
)


class PostParseError(ValueError):
    """Raised when a post chunk can't be split into title/desc/date/counts."""


def parse_page_text_to_posts(text: str, page_number: int) -> list[dict[str, Any]]:
    """Split a single page's text into post dicts.

    Returns [] if the page has no posts (e.g. profile header page).
    Raises PostParseError on chunks that match the byline pattern but lack
    the date or counts.
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    posts: list[dict[str, Any]] = []
    buffer: list[str] = []

    for ln in lines:
        m = _BY_LINE.match(ln)
        if m is not None:
            counts_tail = m.group(1) or ""
            comments, boosts = _extract_counts(counts_tail)
            # The previous lines are the title (1) and description (1+).
            if not buffer:
                continue  # boundary with no preceding content; skip
            title = buffer[0].strip()
            description = " ".join(b.strip() for b in buffer[1:]).strip()
            buffer = []  # reset for next post
            posts.append({
                "_pending_post": True,
                "title": title,
                "description": description,
                "comments": comments,
                "boosts": boosts,
                "page_number": page_number,
            })
            continue

        # Date line — closes the most recently opened post
        date_m = _DATE_LINE.match(ln)
        if date_m and posts and posts[-1].get("_pending_post"):
            date_str = date_m.group(1).strip()
            posts[-1]["date_string"] = date_str
            posts[-1]["is_updated"] = date_str.lower().startswith("updated")
            del posts[-1]["_pending_post"]
            continue

        # Otherwise: accumulate as title/description for the next boundary.
        if ln.strip() and not _is_page_furniture(ln):
            buffer.append(ln)

    # Drop any post that never received a date — unparsed.
    finalized: list[dict[str, Any]] = []
    for p in posts:
        if p.pop("_pending_post", False):
            continue  # dropped: pending boundary never closed
        finalized.append(p)
    return finalized


def _extract_counts(tail: str) -> tuple[int, int]:
    """Pull (comments, boosts) from the byline tail. Default (0, 0)."""
    m = _TWO_INTS.search(tail)
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def _is_page_furniture(line: str) -> bool:
    """Filter URL/page-number/header lines that aren't post content."""
    s = line.strip()
    if s.startswith("http"):
        return True
    if "tradingview.com" in s.lower():
        return True
    if re.fullmatch(r"\d+/\d+", s):  # "2/199"
        return True
    if "MasterAnanda" in s and "Trading Ideas" in s:
        return True
    if re.match(r"^\d+/\d+/\d+,?\s+\d", s):  # "5/6/26, 5:00 PM"
        return True
    return False


def extract_pdf_to_jsonl(
    pdf_path: pathlib.Path,
    output_path: pathlib.Path,
    unparsed_path: pathlib.Path,
) -> tuple[int, int]:
    """Iterate the PDF, parse each page, write parsed/unparsed JSONL.

    Returns (n_parsed, n_unparsed).
    """
    import pdfplumber  # local import — keeps the CLI fast when not used

    output_path.parent.mkdir(parents=True, exist_ok=True)
    unparsed_path.parent.mkdir(parents=True, exist_ok=True)
    n_parsed = n_unparsed = 0

    # NOTE: Python 3.9-compatible nested with-statements (parenthesized
    # multi-context with-statements are a 3.10+ feature).
    with pdfplumber.open(pdf_path) as pdf:
        with open(output_path, "w") as out_f:
            with open(unparsed_path, "w") as bad_f:
                for page_idx, page in enumerate(pdf.pages, start=1):
                    if page_idx == 1:
                        continue  # profile header
                    text = page.extract_text() or ""
                    try:
                        posts = parse_page_text_to_posts(text, page_number=page_idx)
                    except PostParseError as e:
                        bad_f.write(json.dumps({"page": page_idx, "error": str(e)}) + "\n")
                        n_unparsed += 1
                        continue
                    for p in posts:
                        if "date_string" not in p:
                            # Pending post that never received a date — count as unparsed
                            bad_f.write(json.dumps({"page": page_idx, "partial": p}) + "\n")
                            n_unparsed += 1
                            continue
                        out_f.write(json.dumps(p) + "\n")
                        n_parsed += 1

    return n_parsed, n_unparsed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract MasterAnanda PDF posts to JSONL.")
    p.add_argument("--pdf", default="MasterAnanda.pdf", type=pathlib.Path)
    p.add_argument("--out", default="data/maa/posts_raw.jsonl", type=pathlib.Path)
    p.add_argument("--unparsed", default="data/maa/posts_unparsed.jsonl", type=pathlib.Path)
    args = p.parse_args(argv)

    if not args.pdf.exists():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    n_parsed, n_unparsed = extract_pdf_to_jsonl(args.pdf, args.out, args.unparsed)
    pct_unparsed = (n_unparsed / max(1, n_parsed + n_unparsed)) * 100
    print(f"Parsed {n_parsed} posts, {n_unparsed} unparsed ({pct_unparsed:.1f}%)")
    print(f"  out: {args.out}")
    print(f"  unparsed: {args.unparsed}")
    if pct_unparsed > 5:
        print("WARNING: >5% unparsed. Consider vision-fallback per spec.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
