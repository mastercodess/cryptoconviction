"""Tests for scripts.maa.score_projects."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.maa.score_projects import (
    group_by_symbol,
    build_judge_prompt,
    score_one_project,
)


@pytest.fixture
def matched_posts():
    return [
        {"matched_symbol": "ADA", "title": "Cardano (ADA) starts to move",
         "description": "...", "effective_date": "2026-05-06T11:00:00",
         "boosts": 31, "comments": 7, "is_updated": True},
        {"matched_symbol": "ADA", "title": "Cardano keeps moving",
         "description": "...", "effective_date": "2026-04-15T08:00:00",
         "boosts": 5, "comments": 2, "is_updated": False},
        {"matched_symbol": "FET", "title": "FET · cycle starting",
         "description": "...", "effective_date": "2026-05-06T06:00:00",
         "boosts": 7, "comments": 7, "is_updated": False},
    ]


def test_group_by_symbol(matched_posts):
    g = group_by_symbol(matched_posts)
    assert set(g.keys()) == {"ADA", "FET"}
    assert len(g["ADA"]) == 2
    assert len(g["FET"]) == 1


def test_group_sorts_newest_first(matched_posts):
    g = group_by_symbol(matched_posts)
    ada_dates = [p["effective_date"] for p in g["ADA"]]
    assert ada_dates == sorted(ada_dates, reverse=True)


def test_judge_prompt_contains_skeptical_instructions(matched_posts):
    g = group_by_symbol(matched_posts)
    prompt = build_judge_prompt(symbol="ADA", name="Cardano",
                                category="Layer 1", posts=g["ADA"])
    # Sanity-check the must-have signals
    assert "leveraged trade thesis" in prompt.lower()
    assert "weight the most recent" in prompt.lower()
    assert "boost" in prompt.lower()
    assert "Cardano" in prompt
    # Posts are inlined
    assert "Cardano (ADA) starts to move" in prompt


def test_score_one_project_mocked(matched_posts):
    g = group_by_symbol(matched_posts)
    fake_response = {
        "score": 8,
        "rationale": "Multiple posts in last week with strong language.",
        "thesis_type": "mixed",
        "latest_post": "2026-05-06T11:00:00",
        "post_count": 2,
        "language_signals": ["last chance", "moves"],
        "price_targets": ["$0.85"],
        "leverage_mentions": ["10X"],
        "skepticism_flags": [],
        "top_posts": [
            {"date": "2026-05-06T11:00:00", "title": "Cardano (ADA) starts to move"},
            {"date": "2026-04-15T08:00:00", "title": "Cardano keeps moving"},
        ],
    }
    with patch("scripts.maa.score_projects.research_json", return_value=fake_response):
        result = score_one_project(symbol="ADA", name="Cardano",
                                   category="Layer 1", posts=g["ADA"])
    assert result["score"] == 8
    assert result["thesis_type"] == "mixed"
    assert result["post_count"] == 2
    assert len(result["top_posts"]) == 2


def test_score_one_project_handles_llm_failure(matched_posts):
    g = group_by_symbol(matched_posts)
    with patch("scripts.maa.score_projects.research_json", return_value=None):
        result = score_one_project(symbol="ADA", name="Cardano",
                                   category="Layer 1", posts=g["ADA"])
    # Should produce a degraded result, not crash
    assert result["score"] == 0
    assert "llm_no_response" in result["skepticism_flags"]
