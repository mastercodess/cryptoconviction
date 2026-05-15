#!/usr/bin/env bash
# Replay the 39 (token,agent) analyzes that failed in the 2026-05-14 batch
# due to Anthropic API credit exhaustion, plus a security re-collect pass
# to populate security_collection_log (introduced in commit 71ceafa
# after the original collects ran). Plus 13 orchestrator passes.
#
# Estimated spend at sonnet-4-6 pricing:
#   13 security collects  ≈ $1.50  (NEW — fixes item 4 freshness)
#   39 analyzes           ≈ $5.85  (~$0.15 each on sonnet-4-6)
#   13 orchestrators      ≈ $4.55  (~$0.35 each)
#   Total                 ≈ $12–16
#
# Usage:
#   1. Top up Anthropic console credits
#   2. source .env  (DUNE_API_KEY, ANTHROPIC_API_KEY, etc.)
#   3. bash scripts/maa/rerun_failed_analyses_2026-05-14.sh
#
# Safe to re-run: each agent's analyze is idempotent and overwrites its own
# output. The security collect+analyze step force-rewrites the security
# files so data_as_of reflects the new collection_log timestamp.

set -u
cd "$(dirname "$0")/../.."

export PYTHONPATH=.

REPLAY_TOKENS=(ADA AVAX HYPE LINK NEAR ONDO RNDR STX SUI TON UNI XLM XRP)

run_collect() {
  local agent="$1" symbol="$2"
  echo "==> collect ${symbol}/${agent}"
  python3 -m "agents.${agent}.collect" "$symbol"
}

run_analyze() {
  local agent="$1" symbol="$2"
  local f="reports/${symbol}/agent_${agent}.json"
  if [ -f "$f" ]; then
    echo "  SKIP ${symbol}/${agent} (already exists)"
    return 0
  fi
  echo "==> analyze ${symbol}/${agent}"
  python3 -m "agents.${agent}.analyze" "$symbol"
}

run_analyze_force() {
  # Force re-analyze even when the success file exists — used for security
  # because the existing file's data_as_of comes from the pre-item-4
  # stamping (audit_date) and won't reflect the new collection_log row.
  local agent="$1" symbol="$2"
  echo "==> analyze ${symbol}/${agent} (force re-analyze)"
  rm -f "reports/${symbol}/agent_${agent}.json"
  python3 -m "agents.${agent}.analyze" "$symbol"
}

run_orchestrator() {
  local symbol="$1"
  echo "==> orchestrator ${symbol}"
  python3 -m agents.08_orchestrator.orchestrator "$symbol"
}

# --- Security freshness pass (item 4 fallout, fix before analyze) -------
# The May 14 security collects ran BEFORE security_collection_log existed.
# Every replay token needs a fresh security collect to populate that table
# (so data_as_of is collection-time, not audit_date) and a force-analyze to
# overwrite the cached LLM-set data_as_of on tokens that already have one.

echo "═══ Security re-collect pass (${#REPLAY_TOKENS[@]} tokens) ═══"
for s in "${REPLAY_TOKENS[@]}"; do
  run_collect 03_security "$s"
  run_analyze_force 03_security "$s"
done

# --- Missing analyzes (the original 39 total — security ones above will SKIP) ---

run_analyze 04_onchain XRP
run_analyze 05_team    XRP
run_analyze 06_moat    XRP

run_analyze 03_security ONDO
run_analyze 04_onchain  ONDO
run_analyze 06_moat     ONDO

run_analyze 04_onchain NEAR

run_analyze 04_onchain LINK
run_analyze 06_moat    LINK

run_analyze 03_security HYPE

run_analyze 01_tokenomics UNI
run_analyze 02_revenue    UNI
run_analyze 03_security   UNI
run_analyze 04_onchain    UNI
run_analyze 05_team       UNI
run_analyze 06_moat       UNI

run_analyze 03_security AVAX
run_analyze 04_onchain  AVAX
run_analyze 05_team     AVAX
run_analyze 06_moat     AVAX

run_analyze 04_onchain ADA
run_analyze 05_team    ADA
run_analyze 06_moat    ADA

run_analyze 01_tokenomics XLM
run_analyze 03_security   XLM
run_analyze 04_onchain    XLM
run_analyze 05_team       XLM
run_analyze 06_moat       XLM

run_analyze 03_security SUI
run_analyze 04_onchain  SUI
run_analyze 05_team     SUI
run_analyze 06_moat     SUI

run_analyze 03_security STX

run_analyze 02_revenue RNDR
run_analyze 04_onchain RNDR
run_analyze 06_moat    RNDR

run_analyze 04_onchain TON
run_analyze 05_team    TON
run_analyze 06_moat    TON

# --- Orchestrator passes (13 tokens) ---
# Run only after analyzes succeed so the orchestrator sees fresh data.

for s in ADA AVAX HYPE LINK NEAR ONDO RNDR STX SUI TON UNI XLM XRP; do
  run_orchestrator "$s"
done

echo ""
echo "Done. Inspect with:"
echo "  for t in ADA AVAX HYPE LINK NEAR ONDO RNDR STX SUI TON UNI XLM XRP; do"
echo "    echo \"== \$t ==\"; jq '{verdict, score: .weighted_conviction, stale: .stale_agents}' reports/\$t/conviction.json;"
echo "  done"
