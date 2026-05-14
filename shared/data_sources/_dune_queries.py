"""Community-built Dune Analytics query IDs used by the onchain agent.

Each constant maps a logical metric to a public Dune query. To replace a
query, fork or write a new one in the Dune UI, click Save, and copy the
integer ID from the URL (https://dune.com/queries/{ID}). Update the
constant below.

Required columns per query are documented in each comment.
"""
from __future__ import annotations

# Daily Active Addresses by chain (yesterday).
# SQL: per-chain UNION ALL of COUNT(DISTINCT "from") on <chain>.transactions
#      filtered by block_date = CURRENT_DATE - INTERVAL '1' DAY.
# Columns: chain (TEXT, e.g. "ethereum","tron"), daily_active_addresses (INT)
CHAIN_DAU: int = 7485961

# CEX Inflow/Outflow per chain (last 30d).
# SQL: tokens.transfers joined to cex_evms.addresses; sum amount_usd of
#      inflows (to CEX) vs outflows (from CEX) per blockchain.
# Columns: chain (TEXT), inflow_usd (FLOAT), outflow_usd (FLOAT),
#          net_usd (FLOAT), date (DATE — last day in window)
CEX_FLOWS_BY_CHAIN: int = 7496843

# BTC long-term-holder supply share (155-day threshold).
# Skipped this round: full BTC UTXO scan was prohibitively expensive on
# free-tier Dune. Code handles None by marking LTH/STH UNAVAILABLE for
# BTC-class tokens. Restore by writing a sampled query and putting the
# integer ID here.
BTC_LTH_STH: int | None = None
