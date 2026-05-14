"""Integration tests for the API-backed onchain collect_one()."""
from __future__ import annotations
import importlib
import json
import pathlib
import sqlite3
import sys
import pytest
from unittest.mock import patch


def _setup_onchain_module(tmp_path, monkeypatch):
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    onchain_collect = importlib.import_module("agents.04_onchain.collect")
    monkeypatch.setattr(onchain_collect, "DB_PATH", tmp_path / "onchain.db")
    monkeypatch.setattr(onchain_collect, "SIDECAR_DIR", tmp_path / "sidecars")
    conn = sqlite3.connect(tmp_path / "onchain.db")
    conn.executescript((repo_root / "agents" / "04_onchain" / "schema.sql").read_text())
    conn.commit()
    conn.close()
    return onchain_collect, repo_root


def test_collect_one_chain_class_populates_activity_from_dune(tmp_path, monkeypatch):
    """For a layer-1 token like TRX, collect_one calls Dune CHAIN_DAU and
    writes the matching row to activity_metric."""
    onchain_collect, _ = _setup_onchain_module(tmp_path, monkeypatch)

    fake_dau_rows = [
        {"chain": "ethereum", "daily_active_addresses": 450000},
        {"chain": "tron", "daily_active_addresses": 2100000},
        {"chain": "solana", "daily_active_addresses": 1200000},
    ]
    fake_flow_rows = [
        {"chain": "tron", "inflow_usd": 50e6, "outflow_usd": 80e6,
         "net_usd": -30e6, "date": "2026-05-12"},
    ]

    def fake_exec(*, query_id, params=None, ttl_hours=24):
        from shared.data_sources._dune_queries import CHAIN_DAU, CEX_FLOWS_BY_CHAIN
        if query_id == CHAIN_DAU:
            return fake_dau_rows
        if query_id == CEX_FLOWS_BY_CHAIN:
            return fake_flow_rows
        return None

    with patch.object(onchain_collect, "execute_query", side_effect=fake_exec):
        result = onchain_collect.collect_one("TRX")
    assert result.get("ok") is True
    assert result.get("data_quality") == "GOOD"

    conn = sqlite3.connect(tmp_path / "onchain.db")
    am = conn.execute(
        "SELECT dau, daily_tx_count FROM activity_metric WHERE token_symbol='TRX'"
    ).fetchone()
    flow = conn.execute(
        "SELECT inflow_usd, outflow_usd, net_usd FROM exchange_flow WHERE token_symbol='TRX'"
    ).fetchone()
    conn.close()
    assert am[0] == 2100000
    assert flow[0] == 50e6
    assert flow[2] == -30e6


def test_collect_one_btc_class_skips_lth_when_query_disabled(tmp_path, monkeypatch):
    """For BTC-class chains, since BTC_LTH_STH is None this round, collect_one
    must skip the LTH/STH query and still populate activity_metric (BTC chain
    in DAU rows) + a UNKNOWN-stance holder_cohort row."""
    onchain_collect, _ = _setup_onchain_module(tmp_path, monkeypatch)

    fake_dau_rows = [{"chain": "bitcoin", "daily_active_addresses": 950000}]

    def fake_exec(*, query_id, params=None, ttl_hours=24):
        from shared.data_sources._dune_queries import CHAIN_DAU, CEX_FLOWS_BY_CHAIN
        if query_id == CHAIN_DAU:
            return fake_dau_rows
        if query_id == CEX_FLOWS_BY_CHAIN:
            return []  # no CEX flow data for bitcoin in our query
        return None

    with patch.object(onchain_collect, "execute_query", side_effect=fake_exec):
        result = onchain_collect.collect_one("BCH")
    assert result.get("ok") is True

    conn = sqlite3.connect(tmp_path / "onchain.db")
    cohort = conn.execute(
        "SELECT lth_supply_pct, sth_supply_pct, smart_money_stance FROM holder_cohort "
        "WHERE token_symbol='BCH'"
    ).fetchone()
    conn.close()
    # LTH/STH stay null (BTC_LTH_STH query is None — skipped)
    assert cohort[0] is None
    assert cohort[1] is None
    assert cohort[2] == "UNKNOWN"


def test_collect_one_other_class_marks_unavailable(tmp_path, monkeypatch):
    """For 'other' categories (meme, ordinals, telegram-game, ai-infra), mark
    sidecar UNAVAILABLE and don't write to DB."""
    onchain_collect, _ = _setup_onchain_module(tmp_path, monkeypatch)

    with patch.object(onchain_collect, "execute_query", return_value=None):
        result = onchain_collect.collect_one("DOGE")
    assert result.get("ok") is True
    assert result.get("data_quality") == "UNAVAILABLE"

    conn = sqlite3.connect(tmp_path / "onchain.db")
    am_count = conn.execute(
        "SELECT COUNT(*) FROM activity_metric WHERE token_symbol='DOGE'"
    ).fetchone()[0]
    conn.close()
    assert am_count == 0


def test_collect_one_handles_dune_unavailable(tmp_path, monkeypatch):
    """When DUNE_API_KEY is not set (execute_query returns None for everything),
    mark UNAVAILABLE rather than crash."""
    onchain_collect, _ = _setup_onchain_module(tmp_path, monkeypatch)

    with patch.object(onchain_collect, "execute_query", return_value=None):
        result = onchain_collect.collect_one("TRX")
    assert result.get("ok") is True
    assert result.get("data_quality") == "UNAVAILABLE"


def test_collect_one_chain_not_in_dune_response_marks_partial_or_unavailable(tmp_path, monkeypatch):
    """If Dune returns rows but our chain isn't in them, the result must NOT
    claim GOOD data_quality. It should be PARTIAL or UNAVAILABLE."""
    onchain_collect, _ = _setup_onchain_module(tmp_path, monkeypatch)

    # DAU rows exist but Tron is missing
    rows_without_tron = [
        {"chain": "ethereum", "daily_active_addresses": 450000},
        {"chain": "solana", "daily_active_addresses": 1200000},
    ]

    def fake_exec(*, query_id, params=None, ttl_hours=24):
        return rows_without_tron  # same for both queries

    with patch.object(onchain_collect, "execute_query", side_effect=fake_exec):
        result = onchain_collect.collect_one("TRX")
    assert result.get("data_quality") in ("UNAVAILABLE", "PARTIAL")
