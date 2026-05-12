"""Tests for the FRED API client."""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from shared.data_sources.fred import latest_observation, fed_funds_rate, m2_yoy_pct


def _mock_fred_response(observations: list[dict]):
    """Build a fake FRED JSON response."""
    return MagicMock(
        status_code=200,
        json=MagicMock(return_value={"observations": observations}),
    )


def test_latest_observation_returns_most_recent(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test_key")
    fake = _mock_fred_response([
        {"date": "2026-05-08", "value": "4.40"},
        {"date": "2026-05-01", "value": "4.33"},
    ])
    with patch("requests.get", return_value=fake) as gm:
        result = latest_observation("DFF")
    assert result == {"date": "2026-05-08", "value": 4.40}
    call_kwargs = gm.call_args.kwargs
    assert "series_id" in call_kwargs["params"]
    assert call_kwargs["params"]["series_id"] == "DFF"
    assert call_kwargs["params"]["api_key"] == "test_key"
    assert call_kwargs["params"]["sort_order"] == "desc"


def test_latest_observation_missing_api_key_returns_none(monkeypatch):
    """Without FRED_API_KEY, return None rather than crash."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    result = latest_observation("DFF")
    assert result is None


def test_latest_observation_skips_period_placeholder(monkeypatch):
    """FRED uses '.' for missing observations; client must skip them."""
    monkeypatch.setenv("FRED_API_KEY", "test_key")
    fake = _mock_fred_response([
        {"date": "2026-05-08", "value": "."},
        {"date": "2026-05-01", "value": "4.33"},
    ])
    with patch("requests.get", return_value=fake):
        result = latest_observation("DFF")
    assert result == {"date": "2026-05-01", "value": 4.33}


def test_fed_funds_rate_calls_dff(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test_key")
    fake = _mock_fred_response([{"date": "2026-05-08", "value": "4.40"}])
    with patch("requests.get", return_value=fake) as gm:
        result = fed_funds_rate()
    assert result == {"date": "2026-05-08", "value": 4.40}
    assert gm.call_args.kwargs["params"]["series_id"] == "DFF"


def test_m2_yoy_pct_computes_year_over_year(monkeypatch):
    """M2 YoY = (M2_now / M2_one_year_ago - 1) * 100."""
    monkeypatch.setenv("FRED_API_KEY", "test_key")
    obs = [{"date": "2026-05-01", "value": "21000"}]
    # Pad with 51 weekly observations going back, then year-ago
    for i in range(1, 51):
        obs.append({"date": f"2026-{(5 - i // 4) if (5 - i // 4) > 0 else 12}-01",
                    "value": str(21000 - i * 10)})
    obs.append({"date": "2025-05-01", "value": "20200"})
    fake = _mock_fred_response(obs)
    with patch("requests.get", return_value=fake):
        result = m2_yoy_pct()
    # (21000/20200 - 1) * 100 = ~3.96%
    assert result is not None
    assert abs(result["value"] - 3.96) < 0.01
    assert result["date"] == "2026-05-01"


def test_m2_yoy_pct_returns_none_when_insufficient_history(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test_key")
    fake = _mock_fred_response([{"date": "2026-05-01", "value": "21000"}])
    with patch("requests.get", return_value=fake):
        result = m2_yoy_pct()
    assert result is None
