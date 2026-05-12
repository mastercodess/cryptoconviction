"""Tests for the phase status writer / reader."""
from __future__ import annotations
import importlib
import json
import pathlib
import sqlite3
import sys
import pytest
from shared.phase_status import write_phase, read_phase, summarize


def test_write_phase_creates_file(tmp_path):
    reports_dir = tmp_path / "reports"
    write_phase(
        symbol="TRX", phase="collect", reports_dir=reports_dir,
        per_agent={"01_tokenomics": "ok", "07_macro": "rc=1"},
        started_at="2026-05-12T10:00:00+00:00",
        ended_at="2026-05-12T10:05:00+00:00",
    )
    path = reports_dir / "TRX" / "_phase_status.json"
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["collect"]["per_agent"]["07_macro"] == "rc=1"
    assert payload["collect"]["started_at"] == "2026-05-12T10:00:00+00:00"


def test_write_phase_appends_without_overwriting(tmp_path):
    reports_dir = tmp_path / "reports"
    write_phase(symbol="TRX", phase="collect", reports_dir=reports_dir,
                per_agent={"01_tokenomics": "ok"},
                started_at="t1", ended_at="t2")
    write_phase(symbol="TRX", phase="analyze", reports_dir=reports_dir,
                per_agent={"01_tokenomics": "ok"},
                started_at="t3", ended_at="t4")
    payload = read_phase("TRX", reports_dir=reports_dir)
    assert set(payload.keys()) == {"collect", "analyze"}


def test_summarize_returns_pass_fail_counts(tmp_path):
    reports_dir = tmp_path / "reports"
    write_phase(symbol="TRX", phase="collect", reports_dir=reports_dir,
                per_agent={"01_tokenomics": "ok", "07_macro": "rc=1",
                           "02_revenue": "skipped(non-protocol)"},
                started_at="t1", ended_at="t2")
    s = summarize("TRX", phase="collect", reports_dir=reports_dir)
    assert s["passed"] == 1 and s["failed"] == 1 and s["skipped"] == 1


def test_run_one_token_writes_phase_status(tmp_path, monkeypatch):
    """End-to-end: invoke run_one_token with a no-op agent set and verify
    _phase_status.json exists with collect + analyze + orchestrate keys."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    import importlib
    batch = importlib.import_module("scripts.maa.run_conviction_batch")

    # Replace _run_subprocess with a stub that always succeeds (rc=0)
    monkeypatch.setattr(batch, "_run_subprocess", lambda **kw: 0)
    # No registry needed — token_meta resolves to is_protocol=True
    monkeypatch.setattr(batch, "_read_token_meta", lambda s: {"is_protocol": True})

    run_log = tmp_path / "run_log.jsonl"
    run_log.write_text("")
    reports_dir = tmp_path / "reports"

    summary = batch.run_one_token(
        symbol="TRX",
        agents=("01_tokenomics", "07_macro"),
        run_log=run_log,
        cumulative_batch_before=0.0,
        batch_cap=999.0,
        per_token_cap=999.0,
        reports_dir=reports_dir,  # new kwarg
    )
    path = reports_dir / "TRX" / "_phase_status.json"
    assert path.exists()
    payload = json.loads(path.read_text())
    assert "collect" in payload and "analyze" in payload and "orchestrate" in payload
    assert payload["collect"]["per_agent"]["01_tokenomics"] == "ok"


def test_analyze_writes_error_json_on_max_iters(tmp_path, monkeypatch):
    """When the RLM hits max_iters and the fallback path runs, analyze must
    write a sibling agent_07_macro.error.json that flags the non-convergence."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    import importlib
    import sqlite3
    macro = importlib.import_module("agents.07_macro.analyze")

    schema = (repo_root / "agents" / "07_macro" / "schema.sql").read_text()
    db_path = tmp_path / "macro.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.execute("INSERT INTO macro_snapshot (snapshot_at) VALUES (?)",
                 ("2026-05-11T08:00:00+00:00",))
    conn.commit()
    conn.close()
    monkeypatch.setattr(macro, "DB_PATH", db_path)
    monkeypatch.setattr(macro, "REPORTS_DIR", tmp_path / "reports")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())

    # Force max_iters_reached by stubbing run_rlm
    monkeypatch.setattr(macro, "run_rlm",
                        lambda **kw: {"error": "max_iters_reached", "iters": 14})

    result = macro.analyze("TRX", max_iters=1)
    assert result["ok"] is True  # fallback still produces a valid output
    out_dir = tmp_path / "reports" / "TRX"
    assert (out_dir / "agent_07_macro.json").exists()
    # NEW: error sibling exists
    assert (out_dir / "agent_07_macro.error.json").exists(), \
        "analyze should write a .error.json sibling when RLM did not converge"
    err = json.loads((out_dir / "agent_07_macro.error.json").read_text())
    assert err["reason"] == "max_iters_reached"


def test_analyze_preserves_fallback_flag_on_validation_failure(tmp_path, monkeypatch):
    """When fallback runs AND Pydantic validation then fails, the .error.json
    must include BOTH the validation error AND the fallback_used flag."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    macro = importlib.import_module("agents.07_macro.analyze")

    schema = (repo_root / "agents" / "07_macro" / "schema.sql").read_text()
    db_path = tmp_path / "macro.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.execute("INSERT INTO macro_snapshot (snapshot_at) VALUES (?)",
                 ("2026-05-11T08:00:00+00:00",))
    conn.commit()
    conn.close()
    monkeypatch.setattr(macro, "DB_PATH", db_path)
    monkeypatch.setattr(macro, "REPORTS_DIR", tmp_path / "reports")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())

    # Force RLM to return a fallback-style rationale BUT with an invalid field
    # (entry_timing_risk out of range) so Pydantic validation fails after fallback.
    bad_fallback = {
        "token_symbol": "TRX",
        "cycle_phase": "ACCUMULATION",
        "macro_rating": "NEUTRAL",
        "entry_timing_risk": 999,  # invalid (must be 1-10)
        "leverage_warning": False,
        "btc_correlation_30d": None,
        "rationale": "RLM did not converge (max_iters=14); fallback applied.",
        "composite_score": 50,
        "data_as_of": "2026-05-11",
    }
    monkeypatch.setattr(macro, "run_rlm", lambda **kw: bad_fallback)
    # Bypass the fallback recreation by making _fallback_output return the same bad data
    monkeypatch.setattr(macro, "_fallback_output", lambda *a, **k: bad_fallback)

    result = macro.analyze("TRX", max_iters=1)
    assert result["ok"] is False
    err = json.loads((tmp_path / "reports" / "TRX" / "agent_07_macro.error.json").read_text())
    # Validation error AND fallback flag must both be present
    assert "error" in err
    assert err.get("reason") == "max_iters_reached"
    assert err.get("fallback_used") is True


def test_validate_phase_cli(tmp_path, capsys, monkeypatch):
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    write_phase(symbol="TRX", phase="collect", reports_dir=tmp_path,
                per_agent={"01_tokenomics": "ok", "07_macro": "rc=1"},
                started_at="t1", ended_at="t2")
    import importlib
    validator = importlib.import_module("scripts.maa.validate_phase")
    rc = validator.main(["TRX", "--phase", "collect", "--reports-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert "passed=1" in captured.out and "failed=1" in captured.out
    assert rc == 1  # non-zero because something failed


def test_analyze_renames_stale_json_on_validation_failure(tmp_path, monkeypatch):
    """When today's analyze fails Pydantic validation, any pre-existing
    agent_NN.json from a prior run must be renamed to agent_NN.stale.json
    so the orchestrator can't silently load yesterday's data."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    macro = importlib.import_module("agents.07_macro.analyze")

    schema = (repo_root / "agents" / "07_macro" / "schema.sql").read_text()
    db_path = tmp_path / "macro.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.execute("INSERT INTO macro_snapshot (snapshot_at) VALUES (?)",
                 ("2026-05-11T08:00:00+00:00",))
    conn.commit()
    conn.close()
    monkeypatch.setattr(macro, "DB_PATH", db_path)
    monkeypatch.setattr(macro, "REPORTS_DIR", tmp_path / "reports")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())

    # Pre-create a "yesterday" agent_07_macro.json with previous data
    out_dir = tmp_path / "reports" / "TRX"
    out_dir.mkdir(parents=True)
    yesterday_payload = {"token_symbol": "TRX", "previous_run": "from yesterday"}
    (out_dir / "agent_07_macro.json").write_text(json.dumps(yesterday_payload))

    # Today's RLM returns a payload that fails Pydantic (entry_timing_risk out of range)
    bad_payload = {
        "token_symbol": "TRX",
        "cycle_phase": "MID_BULL",
        "macro_rating": "NEUTRAL",
        "entry_timing_risk": 999,  # invalid; must be 1-10
        "leverage_warning": False,
        "btc_correlation_30d": None,
        "rationale": "today's run, but invalid",
        "composite_score": 50,
        "data_as_of": "2026-05-12",
    }
    monkeypatch.setattr(macro, "run_rlm", lambda **kw: bad_payload)
    monkeypatch.setattr(macro, "_fallback_output", lambda *a, **k: bad_payload)

    result = macro.analyze("TRX", max_iters=1)
    assert result["ok"] is False
    # Yesterday's data should have been moved aside
    assert not (out_dir / "agent_07_macro.json").exists(), \
        "stale agent_07_macro.json must be renamed away on validation failure"
    assert (out_dir / "agent_07_macro.stale.json").exists()
    stale = json.loads((out_dir / "agent_07_macro.stale.json").read_text())
    assert stale == yesterday_payload, "stale file must preserve yesterday's content"
    # And today's failure should be flagged in .error.json
    assert (out_dir / "agent_07_macro.error.json").exists()


def test_analyze_cleans_up_stale_json_on_validation_success(tmp_path, monkeypatch):
    """When today's analyze succeeds, any pre-existing agent_NN.stale.json
    from a prior failure must be removed (the run has recovered)."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    macro = importlib.import_module("agents.07_macro.analyze")

    schema = (repo_root / "agents" / "07_macro" / "schema.sql").read_text()
    db_path = tmp_path / "macro.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.execute("INSERT INTO macro_snapshot (snapshot_at) VALUES (?)",
                 ("2026-05-11T08:00:00+00:00",))
    conn.commit()
    conn.close()
    monkeypatch.setattr(macro, "DB_PATH", db_path)
    monkeypatch.setattr(macro, "REPORTS_DIR", tmp_path / "reports")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())

    # Pre-create a leftover agent_07_macro.stale.json from a prior failure
    out_dir = tmp_path / "reports" / "TRX"
    out_dir.mkdir(parents=True)
    (out_dir / "agent_07_macro.stale.json").write_text(json.dumps({"old": "stale"}))

    # Today's RLM returns valid output (fallback path, will validate cleanly)
    monkeypatch.setattr(macro, "run_rlm",
                        lambda **kw: {"error": "max_iters_reached", "iters": 14})

    result = macro.analyze("TRX", max_iters=1)
    assert result["ok"] is True
    assert (out_dir / "agent_07_macro.json").exists()
    # Prior .stale.json should have been cleaned up
    assert not (out_dir / "agent_07_macro.stale.json").exists(), \
        "stale file from prior failure must be removed when current run succeeds"
