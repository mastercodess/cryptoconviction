"""
Thin wrapper around the Anthropic API.

Two helpers:
  - sub_lm(prompt, ...): single-shot Sonnet call, used inside the RLM REPL as
    the recursive sub-LLM. Returns the text reply.
  - research(prompt, ...): same shape, but runs Sonnet with web-research style
    prompting for free-source data collection. Used by collect.py scripts.

Why split them: keeping research and sub-LLM behind named entry points makes
budget tracking and model-switching trivial later (e.g. swap Sonnet for Haiku
on cheap probes without touching agent code).
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

try:
    from anthropic import Anthropic
except ImportError:                # pragma: no cover
    Anthropic = None                # type: ignore


DEFAULT_SUB_MODEL = os.getenv("RLM_SUB_MODEL", "claude-sonnet-4-6")
DEFAULT_RESEARCH_MODEL = os.getenv("RESEARCH_MODEL", "claude-sonnet-4-6")
DEFAULT_ROOT_MODEL = os.getenv("RLM_ROOT_MODEL", "claude-opus-4-6")


@lru_cache(maxsize=1)
def _client() -> "Anthropic":
    if Anthropic is None:
        raise RuntimeError(
            "anthropic SDK not installed. Run: pip install -r requirements.txt"
        )
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill it in."
        )
    return Anthropic(api_key=key)


def sub_lm(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: str = DEFAULT_SUB_MODEL,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> str:
    """
    Single Sonnet call. This is THE function the RLM root invokes recursively
    over prompt slices. Keep it side-effect-free with respect to model state
    (no logging that mutates state) so the REPL is reproducible. The optional
    cost-log emission below is gated on MAA_RUN_LOG env var and is purely
    observational.
    """
    msg = _client().messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system or "You are a precise sub-analyst. Answer only what is asked, with no preamble.",
        messages=[{"role": "user", "content": prompt}],
    )
    _emit_usage_log(msg, model)
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def _emit_usage_log(msg, model: str) -> None:
    """Append one JSONL line to MAA_RUN_LOG if set; no-op otherwise.

    This is additive. When MAA_RUN_LOG is unset (smoke tests, current single-
    token runs, anything outside the MAA batch context), this function does
    nothing. The MAA batch runner sets MAA_RUN_LOG, MAA_AGENT_NAME, and
    MAA_ACTION on each subprocess invocation.
    """
    log_path = os.getenv("MAA_RUN_LOG")
    if not log_path:
        return
    import datetime as _dt
    import json as _json
    from shared.pricing import compute_cost_usd, UnknownModelError

    usage = getattr(msg, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    # Cache fields are present on responses from 2024-06+; default to 0 if absent
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    try:
        cost = compute_cost_usd(model, in_tok, out_tok, cache_read, cache_create)
    except UnknownModelError:
        cost = None
    record = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "model": model,
        "prompt_tokens": in_tok,
        "completion_tokens": out_tok,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_create,
        "cost_usd": cost,
        "agent_name": os.getenv("MAA_AGENT_NAME", "unknown"),
        "action": os.getenv("MAA_ACTION", "unknown"),
    }
    # Append-mode write. Multiple processes writing concurrently could in
    # theory interleave, but the MAA batch runner is sequential by design,
    # so we do not lock.
    with open(log_path, "a") as f:
        f.write(_json.dumps(record) + "\n")


def research_json(prompt: str, *, model: str = DEFAULT_RESEARCH_MODEL,
                  max_tokens: int = 8192) -> Optional[dict]:
    """
    Run research() and parse the first balanced JSON object/array out of the
    reply. Returns None if no parseable JSON found. Used by every agent's
    collect.py — keeps the parse-once-loosely logic in one place.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    txt = research(prompt, model=model, max_tokens=max_tokens)
    starts = [txt.find(c) for c in "{[" if txt.find(c) >= 0]
    if not starts:
        return None
    start = min(starts)
    open_c = txt[start]
    close_c = "}" if open_c == "{" else "]"
    depth = 0
    end = -1
    in_str = False
    escape = False
    import json as _json
    for i in range(start, len(txt)):
        ch = txt[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return None
    try:
        return _json.loads(txt[start:end])
    except Exception:                                   # noqa: BLE001
        return None


def research(
    prompt: str,
    *,
    model: str = DEFAULT_RESEARCH_MODEL,
    max_tokens: int = 8192,
) -> str:
    """
    Web-research style call for collect.py scripts. Uses a system prompt biased
    toward citing sources and admitting when data isn't free-source available.
    """
    system = (
        "You are a crypto data researcher. Pull facts from the user's listed "
        "free sources (CoinGecko, DefiLlama, Etherscan, GitHub, project docs, "
        "Coinglass public pages, FRED, alternative.me). When a value is not "
        "freely available, say 'NOT_AVAILABLE_FREE_TIER' and explain why. "
        "Always cite the URL where each fact came from. Be concise; structured "
        "JSON when asked, terse prose otherwise."
    )
    return sub_lm(prompt, system=system, model=model, max_tokens=max_tokens, temperature=0.0)
