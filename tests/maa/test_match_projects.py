"""Tests for scripts.maa.match_projects."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.maa.match_projects import (
    build_xlsx_index,
    match_post_to_symbol,
    XlsxRow,
)


@pytest.fixture
def sample_xlsx_rows() -> list:
    return [
        XlsxRow(rank=1, name="Bitcoin", symbol="BTC", trading_pair="BTCUSDT",
                category="Layer 1", asset_type="Crypto", sentiment="Bullish",
                signal="Monthly harmonic", tradingview_symbol="BINANCE:BTCUSDT"),
        XlsxRow(rank=4, name="Solana", symbol="SOL", trading_pair="SOLUSDT",
                category="Layer 1", asset_type="Crypto", sentiment="Bullish",
                signal="...", tradingview_symbol="BINANCE:SOLUSDT"),
        XlsxRow(rank=6, name="Cardano", symbol="ADA", trading_pair="ADAUSDT",
                category="Layer 1", asset_type="Crypto", sentiment="Bullish",
                signal="...", tradingview_symbol="BINANCE:ADAUSDT"),
        XlsxRow(rank=99, name="Fetch.ai", symbol="FET", trading_pair="FETUSDT",
                category="AI", asset_type="Crypto", sentiment="Bullish",
                signal="...", tradingview_symbol="BINANCE:FETUSDT"),
    ]


def test_match_paren_symbol(sample_xlsx_rows):
    idx = build_xlsx_index(sample_xlsx_rows)
    sym, method = match_post_to_symbol(
        title="Cardano (ADA) starts to move — Last chance!",
        index=idx,
        llm_disambiguate=lambda *_, **__: None,
    )
    assert sym == "ADA"
    assert method == "paren_symbol"


def test_match_bullet_symbol(sample_xlsx_rows):
    idx = build_xlsx_index(sample_xlsx_rows)
    sym, method = match_post_to_symbol(
        title="FET · The Start of a New Bullish Cycle",
        index=idx,
        llm_disambiguate=lambda *_, **__: None,
    )
    assert sym == "FET"
    assert method == "bullet_symbol"


def test_match_trading_pair(sample_xlsx_rows):
    idx = build_xlsx_index(sample_xlsx_rows)
    sym, method = match_post_to_symbol(
        title="SOLUSDT breakout into 200",
        index=idx,
        llm_disambiguate=lambda *_, **__: None,
    )
    assert sym == "SOL"
    assert method == "trading_pair"


def test_match_fuzzy_name(sample_xlsx_rows):
    idx = build_xlsx_index(sample_xlsx_rows)
    sym, method = match_post_to_symbol(
        title="Bitcoin: ten years of building",
        index=idx,
        llm_disambiguate=lambda *_, **__: None,
    )
    assert sym == "BTC"
    assert method == "fuzzy_name"


def test_match_llm_fallback(sample_xlsx_rows):
    idx = build_xlsx_index(sample_xlsx_rows)
    sym, method = match_post_to_symbol(
        title="A weird post with no obvious symbol",
        index=idx,
        llm_disambiguate=lambda title, candidates: "FET",
    )
    assert sym == "FET"
    assert method == "llm"


def test_no_match_returns_none(sample_xlsx_rows):
    idx = build_xlsx_index(sample_xlsx_rows)
    sym, method = match_post_to_symbol(
        title="A weird post with no obvious symbol",
        index=idx,
        llm_disambiguate=lambda *_, **__: None,
    )
    assert sym is None
    assert method == "none"


def test_first_symbol_wins_for_multi_symbol(sample_xlsx_rows):
    """Multi-symbol post matches the first symbol per the spec."""
    idx = build_xlsx_index(sample_xlsx_rows)
    sym, method = match_post_to_symbol(
        title="BTC vs ETH analysis — but ADA is interesting too",
        index=idx,
        llm_disambiguate=lambda *_, **__: None,
    )
    # BTC is matched fuzzy/paren first
    assert sym == "BTC"
