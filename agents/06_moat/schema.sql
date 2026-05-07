-- Agent 6 — Competitive Moat
CREATE TABLE IF NOT EXISTS competitor (
    token_symbol  TEXT NOT NULL,
    competitor    TEXT NOT NULL,
    market_cap_usd REAL,
    tvl_usd       REAL,
    dau           INTEGER,
    revenue_30d_usd REAL,
    PRIMARY KEY (token_symbol, competitor)
);

CREATE TABLE IF NOT EXISTS market_share (
    token_symbol  TEXT NOT NULL,
    snapshot_at   TEXT NOT NULL,
    category      TEXT NOT NULL,
    share_pct     REAL NOT NULL,
    PRIMARY KEY (token_symbol, snapshot_at, category)
);

CREATE TABLE IF NOT EXISTS dev_ecosystem (
    token_symbol     TEXT PRIMARY KEY,
    monthly_active_devs INTEGER,
    full_time_devs   INTEGER,
    repos_building_on INTEGER,
    integrations_count INTEGER,
    snapshot_at      TEXT
);

CREATE TABLE IF NOT EXISTS moat_research_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol TEXT NOT NULL,
    topic TEXT NOT NULL, body TEXT NOT NULL, sources TEXT, collected_at TEXT NOT NULL
);
