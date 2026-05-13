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
