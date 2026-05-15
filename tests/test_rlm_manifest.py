"""Tests for shared.rlm._format_manifest.

The manifest is the single piece of information every agent's root LLM
sees on turn 1. Adding a data_quality_hint here is the highest-leverage
single change in the codebase (touches all 7 agents simultaneously), so
it MUST be defensively coded — a crash here breaks every agent, not
just one.

Coverage:
  - happy path: valid sidecar with top-level `data_quality` → hint inline
  - aggregation: worst quality across multiple sidecars wins
  - malformed JSON → silent, no hint, no crash
  - sidecar_dir absent from env → silent, no hint
  - sidecar_dir path doesn't exist → silent, no hint
  - sidecar without data_quality field → silent, no hint
  - regression: non-sidecar env keys still rendered as before
"""
from __future__ import annotations

from shared.rlm import _format_manifest


def test_manifest_includes_data_quality_hint_inline_with_sidecar_files(tmp_path):
    """Happy path. Hint must appear on the same line as `sidecar_files`
    (inline), so a model skimming sidecar-related lines catches it."""
    sidecar_dir = tmp_path / "TRX"
    sidecar_dir.mkdir()
    (sidecar_dir / "research.json").write_text(
        '{"data_quality": "PARTIAL", "dau": 4251001}'
    )
    env = {
        "token_symbol": "TRX",
        "sidecar_dir": str(sidecar_dir),
        "sidecar_files": ["research.json"],
    }
    manifest = _format_manifest(env)
    sidecar_files_line = [
        ln for ln in manifest.splitlines() if "sidecar_files" in ln
    ][0]
    assert "PARTIAL" in sidecar_files_line, (
        f"data_quality hint must be inline with sidecar_files line. Got:\n"
        f"  {sidecar_files_line}"
    )
    assert "data_quality_hint" in sidecar_files_line


def test_manifest_picks_worst_data_quality_across_multiple_sidecars(tmp_path):
    """Aggregation rule: UNAVAILABLE > PARTIAL > GOOD. Worst wins so the
    model doesn't get a falsely optimistic hint."""
    sidecar_dir = tmp_path / "X"
    sidecar_dir.mkdir()
    (sidecar_dir / "good.json").write_text('{"data_quality": "GOOD"}')
    (sidecar_dir / "partial.json").write_text('{"data_quality": "PARTIAL"}')
    (sidecar_dir / "unavailable.json").write_text('{"data_quality": "UNAVAILABLE"}')
    env = {
        "sidecar_dir": str(sidecar_dir),
        "sidecar_files": ["good.json", "partial.json", "unavailable.json"],
    }
    manifest = _format_manifest(env)
    assert "UNAVAILABLE" in manifest
    assert "PARTIAL" not in manifest  # only the worst surfaces
    assert "GOOD" not in manifest


def test_manifest_silent_on_malformed_json(tmp_path):
    """Defensive: a malformed sidecar JSON must NOT crash _format_manifest
    or surface a fabricated hint. This is the single defensive case that
    matters most — a crash breaks every agent."""
    sidecar_dir = tmp_path / "X"
    sidecar_dir.mkdir()
    (sidecar_dir / "broken.json").write_text("{ not valid json :::")
    env = {
        "sidecar_dir": str(sidecar_dir),
        "sidecar_files": ["broken.json"],
    }
    manifest = _format_manifest(env)
    assert "ENVIRONMENT MANIFEST" in manifest  # doesn't crash
    assert "data_quality_hint" not in manifest


def test_manifest_silent_when_no_sidecar_dir_in_env():
    """Most agents pass a sidecar_dir; some (07_macro at minimum) may not.
    No sidecar_dir → manifest still works, no hint."""
    env = {"token_symbol": "X", "macro_db": "fake_conn"}
    manifest = _format_manifest(env)
    assert "ENVIRONMENT MANIFEST" in manifest
    assert "data_quality_hint" not in manifest


def test_manifest_silent_when_sidecar_dir_path_missing():
    """sidecar_dir set to a path that doesn't exist on disk → silent."""
    env = {
        "sidecar_dir": "/tmp/definitely-does-not-exist-xyz-123",
        "sidecar_files": [],
    }
    manifest = _format_manifest(env)
    assert "ENVIRONMENT MANIFEST" in manifest
    assert "data_quality_hint" not in manifest


def test_manifest_silent_when_sidecar_has_no_data_quality_field(tmp_path):
    """Sidecar exists, JSON is valid, but no `data_quality` key → no hint."""
    sidecar_dir = tmp_path / "X"
    sidecar_dir.mkdir()
    (sidecar_dir / "research.json").write_text('{"some_field": "x", "another": 1}')
    env = {
        "sidecar_dir": str(sidecar_dir),
        "sidecar_files": ["research.json"],
    }
    manifest = _format_manifest(env)
    assert "data_quality_hint" not in manifest


def test_manifest_regression_non_sidecar_keys_unchanged():
    """Existing behavior must hold for envs without sidecar context."""
    env = {"token_symbol": "TRX", "some_string": "hello world"}
    manifest = _format_manifest(env)
    assert "token_symbol" in manifest
    assert "some_string" in manifest
    assert "str" in manifest         # type info still rendered
    assert "len=11" in manifest      # size info for "hello world"
