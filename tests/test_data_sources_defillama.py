"""Tests for the DefiLlama API client (focused on chains() and protocols())."""
from __future__ import annotations
import pytest
import requests
from unittest.mock import patch, MagicMock
from shared.data_sources.defillama import chains, protocols


def test_chains_returns_normalized_records():
    fake = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[
            {"name": "Ethereum", "tvl": 80_000_000_000, "tokenSymbol": "ETH",
             "gecko_id": "ethereum", "cmcId": "1027"},
            {"name": "Tron",     "tvl":  8_000_000_000, "tokenSymbol": "TRX",
             "gecko_id": "tron", "cmcId": "1958"},
            {"name": "Solana",   "tvl": 12_000_000_000, "tokenSymbol": "SOL",
             "gecko_id": "solana", "cmcId": "5426"},
        ]),
    )
    with patch("requests.get", return_value=fake) as gm:
        result = chains()
    assert result is not None
    # Returned sorted by TVL descending
    assert result[0]["name"] == "Ethereum"
    assert result[0]["tvl_usd"] == 80_000_000_000
    assert result[0]["coingecko_id"] == "ethereum"
    # Endpoint check
    assert "api.llama.fi/chains" in gm.call_args.args[0]


def test_chains_returns_none_on_http_error():
    fake = MagicMock(
        status_code=500,
        raise_for_status=MagicMock(side_effect=requests.HTTPError("500")),
    )
    with patch("requests.get", return_value=fake):
        result = chains()
    assert result is None


def test_chains_skips_records_without_tvl():
    """Some DefiLlama chain entries have tvl: null. Skip them rather than NaN-propagate."""
    fake = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[
            {"name": "Ethereum", "tvl": 80_000_000_000},
            {"name": "ChainWithoutTVL", "tvl": None},
            {"name": "Tron", "tvl": 8_000_000_000},
        ]),
    )
    with patch("requests.get", return_value=fake):
        result = chains()
    names = [c["name"] for c in result]
    assert "ChainWithoutTVL" not in names
    assert "Ethereum" in names and "Tron" in names


def test_protocols_returns_normalized_records():
    fake = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[
            {"name": "Aave", "tvl": 24_000_000_000, "mcap": 2_200_000_000,
             "category": "Lending", "symbol": "AAVE", "slug": "aave",
             "gecko_id": "aave"},
            {"name": "Compound", "tvl": 3_000_000_000, "mcap": 450_000_000,
             "category": "Lending", "symbol": "COMP", "slug": "compound",
             "gecko_id": "compound-governance-token"},
            {"name": "Uniswap", "tvl": 8_000_000_000, "mcap": 6_500_000_000,
             "category": "Dexes", "symbol": "UNI", "slug": "uniswap",
             "gecko_id": "uniswap"},
        ]),
    )
    with patch("requests.get", return_value=fake) as gm:
        result = protocols()
    assert result is not None
    aave = next(p for p in result if p["slug"] == "aave")
    assert aave["tvl_usd"] == 24_000_000_000
    assert aave["mcap_usd"] == 2_200_000_000
    assert aave["category"] == "Lending"
    # Endpoint check
    assert "api.llama.fi/protocols" in gm.call_args.args[0]


def test_protocols_filters_by_category():
    fake = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[
            {"name": "Aave", "tvl": 24e9, "mcap": 2.2e9,
             "category": "Lending", "symbol": "AAVE", "slug": "aave"},
            {"name": "Compound", "tvl": 3e9, "mcap": 0.45e9,
             "category": "Lending", "symbol": "COMP", "slug": "compound"},
            {"name": "Uniswap", "tvl": 8e9, "mcap": 6.5e9,
             "category": "Dexes", "symbol": "UNI", "slug": "uniswap"},
        ]),
    )
    with patch("requests.get", return_value=fake):
        result = protocols(category="Lending")
    names = [p["name"] for p in result]
    assert set(names) == {"Aave", "Compound"}
    assert "Uniswap" not in names


def test_protocols_returns_none_on_http_error():
    fake = MagicMock(
        status_code=500,
        raise_for_status=MagicMock(side_effect=requests.HTTPError("500")),
    )
    with patch("requests.get", return_value=fake):
        result = protocols()
    assert result is None
