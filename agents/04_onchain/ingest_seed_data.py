"""
One-shot seed ingest for Agent 4 — populates onchain.db from JSON sidecars
written by the Sonnet research subagents.

Reuses the deep-search normalizer pattern from Agent 2 (revenue) since the
sidecars came back with similar shape variations.

Run:
    python -m agents.04_onchain.ingest_seed_data
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.db_helpers import (                                    # noqa: E402
    coerce_float as _coerce_float,
    coerce_int as _coerce_int,
    deep_find as _deep_find,
    deep_find_str as _deep_find_str,
    normalize_grade as _normalize_grade,
    normalize_pct as _normalize_pct,
    normalize_smart as _normalize_smart,
    parse_numeric as _parse_numeric,
    upsert_note as _upsert_note_generic,
    walk as _walk,
)

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = AGENT_DIR / "data"
DB_PATH = DATA_DIR / "onchain.db"
SIDECAR_DIR = DATA_DIR / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"


# ─── Field aliases ──────────────────────────────────────────────────────

KEYS_DAU = ("dau", "daily_active_users", "daily_active_addresses",
            "daily_unique_users", "dau_traders", "active_traders_count",
            "active_users", "daily_unique_swappers")
KEYS_WAU = ("wau", "weekly_active_users", "weekly_active_addresses",
            "weekly_active_stakers", "weekly_stakers")
KEYS_MAU = ("mau", "monthly_active_users", "monthly_active_addresses",
            "monthly_active_wallets", "monthly_active_wallets_28d",
            "monthly_active_users_28d")
KEYS_DAU_MAU = ("dau_mau_ratio", "dau_mau", "stickiness", "stickiness_ratio")
KEYS_TX = ("daily_tx_count", "tx_count_daily", "daily_transactions",
           "transaction_count_daily")
KEYS_NEW_ADDR = ("new_addresses_7d", "new_addresses_weekly", "new_wallets_7d")
KEYS_AS_OF = ("as_of", "as_of_date", "snapshot_date", "research_date", "timestamp")

KEYS_INFLOW = ("total_inflow_usd", "inflow_usd", "exchange_inflow_usd",
               "inflow_30d", "total_inflow")
KEYS_OUTFLOW = ("total_outflow_usd", "outflow_usd", "exchange_outflow_usd",
                "outflow_30d", "total_outflow")
KEYS_NET = ("net_usd", "net_flow_usd", "net_flow", "net_30d")
KEYS_FLOW_TREND = ("trend", "flow_trend", "net_flow_signal")

KEYS_LTH = ("lth_supply_pct", "long_term_holder_supply_pct",
            "lth_supply_percentage", "long_term_holders_pct",
            "lth_pct", "long_term_holder_pct")
KEYS_STH = ("sth_supply_pct", "short_term_holder_supply_pct",
            "short_term_holders_pct", "sth_pct")
KEYS_SMART = ("smart_money_stance", "smart_money", "smart_money_signal",
              "smart_money_signals_summary")

KEYS_RETENTION = ("retention_health", "retention", "retention_grade")
KEYS_AUTHENTICITY = ("growth_authenticity", "growth_authenticity_verdict",
                     "authenticity")
KEYS_RATIONALE = ("rationale", "summary", "executive_summary",
                  "key_findings_summary")
KEYS_DQ = ("data_quality", "data_quality_notes")
KEYS_WASH = ("wash_trade_assessment", "wash_trade_flag", "wash_trade_concerns")


# ─── DB plumbing ────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fresh = not DB_PATH.exists()
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    if fresh:
        c.executescript(SCHEMA_PATH.read_text())
        c.commit()
    return c


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _upsert_note(c: sqlite3.Connection, *, symbol: str, topic: str,
                 body: str, sources: list | None) -> bool:
    """Thin wrapper that pins the agent's research-note table name.
    Shared with collect.py — both call this rather than INSERTing directly."""
    return _upsert_note_generic(
        c, table="onchain_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )


# ─── Per-token ingest ───────────────────────────────────────────────────

def ingest_one(c: sqlite3.Connection, symbol: str) -> dict:
    sidecar = SIDECAR_DIR / symbol / "onchain_research.json"
    if not sidecar.exists():
        return {"skipped": True, "reason": "no onchain_research.json"}
    raw = json.loads(sidecar.read_text())

    # 1. activity_metric
    dau = _coerce_int(_deep_find(raw, KEYS_DAU))
    wau = _coerce_int(_deep_find(raw, KEYS_WAU))
    mau = _coerce_int(_deep_find(raw, KEYS_MAU))
    dau_mau = _normalize_pct(_deep_find(raw, KEYS_DAU_MAU))
    if dau_mau is None and dau and mau:
        dau_mau = round(dau / mau, 4)
    tx = _coerce_int(_deep_find(raw, KEYS_TX))
    new_addr = _coerce_int(_deep_find(raw, KEYS_NEW_ADDR))
    as_of = _deep_find_str(raw, KEYS_AS_OF) or _now()[:10]

    c.execute(
        "INSERT OR REPLACE INTO activity_metric "
        "(token_symbol, snapshot_at, dau, wau, mau, dau_mau_ratio, "
        " daily_tx_count, new_addresses_7d) VALUES (?,?,?,?,?,?,?,?)",
        (symbol, as_of, dau, wau, mau, dau_mau, tx, new_addr),
    )

    # 2. exchange_flow — store the 30d aggregate as a single synthetic row
    inflow = _coerce_float(_deep_find(raw, KEYS_INFLOW))
    outflow = _coerce_float(_deep_find(raw, KEYS_OUTFLOW))
    net = _coerce_float(_deep_find(raw, KEYS_NET))
    if net is None and inflow is not None and outflow is not None:
        net = inflow - outflow
    if any(x is not None for x in (inflow, outflow, net)):
        c.execute(
            "INSERT OR REPLACE INTO exchange_flow "
            "(token_symbol, date, inflow_usd, outflow_usd, net_usd) "
            "VALUES (?,?,?,?,?)",
            (symbol, as_of, inflow, outflow, net),
        )

    # 3. holder_cohort
    lth = _normalize_pct(_deep_find(raw, KEYS_LTH))
    sth = _normalize_pct(_deep_find(raw, KEYS_STH))
    smart = _normalize_smart(_deep_find_str(raw, KEYS_SMART))
    c.execute(
        "INSERT OR REPLACE INTO holder_cohort "
        "(token_symbol, snapshot_at, lth_supply_pct, sth_supply_pct, smart_money_stance) "
        "VALUES (?,?,?,?,?)",
        (symbol, as_of, lth, sth, smart),
    )

    # 4. research notes (rationale + wash-trade signal) — idempotent on
    #    (symbol, topic, body) so re-running ingest doesn't multiply rows.
    rationale = _deep_find_str(raw, KEYS_RATIONALE)
    if rationale:
        _upsert_note(
            c, symbol=symbol, topic="summary",
            body=rationale[:4000],
            sources=raw.get("sources") or raw.get("data_sources") or [],
        )
    wash = _deep_find_str(raw, KEYS_WASH)
    if wash:
        _upsert_note(
            c, symbol=symbol, topic="wash_trade",
            body=wash[:2000], sources=None,
        )

    c.commit()
    return {
        "symbol": symbol,
        "dau": dau, "mau": mau, "dau_mau": dau_mau,
        "net_flow_usd": net,
        "lth_pct": lth,
        "smart_money": smart,
        "retention": _normalize_grade(_deep_find_str(raw, KEYS_RETENTION)),
        "authenticity": _deep_find_str(raw, KEYS_AUTHENTICITY),
    }


def main() -> int:
    c = _conn()
    print(f"Ingesting from {SIDECAR_DIR} → {DB_PATH}\n")
    for sym in tokens.all_symbols():
        try:
            r = ingest_one(c, sym)
            print(f"  {sym}: {r}")
        except Exception as e:                              # noqa: BLE001
            print(f"  {sym}: ERROR — {type(e).__name__}: {e}")

    print("\nDB sanity check:")
    for q, label in [
        ("SELECT COUNT(*) FROM activity_metric", "activity rows"),
        ("SELECT COUNT(*) FROM exchange_flow", "exchange flow rows"),
        ("SELECT COUNT(*) FROM holder_cohort", "holder cohort rows"),
        ("SELECT COUNT(*) FROM onchain_research_note", "research notes"),
        ("SELECT COUNT(*) FROM activity_metric WHERE dau IS NOT NULL", "tokens with DAU"),
    ]:
        n = c.execute(q).fetchone()[0]
        print(f"  {label:>22}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
