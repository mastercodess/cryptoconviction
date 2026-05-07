"""Tests for scripts.maa.parse_dates."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from scripts.maa.parse_dates import (
    parse_date_string,
    filter_to_window,
    UnparseableDateError,
)


@pytest.mark.parametrize("date_str,expected_offset", [
    ("9 minutes ago",            dt.timedelta(minutes=9)),
    ("1 minute ago",             dt.timedelta(minutes=1)),
    ("Updated 6 hours ago",      dt.timedelta(hours=6)),
    ("11 hours ago",             dt.timedelta(hours=11)),
    ("1 day ago",                dt.timedelta(days=1)),
    ("3 days ago",               dt.timedelta(days=3)),
    ("2 weeks ago",              dt.timedelta(weeks=2)),
    ("1 month ago",              dt.timedelta(days=30)),
    ("2 months ago",             dt.timedelta(days=60)),
])
def test_relative_to_absolute(date_str, expected_offset, anchor_dt):
    result = parse_date_string(date_str, anchor=anchor_dt)
    expected = anchor_dt - expected_offset
    diff = abs((result - expected).total_seconds())
    assert diff < 60, f"expected {expected}, got {result}"


@pytest.mark.parametrize("date_str,expected_iso", [
    ("May 4",            "2026-05-04T00:00:00"),
    ("Apr 25",           "2026-04-25T00:00:00"),
    ("Mar 30",           "2026-03-30T00:00:00"),
    ("Updated Apr 16",   "2026-04-16T00:00:00"),
    ("May 1, 2025",      "2025-05-01T00:00:00"),
])
def test_absolute_date(date_str, expected_iso, anchor_dt):
    result = parse_date_string(date_str, anchor=anchor_dt)
    assert result.isoformat() == expected_iso


def test_unparseable_date_raises():
    anchor = dt.datetime(2026, 5, 6, 17, 0)
    with pytest.raises(UnparseableDateError):
        parse_date_string("two thousand years ago", anchor=anchor)


def test_filter_to_window_drops_pre_window(anchor_dt, window_start):
    posts = [
        {"date_string": "11 hours ago", "title": "in-window"},
        {"date_string": "2 months ago", "title": "outside"},
        {"date_string": "Updated 5 weeks ago", "title": "right at edge"},
        {"date_string": "Apr 25", "title": "absolute-in-window"},
        {"date_string": "Mar 1", "title": "absolute-outside"},
    ]
    kept = filter_to_window(posts, anchor=anchor_dt, window_start=window_start)
    titles = [p["title"] for p in kept]
    assert "in-window" in titles
    assert "outside" not in titles
    assert "right at edge" in titles
    assert "absolute-in-window" in titles
    assert "absolute-outside" not in titles


def test_filter_adds_effective_date_iso(anchor_dt, window_start):
    posts = [{"date_string": "1 day ago", "title": "x"}]
    kept = filter_to_window(posts, anchor=anchor_dt, window_start=window_start)
    assert "effective_date" in kept[0]
    parsed_back = dt.datetime.fromisoformat(kept[0]["effective_date"])
    assert parsed_back == anchor_dt - dt.timedelta(days=1)


def test_filter_drops_unparseable(anchor_dt, window_start):
    posts = [
        {"date_string": "1 day ago", "title": "ok"},
        {"date_string": "?????", "title": "bad"},
    ]
    kept = filter_to_window(posts, anchor=anchor_dt, window_start=window_start)
    titles = [p["title"] for p in kept]
    assert "ok" in titles
    assert "bad" not in titles


def test_run_overwrites_output(tmp_data_dir, write_jsonl, anchor_dt, window_start):
    """The CLI overwrites its output, so re-running is idempotent."""
    from scripts.maa.parse_dates import run

    in_path = write_jsonl("posts_raw.jsonl", [
        {"date_string": "1 day ago", "title": "kept", "page_number": 2,
         "description": "x", "comments": 0, "boosts": 0, "is_updated": False},
        {"date_string": "2 months ago", "title": "dropped", "page_number": 3,
         "description": "y", "comments": 0, "boosts": 0, "is_updated": False},
    ])
    out_path = tmp_data_dir / "posts_filtered.jsonl"

    n_kept = run(
        in_path=in_path,
        out_path=out_path,
        anchor=anchor_dt,
        window_start=window_start,
    )
    assert n_kept == 1
    rows = [json.loads(l) for l in open(out_path)]
    assert len(rows) == 1
    assert rows[0]["title"] == "kept"

    # Run again — should overwrite, not append
    n_kept_2 = run(
        in_path=in_path,
        out_path=out_path,
        anchor=anchor_dt,
        window_start=window_start,
    )
    assert n_kept_2 == 1
    rows_2 = [json.loads(l) for l in open(out_path)]
    assert len(rows_2) == 1
