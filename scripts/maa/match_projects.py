"""Match each post to a row in MasterAnanda_Watchlist.xlsx.

Match priority:
  1. Parenthesized symbol — title contains "(SYM)"
  2. Bullet/dash symbol  — title starts "SYM ·" or "SYM —"
  3. Trading pair        — title contains "SYMUSDT"
  4. Bare symbol token   — first standalone all-caps ticker in the title that
                           exists in the xlsx symbol universe (handles
                           multi-symbol prose like "BTC vs ETH")
  5. Fuzzy project name  — RapidFuzz score ≥ 90
  6. LLM disambiguation  — Sonnet sees title + xlsx universe, returns symbol or null

Multi-symbol posts (e.g. "BTC vs ETH") match the FIRST symbol detected.
This is a deliberate simplification per the design spec.

Usage:
    python -m scripts.maa.match_projects \
        --in data/maa/posts_filtered.jsonl \
        --xlsx MasterAnanda_Watchlist.xlsx \
        --out data/maa/posts_matched.jsonl \
        --unmatched data/maa/posts_unmatched.jsonl
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys
from typing import Callable, Optional

import openpyxl
from rapidfuzz import process, fuzz


@dataclasses.dataclass(frozen=True)
class XlsxRow:
    rank: int
    name: str
    symbol: str
    trading_pair: str
    category: str
    asset_type: str
    sentiment: str
    signal: str
    tradingview_symbol: str


@dataclasses.dataclass
class XlsxIndex:
    by_symbol: dict
    by_name: dict
    by_pair: dict
    name_choices: list


_PAREN_SYM = re.compile(r"\(([A-Z0-9]{2,8})\)")
_BULLET_SYM = re.compile(r"^([A-Z0-9]{2,8})\s*[·\-—]")
_PAIR_SYM = re.compile(r"\b([A-Z0-9]{2,8})USDT?\b")
_BARE_SYM = re.compile(r"\b([A-Z0-9]{2,8})\b")


def load_xlsx_rows(xlsx_path: pathlib.Path) -> list:
    """Load the 'Crypto Watchlist' sheet, skip header rows, return data rows."""
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb["Crypto Watchlist"]
    rows: list = []
    for r in ws.iter_rows(values_only=True):
        if not isinstance(r[0], int):
            continue
        rows.append(XlsxRow(
            rank=r[0], name=str(r[1] or ""), symbol=str(r[2] or "").upper(),
            trading_pair=str(r[3] or ""), category=str(r[4] or ""),
            asset_type=str(r[9] or ""),
            sentiment=str(r[7] or ""), signal=str(r[8] or ""),
            tradingview_symbol=str(r[6] or ""),
        ))
    return rows


def build_xlsx_index(rows: list) -> XlsxIndex:
    return XlsxIndex(
        by_symbol={r.symbol.upper(): r for r in rows if r.symbol},
        by_name={r.name.lower(): r for r in rows if r.name},
        by_pair={r.trading_pair.upper().replace("USDT", "").replace("USD", ""): r
                 for r in rows if r.trading_pair},
        name_choices=[r.name for r in rows if r.name],
    )


def match_post_to_symbol(
    *,
    title: str,
    index: XlsxIndex,
    llm_disambiguate: Callable,
    fuzzy_threshold: int = 90,
) -> tuple:
    """Return (symbol, method) — symbol is None if no match."""
    # 1. Parenthesized symbol
    m = _PAREN_SYM.search(title)
    if m and m.group(1) in index.by_symbol:
        return m.group(1), "paren_symbol"

    # 2. Bullet/dash symbol
    m = _BULLET_SYM.match(title)
    if m and m.group(1) in index.by_symbol:
        return m.group(1), "bullet_symbol"

    # 3. Trading pair (BTCUSDT, ETHUSD)
    for m in _PAIR_SYM.finditer(title):
        cand = m.group(1)
        if cand in index.by_symbol:
            return cand, "trading_pair"

    # 4. Bare symbol token — first standalone ticker present in the index.
    # Handles "BTC vs ETH" prose where there's no paren / bullet / pair anchor.
    for m in _BARE_SYM.finditer(title):
        cand = m.group(1)
        if cand in index.by_symbol:
            return cand, "bare_symbol"

    # 5. Fuzzy project name (canonical name appears verbatim in the title)
    best = process.extractOne(
        title, index.name_choices, scorer=fuzz.partial_ratio, score_cutoff=fuzzy_threshold
    )
    if best is not None:
        match_name, score, _ = best
        row = index.by_name.get(match_name.lower())
        if row:
            return row.symbol, "fuzzy_name"

    # 6. LLM disambiguation
    sym = llm_disambiguate(title, sorted(index.by_symbol.keys()))
    if sym and sym.upper() in index.by_symbol:
        return sym.upper(), "llm"

    return None, "none"


def _llm_disambiguate(title: str, candidates: list) -> Optional[str]:
    """Default LLM disambiguator — uses shared/llm_client.research_json."""
    from shared.llm_client import research_json

    prompt = (
        f"Title: {title!r}\n"
        f"Candidate symbols: {', '.join(candidates)}\n"
        f"Return JSON: {{\"symbol\": \"<one of the candidates or null if no match>\"}}.\n"
        f"Match only if highly confident (the title clearly references one specific project)."
    )
    obj = research_json(prompt)
    if not isinstance(obj, dict):
        return None
    return obj.get("symbol")


def run(
    *,
    in_path: pathlib.Path,
    xlsx_path: pathlib.Path,
    out_path: pathlib.Path,
    unmatched_path: pathlib.Path,
    use_llm: bool = True,
) -> tuple:
    rows = load_xlsx_rows(xlsx_path)
    idx = build_xlsx_index(rows)
    posts = [json.loads(l) for l in open(in_path) if l.strip()]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_matched = n_unmatched = 0
    llm = _llm_disambiguate if use_llm else (lambda *_, **__: None)
    with open(out_path, "w") as out_f, open(unmatched_path, "w") as bad_f:
        for p in posts:
            sym, method = match_post_to_symbol(
                title=p["title"], index=idx, llm_disambiguate=llm
            )
            if sym is None:
                bad_f.write(json.dumps(p) + "\n")
                n_unmatched += 1
            else:
                p2 = dict(p)
                p2["matched_symbol"] = sym
                p2["match_method"] = method
                out_f.write(json.dumps(p2) + "\n")
                n_matched += 1
    return n_matched, n_unmatched


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", default="data/maa/posts_filtered.jsonl",
                   type=pathlib.Path)
    p.add_argument("--xlsx", default="MasterAnanda_Watchlist.xlsx", type=pathlib.Path)
    p.add_argument("--out", dest="out_path", default="data/maa/posts_matched.jsonl",
                   type=pathlib.Path)
    p.add_argument("--unmatched", dest="unmatched_path",
                   default="data/maa/posts_unmatched.jsonl", type=pathlib.Path)
    p.add_argument("--no-llm", action="store_true",
                   help="Skip the LLM disambiguation fallback")
    args = p.parse_args(argv)

    if not args.in_path.exists() or not args.xlsx.exists():
        print(f"Missing input: {args.in_path} or {args.xlsx}", file=sys.stderr)
        return 2

    n_match, n_unmatch = run(
        in_path=args.in_path, xlsx_path=args.xlsx,
        out_path=args.out_path, unmatched_path=args.unmatched_path,
        use_llm=not args.no_llm,
    )
    print(f"Matched {n_match}, unmatched {n_unmatch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
