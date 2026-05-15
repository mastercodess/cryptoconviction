-- Agent 3 — Security & Code Integrity
CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol    TEXT NOT NULL,
    auditor         TEXT NOT NULL,    -- 'Trail of Bits' | 'OpenZeppelin' | etc.
    audit_date      TEXT,
    scope           TEXT,
    severity_high   INTEGER DEFAULT 0,
    severity_med    INTEGER DEFAULT 0,
    severity_low    INTEGER DEFAULT 0,
    pdf_path        TEXT,             -- relative path to sidecar PDF
    summary         TEXT
);

CREATE TABLE IF NOT EXISTS exploit_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_symbol    TEXT NOT NULL,
    incident_date   TEXT NOT NULL,
    severity        TEXT NOT NULL,     -- 'minor' | 'moderate' | 'major' | 'catastrophic'
    description     TEXT NOT NULL,
    funds_lost_usd  REAL,
    post_mortem_url TEXT
);

CREATE TABLE IF NOT EXISTS code_health (
    token_symbol         TEXT PRIMARY KEY,
    primary_repo_url     TEXT,
    contributors_count   INTEGER,
    bus_factor           INTEGER,
    weekly_commits_avg   REAL,
    last_commit_date     TEXT,
    upgrade_mechanism    TEXT,         -- 'immutable' | 'proxy_timelock' | 'proxy_no_timelock' | 'multisig_only'
    multisig_signers     INTEGER,
    multisig_threshold   INTEGER,
    bug_bounty_max_usd   REAL,
    bug_bounty_platform  TEXT
);

CREATE TABLE IF NOT EXISTS dependency (
    token_symbol  TEXT NOT NULL,
    dep_type      TEXT NOT NULL,     -- 'oracle' | 'bridge' | 'l1' | 'l2_sequencer'
    provider      TEXT NOT NULL,     -- 'Chainlink' | 'LayerZero' | etc
    risk_level    TEXT NOT NULL,     -- 'low' | 'medium' | 'high'
    notes         TEXT,
    PRIMARY KEY (token_symbol, dep_type, provider)
);

-- Tracks WHEN we last collected security data for each token, distinct
-- from WHEN the underlying events happened (audit_date, incident_date).
-- The orchestrator's freshness gate reads this — collection time, not
-- event time, is the right semantic for "how stale is our view?".
CREATE TABLE IF NOT EXISTS security_collection_log (
    token_symbol  TEXT PRIMARY KEY,
    collected_at  TEXT NOT NULL          -- ISO 8601 datetime, UTC
);
