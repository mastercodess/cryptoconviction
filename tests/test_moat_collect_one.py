"""Integration tests for the API-backed moat collect_one()."""
from __future__ import annotations
import importlib
import json
import pathlib
import sqlite3
import sys
import pytest
from unittest.mock import patch


def _setup_moat_module(tmp_path, monkeypatch):
    """Common fixture: rewire DB_PATH + SIDECAR_DIR to tmp_path."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    moat_collect = importlib.import_module("agents.06_moat.collect")
    monkeypatch.setattr(moat_collect, "DB_PATH", tmp_path / "moat.db")
    monkeypatch.setattr(moat_collect, "SIDECAR_DIR", tmp_path / "sidecars")
    # Initialize schema
    conn = sqlite3.connect(tmp_path / "moat.db")
    conn.executescript((repo_root / "agents" / "06_moat" / "schema.sql").read_text())
    conn.commit()
    conn.close()
    return moat_collect, repo_root


def test_collect_one_chain_class_uses_chains_api(tmp_path, monkeypatch):
    """For a layer-1 token like TRX, collect must call chains() and populate
    competitor (top 5 chains excluding self) + market_share (self TVL share)."""
    moat_collect, repo_root = _setup_moat_module(tmp_path, monkeypatch)

    fake_chains = [
        {"name": "Ethereum", "tvl_usd": 80e9, "tokenSymbol": "ETH",
         "coingecko_id": "ethereum", "cmc_id": "1027"},
        {"name": "Solana", "tvl_usd": 12e9, "tokenSymbol": "SOL",
         "coingecko_id": "solana", "cmc_id": "5426"},
        {"name": "BSC", "tvl_usd": 9e9, "tokenSymbol": "BNB",
         "coingecko_id": "binancecoin", "cmc_id": "1839"},
        {"name": "Tron", "tvl_usd": 8e9, "tokenSymbol": "TRX",
         "coingecko_id": "tron", "cmc_id": "1958"},
        {"name": "Base", "tvl_usd": 7e9, "tokenSymbol": "ETH",
         "coingecko_id": "ethereum", "cmc_id": "1027"},
        {"name": "Arbitrum", "tvl_usd": 6e9, "tokenSymbol": "ARB",
         "coingecko_id": "arbitrum", "cmc_id": "11841"},
    ]
    with patch.object(moat_collect, "chains", return_value=fake_chains):
        result = moat_collect.collect_one("TRX")
    assert result.get("ok") is True
    assert result.get("data_quality") == "GOOD"

    # Verify DB: competitor table has top 5 chains excluding Tron
    conn = sqlite3.connect(tmp_path / "moat.db")
    rows = conn.execute(
        "SELECT competitor, tvl_usd FROM competitor WHERE token_symbol='TRX' "
        "ORDER BY tvl_usd DESC"
    ).fetchall()
    conn.close()
    names = [r[0] for r in rows]
    assert "Tron" not in names
    assert len(names) == 5
    assert names[0] == "Ethereum"

    # Verify market_share: TRX TVL / total category TVL
    conn = sqlite3.connect(tmp_path / "moat.db")
    ms_rows = conn.execute(
        "SELECT category, share_pct FROM market_share WHERE token_symbol='TRX'"
    ).fetchall()
    conn.close()
    assert len(ms_rows) == 1
    assert ms_rows[0][0] == "layer-1"
    total = 80e9 + 12e9 + 9e9 + 8e9 + 7e9 + 6e9  # 122e9
    expected_share = 8e9 / total
    assert abs(ms_rows[0][1] - expected_share) < 1e-6

    # Verify sidecar
    sidecar = json.loads((tmp_path / "sidecars" / "TRX" / "moat_research.json").read_text())
    assert sidecar["data_quality"] == "GOOD"
    assert sidecar["as_of"] is not None  # ISO date present


def test_collect_one_protocol_class_uses_protocols_api(tmp_path, monkeypatch):
    """For a defi-lending token like AAVE, collect must call protocols(category='Lending')
    and populate competitor + market_share."""
    moat_collect, repo_root = _setup_moat_module(tmp_path, monkeypatch)

    fake_protocols = [
        {"name": "Aave", "slug": "aave", "tvl_usd": 24e9, "mcap_usd": 2.2e9,
         "category": "Lending", "symbol": "AAVE", "coingecko_id": "aave"},
        {"name": "Compound", "slug": "compound", "tvl_usd": 3e9, "mcap_usd": 0.45e9,
         "category": "Lending", "symbol": "COMP", "coingecko_id": "compound-governance-token"},
        {"name": "Morpho", "slug": "morpho", "tvl_usd": 8e9, "mcap_usd": 1.1e9,
         "category": "Lending", "symbol": "MORPHO", "coingecko_id": "morpho"},
    ]
    with patch.object(moat_collect, "protocols", return_value=fake_protocols):
        result = moat_collect.collect_one("AAVE")
    assert result.get("ok") is True
    assert result.get("data_quality") == "GOOD"

    conn = sqlite3.connect(tmp_path / "moat.db")
    comp_rows = conn.execute(
        "SELECT competitor, tvl_usd, market_cap_usd FROM competitor "
        "WHERE token_symbol='AAVE' ORDER BY tvl_usd DESC"
    ).fetchall()
    ms_rows = conn.execute(
        "SELECT category, share_pct FROM market_share WHERE token_symbol='AAVE'"
    ).fetchall()
    conn.close()
    comp_names = [r[0] for r in comp_rows]
    assert "Aave" not in comp_names  # self excluded
    assert "Morpho" in comp_names and "Compound" in comp_names
    assert ms_rows[0][0] == "Lending"
    expected = 24e9 / (24e9 + 8e9 + 3e9)
    assert abs(ms_rows[0][1] - expected) < 1e-6


def test_collect_one_other_class_marks_unavailable(tmp_path, monkeypatch):
    """For 'other' categories (meme, ordinals, etc.), no DefiLlama route
    exists. Mark sidecar data_quality=UNAVAILABLE and do not populate DB."""
    moat_collect, repo_root = _setup_moat_module(tmp_path, monkeypatch)

    result = moat_collect.collect_one("DOGE")
    assert result.get("ok") is True
    assert result.get("data_quality") == "UNAVAILABLE"

    conn = sqlite3.connect(tmp_path / "moat.db")
    comp_count = conn.execute(
        "SELECT COUNT(*) FROM competitor WHERE token_symbol='DOGE'"
    ).fetchone()[0]
    ms_count = conn.execute(
        "SELECT COUNT(*) FROM market_share WHERE token_symbol='DOGE'"
    ).fetchone()[0]
    conn.close()
    assert comp_count == 0
    assert ms_count == 0

    sidecar = json.loads((tmp_path / "sidecars" / "DOGE" / "moat_research.json").read_text())
    assert sidecar["data_quality"] == "UNAVAILABLE"
    assert "no DefiLlama route" in sidecar.get("notes", "")


def test_collect_one_handles_api_failure(tmp_path, monkeypatch):
    """When chains() returns None (API error), collect must mark UNAVAILABLE
    rather than crash."""
    moat_collect, repo_root = _setup_moat_module(tmp_path, monkeypatch)

    with patch.object(moat_collect, "chains", return_value=None):
        result = moat_collect.collect_one("TRX")
    assert result.get("ok") is True
    assert result.get("data_quality") == "UNAVAILABLE"


def test_collect_one_self_tvl_zero_marks_unavailable(tmp_path, monkeypatch):
    """When the matched DefiLlama row has TVL=0 (e.g., decommissioned entry),
    mark UNAVAILABLE rather than emit a meaningless 0% market share."""
    moat_collect, repo_root = _setup_moat_module(tmp_path, monkeypatch)

    fake_chains = [
        {"name": "Ethereum", "tvl_usd": 80e9, "tokenSymbol": "ETH",
         "coingecko_id": "ethereum", "cmc_id": "1027"},
        {"name": "Tron", "tvl_usd": 0, "tokenSymbol": "TRX",
         "coingecko_id": "tron", "cmc_id": "1958"},
    ]
    with patch.object(moat_collect, "chains", return_value=fake_chains):
        result = moat_collect.collect_one("TRX")
    assert result.get("ok") is True
    assert result.get("data_quality") == "UNAVAILABLE"

    sidecar = json.loads(
        (tmp_path / "sidecars" / "TRX" / "moat_research.json").read_text())
    assert "TVL=0" in sidecar["notes"]
