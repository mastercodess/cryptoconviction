"""Sequential, fail-tolerant conviction batch runner with cost caps.

Per spec: top-20 -> for each token (in rank order):
  - skip agent 02 (revenue) when token.is_protocol == False
  - run 7 x collect, then 7 x analyze, then 08 orchestrator
  - per-token soft cap $2.50: abort that token's remaining agents on cross
  - batch hard cap $35: abort the rest of the batch
  - orchestrator narrative call: exempt from per-token, counts toward batch

Pre-flight:
  1. Truncate data/maa/run_log.jsonl
  2. Halt if data/maa/registry.committed.flag missing
  3. Read data/maa/dropped_symbols.json, skip those, print banner
  4. Preserve existing reports/{S}/* to reports/{S}/_pre_maa_2026-05-06/
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

DEFAULT_AGENTS = (
    "01_tokenomics",
    "02_revenue",
    "03_security",
    "04_onchain",
    "05_team",
    "06_moat",
    "07_macro",
)
ORCHESTRATOR = "08_orchestrator"
PRESERVE_SUFFIX = "_pre_maa_2026-05-06"
PER_TOKEN_CAP_USD = 2.50
BATCH_CAP_USD = 35.0
COLLECT_TIMEOUT = 600        # 10 min
ANALYZE_TIMEOUT = 900        # 15 min
ORCHESTRATOR_TIMEOUT = 300   # 5 min


class BatchAbortedError(RuntimeError):
    pass


def halt_if_no_flag(flag_path):
    if not flag_path.exists():
        raise BatchAbortedError(
            f"registry.committed.flag missing at {flag_path}. "
            f"Review data/maa/proposed_registry.json, edit if needed, "
            f"then run: python -m scripts.maa.commit_registry"
        )


def truncate_run_log(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


def read_cumulative_costs(path):
    """Return (per-agent total cost dict, batch total)."""
    per_agent = {}
    total = 0.0
    if not path.exists():
        return per_agent, 0.0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        cost = r.get("cost_usd")
        if cost is None:
            continue
        agent = r.get("agent_name", "unknown")
        per_agent[agent] = per_agent.get(agent, 0.0) + cost
        total += cost
    return per_agent, total


def preserve_existing_reports(symbols, *, reports_dir, suffix):
    for sym in symbols:
        d = reports_dir / sym
        if not d.exists():
            continue
        target = d / suffix
        if target.exists():
            continue  # already preserved by an earlier run
        target.mkdir()
        for entry in d.iterdir():
            if entry.name == suffix:
                continue
            shutil.move(str(entry), str(target / entry.name))


def should_skip_agent(agent, token_meta):
    if agent == "02_revenue" and not token_meta.get("is_protocol", True):
        return True
    return False


def _read_token_meta(symbol):
    """Read is_protocol from the registry. Defaults to True on miss."""
    from shared import tokens as token_registry
    try:
        token_registry.get(symbol)
    except KeyError:
        return {"is_protocol": True}  # unknown: fail-open, run all agents
    # is_protocol is on the proposed_registry but not yet on Token dataclass.
    # Read from data/maa/proposed_registry.json if present.
    proposed = pathlib.Path("data/maa/proposed_registry.json")
    if proposed.exists():
        for r in json.loads(proposed.read_text()):
            if r["symbol"] == symbol:
                return {"is_protocol": r.get("is_protocol", True)}
    return {"is_protocol": True}


def _run_subprocess(*, cmd, agent_name, action, run_log, timeout):
    env = {
        **os.environ,
        "MAA_RUN_LOG": str(run_log.resolve()),
        "MAA_AGENT_NAME": agent_name,
        "MAA_ACTION": action,
    }
    print(f"  -> {' '.join(cmd)}", file=sys.stderr)
    try:
        r = subprocess.run(cmd, env=env, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT after {timeout}s", file=sys.stderr)
        return 124
    return r.returncode


def run_one_token(*, symbol, agents, run_log, cumulative_batch_before,
                  batch_cap, per_token_cap):
    token_meta = _read_token_meta(symbol)
    summary = {"symbol": symbol, "phases": {}}

    def _per_token_cost():
        _, total = read_cumulative_costs(run_log)
        return total - cumulative_batch_before

    # Collect phase
    for agent in agents:
        if should_skip_agent(agent, token_meta):
            summary["phases"][f"collect:{agent}"] = "skipped(non-protocol)"
            continue
        # Use sys.executable to invoke the same interpreter — works on systems
        # where 'python' isn't aliased and only 'python3' is on PATH.
        rc = _run_subprocess(
            cmd=[sys.executable, "-m", f"agents.{agent}.collect", symbol],
            agent_name=agent, action="collect",
            run_log=run_log, timeout=COLLECT_TIMEOUT,
        )
        summary["phases"][f"collect:{agent}"] = "ok" if rc == 0 else f"rc={rc}"
        if read_cumulative_costs(run_log)[1] >= batch_cap:
            raise BatchAbortedError(f"batch cap ${batch_cap} hit during {symbol} collect")
        if _per_token_cost() >= per_token_cap:
            print(f"  WARNING: per-token cap hit for {symbol} during collect; jumping to orchestrator",
                  file=sys.stderr)
            return _orchestrate_and_return(symbol, summary, run_log)

    # Analyze phase
    for agent in agents:
        if should_skip_agent(agent, token_meta):
            summary["phases"][f"analyze:{agent}"] = "skipped(non-protocol)"
            continue
        rc = _run_subprocess(
            cmd=[sys.executable, "-m", f"agents.{agent}.analyze", symbol],
            agent_name=agent, action="analyze",
            run_log=run_log, timeout=ANALYZE_TIMEOUT,
        )
        summary["phases"][f"analyze:{agent}"] = "ok" if rc == 0 else f"rc={rc}"
        if read_cumulative_costs(run_log)[1] >= batch_cap:
            raise BatchAbortedError(f"batch cap ${batch_cap} hit during {symbol} analyze")
        if _per_token_cost() >= per_token_cap:
            print(f"  WARNING: per-token cap hit for {symbol} during analyze; jumping to orchestrator",
                  file=sys.stderr)
            return _orchestrate_and_return(symbol, summary, run_log)

    return _orchestrate_and_return(symbol, summary, run_log)


def _orchestrate_and_return(symbol, summary, run_log):
    rc = _run_subprocess(
        cmd=[sys.executable, "-m", f"agents.{ORCHESTRATOR}.orchestrator", symbol],
        agent_name=ORCHESTRATOR, action="orchestrator",
        run_log=run_log, timeout=ORCHESTRATOR_TIMEOUT,
    )
    summary["phases"]["orchestrator"] = "ok" if rc == 0 else f"rc={rc}"
    return summary


def run(*, top20_path, dropped_path, flag_path, run_log_path,
        reports_dir, agents=DEFAULT_AGENTS):
    halt_if_no_flag(flag_path)
    truncate_run_log(run_log_path)

    top20 = json.loads(top20_path.read_text())
    dropped = json.loads(dropped_path.read_text()) if dropped_path.exists() else []
    dropped_syms = {d["symbol"] for d in dropped}
    if dropped_syms:
        print(f"WARNING: {len(dropped_syms)} symbols dropped due to unresolved registry: "
              f"{', '.join(sorted(dropped_syms))}", file=sys.stderr)
        print(f"  (See {dropped_path}. The remaining {len(top20) - len(dropped_syms)} tokens will run.)",
              file=sys.stderr)

    effective = [e["symbol"] for e in top20 if e["symbol"] not in dropped_syms]
    preserve_existing_reports(effective, reports_dir=reports_dir,
                              suffix=PRESERVE_SUFFIX)

    summaries = []
    for sym in effective:
        _, batch_before = read_cumulative_costs(run_log_path)
        if batch_before >= BATCH_CAP_USD:
            print(f"WARNING: Batch cap ${BATCH_CAP_USD} reached. Halting before {sym}.",
                  file=sys.stderr)
            break
        print(f"\n=== {sym} (rank-ordered) ===", file=sys.stderr)
        try:
            s = run_one_token(
                symbol=sym, agents=agents, run_log=run_log_path,
                cumulative_batch_before=batch_before,
                batch_cap=BATCH_CAP_USD, per_token_cap=PER_TOKEN_CAP_USD,
            )
        except BatchAbortedError as e:
            print(f"BATCH ABORTED: {e}", file=sys.stderr)
            summaries.append({"symbol": sym, "aborted": str(e)})
            break
        summaries.append(s)

    return summaries


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--top20", default="reports/_maa_top20_2026-05-06.json",
                   type=pathlib.Path)
    p.add_argument("--dropped", default="data/maa/dropped_symbols.json",
                   type=pathlib.Path)
    p.add_argument("--flag", default="data/maa/registry.committed.flag",
                   type=pathlib.Path)
    p.add_argument("--run-log", default="data/maa/run_log.jsonl",
                   type=pathlib.Path)
    p.add_argument("--reports-dir", default="reports", type=pathlib.Path)
    args = p.parse_args(argv)

    if not args.top20.exists():
        print(f"Missing: {args.top20}", file=sys.stderr)
        return 2

    summaries = run(
        top20_path=args.top20, dropped_path=args.dropped,
        flag_path=args.flag, run_log_path=args.run_log,
        reports_dir=args.reports_dir,
    )
    print(f"\nBatch complete. {len(summaries)} tokens processed.")
    _, total = read_cumulative_costs(args.run_log)
    print(f"Total spend: ${total:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
