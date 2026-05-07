"""Agent 5 collector — team + investor diligence via Sonnet research."""
from __future__ import annotations
import argparse, datetime as dt, json, pathlib, sqlite3, sys, textwrap
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path: sys.path.insert(0, str(_REPO_ROOT))
from shared import tokens
from shared.llm_client import research_json
from shared.db_helpers import (
    coerce_float as _coerce_float,
    deep_merge_sidecar as _deep_merge_sidecar,
    normalize_pct as _normalize_pct,
    normalize_severity as _normalize_severity,
    upsert_note as _upsert_note_generic,
    upsert_unique_row as _upsert_unique_row,
)


def _upsert_note(c, *, symbol, topic, body, sources):
    return _upsert_note_generic(
        c, table="team_research_note",
        symbol=symbol, topic=topic, body=body, sources=sources,
    )

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "team.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"

_PROMPT = textwrap.dedent("""\
    Research the team, investors, and legal posture for {name} ({symbol})
    using FREE public sources: founder LinkedIn, Crunchbase free pages,
    project blog, regulatory filings (SEC EDGAR free search).

    Return JSON:
    {{
      "team_members": [{{"name":"...", "role":"...", "doxxed":<bool>, "linkedin_url":"...", "prior_projects":["..."], "notes":"..."}}],
      "investors": [{{"investor_name":"...", "round":"seed|series_a|private|public", "valuation_usd":<num or null>, "ownership_pct":<decimal or null>, "unlock_status":"fully_vested|cliff_remaining|linear"}}],
      "legal_events": [{{"event_date":"YYYY-MM-DD", "jurisdiction":"...", "description":"...", "severity":"minor|moderate|severe", "source_url":"..."}}],
      "founder_credibility_summary": "<2 sentences>",
      "vc_overhang_summary": "<2 sentences>",
      "alignment_summary": "<are insider and retail incentives aligned?>",
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
    data = research_json(_PROMPT.format(name=tok.name, symbol=symbol))
    if not data: return {"skipped": True}
    sidecar = SIDECAR_DIR / symbol / "team_research.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    data = _deep_merge_sidecar(existing, data)
    sidecar.write_text(json.dumps(data, indent=2))
    # team_member — dedupe on (symbol, name); each person inserted once.
    for m in data.get("team_members", []) or []:
        name = (m.get("name") or "").strip()
        if not name:
            continue
        _upsert_unique_row(
            c, table="team_member",
            match_cols={"token_symbol": symbol, "name": name},
            insert_cols={
                "token_symbol": symbol,
                "name": name,
                "role": m.get("role"),
                "doxxed": int(bool(m.get("doxxed"))),
                "linkedin_url": m.get("linkedin_url"),
                "prior_projects": json.dumps(m.get("prior_projects", [])),
                "notes": m.get("notes", ""),
            },
        )
    # investor — dedupe on (symbol, investor_name, round); coerce numerics.
    for i in data.get("investors", []) or []:
        inv_name = (i.get("investor_name") or "").strip()
        if not inv_name:
            continue
        _upsert_unique_row(
            c, table="investor",
            match_cols={
                "token_symbol": symbol,
                "investor_name": inv_name,
                "round": i.get("round"),
            },
            insert_cols={
                "token_symbol": symbol,
                "investor_name": inv_name,
                "round": i.get("round"),
                "valuation_usd": _coerce_float(i.get("valuation_usd")),
                "ownership_pct": _normalize_pct(i.get("ownership_pct")),
                "unlock_status": i.get("unlock_status"),
            },
        )
    # legal_event — dedupe on (symbol, event_date, description).
    for e in data.get("legal_events", []) or []:
        ev_date = e.get("event_date")
        desc = (e.get("description") or "").strip()
        if not ev_date or not desc:
            continue
        _upsert_unique_row(
            c, table="legal_event",
            match_cols={
                "token_symbol": symbol,
                "event_date": ev_date,
                "description": desc,
            },
            insert_cols={
                "token_symbol": symbol,
                "event_date": ev_date,
                "jurisdiction": e.get("jurisdiction", ""),
                "description": desc,
                "severity": _normalize_severity(e.get("severity"), default="low"),
                "source_url": e.get("source_url"),
            },
        )
    # research notes — idempotent upsert.
    for topic, body in [("founder_credibility", data.get("founder_credibility_summary", "")),
                        ("vc_overhang",         data.get("vc_overhang_summary", "")),
                        ("alignment",           data.get("alignment_summary", ""))]:
        if body:
            _upsert_note(c, symbol=symbol, topic=topic,
                         body=body, sources=data.get("sources", []))
    c.commit()
    return {"ok": True, "data_quality": data.get("data_quality")}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("symbols", nargs="*")
    args = p.parse_args(argv)
    syms = [s.upper() for s in (args.symbols or tokens.all_symbols())]
    for s in syms:
        try: print(json.dumps({s: collect_one(s)}, indent=2, default=str))
        except KeyError as e: print(f"SKIP {s}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
