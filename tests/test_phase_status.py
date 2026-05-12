"""Tests for the phase status writer / reader."""
from __future__ import annotations
import json
import pathlib
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
