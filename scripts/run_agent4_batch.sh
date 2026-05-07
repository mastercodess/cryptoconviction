#!/usr/bin/env bash
# Agent 4 fan-out runner. Runs collect → ingest (once, batched) → analyze for
# every token passed on the command line. Defaults to "all tokens except LINK"
# since LINK is normally already done by run_agent4_link.sh.
#
# Usage:
#   bash scripts/run_agent4_batch.sh                    # default 9-token list
#   bash scripts/run_agent4_batch.sh AAVE SUI XMR       # custom subset
#   bash scripts/run_agent4_batch.sh --all              # include LINK too
#
# Requires .env with a working ANTHROPIC_API_KEY in repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEFAULT_SYMS=(AAVE ONDO ENA SUI AERO OGN NMR AVNT XMR)
ALL_SYMS=(LINK AAVE ONDO ENA SUI AERO OGN NMR AVNT XMR)

if [[ $# -eq 0 ]]; then
  SYMS=("${DEFAULT_SYMS[@]}")
elif [[ "${1:-}" == "--all" ]]; then
  SYMS=("${ALL_SYMS[@]}")
else
  SYMS=("$@")
fi

echo "==> repo:    $REPO_ROOT"
echo "==> symbols: ${SYMS[*]}"
echo "==> count:   ${#SYMS[@]}"
echo

# Load .env.
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found at $REPO_ROOT/.env" >&2
  exit 1
fi
set -a; . ./.env; set +a
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: ANTHROPIC_API_KEY not set" >&2
  exit 1
fi

# Verify deps once.
if ! python3 -c "import anthropic, pydantic, requests, dotenv, yaml, tabulate" 2>/dev/null; then
  echo "==> installing requirements"
  python3 -m pip install -r requirements.txt --break-system-packages
fi

START=$(date +%s)

# 1. Collect all in sequence (Sonnet research per token, ~$0.01 each).
echo
echo "============================================================"
echo "  PHASE 1 / 3 — collect ${#SYMS[@]} sidecars"
echo "============================================================"
for s in "${SYMS[@]}"; do
  echo
  echo "--- collect $s ---"
  if ! python3 -m agents.04_onchain.collect "$s"; then
    echo "WARN: collect failed for $s, continuing" >&2
  fi
done

# 2. One ingest pass (cheap; INSERT OR REPLACE on activity/flow/cohort,
#    idempotent on research_note via _upsert_note).
echo
echo "============================================================"
echo "  PHASE 2 / 3 — ingest"
echo "============================================================"
python3 -m agents.04_onchain.ingest_seed_data

# 3. Analyze each (RLM trajectory, Opus + Sonnet, ~$0.10–0.50 each).
echo
echo "============================================================"
echo "  PHASE 3 / 3 — analyze ${#SYMS[@]} tokens"
echo "============================================================"
SUCCESS=()
FAILED=()
for s in "${SYMS[@]}"; do
  echo
  echo "--- analyze $s ---"
  if python3 -m agents.04_onchain.analyze "$s"; then
    if [[ -f "reports/$s/agent_04_onchain.json" ]]; then
      SUCCESS+=("$s")
    else
      FAILED+=("$s")
    fi
  else
    FAILED+=("$s")
  fi
done

ELAPSED=$(( $(date +%s) - START ))

echo
echo "============================================================"
echo "  SUMMARY (${ELAPSED}s)"
echo "============================================================"
echo "  succeeded (${#SUCCESS[@]}): ${SUCCESS[*]:-<none>}"
echo "  failed    (${#FAILED[@]}): ${FAILED[*]:-<none>}"
echo
echo "  reports:"
for s in "${SUCCESS[@]}"; do
  printf "    %-6s composite=%s\n" "$s" \
    "$(python3 -c "
import json
try:
    d = json.load(open('reports/$s/agent_04_onchain.json'))
    print(d.get('composite_score'))
except Exception:
    print('?')
")"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  exit 2
fi
