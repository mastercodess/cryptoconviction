"""Tests for the CoinGecko client (focused on global_metrics for macro use)."""
from __future__ import annotations
import pytest
import requests
from unittest.mock import patch, MagicMock
from shared.data_sources.coingecko import global_metrics


def test_global_metrics_parses_response():
    fake = MagicMock(
        status_code=200,
        json=MagicMock(return_value={
            "data": {
                "active_cryptocurrencies": 17000,
                "total_market_cap": {"usd": 3.5e12},
                "total_volume": {"usd": 1.5e11},
                "market_cap_percentage": {"btc": 58.1, "eth": 14.2},
                "market_cap_change_percentage_24h_usd": 1.2,
                "updated_at": 1747008000,  # 2025-05-12
            }
        }),
    )
    with patch("requests.get", return_value=fake):
        result = global_metrics()
    assert result is not None
    assert result["btc_dominance_pct"] == 58.1
    assert result["total_mc_usd"] == 3.5e12
    assert result["total_mc_ex_btc_usd"] is not None  # derived
    # total_ex_btc = 3.5e12 * (1 - 0.581) = ~1.467e12
    assert abs(result["total_mc_ex_btc_usd"] - 3.5e12 * (1 - 0.581)) < 1e6
    assert result["as_of"] == "2025-05-12"


def test_global_metrics_returns_none_on_error():
    fake = MagicMock(
        status_code=500,
        raise_for_status=MagicMock(side_effect=requests.HTTPError("500")),
    )
    with patch("requests.get", return_value=fake):
        result = global_metrics()
    assert result is None


import os
from shared.data_sources.coingecko import _base, _headers, BASE, PRO_BASE


def test_no_key_uses_base_no_headers(monkeypatch):
    """Without any CoinGecko env var, use the free public BASE with no auth header."""
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    monkeypatch.delenv("COINGECKO_API_TIER", raising=False)
    assert _base() == BASE
    assert _headers() == {}


def test_demo_key_uses_base_with_demo_header(monkeypatch):
    """Default behavior when COINGECKO_API_KEY is set (no TIER): demo tier.
    Uses the public BASE endpoint + x-cg-demo-api-key header."""
    monkeypatch.setenv("COINGECKO_API_KEY", "CG-demo-test-key")
    monkeypatch.delenv("COINGECKO_API_TIER", raising=False)
    assert _base() == BASE
    assert _headers() == {"x-cg-demo-api-key": "CG-demo-test-key"}


def test_pro_tier_uses_pro_base_with_pro_header(monkeypatch):
    """Opt-in to pro tier via COINGECKO_API_TIER=pro: uses pro-api endpoint
    + x-cg-pro-api-key header."""
    monkeypatch.setenv("COINGECKO_API_KEY", "CG-pro-test-key")
    monkeypatch.setenv("COINGECKO_API_TIER", "pro")
    assert _base() == PRO_BASE
    assert _headers() == {"x-cg-pro-api-key": "CG-pro-test-key"}


def test_demo_tier_explicit(monkeypatch):
    """COINGECKO_API_TIER=demo behaves the same as the default (demo)."""
    monkeypatch.setenv("COINGECKO_API_KEY", "CG-demo-test-key")
    monkeypatch.setenv("COINGECKO_API_TIER", "demo")
    assert _base() == BASE
    assert _headers() == {"x-cg-demo-api-key": "CG-demo-test-key"}
