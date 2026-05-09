"""End-to-end smoke test for the MAA pipeline on a synthetic 2-token sample."""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from scripts.maa import (
    parse_dates, match_projects, score_projects, select_top_20,
    expand_registry, commit_registry, run_conviction_batch, build_summary,
)


@pytest.fixture
def tiny_universe(tmp_path):
    """Set up a tiny project tree with two synthetic posts and two xlsx rows."""
    # Posts
    posts_raw = tmp_path / "posts_raw.jsonl"
    posts_raw.write_text("\n".join([
        json.dumps({
            "title": "Cardano (ADA) starts to move", "description": "...",
            "date_string": "1 day ago", "comments": 3, "boosts": 10,
            "is_updated": False, "page_number": 2,
        }),
        json.dumps({
            "title": "FET · cycle starting", "description": "...",
            "date_string": "2 days ago", "comments": 1, "boosts": 4,
            "is_updated": False, "page_number": 3,
        }),
    ]) + "\n")

    # Synthetic xlsx (we'll write a real workbook so match_projects can load it)
    xlsx_path = tmp_path / "tiny.xlsx"
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Crypto Watchlist"
    ws.append(["title-row"])
    ws.append(["subtitle"])
    ws.append(["#", "Coin / Project", "Symbol", "Trading Pair", "Category",
               "Best Exchange", "TradingView Symbol", "Sentiment",
               "Signal / Note", "Asset Type"])
    ws.append([1, "Cardano", "ADA", "ADAUSDT", "Layer 1", "Binance",
               "BINANCE:ADAUSDT", "Bullish", "...", "Crypto"])
    ws.append([2, "Fetch.ai", "FET", "FETUSDT", "AI", "Binance",
               "BINANCE:FETUSDT", "Bullish", "...", "Crypto"])
    wb.save(xlsx_path)

    return {"tmp": tmp_path, "posts_raw": posts_raw, "xlsx": xlsx_path}


def test_phase1_e2e(tiny_universe, anchor_dt, window_start):
    """Posts -> filtered -> matched -> scored -> top-20 -- without real API."""
    tmp = tiny_universe["tmp"]
    raw = tiny_universe["posts_raw"]
    xlsx = tiny_universe["xlsx"]

    filtered = tmp / "posts_filtered.jsonl"
    matched = tmp / "posts_matched.jsonl"
    unmatched = tmp / "posts_unmatched.jsonl"
    scores = tmp / "scores.json"
    top_json = tmp / "top.json"
    top_md = tmp / "top.md"

    # Step 1: parse_dates
    n_kept = parse_dates.run(in_path=raw, out_path=filtered,
                             anchor=anchor_dt, window_start=window_start)
    assert n_kept == 2

    # Step 2: match_projects -- disable LLM
    n_match, n_unmatch = match_projects.run(
        in_path=filtered, xlsx_path=xlsx, out_path=matched,
        unmatched_path=unmatched, use_llm=False,
    )
    assert n_match == 2
    assert n_unmatch == 0

    # Step 3: score_projects -- mock the LLM
    fake_score = {
        "score": 7, "rationale": "synthetic", "thesis_type": "mixed",
        "latest_post": "2026-05-06T11:00:00", "post_count": 1,
        "language_signals": [], "price_targets": [], "leverage_mentions": [],
        "skepticism_flags": [], "top_posts": [],
    }
    with patch("scripts.maa.score_projects.research_json", return_value=fake_score):
        n_scored = score_projects.run(
            in_path=matched, xlsx_path=xlsx, out_path=scores,
        )
    assert n_scored == 2

    # Step 4: select_top_20
    select_top_20.main([
        "--scores", str(scores),
        "--xlsx", str(xlsx),
        "--out-json", str(top_json),
        "--out-md", str(top_md),
        "--top-n", "20",
    ])
    ranked = json.loads(top_json.read_text())
    assert len(ranked) == 2
    md = top_md.read_text()
    assert "ADA" in md and "FET" in md
    assert "/10" in md  # unit suffix


def test_build_summary_with_no_conviction_files(tiny_universe, tmp_path):
    """build_summary handles missing conviction.json files gracefully."""
    top20 = [{"rank": 1, "symbol": "ADA", "name": "Cardano", "category": "Layer 1",
              "score": 7, "thesis_type": "mixed",
              "rationale": "x", "skepticism_flags": [],
              "language_signals": [], "top_posts": []}]
    top_path = tmp_path / "top20.json"
    top_path.write_text(json.dumps(top20))
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    run_log = tmp_path / "run_log.jsonl"
    run_log.write_text("")
    out_path = tmp_path / "summary.md"

    build_summary.run(
        top20_path=top_path, reports_dir=reports_dir,
        run_log_path=run_log, out_path=out_path,
    )
    md = out_path.read_text()
    assert "ADA" in md
    assert "NOT_RUN" in md or "no conviction.json" in md


def test_runner_halts_when_flag_missing(tmp_path):
    from scripts.maa.run_conviction_batch import halt_if_no_flag, BatchAbortedError
    flag = tmp_path / "missing.flag"
    with pytest.raises(BatchAbortedError):
        halt_if_no_flag(flag)
