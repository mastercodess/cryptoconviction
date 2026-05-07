#!/usr/bin/env bash
# Agent 2 (revenue) smoke-test pipeline for one token (default LINK).
#
#   collect → ingest → DB sanity check → analyze → show report
#
# Mirrors run_agent4_link.sh but for the patched agent 2 modules. Adds an
# explicit DB-cleanliness check between ingest and analyze so any string
# sentinels still lurking in REAL columns surface before we burn API on
# the analyzer.
#
# Requires .env with ANTHROPIC_API_KEY in repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SYMBOL="${1:-LINK}"

echo "==> repo:   $REPO_ROOT"
echo "==> symbol: $SYMBOL"
echo

# 1. Load .env.
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

# 2. Verify deps.
if ! python3 -c "import anthropic, pydantic, requests, dotenv, yaml, tabulate" 2>/dev/null; then
  echo "==> installing requirements"
  python3 -m pip install -r requirements.txt --break-system-packages
fi

# 3. Refresh sidecar (one Sonnet research call, ~$0.01-0.02).
echo
echo "==> [1/4] collect $SYMBOL"
python3 -m agents.02_revenue.collect "$SYMBOL"

# 4. Re-ingest sidecars (idempotent).
echo
echo "==> [2/4] ingest"
python3 -m agents.02_revenue.ingest_seed_data

# 5. Sanity-check the DB BEFORE we burn API on analyze.
echo
echo "==> [3/4] DB sanity check"
python3 - <<PY
import sqlite3, sys
DB = "agents/02_revenue/data/revenue.db"
c = sqlite3.connect(DB)
issues = 0

# REAL columns that should never hold strings
real_cols = {
    "revenue_snapshot": ["annualized_revenue_usd","tvl_usd","p_s_ratio",
                         "p_tvl_ratio","real_yield_apr","inflationary_yield_apr"],
    "peer_comparison":  ["self_value","peer_value"],
}
for tbl, cols in real_cols.items():
    for col in cols:
        n = c.execute(
            f"SELECT COUNT(*) FROM {tbl} "
            f"WHERE {col} IS NOT NULL "
            f"AND typeof({col}) NOT IN ('integer','real')"
        ).fetchone()[0]
        if n > 0:
            print(f"  FAIL: {tbl}.{col} has {n} non-numeric rows")
            issues += 1

# research_note duplicates for $SYMBOL
dup = c.execute("""
    SELECT topic, COUNT(*) FROM revenue_research_note
    WHERE token_symbol = '$SYMBOL'
    GROUP BY topic, body HAVING COUNT(*) > 1
""").fetchall()
if dup:
    print(f"  FAIL: duplicate research_note rows for $SYMBOL: {dup}")
    issues += 1

# Print row counts for context
print("  current state:")
for tbl in ["revenue_snapshot","peer_comparison","revenue_research_note"]:
    n_total = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    n_link  = c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE token_symbol='$SYMBOL'").fetchone()[0]
    print(f"    {tbl:>22}: {n_total} total ({n_link} for $SYMBOL)")

if issues:
    sys.exit("aborting analyze — DB has issues")
print("  DB clean — proceeding to analyze")
PY

# 6. Analyze (RLM trajectory, ~$0.10-0.40).
echo
echo "==> [4/4] analyze $SYMBOL"
python3 -m agents.02_revenue.analyze "$SYMBOL"

# 7. Surface the report.
REPORT="reports/$SYMBOL/agent_02_revenue.json"
echo
if [[ -f "$REPORT" ]]; then
  echo "==> report at $REPORT"
  echo "----------------------------------------"
  python3 -m json.tool "$REPORT"
  echo "----------------------------------------"
  echo "DONE."
else
  ERR="reports/$SYMBOL/agent_02_revenue.error.json"
  if [[ -f "$ERR" ]]; then
    echo "==> analyze produced an error file: $ERR"
    cat "$ERR"
    exit 2
  fi
  echo "ERROR: no report at $REPORT and no error sidecar." >&2
  exit 3
fi
