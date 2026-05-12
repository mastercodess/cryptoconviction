"""Tests for the freshness contract: schema fields + age computation."""
from __future__ import annotations
import datetime as dt
import pytest
from shared.schemas import (
    TokenomicsOutput, RevenueOutput, SecurityOutput, OnChainOutput,
    TeamOutput, MoatOutput, MacroOutput, FinalVerdict,
)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def test_tokenomics_output_accepts_data_as_of():
    out = TokenomicsOutput(
        token_symbol="TRX", fdv_risk_rating=7, inflation_pressure_score=8,
        value_accrual_verdict="NEUTRAL", concentration_risk_flag=False,
        top10_holding_pct=0.1, unlock_pressure_next_90d_pct=0.0,
        rationale="t", composite_score=70, data_as_of=_now_iso(),
    )
    assert out.data_as_of is not None


def test_tokenomics_output_data_as_of_optional():
    """Old records (no data_as_of) must still parse."""
    out = TokenomicsOutput(
        token_symbol="TRX", fdv_risk_rating=7, inflation_pressure_score=8,
        value_accrual_verdict="NEUTRAL", concentration_risk_flag=False,
        top10_holding_pct=0.1, unlock_pressure_next_90d_pct=0.0,
        rationale="t", composite_score=70,
    )
    assert out.data_as_of is None


def test_final_verdict_stale_agents_default_empty():
    fv = FinalVerdict(
        token_symbol="TRX", weighted_conviction=56, final_verdict="CONDITIONAL",
        bull_case=["a"], bear_case=["b"], invalidation_conditions=["c"],
        recommended_position_pct=0.6, monitoring_checklist=["d"],
        category_scorecard={"tokenomics": 87}, auto_reject_triggered=False,
    )
    assert fv.stale_agents == []
    assert fv.data_as_of_per_agent == {}


def test_final_verdict_stale_agents_populated():
    fv = FinalVerdict(
        token_symbol="TRX", weighted_conviction=56, final_verdict="AVOID",
        bull_case=["a"], bear_case=["b"], invalidation_conditions=["c"],
        recommended_position_pct=0.0, monitoring_checklist=["d"],
        category_scorecard={"tokenomics": 87}, auto_reject_triggered=True,
        auto_reject_reason="macro stale by 287h",
        stale_agents=["macro"], data_as_of_per_agent={"tokenomics": "2026-05-12"},
    )
    assert fv.stale_agents == ["macro"]
    assert fv.data_as_of_per_agent["tokenomics"] == "2026-05-12"
