"""
One-shot seed ingest for Agent 3 — populates security.db from the JSON
sidecars written by the Sonnet research subagents.

Why a separate ingester (rather than re-using collect.py): the seed data
sidecars are looser than collect.py's contract — Sonnet sometimes used
'audit_history' instead of 'audits', 'historical_exploits' instead of
'exploit_history', etc. This script is tolerant of those variations and
maps them all to the canonical SQLite schema. After it runs once, normal
collect.py runs will keep things fresh on the strict schema.

Run:
    python -m agents.03_security.ingest_seed_data
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
import sys
from typing import Any, Iterable, Optional

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens                                          # noqa: E402
from shared.db_helpers import upsert_unique_row as _upsert_unique_row  # noqa: E402

AGENT_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = AGENT_DIR / "data"
DB_PATH = DATA_DIR / "security.db"
SIDECAR_DIR = DATA_DIR / "sidecars"
SCHEMA_PATH = AGENT_DIR / "schema.sql"


# ─── Tolerant accessors ─────────────────────────────────────────────────

def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _coerce_int(x: Any) -> Optional[int]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    if isinstance(x, str):
        s = x.replace(",", "").strip()
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


def _coerce_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    if isinstance(x, str):
        s = x.replace(",", "").replace("$", "").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _normalize_date(x: Any) -> Optional[str]:
    """Accept 'YYYY-MM-DD', 'May 2017', 'Q3 2020', etc. Return ISO when possible."""
    if not x:
        return None
    if not isinstance(x, str):
        return str(x)
    s = x.strip()
    # already ISO
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    # 'Month YYYY' → first of that month (best-effort)
    try:
        return dt.datetime.strptime(s, "%B %Y").date().isoformat()
    except ValueError:
        pass
    try:
        return dt.datetime.strptime(s, "%b %Y").date().isoformat()
    except ValueError:
        pass
    # leave as-is; SQLite accepts arbitrary text in TEXT columns
    return s


def _data_quality_str(x: Any) -> str:
    """Sonnet sometimes emitted a dict for data_quality. Reduce to a label."""
    if isinstance(x, str):
        return x.upper()[:32]
    if isinstance(x, dict):
        # Try to find a confidence/quality scalar inside
        for k in ("confidence", "quality", "completeness", "level"):
            if k in x and isinstance(x[k], str):
                return x[k].upper()[:32]
        return "PARTIAL"
    return "UNKNOWN"


def _normalize_severity(x: Any) -> str:
    """Accept 'minor'/'Minor'/'major'/etc. Default to 'moderate'."""
    if not isinstance(x, str):
        return "moderate"
    s = x.strip().lower()
    if s in {"minor", "low"}:
        return "minor"
    if s in {"moderate", "medium", "med"}:
        return "moderate"
    if s in {"major", "high"}:
        return "major"
    if s in {"catastrophic", "critical"}:
        return "catastrophic"
    return "moderate"


# ─── Per-section extraction (tolerant to alias keys) ────────────────────

def _audits_iter(raw: dict) -> Iterable[dict]:
    """Yield audit dicts regardless of which key Sonnet chose."""
    for key in ("audits", "audit_history", "audit_reports", "audits_list"):
        v = raw.get(key)
        if isinstance(v, list):
            yield from v
            return  # only one source — don't double-count


def _exploits_iter(raw: dict) -> Iterable[dict]:
    for key in ("exploit_history", "historical_exploits", "exploits", "incidents"):
        v = raw.get(key)
        if isinstance(v, list):
            yield from v
            return


def _code_health(raw: dict) -> dict:
    """
    Synthesize a code_health record from whichever shape Sonnet used.

    Possible top-level keys we draw from:
      - code_health (canonical)
      - repositories (list — pick first repo entry)
      - governance_and_upgrades (for upgrade_mechanism + multisig)
      - upgrade_mechanism (top-level dict on some agents like NMR)
      - bug_bounty (top-level on most variants)
    """
    base = raw.get("code_health") or raw.get("codebase_health") or {}
    if not isinstance(base, dict):
        base = {}
    base = dict(base)  # local copy

    # repositories[] — take the first entry as primary if no canonical url
    repos = raw.get("repositories")
    if isinstance(repos, list) and repos and not base.get("primary_repo_url"):
        first = repos[0] if isinstance(repos[0], dict) else {}
        base.setdefault("primary_repo_url", first.get("url") or first.get("name"))

    # governance_and_upgrades — synthesize upgrade + multisig fields
    gov = raw.get("governance_and_upgrades") or raw.get("governance") or {}
    if isinstance(gov, dict):
        if not base.get("upgrade_mechanism"):
            base["upgrade_mechanism"] = gov.get("model") or gov.get("upgrade_mechanism")
        details = gov.get("details") if isinstance(gov.get("details"), dict) else {}
        guardian = details.get("guardian_multisig") if details else None
        if isinstance(guardian, str) and "of" in guardian.lower():
            # crude parse: "2-of-8" or "2 of 8"
            import re
            m = re.search(r"(\d+)\s*[-]?\s*of\s*[-]?\s*(\d+)", guardian, re.I)
            if m:
                base.setdefault("multisig_threshold", int(m.group(1)))
                base.setdefault("multisig_signers", int(m.group(2)))

    # top-level upgrade_mechanism dict (NMR-style)
    top_um = raw.get("upgrade_mechanism")
    if not base.get("upgrade_mechanism") and top_um:
        if isinstance(top_um, dict):
            base["upgrade_mechanism"] = top_um.get("type") or top_um.get("model") or "documented"
        else:
            base["upgrade_mechanism"] = str(top_um)[:200]

    # bug_bounty top-level
    bb = raw.get("bug_bounty") or {}
    if isinstance(bb, dict):
        if not base.get("bug_bounty_max_usd"):
            base["bug_bounty_max_usd"] = (
                bb.get("max_reward_usd") or bb.get("max_payout_usd") or
                bb.get("bug_bounty_max_usd") or bb.get("max_bounty_usd")
            )
        if not base.get("bug_bounty_platform"):
            base["bug_bounty_platform"] = (
                bb.get("platform") or bb.get("bug_bounty_platform") or
                ("Immunefi" if bb.get("immunefi_listed") else None)
            )

    # treasury_security may carry multisig info if not parsed above
    ts = raw.get("treasury_security") or {}
    if isinstance(ts, dict) and not base.get("multisig_signers"):
        ts_signers = ts.get("signers")
        if isinstance(ts_signers, str):
            import re
            m = re.search(r"(\d+)\s*[-]?\s*of\s*[-]?\s*(\d+)", ts_signers)
            if m:
                base["multisig_threshold"] = int(m.group(1))
                base["multisig_signers"] = int(m.group(2))

    return base


def _dependencies_iter(raw: dict) -> Iterable[dict]:
    """
    Yield dependency dicts. Tolerant to:
      - list[dict] (canonical)
      - dict[str, str] (NMR-style: {blockchain: 'Ethereum', ...})
    """
    for key in ("dependencies", "external_dependencies", "deps"):
        v = raw.get(key)
        if isinstance(v, list):
            yield from v
            return
        if isinstance(v, dict):
            for k, val in v.items():
                # skip null-like values
                if val is None:
                    continue
                if isinstance(val, str) and val.lower() in {"none", "n/a", "not applicable"}:
                    continue
                yield {
                    "dep_type": k,
                    "provider": val if isinstance(val, str) else json.dumps(val),
                    "risk_level": "low" if k in {"blockchain"} else "medium",
                    "notes": "",
                }
            return


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
    sidecar = SIDECAR_DIR / symbol / "security_research.json"
    if not sidecar.exists():
        return {"skipped": True, "reason": "no security_research.json"}
    raw = json.loads(sidecar.read_text())
    out = {"symbol": symbol, "data_quality": _data_quality_str(raw.get("data_quality"))}

    # 1. audits — dedupe on (symbol, auditor, audit_date)
    n_aud = 0
    for a in _audits_iter(raw):
        try:
            auditor = str(_first(a, "auditor", "firm", "auditor_name") or "Unknown")
            audit_date = _normalize_date(_first(a, "audit_date", "date", "completion_date"))
            inserted = _upsert_unique_row(
                c, table="audit",
                match_cols={
                    "token_symbol": symbol,
                    "auditor": auditor,
                    "audit_date": audit_date,
                },
                insert_cols={
                    "token_symbol": symbol,
                    "auditor": auditor,
                    "audit_date": audit_date,
                    "scope": str(_first(a, "scope", "what_was_audited") or ""),
                    "severity_high": _coerce_int(_first(a, "severity_high", "high", "high_count")) or 0,
                    "severity_med": _coerce_int(_first(a, "severity_med", "medium", "med", "medium_count")) or 0,
                    "severity_low": _coerce_int(_first(a, "severity_low", "low", "low_count")) or 0,
                    "summary": str(_first(a, "summary", "findings_summary", "notes") or "")[:1000],
                },
            )
            if inserted:
                n_aud += 1
        except Exception as e:                                  # noqa: BLE001
            out.setdefault("audit_errors", []).append(str(e))
    out["audits_loaded"] = n_aud

    # 2. exploits — dedupe on (symbol, incident_date, description)
    n_exp = 0
    for e in _exploits_iter(raw):
        try:
            inc_date = _normalize_date(_first(e, "incident_date", "date", "occurred_at"))
            desc = str(_first(e, "description", "name", "details", "summary") or "")[:2000]
            if not inc_date or not desc:
                continue
            inserted = _upsert_unique_row(
                c, table="exploit_history",
                match_cols={
                    "token_symbol": symbol,
                    "incident_date": inc_date,
                    "description": desc,
                },
                insert_cols={
                    "token_symbol": symbol,
                    "incident_date": inc_date,
                    "severity": _normalize_severity(_first(e, "severity", "impact")),
                    "description": desc,
                    "funds_lost_usd": _coerce_float(_first(e, "funds_lost_usd", "loss_usd", "amount_lost_usd")),
                    "post_mortem_url": _first(e, "post_mortem_url", "url", "report_url"),
                },
            )
            if inserted:
                n_exp += 1
        except Exception as ex:                                 # noqa: BLE001
            out.setdefault("exploit_errors", []).append(str(ex))
    out["exploits_loaded"] = n_exp

    # 3. code_health
    ch = _code_health(raw)
    if isinstance(ch, dict) and ch:
        # Sometimes ch is a dict-of-repos rather than a single record. Flatten.
        if "primary_repo_url" not in ch and len(ch) and isinstance(next(iter(ch.values())), dict):
            # take the first repo entry
            ch = next(iter(ch.values()))
        try:
            c.execute(
                "INSERT OR REPLACE INTO code_health "
                "(token_symbol, primary_repo_url, contributors_count, weekly_commits_avg, "
                " last_commit_date, upgrade_mechanism, multisig_signers, multisig_threshold, "
                " bug_bounty_max_usd, bug_bounty_platform) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    _first(ch, "primary_repo_url", "repo_url", "github_url", "github"),
                    _coerce_int(_first(ch, "contributors_count", "contributors", "contributor_count")),
                    _coerce_float(_first(ch, "weekly_commits_avg", "commits_per_week", "weekly_commits")),
                    _normalize_date(_first(ch, "last_commit_date", "last_commit")),
                    str(_first(ch, "upgrade_mechanism", "upgradeability", "upgrade_pattern") or "")[:200],
                    _coerce_int(_first(ch, "multisig_signers", "signers", "multisig_total")),
                    _coerce_int(_first(ch, "multisig_threshold", "threshold", "multisig_required")),
                    _coerce_float(_first(raw.get("bug_bounty") or ch, "bug_bounty_max_usd", "max_payout_usd", "max_bounty_usd")),
                    _first(raw.get("bug_bounty") or ch, "bug_bounty_platform", "platform", "bounty_platform"),
                ),
            )
            out["code_health"] = "ok"
        except Exception as e:                                  # noqa: BLE001
            out["code_health_error"] = str(e)

    # 4. dependencies
    n_dep = 0
    for d in _dependencies_iter(raw):
        try:
            c.execute(
                "INSERT OR REPLACE INTO dependency "
                "(token_symbol, dep_type, provider, risk_level, notes) VALUES (?,?,?,?,?)",
                (
                    symbol,
                    str(_first(d, "dep_type", "type", "category") or "other"),
                    str(_first(d, "provider", "name", "service") or "Unknown"),
                    str(_first(d, "risk_level", "risk", "severity") or "medium").lower(),
                    str(_first(d, "notes", "description", "rationale") or "")[:500],
                ),
            )
            n_dep += 1
        except Exception as e:                                  # noqa: BLE001
            out.setdefault("dep_errors", []).append(str(e))
    out["deps_loaded"] = n_dep

    c.commit()
    return out


def main() -> int:
    c = _conn()
    print(f"Ingesting from {SIDECAR_DIR} → {DB_PATH}\n")
    for sym in tokens.all_symbols():
        try:
            r = ingest_one(c, sym)
            print(f"  {sym}: {r}")
        except Exception as e:                                  # noqa: BLE001
            print(f"  {sym}: ERROR — {type(e).__name__}: {e}")

    print("\nDB sanity check:")
    for q, label in [
        ("SELECT COUNT(*) FROM audit", "audits"),
        ("SELECT COUNT(*) FROM exploit_history", "exploit incidents"),
        ("SELECT COUNT(*) FROM code_health", "code_health rows"),
        ("SELECT COUNT(*) FROM dependency", "dependencies"),
    ]:
        n = c.execute(q).fetchone()[0]
        print(f"  {label:>20}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
