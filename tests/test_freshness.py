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
    assert "48h" in reason


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
