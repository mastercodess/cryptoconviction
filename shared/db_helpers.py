"""
Shared DB-write helpers used by every agent's collect.py and
ingest_seed_data.py.

Two jobs:

  1. Coerce LLM-returned values to numerics so REAL/INTEGER columns can
     never be poisoned with string sentinels like "N/A",
     "NOT_AVAILABLE_FREE_TIER", or "—". Anything non-numeric becomes None.
  2. Provide an idempotent upsert for the per-agent <name>_research_note
     tables, keyed on (token_symbol, topic, body).

Lifted unchanged from agents/04_onchain/ingest_seed_data.py — it's the
reference implementation that's already proven against the agent-4 sidecars.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from typing import Any, Iterable, Optional


# ─── Deep-walk + numeric coercion ──────────────────────────────────────

def walk(obj: Any, path: tuple = ()) -> Iterable[tuple[tuple, Any]]:
    """Yield every (key-path, value) pair in a nested dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield (path + (k,), v)
            yield from walk(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, path + (f"[{i}]",))


_NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")
_UNAVAILABLE = {
    "UNAVAILABLE", "N/A", "NULL", "UNKNOWN", "NOT_AVAILABLE", "NOT_APPLICABLE",
    "NOT_AVAILABLE_FREE_TIER", "NONE", "—", "-",
}


def parse_numeric(s: str) -> Optional[float]:
    """Pull a numeric out of a string, handling K/M/B suffixes. Returns None
    for explicit unavailability sentinels and for strings with no digits."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s or s.upper() in _UNAVAILABLE:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        n = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    su = s.upper()
    if "K" in su and " K" not in s:
        n *= 1e3
    if re.search(r"\d\s*M\b", s, re.I):
        n *= 1e6
    if re.search(r"\d\s*B\b", s, re.I):
        n *= 1e9
    return n


def coerce_float(x: Any) -> Optional[float]:
    """Anything → float | None. Sentinel strings, dicts with no numeric
    leaf, etc. all become None."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        return parse_numeric(x)
    if isinstance(x, dict):
        for v in x.values():
            f = coerce_float(v)
            if f is not None:
                return f
    return None


def coerce_int(x: Any) -> Optional[int]:
    f = coerce_float(x)
    return int(f) if f is not None else None


def normalize_pct(x: Any) -> Optional[float]:
    """Standardize percentages to the 0..1 range. Inputs > 1 but ≤ 100 are
    treated as percent (75 → 0.75); inputs > 100 are rejected."""
    n = coerce_float(x)
    if n is None:
        return None
    if n > 1:
        if n <= 100:
            return n / 100
        return None
    return n


# ─── Light enum normalizers ────────────────────────────────────────────

_SMART_TAGS = ("ACCUMULATING", "DISTRIBUTING", "NEUTRAL", "UNKNOWN")


def normalize_smart(s: Optional[str]) -> str:
    if not isinstance(s, str):
        return "UNKNOWN"
    su = s.strip().upper()
    for tag in _SMART_TAGS:
        if tag in su:
            return tag
    return "UNKNOWN"


def normalize_grade(s: Optional[str]) -> Optional[str]:
    if not isinstance(s, str):
        return None
    s = s.strip().upper()
    if s and s[0] in ("A", "B", "C", "D", "F"):
        return s[0]
    return None


def normalize_severity(s: Optional[str], default: str = "moderate") -> str:
    """Map free-form severity strings into a small whitelist."""
    if not isinstance(s, str) or not s.strip():
        return default
    su = s.strip().upper()
    for tag in ("CRITICAL", "HIGH", "MAJOR", "SEVERE",
                "MEDIUM", "MODERATE",
                "LOW", "MINOR",
                "INFO", "INFORMATIONAL"):
        if tag in su:
            # collapse synonyms onto the four canonical buckets
            if tag in ("CRITICAL", "HIGH", "MAJOR", "SEVERE"):
                return "high"
            if tag in ("MEDIUM", "MODERATE"):
                return "moderate"
            if tag in ("LOW", "MINOR"):
                return "low"
            return "info"
    return default


# ─── Deep finders (used by ingest paths walking heterogeneous sidecars) ─

def deep_find(raw: dict, candidates: tuple[str, ...]) -> Optional[Any]:
    """Find first numeric value at any candidate-named key."""
    for path, v in walk(raw):
        if path[-1] in candidates:
            f = coerce_float(v) if not isinstance(v, str) else (
                parse_numeric(v) if v else None)
            if f is not None:
                return f
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
    return None


def deep_find_str(raw: dict, candidates: tuple[str, ...]) -> Optional[str]:
    for path, v in walk(raw):
        if path[-1] in candidates and isinstance(v, str) and v.strip():
            return v
    return None


# ─── Idempotent research-note upsert ───────────────────────────────────

def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def upsert_note(
    c: sqlite3.Connection,
    *,
    table: str,
    symbol: str,
    topic: str,
    body: str,
    sources: list | None = None,
    max_body_chars: int = 4000,
) -> bool:
    """Insert into <table> only if (token_symbol, topic, body) doesn't
    already exist. Returns True if a row was inserted.

    Every agent uses a slightly different table name (research_note,
    revenue_research_note, team_research_note, ...), so the table name is
    a parameter rather than hardcoded.
    """
    if not body:
        return False
    body = body.strip()[:max_body_chars]
    if not body:
        return False
    existing = c.execute(
        f"SELECT 1 FROM {table} "
        "WHERE token_symbol=? AND topic=? AND body=? LIMIT 1",
        (symbol, topic, body),
    ).fetchone()
    if existing:
        return False
    c.execute(
        f"INSERT INTO {table} "
        "(token_symbol, topic, body, sources, collected_at) "
        "VALUES (?,?,?,?,?)",
        (symbol, topic, body, json.dumps(sources or []), _now()),
    )
    return True


def deep_merge_sidecar(old: Any, new: Any) -> Any:
    """Deep-merge two JSON-shaped values with 'new wins on non-null' semantics.

    Rules:
      • dicts: recurse on overlapping keys, union of keys preserved
      • lists: prefer the non-empty side; if both non-empty, NEW wins
        (assumed to be the latest ground truth from a fresh research call)
      • scalars: prefer NEW if non-null/non-empty, else keep OLD
      • types mismatch: NEW wins (types may legitimately drift across runs)

    Why this exists: collect.py used to clobber sidecar JSONs every run, so a
    richer Apr 30 research result would get overwritten by a sparser May
    response (e.g. Sonnet refusing to synthesize paywalled metrics today
    even though we already had them). Merging preserves the union of fields
    while still letting fresh non-null values override stale ones.
    """
    # If new is missing/empty/None, keep old.
    if new is None:
        return old
    if isinstance(new, str):
        s = new.strip()
        if not s:
            return old if old is not None else new
        # Treat known unavailability sentinels as "no information" — never
        # let a sentinel overwrite a real value, and never let a sentinel
        # string survive into the merged sidecar.
        if s.upper() in _UNAVAILABLE:
            return old   # may be None — that's correct, sentinel becomes null
    if isinstance(new, (list, tuple)) and len(new) == 0:
        return old if old is not None else new

    # If old is missing, take new wholesale.
    if old is None:
        return new

    # Recurse on dicts.
    if isinstance(old, dict) and isinstance(new, dict):
        merged: dict = dict(old)
        for k, v in new.items():
            if k in merged:
                merged[k] = deep_merge_sidecar(merged[k], v)
            else:
                merged[k] = v
        return merged

    # Lists — both non-empty: NEW wins (it's the freshest ground truth).
    if isinstance(old, (list, tuple)) and isinstance(new, (list, tuple)):
        return list(new) if new else list(old)

    # Scalars / type mismatches: prefer NEW.
    return new


def upsert_unique_row(
    c: sqlite3.Connection,
    *,
    table: str,
    match_cols: dict[str, Any],
    insert_cols: dict[str, Any],
) -> bool:
    """Generic dedupe for tables with autoincrement IDs (e.g. team_member,
    audit, exploit_history). Insert only if no existing row matches
    `match_cols`. Returns True on insert.

    Use match_cols for the uniqueness key (e.g. {'token_symbol':'LINK',
    'auditor':'Trail of Bits', 'audit_date':'2024-06-01'}) and insert_cols
    for the full payload (a superset is fine; ALL of these are inserted).
    """
    if not match_cols:
        raise ValueError("match_cols must be non-empty")
    # Use IS (not =) so NULL match values compare correctly
    # (SQLite: NULL = NULL is NULL, but NULL IS NULL is true).
    where = " AND ".join(f"{k} IS ?" for k in match_cols)
    existing = c.execute(
        f"SELECT 1 FROM {table} WHERE {where} LIMIT 1",
        tuple(match_cols.values()),
    ).fetchone()
    if existing:
        return False
    cols = list(insert_cols.keys())
    placeholders = ",".join(["?"] * len(cols))
    c.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        tuple(insert_cols[k] for k in cols),
    )
    return True
