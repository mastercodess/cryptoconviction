"""Tests for scripts.maa.build_summary."""
from __future__ import annotations

import json
import pathlib

import pytest

from scripts.maa.build_summary import (
    classify_divergence,
    build_summary_md,
)


def test_divergence_aligned():
    assert classify_divergence(maa=8, conviction=70) == "ALIGNED"


def test_divergence_high_maa_low_conv():
    assert classify_divergence(maa=9, conviction=30) == "HIGH-MAA-LOW-CONVICTION"


def test_divergence_low_maa_high_conv():
    assert classify_divergence(maa=3, conviction=75) == "LOW-MAA-HIGH-CONVICTION"


def test_divergence_neutral():
    assert classify_divergence(maa=5, conviction=50) == ""


def test_summary_md_renders_table_and_paragraphs(tmp_path):
    top20 = [
        {"rank": 1, "symbol": "BTC", "name": "Bitcoin", "category": "Layer 1",
         "score": 9, "thesis_type": "fundamental",
         "rationale": "Macro setup strong.",
         "skepticism_flags": [], "language_signals": [],
         "top_posts": [{"date": "2026-05-06", "title": "BTC ATH"}]},
        {"rank": 2, "symbol": "DOGE", "name": "Dogecoin", "category": "Meme",
         "score": 8, "thesis_type": "leverage",
         "rationale": "10X long PP.",
         "skepticism_flags": ["leverage-only thesis"], "language_signals": ["10X long"],
         "top_posts": []},
    ]
    convictions = {
        "BTC": {
            "weighted_conviction": 75, "final_verdict": "BUY",
            "bull_case": ["Strong macro", "ETF flows"],
            "bear_case": ["Cycle risk"],
            "category_scorecard": {},
        },
        "DOGE": {
            "weighted_conviction": 25, "final_verdict": "AVOID",
            "bull_case": [], "bear_case": ["Leverage thesis", "No fundamental"],
            "category_scorecard": {},
        },
    }
    md = build_summary_md(
        top20=top20, convictions=convictions,
        run_log={"total_cost_usd": 18.50, "tokens_completed": 2,
                 "tokens_skipped": 0, "wall_time_s": 7200},
    )
    # Table presence
    assert "| Rank |" in md
    assert "MAA score (/10)" in md
    assert "Conviction (/100)" in md
    # Divergence visible
    assert "HIGH-MAA-LOW-CONVICTION" in md  # DOGE: maa=8, conv=25
    assert "ALIGNED" in md  # BTC: maa=9, conv=75
    # Per-token paragraph
    assert "BTC" in md and "Bitcoin" in md
    assert "DOGE" in md
    # Run summary header
    assert "$18.50" in md or "18.50" in md
