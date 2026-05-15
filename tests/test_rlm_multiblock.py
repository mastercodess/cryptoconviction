"""Tests for multi-block code emission in shared.rlm.

The T4 canary on TRX security revealed that opus tends to batch 3-4
code blocks in one reply. The original `_extract_code` only ran the
first block, forcing the others to be re-emitted on subsequent turns —
3 of 14 security turns on TRX were pure re-emission overhead.

After this fix:
  - All ```python blocks in a reply execute against the persistent
    REPL in order, sharing globals.
  - A `print('---block-boundary---')` marker between blocks makes
    multi-block runs diagnosable from feedback alone (which block's
    output is which, where a runtime error occurred).
  - Single-block emission is byte-identical to the old behavior
    (re.findall on a single match collapses to the single-block case).

The _ROOT_SYSTEM prompt is also updated to declare the new contract
explicitly so opus's batching is a designed feature, not emergent.
"""
from __future__ import annotations

from shared import rlm
from shared.rlm import _extract_code


# ─── Unit: _extract_code behavior ───────────────────────────────────────

def test_extract_code_single_block_unchanged():
    """Regression: single block returns the block content stripped, same
    as before this fix."""
    reply = "Probing.\n```python\nprint('hello')\n```\nDone."
    assert _extract_code(reply) == "print('hello')"


def test_extract_code_no_code_blocks_returns_none():
    """Regression: replies with no code return None (the 'nudge' path)."""
    reply = "I'm thinking. Let me set FINAL."
    assert _extract_code(reply) is None


def test_extract_code_two_blocks_concatenated_with_boundary_marker():
    """Multiple blocks are joined with a print() marker so feedback can
    attribute output to source block."""
    reply = (
        "First step:\n"
        "```python\nx = 1\n```\n"
        "Then:\n"
        "```python\nprint(x + 1)\n```"
    )
    out = _extract_code(reply)
    assert "x = 1" in out
    assert "print(x + 1)" in out
    assert "---block-boundary---" in out
    # And the marker must appear BETWEEN the two blocks, not before or after.
    pos_b1 = out.index("x = 1")
    pos_marker = out.index("---block-boundary---")
    pos_b2 = out.index("print(x + 1)")
    assert pos_b1 < pos_marker < pos_b2


def test_extract_code_three_blocks_have_two_boundary_markers():
    """N blocks → N-1 separator markers."""
    reply = (
        "```python\nprint(1)\n```\n"
        "```python\nprint(2)\n```\n"
        "```python\nprint(3)\n```"
    )
    out = _extract_code(reply)
    assert out.count("---block-boundary---") == 2


def test_extract_code_python_language_tag_optional():
    """Regression: the existing regex accepts both ```python and bare ```
    fences."""
    reply = "```\nx = 1\n```\n```python\nprint(x)\n```"
    out = _extract_code(reply)
    assert "x = 1" in out
    assert "print(x)" in out


# ─── Integration: run_rlm with multi-block emissions ────────────────────

def test_run_rlm_executes_multiple_blocks_with_shared_globals(monkeypatch, capsys):
    """Block 1 sets x; block 2 reads x and prints. If shared globals work
    end-to-end, the feedback shows 42."""
    replies = iter([
        # Turn 0: two blocks, second depends on first's global
        '```python\nx = 42\n```\n```python\nprint(f"got x={x}")\n```',
        # Turn 1: finalize
        '```python\nFINAL = {"v": 1}\n```',
    ])
    monkeypatch.setattr(rlm, "_root_turn", lambda *a, **kw: next(replies))
    rlm.run_rlm(
        agent_name="test",
        environment={"token_symbol": "TEST"},
        task="t",
        output_schema={"v": "int"},
        max_iters=3,
        verbose=True,
    )
    out = capsys.readouterr().out
    assert "got x=42" in out


def test_run_rlm_feedback_shows_block_boundary_marker(monkeypatch, capsys):
    """When >1 block runs, the boundary marker must appear in the REPL
    feedback so the source of each output line is attributable."""
    replies = iter([
        '```python\nprint("from-block-1")\n```\n```python\nprint("from-block-2")\n```',
        '```python\nFINAL = {"v": 1}\n```',
    ])
    monkeypatch.setattr(rlm, "_root_turn", lambda *a, **kw: next(replies))
    rlm.run_rlm(
        agent_name="test",
        environment={"token_symbol": "TEST"},
        task="t",
        output_schema={"v": "int"},
        max_iters=3,
        verbose=True,
    )
    out = capsys.readouterr().out
    # The REPL section for iter 0 must contain both block outputs AND the marker
    repl_start = out.index("iter 0 repl")
    repl_section = out[repl_start:out.index("iter 1 root")]
    assert "from-block-1" in repl_section
    assert "---block-boundary---" in repl_section
    assert "from-block-2" in repl_section
    # Ordering: block 1 → marker → block 2
    assert repl_section.index("from-block-1") < repl_section.index("---block-boundary---") \
        < repl_section.index("from-block-2")


def test_run_rlm_single_block_no_boundary_marker_in_feedback(monkeypatch, capsys):
    """Regression: when opus emits exactly one block (the common case for
    the four converging agents), no marker pollutes the feedback."""
    replies = iter([
        '```python\nprint("only-block")\n```',
        '```python\nFINAL = {"v": 1}\n```',
    ])
    monkeypatch.setattr(rlm, "_root_turn", lambda *a, **kw: next(replies))
    rlm.run_rlm(
        agent_name="test",
        environment={"token_symbol": "TEST"},
        task="t",
        output_schema={"v": "int"},
        max_iters=3,
        verbose=True,
    )
    out = capsys.readouterr().out
    assert "only-block" in out
    repl_start = out.index("iter 0 repl")
    repl_section = out[repl_start:out.index("iter 1 root")]
    assert "---block-boundary---" not in repl_section


# ─── Unit: _ROOT_SYSTEM prompt reflects the new contract ────────────────

def test_root_system_prompt_declares_multi_block_contract():
    """The system prompt must explicitly tell opus that multiple blocks
    per turn are supported and execute in order with shared globals.
    Without this, the multi-block harvesting we just enabled is emergent
    behavior; with it, it's a designed feature."""
    sys_prompt = rlm._ROOT_SYSTEM
    # Must mention multi-block capability
    assert "one or more code blocks" in sys_prompt or "multiple blocks" in sys_prompt
    # Must mention persistent REPL / shared globals
    has_state_language = (
        "persistent REPL" in sys_prompt
        or "shared globals" in sys_prompt
        or "sharing globals" in sys_prompt
    )
    assert has_state_language


def test_root_system_prompt_no_longer_says_one_logical_step():
    """Regression-style: the old "one logical step per turn" language was
    instructing opus to do the opposite of what the engine now rewards.
    Make sure the misleading line is gone."""
    assert "one logical step per turn" not in rlm._ROOT_SYSTEM
