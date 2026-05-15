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


from shared.freshness import (
    parse_iso, age_hours, is_stale, classify_agents,
)


def test_parse_iso_handles_date_and_datetime():
    assert parse_iso("2026-05-12") is not None
    assert parse_iso("2026-05-12T15:30:00+00:00") is not None
    assert parse_iso("") is None
    assert parse_iso(None) is None
    assert parse_iso("garbage") is None


def test_age_hours_with_fresh_data():
    now = dt.datetime.now(dt.timezone.utc)
    fresh = (now - dt.timedelta(hours=12)).isoformat()
    assert 11 < age_hours(fresh) < 13


def test_age_hours_with_null_input_returns_inf():
    import math
    assert math.isinf(age_hours(None))
    assert math.isinf(age_hours(""))


def test_is_stale_threshold():
    now = dt.datetime.now(dt.timezone.utc)
    fresh = (now - dt.timedelta(hours=24)).isoformat()
    stale = (now - dt.timedelta(hours=72)).isoformat()
    assert not is_stale(fresh, max_hours=48)
    assert is_stale(stale, max_hours=48)
    assert is_stale(None, max_hours=48)  # null counts as stale


def test_classify_agents_partitions_correctly():
    now = dt.datetime.now(dt.timezone.utc)
    fresh = (now - dt.timedelta(hours=12)).isoformat()
    stale = (now - dt.timedelta(hours=200)).isoformat()
    agents = {
        "tokenomics": {"data_as_of": fresh},
        "revenue": {"data_as_of": stale},
        "security": {"data_as_of": None},
        "onchain": {"data_as_of": fresh},
    }
    fresh_list, stale_list, per_agent = classify_agents(agents, max_hours=48)
    assert set(fresh_list) == {"tokenomics", "onchain"}
    assert set(stale_list) == {"revenue", "security"}
    assert per_agent["tokenomics"] == fresh
    assert per_agent["security"] == "unknown"


# ─── Item 2: per-agent freshness thresholds ─────────────────────────────

def test_classify_agents_honors_per_agent_threshold_override():
    """The 'audit_date' stamped by security is conceptually 'when the audit
    happened', not 'when we last collected.' Until a collector-side
    timestamp exists, allow security to use a wider threshold (6mo) so
    the gate doesn't fire on a 2022 audit that we re-verified yesterday."""
    now = dt.datetime.now(dt.timezone.utc)
    five_months_ago = (now - dt.timedelta(days=150)).isoformat()
    eight_months_ago = (now - dt.timedelta(days=240)).isoformat()
    agents = {
        "security": {"data_as_of": five_months_ago},
        "tokenomics": {"data_as_of": five_months_ago},
    }
    # Per-agent override: security gets 6mo (4320h), tokenomics uses default 48h.
    fresh, stale, _ = classify_agents(
        agents, max_hours=48, per_agent_max_hours={"security": 4320},
    )
    assert "security" in fresh, (
        "security with 5mo-old data_as_of must be fresh under 6mo threshold"
    )
    assert "tokenomics" in stale, (
        "tokenomics with 5mo-old data_as_of stays stale under default 48h"
    )


def test_classify_agents_per_agent_override_does_not_loosen_other_agents():
    """An override for one agent must not affect any other agent's threshold."""
    now = dt.datetime.now(dt.timezone.utc)
    week_old = (now - dt.timedelta(days=7)).isoformat()
    agents = {
        "security": {"data_as_of": week_old},
        "onchain":  {"data_as_of": week_old},
    }
    fresh, stale, _ = classify_agents(
        agents, max_hours=48, per_agent_max_hours={"security": 4320},
    )
    assert "security" in fresh
    assert "onchain" in stale


def test_classify_agents_exceeded_per_agent_threshold_is_stale():
    """If security data_as_of exceeds even the wide 6mo threshold, it's stale."""
    now = dt.datetime.now(dt.timezone.utc)
    ten_months_ago = (now - dt.timedelta(days=300)).isoformat()
    agents = {"security": {"data_as_of": ten_months_ago}}
    _, stale, _ = classify_agents(
        agents, max_hours=48, per_agent_max_hours={"security": 4320},
    )
    assert "security" in stale


def test_classify_agents_backward_compat_no_per_agent_arg():
    """Existing callers that don't pass per_agent_max_hours must keep working."""
    now = dt.datetime.now(dt.timezone.utc)
    fresh_ts = (now - dt.timedelta(hours=12)).isoformat()
    stale_ts = (now - dt.timedelta(hours=200)).isoformat()
    agents = {"a": {"data_as_of": fresh_ts}, "b": {"data_as_of": stale_ts}}
    fresh, stale, _ = classify_agents(agents, max_hours=48)
    assert fresh == ["a"]
    assert stale == ["b"]


def test_config_yaml_security_threshold_is_six_months():
    """The committed config must declare the security override at 4320h (6mo)."""
    import yaml
    import pathlib
    cfg = yaml.safe_load(pathlib.Path("config.yaml").read_text())
    per_agent = cfg.get("red_flags", {}).get("max_data_age_hours_per_agent", {})
    assert per_agent.get("security") == 4320, (
        f"config.yaml red_flags.max_data_age_hours_per_agent.security must be "
        f"4320 (6 months in hours). Got: {per_agent.get('security')}"
    )


# ─── Item 3: moat / macro freshness thresholds at 7d ────────────────────

def test_config_yaml_moat_threshold_is_seven_days():
    """Moat metrics (TVL, category rank, network-effect signal) shift on
    weekly-to-monthly timescales. 48h gate is wasted credits."""
    import yaml
    import pathlib
    cfg = yaml.safe_load(pathlib.Path("config.yaml").read_text())
    per_agent = cfg.get("red_flags", {}).get("max_data_age_hours_per_agent", {})
    assert per_agent.get("moat") == 168, (
        f"config.yaml red_flags.max_data_age_hours_per_agent.moat must be "
        f"168 (7 days in hours). Got: {per_agent.get('moat')}"
    )


def test_config_yaml_macro_threshold_is_seven_days():
    """Macro cycle phase, BTC correlation, and Fear&Greed are smoothed over
    windows by construction — sub-daily re-collection has no information
    value."""
    import yaml
    import pathlib
    cfg = yaml.safe_load(pathlib.Path("config.yaml").read_text())
    per_agent = cfg.get("red_flags", {}).get("max_data_age_hours_per_agent", {})
    assert per_agent.get("macro") == 168, (
        f"config.yaml red_flags.max_data_age_hours_per_agent.macro must be "
        f"168 (7 days in hours). Got: {per_agent.get('macro')}"
    )


def test_config_yaml_other_agents_keep_default_48h():
    """Regression: only security/moat/macro override the default. The
    asymmetry matters — onchain DAU and exchange flow genuinely go stale
    at the day timescale."""
    import yaml
    import pathlib
    cfg = yaml.safe_load(pathlib.Path("config.yaml").read_text())
    per_agent = cfg.get("red_flags", {}).get("max_data_age_hours_per_agent", {})
    overridden = set(per_agent.keys())
    assert overridden == {"security", "moat", "macro"}, (
        f"per-agent overrides must be exactly {{security, moat, macro}}. "
        f"Got: {overridden}"
    )


import json
import sqlite3
import pathlib
import sys
import importlib


def test_macro_analyze_emits_data_as_of(tmp_path, monkeypatch):
    """Macro agent's emitted JSON must include data_as_of from the most-recent
    macro_snapshot row (or null if no snapshot exists)."""
    # Set up a temp DB matching the macro schema
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    schema = (repo_root / "agents" / "07_macro" / "schema.sql").read_text()
    db_path = tmp_path / "macro.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.execute(
        "INSERT INTO macro_snapshot (snapshot_at, fear_greed_index) VALUES (?, ?)",
        ("2026-05-10T12:00:00+00:00", 65),
    )
    conn.commit()
    conn.close()

    # Patch DB_PATH in the macro analyze module
    sys.path.insert(0, str(repo_root))
    import importlib
    macro = importlib.import_module("agents.07_macro.analyze")
    monkeypatch.setattr(macro, "DB_PATH", db_path)
    monkeypatch.setattr(macro, "REPORTS_DIR", tmp_path / "reports")
    # Patch tokens.get to accept TRX
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())
    # Force the max_iters_reached fallback path explicitly
    monkeypatch.setattr(macro, "run_rlm",
                        lambda **kw: {"error": "max_iters_reached", "iters": 14})

    result = macro.analyze("TRX", max_iters=1)
    assert result["ok"], result
    out = json.loads((tmp_path / "reports" / "TRX" / "agent_07_macro.json").read_text())
    assert out["data_as_of"] == "2026-05-10T12:00:00+00:00"


def test_stamp_data_as_of_global_no_existing_value(tmp_path):
    """Global query (no symbol) populates data_as_of from MAX(ts_col)."""
    from shared.freshness import stamp_data_as_of
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE macro_snapshot (snapshot_at TEXT, fg INTEGER)")
    conn.execute("INSERT INTO macro_snapshot VALUES ('2026-05-01', 60)")
    conn.execute("INSERT INTO macro_snapshot VALUES ('2026-05-10', 65)")
    raw = {"composite_score": 50}
    stamp_data_as_of(raw, conn, table="macro_snapshot")
    assert raw["data_as_of"] == "2026-05-10"


def test_stamp_data_as_of_per_token(tmp_path):
    """Per-token query filters by token_symbol=?."""
    from shared.freshness import stamp_data_as_of
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE supply_snapshot (token_symbol TEXT, snapshot_at TEXT)")
    conn.execute("INSERT INTO supply_snapshot VALUES ('TRX', '2026-05-09')")
    conn.execute("INSERT INTO supply_snapshot VALUES ('TRX', '2026-05-11')")
    conn.execute("INSERT INTO supply_snapshot VALUES ('LINK', '2026-05-12')")
    raw = {}
    stamp_data_as_of(raw, conn, table="supply_snapshot", symbol="TRX")
    assert raw["data_as_of"] == "2026-05-11"


def test_stamp_data_as_of_preserves_existing(tmp_path):
    """If raw already has a non-null data_as_of, do not overwrite."""
    from shared.freshness import stamp_data_as_of
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE macro_snapshot (snapshot_at TEXT)")
    conn.execute("INSERT INTO macro_snapshot VALUES ('2026-05-10')")
    raw = {"data_as_of": "2026-05-12"}
    stamp_data_as_of(raw, conn, table="macro_snapshot")
    assert raw["data_as_of"] == "2026-05-12"


def test_stamp_data_as_of_empty_table(tmp_path):
    """Empty table sets data_as_of to None."""
    from shared.freshness import stamp_data_as_of
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE supply_snapshot (token_symbol TEXT, snapshot_at TEXT)")
    raw = {}
    stamp_data_as_of(raw, conn, table="supply_snapshot", symbol="TRX")
    assert raw["data_as_of"] is None


def test_orchestrator_check_red_flags_fires_on_stale_data():
    """_check_red_flags must auto-reject when any agent's data is stale and
    max_data_age_hours is configured."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    orch = importlib.import_module("agents.08_orchestrator.orchestrator")

    loaded = {
        "tokenomics": {"composite_score": 87, "top10_holding_pct": 0.1,
                       "unlock_pressure_next_90d_pct": 0.0},
        "security": {"composite_score": 81, "security_tier": 5},
    }
    rules = {
        "max_data_age_hours": 48,
        "require_security_agent": True,
        "reject_if_security_below": 5,
        "min_agent_coverage_pct": 0.0,
        "reject_if_holder_concentration_above": 0.5,
        "reject_if_unlock_pressure_next_90d_above": 0.25,
    }
    auto, reason = orch._check_red_flags(
        loaded, rules, coverage_pct=1.0, stale_agents=["revenue", "macro"]
    )
    assert auto is True
    assert "stale data" in reason
    assert "revenue" in reason and "macro" in reason
    # Reason text no longer hard-codes a single threshold value: per-agent
    # overrides can apply different cutoffs to different agents.
    assert "freshness threshold" in reason


def test_orchestrator_check_red_flags_no_stale_no_reject():
    """When stale_agents is empty, the staleness rule must not fire."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    orch = importlib.import_module("agents.08_orchestrator.orchestrator")

    loaded = {
        "tokenomics": {"composite_score": 87, "top10_holding_pct": 0.1,
                       "unlock_pressure_next_90d_pct": 0.0},
        "security": {"composite_score": 81, "security_tier": 5},
    }
    rules = {
        "max_data_age_hours": 48,
        "require_security_agent": True,
        "reject_if_security_below": 5,
        "min_agent_coverage_pct": 0.0,
    }
    auto, reason = orch._check_red_flags(
        loaded, rules, coverage_pct=1.0, stale_agents=[]
    )
    assert auto is False
    assert reason is None


def test_render_markdown_includes_stale_agents():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    orch = importlib.import_module("agents.08_orchestrator.orchestrator")
    fv = FinalVerdict(
        token_symbol="TRX", weighted_conviction=56, final_verdict="AVOID",
        bull_case=["a"], bear_case=["b"], invalidation_conditions=["c"],
        recommended_position_pct=0.0, monitoring_checklist=["d"],
        category_scorecard={"tokenomics": 87}, auto_reject_triggered=True,
        auto_reject_reason="stale data: macro exceeds 48h",
        stale_agents=["macro"],
        data_as_of_per_agent={"tokenomics": "2026-05-11", "macro": "unknown"},
    )
    md = orch._render_markdown(fv, {"tokenomics": 1.0})
    # Trust signals section should fire (stale_agents present)
    assert "## ⚠ Trust signals" in md
    assert "Stale agents" in md and "macro" in md
    # Data freshness section should be separate
    assert "## Data freshness" in md
    assert "tokenomics: 2026-05-11" in md
    assert "macro: unknown" in md


def test_render_markdown_no_trust_signals_when_healthy():
    """When all agents loaded fresh and no fallback, the warning section
    should NOT render — only the Data freshness section."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    orch = importlib.import_module("agents.08_orchestrator.orchestrator")
    fv = FinalVerdict(
        token_symbol="HEALTHY", weighted_conviction=78, final_verdict="STRONG_CONVICTION",
        bull_case=["a"], bear_case=["b"], invalidation_conditions=["c"],
        recommended_position_pct=4.0, monitoring_checklist=["d"],
        category_scorecard={"tokenomics": 80, "revenue": 75},
        auto_reject_triggered=False,
        # No missing, no fallback, full coverage, nothing stale
        missing_agents=[], fallback_agents=[], coverage_pct=1.0,
        stale_agents=[],
        data_as_of_per_agent={"tokenomics": "2026-05-11", "revenue": "2026-05-12"},
    )
    md = orch._render_markdown(fv, {"tokenomics": 0.5, "revenue": 0.5})
    assert "## ⚠ Trust signals" not in md
    assert "## Data freshness" in md
    assert "tokenomics: 2026-05-11" in md


def test_team_output_accepts_rationale_up_to_1500_chars():
    """The 800-char limit was too tight — observed TRX team rationale was 892
    chars and got rejected. Bump to 1500 to accommodate richer team analysis."""
    long_rationale = "x" * 1200  # well above old 800, below new 1500
    out = TeamOutput(
        token_symbol="TEST", founder_credibility_score=5,
        vc_overhang_risk="MODERATE", alignment_score=5,
        legal_exposure_flag=False, trust_tier="TIER_2", doxxed=True,
        rationale=long_rationale, composite_score=50,
    )
    assert len(out.rationale) == 1200


def test_team_output_rejects_rationale_above_1500_chars():
    """New ceiling is 1500. Verify the new limit is enforced (not just gone)."""
    too_long = "x" * 1501
    with pytest.raises(Exception):  # Pydantic ValidationError
        TeamOutput(
            token_symbol="TEST", founder_credibility_score=5,
            vc_overhang_risk="MODERATE", alignment_score=5,
            legal_exposure_flag=False, trust_tier="TIER_2", doxxed=True,
            rationale=too_long, composite_score=50,
        )
