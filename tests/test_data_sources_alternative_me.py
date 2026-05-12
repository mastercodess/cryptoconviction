"""Tests for the alternative.me Fear & Greed Index client."""
from __future__ import annotations
import pytest
import requests
from unittest.mock import patch, MagicMock
from shared.data_sources.alternative_me import fear_greed_index


def test_fear_greed_index_parses_response():
    fake = MagicMock(
        status_code=200,
        json=MagicMock(return_value={
            "data": [
                {
                    "value": "73",
                    "value_classification": "Greed",
                    "timestamp": "1747008000",  # 2025-05-12 00:00 UTC
                }
            ]
        }),
    )
    with patch("requests.get", return_value=fake) as gm:
        result = fear_greed_index()
    assert result is not None
    assert result["value"] == 73
    assert result["classification"] == "Greed"
    assert result["date"] == "2025-05-12"
    # Endpoint check
    assert "alternative.me" in gm.call_args.args[0]


def test_fear_greed_index_returns_none_on_http_error():
    fake = MagicMock(status_code=500, raise_for_status=MagicMock(
        side_effect=requests.HTTPError("500")
    ))
    with patch("requests.get", return_value=fake):
        result = fear_greed_index()
    assert result is None


def test_fear_greed_index_returns_none_on_empty_data():
    fake = MagicMock(status_code=200, json=MagicMock(return_value={"data": []}))
    with patch("requests.get", return_value=fake):
        result = fear_greed_index()
    assert result is None
