"""Tests for the phase status writer / reader."""
from __future__ import annotations
import json
import pathlib
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
