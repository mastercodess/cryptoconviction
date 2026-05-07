-- Agent 2 — Protocol Revenue & Fundamentals
-- Tracks fees, revenue, TVL, and valuation multiples over time.

CREATE TABLE IF NOT EXISTS revenue_snapshot (
    token_symbol           TEXT NOT NULL,
    snapshot_at            TEXT NOT NULL,
    daily_fees_usd         REAL,
    daily_revenue_usd      REAL,           -- protocol-captured (fees minus LP/payouts)
    annualized_revenue_usd REAL,
    tvl_usd                REAL,
    p_s_ratio              REAL,            -- MC / annualized_revenue
    p_tvl_ratio            REAL,            -- MC / TVL
    real_yield_apr         REAL,            -- distributed to token holders, %
    inflationary_yield_apr REAL,            -- emission-funded, %
    seasonality_note       TEXT,
    PRIMARY KEY (token_symbol, snapshot_at)
);

CREATE TABLE IF NOT EXISTS revenue_history (
    token_symbol  TEXT NOT NULL,
    date          TEXT NOT NULL,
    fees_usd      REAL,
    revenue_usd   REAL,
    tvl_usd       REAL,
    PRIMARY KEY (token_symbol, date)
);

CREATE TABLE IF NOT EXISTS peer_comparison (
    token_symbol  TEXT NOT NULL,
    peer_symbol   TEXT NOT NULL,
    metric        TEXT NOT NULL,            -- 'p_s' | 'p_tvl' | 'rev_30d' | etc.
    self_value    REAL,
    peer_value    REAL,
    captured_at   TEXT NOT NULL,
    PRIMARY KEY (token_symbol, peer_symbol, metric, captured_at)
);

CREATE TABLE IF NOT EXISTS revenue_research_note (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol  TEXT NOT NULL,
    topic         TEXT NOT NULL,
    body          TEXT NOT NULL,
    sources       TEXT,
    collected_at  TEXT NOT NULL
);
