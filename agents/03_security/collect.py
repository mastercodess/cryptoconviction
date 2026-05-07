"""
Agent 3 collector — audit history, code health, exploit incidents.

Free sources:
  • GitHub API (public)               — repo metadata, contributor counts, commits
  • Project audit-report repos / docs  — locate audit PDFs
  • Rekt.news / CryptoExploitDB        — incident history
  • DeFi Safety / DefiLlama hacks DB   — incident enumeration
  • Sonnet research()                  — synthesize the above

PDFs from auditors are saved to data/sidecars/{SYMBOL}/audits/. The RLM
agent uses these as variables (Path.read_text() or pdftotext) and
delegates dense reading to sub_lm() rather than dumping into root context.

This is a Phase-2 stub — fill in the GitHub API client when running on a
machine with open egress.
"""
from __future__ import annotations

import argparse, datetime as dt, json, pathlib, sqlite3, sys, textwrap
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.llm_client import research_json                        # noqa: E402
from shared.db_helpers import (                                    # noqa: E402
    coerce_float as _coerce_float,
    coerce_int as _coerce_int,
    deep_merge_sidecar as _deep_merge_sidecar,
    normalize_severity as _normalize_severity,
    upsert_unique_row as _upsert_unique_row,
)

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = AGENT_DIR / "data" / "security.db"
SIDECAR_DIR = AGENT_DIR / "data" / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"

_PROMPT = textwrap.dedent("""\
    Research the security posture of {name} ({symbol}) using FREE sources:
    project docs, audit-report repos on GitHub, Rekt.news, DefiLlama hacks
    page, Immunefi public listings.

    Return JSON:
    {{
      "audits": [
        {{"auditor": "...", "audit_date": "YYYY-MM-DD", "scope": "...",
          "severity_high": <int>, "severity_med": <int>, "severity_low": <int>,
          "summary": "<1 sentence>", "url": "..."}}
      ],
      "exploit_history": [
        {{"incident_date": "YYYY-MM-DD", "severity": "minor|moderate|major|catastrophic",
          "description": "...", "funds_lost_usd": <num or null>, "post_mortem_url": "..."}}
      ],
      "code_health": {{
        "primary_repo_url": "...",
        "contributors_count": <int>,
        "weekly_commits_avg": <num>,
        "last_commit_date": "YYYY-MM-DD",
        "upgrade_mechanism": "immutable|proxy_timelock|proxy_no_timelock|multisig_only",
        "multisig_signers": <int or null>,
        "multisig_threshold": <int or null>,
        "bug_bounty_max_usd": <num or null>,
        "bug_bounty_platform": "..."
      }},
      "dependencies": [
        {{"dep_type": "oracle|bridge|l1|l2_sequencer", "provider": "...",
          "risk_level": "low|medium|high", "notes": "..."}}
      ],
      "rationale": "<2-3 sentences on overall security posture>",
      "data_quality": "GOOD|PARTIAL|UNAVAILABLE",
      "sources": ["..."]
    }}
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


def collect_one(symbol: str) -> dict:
    tok = tokens.get(symbol)
    c = _conn()
    data = research_json(_PROMPT.format(name=tok.name, symbol=symbol))
    if not data:
        return {"skipped": True}
    sidecar = SIDECAR_DIR / symbol / "security_research.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    data = _deep_merge_sidecar(existing, data)
    sidecar.write_text(json.dumps(data, indent=2))
    # audits — dedupe on (symbol, auditor, audit_date) since the table has
    # an autoincrement id and would otherwise duplicate on every re-collect.
    for a in data.get("audits", []) or []:
        auditor = (a.get("auditor") or "").strip()
        audit_date = (a.get("audit_date") or "").strip()
        if not auditor:
            continue
        _upsert_unique_row(
            c, table="audit",
            match_cols={
                "token_symbol": symbol,
                "auditor": auditor,
                "audit_date": audit_date or None,
            },
            insert_cols={
                "token_symbol": symbol,
                "auditor": auditor,
                "audit_date": audit_date or None,
                "scope": a.get("scope", ""),
                "severity_high": _coerce_int(a.get("severity_high")) or 0,
                "severity_med": _coerce_int(a.get("severity_med")) or 0,
                "severity_low": _coerce_int(a.get("severity_low")) or 0,
                "summary": a.get("summary", ""),
            },
        )

    # exploit_history — dedupe on (symbol, incident_date, description).
    for e in data.get("exploit_history", []) or []:
        incident_date = e.get("incident_date")
        description = (e.get("description") or "").strip()
        if not incident_date or not description:
            continue
        _upsert_unique_row(
            c, table="exploit_history",
            match_cols={
                "token_symbol": symbol,
                "incident_date": incident_date,
                "description": description,
            },
            insert_cols={
                "token_symbol": symbol,
                "incident_date": incident_date,
                "severity": _normalize_severity(e.get("severity")),
                "description": description,
                "funds_lost_usd": _coerce_float(e.get("funds_lost_usd")),
                "post_mortem_url": e.get("post_mortem_url"),
            },
        )

    ch = data.get("code_health") or {}
    if ch:
        c.execute(
            "INSERT OR REPLACE INTO code_health (token_symbol, primary_repo_url, contributors_count, "
            "weekly_commits_avg, last_commit_date, upgrade_mechanism, multisig_signers, multisig_threshold, "
            "bug_bounty_max_usd, bug_bounty_platform) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (symbol, ch.get("primary_repo_url"),
             _coerce_int(ch.get("contributors_count")),
             _coerce_float(ch.get("weekly_commits_avg")),
             ch.get("last_commit_date"),
             ch.get("upgrade_mechanism"),
             _coerce_int(ch.get("multisig_signers")),
             _coerce_int(ch.get("multisig_threshold")),
             _coerce_float(ch.get("bug_bounty_max_usd")),
             ch.get("bug_bounty_platform")),
        )

    for d in data.get("dependencies", []) or []:
        dep_type = (d.get("dep_type") or "").strip()
        provider = (d.get("provider") or "").strip()
        if not dep_type or not provider:
            continue
        c.execute(
            "INSERT OR REPLACE INTO dependency (token_symbol, dep_type, provider, risk_level, notes) VALUES (?,?,?,?,?)",
            (symbol, dep_type, provider,
             d.get("risk_level", "medium"), d.get("notes", "")),
        )
    c.commit()
    return {"ok": True, "data_quality": data.get("data_quality")}


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
