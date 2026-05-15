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
analyze = _onchain.analyze
# fmt: on


def test_max_iters_default_matches_converging_cohort():
    """The TASK string promises a 'HARD 14-turn budget' but the analyze
    function signature was left at 12 in T3 onchain — a silent contradiction
    that gave opus a misleading budget. security and revenue were bumped
    to 14 in d072f2f and 552b8cb; onchain must match."""
    import inspect
    sig = inspect.signature(analyze)
    assert sig.parameters["max_iters"].default == 14


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


# ─── T3: converging-agent template (EMIT-EARLY block + named fast paths) ──

def test_task_has_hard_turn_budget():
    """The four agents that never max_iters all have an explicit budget
    reminder. Onchain must match."""
    assert "HARD" in _TASK
    assert "turn budget" in _TASK


def test_task_has_emit_early_block():
    """Visual structure matching tokenomics/team/moat — opus latches onto
    the EMIT-EARLY header to authorize short-path termination."""
    assert "EMIT-EARLY" in _TASK


def test_task_has_unavailable_fast_path():
    """Protocol-class tokens (AAVE/UNI/MORPHO/RNDR/etc.) have no Dune
    queries — all three tables empty. The LLM must short-circuit, not
    loop trying to find data that doesn't exist."""
    assert "UNAVAILABLE" in _TASK
    # The fast path should reference a turn target so opus emits early.
    assert "turn 3" in _TASK or "turn 4" in _TASK


def test_task_has_dau_only_named_fast_path():
    """Promote the T2b directive into a named fast path the LLM can match
    on the same line as DAU-ONLY."""
    assert "DAU-ONLY" in _TASK or "DAU ONLY" in _TASK


def test_task_has_stale_flow_named_fast_path():
    """T2b had this as a strategy bullet; T3 elevates it to a named path."""
    assert "STALE FLOW" in _TASK or "STALE-FLOW" in _TASK


def test_task_references_data_quality_hint():
    """T2c surfaces data_quality_hint in the manifest. The TASK must tell
    the LLM to read it on turn 1 so the plumbing is actually used."""
    assert "data_quality_hint" in _TASK
