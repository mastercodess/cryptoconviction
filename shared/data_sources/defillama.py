"""
DefiLlama public client. No API key required.

Endpoints used:
  /protocol/{slug}            — TVL, fees, revenue history for a protocol
  /summary/fees/{slug}        — fee/revenue breakdown
  /tvl/{slug}                 — current TVL only

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
