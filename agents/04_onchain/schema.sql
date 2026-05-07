-- Agent 4 — On-Chain Intelligence
CREATE TABLE IF NOT EXISTS activity_metric (
    token_symbol      TEXT NOT NULL,
    snapshot_at       TEXT NOT NULL,
    dau               INTEGER,
    wau               INTEGER,
    mau               INTEGER,
    dau_mau_ratio     REAL,
    daily_tx_count    INTEGER,
    new_addresses_7d  INTEGER,
    PRIMARY KEY (token_symbol, snapshot_at)
);

CREATE TABLE IF NOT EXISTS exchange_flow (
    token_symbol  TEXT NOT NULL,
    date          TEXT NOT NULL,
    inflow_usd    REAL,
    outflow_usd   REAL,
    net_usd       REAL,
    PRIMARY KEY (token_symbol, date)
);

CREATE TABLE IF NOT EXISTS holder_cohort (
    token_symbol      TEXT NOT NULL,
    snapshot_at       TEXT NOT NULL,
    lth_supply_pct    REAL,    -- long-term-holder supply %
    sth_supply_pct    REAL,    -- short-term-holder
    smart_money_stance TEXT,    -- 'ACCUMULATING'|'DISTRIBUTING'|'NEUTRAL'
    PRIMARY KEY (token_symbol, snapshot_at)
);

CREATE TABLE IF NOT EXISTS retention_cohort (
    token_symbol  TEXT NOT NULL,
    cohort_month  TEXT NOT NULL,    -- 'YYYY-MM' of first activity
    week          INTEGER NOT NULL, -- weeks since cohort_month
    retained_pct  REAL NOT NULL,
    PRIMARY KEY (token_symbol, cohort_month, week)
);

CREATE TABLE IF NOT EXISTS onchain_research_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol  TEXT NOT NULL,
    topic         TEXT NOT NULL,
    body          TEXT NOT NULL,
    sources       TEXT,
    collected_at  TEXT NOT NULL
);
