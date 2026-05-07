-- Agent 7 — Macro & Cycle Positioning
CREATE TABLE IF NOT EXISTS macro_snapshot (
    snapshot_at         TEXT PRIMARY KEY,
    btc_price_usd       REAL,
    btc_dominance_pct   REAL,
    total_mc_usd        REAL,
    total_mc_ex_btc     REAL,
    altcoin_season_index INTEGER,
    fear_greed_index    INTEGER,
    fed_funds_rate      REAL,
    m2_yoy_pct          REAL,
    btc_halving_day     INTEGER,         -- days since most recent halving
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS token_cycle_metric (
    token_symbol  TEXT NOT NULL,
    snapshot_at   TEXT NOT NULL,
    funding_rate_8h REAL,
    open_interest_usd REAL,
    btc_correlation_30d REAL,
    eth_correlation_30d REAL,
    nasdaq_correlation_30d REAL,
    PRIMARY KEY (token_symbol, snapshot_at)
);

CREATE TABLE IF NOT EXISTS macro_research_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol TEXT,
    topic TEXT NOT NULL, body TEXT NOT NULL, sources TEXT, collected_at TEXT NOT NULL
);
