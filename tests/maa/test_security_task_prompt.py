"""Tests for agents/03_security/analyze.py _TASK content and max_iters.

Security is one of the three agents historically failing on max_iters
(TRX, ORCA, RNDR — confirmed via report grep). The four converging
agents (tokenomics/team/moat/macro) all carry an EMIT-EARLY block with
named fast paths and a 14-turn budget. This brings security into that
pattern, grounded in the actual TRX security data shape (8 audits, 2
unresolved high, 11 exploits worst MAJOR, multisig_only — hits the
RECENT-MAJOR fast path → tier 2 by turn 3).
"""
from __future__ import annotations

import importlib

# fmt: off
_security = importlib.import_module("agents.03_security.analyze")
_TASK = _security._TASK
analyze = _security.analyze
# fmt: on


def test_max_iters_default_matches_converging_cohort():
    """Tokenomics/team/moat/macro use 14. Security has been on 12,
    correlating with its higher fallback rate."""
    import inspect
    sig = inspect.signature(analyze)
    assert sig.parameters["max_iters"].default == 14


def test_task_has_hard_turn_budget():
    assert "HARD" in _TASK
    assert "turn budget" in _TASK


def test_task_has_emit_early_block():
    assert "EMIT-EARLY" in _TASK


def test_task_has_immutable_fast_path():
    """Bitcoin, Litecoin, and similar fixed-supply chains with no upgrade
    mechanism should hit this — emit tier 5 by turn 3."""
    assert "IMMUTABLE" in _TASK


def test_task_has_recent_major_exploit_fast_path():
    """The path TRX hits: 11 exploits, worst MAJOR. Should emit tier <= 2
    by turn 3 instead of looping trying to find more context."""
    has_named = "RECENT-MAJOR" in _TASK or "MAJOR-EXPLOIT" in _TASK
    assert has_named


def test_task_has_well_audited_fast_path():
    """≥3 audits, 0 unresolved high, 0 major exploits → tier 4 by turn 4."""
    assert "WELL-AUDITED" in _TASK or "WELL AUDITED" in _TASK


def test_task_references_data_quality_hint():
    """T2c surfaces this in the manifest; security should read it on turn 1
    if present (e.g., UNAVAILABLE for tokens with no collected audit data)."""
    assert "data_quality_hint" in _TASK


def test_task_regression_lists_existing_tables():
    """The four security tables should still be named for the fallback path."""
    assert "audit" in _TASK
    assert "exploit_history" in _TASK
    assert "code_health" in _TASK
    assert "dependency" in _TASK


def test_task_regression_describes_tier_scale():
    """The 1-5 tier scale prose should remain — the EMIT-EARLY rules
    augment but don't replace the underlying definitions."""
    assert "tier" in _TASK.lower()
    assert "5 =" in _TASK or "5=" in _TASK or "tier_5" in _TASK.lower() or "battle-tested" in _TASK.lower()
