"""FRED (Federal Reserve Economic Data) free API client.

Free tier: requires a free API key from
https://fredaccount.stlouisfed.org/apikey. Set as env var FRED_API_KEY.
Without a key, all client functions return None (caller decides what to do).

Series used:
  DFF    — Effective Federal Funds Rate (daily)
  WM2NS  — M2 Money Stock, weekly, not seasonally adjusted (for YoY %)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

BASE = "https://api.stlouisfed.org/fred/series/observations"
_LOG = logging.getLogger(__name__)


def _api_key() -> Optional[str]:
    return (os.getenv("FRED_API_KEY") or "").strip() or None


def latest_observation(series_id: str, *, limit: int = 1) -> Optional[dict]:
    """Return the most recent non-placeholder observation for a FRED series.

    Returns a dict with `{"date": "YYYY-MM-DD", "value": float}`, or None if
    no API key is configured, the API call fails, or no valid observations
    are returned. FRED uses '.' as a placeholder for missing values; those
    are skipped automatically.
    """
    key = _api_key()
    if not key:
        _LOG.warning("FRED_API_KEY not set; macro fields requiring FRED will be null")
        return None
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    try:
        r = requests.get(BASE, params=params, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        _LOG.warning("FRED request failed for %s: %s", series_id, e)
        return None
    obs = r.json().get("observations", [])
    for o in obs:
        if o.get("value") not in (None, "", "."):
            try:
                return {"date": o["date"], "value": float(o["value"])}
            except (TypeError, ValueError):
                continue
    return None


def fed_funds_rate() -> Optional[dict]:
    """Most recent Effective Federal Funds Rate (DFF series)."""
    return latest_observation("DFF")


def m2_yoy_pct() -> Optional[dict]:
    """Year-over-year % change in M2 (WM2NS series).

    Fetches the 60 most recent weekly observations, computes the YoY ratio
    using the latest observation vs. the observation closest to one year
    prior. Returns `{"date": "...", "value": float}` or None.
    """
    key = _api_key()
    if not key:
        _LOG.warning("FRED_API_KEY not set; macro fields requiring FRED will be null")
        return None
    params = {
        "series_id": "WM2NS",
        "api_key": key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 60,
    }
    try:
        r = requests.get(BASE, params=params, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        _LOG.warning("FRED request for M2 failed: %s", e)
        return None
    obs = [o for o in r.json().get("observations", [])
           if o.get("value") not in (None, "", ".")]
    if len(obs) < 52:  # need ~1y of weekly data
        return None
    try:
        latest = obs[0]
        year_ago = obs[51]  # 52 weeks back, 0-indexed
        latest_v = float(latest["value"])
        year_ago_v = float(year_ago["value"])
    except (KeyError, TypeError, ValueError):
        return None
    if year_ago_v <= 0:
        return None
    yoy = (latest_v / year_ago_v - 1.0) * 100.0
    return {"date": latest["date"], "value": round(yoy, 2)}
