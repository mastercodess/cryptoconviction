"""
CoinGecko free-tier client.

Free tier: 30 calls/min, no API key required, but supports CG_API_KEY for
higher limits. Endpoints used:
  /coins/{id}                 — full snapshot (MC, FDV, supply, ATH/ATL, links)
  /coins/{id}/market_chart    — historical price/MC/volume

Docs: https://www.coingecko.com/en/api/documentation
"""
from __future__ import annotations

import os
import time
from typing import Optional

import requests

BASE = "https://api.coingecko.com/api/v3"
PRO_BASE = "https://pro-api.coingecko.com/api/v3"


def _headers() -> dict:
    key = os.getenv("COINGECKO_API_KEY", "").strip()
    return {"x-cg-pro-api-key": key} if key else {}


def _base() -> str:
    return PRO_BASE if os.getenv("COINGECKO_API_KEY") else BASE


def _get(path: str, params: Optional[dict] = None, *, retries: int = 3) -> dict:
    url = f"{_base()}{path}"
    for attempt in range(retries):
        r = requests.get(url, params=params, headers=_headers(), timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
    raise RuntimeError(f"CoinGecko {path}: rate-limited after {retries} retries")


def coin_snapshot(coingecko_id: str) -> dict:
    """Full /coins/{id} response. Includes market_data with MC, FDV, supplies."""
    return _get(
        f"/coins/{coingecko_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "true",
            "sparkline": "false",
        },
    )


def market_chart(coingecko_id: str, days: int = 365) -> dict:
    """Historical prices/MC/volume. Free tier limited to daily granularity past 90d."""
    return _get(
        f"/coins/{coingecko_id}/market_chart",
        params={"vs_currency": "usd", "days": days},
    )
