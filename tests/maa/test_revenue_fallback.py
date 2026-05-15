"""Tests for agents/02_revenue/analyze.py:_fallback_output.

Specifically the NON-PROTOCOL detection threshold. T3 loosens it from
3-of-3-null to 2-of-3-null on {annualized_revenue_usd, tvl_usd,
p_s_ratio} — even one populated field (e.g. tvl_usd from a partial
DefiLlama pull) shouldn't force the protocol path when the other two
are missing.
"""
from __future__ import annotations

import importlib
import sqlite3

# fmt: off
_revenue = importlib.import_module("agents.02_revenue.analyze")
_fallback_output = _revenue._fallback_output
# fmt: on


def _make_db_with(snapshot_row: dict | None) -> sqlite3.Connection:
    """Build an in-memory revenue_snapshot table with a single row (or none)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
      CREATE TABLE revenue_snapshot (
        token_symbol TEXT, snapshot_at TEXT,
        annualized_revenue_usd REAL, tvl_usd REAL, p_s_ratio REAL,
        p_tvl_ratio REAL, real_yield_apr REAL, inflationary_yield_apr REAL
      )
    """)
    if snapshot_row is not None:
        cols = ", ".join(snapshot_row.keys())
        placeholders = ", ".join(["?"] * len(snapshot_row))
        conn.execute(
            f"INSERT INTO revenue_snapshot ({cols}) VALUES ({placeholders})",
            tuple(snapshot_row.values()),
        )
    return conn


def test_non_protocol_triggers_when_all_three_revenue_fields_null():
    """Regression: existing 3-of-3-null case still triggers NON-PROTOCOL."""
    conn = _make_db_with({
        "token_symbol": "XMR", "snapshot_at": "2026-05-14",
        "annualized_revenue_usd": None, "tvl_usd": None, "p_s_ratio": None,
        "real_yield_apr": None, "inflationary_yield_apr": 0.07,
    })
    out = _fallback_output("XMR", conn, why="test")
    assert out["revenue_quality_score"] == 4
    assert out["composite_score"] == 35


def test_non_protocol_triggers_when_two_of_three_null():
    """T3 loosening: only 2 of 3 null should suffice. LINK has p_s_ratio
    null + annualized_revenue null but tvl_usd populated."""
    conn = _make_db_with({
        "token_symbol": "LINK", "snapshot_at": "2026-05-14",
        "annualized_revenue_usd": None, "tvl_usd": 250_000_000.0,
        "p_s_ratio": None, "real_yield_apr": None, "inflationary_yield_apr": None,
    })
    out = _fallback_output("LINK", conn, why="test")
    assert out["revenue_quality_score"] == 4
    assert out["composite_score"] == 35


def test_protocol_path_when_only_one_field_null():
    """1-of-3 null is NOT non-protocol: enough data to attempt scoring."""
    conn = _make_db_with({
        "token_symbol": "AAVE", "snapshot_at": "2026-05-14",
        "annualized_revenue_usd": 200_000_000.0, "tvl_usd": 22_000_000_000.0,
        "p_s_ratio": None, "real_yield_apr": 0.03, "inflationary_yield_apr": None,
    })
    out = _fallback_output("AAVE", conn, why="test")
    # Protocol path returns score=5 (not 4), composite=50 (not 35)
    assert out["revenue_quality_score"] == 5
    assert out["composite_score"] == 50


def test_no_snapshot_row_still_triggers_non_protocol():
    """Regression: missing row → treat as non-protocol (most conservative)."""
    conn = _make_db_with(None)
    out = _fallback_output("UNKNOWN", conn, why="test")
    assert out["revenue_quality_score"] == 4
    assert out["composite_score"] == 35
