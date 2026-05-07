-- ─── Agent 1 — Token Economics ────────────────────────────────────────
-- One DB per agent. All tables keyed by token_symbol (uppercase).
-- collect.py upserts; analyze.py reads.

PRAGMA foreign_keys = ON;
-- journal_mode left at default (DELETE). WAL needs shared memory which some
-- sandboxes don't support. The user can switch to WAL on their own machine
-- by running: sqlite3 tokenomics.db "PRAGMA journal_mode=WAL;"

-- Snapshot of supply / market metrics. One row per (token, snapshot_at).
-- Keep history; we want to plot inflation over time.
CREATE TABLE IF NOT EXISTS supply_snapshot (
    token_symbol      TEXT NOT NULL,
    snapshot_at       TEXT NOT NULL,    -- ISO8601 UTC
    market_cap_usd    REAL,
    fdv_usd           REAL,
    price_usd         REAL,
    circulating       REAL,             -- token units
    total_supply      REAL,
    max_supply        REAL,             -- NULL = uncapped
    PRIMARY KEY (token_symbol, snapshot_at)
);

-- Vesting / unlock events. Either parsed from Token Unlocks-style CSV or
-- from Sonnet research. category in:
--   'investor_unlock' | 'team_unlock' | 'foundation_unlock' |
--   'public_emission' | 'staking_emission' | 'airdrop' | 'other'
CREATE TABLE IF NOT EXISTS unlock_event (
    token_symbol    TEXT NOT NULL,
    unlock_date     TEXT NOT NULL,        -- ISO8601 date
    category        TEXT NOT NULL,
    tokens_unlocked REAL NOT NULL,
    pct_of_supply   REAL,                  -- vs current circulating
    source_url      TEXT,
    notes           TEXT,
    PRIMARY KEY (token_symbol, unlock_date, category)
);

-- Top holder concentration snapshot. Optional — left empty when free tier
-- can't supply it. analyze.py treats missing holder data as UNKNOWN, not zero.
CREATE TABLE IF NOT EXISTS holder_snapshot (
    token_symbol    TEXT NOT NULL,
    snapshot_at     TEXT NOT NULL,
    rank            INTEGER NOT NULL,
    address         TEXT,
    label           TEXT,                  -- 'binance_hot' / 'team_treasury' / etc
    balance         REAL NOT NULL,
    pct_of_supply   REAL NOT NULL,
    is_known_cex    INTEGER DEFAULT 0,
    PRIMARY KEY (token_symbol, snapshot_at, rank)
);

-- Burn / staking / fee mechanism notes — short structured fields the agent
-- consumes directly. Long-form descriptions live in JSON sidecars.
CREATE TABLE IF NOT EXISTS mechanism (
    token_symbol           TEXT PRIMARY KEY,
    has_burn               INTEGER,            -- 0/1
    burn_source            TEXT,                -- 'fees' | 'buyback' | 'NA'
    has_staking            INTEGER,
    staking_apr_pct        REAL,
    staking_emission_inflationary INTEGER,      -- 1 if APR funded by mint, 0 if from real fees
    fee_capture_target     TEXT,                -- 'protocol' | 'stakers' | 'lps' | 'split'
    value_accrual_summary  TEXT,                -- one-line plain English
    last_updated           TEXT
);

-- Inflation curve points. Annual inflation rate at each snapshot.
CREATE TABLE IF NOT EXISTS inflation_point (
    token_symbol       TEXT NOT NULL,
    as_of_date         TEXT NOT NULL,
    annualized_rate    REAL NOT NULL,    -- decimal, e.g. 0.04 = 4%
    method             TEXT,             -- 'observed' | 'projected'
    notes              TEXT,
    PRIMARY KEY (token_symbol, as_of_date, method)
);

-- Free-form research notes (sources, caveats, what we couldn't find).
-- The RLM's sub_lm() will read these directly when the topic comes up.
CREATE TABLE IF NOT EXISTS research_note (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol    TEXT NOT NULL,
    topic           TEXT NOT NULL,            -- 'supply_structure' | 'unlocks' | 'mechanism' | etc
    body            TEXT NOT NULL,
    sources         TEXT,                      -- JSON array of URLs
    collected_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_unlock_event_date ON unlock_event(unlock_date);
CREATE INDEX IF NOT EXISTS ix_supply_snapshot_token ON supply_snapshot(token_symbol);
CREATE INDEX IF NOT EXISTS ix_research_note_token ON research_note(token_symbol);
