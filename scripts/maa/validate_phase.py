"""CLI to validate a token's phase status between pipeline phases.

Usage:
  python -m scripts.maa.validate_phase TRX --phase collect
  python -m scripts.maa.validate_phase TRX --phase analyze
  python -m scripts.maa.validate_phase TRX --phase orchestrate

Exit code: 0 if all agents in that phase passed (or were intentionally
skipped); 1 if any failed. Prints the per-agent status table.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.phase_status import read_phase, summarize        # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbol")
    p.add_argument("--phase", required=True,
                   choices=["collect", "analyze", "orchestrate"])
    p.add_argument("--reports-dir", default="reports", type=pathlib.Path)
    args = p.parse_args(argv)
    sym = args.symbol.upper()
    payload = read_phase(sym, reports_dir=args.reports_dir)
    block = payload.get(args.phase)
    if not block:
        print(f"NO STATUS for {sym}/{args.phase}", file=sys.stderr)
        return 2
    s = summarize(sym, phase=args.phase, reports_dir=args.reports_dir)
    print(f"{sym} {args.phase} :: passed={s['passed']} failed={s['failed']} skipped={s['skipped']}")
    for agent, status in sorted(block.get("per_agent", {}).items()):
        flag = "✓" if status == "ok" else ("◌" if status.startswith("skipped") else "✗")
        print(f"  {flag} {agent}: {status}")
    return 0 if s["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
