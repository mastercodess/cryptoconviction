"""Tests for shared.rlm verbose mode.

When the canary T4 run falls back on a token, the verbose log is the
only telemetry we have for diagnosing where opus spent its turns.
Verbose must therefore capture BOTH directions of the conversation:
  - what the LLM emitted (the code it ran)
  - what the REPL returned (the data it saw)

Without the REPL side we can't tell whether opus was stuck on empty
results, large/garbled dumps, or unfortunate prompt wording — the
exact distinction that decides whether to add more EMIT-EARLY rules,
cap probe types, or tweak the schema.
"""
from __future__ import annotations

import pytest

from shared import rlm


def test_verbose_prints_root_reply(capsys, monkeypatch):
    """Regression: verbose still shows the LLM's reply per iter (existing behavior)."""
    replies = iter([
        '```python\nFINAL = {"x": 1}\n```',
    ])
    monkeypatch.setattr(rlm, "_root_turn", lambda *a, **kw: next(replies))
    rlm.run_rlm(
        agent_name="test",
        environment={"token_symbol": "TEST"},
        task="test",
        output_schema={"x": "int"},
        max_iters=3,
        verbose=True,
    )
    out = capsys.readouterr().out
    assert "iter 0" in out
    assert "FINAL" in out  # the LLM's reply contained 'FINAL ='


def test_verbose_prints_repl_feedback(capsys, monkeypatch):
    """New behavior (the gap): verbose must also print the REPL's response,
    so the turn-by-turn probe sequence is fully reconstructible from logs
    without re-running the (paid) RLM."""
    # The LLM emits code that computes a value the REPL only produces at
    # runtime — testing for "13" alone is unsafe (could appear in the code
    # source). We check for the "iter N repl" section header that the new
    # verbose block must emit, then for the computed value within it.
    replies = iter([
        '```python\nprint(6 + 7)\n```',
        '```python\nFINAL = {"x": 1}\n```',
    ])
    monkeypatch.setattr(rlm, "_root_turn", lambda *a, **kw: next(replies))
    rlm.run_rlm(
        agent_name="test",
        environment={"token_symbol": "TEST"},
        task="test",
        output_schema={"x": "int"},
        max_iters=3,
        verbose=True,
    )
    out = capsys.readouterr().out
    # Marker header for the REPL-output section (paired with "--- iter N root ---").
    assert "iter 0 repl" in out, (
        f"verbose log must include a 'iter N repl' section header per turn. Got:\n{out}"
    )
    # The runtime-computed value (13 = 6+7) must appear in the captured REPL stdout.
    # Locate the iter 0 repl section and assert the value lives inside it.
    repl_start = out.index("iter 0 repl")
    repl_section = out[repl_start:]
    assert "13" in repl_section, (
        f"REPL section must include the value computed at runtime. Got section:\n{repl_section}"
    )


def test_verbose_prints_repl_feedback_for_no_output_case(capsys, monkeypatch):
    """Even when the LLM's code prints nothing (silent assignment, e.g.
    `x = something`), the verbose log should record that — the absence
    of stdout is itself diagnostic ('opus was probing but not extracting')."""
    replies = iter([
        '```python\n_silent = 42\n```',         # no stdout
        '```python\nFINAL = {"x": 1}\n```',
    ])
    monkeypatch.setattr(rlm, "_root_turn", lambda *a, **kw: next(replies))
    rlm.run_rlm(
        agent_name="test",
        environment={"token_symbol": "TEST"},
        task="test",
        output_schema={"x": "int"},
        max_iters=3,
        verbose=True,
    )
    out = capsys.readouterr().out
    # The "(no output)" sentinel that history records should also appear in verbose.
    assert "no output" in out.lower() or "(no output)" in out


def test_non_verbose_remains_silent(capsys, monkeypatch):
    """Regression: verbose=False (the default) must not print anything to stdout."""
    replies = iter([
        '```python\nprint("should-not-appear")\n```',
        '```python\nFINAL = {"x": 1}\n```',
    ])
    monkeypatch.setattr(rlm, "_root_turn", lambda *a, **kw: next(replies))
    rlm.run_rlm(
        agent_name="test",
        environment={"token_symbol": "TEST"},
        task="test",
        output_schema={"x": "int"},
        max_iters=3,
        verbose=False,
    )
    out = capsys.readouterr().out
    assert "should-not-appear" not in out
    assert "iter 0" not in out
