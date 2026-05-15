"""Tests for agents/02_revenue/analyze.py _TASK content and max_iters.

Revenue is the third agent in the failing trio (5.6% historical max_iters
rate vs 0-0.0% for the converging cohort). It already has a partial fast
path (NON-PROTOCOL FAST PATH) but two issues to address:

  - The condition is brittle (requires ALL THREE of revenue/tvl/p_s_ratio
    to be null; one populated field kicks the LLM into the slow PROTOCOL
    PATH where it may loop on sparse history).
  - No fast path for stale data: a revenue_snapshot row from 6 months ago
    leads the LLM down the PROTOCOL PATH even though the data is too old
    to support a current verdict.

Plus the structural fixes: HARD 14-turn budget (currently 12 at the bottom),
explicit EMIT-EARLY block, data_quality_hint reference.
"""
from __future__ import annotations

import importlib

# fmt: off
_revenue = importlib.import_module("agents.02_revenue.analyze")
_TASK = _revenue._TASK
analyze = _revenue.analyze
# fmt: on


def test_max_iters_default_matches_converging_cohort():
    import inspect
    sig = inspect.signature(analyze)
    assert sig.parameters["max_iters"].default == 14


def test_task_has_hard_14_turn_budget():
    """The HARD-budget reminder should be near the top, not buried at the
    bottom of the prompt."""
    assert "HARD 14-turn" in _TASK or "HARD 14 turn" in _TASK


def test_task_has_emit_early_block():
    """Same visual structure as tokenomics/team/moat/macro."""
    assert "EMIT-EARLY" in _TASK


def test_task_non_protocol_loosened_to_two_of_three_null():
    """Loosen the NON-PROTOCOL condition: 2-of-3 null fields is sufficient
    to call something a non-protocol. Currently requires all 3 null,
    which is too strict — even one populated field forces the slow path."""
    assert "2 of 3" in _TASK or "two of three" in _TASK or "2-of-3" in _TASK


def test_task_has_stale_protocol_data_fast_path():
    """If revenue_snapshot exists but as_of is >90 days old, emit FINAL
    by turn 4 with the cached numbers and a stale-data caveat."""
    has_named = "STALE PROTOCOL" in _TASK or "STALE-PROTOCOL" in _TASK or "STALE DATA" in _TASK
    assert has_named


def test_task_references_data_quality_hint():
    assert "data_quality_hint" in _TASK


def test_task_regression_lists_existing_tables():
    """The four revenue tables should still be named for the fallback path."""
    assert "revenue_snapshot" in _TASK
    assert "revenue_history" in _TASK
    assert "peer_comparison" in _TASK
    assert "revenue_research_note" in _TASK


def test_task_regression_keeps_real_vs_inflationary_yield_distinction():
    """The 'CRUCIAL' real-yield-vs-inflationary distinction is load-bearing
    for the composite score — must not be lost in the rewrite."""
    assert "real_yield" in _TASK
    assert "inflationary" in _TASK or "inflation" in _TASK


def test_task_regression_non_protocol_fast_path_still_exists():
    """The existing NON-PROTOCOL fast path stays — just loosened."""
    assert "NON-PROTOCOL" in _TASK
