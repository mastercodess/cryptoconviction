"""Per-phase status persistence so the user can validate gates between
collect / analyze / orchestrate runs.

Layout: reports/<TOKEN>/_phase_status.json with shape:
{
  "collect": {
    "started_at": "ISO",
    "ended_at": "ISO",
    "per_agent": {"01_tokenomics": "ok", "07_macro": "rc=1"}
  },
  "analyze": {...},
  "orchestrate": {...}
}

Status string conventions:
  "ok"                            -> agent succeeded
  "rc=<n>"                        -> subprocess returned non-zero
  "skipped(<reason>)"             -> intentional skip
  "stale(<hours>h)"               -> data older than threshold (freshness gate)
  "fallback"                      -> analyze ran but emitted heuristic fallback
  "error(<class>)"                -> analyze raised an unexpected exception
"""
from __future__ import annotations

import json
import pathlib
from typing import Any


def write_phase(
    *,
    symbol: str,
    phase: str,
    reports_dir: pathlib.Path,
    per_agent: dict[str, str],
    started_at: str,
    ended_at: str,
) -> pathlib.Path:
    """Merge a phase block into reports/<SYMBOL>/_phase_status.json."""
    out_dir = reports_dir / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "_phase_status.json"
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = {}
    existing[phase] = {
        "started_at": started_at,
        "ended_at": ended_at,
        "per_agent": per_agent,
    }
    path.write_text(json.dumps(existing, indent=2))
    return path


def read_phase(symbol: str, *, reports_dir: pathlib.Path) -> dict[str, Any]:
    path = reports_dir / symbol / "_phase_status.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def summarize(
    symbol: str,
    *,
    phase: str,
    reports_dir: pathlib.Path,
) -> dict[str, int]:
    """Return {passed, failed, skipped} counts for a phase."""
    payload = read_phase(symbol, reports_dir=reports_dir)
    per_agent = payload.get(phase, {}).get("per_agent", {})
    passed = sum(1 for v in per_agent.values() if v == "ok")
    failed = sum(1 for v in per_agent.values()
                 if v != "ok" and not v.startswith("skipped"))
    skipped = sum(1 for v in per_agent.values() if v.startswith("skipped"))
    return {"passed": passed, "failed": failed, "skipped": skipped}
