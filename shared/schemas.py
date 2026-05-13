"""
Pydantic models for every agent's output.

Why centralize: the Orchestrator (Agent 8) needs a stable, validated shape from
each specialist. Schema drift between agents is the silent killer of
multi-agent systems — every score must mean the same thing in the final
weighted average. Keep these in sync with the prompts in each agent's
analyze.py.

Convention: all numeric scores are 0–10 (10 = best), with explicit units.
'verdict' is one of: STRONG / NEUTRAL / WEAK. Free-text 'rationale' fields
are bounded to ~500 chars to keep orchestrator context manageable.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field

Verdict = Literal["STRONG", "NEUTRAL", "WEAK"]
Direction = Literal["INFLOW", "OUTFLOW", "MIXED", "FLAT"]


# ─── Agent 1 — Token Economics ──────────────────────────────────────────

class TokenomicsOutput(BaseModel):
    token_symbol: str
    fdv_risk_rating: int = Field(ge=1, le=10, description="10 = low risk; 1 = severe FDV overhang")
    inflation_pressure_score: int = Field(ge=1, le=10, description="10 = no inflation; 1 = hyperinflationary")
    value_accrual_verdict: Verdict
    concentration_risk_flag: bool
    top10_holding_pct: float = Field(ge=0, le=1)
    unlock_pressure_next_90d_pct: float = Field(ge=0, le=1)
    next_unlock_date: Optional[str] = None
    next_unlock_pct_of_supply: Optional[float] = None
    data_as_of: Optional[str] = Field(default=None, description="ISO date/datetime of the most recent DB row the agent consumed. Null = unknown/LLM-only.")
    rationale: str = Field(max_length=1500)
    composite_score: int = Field(ge=0, le=100, description="Agent's own 0-100 conviction on tokenomics")


# ─── Agent 2 — Protocol Revenue & Fundamentals ──────────────────────────

class RevenueOutput(BaseModel):
    token_symbol: str
    revenue_quality_score: int = Field(ge=1, le=10)
    growth_trend: Literal["ACCELERATING", "STEADY", "DECELERATING", "DECLINING"]
    valuation_vs_peers: Verdict
    real_yield_apr: Optional[float] = None
    inflationary_yield_apr: Optional[float] = None
    annualized_revenue_usd: Optional[float] = None
    p_s_ratio: Optional[float] = None
    data_as_of: Optional[str] = Field(default=None, description="ISO date/datetime of the most recent DB row the agent consumed. Null = unknown/LLM-only.")
    rationale: str = Field(max_length=1500)
    composite_score: int = Field(ge=0, le=100)


# ─── Agent 3 — Security & Code Integrity ────────────────────────────────

class SecurityOutput(BaseModel):
    token_symbol: str
    security_tier: int = Field(ge=1, le=5, description="1 = highest risk, 5 = battle-tested")
    audit_coverage_score: int = Field(ge=1, le=10)
    single_points_of_failure: list[str]
    centralization_risks: list[str]
    incident_history_severity: Literal["NONE", "MINOR", "MODERATE", "MAJOR", "CATASTROPHIC"]
    upgrade_mechanism: str
    data_as_of: Optional[str] = Field(default=None, description="ISO date/datetime of the most recent DB row the agent consumed. Null = unknown/LLM-only.")
    rationale: str = Field(max_length=1500)
    composite_score: int = Field(ge=0, le=100)


# ─── Agent 4 — On-Chain Intelligence ────────────────────────────────────

class OnChainOutput(BaseModel):
    token_symbol: str
    organic_activity_score: int = Field(ge=1, le=10)
    capital_flow_direction: Direction
    holder_quality_rating: int = Field(ge=1, le=10)
    growth_authenticity_verdict: Verdict
    retention_health_grade: Literal["A", "B", "C", "D", "F"]
    smart_money_stance: Literal["ACCUMULATING", "DISTRIBUTING", "NEUTRAL", "UNKNOWN"]
    data_as_of: Optional[str] = Field(default=None, description="ISO date/datetime of the most recent DB row the agent consumed. Null = unknown/LLM-only.")
    rationale: str = Field(max_length=1500)
    composite_score: int = Field(ge=0, le=100)


# ─── Agent 5 — Team & Investor Diligence ────────────────────────────────

class TeamOutput(BaseModel):
    token_symbol: str
    founder_credibility_score: int = Field(ge=1, le=10)
    vc_overhang_risk: Literal["LOW", "MODERATE", "HIGH", "EXTREME"]
    alignment_score: int = Field(ge=1, le=10)
    legal_exposure_flag: bool
    trust_tier: Literal["TIER_1", "TIER_2", "TIER_3", "UNKNOWN"]
    doxxed: bool
    data_as_of: Optional[str] = Field(default=None, description="ISO date/datetime of the most recent DB row the agent consumed. Null = unknown/LLM-only.")
    rationale: str = Field(max_length=1500)
    composite_score: int = Field(ge=0, le=100)


# ─── Agent 6 — Competitive Moat ─────────────────────────────────────────

class MoatOutput(BaseModel):
    token_symbol: str
    moat_strength_score: int = Field(ge=1, le=10)
    category_rank: int = Field(ge=1, description="1 = category leader")
    network_effect_type: Literal["LIQUIDITY", "DEVELOPER", "USER", "DATA", "NONE"]
    competitive_threat: Literal["LOW", "MODERATE", "HIGH", "SEVERE"]
    regulatory_relative_risk: Literal["LOWER", "SIMILAR", "HIGHER"]
    data_as_of: Optional[str] = Field(default=None, description="ISO date/datetime of the most recent DB row the agent consumed. Null = unknown/LLM-only.")
    rationale: str = Field(max_length=1500)
    composite_score: int = Field(ge=0, le=100)


# ─── Agent 7 — Macro & Cycle Positioning ────────────────────────────────

class MacroOutput(BaseModel):
    token_symbol: str
    cycle_phase: Literal["EARLY_BULL", "MID_BULL", "LATE_BULL", "DISTRIBUTION", "BEAR", "ACCUMULATION"]
    macro_rating: Verdict
    entry_timing_risk: int = Field(ge=1, le=10, description="10 = great entry, 1 = terrible")
    leverage_warning: bool
    btc_correlation_30d: Optional[float] = None
    data_as_of: Optional[str] = Field(default=None, description="ISO date/datetime of the most recent DB row the agent consumed. Null = unknown/LLM-only.")
    rationale: str = Field(max_length=1500)
    composite_score: int = Field(ge=0, le=100)


# ─── Agent 8 — Orchestrator (final report) ──────────────────────────────

class FinalVerdict(BaseModel):
    token_symbol: str
    weighted_conviction: int = Field(ge=0, le=100)
    final_verdict: Literal["STRONG_CONVICTION", "CONDITIONAL", "AVOID"]
    bull_case: list[str]
    bear_case: list[str]
    invalidation_conditions: list[str]
    recommended_position_pct: float = Field(ge=0, le=100)
    monitoring_checklist: list[str]
    category_scorecard: dict[str, int]   # agent_name -> 0-100
    auto_reject_triggered: bool
    auto_reject_reason: Optional[str] = None
    # Trust signals — populated by orchestrator so the reader can see how
    # much of the score is grounded and where the gaps are.
    missing_agents: list[str] = Field(default_factory=list)
    fallback_agents: list[str] = Field(default_factory=list)
    coverage_pct: float = Field(default=1.0, ge=0.0, le=1.0)
    # Per-agent freshness telemetry. data_as_of_per_agent maps agent_name -> ISO string
    # of the most-recent data the agent's output references. stale_agents lists the
    # agents whose data exceeds config.red_flags.max_data_age_hours.
    data_as_of_per_agent: dict[str, str] = Field(default_factory=dict)
    stale_agents: list[str] = Field(default_factory=list)
