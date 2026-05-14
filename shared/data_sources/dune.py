"""Dune Analytics SQL API client with TTL cache.

Free tier: 2,500 credits/month, requires DUNE_API_KEY env var (sign up at
https://dune.com/settings/api). Without a key, every call returns None — the
caller decides what to do (typically: mark the relevant field UNAVAILABLE).

Pattern: Dune queries are async. We POST to /query/{id}/execute, then poll
/execution/{exec_id}/status until QUERY_STATE_COMPLETED, then GET
/execution/{exec_id}/results. Each step is a paid HTTP call. The cache (in
agents/04_onchain/data/onchain.db, table _dune_cache) prevents re-execution
within a TTL window keyed on (query_id, params_hash).

Public surface: just execute_query(query_id, params=None, ttl_hours=24).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import sqlite3
import time
from typing import Optional

import requests

BASE = "https://api.dune.com/api/v1"
CACHE_DB_PATH = pathlib.Path(__file__).resolve().parents[2] / "agents" / "04_onchain" / "data" / "onchain.db"
_LOG = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 2
MAX_POLL_SECONDS = 180


def _api_key() -> Optional[str]:
    return (os.getenv("DUNE_API_KEY") or "").strip() or None


def _cache_key(params: Optional[dict]) -> str:
    """Deterministic hash of params dict (order-independent)."""
    if not params:
        return "noargs"
    canon = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha1(canon.encode()).hexdigest()[:16]


def _cache_get(query_id: int, key: str, ttl_hours: float) -> Optional[list]:
    if not CACHE_DB_PATH.exists():
        return None
    conn = sqlite3.connect(CACHE_DB_PATH)
    try:
        row = conn.execute(
            "SELECT fetched_at, rows_json FROM _dune_cache WHERE query_id=? AND params_hash=?",
            (query_id, key),
        ).fetchone()
    except sqlite3.OperationalError:
        return None  # table doesn't exist yet
    finally:
        conn.close()
    if not row:
        return None
    fetched_at, rows_json = row
    try:
        ts = dt.datetime.fromisoformat(fetched_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        age = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() / 3600.0
        if age > ttl_hours:
            return None
        return json.loads(rows_json)
    except (ValueError, json.JSONDecodeError):
        return None


def _cache_put(query_id: int, key: str, rows: list) -> None:
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS _dune_cache (
            query_id INTEGER NOT NULL, params_hash TEXT NOT NULL,
            fetched_at TEXT NOT NULL, rows_json TEXT NOT NULL,
            PRIMARY KEY (query_id, params_hash))""")
        conn.execute(
            "INSERT OR REPLACE INTO _dune_cache (query_id, params_hash, fetched_at, rows_json) "
            "VALUES (?, ?, ?, ?)",
            (query_id, key,
             dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             json.dumps(rows)),
        )
        conn.commit()
    finally:
        conn.close()


def _request(method: str, path: str, **kw) -> Optional[dict]:
    """One Dune API call. Returns parsed JSON or None on any error."""
    headers = {"X-Dune-API-Key": _api_key() or ""}
    headers.update(kw.pop("headers", {}))
    try:
        r = requests.request(method, f"{BASE}{path}", headers=headers, timeout=30, **kw)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        # ValueError catches JSON decode failures (200 with non-JSON body).
        _LOG.warning("Dune %s %s failed: %s", method, path, e)
        return None


def execute_query(
    *,
    query_id: int,
    params: Optional[dict] = None,
    ttl_hours: float = 24,
) -> Optional[list]:
    """Run a Dune query and return its rows. Cache for ttl_hours.

    Returns:
        list[dict] — rows from Dune's `result.rows`
        None — no API key, HTTP error, or query timed out
    """
    if not _api_key():
        _LOG.warning("DUNE_API_KEY not set; onchain fields requiring Dune will be null")
        return None

    cache_key = _cache_key(params)
    cached = _cache_get(query_id, cache_key, ttl_hours)
    if cached is not None:
        return cached

    body = {"query_parameters": params or {}}
    submit = _request("POST", f"/query/{query_id}/execute", json=body)
    if not submit or not submit.get("execution_id"):
        return None
    exec_id = submit["execution_id"]

    deadline = time.time() + MAX_POLL_SECONDS
    while time.time() < deadline:
        status = _request("GET", f"/execution/{exec_id}/status")
        if not status:
            return None
        state = status.get("state", "")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            _LOG.warning("Dune query %d failed: state=%s", query_id, state)
            return None
        time.sleep(POLL_INTERVAL_SEC)
    else:
        _LOG.warning("Dune query %d timed out after %ds", query_id, MAX_POLL_SECONDS)
        return None

    results = _request("GET", f"/execution/{exec_id}/results")
    if not results:
        return None
    rows = (results.get("result") or {}).get("rows", [])
    _cache_put(query_id, cache_key, rows)
    return rows
