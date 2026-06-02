"""
Bulk pre-fill manual-research worksheet gaps from existing sidecar data.

The system's agent sidecars already contain numeric fields that map directly
to several worksheet gaps (real yield, exploit history, chain DAU, etc.).
This script copies them into the worksheets' `value` slots with citation
notes pointing back at the source, so the user doesn't have to re-dig the
same data manually.

Only fills gaps where:
  - The worksheet's `value` is currently None (does not overwrite user input)
  - The source sidecar exists and the relevant field is non-null
  - The token's category matches the extractor's scope

Each filled value gets a `notes` block explaining the source so the user
knows what's projected vs. realized vs. proxied. The `retrieved_at` field
is set to today's date.

Run:
  python3 -m scripts.prefill_from_sidecars
  python3 -m scripts.prefill_from_sidecars AAVE LINK   # specific tokens only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any, Callable

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens  # noqa: E402

WORKSHEETS_DIR = _REPO_ROOT / "data" / "manual_research"
SIDECAR_DIRS = {
    "tokenomics": _REPO_ROOT / "agents" / "01_tokenomics" / "data" / "sidecars",
    "revenue": _REPO_ROOT / "agents" / "02_revenue" / "data" / "sidecars",
    "security": _REPO_ROOT / "agents" / "03_security" / "data" / "sidecars",
    "onchain": _REPO_ROOT / "agents" / "04_onchain" / "data" / "sidecars",
    "team": _REPO_ROOT / "agents" / "05_team" / "data" / "sidecars",
    "moat": _REPO_ROOT / "agents" / "06_moat" / "data" / "sidecars",
    "macro": _REPO_ROOT / "agents" / "07_macro" / "data" / "sidecars",
}
SIDECAR_FILES = {
    "tokenomics": "research.json",      # main tokenomics research
    "revenue": "revenue_research.json",
    "security": "security_research.json",
    "onchain": "onchain_research.json",
    "team": "team_research.json",
    "moat": "moat_research.json",
    "macro": "macro_token.json",
}


def load_sidecars(symbol: str) -> dict[str, Any]:
    """Load all available sidecars for a token. Missing ones become empty dicts."""
    out: dict[str, Any] = {}
    for agent, fname in SIDECAR_FILES.items():
        p = SIDECAR_DIRS[agent] / symbol / fname
        if p.exists():
            try:
                out[agent] = json.loads(p.read_text())
            except Exception:                                  # noqa: BLE001
                out[agent] = {}
        else:
            out[agent] = {}
    return out


# ─── Extractors ─────────────────────────────────────────────────────────
# Each entry: (gap_field, allowed_categories_or_None, extractor_fn, note_template)
# extractor_fn returns (value, citation_context_dict) or (None, _)
# allowed_categories=None means apply to all tokens.

def _extract_real_yield_lending(s: dict) -> tuple[Any, dict]:
    rev = s.get("revenue") or {}
    v = rev.get("real_yield_apr_pct")
    return v, {"source": "agents/02_revenue/data/sidecars/<SYM>/revenue_research.json:real_yield_apr_pct"}


def _extract_l1_real_fee_pct(s: dict) -> tuple[Any, dict]:
    """For L1 tokens: fraction of staker yield from real fees vs. inflationary subsidy."""
    rev = s.get("revenue") or {}
    real = rev.get("real_yield_apr_pct")
    infl = rev.get("inflationary_yield_apr_pct")
    if real is None and (infl is None or infl == 0):
        return None, {}
    if real is None and infl is not None and infl > 0:
        # Pure subsidy chain
        return 0.0, {"source": "revenue agent: real_yield_apr_pct=None, inflationary_yield_apr_pct={infl}".format(infl=infl)}
    if infl is None:
        return None, {}
    total = real + infl
    if total == 0:
        return None, {}
    return round(real / total, 3), {
        "source": f"revenue agent: real_yield_apr_pct={real}, inflationary_yield_apr_pct={infl}; ratio={real}/{total}",
    }


def _extract_chain_dau(s: dict) -> tuple[Any, dict]:
    onchain = s.get("onchain") or {}
    if onchain.get("data_quality") in (None, "UNAVAILABLE"):
        return None, {}
    activity = onchain.get("activity") or {}
    dau = activity.get("dau")
    if dau is None:
        return None, {}
    return int(dau), {
        "source": "agents/04_onchain/data/sidecars/<SYM>/onchain_research.json:activity.dau",
        "caveat": "This is CHAIN-LEVEL DAU, not top-application DAU. Treat as upper bound on the gap's actual measurement.",
    }


def _extract_bad_debt_history(s: dict) -> tuple[Any, dict]:
    sec = s.get("security") or {}
    history = sec.get("exploit_history") or []
    if not history:
        return None, {}
    total = sum((h.get("funds_lost_usd") or 0) for h in history)
    return int(total), {
        "source": f"agents/03_security exploit_history sum across {len(history)} events",
        "caveat": "Historical aggregate (since protocol launch), not necessarily currently outstanding. Verify remediation status in governance forum.",
    }


def _extract_audit_count(s: dict) -> tuple[Any, dict]:
    sec = s.get("security") or {}
    audits = sec.get("audits") or []
    if not audits:
        return None, {}
    return len(audits), {
        "source": f"agents/03_security audits[] count",
    }


# Add more extractors here as you identify reliable mappings.

EXTRACTORS: list[tuple[str, set[str] | None, Callable, str]] = [
    (
        "real_yield_trailing_90d_pct",
        {"defi-lending"},
        _extract_real_yield_lending,
        "Pre-filled from revenue agent (projected real_yield_apr_pct). "
        "Source: {source}. This is the agent's PROJECTED real yield from "
        "current revenue × staker share, not realized buyback execution. "
        "Verify with quarterly buyback execution report for the realized figure."
    ),
    (
        "real_fee_revenue_vs_subsidy_pct",
        {"l1-general", "l2"},
        _extract_l1_real_fee_pct,
        "Pre-filled by computing fee share from revenue agent. Source: {source}. "
        "A value of 0 means staker rewards are entirely inflationary subsidy "
        "(typical for early L1s). Verify the underlying inflation_yield_apr_pct "
        "is genuine subsidy and not double-counted."
    ),
    (
        "top_application_dau",
        {"l1-general", "l2"},
        _extract_chain_dau,
        "Pre-filled with CHAIN-LEVEL DAU from on-chain agent. Source: {source}. "
        "NOTE: {caveat} The actual top-app DAU is usually a fraction of chain DAU; "
        "look up the #1 app on DappRadar/TokenTerminal for the precise gap value."
    ),
    (
        "total_bad_debt_outstanding_usd",
        {"defi-lending"},
        _extract_bad_debt_history,
        "Pre-filled with historical exploit/bad-debt aggregate. Source: {source}. "
        "CAVEAT: {caveat}"
    ),
]


def prefill_token(symbol: str) -> dict[str, Any]:
    """Pre-fill one token's worksheet. Returns a summary dict."""
    symbol = symbol.upper()
    worksheet_path = WORKSHEETS_DIR / f"{symbol}.json"
    if not worksheet_path.exists():
        return {"symbol": symbol, "skipped": "no worksheet"}

    ws = json.loads(worksheet_path.read_text())
    category = ws.get("category")
    sidecars = load_sidecars(symbol)

    today = dt.date.today().isoformat()
    filled: list[str] = []
    skipped: list[str] = []

    gap_by_field = {g["field"]: g for g in ws.get("schema_overlay_gaps", [])}

    for field, allowed_cats, fn, note_template in EXTRACTORS:
        if field not in gap_by_field:
            continue
        gap = gap_by_field[field]
        if gap.get("value") is not None:
            skipped.append(f"{field} (already filled)")
            continue
        if allowed_cats and category not in allowed_cats:
            continue

        try:
            value, ctx = fn(sidecars)
        except Exception as e:                                 # noqa: BLE001
            skipped.append(f"{field} (extractor error: {e})")
            continue

        if value is None:
            continue

        # Substitute citation context into the note template
        note = note_template
        for k, v in ctx.items():
            note = note.replace("{" + k + "}", str(v))
        # Replace <SYM> placeholder in source paths
        note = note.replace("<SYM>", symbol)

        gap["value"] = value
        gap["notes"] = note
        gap["retrieved_at"] = today
        filled.append(field)

    if filled:
        worksheet_path.write_text(json.dumps(ws, indent=2))

    return {
        "symbol": symbol,
        "category": category,
        "filled_count": len(filled),
        "filled_fields": filled,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("symbols", nargs="*", help="Specific symbols (default: all)")
    args = p.parse_args(argv)

    syms = [s.upper() for s in args.symbols] or tokens.all_symbols()
    summaries = []
    for s in syms:
        summaries.append(prefill_token(s))

    print(f"Pre-fill complete. Summary:")
    total_filled = sum(s.get("filled_count", 0) for s in summaries)
    print(f"  Total fields filled: {total_filled} across {len(syms)} tokens\n")
    for s in summaries:
        if s.get("filled_count"):
            print(f"  {s['symbol']:6} ({s['category']:18}) +{s['filled_count']}: "
                  f"{', '.join(s['filled_fields'])}")
    no_fills = [s["symbol"] for s in summaries if not s.get("filled_count") and "skipped" not in s]
    if no_fills:
        print(f"\n  No pre-fills available: {', '.join(no_fills)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
