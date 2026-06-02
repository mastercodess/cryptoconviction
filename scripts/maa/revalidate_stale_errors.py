"""Revalidate stale .error.json artifacts left over from before the
rationale max_length bump (800 → 1500, commit 3388cb7, 2026-05-12).

Two outcomes per file:
  - ORPHAN: a sibling success.json already exists from a later run — the
    .error.json is stale noise. Action: delete .error.json.
  - RECOVERABLE: no success.json exists, but the cached `raw` block in
    .error.json validates under the current schema. Action: write the
    validated raw as the success.json, then delete .error.json.
  - STILL_INVALID: raw fails validation under the current schema too.
    Action: leave as-is, report for follow-up.

Zero LLM calls. Idempotent. Dry-run by default — pass --apply to mutate.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.schemas import (  # noqa: E402
    TokenomicsOutput,
    RevenueOutput,
    SecurityOutput,
    OnChainOutput,
    TeamOutput,
    MoatOutput,
    MacroOutput,
)

_SCHEMA_BY_AGENT = {
    "agent_01_tokenomics": TokenomicsOutput,
    "agent_02_revenue":    RevenueOutput,
    "agent_03_security":   SecurityOutput,
    "agent_04_onchain":    OnChainOutput,
    "agent_05_team":       TeamOutput,
    "agent_06_moat":       MoatOutput,
    "agent_07_macro":      MacroOutput,
}


def _classify(error_path: pathlib.Path) -> tuple[str, dict[str, Any]]:
    """Return (action, info) where action is ORPHAN/RECOVERABLE/STILL_INVALID/SKIP."""
    success_path = error_path.parent / error_path.name.replace(".error.json", ".json")
    info: dict[str, Any] = {
        "error_path": str(error_path),
        "success_path": str(success_path),
        "success_exists": success_path.exists(),
    }
    try:
        err_data = json.loads(error_path.read_text())
    except Exception as e:
        return "SKIP", {**info, "reason": f"unreadable: {e}"}

    raw = err_data.get("raw") or err_data.get("raw_output")
    if not raw:
        return "SKIP", {**info, "reason": "no raw block to revalidate"}

    info["raw_rationale_len"] = len(str(raw.get("rationale", "")))

    if success_path.exists():
        return "ORPHAN", info

    agent_key = error_path.name.replace(".error.json", "")
    Schema = _SCHEMA_BY_AGENT.get(agent_key)
    if Schema is None:
        return "SKIP", {**info, "reason": f"no schema for {agent_key}"}

    symbol = error_path.parent.name
    payload = {**raw, "token_symbol": symbol}
    try:
        validated = Schema(**payload)
        info["validated_payload"] = validated.model_dump()
        return "RECOVERABLE", info
    except Exception as e:
        return "STILL_INVALID", {**info, "validation_error": str(e)[:200]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="actually mutate files (default: dry-run)")
    p.add_argument("--reports-dir", default="reports")
    args = p.parse_args(argv)

    reports_dir = pathlib.Path(args.reports_dir)
    if not reports_dir.exists():
        print(f"reports dir not found: {reports_dir}", file=sys.stderr)
        return 1

    # Top-level only — skip _pre_* archives
    error_files = []
    for sym_dir in reports_dir.iterdir():
        if not sym_dir.is_dir() or sym_dir.name.startswith("_"):
            continue
        for f in sym_dir.glob("agent_*.error.json"):
            error_files.append(f)

    error_files.sort()
    if not error_files:
        print("no .error.json files found")
        return 0

    print(f"Found {len(error_files)} .error.json files\n")
    counts = {"ORPHAN": 0, "RECOVERABLE": 0, "STILL_INVALID": 0, "SKIP": 0}
    actions: list[tuple[str, dict[str, Any]]] = []
    for f in error_files:
        action, info = _classify(f)
        counts[action] += 1
        actions.append((action, info))
        rl = info.get("raw_rationale_len", "n/a")
        print(f"  [{action:13}] {f}  rationale_len={rl}")
        if action == "STILL_INVALID":
            print(f"      → {info.get('validation_error')}")

    print(f"\nSummary: ORPHAN={counts['ORPHAN']}  RECOVERABLE={counts['RECOVERABLE']}  "
          f"STILL_INVALID={counts['STILL_INVALID']}  SKIP={counts['SKIP']}")

    if not args.apply:
        print("\n[dry-run] no files mutated. Re-run with --apply to commit.")
        return 0

    # Apply phase
    print("\n=== applying ===")
    for action, info in actions:
        ep = pathlib.Path(info["error_path"])
        if action == "ORPHAN":
            ep.unlink()
            print(f"  deleted (orphan): {ep}")
        elif action == "RECOVERABLE":
            sp = pathlib.Path(info["success_path"])
            sp.write_text(json.dumps(info["validated_payload"], indent=2))
            ep.unlink()
            print(f"  recovered: wrote {sp} ({sp.stat().st_size} bytes) + deleted {ep.name}")
    print("\ndone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
