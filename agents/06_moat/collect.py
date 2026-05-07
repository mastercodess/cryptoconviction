"""Agent 6 collector — competitive position via Sonnet research + Electric Capital data."""
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
    normalize_pct as _normalize_pct,
    upsert_note as _upsert_note_generic,
)


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="moat_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "moat.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"

_PROMPT = textwrap.dedent("""\
    Research competitive moat for {name} ({symbol}, category: {category}) using
    FREE sources: DefiLlama category rankings, Token Terminal free pages,
    Electric Capital developer report (free), project integration pages.

    Return JSON:
    {{
      "competitors": [{{"competitor":"...", "market_cap_usd":<num>, "tvl_usd":<num or null>, "dau":<num or null>, "revenue_30d_usd":<num or null>}}],
      "market_share": [{{"category":"<category>", "share_pct":<decimal 0..1>, "snapshot_at":"YYYY-MM-DD"}}],
      "dev_ecosystem": {{"monthly_active_devs":<int>, "full_time_devs":<int>, "repos_building_on":<int>, "integrations_count":<int>}},
      "switching_cost_analysis": "<one sentence: how easy to switch to a competitor>",
      "category_rank": <int, 1=leader>,
      "regulatory_relative_risk": "LOWER|SIMILAR|HIGHER",
      "rationale": "<2-3 sentences>",
      "data_quality": "GOOD|PARTIAL|UNAVAILABLE",
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


def collect_one(symbol: str) -> dict:
    tok = tokens.get(symbol); c = _conn()
    data = research_json(_PROMPT.format(name=tok.name, symbol=symbol, category=tok.category))
    if not data: return {"skipped": True}
    sidecar = SIDECAR_DIR / symbol / "moat_research.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    data = _deep_merge_sidecar(existing, data)
    sidecar.write_text(json.dumps(data, indent=2))
    for comp in data.get("competitors", []) or []:
        comp_name = (comp.get("competitor") or "").strip()
        if not comp_name:
            continue
        c.execute(
            "INSERT OR REPLACE INTO competitor "
            "(token_symbol, competitor, market_cap_usd, tvl_usd, dau, revenue_30d_usd) "
            "VALUES (?,?,?,?,?,?)",
            (symbol, comp_name,
             _coerce_float(comp.get("market_cap_usd")),
             _coerce_float(comp.get("tvl_usd")),
             _coerce_int(comp.get("dau")),
             _coerce_float(comp.get("revenue_30d_usd"))),
        )
    for ms in data.get("market_share", []) or []:
        cat = (ms.get("category") or "").strip()
        share = _normalize_pct(ms.get("share_pct"))
        if not cat or share is None:
            continue
        c.execute(
            "INSERT OR REPLACE INTO market_share "
            "(token_symbol, snapshot_at, category, share_pct) VALUES (?,?,?,?)",
            (symbol, ms.get("snapshot_at") or _now()[:10], cat, share),
        )
    de = data.get("dev_ecosystem") or {}
    if de:
        c.execute(
            "INSERT OR REPLACE INTO dev_ecosystem "
            "(token_symbol, monthly_active_devs, full_time_devs, "
            " repos_building_on, integrations_count, snapshot_at) "
            "VALUES (?,?,?,?,?,?)",
            (symbol,
             _coerce_int(de.get("monthly_active_devs")),
             _coerce_int(de.get("full_time_devs")),
             _coerce_int(de.get("repos_building_on")),
             _coerce_int(de.get("integrations_count")),
             _now()),
        )
    if data.get("rationale"):
        _upsert_note(c, symbol=symbol, topic="moat_summary",
                     body=data["rationale"], sources=data.get("sources"))
    c.commit()
    return {"ok": True, "data_quality": data.get("data_quality")}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("symbols", nargs="*")
    args = p.parse_args(argv); syms = [s.upper() for s in (args.symbols or tokens.all_symbols())]
    for s in syms:
        try: print(json.dumps({s: collect_one(s)}, indent=2, default=str))
        except KeyError as e: print(f"SKIP {s}: {e}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
