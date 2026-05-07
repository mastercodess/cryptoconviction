"""Agent 7 collector — global macro + per-token cycle metrics.

Free sources:
  • alternative.me Fear & Greed API
  • CoinGecko global endpoint (BTC dom, total MC) — when egress allows
  • TradingView public BTC dominance widget (read via Sonnet)
  • FRED (free) for M2, Fed funds rate
  • Coinglass public tab for funding rates / OI
"""
from __future__ import annotations
import argparse, datetime as dt, json, pathlib, sqlite3, sys, textwrap
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path: sys.path.insert(0, str(_REPO_ROOT))
from shared import tokens
from shared.llm_client import research_json
from shared.db_helpers import (
    coerce_float as _coerce_float,
    coerce_int as _coerce_int,
    deep_merge_sidecar as _deep_merge_sidecar,
    upsert_note as _upsert_note_generic,
)


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="macro_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "macro.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"

_GLOBAL_PROMPT = textwrap.dedent("""\
    Pull the following macro indicators from FREE sources (alternative.me,
    CoinGecko global page, TradingView, FRED, Coinglass public). Return JSON:
    {
      "as_of": "YYYY-MM-DD",
      "btc_price_usd": <num>, "btc_dominance_pct": <num>,
      "total_mc_usd": <num>, "total_mc_ex_btc_usd": <num>,
      "altcoin_season_index": <int 0-100>,
      "fear_greed_index": <int 0-100>,
      "fed_funds_rate": <decimal>,
      "m2_yoy_pct": <decimal>,
      "btc_halving_day": <int — days since 4th halving April 2024>,
      "notes": "<one-paragraph cycle context>",
      "sources": ["..."]
    }
""")

_PER_TOKEN_PROMPT = textwrap.dedent("""\
    For {name} ({symbol}), pull from FREE sources:
    {{
      "funding_rate_8h": <decimal e.g. 0.0001>,
      "open_interest_usd": <num>,
      "btc_correlation_30d": <decimal -1..1>,
      "eth_correlation_30d": <decimal -1..1>,
      "nasdaq_correlation_30d": <decimal -1..1>,
      "rationale": "<one sentence on derivatives positioning>",
      "sources": ["..."]
    }}
""")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True); SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    fresh = not DB_PATH.exists()
    c = sqlite3.connect(DB_PATH)
    if fresh: c.executescript(SCHEMA_PATH.read_text()); c.commit()
    return c


def _now() -> str: return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def collect_global() -> dict:
    c = _conn()
    g = research_json(_GLOBAL_PROMPT)
    if not g: return {"skipped": True}
    g_sidecar = SIDECAR_DIR / "_global" / "macro_global.json"
    g_sidecar.parent.mkdir(parents=True, exist_ok=True)
    g_existing = json.loads(g_sidecar.read_text()) if g_sidecar.exists() else {}
    g = _deep_merge_sidecar(g_existing, g)
    g_sidecar.write_text(json.dumps(g, indent=2))
    c.execute(
        "INSERT OR REPLACE INTO macro_snapshot (snapshot_at, btc_price_usd, "
        "btc_dominance_pct, total_mc_usd, total_mc_ex_btc, altcoin_season_index, "
        "fear_greed_index, fed_funds_rate, m2_yoy_pct, btc_halving_day, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (g.get("as_of") or _now(),
         _coerce_float(g.get("btc_price_usd")),
         _coerce_float(g.get("btc_dominance_pct")),
         _coerce_float(g.get("total_mc_usd")),
         _coerce_float(g.get("total_mc_ex_btc_usd")),
         _coerce_int(g.get("altcoin_season_index")),
         _coerce_int(g.get("fear_greed_index")),
         _coerce_float(g.get("fed_funds_rate")),
         _coerce_float(g.get("m2_yoy_pct")),
         _coerce_int(g.get("btc_halving_day")),
         g.get("notes", "")),
    )
    c.commit(); return {"ok": True}


def collect_one(symbol: str) -> dict:
    tok = tokens.get(symbol); c = _conn()
    d = research_json(_PER_TOKEN_PROMPT.format(name=tok.name, symbol=symbol))
    if not d: return {"skipped": True}
    t_sidecar = SIDECAR_DIR / symbol / "macro_token.json"
    t_sidecar.parent.mkdir(parents=True, exist_ok=True)
    t_existing = json.loads(t_sidecar.read_text()) if t_sidecar.exists() else {}
    d = _deep_merge_sidecar(t_existing, d)
    t_sidecar.write_text(json.dumps(d, indent=2))
    c.execute(
        "INSERT OR REPLACE INTO token_cycle_metric (token_symbol, snapshot_at, "
        "funding_rate_8h, open_interest_usd, btc_correlation_30d, "
        "eth_correlation_30d, nasdaq_correlation_30d) VALUES (?,?,?,?,?,?,?)",
        (symbol, _now(),
         _coerce_float(d.get("funding_rate_8h")),
         _coerce_float(d.get("open_interest_usd")),
         _coerce_float(d.get("btc_correlation_30d")),
         _coerce_float(d.get("eth_correlation_30d")),
         _coerce_float(d.get("nasdaq_correlation_30d"))),
    )
    if d.get("rationale"):
        _upsert_note(c, symbol=symbol, topic="derivatives_summary",
                     body=d["rationale"], sources=d.get("sources"))
    c.commit(); return {"ok": True}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("symbols", nargs="*"); p.add_argument("--global-only", action="store_true")
    args = p.parse_args(argv)
    print(json.dumps({"_global": collect_global()}, indent=2, default=str))
    if args.global_only: return 0
    syms = [s.upper() for s in (args.symbols or tokens.all_symbols())]
    for s in syms:
        try: print(json.dumps({s: collect_one(s)}, indent=2, default=str))
        except KeyError as e: print(f"SKIP {s}: {e}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
