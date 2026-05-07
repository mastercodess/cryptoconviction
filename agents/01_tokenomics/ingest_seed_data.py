"""
One-shot seed ingest — populates Agent 1's SQLite DB from the JSON sidecars
that the Sonnet research subagents wrote.

Why this exists separately from collect.py:
  • collect.py pulls fresh data from CoinGecko + Sonnet research and assumes
    a stable JSON contract. The seed sidecars were free-form (Sonnet wrote
    whatever shape made sense per token), so direct ingestion needs a
    tolerant normalizer.
  • Run this ONCE after the seed sidecars exist. After that, normal
    collect.py runs will keep things in sync.

Run:
    python -m agents.01_tokenomics.ingest_seed_data

Idempotent — re-running upserts.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
import sys
from typing import Any, Optional

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.db_helpers import upsert_note as _upsert_note_generic   # noqa: E402


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = AGENT_DIR / "data"
DB_PATH = DATA_DIR / "tokenomics.db"
SIDECAR_DIR = DATA_DIR / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"


# ─── Tolerant accessors: handle the variations in sidecar shapes ────────

def _first(d: dict, *keys, default=None):
    """Return the first present, non-None value among the given keys."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _normalize_supply(s: dict) -> dict:
    """Map the various Sonnet-emitted supply shapes to canonical keys.

    Common gotcha: Sonnet sometimes puts the supply cap into `total_supply`
    (or `total_supply_cap`) when there's also a `supply_capped: true` flag,
    rather than into `max_supply`. We promote total → max for capped tokens
    that don't have an explicit max.
    """
    out = {
        "circulating": _first(s, "circulating", "circulating_supply", "circulating_supply_tokens", "circulating_supply_current"),
        "total_supply": _first(s, "total_supply", "total", "total_supply_tokens", "total_supply_current", "total_supply_cap", "current_supply"),
        "max_supply": _first(s, "max_supply", "max", "maximum_supply", "max_supply_tokens", "supply_cap_numeric"),
        "as_of": _first(s, "as_of", "as_of_date", "circulating_supply_timestamp"),
        "source": _first(s, "source", "sources"),
    }
    # Capped-token fallback: if the agent flagged the token as capped and we
    # don't have max_supply, use total_supply.
    capped = bool(s.get("supply_capped") or s.get("max_equals_total"))
    if out["max_supply"] is None and capped and out["total_supply"] is not None:
        out["max_supply"] = out["total_supply"]
    # Also: explicit cap notes like "11M cap" sometimes hide in total_supply_cap
    # which we already promoted to total_supply above. If that key was the
    # source AND no max_supply set, mirror it.
    if out["max_supply"] is None and "total_supply_cap" in s and s["total_supply_cap"] is not None:
        out["max_supply"] = s["total_supply_cap"]
    return out


def _normalize_market(m: dict) -> dict:
    return {
        "price_usd": _first(m, "price_usd", "current_price_usd", "price"),
        "market_cap_usd": _first(m, "market_cap_usd", "market_cap", "mc_usd"),
        "fdv_usd": _first(m, "fdv_usd", "fdv", "fully_diluted_valuation_usd"),
        "as_of": _first(m, "as_of", "as_of_date", "snapshot_date"),
    }


def _coerce_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        x = x.replace(",", "").replace("$", "").strip()
        try:
            return float(x)
        except ValueError:
            return None
    return None


# ─── DB setup ───────────────────────────────────────────────────────────

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


# ─── Per-token ingest ───────────────────────────────────────────────────

def ingest_one(c: sqlite3.Connection, symbol: str) -> dict:
    sidecar = SIDECAR_DIR / symbol / "research.json"
    if not sidecar.exists():
        return {"skipped": True, "reason": "no research.json sidecar"}
    raw = json.loads(sidecar.read_text())
    summary = {"symbol": symbol}

    # 1. supply_snapshot
    sup = _normalize_supply(raw.get("supply", {}))
    mkt = _normalize_market(raw.get("market", {}))
    c.execute(
        "INSERT OR REPLACE INTO supply_snapshot "
        "(token_symbol, snapshot_at, market_cap_usd, fdv_usd, price_usd, "
        " circulating, total_supply, max_supply) VALUES (?,?,?,?,?,?,?,?)",
        (
            symbol,
            sup.get("as_of") or mkt.get("as_of") or _now(),
            _coerce_float(mkt.get("market_cap_usd")),
            _coerce_float(mkt.get("fdv_usd")),
            _coerce_float(mkt.get("price_usd")),
            _coerce_float(sup.get("circulating")),
            _coerce_float(sup.get("total_supply")),
            _coerce_float(sup.get("max_supply")),
        ),
    )
    summary["supply"] = "ok"

    # 2. unlock_event(s)
    unlocks = raw.get("unlocks", {}) or {}
    n_unlocks = 0
    for ev in unlocks.get("events", []) or []:
        try:
            # Sonnet emitted varied shapes: tokens_unlocked, amount, amount_<symbol>.
            tokens_unlocked = _coerce_float(_first(ev, "tokens_unlocked", "tokens", "amount"))
            if tokens_unlocked is None:
                # last-resort: any key starting with amount_ or tokens_
                for k, v in ev.items():
                    if k.lower().startswith(("amount_", "tokens_")) and isinstance(v, (int, float)):
                        tokens_unlocked = float(v)
                        break
            if tokens_unlocked is None:
                continue
            c.execute(
                "INSERT OR REPLACE INTO unlock_event "
                "(token_symbol, unlock_date, category, tokens_unlocked, "
                " pct_of_supply, source_url, notes) VALUES (?,?,?,?,?,?,?)",
                (
                    symbol,
                    str(_first(ev, "unlock_date", "date", "release_date") or ""),
                    str(_first(ev, "category", "type") or "other"),
                    tokens_unlocked,
                    _coerce_float(_first(ev, "pct_of_supply", "percent_of_supply",
                                         "percentage_of_total_supply", "percentage_of_total")),
                    _first(ev, "source_url", "source"),
                    _first(ev, "notes", "description", "note") or "",
                ),
            )
            n_unlocks += 1
        except Exception as e:                       # noqa: BLE001
            summary.setdefault("unlock_errors", []).append(str(e))
    summary["unlocks_loaded"] = n_unlocks
    if unlocks.get("summary"):
        _upsert_note(
            c, symbol=symbol, topic="vesting_summary",
            body=unlocks["summary"],
            sources=[e.get("source_url", "") for e in unlocks.get("events", []) if e.get("source_url")],
        )

    # 3. mechanism
    mech = raw.get("mechanism", {}) or {}
    if mech:
        c.execute(
            "INSERT OR REPLACE INTO mechanism "
            "(token_symbol, has_burn, burn_source, has_staking, staking_apr_pct, "
            " staking_emission_inflationary, fee_capture_target, value_accrual_summary, last_updated) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                symbol,
                int(bool(mech.get("has_burn"))),
                mech.get("burn_source"),
                int(bool(mech.get("has_staking"))),
                _coerce_float(mech.get("staking_apr_pct")),
                int(bool(mech.get("staking_emission_inflationary"))),
                mech.get("fee_capture_target"),
                mech.get("value_accrual_summary"),
                _now(),
            ),
        )
        summary["mechanism"] = "ok"

    # 4. inflation
    inf = raw.get("inflation", {}) or {}
    rate = _coerce_float(_first(inf, "current_annualized_rate", "annualized_rate", "rate"))
    if rate is not None:
        c.execute(
            "INSERT OR REPLACE INTO inflation_point "
            "(token_symbol, as_of_date, annualized_rate, method, notes) VALUES (?,?,?,?,?)",
            (
                symbol,
                inf.get("as_of_date") or _now()[:10],
                rate,
                inf.get("method", "observed_yoy"),
                inf.get("rationale") or inf.get("notes") or "",
            ),
        )
        summary["inflation_rate"] = rate

    # 5. catch-all research note: data_collection_notes + raw mechanism summary
    if raw.get("data_collection_notes"):
        _upsert_note(
            c, symbol=symbol, topic="collection_notes",
            body=raw["data_collection_notes"],
            sources=raw.get("sources", []),
        )

    c.commit()
    return summary


def main() -> int:
    c = _conn()
    print(f"Ingesting from {SIDECAR_DIR} → {DB_PATH}")
    print()
    total = {"loaded": 0, "skipped": 0}
    for sym in tokens.all_symbols():
        try:
            r = ingest_one(c, sym)
            print(f"  {sym}: {r}")
            if r.get("skipped"):
                total["skipped"] += 1
            else:
                total["loaded"] += 1
        except Exception as e:                          # noqa: BLE001
            print(f"  {sym}: ERROR — {type(e).__name__}: {e}")
    print()
    print(f"Done. {total['loaded']} loaded, {total['skipped']} skipped.")

    # Quick sanity check
    print("\nDB sanity check:")
    for q, label in [
        ("SELECT COUNT(*) FROM supply_snapshot", "supply rows"),
        ("SELECT COUNT(*) FROM unlock_event", "unlock events"),
        ("SELECT COUNT(*) FROM mechanism", "mechanism rows"),
        ("SELECT COUNT(*) FROM inflation_point", "inflation rows"),
        ("SELECT COUNT(*) FROM research_note", "research notes"),
    ]:
        n = c.execute(q).fetchone()[0]
        print(f"  {label}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
