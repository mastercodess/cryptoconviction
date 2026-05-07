"""
Agent 1 collector — pulls tokenomics data from free sources and writes to:
  - data/tokenomics.db        (SQLite, schema in schema.sql)
  - data/sidecars/{symbol}/   (JSON sidecars: whitepaper excerpts, etc.)

Sources used (all free):
  • CoinGecko /coins/{id}                  — supply, MC, FDV
  • Etherscan/BaseScan stats               — on-chain total supply (sanity check)
  • Sonnet research()                      — vesting schedule lookups, mechanism
                                             descriptions, value-accrual notes

Run:
  python -m agents.01_tokenomics.collect             # all tokens
  python -m agents.01_tokenomics.collect LINK AAVE   # subset

Idempotent: re-running upserts the latest snapshot. SQLite path is created if
missing; schema.sql applied on first run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import sys
import textwrap
from typing import Any, Optional

# Path setup — run as `python -m agents.01_tokenomics.collect` from repo root,
# or directly. Either works.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.data_sources import coingecko, etherscan               # noqa: E402
from shared.llm_client import research, research_json              # noqa: E402
from shared.db_helpers import (                                    # noqa: E402
    coerce_float as _coerce_float,
    coerce_int as _coerce_int,
    deep_merge_sidecar as _deep_merge_sidecar,
    normalize_pct as _normalize_pct,
    upsert_note as _upsert_note_generic,
)


def _upsert_note(c, *, symbol, topic, body, sources):
    """research_note upsert pinned to this agent's table."""
    return _upsert_note_generic(
        c, table="research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = AGENT_DIR / "data"
DB_PATH = DATA_DIR / "tokenomics.db"
SIDECAR_DIR = DATA_DIR / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"


# ─── DB helpers ─────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    fresh = not DB_PATH.exists()
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    if fresh:
        c.executescript(SCHEMA_PATH.read_text())
        c.commit()
    return c


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ─── Per-source ingest ──────────────────────────────────────────────────

def ingest_coingecko(c: sqlite3.Connection, symbol: str) -> dict:
    """Snapshot supply/MC/FDV from CoinGecko. Returns the raw response too."""
    tok = tokens.get(symbol)
    if not tok.coingecko_id:
        return {"skipped": True, "reason": "no coingecko_id in registry"}
    snap = coingecko.coin_snapshot(tok.coingecko_id)
    md = snap.get("market_data", {}) or {}
    # Coerce every numeric — CG normally returns floats, but be defensive.
    row = (
        symbol,
        _now_iso(),
        _coerce_float((md.get("market_cap") or {}).get("usd")),
        _coerce_float((md.get("fully_diluted_valuation") or {}).get("usd")),
        _coerce_float((md.get("current_price") or {}).get("usd")),
        _coerce_float(md.get("circulating_supply")),
        _coerce_float(md.get("total_supply")),
        _coerce_float(md.get("max_supply")),
    )
    c.execute(
        "INSERT OR REPLACE INTO supply_snapshot "
        "(token_symbol, snapshot_at, market_cap_usd, fdv_usd, price_usd, "
        " circulating, total_supply, max_supply) VALUES (?,?,?,?,?,?,?,?)",
        row,
    )
    c.commit()
    # Persist the full CG payload as a sidecar — agent's RLM may want to peek
    # at categories, links, dev stats, etc. without re-fetching.
    sidecar = SIDECAR_DIR / symbol / "coingecko_snapshot.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    snap = _deep_merge_sidecar(existing, snap)
    sidecar.write_text(json.dumps(snap, indent=2, default=str))
    return {"ok": True, "circulating": md.get("circulating_supply"),
            "max_supply": md.get("max_supply")}


def ingest_etherscan_supply(c: sqlite3.Connection, symbol: str) -> dict:
    """Sanity-check supply against on-chain. Useful when CG is stale."""
    if not tokens.is_evm(symbol):
        return {"skipped": True, "reason": "non-EVM"}
    tok = tokens.get(symbol)
    try:
        raw = etherscan.total_supply(tok.chain, tok.contract_address)
    except Exception as e:
        return {"error": str(e)}
    if raw is None:
        return {"error": "etherscan returned no result (rate-limit or bad contract?)"}
    note = (
        f"On-chain raw total supply (smallest unit) per {tok.chain}scan: {raw}. "
        "Divide by token decimals (typically 1e18 for ERC-20) to get human units."
    )
    _upsert_note(
        c, symbol=symbol, topic="onchain_supply", body=note,
        sources=[
            f"https://{'basescan' if tok.chain == 'base' else 'etherscan'}.io/token/{tok.contract_address}"
        ],
    )
    c.commit()
    return {"ok": True, "raw_total_supply": raw}


# ─── Sonnet-backed research (vesting, mechanism, value accrual) ─────────

_VESTING_PROMPT = textwrap.dedent("""\
    Research the token vesting / unlock schedule for {name} ({symbol}).

    Use ONLY free, publicly cited sources. Preferred:
      - tokenunlocks.app (free public pages)
      - the project's own docs / blog / launch announcement
      - CryptoRank.io free tab
      - Messari free profile

    Return JSON in this EXACT shape:
    {{
      "events": [
        {{
          "unlock_date": "YYYY-MM-DD",
          "category": "investor_unlock|team_unlock|foundation_unlock|public_emission|staking_emission|airdrop|other",
          "tokens_unlocked": <number>,
          "pct_of_supply": <decimal 0..1 or null>,
          "source_url": "https://...",
          "notes": "<one short sentence>"
        }}
      ],
      "summary": "<2-3 sentence plain-English overview of unlock pressure>",
      "data_quality": "GOOD|PARTIAL|UNAVAILABLE",
      "caveats": "<what couldn't be sourced for free, if anything>"
    }}

    If the project has no time-locked unlocks (e.g. fair launch like XMR or LINK
    pre-2017), return events: [] and explain in summary. Do not fabricate dates.
""")

_MECHANISM_PROMPT = textwrap.dedent("""\
    For {name} ({symbol}), describe the value-accrual mechanism. Use ONLY free
    public sources (project docs, governance forum, audited code).

    Return JSON:
    {{
      "has_burn": true|false,
      "burn_source": "fees|buyback|NA",
      "has_staking": true|false,
      "staking_apr_pct": <number or null>,
      "staking_emission_inflationary": true|false,
      "fee_capture_target": "protocol|stakers|lps|split|none",
      "value_accrual_summary": "<one sentence: who actually captures economic value>",
      "sources": ["https://..."],
      "data_quality": "GOOD|PARTIAL|UNAVAILABLE"
    }}

    Be honest: if staking yield is funded by token emission rather than real
    fees, mark staking_emission_inflationary=true. The orchestrator penalizes
    fake yield.
""")

_INFLATION_PROMPT = textwrap.dedent("""\
    For {name} ({symbol}), report the current annualized supply inflation rate.

    Use ONLY free sources: project docs (emission schedule), CoinGecko supply
    history, on-chain emissions tracker if public, Messari free profile.

    Return JSON:
    {{
      "current_annualized_rate": <decimal, e.g. 0.04 for 4%>,
      "method": "observed_yoy|projected_from_schedule|disclosed_target",
      "as_of_date": "YYYY-MM-DD",
      "trend_next_24m": "rising|flat|declining",
      "rationale": "<2 sentences>",
      "sources": ["https://..."]
    }}

    If the asset has fixed supply (no further emissions), return rate=0 and
    method=disclosed_target.
""")


def _research_json(prompt: str) -> Optional[dict]:
    """Thin alias for shared.llm_client.research_json — kept for backward compat."""
    return research_json(prompt)


def ingest_vesting(c: sqlite3.Connection, symbol: str) -> dict:
    tok = tokens.get(symbol)
    data = _research_json(_VESTING_PROMPT.format(name=tok.name, symbol=symbol))
    if not data:
        return {"skipped": True, "reason": "no API key or research returned no JSON"}
    sidecar = SIDECAR_DIR / symbol / "vesting_research.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    data = _deep_merge_sidecar(existing, data)
    sidecar.write_text(json.dumps(data, indent=2))
    n = 0
    for ev in data.get("events", []):
        # Coerce numerics; skip rows where date or category are missing,
        # or where tokens_unlocked can't be parsed at all.
        date = ev.get("unlock_date")
        category = ev.get("category")
        tokens_unlocked = _coerce_float(ev.get("tokens_unlocked"))
        if not date or not category or tokens_unlocked is None:
            continue
        c.execute(
            "INSERT OR REPLACE INTO unlock_event "
            "(token_symbol, unlock_date, category, tokens_unlocked, "
            " pct_of_supply, source_url, notes) VALUES (?,?,?,?,?,?,?)",
            (
                symbol,
                date,
                category,
                tokens_unlocked,
                _normalize_pct(ev.get("pct_of_supply")),
                ev.get("source_url"),
                ev.get("notes", ""),
            ),
        )
        n += 1
    summary = data.get("summary") or ""
    if summary:
        _upsert_note(
            c, symbol=symbol, topic="vesting_summary", body=summary,
            sources=[e.get("source_url", "") for e in data.get("events", []) if e.get("source_url")],
        )
    c.commit()
    return {"ok": True, "events_loaded": n, "data_quality": data.get("data_quality")}


def ingest_mechanism(c: sqlite3.Connection, symbol: str) -> dict:
    tok = tokens.get(symbol)
    data = _research_json(_MECHANISM_PROMPT.format(name=tok.name, symbol=symbol))
    if not data:
        return {"skipped": True}
    sidecar = SIDECAR_DIR / symbol / "mechanism_research.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    data = _deep_merge_sidecar(existing, data)
    sidecar.write_text(json.dumps(data, indent=2))
    c.execute(
        "INSERT OR REPLACE INTO mechanism "
        "(token_symbol, has_burn, burn_source, has_staking, staking_apr_pct, "
        " staking_emission_inflationary, fee_capture_target, value_accrual_summary, last_updated) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            symbol,
            int(bool(data.get("has_burn"))),
            data.get("burn_source"),
            int(bool(data.get("has_staking"))),
            _coerce_float(data.get("staking_apr_pct")),
            int(bool(data.get("staking_emission_inflationary"))),
            data.get("fee_capture_target"),
            data.get("value_accrual_summary"),
            _now_iso(),
        ),
    )
    c.commit()
    return {"ok": True, "data_quality": data.get("data_quality")}


def ingest_inflation(c: sqlite3.Connection, symbol: str) -> dict:
    tok = tokens.get(symbol)
    data = _research_json(_INFLATION_PROMPT.format(name=tok.name, symbol=symbol))
    if not data:
        return {"skipped": True}
    sidecar = SIDECAR_DIR / symbol / "inflation_research.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    data = _deep_merge_sidecar(existing, data)
    sidecar.write_text(json.dumps(data, indent=2))
    rate = _coerce_float(data.get("current_annualized_rate"))
    if rate is not None:
        c.execute(
            "INSERT OR REPLACE INTO inflation_point "
            "(token_symbol, as_of_date, annualized_rate, method, notes) VALUES (?,?,?,?,?)",
            (
                symbol,
                data.get("as_of_date") or _now_iso()[:10],
                rate,
                data.get("method", "observed_yoy"),
                data.get("rationale", ""),
            ),
        )
        c.commit()
    return {"ok": True, "rate": rate, "trend": data.get("trend_next_24m")}


# ─── Orchestration ──────────────────────────────────────────────────────

SOURCES = [
    ("coingecko", ingest_coingecko),
    ("etherscan_supply", ingest_etherscan_supply),
    ("vesting", ingest_vesting),
    ("mechanism", ingest_mechanism),
    ("inflation", ingest_inflation),
]


def collect_one(symbol: str, *, only: Optional[set[str]] = None) -> dict[str, Any]:
    c = _conn()
    out: dict[str, Any] = {"symbol": symbol}
    for name, fn in SOURCES:
        if only and name not in only:
            continue
        try:
            out[name] = fn(c, symbol)
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*", help="leave empty for all")
    p.add_argument("--only", help="comma-list of source names to run", default=None)
    args = p.parse_args(argv)
    syms = [s.upper() for s in (args.symbols or tokens.all_symbols())]
    only = set(args.only.split(",")) if args.only else None
    for s in syms:
        try:
            print(json.dumps({s: collect_one(s, only=only)}, indent=2, default=str))
        except KeyError as e:
            print(f"SKIP {s}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
