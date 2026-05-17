"""Pin the ADA/Cardano onchain integration from plan 2026-05-17 (plan 2b).

The handler fetches DAU from AdaStat's /accounts.json endpoint via
cursor pagination (sort=last_tx desc, count until last_tx < now-24h).
Count = DAU at stake-address level per Cardano canonical methodology.

Tests pin the integration and the routing without making real network
calls — AdaStat is mocked at both the helper-function boundary
(_fetch_cardano_dau) and the requests.get boundary (for pagination logic).
"""
from __future__ import annotations

import importlib
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

# fmt: off
_collect = importlib.import_module("agents.04_onchain.collect")
# fmt: on


# ─── Dispatch / registration ─────────────────────────────────────────────

def test_cardano_handler_registered_in_dispatch_map():
    """ADA's registry chain is 'cardano'. The shim from plan 2a must
    now map that to _collect_cardano."""
    assert "cardano" in _collect._NON_DUNE_CHAIN_HANDLERS
    assert callable(_collect._NON_DUNE_CHAIN_HANDLERS["cardano"])


def test_stellar_still_registered_no_regression():
    """Plan 2a's stellar registration must coexist with plan 2b's
    cardano registration."""
    assert "stellar" in _collect._NON_DUNE_CHAIN_HANDLERS


def test_collect_one_ada_dispatches_to_cardano_handler():
    """collect_one must check the non-Dune shim BEFORE the Dune
    category path. ADA's chain='cardano' triggers _collect_cardano;
    _collect_chain must NOT be called."""
    mock_handler = MagicMock(return_value={"ok": True, "data_quality": "PARTIAL"})
    with patch.dict(_collect._NON_DUNE_CHAIN_HANDLERS, {"cardano": mock_handler}), \
         patch.object(_collect, "_collect_chain") as dune_h:
        result = _collect.collect_one("ADA")
    mock_handler.assert_called_once()
    dune_h.assert_not_called()
    assert result["data_quality"] == "PARTIAL"


def test_dune_chains_unaffected_by_cardano_registration():
    """Plan 2b's registration must NOT divert Dune-routed chains.
    AVAX/SUI/TON/XRP/NEAR must remain absent from the non-Dune map."""
    from shared.tokens import get
    for sym in ("AVAX", "SUI", "TON", "XRP", "NEAR"):
        tok = get(sym)
        assert (tok.chain or "").lower() not in _collect._NON_DUNE_CHAIN_HANDLERS


# ─── Handler behavior (mocked DAU helper) ────────────────────────────────

def _init_test_db(db_path) -> None:
    """Apply onchain schema.sql to a fresh sqlite file."""
    schema = (_collect.AGENT_DIR / "schema.sql").read_text()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()
    conn.close()


def test_collect_cardano_writes_activity_metric_when_dau_returned(tmp_path, monkeypatch):
    """When _fetch_cardano_dau returns an integer, _collect_cardano
    must INSERT/UPSERT into activity_metric, write a PARTIAL sidecar
    with chain='cardano', and return populated containing
    'activity_metric.dau'."""
    test_db = tmp_path / "onchain.db"
    test_sidecars = tmp_path / "sidecars"
    monkeypatch.setattr(_collect, "DB_PATH", test_db)
    monkeypatch.setattr(_collect, "SIDECAR_DIR", test_sidecars)
    _init_test_db(test_db)

    from shared.tokens import get
    tok = get("ADA")

    with patch.object(_collect, "_fetch_cardano_dau", return_value=52000):
        result = _collect._collect_cardano("ADA", tok)

    assert result["ok"] is True
    assert result["data_quality"] == "PARTIAL"
    assert "activity_metric.dau" in result["populated"]

    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT dau FROM activity_metric WHERE token_symbol='ADA'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["dau"] == 52000

    import json
    sidecar = test_sidecars / "ADA" / "onchain_research.json"
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text())
    assert payload["chain"] == "cardano"
    assert payload["data_quality"] == "PARTIAL"
    assert payload["activity"]["dau"] == 52000


def test_collect_cardano_unavailable_when_adastat_raises(tmp_path, monkeypatch):
    """If the AdaStat fetch raises (network, 429 rate limit, etc.),
    _collect_cardano must return UNAVAILABLE — never crash the collector."""
    test_db = tmp_path / "onchain.db"
    test_sidecars = tmp_path / "sidecars"
    monkeypatch.setattr(_collect, "DB_PATH", test_db)
    monkeypatch.setattr(_collect, "SIDECAR_DIR", test_sidecars)
    _init_test_db(test_db)

    from shared.tokens import get
    tok = get("ADA")

    with patch.object(_collect, "_fetch_cardano_dau",
                      side_effect=RuntimeError("AdaStat timeout")):
        result = _collect._collect_cardano("ADA", tok)

    assert result["data_quality"] == "UNAVAILABLE"
    assert "AdaStat timeout" in result["reason"]


def test_collect_cardano_unavailable_when_dau_is_none(tmp_path, monkeypatch):
    """If _fetch_cardano_dau returns None (empty result), _collect_cardano
    must emit UNAVAILABLE rather than insert null."""
    test_db = tmp_path / "onchain.db"
    test_sidecars = tmp_path / "sidecars"
    monkeypatch.setattr(_collect, "DB_PATH", test_db)
    monkeypatch.setattr(_collect, "SIDECAR_DIR", test_sidecars)
    _init_test_db(test_db)

    from shared.tokens import get
    tok = get("ADA")

    with patch.object(_collect, "_fetch_cardano_dau", return_value=None):
        result = _collect._collect_cardano("ADA", tok)

    assert result["data_quality"] == "UNAVAILABLE"


def test_collect_cardano_writes_holder_cohort_placeholder(tmp_path, monkeypatch):
    """Mirror _collect_chain / _collect_stellar: write a holder_cohort
    row with smart_money_stance=UNKNOWN even when no cohort data exists."""
    test_db = tmp_path / "onchain.db"
    test_sidecars = tmp_path / "sidecars"
    monkeypatch.setattr(_collect, "DB_PATH", test_db)
    monkeypatch.setattr(_collect, "SIDECAR_DIR", test_sidecars)
    _init_test_db(test_db)

    from shared.tokens import get
    tok = get("ADA")

    with patch.object(_collect, "_fetch_cardano_dau", return_value=52000):
        _collect._collect_cardano("ADA", tok)

    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT smart_money_stance FROM holder_cohort WHERE token_symbol='ADA'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["smart_money_stance"] == "UNKNOWN"


# ─── Pagination logic (mocked HTTP) ──────────────────────────────────────

def _make_response(rows: list, next_cursor: str | None) -> MagicMock:
    """Build a mocked requests.Response with the AdaStat envelope."""
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = {
        "code": 200,
        "rows": rows,
        "cursor": {"after": next_cursor or "", "next": bool(next_cursor)},
    }
    return r


def test_fetch_cardano_dau_paginates_and_stops_at_cutoff(monkeypatch):
    """The helper must paginate until last_tx falls below the 24h
    cutoff, returning the count of accounts traversed before that point.

    Mock 3 pages: 1000 fresh + 1000 fresh + 500 fresh then 500 stale.
    Expected DAU = 2500."""
    import time
    now = time.time()
    fresh_ts = int(now - 1800)      # 30 min ago — well within 24h
    stale_ts = int(now - 86400 * 2)  # 2 days ago — outside 24h

    page1 = [{"last_tx": fresh_ts} for _ in range(1000)]
    page2 = [{"last_tx": fresh_ts} for _ in range(1000)]
    page3 = (
        [{"last_tx": fresh_ts} for _ in range(500)]
        + [{"last_tx": stale_ts} for _ in range(500)]
    )

    responses = iter([
        _make_response(page1, next_cursor="cursor-1"),
        _make_response(page2, next_cursor="cursor-2"),
        _make_response(page3, next_cursor="cursor-3"),
    ])

    def fake_get(url, params=None, timeout=None):
        return next(responses)

    # Speed: no real sleep between pages
    monkeypatch.setattr(_collect.time, "sleep", lambda s: None)
    monkeypatch.setattr(_collect.requests, "get", fake_get)

    dau = _collect._fetch_cardano_dau()
    assert dau == 2500


def test_fetch_cardano_dau_handles_no_next_cursor(monkeypatch):
    """If pagination exhausts (cursor.next == False) before hitting
    a stale row, return the total count."""
    import time
    fresh_ts = int(time.time() - 1800)
    page = [{"last_tx": fresh_ts} for _ in range(42)]
    responses = iter([_make_response(page, next_cursor=None)])

    monkeypatch.setattr(_collect.time, "sleep", lambda s: None)
    monkeypatch.setattr(_collect.requests, "get",
                        lambda *a, **kw: next(responses))

    assert _collect._fetch_cardano_dau() == 42


def test_fetch_cardano_dau_returns_zero_when_first_row_already_stale(monkeypatch):
    """If even the most-recently-active account is older than 24h
    (extreme edge case), return 0."""
    import time
    stale_ts = int(time.time() - 86400 * 2)
    page = [{"last_tx": stale_ts}]
    responses = iter([_make_response(page, next_cursor=None)])

    monkeypatch.setattr(_collect.time, "sleep", lambda s: None)
    monkeypatch.setattr(_collect.requests, "get",
                        lambda *a, **kw: next(responses))

    assert _collect._fetch_cardano_dau() == 0
