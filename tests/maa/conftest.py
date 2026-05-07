"""Pytest fixtures shared across MAA pipeline tests."""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def anchor_dt() -> dt.datetime:
    """PDF export anchor: 2026-05-06 17:00."""
    return dt.datetime(2026, 5, 6, 17, 0, 0)


@pytest.fixture
def window_start() -> dt.datetime:
    return dt.datetime(2026, 3, 30, 0, 0, 0)


@pytest.fixture
def sample_posts_raw() -> list[dict]:
    """Three synthetic posts spanning the boundary types."""
    return [
        {
            "title": "Cardano (ADA) starts to move — Last chance!",
            "description": "Cardano (ADAUSDT) has been moving close to support...",
            "date_string": "Updated 6 hours ago",
            "comments": 7,
            "boosts": 31,
            "is_updated": True,
            "page_number": 2,
        },
        {
            "title": "FET · The Start of a New Bullish Cycle",
            "description": "I always ask myself this question—I see the market...",
            "date_string": "11 hours ago",
            "comments": 7,
            "boosts": 7,
            "is_updated": False,
            "page_number": 3,
        },
        {
            "title": "Old NEO post — should be filtered out",
            "description": "Pre-window content.",
            "date_string": "2 months ago",
            "comments": 0,
            "boosts": 1,
            "is_updated": False,
            "page_number": 100,
        },
    ]


@pytest.fixture
def tmp_data_dir(tmp_path) -> pathlib.Path:
    """Isolated data/maa/ directory for the test."""
    d = tmp_path / "data" / "maa"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def write_jsonl(tmp_data_dir):
    """Helper to write a list[dict] as JSONL into the temp dir."""
    def _write(filename: str, rows: list[dict]) -> pathlib.Path:
        p = tmp_data_dir / filename
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p
    return _write
