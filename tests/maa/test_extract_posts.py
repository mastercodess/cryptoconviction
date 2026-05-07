"""Tests for scripts.maa.extract_posts.

Strategy:
  - Pure-function parsers tested with synthetic page text.
  - One real-PDF smoke check (skipped if MasterAnanda.pdf absent).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from scripts.maa.extract_posts import (
    parse_page_text_to_posts,
    PostParseError,
)


SAMPLE_PAGE_TEXT = """5/6/26, 5:00 PM    MasterAnanda — Trading Ideas and Scripts — TradingView
Cardano (ADA) starts to move — Last chance! (Fast Sudden Growth!)
Cardano (ADAUSDT) has been moving all this time very close to support. At first there was
some bullish action, in February, then the market consolidated sideways with a downward
bent. No news positive, nothing bullish, no buyers... Things change. Since Cardano...
by MasterAnanda                                                              7   31
Updated 6 hours ago

FET · The Start of a New Bullish Cycle, Nature & The Market
I always ask myself this question—I see the market as a unit rather than considering each
project and chart in isolation—can FETUSDT grow, massively, while Bitcoin crashes? Can
hundreds of altcoins produce sustained growth, several months in succession, while...
by MasterAnanda                                                              7    7
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


@pytest.mark.skipif(
    not pathlib.Path("MasterAnanda.pdf").exists(),
    reason="real PDF not present (CI environment)",
)
def test_real_pdf_extracts_at_least_100_posts(tmp_path):
    """Smoke test: against the real PDF, we should get many posts."""
    from scripts.maa.extract_posts import extract_pdf_to_jsonl

    out_path = tmp_path / "posts_raw.jsonl"
    unparsed_path = tmp_path / "posts_unparsed.jsonl"
    n_parsed, n_unparsed = extract_pdf_to_jsonl(
        pdf_path=pathlib.Path("MasterAnanda.pdf"),
        output_path=out_path,
        unparsed_path=unparsed_path,
    )

    assert n_parsed >= 100, f"expected >=100 posts, got {n_parsed}"
    # Unparsed rate should be <5% — sanity check, not strict
    assert n_unparsed < n_parsed * 0.05

    # Spot check: every parsed record has the required keys
    with open(out_path) as f:
        for line in f:
            r = json.loads(line)
            assert {"title", "description", "date_string", "comments",
                    "boosts", "is_updated", "page_number"} <= set(r.keys())
            assert isinstance(r["title"], str) and r["title"]
