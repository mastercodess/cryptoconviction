# Agent 1 — Token Economics

**Focus:** Supply structure, emissions, value accrual, holder concentration.

## Data layout

```
agents/01_tokenomics/
├── schema.sql             # SQLite schema (loaded on first run)
├── collect.py             # Pulls from CoinGecko, Etherscan, Sonnet research
├── analyze.py             # RLM-driven analysis, emits TokenomicsOutput JSON
└── data/
    ├── tokenomics.db      # SQLite — supply_snapshot, unlock_event, mechanism, ...
    └── sidecars/{SYMBOL}/ # Raw JSON dumps the RLM can `read_text()` on demand
```

## Run

```bash
# 1. One-time: install deps + set ANTHROPIC_API_KEY in .env
pip install -r requirements.txt
cp .env.example .env && $EDITOR .env

# 2. Collect data for all 10 tokens (or a subset)
python -m agents.01_tokenomics.collect
python -m agents.01_tokenomics.collect LINK AAVE

# 3. Run the RLM analysis on one token
python -m agents.01_tokenomics.analyze LINK --verbose

# 4. Output lands in reports/LINK/agent_01_tokenomics.json
```

## RLM behavior

The Opus root model never sees the whitepaper, unlock CSV, or holder snapshot
in its context. They live in the SQLite DB and JSON sidecars. The root
writes Python — `tokenomics_db.execute(...)`, `Path(sidecar_dir, ...).read_text()` —
to peek, then delegates dense reads (e.g. "is this staking APR funded by
real fees or emission?") to `sub_lm()` running Sonnet.

This means a token with 5 audit PDFs + a 40-page whitepaper section + a
1000-row unlock CSV doesn't blow the root context — only the slices the
root deliberately fetches do.

## Output schema

See `shared/schemas.py` → `TokenomicsOutput`. Validated with Pydantic
before write. Failures save to `*.error.json` for debugging.

## What's intentionally NOT collected on free tier

- **Top 50 holder snapshots** — Etherscan paid feature ($199/mo). Agent
  treats holder concentration as `UNKNOWN` when not provided. To fill in:
  paste a CSV of holders into `data/sidecars/{SYMBOL}/holders.csv` and
  add an INSERT row to the `holder_snapshot` table.
- **Detailed VC cap table** — Agent 5's territory. Agent 1 only handles
  on-chain unlock effects.
