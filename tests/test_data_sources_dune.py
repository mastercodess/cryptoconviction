"""Tests for the Dune Analytics client."""
from __future__ import annotations
import json
import pathlib
import pytest
import requests
import time
from unittest.mock import patch, MagicMock
from shared.data_sources.dune import execute_query, _cache_key


def test_no_api_key_returns_none(monkeypatch):
    """Without DUNE_API_KEY, execute_query returns None — no crash, no LLM fabrication."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    result = execute_query(query_id=12345, ttl_hours=24)
    assert result is None


def test_cache_key_is_deterministic_for_same_params():
    """params_hash must be stable for the same params dict (order-independent)."""
    k1 = _cache_key({"chain": "ethereum", "limit": 100})
    k2 = _cache_key({"limit": 100, "chain": "ethereum"})  # reverse order
    assert k1 == k2


def test_cache_key_differs_for_different_params():
    k1 = _cache_key({"chain": "ethereum"})
    k2 = _cache_key({"chain": "tron"})
    assert k1 != k2


def test_execute_query_polls_until_complete(tmp_path, monkeypatch):
    """Happy path: execute → poll (EXECUTING, COMPLETED) → results."""
    monkeypatch.setenv("DUNE_API_KEY", "test-key")
    import shared.data_sources.dune as dune_mod
    monkeypatch.setattr(dune_mod, "CACHE_DB_PATH", tmp_path / "cache.db")

    execute_resp = MagicMock(status_code=200,
                              json=MagicMock(return_value={"execution_id": "exec-123"}))
    pending_resp = MagicMock(status_code=200,
                              json=MagicMock(return_value={"state": "QUERY_STATE_EXECUTING"}))
    done_resp = MagicMock(status_code=200,
                           json=MagicMock(return_value={"state": "QUERY_STATE_COMPLETED"}))
    results_resp = MagicMock(status_code=200,
                              json=MagicMock(return_value={"result": {"rows": [
                                  {"chain": "ethereum", "dau": 450000},
                                  {"chain": "tron", "dau": 2100000},
                              ]}}))

    call_seq = [execute_resp, pending_resp, done_resp, results_resp]
    with patch("requests.request", side_effect=call_seq) as gm, \
         patch("time.sleep") as _sleep:
        result = execute_query(query_id=12345, ttl_hours=24)

    assert result == [
        {"chain": "ethereum", "dau": 450000},
        {"chain": "tron", "dau": 2100000},
    ]
    assert gm.call_count == 4


def test_execute_query_returns_cached_within_ttl(tmp_path, monkeypatch):
    """Second call with same params within TTL returns from cache (no HTTP)."""
    monkeypatch.setenv("DUNE_API_KEY", "test-key")
    import shared.data_sources.dune as dune_mod
    monkeypatch.setattr(dune_mod, "CACHE_DB_PATH", tmp_path / "cache.db")

    seq_first = [
        MagicMock(status_code=200, json=MagicMock(return_value={"execution_id": "x"})),
        MagicMock(status_code=200, json=MagicMock(return_value={"state": "QUERY_STATE_COMPLETED"})),
        MagicMock(status_code=200, json=MagicMock(return_value={"result": {"rows": [{"v": 1}]}})),
    ]
    with patch("requests.request", side_effect=seq_first) as gm1, \
         patch("time.sleep"):
        r1 = execute_query(query_id=999, ttl_hours=24)
    assert r1 == [{"v": 1}]
    assert gm1.call_count == 3

    with patch("requests.request") as gm2:
        r2 = execute_query(query_id=999, ttl_hours=24)
    assert r2 == [{"v": 1}]
    assert gm2.call_count == 0


def test_execute_query_refetches_after_ttl_expiry(tmp_path, monkeypatch):
    """When cache entry is older than ttl_hours, refetch."""
    monkeypatch.setenv("DUNE_API_KEY", "test-key")
    import shared.data_sources.dune as dune_mod
    monkeypatch.setattr(dune_mod, "CACHE_DB_PATH", tmp_path / "cache.db")

    import sqlite3, datetime as dt
    conn = sqlite3.connect(tmp_path / "cache.db")
    conn.execute("""CREATE TABLE _dune_cache (
        query_id INTEGER NOT NULL, params_hash TEXT NOT NULL, fetched_at TEXT NOT NULL,
        rows_json TEXT NOT NULL, PRIMARY KEY (query_id, params_hash))""")
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25)).isoformat()
    conn.execute("INSERT INTO _dune_cache VALUES (?, ?, ?, ?)",
                 (777, _cache_key({}), old_ts, json.dumps([{"old": True}])))
    conn.commit(); conn.close()

    seq = [
        MagicMock(status_code=200, json=MagicMock(return_value={"execution_id": "y"})),
        MagicMock(status_code=200, json=MagicMock(return_value={"state": "QUERY_STATE_COMPLETED"})),
        MagicMock(status_code=200, json=MagicMock(return_value={"result": {"rows": [{"fresh": True}]}})),
    ]
    with patch("requests.request", side_effect=seq), patch("time.sleep"):
        r = execute_query(query_id=777, ttl_hours=24)
    assert r == [{"fresh": True}]


def test_execute_query_returns_none_on_http_error(tmp_path, monkeypatch):
    """If Dune returns 5xx or similar, return None — no crash."""
    monkeypatch.setenv("DUNE_API_KEY", "test-key")
    import shared.data_sources.dune as dune_mod
    monkeypatch.setattr(dune_mod, "CACHE_DB_PATH", tmp_path / "cache.db")

    fake = MagicMock(status_code=500,
                     raise_for_status=MagicMock(side_effect=requests.HTTPError("500")))
    with patch("requests.request", return_value=fake):
        result = execute_query(query_id=12345, ttl_hours=24)
    assert result is None
