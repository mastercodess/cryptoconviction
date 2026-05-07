"""Tests for scripts.maa.select_top_20."""
from __future__ import annotations

import json

import pytest

from scripts.maa.select_top_20 import (
    rank_scores,
    is_leverage_only,
    render_markdown,
)
from scripts.maa.match_projects import XlsxRow


def _xlsx(symbol: str, name: str, category: str = "Layer 1",
          asset_type: str = "Crypto") -> XlsxRow:
    return XlsxRow(rank=0, name=name, symbol=symbol, trading_pair=f"{symbol}USDT",
                   category=category, asset_type=asset_type, sentiment="Bullish",
                   signal="", tradingview_symbol=f"BINANCE:{symbol}USDT")


def test_rank_filters_non_crypto():
    scores = {"NVDA": {"score": 9}, "BTC": {"score": 7}, "ADA": {"score": 8}}
    xlsx_rows = [
        _xlsx("BTC", "Bitcoin"),
        _xlsx("ADA", "Cardano"),
        _xlsx("NVDA", "NVIDIA", category="Semiconductor", asset_type="Stock"),
    ]
    ranked = rank_scores(scores, xlsx_rows, top_n=20)
    syms = [r["symbol"] for r in ranked]
    assert "NVDA" not in syms
    assert "BTC" in syms and "ADA" in syms


def test_rank_sorts_by_score_desc():
    scores = {
        "BTC": {"score": 7, "latest_post": "2026-05-06", "post_count": 2},
        "ADA": {"score": 8, "latest_post": "2026-05-05", "post_count": 1},
        "FET": {"score": 9, "latest_post": "2026-05-04", "post_count": 1},
    }
    xlsx_rows = [_xlsx("BTC", "B"), _xlsx("ADA", "A"), _xlsx("FET", "F")]
    ranked = rank_scores(scores, xlsx_rows, top_n=20)
    assert [r["symbol"] for r in ranked] == ["FET", "ADA", "BTC"]


def test_rank_recency_breaks_score_tie():
    scores = {
        "ADA": {"score": 8, "latest_post": "2026-05-05", "post_count": 1},
        "BTC": {"score": 8, "latest_post": "2026-05-06", "post_count": 1},
    }
    xlsx_rows = [_xlsx("BTC", "B"), _xlsx("ADA", "A")]
    ranked = rank_scores(scores, xlsx_rows, top_n=20)
    assert [r["symbol"] for r in ranked] == ["BTC", "ADA"]


def test_rank_takes_top_n():
    scores = {f"S{i}": {"score": i, "latest_post": "2026-05-06", "post_count": 1}
              for i in range(1, 30)}
    xlsx_rows = [_xlsx(f"S{i}", f"P{i}") for i in range(1, 30)]
    ranked = rank_scores(scores, xlsx_rows, top_n=20)
    assert len(ranked) == 20


def test_rank_handles_fewer_than_n():
    """Per spec: if fewer than 20 qualify, return what we have."""
    scores = {"BTC": {"score": 8, "latest_post": "2026-05-06", "post_count": 1}}
    xlsx_rows = [_xlsx("BTC", "Bitcoin")]
    ranked = rank_scores(scores, xlsx_rows, top_n=20)
    assert len(ranked) == 1


def test_is_leverage_only():
    """⚠ flag fires when thesis_type=='leverage' AND no fundamental signals."""
    leverage = {"thesis_type": "leverage",
                "language_signals": ["10X long", "extreme buy"]}
    assert is_leverage_only(leverage) is True

    fundamental_mix = {"thesis_type": "leverage",
                       "language_signals": ["10X long", "ecosystem growth"]}
    assert is_leverage_only(fundamental_mix) is False

    not_leverage = {"thesis_type": "fundamental", "language_signals": []}
    assert is_leverage_only(not_leverage) is False


def test_markdown_includes_top_posts_and_runners_up():
    ranked = [{
        "rank": 1, "symbol": "BTC", "name": "Bitcoin",
        "category": "Layer 1", "score": 9, "thesis_type": "fundamental",
        "rationale": "Strong macro setup.", "latest_post": "2026-05-06T11:00:00",
        "post_count": 5, "skepticism_flags": [],
        "language_signals": ["new ATH"],
        "top_posts": [
            {"date": "2026-05-06T11:00:00", "title": "BTC monthly harmonic"},
            {"date": "2026-05-05T08:00:00", "title": "BTC EMA reclaim"},
        ],
    }]
    runners_up = [{
        "rank": 21, "symbol": "DOGE", "name": "Dogecoin",
        "category": "Meme", "score": 6, "thesis_type": "leverage",
        "rationale": "Single post.", "latest_post": "2026-04-15T00:00:00",
        "post_count": 1, "skepticism_flags": [], "language_signals": [],
        "top_posts": [],
    }]
    md = render_markdown(ranked=ranked, runners_up=runners_up,
                         window_start="2026-03-30", window_end="2026-05-06")
    assert "BTC" in md
    assert "BTC monthly harmonic" in md
    assert "Runners-up" in md
    assert "DOGE" in md
    assert "/10" in md  # unit suffix from spec
