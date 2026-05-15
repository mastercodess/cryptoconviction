"""Tests for the onchain analyze._TASK string content.

These pin down the directives that prevent RLM divergence on:
  - chains where Dune populates `dau` but not `dau_mau_ratio` (every
    chain we have today)
  - chains where the most recent `exchange_flow` row is months stale
    (every non-EVM chain — Dune's CEX_FLOWS_BY_CHAIN doesn't label them)

Plus a phantom-table guard: `retention_cohort` is empty DB-wide, so the
prompt must not direct the LLM to probe it.
"""
from __future__ import annotations

import importlib

# fmt: off
_onchain = importlib.import_module("agents.04_onchain.analyze")
_TASK = _onchain._TASK
# fmt: on


def test_task_does_not_mention_retention_cohort_table():
    """retention_cohort has 0 rows across the entire DB. The prompt must
    not list it as a probe target — that's a phantom that consumes turns."""
    assert "retention_cohort" not in _TASK


def test_task_has_stale_flow_directive():
    """When the most recent exchange_flow row is >60 days old, the LLM
    should treat capital_flow_direction as FLAT and stop probing — not
    misinterpret stale Jan-2025 data as 'current INFLOW'. This is the
    exact failure mode that wasted TRX's turns 2-12."""
    # 60d cutoff is the canonical phrasing; allow alternates if the
    # author chose different wording so this test isn't over-fitted.
    has_threshold = "60 days" in _TASK or "60d" in _TASK
    has_flat_outcome = "FLAT" in _TASK
    assert has_threshold and has_flat_outcome, (
        f"TASK must direct the LLM to treat >60-day-old exchange_flow rows "
        f"as FLAT. Got:\n{_TASK}"
    )


def test_task_has_dau_only_scoring_directive():
    """When dau_mau_ratio is null but dau is populated (every Dune-populated
    chain), the LLM must score from DAU alone instead of treating the null
    ratio as 'no activity data'."""
    # Phrasings vary; assert the key concepts are present together.
    mentions_null_ratio = (
        "dau_mau_ratio is null" in _TASK
        or "ratio is null" in _TASK
        or "ratio null" in _TASK
    )
    mentions_dau_alone = "DAU alone" in _TASK or "from DAU" in _TASK
    assert mentions_null_ratio and mentions_dau_alone, (
        f"TASK must tell the LLM that when dau_mau_ratio is null but dau is "
        f"populated, score from DAU alone. Got:\n{_TASK}"
    )


def test_task_still_lists_remaining_tables():
    """Regression: the four tables we DO read should still be named."""
    assert "activity_metric" in _TASK
    assert "exchange_flow" in _TASK
    assert "holder_cohort" in _TASK
    assert "onchain_research_note" in _TASK
