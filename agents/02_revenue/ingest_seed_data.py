"""
One-shot seed ingest for Agent 2 — populates revenue.db from the JSON
sidecars written by the Sonnet research subagents.

The Sonnet sidecars are LOOSE — every token used a different nesting
shape (LINK puts revenue under `valuation_analysis.price_to_sales_analysis`,
ENA under `revenue_metrics`, AVNT under `revenue_and_tokenomics`, etc).
Rather than write a per-token mapper, this ingester does a recursive
key-name search to find canonical fields wherever they live.

Run:
    python -m agents.02_revenue.ingest_seed_data
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sqlite3
import sys
from typing import Any, Iterable, Optional

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.db_helpers import upsert_note as _upsert_note_generic   # noqa: E402


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="revenue_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = AGENT_DIR / "data"
DB_PATH = DATA_DIR / "revenue.db"
SIDECAR_DIR = DATA_DIR / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"


# ─── Generic deep accessors ─────────────────────────────────────────────

def _walk(obj: Any, path: tuple = ()) -> Iterable[tuple[tuple, Any]]:
    """Recursively yield (path, value) for every dict node in the tree."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield (path + (k,), v)
            yield from _walk(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, path + (f"[{i}]",))


def _deep_find(raw: dict, candidates: tuple[str, ...], *,
               want: str = "scalar") -> Optional[Any]:
    """
    Find the first occurrence of any candidate key whose value is the right
    shape. `want` is one of:
      'scalar' — int / float / numeric string
      'string' — plain string
      'list'   — non-empty list

    For 'scalar' lookups, when the canonical key holds a DICT (e.g. ONDO's
    `annualized_revenue: {protocol_total_usd: 648000, ...}`), we recurse
    into that dict looking for likely-total-style keys before giving up.
    """
    TOTAL_LIKE_KEYS = ("total_usd", "protocol_total_usd", "annual_usd",
                       "annualized_usd", "total", "usd")
    for path, v in _walk(raw):
        if path[-1] not in candidates:
            continue
        if want == "scalar":
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
            if isinstance(v, str):
                f = _parse_numeric(v)
                if f is not None:
                    return f
            if isinstance(v, dict):
                # Canonical key holds a dict — pluck a total-like sub-key.
                for k in TOTAL_LIKE_KEYS:
                    if k in v and isinstance(v[k], (int, float)) and not isinstance(v[k], bool):
                        return v[k]
        elif want == "string":
            if isinstance(v, str) and v.strip():
                return v
        elif want == "list":
            if isinstance(v, list) and v:
                return v
    # Second pass: look for *_millions or *_billions keys whose names contain
    # any candidate stem. Multiply accordingly.
    stems = tuple(c.replace("_usd", "").replace("_apr_pct", "").replace("_pct", "")
                  for c in candidates)
    for path, v in _walk(raw):
        k = path[-1].lower()
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if k.endswith(("_millions", "_million")) and any(s in k for s in stems):
            return float(v) * 1e6
        if k.endswith(("_billions", "_billion")) and any(s in k for s in stems):
            return float(v) * 1e9
    return None


_NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")


def _parse_numeric(s: str) -> Optional[float]:
    """Pull the first number out of a string. '4.32%' → 4.32; '$15M' → 15000000."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s or s.upper() in {"UNAVAILABLE", "N/A", "NULL"}:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        n = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    # scale by suffix
    su = s.upper()
    if "K" in su and "$" in su or " K" in su:
        n *= 1e3
    if "M" in su:
        n *= 1e6
    if "B" in su:
        n *= 1e9
    return n


def _coerce_float(x: Any) -> Optional[float]:
    """As above but also handles dict-shaped yield fields (AERO style)."""
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        return _parse_numeric(x)
    if isinstance(x, dict):
        # Take the first numeric leaf — AERO puts {veAERO_locked_4yr: 35, ...}
        for v in x.values():
            f = _coerce_float(v)
            if f is not None:
                return f
    if isinstance(x, list):
        for v in x:
            f = _coerce_float(v)
            if f is not None:
                return f
    return None


def _coerce_yield_pct(x: Any, *, source_key: str = "") -> Optional[float]:
    """
    Yield fields come in two shapes that share a 0..1 numeric range and are
    impossible to disambiguate without context:
      - 0.0432 (decimal fraction representing 4.32%)
      - 0.85   (percent literal representing 0.85%)

    We use the source field NAME as a tiebreaker: keys ending in `_pct` /
    `_percent` are assumed percent; keys ending in `_apr` (no pct) are
    assumed decimal. If we can't tell, we leave the value alone and trust
    the agent's analyzer to interpret.
    """
    n = _coerce_float(x)
    if n is None:
        return None
    is_pct_field = source_key.lower().endswith(("_pct", "_percent", "_percentage"))
    is_decimal_field = source_key.lower().endswith("_apr") and not is_pct_field
    if 0 < n < 1 and is_decimal_field:
        return n * 100
    return n


# ─── Field-resolution candidates per concept ────────────────────────────

# Most-specific to least-specific key names.
KEYS_REVENUE = (
    "annualized_revenue_usd", "annualized_protocol_revenue_usd",
    "annualized_run_rate_usd", "annualized_run_rate", "annualized_revenue",
    "on_chain_revenue_annualized_usd", "protocol_revenue_usd_annualized",
    "annual_revenue_usd",
)
KEYS_FEES = (
    "annualized_fees_usd", "annual_fees_usd", "annualized_fees",
    "yearly_fees_usd",
)
KEYS_TVL = (
    "tvl_usd", "current_tvl_usd", "tvl", "total_value_locked_usd",
    "tvl_usdc_vault_usd",
)
KEYS_PS = ("p_s_ratio", "p_s", "estimated_ps_ratio", "ps_ratio", "price_to_sales")
KEYS_PTVL = ("p_tvl_ratio", "p_tvl", "ptvl_ratio", "price_to_tvl",
             "market_cap_to_tvl")
KEYS_REAL_YIELD = ("real_yield_apr_pct", "real_yield_apr", "real_yield_pct")
KEYS_INFL_YIELD = (
    "inflationary_yield_apr_pct", "inflationary_yield_apr",
    "emission_yield_apr_pct", "subsidy_apr_pct",
)
KEYS_GROWTH = ("growth_trend", "growth_classification", "trend")
KEYS_SEASONALITY = ("seasonality", "seasonality_note", "revenue_seasonality")
KEYS_DQ = ("data_quality", "data_quality_notes")
KEYS_RATIONALE = ("rationale", "summary", "key_findings_summary",
                  "summary_3_line_funding_dependency_risk")
KEYS_AS_OF = ("as_of_date", "as_of", "research_date", "date_collected")


def _normalize_growth(x: Any) -> Optional[str]:
    if not isinstance(x, str):
        return None
    s = x.strip().upper()
    for tag in ("ACCELERATING", "STEADY", "DECELERATING", "DECLINING"):
        if tag in s:
            return tag
    return None


def _normalize_dq(x: Any) -> str:
    if isinstance(x, str):
        return x.upper()[:32]
    if isinstance(x, dict):
        return "PARTIAL"
    return "UNKNOWN"


# ─── DB plumbing ────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fresh = not DB_PATH.exists()
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    if fresh:
        c.executescript(SCHEMA_PATH.read_text())
        c.commit()
    return c


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ─── Per-token ingest ───────────────────────────────────────────────────

def ingest_one(c: sqlite3.Connection, symbol: str) -> dict:
    sidecar = SIDECAR_DIR / symbol / "revenue_research.json"
    if not sidecar.exists():
        return {"skipped": True, "reason": "no revenue_research.json"}
    raw = json.loads(sidecar.read_text())

    rev_usd = _coerce_float(_deep_find(raw, KEYS_REVENUE, want="scalar"))
    fees_usd = _coerce_float(_deep_find(raw, KEYS_FEES, want="scalar"))
    tvl_usd = _coerce_float(_deep_find(raw, KEYS_TVL, want="scalar"))
    ps = _coerce_float(_deep_find(raw, KEYS_PS, want="scalar"))
    ptvl = _coerce_float(_deep_find(raw, KEYS_PTVL, want="scalar"))
    # Yield fields: pass the source key so coerce can apply the
    # decimal-vs-percent heuristic correctly.
    real_y_raw = _deep_find(raw, KEYS_REAL_YIELD, want="scalar")
    infl_y_raw = _deep_find(raw, KEYS_INFL_YIELD, want="scalar")
    # Find the actual key name where the value lives (for unit hint)
    real_key = next((p[-1] for p, v in _walk(raw)
                     if p[-1] in KEYS_REAL_YIELD and isinstance(v, (int, float))), "")
    infl_key = next((p[-1] for p, v in _walk(raw)
                     if p[-1] in KEYS_INFL_YIELD and isinstance(v, (int, float))), "")
    real_y = _coerce_yield_pct(real_y_raw, source_key=real_key)
    infl_y = _coerce_yield_pct(infl_y_raw, source_key=infl_key)
    growth = _normalize_growth(_deep_find(raw, KEYS_GROWTH, want="string"))
    seas = _deep_find(raw, KEYS_SEASONALITY, want="string")
    dq = _normalize_dq(_deep_find(raw, KEYS_DQ, want="string"))
    as_of = _deep_find(raw, KEYS_AS_OF, want="string") or _now()[:10]

    c.execute(
        "INSERT OR REPLACE INTO revenue_snapshot "
        "(token_symbol, snapshot_at, daily_fees_usd, daily_revenue_usd, "
        " annualized_revenue_usd, tvl_usd, p_s_ratio, p_tvl_ratio, "
        " real_yield_apr, inflationary_yield_apr, seasonality_note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            symbol, as_of,
            (fees_usd / 365) if fees_usd else None,
            (rev_usd / 365) if rev_usd else None,
            rev_usd, tvl_usd, ps, ptvl,
            real_y, infl_y,
            (seas or "")[:500],
        ),
    )

    # Peers: deep-find a list whose elements look like peer rows.
    peers = []
    for path, v in _walk(raw):
        if path[-1] in ("peer_comparisons", "peer_comparison", "peers", "comparable_peers"):
            if isinstance(v, list) and v and isinstance(v[0], dict):
                peers = v
                break
            if isinstance(v, dict) and "peers" in v and isinstance(v["peers"], list):
                peers = v["peers"]
                break
    n_peers = 0
    for p in peers:
        if not isinstance(p, dict):
            continue
        peer_sym = (p.get("peer_symbol") or p.get("symbol") or
                    p.get("name") or p.get("ticker") or "")[:32]
        if not peer_sym:
            continue

        # Two shapes:
        #  (A) competitor row: {peer_symbol, p_s_ratio: X, p_tvl_ratio: Y, tvl_usd: Z}
        #  (B) AAVE-style:     {peer_symbol, metric: "p_s", self_value: X, peer_value: Y}
        if "metric" in p and "peer_value" in p:
            metric = str(p["metric"])[:32]
            self_val = _coerce_float(p.get("self_value"))
            peer_val = _coerce_float(p.get("peer_value"))
            if peer_val is not None or self_val is not None:
                c.execute(
                    "INSERT OR REPLACE INTO peer_comparison "
                    "(token_symbol, peer_symbol, metric, self_value, peer_value, captured_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (symbol, peer_sym, metric, self_val, peer_val, _now()),
                )
                n_peers += 1
                continue

        # Style A — emit one row per metric present
        for metric, keys in [
            ("p_s",     ("p_s_ratio", "p_s", "ps_ratio", "price_to_sales")),
            ("p_tvl",   ("p_tvl_ratio", "p_tvl", "market_cap_to_tvl")),
            ("rev_usd", KEYS_REVENUE),
            ("tvl_usd", KEYS_TVL),
            ("mc_usd",  ("market_cap_usd", "mc_usd", "market_cap")),
        ]:
            for k in keys:
                if k in p and p[k] is not None:
                    val = _coerce_float(p[k])
                    if val is not None:
                        c.execute(
                            "INSERT OR REPLACE INTO peer_comparison "
                            "(token_symbol, peer_symbol, metric, self_value, peer_value, captured_at) "
                            "VALUES (?,?,?,?,?,?)",
                            (symbol, peer_sym, metric, None, val, _now()),
                        )
                        n_peers += 1
                        break

    # Rationale → research_note (idempotent on (symbol, topic, body))
    rat = _deep_find(raw, KEYS_RATIONALE, want="string")
    if rat:
        _upsert_note(
            c, symbol=symbol, topic="summary", body=str(rat),
            sources=raw.get("sources") or raw.get("data_sources") or [],
        )

    c.commit()
    return {
        "symbol": symbol, "data_quality": dq,
        "rev": rev_usd, "tvl": tvl_usd, "p_s": ps, "p_tvl": ptvl,
        "real_y%": real_y, "infl_y%": infl_y, "growth": growth,
        "peers_loaded": n_peers,
    }


def main() -> int:
    c = _conn()
    print(f"Ingesting from {SIDECAR_DIR} → {DB_PATH}\n")
    for sym in tokens.all_symbols():
        try:
            r = ingest_one(c, sym)
            print(f"  {sym}: {r}")
        except Exception as e:                              # noqa: BLE001
            print(f"  {sym}: ERROR — {type(e).__name__}: {e}")
    print("\nDB sanity check:")
    for q, label in [
        ("SELECT COUNT(*) FROM revenue_snapshot", "snapshots"),
        ("SELECT COUNT(*) FROM peer_comparison", "peer rows"),
        ("SELECT COUNT(*) FROM revenue_research_note", "research notes"),
        ("SELECT COUNT(*) FROM revenue_snapshot WHERE annualized_revenue_usd IS NOT NULL", "with revenue"),
        ("SELECT COUNT(*) FROM revenue_snapshot WHERE p_s_ratio IS NOT NULL", "with P/S"),
        ("SELECT COUNT(*) FROM revenue_snapshot WHERE tvl_usd IS NOT NULL", "with TVL"),
    ]:
        n = c.execute(q).fetchone()[0]
        print(f"  {label:>20}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
