"""Pin the XLM/Stellar onchain integration from plan 2026-05-17 (plan 2a).

The handler fetches DAU from Stellar Foundation's canonical Hubble
BigQuery dataset (crypto-stellar.crypto_stellar_dbt.enriched_history_operations)
using COUNT(DISTINCT op_source_account) per OrbitLens methodology. These
tests pin the integration and the routing without making real GCP calls —
BigQuery is mocked at the helper-function boundary.
"""
from __future__ import annotations

import importlib
import sqlite3
from unittest.mock import patch

import pytest

# fmt: off
_collect = importlib.import_module("agents.04_onchain.collect")
# fmt: on


# ─── Dispatch shim ───────────────────────────────────────────────────────

def test_non_dune_chain_handlers_dict_exists():
    """The dispatch shim must exist as a module-level dict so future
    non-Dune chains (cardano, hyperliquid) can register their handlers
    via _NON_DUNE_CHAIN_HANDLERS[chain] = handler."""
    assert hasattr(_collect, "_NON_DUNE_CHAIN_HANDLERS")
    assert isinstance(_collect._NON_DUNE_CHAIN_HANDLERS, dict)


def test_stellar_handler_registered_in_dispatch_map():
    """XLM's registry chain is 'stellar'. The shim must map that to
    _collect_stellar so collect_one routes correctly."""
    assert "stellar" in _collect._NON_DUNE_CHAIN_HANDLERS
    assert callable(_collect._NON_DUNE_CHAIN_HANDLERS["stellar"])


def test_collect_one_xlm_dispatches_to_stellar_handler():
    """collect_one must check the non-Dune shim BEFORE falling through
    to the Dune category path. XLM's chain='stellar' triggers the
    registered handler; _collect_chain (the Dune path) must NOT be called.

    NB: must patch the dict entry, not the module attribute — the dict
    holds the original function reference and patch.object would only
    update the module-level name, missing the dispatch lookup."""
    from unittest.mock import MagicMock
    mock_handler = MagicMock(return_value={"ok": True, "data_quality": "PARTIAL"})
    with patch.dict(_collect._NON_DUNE_CHAIN_HANDLERS, {"stellar": mock_handler}), \
         patch.object(_collect, "_collect_chain") as dune_h:
        result = _collect.collect_one("XLM")
    mock_handler.assert_called_once()
    dune_h.assert_not_called()
    assert result["data_quality"] == "PARTIAL"


# ─── Regression: Dune-routed chains still work ───────────────────────────

def test_dune_chains_unaffected_by_dispatch_shim():
    """Adding _NON_DUNE_CHAIN_HANDLERS must NOT divert Dune-routed
    chains. AVAX/SUI/TON/XRP/NEAR registry chains must remain absent
    from the non-Dune map so collect_one falls through to
    _collect_chain."""
    from shared.tokens import get
    for sym in ("AVAX", "SUI", "TON", "XRP", "NEAR"):
        tok = get(sym)
        chain = (tok.chain or "").lower()
        assert chain not in _collect._NON_DUNE_CHAIN_HANDLERS, (
            f"{sym} registry chain={chain!r} should route via _collect_chain "
            f"(Dune), not via _NON_DUNE_CHAIN_HANDLERS"
        )


def test_collect_one_avax_still_routes_to_collect_chain():
    """End-to-end regression: AVAX must still hit _collect_chain
    (the Dune path), not get hijacked by the new dispatch shim."""
    with patch.object(_collect, "_collect_chain",
                      return_value={"ok": True, "data_quality": "PARTIAL"}) as dune_h, \
         patch.object(_collect, "_collect_stellar") as stellar_h:
        _collect.collect_one("AVAX")
    dune_h.assert_called_once()
    stellar_h.assert_not_called()


# ─── Handler behavior ────────────────────────────────────────────────────

def _init_test_db(db_path) -> None:
    """Apply onchain schema.sql to a fresh sqlite file."""
    schema = (_collect.AGENT_DIR / "schema.sql").read_text()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()
    conn.close()


def test_collect_stellar_writes_activity_metric_when_dau_returned(tmp_path, monkeypatch):
    """When _fetch_stellar_dau returns an integer, _collect_stellar
    must INSERT/UPSERT into activity_metric, write a PARTIAL sidecar
    with chain='stellar', and return populated containing
    'activity_metric.dau'."""
    test_db = tmp_path / "onchain.db"
    test_sidecars = tmp_path / "sidecars"
    monkeypatch.setattr(_collect, "DB_PATH", test_db)
    monkeypatch.setattr(_collect, "SIDECAR_DIR", test_sidecars)
    _init_test_db(test_db)

    from shared.tokens import get
    tok = get("XLM")

    with patch.object(_collect, "_fetch_stellar_dau", return_value=52256):
        result = _collect._collect_stellar("XLM", tok)

    # Return value contract
    assert result["ok"] is True
    assert result["data_quality"] == "PARTIAL"
    assert "activity_metric.dau" in result["populated"]

    # DB row written
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT dau, snapshot_at FROM activity_metric WHERE token_symbol='XLM'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["dau"] == 52256

    # Sidecar written with correct chain label
    import json
    sidecar = test_sidecars / "XLM" / "onchain_research.json"
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text())
    assert payload["chain"] == "stellar"
    assert payload["data_quality"] == "PARTIAL"
    assert payload["activity"]["dau"] == 52256


def test_collect_stellar_unavailable_when_bigquery_raises(tmp_path, monkeypatch):
    """If the BigQuery client raises (auth failure, network, etc.),
    _collect_stellar must return UNAVAILABLE — never crash the collector."""
    test_db = tmp_path / "onchain.db"
    test_sidecars = tmp_path / "sidecars"
    monkeypatch.setattr(_collect, "DB_PATH", test_db)
    monkeypatch.setattr(_collect, "SIDECAR_DIR", test_sidecars)
    _init_test_db(test_db)

    from shared.tokens import get
    tok = get("XLM")

    with patch.object(_collect, "_fetch_stellar_dau",
                      side_effect=RuntimeError("auth failed")):
        result = _collect._collect_stellar("XLM", tok)

    assert result["data_quality"] == "UNAVAILABLE"
    assert "auth failed" in result["reason"]


def test_collect_stellar_unavailable_when_dau_is_none(tmp_path, monkeypatch):
    """If _fetch_stellar_dau returns None (empty BigQuery result),
    _collect_stellar must emit UNAVAILABLE rather than insert null."""
    test_db = tmp_path / "onchain.db"
    test_sidecars = tmp_path / "sidecars"
    monkeypatch.setattr(_collect, "DB_PATH", test_db)
    monkeypatch.setattr(_collect, "SIDECAR_DIR", test_sidecars)
    _init_test_db(test_db)

    from shared.tokens import get
    tok = get("XLM")

    with patch.object(_collect, "_fetch_stellar_dau", return_value=None):
        result = _collect._collect_stellar("XLM", tok)

    assert result["data_quality"] == "UNAVAILABLE"


def test_collect_stellar_writes_holder_cohort_placeholder(tmp_path, monkeypatch):
    """Mirroring _collect_chain's behavior: write a holder_cohort row
    with smart_money_stance=UNKNOWN even when no cohort data exists.
    Lets the analyzer distinguish 'we tried' from 'we never queried'."""
    test_db = tmp_path / "onchain.db"
    test_sidecars = tmp_path / "sidecars"
    monkeypatch.setattr(_collect, "DB_PATH", test_db)
    monkeypatch.setattr(_collect, "SIDECAR_DIR", test_sidecars)
    _init_test_db(test_db)

    from shared.tokens import get
    tok = get("XLM")

    with patch.object(_collect, "_fetch_stellar_dau", return_value=52256):
        _collect._collect_stellar("XLM", tok)

    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT smart_money_stance FROM holder_cohort WHERE token_symbol='XLM'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["smart_money_stance"] == "UNKNOWN"
