"""
Single source of truth for Anthropic model pricing.

Used by shared/llm_client.py to compute per-call cost when the MAA pipeline
batch runner has set MAA_RUN_LOG. Keep prices in sync with
https://www.anthropic.com/pricing (last verified 2026-05-06).
"""
from __future__ import annotations


class UnknownModelError(KeyError):
    """Raised when compute_cost_usd is called with a model not in the table."""


# (input_usd_per_MTok, output_usd_per_MTok)
MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # Claude 4.x family — verified 2026-05-06
    "claude-opus-4-7":    (15.0, 75.0),
    "claude-opus-4-6":    (15.0, 75.0),
    "claude-sonnet-4-6":  (3.0, 15.0),
    "claude-haiku-4-5":   (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    # Claude 3.x — kept for legacy callers / smoke tests
    "claude-3-5-sonnet-latest": (3.0, 15.0),
    "claude-3-5-haiku-latest":  (0.8, 4.0),
}


def compute_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Return USD cost of a single API call given token counts.

    Raises UnknownModelError if the model isn't in MODEL_PRICES_USD_PER_MTOK
    so the caller cannot silently mis-attribute cost. Callers in the cost-log
    path should catch this and fall back to logging cost_usd=None.
    """
    try:
        in_price, out_price = MODEL_PRICES_USD_PER_MTOK[model]
    except KeyError:
        raise UnknownModelError(model) from None
    return (
        prompt_tokens * in_price / 1_000_000
        + completion_tokens * out_price / 1_000_000
    )
