"""Tests for scripts.maa.run_conviction_batch."""
from __future__ import annotations

import json
import pathlib
import shutil
from unittest.mock import MagicMock, patch

import pytest

from scripts.maa.run_conviction_batch import (
    read_cumulative_costs,
    truncate_run_log,
    preserve_existing_reports,
    should_skip_agent,
    halt_if_no_flag,
    BatchAbortedError,
)


def test_read_cumulative_costs(tmp_path):
    log = tmp_path / "run_log.jsonl"
    log.write_text("\n".join([
        json.dumps({"agent_name": "01_tokenomics", "cost_usd": 0.10}),
        json.dumps({"agent_name": "01_tokenomics", "cost_usd": 0.20}),
        json.dumps({"agent_name": "02_revenue",   "cost_usd": 0.50}),
        json.dumps({"agent_name": "02_revenue",   "cost_usd": 1.00}),
    ]) + "\n")

    per_agent, total = read_cumulative_costs(log)
    assert per_agent["01_tokenomics"] == pytest.approx(0.30)
    assert per_agent["02_revenue"] == pytest.approx(1.50)
    assert total == pytest.approx(1.80)


def test_read_cumulative_handles_null_cost(tmp_path):
    log = tmp_path / "run_log.jsonl"
    log.write_text(json.dumps({"agent_name": "x", "cost_usd": None}) + "\n")
    per_agent, total = read_cumulative_costs(log)
    assert total == 0.0


def test_truncate_run_log(tmp_path):
    log = tmp_path / "run_log.jsonl"
    log.write_text("garbage\nfrom\nprior\nrun\n")
    truncate_run_log(log)
    assert log.read_text() == ""


def test_preserve_existing_reports(tmp_path):
    reports = tmp_path / "reports"
    aave_dir = reports / "AAVE"
    aave_dir.mkdir(parents=True)
    (aave_dir / "conviction.json").write_text("{}")
    preserve_existing_reports(["AAVE", "NEW1"], reports_dir=reports,
                              suffix="_pre_maa_2026-05-06")
    assert (aave_dir / "_pre_maa_2026-05-06" / "conviction.json").exists()
    # NEW1 has no existing folder — no-op, no error
    assert not (reports / "NEW1").exists()


def test_should_skip_agent_02_for_non_protocol():
    token_meta = {"is_protocol": False}
    assert should_skip_agent("02_revenue", token_meta) is True


def test_should_skip_agent_02_for_protocol():
    token_meta = {"is_protocol": True}
    assert should_skip_agent("02_revenue", token_meta) is False


def test_should_not_skip_other_agents():
    token_meta = {"is_protocol": False}
    for agent in ("01_tokenomics", "03_security", "04_onchain",
                  "05_team", "06_moat", "07_macro"):
        assert should_skip_agent(agent, token_meta) is False


def test_halt_if_no_flag_raises_when_missing(tmp_path):
    flag = tmp_path / "registry.committed.flag"
    with pytest.raises(BatchAbortedError, match="registry.committed.flag"):
        halt_if_no_flag(flag)


def test_halt_if_no_flag_passes_when_present(tmp_path):
    flag = tmp_path / "registry.committed.flag"
    flag.touch()
    halt_if_no_flag(flag)  # Should not raise
