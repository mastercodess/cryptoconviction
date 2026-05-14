"""
Token registry — single source of truth for the assets under coverage.

Each entry maps a symbol to its canonical metadata: name, chain, contract address
(if applicable), CoinGecko id (for market data), and DefiLlama protocol slug
(if applicable, for revenue/TVL data). Agents use this registry to know where
to pull each token's on-chain and market data from.

Add new tokens here when expanding coverage. None of the per-agent code should
hardcode a contract address.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Token:
    symbol: str
    name: str
    chain: str                    # 'ethereum' | 'base' | 'sui' | 'monero' | etc.
    coingecko_id: str             # for /coins/{id} on CoinGecko
    contract_address: Optional[str] = None   # None for native L1 assets (XMR, SUI)
    defillama_protocol: Optional[str] = None # slug on DefiLlama, e.g. 'aave'
    category: str = ""            # short tag, used by Agent 6 (moat) for peer grouping
    notes: str = ""


# ─── Initial coverage set ───────────────────────────────────────────────
# Contract addresses verified against Etherscan / BaseScan / canonical project docs.
# CoinGecko IDs verified by `/api/v3/coins/list` lookups.
# Update if a project migrates contracts.

REGISTRY: dict[str, Token] = {
    "LINK": Token(
        symbol="LINK",
        name="Chainlink",
        chain="ethereum",
        coingecko_id="chainlink",
        contract_address="0x514910771AF9Ca656af840dff83E8264EcF986CA",
        defillama_protocol="chainlink",
        category="oracle",
    ),
    "AAVE": Token(
        symbol="AAVE",
        name="Aave",
        chain="ethereum",
        coingecko_id="aave",
        contract_address="0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
        defillama_protocol="aave",
        category="lending",
    ),
    "ONDO": Token(
        symbol="ONDO",
        name="Ondo Finance",
        chain="ethereum",
        coingecko_id="ondo-finance",
        contract_address="0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3",
        defillama_protocol="ondo-finance",
        category="rwa",
    ),
    "ENA": Token(
        symbol="ENA",
        name="Ethena",
        chain="ethereum",
        coingecko_id="ethena",
        contract_address="0x57e114B691Db790C35207b2e685D4A43181e6061",
        defillama_protocol="ethena",
        category="synthetic-dollar",
    ),
    "SUI": Token(
        symbol="SUI",
        name="Sui",
        chain="sui",
        coingecko_id="sui",
        contract_address=None,                # native L1 asset
        defillama_protocol=None,
        category="l1-smart-contract",
        notes="Native L1 — supply data via Sui RPC, not Ethereum-style tooling.",
    ),
    "AERO": Token(
        symbol="AERO",
        name="Aerodrome Finance",
        chain="base",
        coingecko_id="aerodrome-finance",
        contract_address="0x940181a94A35A4569E4529A3CDfB74e38FD98631",
        defillama_protocol="aerodrome-v1",     # also has aerodrome-slipstream
        category="dex",
    ),
    "OGN": Token(
        symbol="OGN",
        name="Origin Protocol",
        chain="ethereum",
        coingecko_id="origin-protocol",
        contract_address="0x8207c1FfC5B6804F6024322CcF34F29c3541Ae26",
        defillama_protocol="origin-defi",
        category="lst-aggregator",
    ),
    "NMR": Token(
        symbol="NMR",
        name="Numeraire",
        chain="ethereum",
        coingecko_id="numeraire",
        contract_address="0x1776e1F26f98b1A5dF9cD347953a26dd3Cb46671",
        defillama_protocol=None,                # not on DefiLlama
        category="data-science-tournament",
    ),
    "AVNT": Token(
        symbol="AVNT",
        name="Avantis",
        chain="base",
        coingecko_id="avantis",
        contract_address="0x696F9436B67233384889472Cd7cD58A6fB5DF4f1",
        defillama_protocol=None,                 # not yet on DefiLlama as of Apr 2026
        category="perp-dex",
        notes=(
            "Resolved Apr 2026 via Sonnet research. Avantis is a perpetual "
            "futures DEX on Base, launched Sep 2025. 1B fixed cap, 32% "
            "circulating, deflationary buyback-and-burn (30%→50% of fees)."
        ),
    ),
    "XMR": Token(
        symbol="XMR",
        name="Monero",
        chain="monero",
        coingecko_id="monero",
        contract_address=None,                   # native chain
        defillama_protocol=None,
        category="privacy-l1",
        notes=(
            "Monero is a standalone privacy chain. No on-chain holder analysis "
            "(by design). Tokenomics agent will rely on emission schedule + "
            "circulating supply only; on-chain agent largely skips XMR."
        ),
    ),
    "NEAR": Token(
        symbol="NEAR",
        name="NEAR Protocol",
        chain="near",
        coingecko_id="near",
        contract_address=None,
        defillama_protocol="near",
        category="layer-1",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract).",
    ),
    "TON": Token(
        symbol="TON",
        name="Toncoin",
        chain="ton",
        coingecko_id="the-open-network",
        contract_address="EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c",
        defillama_protocol="ton",
        category="layer-1",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer. Manually corrected at commit time.",
    ),
    "LUNC": Token(
        symbol="LUNC",
        name="Terra Classic",
        chain="terra-classic",
        coingecko_id="terra-luna",
        contract_address=None,
        defillama_protocol=None,
        category="layer-1",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract). Manually corrected at commit time.",
    ),
    "NOT": Token(
        symbol="NOT",
        name="Notcoin",
        chain="ton",
        coingecko_id="notcoin",
        contract_address="EQAvlWFDxGF2lXm67y4yzC17wYKD9A0guwPkMs1gOsM__NOT",
        defillama_protocol=None,
        category="telegram-game",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer.",
    ),
    "AVAX": Token(
        symbol="AVAX",
        name="Avalanche",
        chain="avalanche",
        coingecko_id="avalanche-2",
        contract_address=None,
        defillama_protocol=None,
        category="layer-1",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract). Manually corrected at commit time.",
    ),
    "DOGE": Token(
        symbol="DOGE",
        name="Dogecoin",
        chain="dogecoin",
        coingecko_id="dogecoin",
        contract_address=None,
        defillama_protocol="dogecoin",
        category="meme",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract).",
    ),
    "DYDX": Token(
        symbol="DYDX",
        name="dYdX",
        chain="cosmos",
        coingecko_id="dydx-chain",
        contract_address="ibc/831F0B1BBB1D08A2B75311892876D71565478C532967545476DF4C2D7492E48C",
        defillama_protocol="dydx-v4",
        category="defi",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer.",
    ),
    "HYPE": Token(
        symbol="HYPE",
        name="Hyperliquid",
        chain="hyperliquid",
        coingecko_id="hyperliquid",
        contract_address="0x0d01dc56dcaaca66ad901c959b4011ec",
        defillama_protocol=None,
        category="defi-perps",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer. Manually corrected at commit time.",
    ),
    "ORDI": Token(
        symbol="ORDI",
        name="Ordinals",
        chain="ordinals",
        coingecko_id="ordinals",
        contract_address="b61b0172d95e266c18aea0c624db987e971a5d6d4ebc2aaed85da4642d635735i0",
        defillama_protocol=None,
        category="btc-ordinals",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer.",
    ),
    "ADA": Token(
        symbol="ADA",
        name="Cardano",
        chain="cardano",
        coingecko_id="cardano",
        contract_address=None,
        defillama_protocol="cardano",
        category="layer-1",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract).",
    ),
    "STX": Token(
        symbol="STX",
        name="Stacks",
        chain="stacks",
        coingecko_id="blockstack",
        contract_address=None,
        defillama_protocol=None,
        category="layer-2",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract). Manually corrected at commit time.",
    ),
    "BCH": Token(
        symbol="BCH",
        name="Bitcoin Cash",
        chain="bitcoin-cash",
        coingecko_id="bitcoin-cash",
        contract_address=None,
        defillama_protocol=None,
        category="bitcoin-fork",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract).",
    ),
    "MORPHO": Token(
        symbol="MORPHO",
        name="Morpho",
        chain="ethereum",
        coingecko_id="morpho",
        contract_address="0x58d97b57bb95320f9a05dc918aef65434969c2b2",
        defillama_protocol="morpho-blue",
        category="defi-lending",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer.",
    ),
    "W": Token(
        symbol="W",
        name="Wormhole",
        chain="solana",
        coingecko_id="wormhole",
        contract_address="85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ",
        defillama_protocol="portal",
        category="bridge",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer.",
    ),
    "TRX": Token(
        symbol="TRX",
        name="Tron",
        chain="tron",
        coingecko_id="tron",
        contract_address=None,
        defillama_protocol="tron",
        category="layer-1",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract).",
    ),
    "XRP": Token(
        symbol="XRP",
        name="XRP",
        chain="ripple",
        coingecko_id="ripple",
        contract_address=None,
        defillama_protocol=None,
        category="layer-1",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract).",
    ),
    "ORCA": Token(
        symbol="ORCA",
        name="Orca",
        chain="solana",
        coingecko_id="orca",
        contract_address="orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
        defillama_protocol="orca-dex",
        category="defi-dex",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer.",
    ),
    "CFX": Token(
        symbol="CFX",
        name="Conflux Token",
        chain="conflux",
        coingecko_id="conflux-token",
        contract_address=None,
        defillama_protocol=None,
        category="layer-1",
        notes="Resolved 2026-05-06 via CoinGecko (native L1, no contract). Manually corrected at commit time.",
    ),
    "RNDR": Token(
        symbol="RNDR",
        name="Render Network",
        chain="ethereum",
        coingecko_id="render-token",
        contract_address="0x6de037ef9ad2725eb40118bb1702ebb27e4aeb24",
        defillama_protocol=None,
        category="ai-infra",
        notes="Resolved 2026-05-06 via CoinGecko + DefiLlama; contract verified via explorer.",
    ),
    "UNI": Token(
        symbol="UNI",
        name="Uniswap",
        chain="ethereum",
        coingecko_id="uniswap",
        contract_address="0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
        defillama_protocol="uniswap",
        category="dex",
        notes="Added 2026-05-14 for top-15 refresh batch.",
    ),
    "XLM": Token(
        symbol="XLM",
        name="Stellar",
        chain="stellar",
        coingecko_id="stellar",
        contract_address=None,
        defillama_protocol=None,
        category="layer-1",
        notes="Added 2026-05-14 for top-15 refresh batch (native L1, no contract).",
    ),
}


def all_symbols() -> list[str]:
    return list(REGISTRY.keys())


def get(symbol: str) -> Token:
    sym = symbol.upper()
    if sym not in REGISTRY:
        raise KeyError(f"Unknown token: {symbol}. Add to shared/tokens.py.")
    return REGISTRY[sym]


def is_evm(symbol: str) -> bool:
    """True if the token has an EVM contract address (Ethereum/Base/etc)."""
    t = get(symbol)
    return t.chain in {"ethereum", "base", "arbitrum", "optimism", "polygon"} and t.contract_address is not None
