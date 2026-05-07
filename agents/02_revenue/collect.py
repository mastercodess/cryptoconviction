"""
Agent 2 collector — protocol revenue + TVL + valuation multiples.

Free sources used:
  • DefiLlama /protocol/{slug}              — TVL history, fees, revenue
  • DefiLlama /summary/fees, /summary/revenue
  • Sonnet research()                        — protocol revenue interpretation,
                                                real-yield vs emission breakdown,
                                                peer comparisons

Tokens without DefiLlama coverage (e.g. LINK, NMR, XMR) are flagged
NOT_COVERED and the agent falls back to research-only narrative.

Run:
    python -m agents.02_revenue.collect              # all
    python -m agents.02_revenue.collect AAVE AERO    # subset

NOTE: This is the Phase-2 implementation skeleton. The DefiLlama egress is
blocked in some sandboxes (use research() fallback there). The Sonnet
research path is wired up; fill in the LLama API call when running on a
machine with open egress.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sqlite3
import sys
import textwrap

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.data_sources import defillama                           # noqa: E402
from shared.llm_client import research_json                         # noqa: E402
from shared.db_helpers import (                                    # noqa: E402
    coerce_float as _coerce_float,
    deep_merge_sidecar as _deep_merge_sidecar,
    upsert_note as _upsert_note_generic,
)


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="revenue_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "revenue.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"


_RESEARCH_PROMPT = textwrap.dedent("""\
    For {name} ({symbol}), pull protocol revenue / fundamentals from FREE
    public sources: DefiLlama public pages, Token Terminal free tier,
    project docs, recent quarterly reviews and reputable news posts that
    quote concrete numbers.

    Return strict JSON (no code fence, no prose around it). Every numeric
    is either a real number or null — NEVER a string sentinel like "N/A",
    "NOT_AVAILABLE", or "—".

    {{
      "annualized_revenue_usd": <number|null>,
      "tvl_usd": <number|null>,
      "p_s_ratio": <number|null>,
      "p_tvl_ratio": <number|null>,
      "real_yield_apr_pct": <number|null>,
      "inflationary_yield_apr_pct": <number|null>,
      "growth_trend": "ACCELERATING|STEADY|DECELERATING|DECLINING",
      "seasonality": "<one sentence — event-driven? consistent?>",
      "peer_comparisons": [
        {{"peer_symbol": "...", "metric": "p_s", "peer_value": <number|null>}}
      ],
      "rationale": "<2-3 sentences with concrete numbers>",
      "data_quality": "GOOD|PARTIAL|UNAVAILABLE",
      "sources": ["<url>", ...]
    }}

    For tokens that aren't protocols (XMR is a chain native, LINK is
    middleware, NMR is a tournament token), set numeric fields to null
    and explain in rationale.
""")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    fresh = not DB_PATH.exists()
    c = sqlite3.connect(DB_PATH)
    if fresh:
        c.executescript(SCHEMA_PATH.read_text())
        c.commit()
    return c


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def collect_one(symbol: str) -> dict:
    tok = tokens.get(symbol)
    c = _conn()
    out: dict = {"symbol": symbol}

    # Try DefiLlama if there's a slug (skipped on egress-blocked sandboxes —
    # the exception fall-through goes to research()).
    llama = {}
    if tok.defillama_protocol:
        try:
            llama = defillama.protocol(tok.defillama_protocol)
            out["defillama"] = "ok"
        except Exception as e:                              # noqa: BLE001
            out["defillama"] = f"error: {e}"

    # Sonnet narrative + multiples (always run, fills gaps).
    # research_json lives in shared/llm_client.py — single source of truth
    # for prompt-to-JSON parsing across every agent.
    data = None
    try:
        data = research_json(_RESEARCH_PROMPT.format(name=tok.name, symbol=symbol))
    except Exception as e:                                  # noqa: BLE001
        out["research_error"] = str(e)
    if data:
        sidecar = SIDECAR_DIR / symbol / "revenue_research.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        # Merge with prior sidecar to preserve richer historical data.
        existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        data = _deep_merge_sidecar(existing, data)
        sidecar.write_text(json.dumps(data, indent=2))
        c.execute(
            "INSERT OR REPLACE INTO revenue_snapshot "
            "(token_symbol, snapshot_at, annualized_revenue_usd, tvl_usd, "
            " p_s_ratio, p_tvl_ratio, real_yield_apr, inflationary_yield_apr, seasonality_note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                symbol, _now(),
                _coerce_float(data.get("annualized_revenue_usd")),
                _coerce_float(data.get("tvl_usd")),
                _coerce_float(data.get("p_s_ratio")),
                _coerce_float(data.get("p_tvl_ratio")),
                _coerce_float(data.get("real_yield_apr_pct")),
                _coerce_float(data.get("inflationary_yield_apr_pct")),
                data.get("seasonality"),
            ),
        )
        for peer in data.get("peer_comparisons", []) or []:
            peer_sym = (peer.get("peer_symbol") or "").strip()
            metric = (peer.get("metric") or "").strip()
            if not peer_sym or not metric:
                continue
            c.execute(
                "INSERT OR REPLACE INTO peer_comparison "
                "(token_symbol, peer_symbol, metric, self_value, peer_value, captured_at) "
                "VALUES (?,?,?,?,?,?)",
                (symbol, peer_sym, metric,
                 None, _coerce_float(peer.get("peer_value")), _now()),
            )
        if data.get("rationale"):
            _upsert_note(
                c, symbol=symbol, topic="revenue_summary",
                body=data["rationale"], sources=data.get("sources"),
            )
        c.commit()
        out["research"] = data.get("data_quality")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*")
    args = p.parse_args(argv)
    syms = [s.upper() for s in (args.symbols or tokens.all_symbols())]
    for s in syms:
        try:
            print(json.dumps({s: collect_one(s)}, indent=2, default=str))
        except KeyError as e:
            print(f"SKIP {s}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
