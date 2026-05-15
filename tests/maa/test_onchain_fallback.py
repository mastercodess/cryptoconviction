"""Tests for agents/04_onchain/analyze.py:_fallback_output.

Specifically the DAU-only fast path that the T1 Dune integration exposes:
Dune's CHAIN_DAU query populates `activity_metric.dau` but not `mau` or
`dau_mau_ratio`. The fallback historically gated on `dau_mau_ratio` and
dropped the DAU signal on the floor — these tests pin down the corrected
behavior.
"""
from __future__ import annotations

import importlib
import sqlite3

# fmt: off
_onchain = importlib.import_module("agents.04_onchain.analyze")
_fallback_output = _onchain._fallback_output
# fmt: on


def _make_db_with(
    *,
    activity=(),       # list of (token, snapshot_at, dau, mau, dau_mau_ratio)
    flow=(),           # list of (token, date, inflow_usd, outflow_usd, net_usd)
    cohort=(),         # list of (token, snapshot_at, lth, sth, smart_money)
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE activity_metric (
        token_symbol TEXT, snapshot_at TEXT,
        dau INTEGER, wau INTEGER, mau INTEGER, dau_mau_ratio REAL,
        daily_tx_count INTEGER, new_addresses_7d INTEGER
      );
      CREATE TABLE exchange_flow (
        token_symbol TEXT, date TEXT,
        inflow_usd REAL, outflow_usd REAL, net_usd REAL
      );
      CREATE TABLE holder_cohort (
        token_symbol TEXT, snapshot_at TEXT,
        lth_supply_pct REAL, sth_supply_pct REAL, smart_money_stance TEXT
      );
    """)
    for t, snap, dau, mau, ratio in activity:
        conn.execute(
            "INSERT INTO activity_metric (token_symbol, snapshot_at, dau, mau, dau_mau_ratio) "
            "VALUES (?, ?, ?, ?, ?)",
            (t, snap, dau, mau, ratio),
        )
    for t, date, inflow, outflow, net in flow:
        conn.execute(
            "INSERT INTO exchange_flow VALUES (?, ?, ?, ?, ?)",
            (t, date, inflow, outflow, net),
        )
    for t, snap, lth, sth, sm in cohort:
        conn.execute(
            "INSERT INTO holder_cohort VALUES (?, ?, ?, ?, ?)",
            (t, snap, lth, sth, sm),
        )
    return conn


# ─── DAU-only fast path (the bug T2a fixes) ─────────────────────────────

def test_dau_only_4M_scores_from_magnitude_not_neutral_five():
    """TRX-shaped Dune row: dau=4.25M, mau=None, ratio=None.
    Should score 7 (1M ≤ dau < 5M tier), NOT default 5."""
    conn = _make_db_with(
        activity=[("TRX", "2026-05-14", 4_251_001, None, None)],
    )
    out = _fallback_output("TRX", conn, why="max_iters=12")
    assert out["organic_activity_score"] == 7


def test_dau_only_surfaces_value_in_rationale():
    """The actual DAU number must appear in rationale; the old text 'DAU/MAU: None'
    is misleading when we know DAU."""
    conn = _make_db_with(
        activity=[("TRX", "2026-05-14", 4_251_001, None, None)],
    )
    out = _fallback_output("TRX", conn, why="max_iters=12")
    assert "4,251,001" in out["rationale"]
    assert "DAU/MAU: None" not in out["rationale"]


def test_dau_only_5M_plus_scores_nine():
    """≥5M DAU is high-activity tier."""
    conn = _make_db_with(
        activity=[("ETH", "2026-05-14", 8_000_000, None, None)],
    )
    out = _fallback_output("ETH", conn, why="test")
    assert out["organic_activity_score"] == 9


def test_dau_only_200k_scores_five():
    """100K ≤ dau < 1M is moderate."""
    conn = _make_db_with(
        activity=[("SUI", "2026-05-14", 200_000, None, None)],
    )
    out = _fallback_output("SUI", conn, why="test")
    assert out["organic_activity_score"] == 5


def test_dau_only_under_100k_scores_three():
    """< 100K DAU is thin activity."""
    conn = _make_db_with(
        activity=[("ORDI", "2026-05-14", 25_000, None, None)],
    )
    out = _fallback_output("ORDI", conn, why="test")
    assert out["organic_activity_score"] == 3


# ─── Regression: existing dau_mau_ratio path must still work ────────────

def test_dau_mau_ratio_high_uses_ratio_based_scoring():
    """When dau_mau_ratio is populated, prefer it (more informative than DAU
    alone). Ratio ≥ 0.4 → score 9."""
    conn = _make_db_with(
        activity=[("BTC", "2026-05-14", 500_000, 1_000_000, 0.5)],
    )
    out = _fallback_output("BTC", conn, why="test")
    assert out["organic_activity_score"] == 9


def test_no_activity_row_defaults_to_five():
    """When nothing is in activity_metric, the existing neutral-5 default holds."""
    conn = _make_db_with()
    out = _fallback_output("UNKNOWN", conn, why="test")
    assert out["organic_activity_score"] == 5
    assert "DAU" not in out["rationale"] or "unknown" in out["rationale"].lower()
