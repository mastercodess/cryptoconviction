"""Tests for scripts.maa.expand_registry."""
from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import patch, MagicMock

import pytest

from scripts.maa.expand_registry import (
    resolve_coingecko_id,
    resolve_chain_and_contract,
    resolve_defillama,
    classify_is_protocol,
    verify_contract_via_explorer,
    propose_token_row,
    PROTOCOL_CATEGORIES,
)


def test_classify_is_protocol_known_protocol_categories():
    for cat in PROTOCOL_CATEGORIES:
        assert classify_is_protocol(category=cat, defillama_protocol=None) is True


def test_classify_is_protocol_l1():
    assert classify_is_protocol(category="Layer 1", defillama_protocol=None) is False


def test_classify_is_protocol_meme():
    assert classify_is_protocol(category="Meme", defillama_protocol=None) is False


def test_classify_is_protocol_with_defillama_match():
    """If DefiLlama resolves the slug, it's a protocol regardless of xlsx category."""
    assert classify_is_protocol(category="Other", defillama_protocol="aave") is True


def test_resolve_coingecko_id_mocked():
    fake_list = [
        {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"},
        {"id": "cardano", "symbol": "ada", "name": "Cardano"},
    ]
    with patch("scripts.maa.expand_registry._get_coingecko_list",
               return_value=fake_list):
        assert resolve_coingecko_id("BTC", "Bitcoin") == "bitcoin"
        assert resolve_coingecko_id("ADA", "Cardano") == "cardano"
        assert resolve_coingecko_id("ZZZ", "Nonsense") is None


def test_resolve_chain_and_contract_evm_mocked():
    fake_coin = {
        "platforms": {"ethereum": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"},
        "asset_platform_id": "ethereum",
    }
    with patch("scripts.maa.expand_registry._get_coingecko_coin",
               return_value=fake_coin):
        chain, addr = resolve_chain_and_contract("uniswap")
    assert chain == "ethereum"
    assert addr.startswith("0x")


def test_resolve_chain_and_contract_native_l1():
    fake_coin = {"platforms": {}, "asset_platform_id": None}
    with patch("scripts.maa.expand_registry._get_coingecko_coin",
               return_value=fake_coin):
        chain, addr = resolve_chain_and_contract("bitcoin")
    assert addr is None


def test_verify_contract_etherscan_mocked():
    """Successful verification: explorer page contains expected symbol."""
    fake_html = '<html>...title>Ethereum (ETH) ...'
    with patch("scripts.maa.expand_registry._fetch_url",
               return_value=fake_html):
        ok = verify_contract_via_explorer(
            chain="ethereum",
            address="0x0000000000000000000000000000000000000000",
            expected_symbol="ETH",
        )
    assert ok is True


def test_verify_contract_mismatch_returns_false():
    fake_html = '<html>...title>Wrong Token (WRONG) ...'
    with patch("scripts.maa.expand_registry._fetch_url",
               return_value=fake_html):
        ok = verify_contract_via_explorer(
            chain="ethereum",
            address="0x0000000000000000000000000000000000000000",
            expected_symbol="LINK",
        )
    assert ok is False


def test_propose_resolved_row_complete():
    """End-to-end with all resolutions mocked → resolved row, no errors."""
    with ExitStack() as stack:
        stack.enter_context(patch("scripts.maa.expand_registry.resolve_coingecko_id",
                                  return_value="cardano"))
        stack.enter_context(patch("scripts.maa.expand_registry.resolve_chain_and_contract",
                                  return_value=("cardano", None)))
        stack.enter_context(patch("scripts.maa.expand_registry.resolve_defillama",
                                  return_value=None))
        stack.enter_context(patch("scripts.maa.expand_registry.verify_contract_via_explorer",
                                  return_value=True))
        ok, row = propose_token_row(
            symbol="ADA", name="Cardano", category="Layer 1"
        )
    assert ok is True
    assert row["symbol"] == "ADA"
    assert row["coingecko_id"] == "cardano"
    assert row["is_protocol"] is False
    assert row["chain"] == "cardano"
    assert row["contract_address"] is None


def test_propose_unresolved_row_explorer_mismatch():
    with ExitStack() as stack:
        stack.enter_context(patch("scripts.maa.expand_registry.resolve_coingecko_id",
                                  return_value="link"))
        stack.enter_context(patch("scripts.maa.expand_registry.resolve_chain_and_contract",
                                  return_value=("ethereum", "0xWRONG")))
        stack.enter_context(patch("scripts.maa.expand_registry.resolve_defillama",
                                  return_value="chainlink"))
        stack.enter_context(patch("scripts.maa.expand_registry.verify_contract_via_explorer",
                                  return_value=False))
        ok, row = propose_token_row(
            symbol="LINK", name="Chainlink", category="Oracle"
        )
    assert ok is False
    assert row["reason"] == "explorer_symbol_mismatch"
