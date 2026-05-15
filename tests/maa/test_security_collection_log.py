"""Tests for the security_collection_log table (item 4 — the deferred TODO
from dc1de3d turning option (b) into option (c)).

Context: the gate was firing on TRX's audit_date=2022-08-01 even though
we just freshly re-collected security data. The conceptual error: the
gate was testing "when was the audit?" (event time) when it wanted to
test "when did we last refresh?" (collection time). Until now, only
event timestamps existed in the schema.

This test set pins the new contract:
  - schema has a `security_collection_log` table keyed on token_symbol
    with a `collected_at` column
  - collect_one INSERT OR REPLACEs that row on every successful collect
  - analyze.stamp_data_as_of reads from this table (not audit.audit_date)
  - if the log has no row for a token (first-time analyze on stale DB),
    data_as_of stays None → freshness gate fails closed as before
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sqlite3
import sys

# fmt: off
_security = importlib.import_module("agents.03_security.analyze")
_security_collect = importlib.import_module("agents.03_security.collect")
analyze = _security.analyze
# fmt: on


def _setup_security_db(db_path: pathlib.Path, schema_path: pathlib.Path) -> sqlite3.Connection:
    """Apply the schema to a fresh DB and return a connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(schema_path.read_text())
    conn.commit()
    return conn


# ─── Schema: the new table exists with the right shape ─────────────────

def test_schema_creates_security_collection_log_table(tmp_path):
    """Schema must declare security_collection_log with token_symbol as PK
    and collected_at as a NOT NULL TEXT column."""
    schema_path = pathlib.Path(_security_collect.__file__).parent / "schema.sql"
    db_path = tmp_path / "test_security.db"
    conn = _setup_security_db(db_path, schema_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "security_collection_log" in tables

    # Columns and constraints
    cols = conn.execute("PRAGMA table_info(security_collection_log)").fetchall()
    col_map = {r["name"]: dict(r) for r in cols}
    assert "token_symbol" in col_map
    assert "collected_at" in col_map
    assert col_map["token_symbol"]["pk"] == 1, "token_symbol must be the PK"
    assert col_map["collected_at"]["notnull"] == 1, "collected_at must be NOT NULL"


# ─── Analyze side: stamp_data_as_of reads from the new table ───────────

def test_analyze_stamps_from_security_collection_log(tmp_path, monkeypatch):
    """When security_collection_log has a row for the symbol, analyze must
    set data_as_of from collected_at — NOT from audit.audit_date."""
    schema_path = pathlib.Path(_security_collect.__file__).parent / "schema.sql"
    db_path = tmp_path / "security.db"
    conn = _setup_security_db(db_path, schema_path)
    # An old audit (2022) AND a fresh collection (2026)
    conn.execute(
        "INSERT INTO audit (token_symbol, auditor, audit_date) VALUES (?, ?, ?)",
        ("TRX", "TestAuditor", "2022-08-01"),
    )
    conn.execute(
        "INSERT INTO security_collection_log (token_symbol, collected_at) VALUES (?, ?)",
        ("TRX", "2026-05-15T10:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(_security, "DB_PATH", db_path)
    monkeypatch.setattr(_security, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(_security, "SIDECAR_DIR", tmp_path / "sidecars")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())
    # Force RLM fallback (no LLM call) so the test is deterministic and offline.
    monkeypatch.setattr(_security, "run_rlm",
                        lambda **kw: {"error": "max_iters_reached", "iters": 14})

    result = analyze("TRX", max_iters=1)
    assert result["ok"], result
    out = json.loads((tmp_path / "reports" / "TRX" / "agent_03_security.json").read_text())
    assert out["data_as_of"] == "2026-05-15T10:00:00+00:00", (
        f"data_as_of must come from collection_log.collected_at, not audit_date. "
        f"Got: {out['data_as_of']}"
    )


def test_analyze_overrides_llm_set_data_as_of_with_collection_log(tmp_path, monkeypatch):
    """Happy-path failure mode discovered in TRX T4 run: the LLM sets
    data_as_of itself from the latest audit_date it observes ("2023-03-01"
    in TRX's case). That's the event-time concept the gate is NOT
    supposed to test. Security analyze must OVERWRITE the LLM's value
    with the collection_log timestamp."""
    schema_path = pathlib.Path(_security_collect.__file__).parent / "schema.sql"
    db_path = tmp_path / "security.db"
    conn = _setup_security_db(db_path, schema_path)
    conn.execute(
        "INSERT INTO audit (token_symbol, auditor, audit_date) VALUES (?, ?, ?)",
        ("TRX", "TestAuditor", "2022-08-01"),
    )
    conn.execute(
        "INSERT INTO security_collection_log (token_symbol, collected_at) VALUES (?, ?)",
        ("TRX", "2026-05-15T10:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(_security, "DB_PATH", db_path)
    monkeypatch.setattr(_security, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(_security, "SIDECAR_DIR", tmp_path / "sidecars")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())
    # LLM happy path: returns a valid FINAL with its own (wrong) data_as_of from audit_date.
    monkeypatch.setattr(_security, "run_rlm", lambda **kw: {
        "token_symbol": "TRX",
        "security_tier": 3,
        "audit_coverage_score": 7,
        "single_points_of_failure": [],
        "centralization_risks": [],
        "incident_history_severity": "MODERATE",
        "upgrade_mechanism": "multisig_only",
        "data_as_of": "2022-08-01",   # LLM picked audit_date (event time, wrong concept)
        "rationale": "x" * 50,
        "composite_score": 50,
    })

    result = analyze("TRX", max_iters=1)
    assert result["ok"], result
    out = json.loads((tmp_path / "reports" / "TRX" / "agent_03_security.json").read_text())
    assert out["data_as_of"] == "2026-05-15T10:00:00+00:00", (
        f"data_as_of must be overwritten with collection_log time, not the "
        f"LLM-set audit_date. Got: {out['data_as_of']}"
    )


def test_analyze_data_as_of_is_none_when_collection_log_empty(tmp_path, monkeypatch):
    """Graceful degradation: if security_collection_log has no row for the
    symbol (first-time analyze before any collect run, or DB migration),
    data_as_of is None → freshness gate fails closed as before."""
    schema_path = pathlib.Path(_security_collect.__file__).parent / "schema.sql"
    db_path = tmp_path / "security.db"
    conn = _setup_security_db(db_path, schema_path)
    # Audit exists (so analyze has SOMETHING to work with) but NO collection_log row.
    conn.execute(
        "INSERT INTO audit (token_symbol, auditor, audit_date) VALUES (?, ?, ?)",
        ("LINK", "TestAuditor", "2024-03-01"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(_security, "DB_PATH", db_path)
    monkeypatch.setattr(_security, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(_security, "SIDECAR_DIR", tmp_path / "sidecars")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())
    monkeypatch.setattr(_security, "run_rlm",
                        lambda **kw: {"error": "max_iters_reached", "iters": 14})

    result = analyze("LINK", max_iters=1)
    assert result["ok"], result
    out = json.loads((tmp_path / "reports" / "LINK" / "agent_03_security.json").read_text())
    assert out["data_as_of"] is None, (
        f"data_as_of must be None when collection_log has no row for symbol. "
        f"Got: {out['data_as_of']}"
    )


# ─── Collect side: a successful collect_one writes to the new table ────

def test_collect_one_writes_security_collection_log(tmp_path, monkeypatch):
    """After a successful collect_one, security_collection_log must have a
    row for the symbol with a non-null collected_at."""
    schema_path = pathlib.Path(_security_collect.__file__).parent / "schema.sql"
    db_path = tmp_path / "security.db"

    # Patch the collect module's paths and the LLM call.
    monkeypatch.setattr(_security_collect, "DB_PATH", db_path)
    monkeypatch.setattr(_security_collect, "SIDECAR_DIR", tmp_path / "sidecars")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())
    # Return a minimal valid research payload (enough to take the "data is truthy" branch).
    monkeypatch.setattr(
        _security_collect, "research_json",
        lambda *a, **kw: {
            "audits": [],
            "exploit_history": [],
            "code_health": {},
            "dependencies": [],
            "data_quality": "PARTIAL",
        },
    )

    result = _security_collect.collect_one("LINK")
    assert result.get("ok") is True

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT token_symbol, collected_at FROM security_collection_log WHERE token_symbol=?",
        ("LINK",),
    ).fetchone()
    assert row is not None, "collect_one must INSERT a row into security_collection_log"
    assert row["collected_at"], "collected_at must be populated, not null/empty"


def test_collect_one_replaces_existing_collection_log_row(tmp_path, monkeypatch):
    """On a re-collect for the same symbol, collected_at must update (not
    create a duplicate / second row, since token_symbol is the PK)."""
    schema_path = pathlib.Path(_security_collect.__file__).parent / "schema.sql"
    db_path = tmp_path / "security.db"
    monkeypatch.setattr(_security_collect, "DB_PATH", db_path)
    monkeypatch.setattr(_security_collect, "SIDECAR_DIR", tmp_path / "sidecars")
    from shared import tokens
    monkeypatch.setattr(tokens, "get", lambda s: type("T", (), {"name": s, "symbol": s})())
    monkeypatch.setattr(
        _security_collect, "research_json",
        lambda *a, **kw: {
            "audits": [], "exploit_history": [], "code_health": {},
            "dependencies": [], "data_quality": "PARTIAL",
        },
    )

    _security_collect.collect_one("LINK")
    _security_collect.collect_one("LINK")

    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM security_collection_log WHERE token_symbol=?",
        ("LINK",),
    ).fetchone()[0]
    assert count == 1, f"Two collects should yield ONE row (PK on symbol). Got: {count}"
