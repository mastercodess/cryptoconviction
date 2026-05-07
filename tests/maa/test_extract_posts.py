"""Tests for scripts.maa.extract_posts.

Strategy:
  - Pure-function parsers tested with synthetic page text that mirrors the
    real TradingView PDF shape (byline alone, counts on next line).
  - One real-PDF smoke check (skipped if MasterAnanda.pdf absent).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from scripts.maa.extract_posts import (
    parse_page_text_to_posts,
    PostParseError,  # re-exported for compatibility; not raised today
)


# Synthetic page text mirroring the real PDF: byline alone, counts on next
# line, then date. Includes the 99+ notification badge as page furniture.
SAMPLE_PAGE_TEXT = """5/6/26, 5:00 PM    MasterAnanda — Trading Ideas and Scripts — TradingView
Cardano (ADA) starts to move — Last chance! (Fast Sudden Growth!)
Cardano (ADAUSDT) has been moving all this time very close to support. At first there was
some bullish action, in February, then the market consolidated sideways with a downward
bent. No news positive, nothing bullish, no buyers... Things change. Since Cardano...
by MasterAnanda
7 31
Updated 6 hours ago
99+
FET · The Start of a New Bullish Cycle, Nature & The Market
I always ask myself this question—I see the market as a unit rather than considering each
project and chart in isolation—can FETUSDT grow, massively, while Bitcoin crashes? Can
hundreds of altcoins produce sustained growth, several months in succession, while...
by MasterAnanda
7 7
11 hours ago
https://www.tradingview.com/u/MasterAnanda/                                            2/199
"""


def test_parse_page_finds_two_posts():
    posts = parse_page_text_to_posts(SAMPLE_PAGE_TEXT, page_number=2)
    assert len(posts) == 2

    p1 = posts[0]
    assert "Cardano" in p1["title"]
    assert "ADA" in p1["title"]
    assert p1["date_string"] == "Updated 6 hours ago"
    assert p1["is_updated"] is True
    assert p1["comments"] == 7
    assert p1["boosts"] == 31
    assert p1["page_number"] == 2

    p2 = posts[1]
    assert p2["title"].startswith("FET")
    assert p2["date_string"] == "11 hours ago"
    assert p2["is_updated"] is False
    assert p2["comments"] == 7
    assert p2["boosts"] == 7


def test_parse_page_handles_uppercase_BY():
    """The 'by MasterAnanda' boundary is matched case-insensitively."""
    text = SAMPLE_PAGE_TEXT.replace("by MasterAnanda", "BY  MasterAnanda")
    posts = parse_page_text_to_posts(text, page_number=2)
    assert len(posts) == 2


def test_parse_page_with_no_posts_returns_empty():
    posts = parse_page_text_to_posts("just a header line", page_number=1)
    assert posts == []


def test_parse_post_without_engagement_counts_falls_back_to_zero():
    """If a date arrives where counts are expected, default counts to 0."""
    text = """\
Some Title Without Engagement Numbers
Description here.
by MasterAnanda
1 day ago
"""
    posts = parse_page_text_to_posts(text, page_number=5)
    assert len(posts) == 1
    assert posts[0]["comments"] == 0
    assert posts[0]["boosts"] == 0
    assert posts[0]["date_string"] == "1 day ago"


def test_parse_page_handles_absolute_date():
    """Absolute dates like 'May 4' and 'Apr 25' are recognized."""
    text = """\
Some Title
Description.
by MasterAnanda
9 12
May 4
"""
    posts = parse_page_text_to_posts(text, page_number=11)
    assert len(posts) == 1
    assert posts[0]["date_string"] == "May 4"
    assert posts[0]["is_updated"] is False
    assert posts[0]["comments"] == 9
    assert posts[0]["boosts"] == 12


def test_parse_page_handles_updated_absolute_date():
    """An 'Updated Apr 16' date sets is_updated=True."""
    text = """\
Title
Desc.
by MasterAnanda
3
Updated Apr 16
"""
    posts = parse_page_text_to_posts(text, page_number=42)
    assert len(posts) == 1
    assert posts[0]["date_string"] == "Updated Apr 16"
    assert posts[0]["is_updated"] is True


def test_parse_page_handles_single_int_counts():
    """A single integer on the counts line is comments; boosts default to 0."""
    text = """\
Title
Description.
by MasterAnanda
13
May 1
"""
    posts = parse_page_text_to_posts(text, page_number=21)
    assert len(posts) == 1
    assert posts[0]["comments"] == 13
    assert posts[0]["boosts"] == 0
    assert posts[0]["date_string"] == "May 1"


def test_parse_page_strips_99_plus_badge():
    """The '99+' notification badge between posts is stripped as page furniture."""
    text = """\
Title 1
Desc 1.
by MasterAnanda
1 5
12 hours ago
99+
Title 2
Desc 2.
by MasterAnanda
2
11 hours ago
"""
    posts = parse_page_text_to_posts(text, page_number=5)
    assert len(posts) == 2
    assert posts[0]["title"] == "Title 1"
    assert posts[1]["title"] == "Title 2"
    # Make sure the "99+" never leaked into the second post's title.
    assert "99+" not in posts[1]["title"]


@pytest.mark.skipif(
    not pathlib.Path("MasterAnanda.pdf").exists(),
    reason="real PDF not present (CI environment)",
)
def test_real_pdf_extracts_at_least_200_posts(tmp_path):
    """Smoke test: against the real PDF, we should get many posts."""
    from scripts.maa.extract_posts import extract_pdf_to_jsonl

    out_path = tmp_path / "posts_raw.jsonl"
    unparsed_path = tmp_path / "posts_unparsed.jsonl"
    n_parsed, n_unparsed = extract_pdf_to_jsonl(
        pdf_path=pathlib.Path("MasterAnanda.pdf"),
        output_path=out_path,
        unparsed_path=unparsed_path,
    )

    assert n_parsed >= 200, f"expected >=200 posts, got {n_parsed}"
    # Unparsed rate should be <5% — sanity check, not strict.
    assert n_unparsed < n_parsed * 0.05

    # Spot check: every parsed record has the required keys.
    required_keys = {
        "title",
        "description",
        "date_string",
        "comments",
        "boosts",
        "is_updated",
        "page_number",
    }
    with open(out_path) as f:
        for line in f:
            r = json.loads(line)
            assert required_keys <= set(r.keys())
            assert isinstance(r["title"], str) and r["title"]
