"""
Agent 4 collector — on-chain activity, capital flows, holder cohorts.

Free sources:
  • Dune Analytics (some queries free)  — DAU/MAU, retention, tx counts
  • DefiLlama active users page         — chain-level DAU
  • Glassnode free studio (limited)     — LTH supply
  • Project block explorers             — top holders (chain-dependent)
  • Coinglass public                    — derivatives positioning

For free-tier collection we lean on Sonnet research() to synthesize
publicly available dashboards (Dune Spellbook public queries, DefiLlama
charts, etc.) into structured rows.
"""
from __future__ import annotations

import argparse, datetime as dt, json, pathlib, sqlite3, sys, textwrap
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.llm_client import research_json                        # noqa: E402

# Shared numeric/string normalizers — keep collect's direct DB writes from
# ever poisoning REAL columns with strings like "NOT_AVAILABLE_FREE_TIER".
from shared.db_helpers import (                                    # noqa: E402
    coerce_float as _coerce_float,
    coerce_int as _coerce_int,
    deep_merge_sidecar as _deep_merge_sidecar,
    normalize_pct as _normalize_pct,
    normalize_smart as _normalize_smart,
    upsert_note as _upsert_note_generic,
)


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="onchain_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "onchain.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"

_PROMPT = textwrap.dedent("""\
    Research on-chain activity and capital flow for {name} ({symbol}) and
    return a strict JSON object (NOT a code fence — just the JSON).

    Sources you can use (any combination, free):
      • CoinGecko, DefiLlama, Etherscan / project explorer (top holders)
      • Dune Analytics public dashboards
      • Glassnode free studio
      • Coinglass public
      • Reputable news / project blog posts and quarterly reviews
        (e.g. CCIP volume reports, ETF inflow filings, Amundi launches)
      • On-chain data summarized by Nansen / Arkham research blogs
    Synthesizing recent quoted figures from such posts IS allowed and
    expected. Cite the URL in "sources". Do NOT fabricate.

    REQUIRED JSON SHAPE (every numeric is either a real number or null —
    NEVER a string sentinel like "N/A" or "NOT_AVAILABLE_FREE_TIER"):
    {{
      "activity": {{"dau": <int|null>, "wau": <int|null>, "mau": <int|null>,
                    "dau_mau_ratio": <num|null>, "daily_tx_count": <int|null>,
                    "new_addresses_7d": <int|null>, "as_of": "YYYY-MM-DD"}},
      "exchange_flows": [
        {{"date": "YYYY-MM-DD",
          "inflow_usd": <num|null>, "outflow_usd": <num|null>, "net_usd": <num|null>}}
      ],
      "holder_cohort": {{"lth_supply_pct": <decimal 0..1 | null>,
                         "sth_supply_pct": <decimal 0..1 | null>,
                         "smart_money_stance": "ACCUMULATING|DISTRIBUTING|NEUTRAL|UNKNOWN"}},
      "wash_trade_concerns": "<one sentence on whether volume looks organic>",
      "rationale": "<2-4 sentences with concrete numbers and dates>",
      "data_quality": "GOOD|PARTIAL|UNAVAILABLE",
      "sources": ["<url>", ...]
    }}

    Rules:
      • If a value isn't available even after you check the sources above,
        set it to null. Do NOT use string placeholders.
      • Even when raw DAU/MAU is paywalled, you can still report flows,
        LTH%, and rationale from public articles. Use them.
      • For privacy chains (XMR): holder_cohort and exchange_flows are
        UNAVAILABLE by design. Set their numeric fields to null,
        smart_money_stance to "UNKNOWN", and explain in rationale.
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
    data = research_json(_PROMPT.format(name=tok.name, symbol=symbol))
    if not data:
        return {"skipped": True}
    sidecar = SIDECAR_DIR / symbol / "onchain_research.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # Merge with any existing sidecar so a sparse fresh response can't clobber
    # richer prior data. New non-null values still win on overlapping fields.
    existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    data = _deep_merge_sidecar(existing, data)
    sidecar.write_text(json.dumps(data, indent=2))

    # 1. activity_metric — coerce every numeric so string sentinels become None.
    a = data.get("activity") or {}
    if a:
        c.execute(
            "INSERT OR REPLACE INTO activity_metric "
            "(token_symbol, snapshot_at, dau, wau, mau, dau_mau_ratio, "
            " daily_tx_count, new_addresses_7d) VALUES (?,?,?,?,?,?,?,?)",
            (
                symbol,
                a.get("as_of") or _now()[:10],
                _coerce_int(a.get("dau")),
                _coerce_int(a.get("wau")),
                _coerce_int(a.get("mau")),
                _normalize_pct(a.get("dau_mau_ratio")),
                _coerce_int(a.get("daily_tx_count")),
                _coerce_int(a.get("new_addresses_7d")),
            ),
        )

    # 2. exchange_flow — coerce each row's USD fields.
    for f in data.get("exchange_flows", []) or []:
        date = f.get("date") or _now()[:10]
        inflow = _coerce_float(f.get("inflow_usd"))
        outflow = _coerce_float(f.get("outflow_usd"))
        net = _coerce_float(f.get("net_usd"))
        if net is None and inflow is not None and outflow is not None:
            net = inflow - outflow
        # Skip if all-null — no point poisoning the table with empty rows.
        if any(v is not None for v in (inflow, outflow, net)):
            c.execute(
                "INSERT OR REPLACE INTO exchange_flow "
                "(token_symbol, date, inflow_usd, outflow_usd, net_usd) "
                "VALUES (?,?,?,?,?)",
                (symbol, date, inflow, outflow, net),
            )

    # 3. holder_cohort — pct fields normalized to 0..1, stance whitelisted.
    h = data.get("holder_cohort") or {}
    if h:
        c.execute(
            "INSERT OR REPLACE INTO holder_cohort "
            "(token_symbol, snapshot_at, lth_supply_pct, sth_supply_pct, "
            " smart_money_stance) VALUES (?,?,?,?,?)",
            (
                symbol,
                _now(),
                _normalize_pct(h.get("lth_supply_pct")),
                _normalize_pct(h.get("sth_supply_pct")),
                _normalize_smart(h.get("smart_money_stance")),
            ),
        )

    # 4. research notes — idempotent on (symbol, topic, body).
    inserted_notes = 0
    if data.get("rationale"):
        if _upsert_note(c, symbol=symbol, topic="summary",
                        body=data["rationale"], sources=data.get("sources")):
            inserted_notes += 1
    if data.get("wash_trade_concerns"):
        if _upsert_note(c, symbol=symbol, topic="wash_trade",
                        body=data["wash_trade_concerns"], sources=None):
            inserted_notes += 1

    c.commit()
    return {"ok": True, "data_quality": data.get("data_quality"),
            "notes_added": inserted_notes}


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
