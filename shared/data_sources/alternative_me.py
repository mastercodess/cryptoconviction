"""alternative.me Fear & Greed Index client.

Free, no API key. Endpoint: https://api.alternative.me/fng/
Returns the most recent daily reading (0-100; 0=extreme fear, 100=extreme greed).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import requests

ENDPOINT = "https://api.alternative.me/fng/"
_LOG = logging.getLogger(__name__)


def fear_greed_index() -> Optional[dict]:
    """Most recent Fear & Greed Index reading.

    Returns `{"value": int 0-100, "classification": str, "date": "YYYY-MM-DD"}`
    or None on any error.
    """
    try:
        r = requests.get(ENDPOINT, params={"limit": 1}, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        _LOG.warning("alternative.me request failed: %s", e)
        return None
    data = r.json().get("data") or []
    if not data:
        return None
    row = data[0]
    try:
        value = int(row["value"])
        ts = int(row["timestamp"])
        date = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "value": value,
        "classification": row.get("value_classification", ""),
        "date": date,
    }
