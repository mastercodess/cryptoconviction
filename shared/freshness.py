"""Freshness contract helpers.

The conviction system enforces a hard age limit on agent data via
`config.yaml: red_flags.max_data_age_hours`. This module parses ISO
timestamps from agent outputs and partitions agents into fresh / stale.

Convention: each agent's JSON output carries `data_as_of`, which is the
ISO string of the most recent DB row the agent's analyze step actually
consumed. Null / unparseable / missing = stale (fail-closed).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional


def parse_iso(value: Optional[str]) -> Optional[dt.datetime]:
    """Parse an ISO date or datetime string. Return None on any failure.

    Accepts both 'YYYY-MM-DD' and full ISO 8601 with timezone. Naive
    datetimes are coerced to UTC.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = dt.datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def age_hours(value: Optional[str]) -> float:
    """Hours between `value` and now (UTC). +inf if unparseable / null."""
    parsed = parse_iso(value)
    if parsed is None:
        return math.inf
    now = dt.datetime.now(dt.timezone.utc)
    return (now - parsed).total_seconds() / 3600.0


def is_stale(value: Optional[str], *, max_hours: float) -> bool:
    """Fail-closed: null/unparseable always returns True."""
    return age_hours(value) > max_hours


def classify_agents(
    loaded: dict[str, dict],
    *,
    max_hours: float,
    per_agent_max_hours: Optional[dict[str, float]] = None,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Split loaded agents into (fresh, stale, per_agent_as_of_strings).

    per_agent_as_of_strings carries the raw data_as_of (or "unknown")
    for surfacing in the final verdict / markdown.

    `per_agent_max_hours` lets specific agents override the global
    `max_hours`. Used today by:
      - security: 4320h (6mo) — stamp source is audit_date (an event
        timestamp), not a collection timestamp. See TODO in config.yaml.
      - moat / macro: 168h (7d) — underlying metrics shift on weekly-
        to-monthly cadence; daily re-collection is wasted credits.
    """
    per_agent_max_hours = per_agent_max_hours or {}
    fresh: list[str] = []
    stale: list[str] = []
    per_agent: dict[str, str] = {}
    for agent, output in loaded.items():
        threshold = per_agent_max_hours.get(agent, max_hours)
        value = output.get("data_as_of") if output else None
        per_agent[agent] = value if value else "unknown"
        if is_stale(value, max_hours=threshold):
            stale.append(agent)
        else:
            fresh.append(agent)
    return fresh, stale, per_agent


def stamp_data_as_of(
    raw: dict,
    conn,
    *,
    table: str,
    ts_col: str = "snapshot_at",
    symbol: Optional[str] = None,
) -> None:
    """Populate raw['data_as_of'] from MAX(ts_col) of the agent's primary table.

    No-op if raw already has a non-null data_as_of (preserves anything the
    RLM happy-path injected). If `symbol` is provided, filters by
    token_symbol=?; otherwise queries the table globally (macro-style).

    Mutates `raw` in place; returns None.
    """
    if raw.get("data_as_of"):
        return
    sql = f"SELECT MAX({ts_col}) AS ts FROM {table}"
    params: tuple = ()
    if symbol is not None:
        sql += " WHERE token_symbol=?"
        params = (symbol,)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raw["data_as_of"] = None
        return
    # Support both sqlite3.Row and tuple
    try:
        ts = row["ts"]
    except (KeyError, TypeError, IndexError):
        ts = row[0] if row else None
    raw["data_as_of"] = ts
