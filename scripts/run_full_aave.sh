#!/usr/bin/env bash
# Full conviction pipeline for AAVE: agents 1→7 + orchestrator.
#
# Cost-aware: skips collect for agents that already have populated DB rows
# for AAVE, skips agent 2 entirely (report already exists). Estimated total
# ~$0.50-1.50 in API spend.
#
# Fail-soft: if one agent's analyzer crashes (max_iters, schema, etc.), the
# script logs and continues to the next agent. Orchestrator runs over
# whatever reports got produced.
set -uo pipefail   # NOT -e — we want to keep going past failures

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SYMBOL="AAVE"
echo "==> repo: $REPO_ROOT  symbol: $SYMBOL"
echo

# Load .env.
if [[ ! -f .env ]]; then echo "ERROR: .env missing" >&2; exit 1; fi
set -a; . ./.env; set +a
[[ -z "${ANTHROPIC_API_KEY:-}" ]] && { echo "ERROR: no key" >&2; exit 1; }
echo "==> .env loaded (key len ${#ANTHROPIC_API_KEY})"
echo

# Verify deps.
if ! python3 -c "import anthropic, pydantic, requests, dotenv, yaml, tabulate" 2>/dev/null; then
  echo "==> installing requirements"
  python3 -m pip install -r requirements.txt --break-system-packages
fi

START=$(date +%s)

# Per-agent runner. Args: agent_pkg need_collect need_ingest report_file
run_agent() {
  local pkg=$1 need_collect=$2 need_ingest=$3 report_name=$4
  local label=${pkg#agents.}

  echo
  echo "============================================================"
  echo "  $label"
  echo "============================================================"

  # Skip if report already exists.
  if [[ -f "reports/$SYMBOL/$report_name" ]]; then
    echo "  SKIP  reports/$SYMBOL/$report_name already exists"
    return 0
  fi

  if [[ "$need_collect" == "yes" ]]; then
    echo "  collect..."
    if ! python3 -m "${pkg}.collect" "$SYMBOL" 2>&1 | tail -20; then
      echo "  WARN  collect failed for $label, continuing"
    fi
  else
    echo "  collect skipped (DB already populated for $SYMBOL)"
  fi

  if [[ "$need_ingest" == "yes" ]]; then
    echo "  ingest..."
    python3 -m "${pkg}.ingest_seed_data" 2>&1 | tail -10 || \
      echo "  WARN  ingest failed for $label, continuing"
  fi

  echo "  analyze..."
  if python3 -m "${pkg}.analyze" "$SYMBOL" 2>&1 | tail -30; then
    if [[ -f "reports/$SYMBOL/$report_name" ]]; then
      echo "  ✓  $report_name written"
    else
      echo "  ✗  analyze ran but no report at reports/$SYMBOL/$report_name"
    fi
  else
    echo "  ✗  analyze failed for $label"
  fi
}

# Agent 1 tokenomics — DB has 1 supply_snapshot for AAVE, just need analyze.
run_agent "agents.01_tokenomics" "no"  "no" "agent_01_tokenomics.json"

# Agent 2 revenue — already done.
echo
echo "============================================================"
echo "  02_revenue (already done)"
echo "============================================================"
[[ -f "reports/$SYMBOL/agent_02_revenue.json" ]] && echo "  SKIP" || echo "  WARN: report missing — run scripts/run_agent2_link.sh AAVE"

# Agent 3 security — DB has 8 audits + 1 exploit + 5 deps for AAVE.
run_agent "agents.03_security" "no"  "no" "agent_03_security.json"

# Agent 4 onchain — DB has activity + cohort + note for AAVE.
run_agent "agents.04_onchain"   "no"  "no" "agent_04_onchain.json"

# Agent 5 team — empty, need full collect.
run_agent "agents.05_team"      "yes" "no" "agent_05_team.json"

# Agent 6 moat — empty, need full collect.
run_agent "agents.06_moat"      "yes" "no" "agent_06_moat.json"

# Agent 7 macro — sidecar exists but DB doesn't; collect.py rebuilds DB from
# Sonnet response so re-running collect is the right path.
run_agent "agents.07_macro"     "yes" "no" "agent_07_macro.json"

# Agent 8 orchestrator — runs on whatever reports got produced.
echo
echo "============================================================"
echo "  08_orchestrator"
echo "============================================================"
python3 -m agents.08_orchestrator.orchestrator "$SYMBOL" 2>&1 | tail -40

ELAPSED=$(( $(date +%s) - START ))

echo
echo "============================================================"
echo "  SUMMARY (${ELAPSED}s)"
echo "============================================================"
echo "  reports/$SYMBOL/:"
ls -la "reports/$SYMBOL/" 2>/dev/null | awk 'NR>1 {printf "    %s\n", $NF}'

if [[ -f "reports/$SYMBOL/conviction.md" ]]; then
  echo
  echo "============================================================"
  echo "  reports/$SYMBOL/conviction.md"
  echo "============================================================"
  cat "reports/$SYMBOL/conviction.md"
fi
