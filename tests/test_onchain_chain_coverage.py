"""Pin the chain-coverage decisions from plan 2026-05-15.

Each addition to the Dune CHAIN_DAU query (id 7485961) implies a
registry-to-Dune-label mapping that must exist in
agents/04_onchain/collect.py:_CHAIN_TO_DUNE. These tests catch silent
regressions: if someone removes a mapping (or if a registry chain value
drifts), the right-shaped test fails before any collect run silently
produces UNAVAILABLE.

Cheat sheet recorded during plan execution (Task 1, 2026-05-15):
    chain label  | table                    | date filter        | addr col
    -------------+--------------------------+--------------------+----------
    avalanche_c  | avalanche_c.transactions | block_time         | "from"
    sui          | sui.transactions         | date               | sender
    ton          | ton.transactions         | block_date         | account
    xrpl         | xrpl.transactions        | ledger_close_date  | account
    near         | near.actions             | block_time         | tx_from
"""
from __future__ import annotations

import importlib

# fmt: off
_collect = importlib.import_module("agents.04_onchain.collect")
_CHAIN_TO_DUNE = _collect._CHAIN_TO_DUNE
# fmt: on


# ─── Pre-plan mappings (regression guard) ────────────────────────────────

def test_chain_to_dune_preserves_existing_evm_entries():
    """Pre-plan EVM mappings must remain. Removing any of these silently
    UNAVAILABLEs a working chain."""
    for k in ("ethereum", "base", "arbitrum", "optimism", "polygon", "bnb"):
        assert k in _CHAIN_TO_DUNE, f"existing EVM mapping {k!r} was removed"
        assert _CHAIN_TO_DUNE[k] == k, (
            f"{k!r} should map to itself; got {_CHAIN_TO_DUNE[k]!r}"
        )


def test_chain_to_dune_avalanche_maps_to_avalanche_c():
    # Pre-plan entry — registry uses 'avalanche', Dune uses 'avalanche_c'.
    assert _CHAIN_TO_DUNE.get("avalanche") == "avalanche_c"


def test_chain_to_dune_preserves_pre_plan_non_evm_entries():
    for k in ("tron", "solana", "sui"):
        assert k in _CHAIN_TO_DUNE, f"existing non-EVM mapping {k!r} was removed"


def test_chain_to_dune_preserves_btc_style_entries():
    """BTC-style chains route to BTC_LTH_STH, not CHAIN_DAU; mappings still
    needed for chain-class category routing in _collect_chain."""
    assert _CHAIN_TO_DUNE.get("bitcoin") == "bitcoin"
    assert _CHAIN_TO_DUNE.get("bitcoin-cash") == "bitcoin-cash"


# ─── New mappings added by plan 2026-05-15 ───────────────────────────────

def test_chain_to_dune_includes_ton():
    """TON: registry 'ton' → Dune 'ton' (self-identical)."""
    assert _CHAIN_TO_DUNE.get("ton") == "ton"


def test_chain_to_dune_includes_ripple_as_xrpl():
    """XRP: registry uses 'ripple' (the company), Dune emits 'xrpl'
    (the ledger schema name, distinct from 'xrp' the token). The
    collector must bridge that naming."""
    assert _CHAIN_TO_DUNE.get("ripple") == "xrpl"


def test_chain_to_dune_includes_near():
    """NEAR: registry 'near' → Dune 'near' (self-identical)."""
    assert _CHAIN_TO_DUNE.get("near") == "near"


# ─── Negative assertions (chains explicitly NOT in this plan) ────────────

def test_chain_to_dune_does_not_include_stacks():
    """Dune does not carry Stacks as of 2026-05-15 (confirmed in Task 1).
    A future plan 2 will integrate STX via Hiro API. If anyone re-adds
    a 'stacks' mapping here without that integration, the collector will
    call CHAIN_DAU for STX, get no row, and silently UNAVAILABLE."""
    assert "stacks" not in _CHAIN_TO_DUNE, (
        "stacks shouldn't be in _CHAIN_TO_DUNE — Dune doesn't index it; "
        "STX onchain integration is plan 2 territory"
    )


# ─── Cross-check: registry chain values for plan-targeted tokens ─────────

def test_registry_chain_values_for_target_tokens_map_to_dune():
    """Each token this plan targets must have a registry chain value that
    is a key in _CHAIN_TO_DUNE — otherwise _collect_chain falls through
    to _unavailable and the new Dune row never gets read."""
    from shared.tokens import get
    for sym in ("AVAX", "SUI", "TON", "XRP", "NEAR"):
        tok = get(sym)
        chain = tok.chain.lower()
        assert chain in _CHAIN_TO_DUNE, (
            f"{sym} registry chain={chain!r} has no _CHAIN_TO_DUNE entry; "
            f"add it or this token will stay UNAVAILABLE"
        )


def test_xrp_registry_chain_bridges_to_xrpl_via_map():
    """End-to-end sanity for the registry='ripple' → Dune='xrpl' bridge.
    Most surprising mapping in the new set; pin it explicitly."""
    from shared.tokens import get
    xrp = get("XRP")
    assert xrp.chain.lower() == "ripple"
    assert _CHAIN_TO_DUNE[xrp.chain.lower()] == "xrpl"


def test_stx_registry_present_but_unmapped_to_dune():
    """STX is in the registry as a chain-class token but intentionally
    NOT in _CHAIN_TO_DUNE — confirms the plan's deferral of Stacks to
    plan 2 is captured in code, not just in prose."""
    from shared.tokens import get
    try:
        stx = get("STX")
    except KeyError:
        # STX not in registry: fine, nothing to assert.
        return
    assert stx.chain.lower() not in _CHAIN_TO_DUNE, (
        f"STX registry chain={stx.chain.lower()!r} unexpectedly mapped — "
        "if this is intentional, the plan-2 Hiro integration also needs "
        "to land before _collect_chain will produce a usable row"
    )
