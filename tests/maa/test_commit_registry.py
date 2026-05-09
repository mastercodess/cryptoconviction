"""Tests for scripts.maa.commit_registry."""
from __future__ import annotations

import json
import pathlib
import shutil
from textwrap import dedent

import pytest

from scripts.maa.commit_registry import (
    render_token_block,
    patch_tokens_py,
    compute_dropped_symbols,
    run,
)


def test_render_token_block_evm():
    row = {
        "symbol": "UNI", "name": "Uniswap", "chain": "ethereum",
        "coingecko_id": "uniswap",
        "contract_address": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
        "defillama_protocol": "uniswap-v3",
        "category": "dex", "is_protocol": True,
        "notes": "Resolved 2026-05-06 via CoinGecko.",
    }
    block = render_token_block(row)
    assert '"UNI": Token(' in block
    assert 'symbol="UNI"' in block
    assert 'chain="ethereum"' in block
    assert 'contract_address="0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"' in block
    assert "defillama_protocol=\"uniswap-v3\"" in block


def test_render_token_block_native_l1():
    row = {
        "symbol": "BTC", "name": "Bitcoin", "chain": "bitcoin",
        "coingecko_id": "bitcoin",
        "contract_address": None, "defillama_protocol": None,
        "category": "layer-1", "is_protocol": False,
        "notes": "Native L1.",
    }
    block = render_token_block(row)
    assert "contract_address=None" in block
    assert "defillama_protocol=None" in block


def test_patch_tokens_py(tmp_path):
    src = tmp_path / "tokens.py"
    src.write_text(dedent('''
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Token:
        symbol: str

    REGISTRY: dict[str, Token] = {
        "LINK": Token(symbol="LINK"),
    }
    ''').lstrip())

    new_block = '    "UNI": Token(symbol="UNI"),'
    patch_tokens_py(src, [new_block])

    final = src.read_text()
    assert '"LINK": Token(symbol="LINK"),' in final
    assert '"UNI": Token(symbol="UNI"),' in final
    # Insertion is BEFORE the closing brace
    link_idx = final.index('"LINK"')
    uni_idx = final.index('"UNI"')
    close_idx = final.rindex("}")
    assert link_idx < uni_idx < close_idx


def test_compute_dropped_symbols():
    top20 = [{"symbol": "BTC", "rank": 1}, {"symbol": "UNI", "rank": 2},
             {"symbol": "ZZZ", "rank": 3}]
    resolved = [{"symbol": "UNI"}]
    existing = {"BTC"}
    dropped = compute_dropped_symbols(top20, resolved, existing)
    syms = [d["symbol"] for d in dropped]
    assert "ZZZ" in syms
    assert "BTC" not in syms  # already in existing registry, not dropped
    assert "UNI" not in syms


def test_run_creates_flag_and_dropped(tmp_path):
    """Integration: run produces the gate flag + dropped_symbols + patched file."""
    # Set up tmp tokens.py
    tokens_src = tmp_path / "tokens.py"
    tokens_src.write_text(dedent('''
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Token:
        symbol: str

    REGISTRY: dict[str, Token] = {
        "LINK": Token(symbol="LINK"),
    }
    ''').lstrip())
    proposed = tmp_path / "proposed_registry.json"
    proposed.write_text(json.dumps([{
        "symbol": "UNI", "name": "Uniswap", "chain": "ethereum",
        "coingecko_id": "uniswap", "contract_address": "0xabc",
        "defillama_protocol": "uniswap-v3", "category": "dex",
        "is_protocol": True, "notes": "test",
    }]))
    top20 = tmp_path / "top20.json"
    top20.write_text(json.dumps([
        {"symbol": "UNI", "rank": 1, "name": "Uniswap"},
        {"symbol": "ZZZ", "rank": 2, "name": "Z"},
    ]))
    flag = tmp_path / "registry.committed.flag"
    dropped = tmp_path / "dropped_symbols.json"

    run(
        proposed_path=proposed,
        top20_path=top20,
        tokens_py_path=tokens_src,
        flag_path=flag,
        dropped_path=dropped,
        existing_symbols={"LINK"},
    )

    assert flag.exists()
    assert "UNI" in tokens_src.read_text()
    dropped_data = json.loads(dropped.read_text())
    assert any(d["symbol"] == "ZZZ" for d in dropped_data)
