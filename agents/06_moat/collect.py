"""Agent 6 collector — competitive position via direct DefiLlama API calls.

Previously this module called an LLM (`research_json`) to hallucinate competitor
lists and market shares. It now routes by token category:
  - chain-class    (layer-1, layer-2, etc.)    → DefiLlama /chains
  - protocol-class (defi-lending, dex, etc.)   → DefiLlama /protocols filtered by category
  - other          (meme, ordinals, etc.)      → no DefiLlama route; emit UNAVAILABLE
"""
from __future__ import annotations
import argparse, datetime as dt, json, pathlib, sqlite3, sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path: sys.path.insert(0, str(_REPO_ROOT))
from shared import tokens
from shared.data_sources.defillama import chains, protocols
from shared.db_helpers import (
    coerce_float as _coerce_float,
    coerce_int as _coerce_int,
    normalize_pct as _normalize_pct,
    upsert_note as _upsert_note_generic,
)


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="moat_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "moat.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True); SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    fresh = not DB_PATH.exists()
    c = sqlite3.connect(DB_PATH)
    if fresh: c.executescript(SCHEMA_PATH.read_text()); c.commit()
    return c


def _now() -> str: return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# Category classes — drives which DefiLlama endpoint we call.
_CHAIN_CLASS_CATEGORIES = frozenset({
    "layer-1", "layer-2", "l1-smart-contract",
    "bitcoin-fork", "privacy-l1",
})
_PROTOCOL_CLASS_CATEGORIES = frozenset({
    "defi", "defi-dex", "defi-lending", "defi-perps", "dex",
    "lending", "perp-dex", "oracle", "rwa", "lst-aggregator",
    "synthetic-dollar",
})

# Map our registry's `category` strings to DefiLlama's `category` strings
# for the /protocols endpoint. DefiLlama uses title-case + slightly
# different vocabulary (e.g. "Dexs" vs "dex"). Categories without a
# verified mapping are intentionally absent — they fall through to
# UNAVAILABLE in collect_one rather than guessing at the wrong category.
# Known-unmapped (need registry retagging or fresh verification):
#   - "synthetic-dollar": ENA's DefiLlama entries are "Basis Trading" / "RWA"
#   - "defi": too generic; DYDX (defi → defi-perps in DefiLlama "Derivatives")
_DEFILLAMA_CATEGORY_MAP = {
    "defi-lending": "Lending",
    "lending": "Lending",
    "defi-dex": "Dexs",
    "dex": "Dexs",
    "defi-perps": "Derivatives",
    "perp-dex": "Derivatives",
    "oracle": "Oracle",
    "rwa": "RWA",
    "lst-aggregator": "Liquid Staking",
}


def _today() -> str:
    return dt.date.today().isoformat()


def _write_sidecar(symbol: str, payload: dict) -> None:
    sidecar = SIDECAR_DIR / symbol / "moat_research.json"
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
    """Fetch moat data from DefiLlama. Routes by token category:
    - chain-class (layer-1, layer-2, etc.) → DefiLlama /chains
    - protocol-class (defi-lending, dex, etc.) → DefiLlama /protocols filtered by category
    - other (meme, ordinals, etc.) → no DefiLlama route; emit UNAVAILABLE
    """
    tok = tokens.get(symbol)
    cat = (tok.category or "").lower()
    c = _conn()

    if cat in _CHAIN_CLASS_CATEGORIES:
        return _collect_chain(symbol, tok, c)
    if cat in _PROTOCOL_CLASS_CATEGORIES:
        return _collect_protocol(symbol, tok, c, cat)
    return _unavailable(symbol, f"category='{cat}' has no DefiLlama route in this collector")


def _collect_chain(symbol: str, tok, c: sqlite3.Connection) -> dict:
    rows = chains()
    if rows is None:
        return _unavailable(symbol, "DefiLlama /chains returned an error")
    # Match self by tokenSymbol case-insensitively, OR by name vs registry name
    sym_l = symbol.lower()
    name_l = (tok.name or "").lower()
    self_row = next(
        (r for r in rows if (r.get("tokenSymbol") or "").lower() == sym_l
                          or (r.get("name") or "").lower() == name_l),
        None,
    )
    if self_row is None:
        return _unavailable(symbol, f"{symbol} not found in DefiLlama /chains")
    self_tvl = self_row["tvl_usd"]
    if not self_tvl:  # 0 or None — DefiLlama lists this entity but it has no TVL
        return _unavailable(symbol, f"{symbol} matches a DefiLlama entry with TVL=0")

    # Top 5 peers by TVL, excluding self
    peers = [r for r in rows if r is not self_row][:5]
    today = _today()
    for p in peers:
        c.execute(
            "INSERT OR REPLACE INTO competitor "
            "(token_symbol, competitor, market_cap_usd, tvl_usd, dau, revenue_30d_usd) "
            "VALUES (?,?,?,?,?,?)",
            (symbol, p["name"], None, _coerce_float(p["tvl_usd"]), None, None),
        )

    # Market share = self TVL / sum of top peers + self
    total = self_tvl + sum(p["tvl_usd"] for p in peers)
    share = (self_tvl / total) if total > 0 else None
    if share is not None:
        c.execute(
            "INSERT OR REPLACE INTO market_share "
            "(token_symbol, snapshot_at, category, share_pct) VALUES (?,?,?,?)",
            (symbol, today, tok.category, share),
        )

    _write_sidecar(symbol, {
        "as_of": today,
        "data_quality": "GOOD",
        "self": {"name": self_row["name"], "tvl_usd": self_tvl},
        "peers": [{"name": p["name"], "tvl_usd": p["tvl_usd"]} for p in peers],
        "market_share_pct": share,
        "notes": "API-fetched (no LLM research): DefiLlama /chains",
        "sources": ["https://api.llama.fi/chains"],
    })
    c.commit()
    return {"ok": True, "data_quality": "GOOD"}


def _collect_protocol(symbol: str, tok, c: sqlite3.Connection, cat: str) -> dict:
    dl_cat = _DEFILLAMA_CATEGORY_MAP.get(cat)
    if not dl_cat:
        return _unavailable(symbol, f"no DefiLlama category mapping for '{cat}'")
    rows = protocols(category=dl_cat)
    if rows is None:
        return _unavailable(symbol, f"DefiLlama /protocols returned an error")
    if not rows:
        return _unavailable(symbol, f"DefiLlama /protocols returned no entries for category '{dl_cat}'")

    sym_l = symbol.lower()
    slug_l = (tok.defillama_protocol or "").lower()
    self_row = next(
        (r for r in rows
         if (r.get("slug") or "").lower() == slug_l
            or (r.get("symbol") or "").lower() == sym_l),
        None,
    )
    if self_row is None:
        return _unavailable(symbol, f"{symbol} not found in DefiLlama /protocols (cat={dl_cat})")
    self_tvl = self_row["tvl_usd"]
    if not self_tvl:  # 0 or None — DefiLlama lists this entity but it has no TVL
        return _unavailable(symbol, f"{symbol} matches a DefiLlama entry with TVL=0")

    peers = [r for r in rows if r is not self_row][:5]
    today = _today()
    for p in peers:
        c.execute(
            "INSERT OR REPLACE INTO competitor "
            "(token_symbol, competitor, market_cap_usd, tvl_usd, dau, revenue_30d_usd) "
            "VALUES (?,?,?,?,?,?)",
            (symbol, p["name"],
             _coerce_float(p.get("mcap_usd")),
             _coerce_float(p["tvl_usd"]), None, None),
        )

    total = self_tvl + sum(p["tvl_usd"] for p in peers)
    share = (self_tvl / total) if total > 0 else None
    if share is not None:
        c.execute(
            "INSERT OR REPLACE INTO market_share "
            "(token_symbol, snapshot_at, category, share_pct) VALUES (?,?,?,?)",
            (symbol, today, dl_cat, share),
        )

    _write_sidecar(symbol, {
        "as_of": today,
        "data_quality": "GOOD",
        "self": {"name": self_row["name"], "slug": self_row["slug"],
                 "tvl_usd": self_tvl, "mcap_usd": self_row.get("mcap_usd")},
        "peers": [{"name": p["name"], "slug": p["slug"],
                   "tvl_usd": p["tvl_usd"], "mcap_usd": p.get("mcap_usd")} for p in peers],
        "market_share_pct": share,
        "notes": f"API-fetched (no LLM research): DefiLlama /protocols (category={dl_cat})",
        "sources": [f"https://api.llama.fi/protocols (filtered by category={dl_cat})"],
    })
    c.commit()
    return {"ok": True, "data_quality": "GOOD"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("symbols", nargs="*")
    args = p.parse_args(argv); syms = [s.upper() for s in (args.symbols or tokens.all_symbols())]
    for s in syms:
        try: print(json.dumps({s: collect_one(s)}, indent=2, default=str))
        except KeyError as e: print(f"SKIP {s}: {e}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
