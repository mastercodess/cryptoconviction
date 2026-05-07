#!/usr/bin/env bash
# One-shot Agent 4 (on-chain) pipeline for LINK.
#
#   collect → ingest → analyze → show report
#
# Run from repo root or anywhere; the script cd's to the repo automatically.
# Requires:  .env with a working ANTHROPIC_API_KEY in repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SYMBOL="${1:-LINK}"

echo "==> repo:   $REPO_ROOT"
echo "==> symbol: $SYMBOL"
echo

# 1. Load .env into the environment.
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found at $REPO_ROOT/.env" >&2
  exit 1
fi
set -a; . ./.env; set +a
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: ANTHROPIC_API_KEY not set in .env" >&2
  exit 1
fi
echo "==> .env loaded (key len ${#ANTHROPIC_API_KEY})"

# 2. Verify Python deps; install if anything's missing.
if ! python3 -c "import anthropic, pydantic, requests, dotenv, yaml, tabulate" 2>/dev/null; then
  echo "==> installing requirements"
  python3 -m pip install -r requirements.txt --break-system-packages
fi

# 3. Refresh the sidecar (Sonnet research call).
echo
echo "==> [1/3] collect $SYMBOL"
python3 -m agents.04_onchain.collect "$SYMBOL"

# 4. Re-ingest sidecars into onchain.db (whole batch is cheap; INSERT OR REPLACE).
echo
echo "==> [2/3] ingest"
python3 -m agents.04_onchain.ingest_seed_data

# 5. Analyze with the RLM (Opus root + Sonnet sub-LM).
echo
echo "==> [3/3] analyze $SYMBOL"
python3 -m agents.04_onchain.analyze "$SYMBOL"

# 6. Surface the report.
REPORT="reports/$SYMBOL/agent_04_onchain.json"
echo
if [[ -f "$REPORT" ]]; then
  echo "==> report at $REPORT"
  echo "----------------------------------------"
  python3 -m json.tool "$REPORT"
  echo "----------------------------------------"
  echo "DONE."
else
  ERR="reports/$SYMBOL/agent_04_onchain.error.json"
  if [[ -f "$ERR" ]]; then
    echo "==> analyze produced an error file: $ERR"
    cat "$ERR"
    exit 2
  fi
  echo "ERROR: no report at $REPORT and no error sidecar." >&2
  exit 3
fi
