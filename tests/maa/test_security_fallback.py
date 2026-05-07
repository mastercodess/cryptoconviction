"""Tests for the immutable-L1 carveout in agents/03_security/analyze.py:_fallback_output."""
from __future__ import annotations

import sqlite3

import pytest

# fmt: off
import importlib
_security = importlib.import_module("agents.03_security.analyze")
_fallback_output = _security._fallback_output
# fmt: on


def _make_db_with(
    *,
    audits=(),         # list of (token, severity_high)
    exploits=(),       # list of (token, severity)
    upgrade_mechanism: str | None = None,
    deps=(),           # list of (token, provider, risk_level)
    token: str = "BTC",
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE audit (token_symbol TEXT, severity_high INTEGER);
      CREATE TABLE exploit_history (token_symbol TEXT, severity TEXT);
      CREATE TABLE code_health (token_symbol TEXT, upgrade_mechanism TEXT);
      CREATE TABLE dependency (token_symbol TEXT, provider TEXT, risk_level TEXT);
    """)
    for t, sev in audits:
        conn.execute("INSERT INTO audit VALUES (?, ?)", (t, sev))
    for t, sev in exploits:
        conn.execute("INSERT INTO exploit_history VALUES (?, ?)", (t, sev))
    if upgrade_mechanism is not None:
        conn.execute(
            "INSERT INTO code_health VALUES (?, ?)", (token, upgrade_mechanism)
        )
    for t, prov, risk in deps:
        conn.execute("INSERT INTO dependency VALUES (?, ?, ?)", (t, prov, risk))
    return conn


def test_immutable_no_audits_no_exploits_returns_tier_5():
    """BTC-shaped token: immutable + no audits + no exploits → tier 5."""
    conn = _make_db_with(upgrade_mechanism="immutable", token="BTC")
    out = _fallback_output("BTC", conn, why="test")
    assert out["security_tier"] == 5
    assert out["upgrade_mechanism"] == "immutable"
    assert out["incident_history_severity"] == "NONE"
    assert "battle-tested" in out["rationale"].lower() or "immutable" in out["rationale"].lower()


def test_immutable_with_minor_exploit_still_tier_5():
    """Immutable chain with only minor incidents stays tier 5."""
    conn = _make_db_with(
        upgrade_mechanism="immutable",
        exploits=[("BTC", "minor")],
        token="BTC",
    )
    out = _fallback_output("BTC", conn, why="test")
    assert out["security_tier"] == 5


def test_immutable_with_major_exploit_drops_to_tier_2():
    """Immutable doesn't override a major historical exploit."""
    conn = _make_db_with(
        upgrade_mechanism="immutable",
        exploits=[("BTC", "major")],
        token="BTC",
    )
    out = _fallback_output("BTC", conn, why="test")
    assert out["security_tier"] == 2


def test_non_immutable_no_audits_returns_tier_2():
    """Solidity protocol with no audits stays at the existing tier=2 default."""
    conn = _make_db_with(upgrade_mechanism="multisig_only", token="LINK")
    out = _fallback_output("LINK", conn, why="test")
    assert out["security_tier"] == 2


def test_non_immutable_three_audits_clean_returns_tier_4():
    """Existing chain still works: 3 audits + 0 high severity → tier 4."""
    conn = _make_db_with(
        upgrade_mechanism="multisig_with_timelock",
        audits=[("AAVE", 0), ("AAVE", 0), ("AAVE", 0)],
        token="AAVE",
    )
    out = _fallback_output("AAVE", conn, why="test")
    assert out["security_tier"] == 4
