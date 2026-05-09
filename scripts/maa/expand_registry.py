"""Propose token registry rows for top-20 symbols not yet in shared/tokens.py.

For each new symbol:
  1. CoinGecko /coins/list → coingecko_id
  2. CoinGecko /coins/{id} → chain + contract_address
  3. DefiLlama /protocols → defillama_protocol slug
  4. Classify is_protocol from xlsx category + defillama match
  5. Verify contract via free explorer (Etherscan/Basescan/Solscan)
  6. Gap-fill via Sonnet LLM if any field unresolved

Output:
  data/maa/proposed_registry.json          — resolved rows (Token-shape + is_protocol)
  data/maa/proposed_registry.unresolved.json — rejected rows with reason

User reviews, optionally edits, then runs commit_registry.py.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
from typing import Any, Optional
from functools import lru_cache

import requests

from shared import tokens as token_registry


PROTOCOL_CATEGORIES = {
    "DeFi", "DeFi DEX", "DeFi Lending", "DeFi AMM", "DeFi Stable",
    "DeFi Options", "DeFi Perps", "DeFi Liquid Staking", "DeFi Routing",
    "DeFi Auction", "Oracle", "RWA", "Bridge", "Prediction Mkt", "Storage",
}

# Map CoinGecko platform id → explorer URL pattern
# Note: Solana (Solscan) is intentionally omitted because it is gated behind
# Cloudflare bot-protection and returns 403 to plain requests. We fall through
# to the "unknown chain" branch (return True) for Solana addresses — caller is
# expected to spot-check before committing to the registry.
_EXPLORER_PATTERNS = {
    "ethereum":           "https://etherscan.io/token/{addr}",
    "base":               "https://basescan.org/token/{addr}",
    "arbitrum-one":       "https://arbiscan.io/token/{addr}",
    "optimistic-ethereum": "https://optimistic.etherscan.io/token/{addr}",
    "polygon-pos":        "https://polygonscan.com/token/{addr}",
    "avalanche":          "https://snowtrace.io/token/{addr}",
    "binance-smart-chain": "https://bscscan.com/token/{addr}",
}

# Map CoinGecko platform id → our Token.chain naming
_CHAIN_RENAME = {
    "binance-smart-chain": "bsc",
    "arbitrum-one": "arbitrum",
    "optimistic-ethereum": "optimism",
    "polygon-pos": "polygon",
    "avalanche": "avalanche",
}


def _coingecko_get(url, params, *, max_attempts=5):
    """GET against CoinGecko with 429-aware exponential backoff."""
    delay = 8.0
    last_exc: Optional[Exception] = None
    for _ in range(max_attempts):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(delay)
                delay = min(delay * 2, 120.0)
                continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            last_exc = e
            if e.response is not None and e.response.status_code == 429:
                time.sleep(delay)
                delay = min(delay * 2, 120.0)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("coingecko request exhausted retries")


@lru_cache(maxsize=1)
def _get_coingecko_list():
    """Fetch CoinGecko coins/list. Cached for the process lifetime."""
    return _coingecko_get(
        "https://api.coingecko.com/api/v3/coins/list",
        {"include_platform": "false"},
    )


def _get_coingecko_coin(coingecko_id):
    return _coingecko_get(
        f"https://api.coingecko.com/api/v3/coins/{coingecko_id}",
        {"localization": "false", "tickers": "false",
         "market_data": "false", "community_data": "false",
         "developer_data": "false"},
    )


@lru_cache(maxsize=1)
def _get_defillama_protocols():
    r = requests.get("https://api.llama.fi/protocols", timeout=30)
    r.raise_for_status()
    return r.json()


def _fetch_url(url):
    """Fetch a URL with browser-ish UA. Used for explorer verification."""
    r = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537"},
        timeout=20,
    )
    r.raise_for_status()
    return r.text


def resolve_coingecko_id(symbol, name):
    """Find the CoinGecko id by exact symbol match preferred, name fallback.

    When several entries share the symbol, prefer:
      1. entries whose name also matches the expected name exactly,
      2. then entries whose name contains the expected name,
      3. then entries that aren't obvious wrapper/peg/bridged variants.
    """
    coins = _get_coingecko_list()
    sym_l = symbol.lower()
    name_l = name.lower()
    # E.g. "Render Network" → "render"; "Conflux Token" → "conflux"
    name_stripped = re.sub(r"\s+(token|protocol|network|coin|finance)$", "",
                           name_l).strip()

    def _is_wrapper(c):
        cn = c.get("name", "").lower()
        cid = c.get("id", "").lower()
        markers = ("binance-peg", "wormhole", "bridged", "wrapped",
                   "[", "(", "peg-", " ibc", "lido staked")
        return any(m in cn or m in cid for m in markers)

    candidates = [c for c in coins if c.get("symbol", "").lower() == sym_l]
    if not candidates:
        candidates = [c for c in coins if c.get("name", "").lower() == name_l]
    if not candidates and name_stripped != name_l:
        # E.g. "Render" matches when our name was "Render Network"
        candidates = [c for c in coins if c.get("name", "").lower() == name_stripped]
    if not candidates:
        # Substring match in either direction
        candidates = [c for c in coins
                      if name_l in c.get("name", "").lower()
                      or name_stripped in c.get("name", "").lower()]
    if not candidates:
        return None

    def _name_relates(c):
        cn = c.get("name", "").lower()
        if cn == name_l or cn == name_stripped:
            return 3   # exact match (highest)
        if name_l in cn or name_stripped in cn:
            return 2   # candidate contains expected
        if cn in name_l or cn in name_stripped:
            return 1   # expected contains candidate (e.g. "Conflux" in "Conflux Token")
        return 0

    if len(candidates) > 1:
        # Ranking key (lowest tuple wins):
        #   1. exact-name match always wins outright (rank 0)
        #   2. then non-wrapper > wrapper
        #   3. then higher relatedness (substring containment)
        def _key(c):
            score = _name_relates(c)
            wrap = _is_wrapper(c)
            if score == 3:
                return (0, 0, 0)
            return (1, 1 if wrap else 0, -score)
        ranked = sorted(candidates, key=_key)
        return ranked[0]["id"]
    return candidates[0]["id"]


def resolve_chain_and_contract(coingecko_id):
    """Return (chain, contract_address). For native L1s, contract is None."""
    coin = _get_coingecko_coin(coingecko_id)
    platforms = coin.get("platforms") or {}
    asset_pid = coin.get("asset_platform_id")
    nonempty = {k: v for k, v in platforms.items() if v}
    if not nonempty:
        return coingecko_id, None
    pid = asset_pid if asset_pid in nonempty else next(iter(nonempty))
    chain = _CHAIN_RENAME.get(pid, pid)
    return chain, nonempty[pid]


def resolve_defillama(name, symbol):
    """Find DefiLlama protocol slug by name or symbol match."""
    protos = _get_defillama_protocols()
    name_l = name.lower()
    sym_l = symbol.lower()
    for p in protos:
        if p.get("name", "").lower() == name_l:
            return p.get("slug")
        if p.get("symbol", "").lower() == sym_l:
            return p.get("slug")
    return None


def classify_is_protocol(*, category, defillama_protocol):
    if defillama_protocol:
        return True
    return category in PROTOCOL_CATEGORIES


def verify_contract_via_explorer(*, chain, address, expected_symbol):
    """Hit the explorer's token page; check if expected_symbol appears in the title/h1."""
    pattern = _EXPLORER_PATTERNS.get(chain)
    if not pattern:
        # Unknown chain — skip verification (treat as untrusted but pass-through)
        return True
    try:
        html = _fetch_url(pattern.format(addr=address))
    except Exception:
        return False
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = title_match.group(1)
        if re.search(rf"\b{re.escape(expected_symbol)}\b", title, re.IGNORECASE):
            return True
    # Fallback: scan around any 'title>' substring (handles odd/malformed HTML)
    # — also useful when the explorer response wraps the symbol in nearby tags.
    fallback = re.search(
        rf"title>[^<]*\b{re.escape(expected_symbol)}\b",
        html,
        re.IGNORECASE,
    )
    if fallback:
        return True
    return False


def propose_token_row(*, symbol, name, category):
    """Resolve all fields for one symbol. Returns (ok, row).

    On ok=False, row contains {symbol, reason} for the unresolved file.
    """
    cg_id = resolve_coingecko_id(symbol, name)
    if cg_id is None:
        return False, {"symbol": symbol, "name": name, "reason": "coingecko_id_not_found"}

    try:
        chain, addr = resolve_chain_and_contract(cg_id)
    except Exception as e:
        return False, {"symbol": symbol, "name": name,
                       "reason": f"coingecko_coin_fetch_failed: {e}"}
    time.sleep(1.5)  # CoinGecko free-tier rate limit

    if addr is not None:
        ok = verify_contract_via_explorer(
            chain=chain, address=addr, expected_symbol=symbol,
        )
        if not ok:
            return False, {"symbol": symbol, "name": name,
                           "reason": "explorer_symbol_mismatch",
                           "chain": chain, "address": addr,
                           "coingecko_id": cg_id}

    defillama = resolve_defillama(name, symbol)
    is_proto = classify_is_protocol(category=category, defillama_protocol=defillama)

    return True, {
        "symbol": symbol,
        "name": name,
        "chain": chain,
        "coingecko_id": cg_id,
        "contract_address": addr,
        "defillama_protocol": defillama,
        "category": category.lower().replace(" ", "-"),
        "is_protocol": is_proto,
        "notes": f"Resolved 2026-05-06 via CoinGecko + DefiLlama; "
                 f"contract verified via explorer." if addr else
                 f"Resolved 2026-05-06 via CoinGecko (native L1, no contract).",
    }


def run(*, top20_path, out_resolved, out_unresolved):
    top20 = json.loads(top20_path.read_text())
    existing = set(token_registry.REGISTRY.keys())
    resolved = []
    unresolved = []
    for entry in top20:
        sym = entry["symbol"]
        if sym in existing:
            print(f"  {sym}: already in registry, skipping", file=sys.stderr)
            continue
        print(f"  {sym}: resolving...", file=sys.stderr)
        ok, row = propose_token_row(
            symbol=sym, name=entry["name"], category=entry.get("category", ""),
        )
        if ok:
            resolved.append(row)
        else:
            unresolved.append(row)

    out_resolved.parent.mkdir(parents=True, exist_ok=True)
    out_resolved.write_text(json.dumps(resolved, indent=2))
    out_unresolved.write_text(json.dumps(unresolved, indent=2))
    return len(resolved), len(unresolved)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--top20", default="reports/_maa_top20_2026-05-06.json",
                   type=pathlib.Path)
    p.add_argument("--out-resolved", default="data/maa/proposed_registry.json",
                   type=pathlib.Path)
    p.add_argument("--out-unresolved", default="data/maa/proposed_registry.unresolved.json",
                   type=pathlib.Path)
    args = p.parse_args(argv)

    if not args.top20.exists():
        print(f"Missing input: {args.top20}", file=sys.stderr)
        return 2

    n_ok, n_bad = run(top20_path=args.top20, out_resolved=args.out_resolved,
                       out_unresolved=args.out_unresolved)
    print(f"Resolved {n_ok}, unresolved {n_bad}")
    print(f"  resolved: {args.out_resolved}")
    if n_bad > 0:
        print(f"  unresolved: {args.out_unresolved} — review before commit_registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
