"""Tests for shared.pricing — model price lookup + cost computation."""
from __future__ import annotations

import pytest

from shared.pricing import (
    MODEL_PRICES_USD_PER_MTOK,
    compute_cost_usd,
    UnknownModelError,
)


def test_known_models_have_input_and_output_prices():
    for model, prices in MODEL_PRICES_USD_PER_MTOK.items():
        assert isinstance(model, str), f"non-string model key: {model!r}"
        assert isinstance(prices, tuple), f"prices not tuple: {model}"
        assert len(prices) == 2, f"prices not (input, output) pair: {model}"
        in_price, out_price = prices
        assert in_price > 0 and out_price > 0, f"non-positive price for {model}"
        assert out_price > in_price, f"output cheaper than input for {model}"


def test_compute_cost_known_model():
    # Sonnet 4.6 prices: ($3 input, $15 output) per MTok
    cost = compute_cost_usd(
        model="claude-sonnet-4-6",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
    )
    assert cost == pytest.approx(18.0, rel=1e-6)


def test_compute_cost_small_request():
    cost = compute_cost_usd(
        model="claude-sonnet-4-6",
        prompt_tokens=10_000,
        completion_tokens=2_000,
    )
    # 10k * 3/1M + 2k * 15/1M = 0.030 + 0.030 = 0.060
    assert cost == pytest.approx(0.060, rel=1e-6)


def test_compute_cost_unknown_model_raises():
    with pytest.raises(UnknownModelError, match="future-claude-99"):
        compute_cost_usd("future-claude-99", 100, 100)


def test_compute_cost_with_cache_reads():
    """Cache reads are billed at 0.10x base input price."""
    # 1M cache_read tokens × $3/MTok × 0.10 = $0.30
    cost = compute_cost_usd(
        model="claude-sonnet-4-6",
        prompt_tokens=0, completion_tokens=0,
        cache_read_tokens=1_000_000,
    )
    assert cost == pytest.approx(0.30, rel=1e-6)


def test_compute_cost_with_cache_creation():
    """Cache creation is billed at 1.25x base input price."""
    # 1M cache_creation tokens × $3/MTok × 1.25 = $3.75
    cost = compute_cost_usd(
        model="claude-sonnet-4-6",
        prompt_tokens=0, completion_tokens=0,
        cache_creation_tokens=1_000_000,
    )
    assert cost == pytest.approx(3.75, rel=1e-6)


def test_compute_cost_realistic_cached_call():
    """Typical RLM call: small uncached prompt, big cache read, small output.
    Cache read should dominate the bill, but at 0.10x — not at full price.
    """
    cost = compute_cost_usd(
        model="claude-opus-4-6",
        prompt_tokens=500,           # Opus $15/MTok input → $0.0075
        completion_tokens=200,       # Opus $75/MTok output → $0.015
        cache_read_tokens=20_000,    # $15/MTok × 0.10 = $1.50/MTok → $0.030
    )
    # 0.0075 + 0.015 + 0.030 = 0.0525
    assert cost == pytest.approx(0.0525, rel=1e-6)


def test_zero_tokens_zero_cost():
    cost = compute_cost_usd("claude-sonnet-4-6", 0, 0)
    assert cost == 0.0
