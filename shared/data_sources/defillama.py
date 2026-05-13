"""
DefiLlama public client. No API key required.

Endpoints used:
  /protocol/{slug}            — TVL, fees, revenue history for a protocol
  /summary/fees/{slug}        — fee/revenue breakdown
  /tvl/{slug}                 — current TVL only
  /chains                     — chain-level TVL list

Docs: https://api-docs.defillama.com/
"""
from __future__ import annotations

import requests

BASE = "https://api.llama.fi"
FEES_BASE = "https://api.llama.fi/summary"


def protocol(slug: str) -> dict:
    r = requests.get(f"{BASE}/protocol/{slug}", timeout=30)
    r.raise_for_status()
    return r.json()


def fees_summary(slug: str) -> dict:
    """Daily/weekly/30d fee + revenue breakdown."""
    r = requests.get(f"{FEES_BASE}/fees/{slug}", timeout=30)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json()


def revenue_summary(slug: str) -> dict:
    r = requests.get(f"{FEES_BASE}/revenue/{slug}", timeout=30)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json()


def current_tvl(slug: str) -> float | None:
    r = requests.get(f"{BASE}/tvl/{slug}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return float(r.json())


def chains() -> list[dict] | None:
    """Return all chains with TVL, sorted descending. None on error.

    Each record: {"name": str, "tvl_usd": float, "tokenSymbol": str,
    "coingecko_id": str | None, "cmc_id": str | None}.
    """
    try:
        r = requests.get(f"{BASE}/chains", timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    out = []
    for c in r.json():
        tvl = c.get("tvl")
        if tvl is None:
            continue
        try:
            tvl_f = float(tvl)
        except (TypeError, ValueError):
            continue
        out.append({
            "name": c.get("name", ""),
            "tvl_usd": tvl_f,
            "tokenSymbol": c.get("tokenSymbol"),
            "coingecko_id": c.get("gecko_id"),
            "cmc_id": c.get("cmcId"),
        })
    out.sort(key=lambda x: x["tvl_usd"], reverse=True)
    return out


# Task 2 will add protocols (a list of protocols across DefiLlama).
# Temporary stub so the Task 1 test file can `from ... import chains, protocols`
# without a collection error. Replace with the real implementation in Task 2.
protocols = None
