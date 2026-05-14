"""
Agent 4 collector — on-chain activity, capital flows, holder cohorts.

API-backed (no LLM research). Routes by token category:
  • chain-class (layer-1, layer-2, bitcoin-fork, etc.) → Dune CHAIN_DAU +
    CEX_FLOWS_BY_CHAIN (+ BTC_LTH_STH when enabled and BTC-style chain).
  • protocol-class (defi, lending, oracle, rwa, etc.) → UNAVAILABLE this
    plan; analyzer falls back to the moat agent's TVL-derived proxies.
  • other (meme, ordinals, telegram-game, ai-infra, ...) → UNAVAILABLE.

Without DUNE_API_KEY, every Dune call returns None and the sidecar is
marked UNAVAILABLE — never fabricated.
"""
from __future__ import annotations

import argparse, datetime as dt, json, pathlib, sqlite3, sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.data_sources.dune import execute_query                 # noqa: E402
from shared.data_sources._dune_queries import (                    # noqa: E402
    CHAIN_DAU,
    CEX_FLOWS_BY_CHAIN,
    BTC_LTH_STH,
)

# Shared numeric/string normalizers — keep collect's direct DB writes from
# ever poisoning REAL columns with strings like "NOT_AVAILABLE_FREE_TIER".
from shared.db_helpers import (                                    # noqa: E402
    coerce_float as _coerce_float,
    coerce_int as _coerce_int,
    normalize_pct as _normalize_pct,
    normalize_smart as _normalize_smart,
    upsert_note as _upsert_note_generic,
)


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="onchain_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "onchain.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    fresh = not DB_PATH.exists()
    c = sqlite3.connect(DB_PATH)
    if fresh:
        c.executescript(SCHEMA_PATH.read_text())
        c.commit()
    return c


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# Map a registry token's chain attribute to Dune's chain string. Some Dune
# queries return slightly different identifiers (e.g. "avalanche_c" for
# Avalanche C-Chain) — the mapping resolves that drift.
_CHAIN_TO_DUNE = {
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "polygon": "polygon",
    "tron": "tron",
    "solana": "solana",
    "bitcoin": "bitcoin",
    "bitcoin-cash": "bitcoin-cash",
    "bnb": "bnb",
    "avalanche": "avalanche_c",  # Dune uses avalanche_c for Avalanche C-Chain
    "sui": "sui",
}

# Token-category buckets for routing (matches shared/tokens.py categories).
_CHAIN_CLASS_CATEGORIES = frozenset({
    "layer-1", "layer-2", "l1-smart-contract",
    "bitcoin-fork", "privacy-l1",
})
_PROTOCOL_CLASS_CATEGORIES = frozenset({
    "defi", "defi-dex", "defi-lending", "defi-perps", "dex",
    "lending", "perp-dex", "oracle", "rwa", "lst-aggregator",
    "synthetic-dollar",
})
# Chains where BTC-style LTH/STH UTXO-age query would apply (when enabled).
_BTC_STYLE_CHAINS = frozenset({"bitcoin", "litecoin", "bitcoin-cash"})


def _today() -> str:
    return dt.date.today().isoformat()


def _write_sidecar(symbol: str, payload: dict) -> None:
    sidecar = SIDECAR_DIR / symbol / "onchain_research.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(payload, indent=2))


def _unavailable(symbol: str, reason: str) -> dict:
    _write_sidecar(symbol, {
        "as_of": _today(),
        "data_quality": "UNAVAILABLE",
        "notes": reason,
        "sources": [],
    })
    return {"ok": True, "data_quality": "UNAVAILABLE", "reason": reason}


def collect_one(symbol: str) -> dict:
    """Fetch on-chain data from Dune Analytics. Routes by token category:
    - chain-class → Dune CHAIN_DAU + CEX_FLOWS_BY_CHAIN
    - protocol-class → skip (use TVL/fee proxies via moat agent's data)
    - other → no Dune route; emit UNAVAILABLE
    """
    tok = tokens.get(symbol)
    cat = (tok.category or "").lower()

    if cat in _CHAIN_CLASS_CATEGORIES:
        return _collect_chain(symbol, tok)
    if cat in _PROTOCOL_CLASS_CATEGORIES:
        return _unavailable(
            symbol,
            f"protocol-class token (cat={cat}); onchain metrics derived from moat agent's TVL — no Dune route this plan",
        )
    return _unavailable(symbol, f"category='{cat}' has no Dune route in onchain collector")


def _collect_chain(symbol: str, tok) -> dict:
    chain_key = _CHAIN_TO_DUNE.get((tok.chain or "").lower())
    if not chain_key:
        return _unavailable(
            symbol, f"chain '{tok.chain}' not in Dune chain map; add to _CHAIN_TO_DUNE",
        )

    dau_rows = execute_query(query_id=CHAIN_DAU, ttl_hours=24)
    flow_rows = execute_query(query_id=CEX_FLOWS_BY_CHAIN, ttl_hours=168)
    # BTC_LTH_STH may be None (not yet implemented); skip if so.
    lth_rows = None
    if BTC_LTH_STH is not None and chain_key in _BTC_STYLE_CHAINS:
        lth_rows = execute_query(query_id=BTC_LTH_STH, ttl_hours=168)

    if dau_rows is None and flow_rows is None and lth_rows is None:
        return _unavailable(symbol, "Dune API unavailable (no key or all queries failed)")

    c = _conn()
    today = _today()
    populated_fields = []

    # activity_metric from CHAIN_DAU
    dau_value = None
    if dau_rows is not None:
        row = next(
            (r for r in dau_rows if (r.get("chain") or "").lower() == chain_key),
            None,
        )
        if row and row.get("daily_active_addresses") is not None:
            dau_value = _coerce_int(row["daily_active_addresses"])
    if dau_value is not None:
        c.execute(
            "INSERT OR REPLACE INTO activity_metric "
            "(token_symbol, snapshot_at, dau, wau, mau, dau_mau_ratio, "
            " daily_tx_count, new_addresses_7d) VALUES (?,?,?,?,?,?,?,?)",
            (symbol, today, dau_value, None, None, None, None, None),
        )
        populated_fields.append("activity_metric.dau")

    # exchange_flow from CEX_FLOWS_BY_CHAIN
    flow_summary = None
    if flow_rows is not None:
        row = next(
            (r for r in flow_rows if (r.get("chain") or "").lower() == chain_key),
            None,
        )
        if row:
            inflow = _coerce_float(row.get("inflow_usd"))
            outflow = _coerce_float(row.get("outflow_usd"))
            net = _coerce_float(row.get("net_usd"))
            if net is None and inflow is not None and outflow is not None:
                net = inflow - outflow
            if any(v is not None for v in (inflow, outflow, net)):
                c.execute(
                    "INSERT OR REPLACE INTO exchange_flow "
                    "(token_symbol, date, inflow_usd, outflow_usd, net_usd) "
                    "VALUES (?,?,?,?,?)",
                    (symbol, row.get("date") or today, inflow, outflow, net),
                )
                populated_fields.append("exchange_flow")
                flow_summary = {"inflow_usd": inflow, "outflow_usd": outflow, "net_usd": net}

    # holder_cohort from BTC_LTH_STH (only for BTC-style chains when enabled)
    cohort_summary = None
    lth_pct = None
    sth_pct = None
    if lth_rows is not None and lth_rows:
        row = lth_rows[0]
        lth_pct = _normalize_pct(row.get("lth_supply_pct"))
        sth_pct = _normalize_pct(row.get("sth_supply_pct"))
        if lth_pct is not None or sth_pct is not None:
            populated_fields.append("holder_cohort.lth_sth")
            cohort_summary = {"lth_supply_pct": lth_pct, "sth_supply_pct": sth_pct}

    # Always write a holder_cohort row with at least the smart_money_stance,
    # so the analyzer can distinguish "we tried, no data" from "we didn't query".
    c.execute(
        "INSERT OR REPLACE INTO holder_cohort "
        "(token_symbol, snapshot_at, lth_supply_pct, sth_supply_pct, "
        " smart_money_stance) VALUES (?,?,?,?,?)",
        (symbol, today, lth_pct, sth_pct, "UNKNOWN"),
    )

    c.commit()

    if not populated_fields:
        return _unavailable(symbol, f"Dune returned no data for chain '{chain_key}'")

    data_quality = "GOOD" if dau_value is not None and flow_summary else "PARTIAL"
    sources_used = [f"https://dune.com/queries/{q}" for q in (CHAIN_DAU, CEX_FLOWS_BY_CHAIN)]
    if BTC_LTH_STH is not None and chain_key in _BTC_STYLE_CHAINS:
        sources_used.append(f"https://dune.com/queries/{BTC_LTH_STH}")

    _write_sidecar(symbol, {
        "as_of": today,
        "data_quality": data_quality,
        "chain": chain_key,
        "activity": {"dau": dau_value},
        "exchange_flow_30d": flow_summary,
        "holder_cohort": cohort_summary,
        "smart_money_stance": "UNKNOWN",
        "wash_trade_concerns": "Smart-money detection requires curated address list (deferred to plan 1c.2).",
        "notes": (
            "API-fetched (no LLM research): Dune Analytics. "
            f"Populated fields: {', '.join(populated_fields)}."
        ),
        "sources": sources_used,
    })
    return {"ok": True, "data_quality": data_quality, "populated": populated_fields}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*")
    args = p.parse_args(argv)
    syms = [s.upper() for s in (args.symbols or tokens.all_symbols())]
    for s in syms:
        try:
            print(json.dumps({s: collect_one(s)}, indent=2, default=str))
        except KeyError as e:
            print(f"SKIP {s}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
