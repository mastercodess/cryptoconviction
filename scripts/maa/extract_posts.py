"""Extract MasterAnanda TradingView posts from the on-disk PDF.

The TradingView PDF export emits, on each page (after page 1, the profile
header), 1-3 posts of this shape:

    <title line>
    <2-3 description lines>
    by MasterAnanda                ← byline ALONE on its own line
    <count_line>                   ← 1 or 2 integers ("13" or "7 31")
    <date_line>                    ← relative or absolute
    99+                            ← optional notification badge (page furniture)

A post can also span a page boundary: title + description on page N, byline +
counts + date on page N+1. The parser is therefore a state machine driven by
a flat stream of lines that crosses page boundaries.

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

# --- Regexes ----------------------------------------------------------------

# Boundary: byline appears alone on its own line.
_BY_LINE = re.compile(r"^\s*by\s+MasterAnanda\s*$", re.IGNORECASE)

# Counts line: 1 or 2 integers, nothing else.
_COUNTS_LINE = re.compile(r"^\s*(\d+)(?:\s+(\d+))?\s*$")

# Relative date: "11 hours ago", "Updated 6 hours ago", "1 day ago", "9 minutes ago".
_REL_DATE = re.compile(
    r"^(?:Updated\s+)?\d+\s+(?:minutes?|hours?|days?|weeks?|months?)\s+ago$",
    re.IGNORECASE,
)

# Absolute date: "May 4", "Apr 25", "Updated Apr 16", "Jan 5, 2026".
_ABS_DATE = re.compile(
    r"^(?:Updated\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+\d{1,2}(?:,\s*\d{4})?$",
    re.IGNORECASE,
)


def _try_extract_date(line: str) -> str | None:
    """Return the line stripped if it matches a known date pattern, else None."""
    s = line.strip()
    if _REL_DATE.match(s) or _ABS_DATE.match(s):
        return s
    return None


# --- Page furniture ---------------------------------------------------------

def _is_page_furniture(line: str) -> bool:
    """Filter URL/page-number/header/badge lines that aren't post content."""
    s = line.strip()
    if not s:
        return True
    if s == "99+":
        return True
    if s.startswith("http"):
        return True
    if "tradingview.com" in s.lower():
        return True
    if re.fullmatch(r"\d+/\d+", s):  # "2/199"
        return True
    if "MasterAnanda" in s and "Trading Ideas" in s:
        return True
    if re.match(r"^\d+/\d+/\d+,?\s+\d", s):  # "5/6/26, 5:09 PM"
        return True
    return False


# --- State machine ---------------------------------------------------------

class _ParserState:
    """Mutable state for the multi-page line stream."""

    def __init__(self) -> None:
        # SEEKING_TITLE: accumulating title + description until we see a byline.
        # AWAITING_COUNTS: byline seen; next non-furniture line should be counts.
        # AWAITING_DATE: counts seen; next non-furniture line should be a date.
        self.mode: str = "SEEKING_TITLE"
        self.buffer: list[str] = []          # accumulating title + description lines
        self.pending: dict[str, Any] | None = None  # post-in-flight after byline


class PostParseError(ValueError):
    """Reserved for future strict parsing; current parser is permissive."""


def _consume_line(
    state: _ParserState, line: str, page_number: int
) -> dict[str, Any] | None:
    """Process one line through the state machine.

    Returns a finalized post dict when a post completes, else None.
    """
    if _is_page_furniture(line):
        return None
    s = line.strip()
    if not s:
        return None  # blank lines never matter

    if state.mode == "SEEKING_TITLE":
        if _BY_LINE.match(s):
            if not state.buffer:
                # byline with no preceding title; orphan — ignore.
                return None
            title = state.buffer[0].strip()
            description = " ".join(b.strip() for b in state.buffer[1:]).strip()
            state.pending = {
                "title": title,
                "description": description,
                "page_number": page_number,
            }
            state.buffer = []
            state.mode = "AWAITING_COUNTS"
            return None
        # Otherwise accumulate as title/description.
        state.buffer.append(s)
        return None

    if state.mode == "AWAITING_COUNTS":
        # Counts line: 1 or 2 ints.
        m = _COUNTS_LINE.match(s)
        if m is not None:
            assert state.pending is not None
            state.pending["comments"] = int(m.group(1))
            state.pending["boosts"] = int(m.group(2)) if m.group(2) else 0
            state.mode = "AWAITING_DATE"
            return None
        # If we see a date directly without counts, default counts to 0.
        date_str = _try_extract_date(s)
        if date_str is not None:
            assert state.pending is not None
            state.pending["comments"] = 0
            state.pending["boosts"] = 0
            state.pending["date_string"] = date_str
            state.pending["is_updated"] = date_str.lower().startswith("updated")
            post = state.pending
            state.pending = None
            state.mode = "SEEKING_TITLE"
            return post
        # Unexpected — drop the pending post and treat this as a new title.
        state.pending = None
        state.buffer = [s]
        state.mode = "SEEKING_TITLE"
        return None

    if state.mode == "AWAITING_DATE":
        date_str = _try_extract_date(s)
        if date_str is not None:
            assert state.pending is not None
            state.pending["date_string"] = date_str
            state.pending["is_updated"] = date_str.lower().startswith("updated")
            post = state.pending
            state.pending = None
            state.mode = "SEEKING_TITLE"
            return post
        # Unexpected — orphan post, drop and treat as a new title.
        state.pending = None
        state.buffer = [s]
        state.mode = "SEEKING_TITLE"
        return None

    # Unreachable.
    return None


# --- Public parsing entry points -------------------------------------------

def parse_page_text_to_posts(
    text: str, page_number: int
) -> list[dict[str, Any]]:
    """Parse a single page's text into completed post dicts.

    For multi-page PDFs use ``extract_pdf_to_jsonl``, which carries state across
    page boundaries (a post can span pages). This function is for unit testing
    self-contained samples.
    """
    state = _ParserState()
    posts: list[dict[str, Any]] = []
    for ln in text.splitlines():
        post = _consume_line(state, ln, page_number)
        if post is not None:
            posts.append(post)
    return posts


def extract_pdf_to_jsonl(
    pdf_path: pathlib.Path,
    output_path: pathlib.Path,
    unparsed_path: pathlib.Path,
) -> tuple[int, int]:
    """Iterate the PDF, drive a single state machine across pages, write JSONL.

    Returns ``(n_parsed, n_unparsed)``.
    """
    import pdfplumber  # local import — keeps the CLI fast when not used

    output_path.parent.mkdir(parents=True, exist_ok=True)
    unparsed_path.parent.mkdir(parents=True, exist_ok=True)
    n_parsed = 0
    n_unparsed = 0

    state = _ParserState()
    # NOTE: Python 3.9-compatible nested with-statements.
    with pdfplumber.open(pdf_path) as pdf:
        with open(output_path, "w") as out_f:
            with open(unparsed_path, "w") as bad_f:
                for page_idx, page in enumerate(pdf.pages, start=1):
                    if page_idx == 1:
                        continue  # profile header
                    text = page.extract_text() or ""
                    for ln in text.splitlines():
                        post = _consume_line(state, ln, page_idx)
                        if post is not None:
                            out_f.write(json.dumps(post) + "\n")
                            n_parsed += 1
                # End-of-PDF: any pending post is dropped (cross-page tail).
                if state.pending is not None:
                    bad_f.write(
                        json.dumps(
                            {"page": "end-of-file", "partial": state.pending}
                        )
                        + "\n"
                    )
                    n_unparsed += 1

    return n_parsed, n_unparsed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Extract MasterAnanda PDF posts to JSONL."
    )
    p.add_argument("--pdf", default="MasterAnanda.pdf", type=pathlib.Path)
    p.add_argument(
        "--out", default="data/maa/posts_raw.jsonl", type=pathlib.Path
    )
    p.add_argument(
        "--unparsed",
        default="data/maa/posts_unparsed.jsonl",
        type=pathlib.Path,
    )
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
        print(
            "WARNING: >5% unparsed. Consider vision-fallback per spec.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
