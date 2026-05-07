-- Agent 5 — Team & Investor Diligence
CREATE TABLE IF NOT EXISTS team_member (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol  TEXT NOT NULL,
    name          TEXT NOT NULL,
    role          TEXT,
    doxxed        INTEGER,
    linkedin_url  TEXT,
    prior_projects TEXT,        -- JSON array
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS investor (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol  TEXT NOT NULL,
    investor_name TEXT NOT NULL,
    round         TEXT,           -- 'seed' | 'series_a' | 'private' | 'public'
    valuation_usd REAL,
    ownership_pct REAL,
    unlock_status TEXT             -- 'fully_vested' | 'cliff_remaining' | 'linear'
);

CREATE TABLE IF NOT EXISTS legal_event (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol  TEXT NOT NULL,
    event_date    TEXT,
    jurisdiction  TEXT,
    description   TEXT,
    severity      TEXT,            -- 'minor' | 'moderate' | 'severe'
    source_url    TEXT
);

CREATE TABLE IF NOT EXISTS team_research_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol  TEXT NOT NULL,
    topic         TEXT NOT NULL,
    body          TEXT NOT NULL,
    sources       TEXT,
    collected_at  TEXT NOT NULL
);
