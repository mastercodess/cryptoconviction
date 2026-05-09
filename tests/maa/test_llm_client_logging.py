"""Tests for shared.llm_client cost-log emission (additive, env-var gated)."""
from __future__ import annotations

import json
import os
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared import llm_client


@pytest.fixture
def fake_anthropic_response():
    """Mock the .messages.create() return shape we depend on."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hello world")],
        usage=SimpleNamespace(input_tokens=42, output_tokens=8),
    )


def _patch_client(monkeypatch, fake_response):
    fake_messages = MagicMock()
    fake_messages.create = MagicMock(return_value=fake_response)
    fake_client = MagicMock()
    fake_client.messages = fake_messages
    monkeypatch.setattr(llm_client, "_client", lambda: fake_client)
    return fake_messages


def test_no_log_when_env_var_unset(monkeypatch, fake_anthropic_response, tmp_path):
    """When MAA_RUN_LOG is unset, no log file is written."""
    monkeypatch.delenv("MAA_RUN_LOG", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _patch_client(monkeypatch, fake_anthropic_response)

    result = llm_client.sub_lm("hello")
    assert result == "hello world"

    # No log file should exist
    log_path = tmp_path / "run_log.jsonl"
    assert not log_path.exists()


def test_log_line_written_when_env_var_set(monkeypatch, fake_anthropic_response, tmp_path):
    """When MAA_RUN_LOG is set, one JSON line is appended per call."""
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setenv("MAA_RUN_LOG", str(log_path))
    monkeypatch.setenv("MAA_AGENT_NAME", "03_security")
    monkeypatch.setenv("MAA_ACTION", "analyze")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _patch_client(monkeypatch, fake_anthropic_response)

    llm_client.sub_lm("hello")

    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["model"] == llm_client.DEFAULT_SUB_MODEL
    assert record["prompt_tokens"] == 42
    assert record["completion_tokens"] == 8
    assert record["agent_name"] == "03_security"
    assert record["action"] == "analyze"
    assert "ts" in record
    # Sonnet 4.6: 42 * 3/1M + 8 * 15/1M = 0.000126 + 0.000120 = 0.000246
    assert record["cost_usd"] == pytest.approx(0.000246, rel=1e-3)


def test_multiple_calls_append(monkeypatch, fake_anthropic_response, tmp_path):
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setenv("MAA_RUN_LOG", str(log_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _patch_client(monkeypatch, fake_anthropic_response)

    llm_client.sub_lm("a")
    llm_client.sub_lm("b")
    llm_client.sub_lm("c")

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 3


def test_unknown_model_logs_null_cost(monkeypatch, fake_anthropic_response, tmp_path):
    """Unknown model does NOT raise; cost_usd is null and the call still works."""
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setenv("MAA_RUN_LOG", str(log_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _patch_client(monkeypatch, fake_anthropic_response)

    result = llm_client.sub_lm("hello", model="future-claude-99")
    assert result == "hello world"

    record = json.loads(log_path.read_text().strip().splitlines()[0])
    assert record["model"] == "future-claude-99"
    assert record["cost_usd"] is None


def test_missing_agent_name_uses_unknown(monkeypatch, fake_anthropic_response, tmp_path):
    """If MAA_AGENT_NAME isn't set, log emits 'unknown' instead of crashing."""
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setenv("MAA_RUN_LOG", str(log_path))
    monkeypatch.delenv("MAA_AGENT_NAME", raising=False)
    monkeypatch.delenv("MAA_ACTION", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _patch_client(monkeypatch, fake_anthropic_response)

    llm_client.sub_lm("hello")
    record = json.loads(log_path.read_text().strip().splitlines()[0])
    assert record["agent_name"] == "unknown"
    assert record["action"] == "unknown"


def test_rlm_root_turn_logs_opus_call(monkeypatch, fake_anthropic_response, tmp_path):
    """Regression test for the cost-logging bypass in shared/rlm.py:_root_turn.

    The MAA batch cap depends on EVERY messages.create() call producing a
    log line. _root_turn calls Anthropic directly (not via sub_lm), so we
    explicitly verify it routes through _emit_usage_log.
    """
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setenv("MAA_RUN_LOG", str(log_path))
    monkeypatch.setenv("MAA_AGENT_NAME", "01_tokenomics")
    monkeypatch.setenv("MAA_ACTION", "analyze")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    from shared import rlm

    # Patch the Anthropic class that _root_turn instantiates internally.
    # _root_turn does `from anthropic import Anthropic` locally, so we patch
    # the symbol on the anthropic module — the local `from ... import` will
    # then bind to our fake.
    fake_messages = MagicMock()
    fake_messages.create = MagicMock(return_value=fake_anthropic_response)
    fake_client = MagicMock()
    fake_client.messages = fake_messages

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: fake_client)

    # _root_turn signature: (history, system, *, model, max_tokens=2048)
    text = rlm._root_turn(
        [{"role": "user", "content": "hi"}],
        "you are a tester",
        model="claude-opus-4-7",
        max_tokens=1024,
    )
    assert text == "hello world"

    assert log_path.exists(), "MAA_RUN_LOG file should exist after _root_turn"
    record = json.loads(log_path.read_text().strip().splitlines()[-1])
    assert record["model"] == "claude-opus-4-7"
    assert record["agent_name"] == "01_tokenomics"
    assert record["action"] == "analyze"
    assert record["prompt_tokens"] == 42  # from fake_anthropic_response
    assert record["completion_tokens"] == 8
