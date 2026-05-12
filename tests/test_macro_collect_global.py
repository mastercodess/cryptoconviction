"""Integration test for the API-backed collect_global()."""
from __future__ import annotations
import importlib
import json
import pathlib
import sqlite3
import sys
import pytest
from unittest.mock import patch


def test_collect_global_writes_factual_data_as_of(tmp_path, monkeypatch):
    """collect_global must call the 3 API clients and produce a sidecar with
    an `as_of` field reflecting the actual API response date — NOT an
    LLM-claimed date and NOT today's date by default."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    macro_collect = importlib.import_module("agents.07_macro.collect")

    # Redirect DB + sidecar
    monkeypatch.setattr(macro_collect, "DB_PATH", tmp_path / "macro.db")
    monkeypatch.setattr(macro_collect, "SIDECAR_DIR", tmp_path / "sidecars")
    # Initialize schema
    schema_path = repo_root / "agents" / "07_macro" / "schema.sql"
    conn = sqlite3.connect(tmp_path / "macro.db")
    conn.executescript(schema_path.read_text())
    conn.commit()
    conn.close()

    # Mock the three clients
    fake_fred_dff = {"date": "2026-05-10", "value": 4.40}
    fake_fred_m2 = {"date": "2026-05-08", "value": 3.96}
    fake_cg = {"btc_dominance_pct": 58.1, "total_mc_usd": 3.5e12,
               "total_mc_ex_btc_usd": 1.467e12, "as_of": "2026-05-12"}
    fake_fng = {"value": 65, "classification": "Greed", "date": "2026-05-12"}

    with patch.object(macro_collect, "fed_funds_rate", return_value=fake_fred_dff), \
         patch.object(macro_collect, "m2_yoy_pct", return_value=fake_fred_m2), \
         patch.object(macro_collect, "global_metrics", return_value=fake_cg), \
         patch.object(macro_collect, "fear_greed_index", return_value=fake_fng):
        result = macro_collect.collect_global()

    assert result.get("ok") is True
    sidecar = json.loads((tmp_path / "sidecars" / "_global" / "macro_global.json").read_text())
    # Most recent of the four API responses: 2026-05-12 (CoinGecko + alt.me)
    assert sidecar["as_of"] == "2026-05-12"
    assert sidecar["btc_dominance_pct"] == 58.1
    assert sidecar["fed_funds_rate"] == 4.40
    assert sidecar["m2_yoy_pct"] == 3.96
    assert sidecar["fear_greed_index"] == 65
    # Verify NO data_freshness_warning is still claiming the LLM excuse
    assert "I cannot execute live API calls" not in json.dumps(sidecar)


def test_collect_global_handles_missing_fred_key(tmp_path, monkeypatch):
    """When FRED_API_KEY is missing, fed_funds and m2_yoy must be null
    but the rest of the sidecar should still populate."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    macro_collect = importlib.import_module("agents.07_macro.collect")

    monkeypatch.setattr(macro_collect, "DB_PATH", tmp_path / "macro.db")
    monkeypatch.setattr(macro_collect, "SIDECAR_DIR", tmp_path / "sidecars")
    schema_path = repo_root / "agents" / "07_macro" / "schema.sql"
    conn = sqlite3.connect(tmp_path / "macro.db")
    conn.executescript(schema_path.read_text())
    conn.commit(); conn.close()

    with patch.object(macro_collect, "fed_funds_rate", return_value=None), \
         patch.object(macro_collect, "m2_yoy_pct", return_value=None), \
         patch.object(macro_collect, "global_metrics", return_value={
             "btc_dominance_pct": 58.1, "total_mc_usd": 3.5e12,
             "total_mc_ex_btc_usd": 1.467e12, "as_of": "2026-05-12"
         }), \
         patch.object(macro_collect, "fear_greed_index", return_value={
             "value": 65, "classification": "Greed", "date": "2026-05-12"
         }):
        result = macro_collect.collect_global()

    assert result.get("ok") is True
    sidecar = json.loads((tmp_path / "sidecars" / "_global" / "macro_global.json").read_text())
    assert sidecar["fed_funds_rate"] is None
    assert sidecar["m2_yoy_pct"] is None
    assert sidecar["btc_dominance_pct"] == 58.1  # other fields still present
    assert sidecar["as_of"] == "2026-05-12"
