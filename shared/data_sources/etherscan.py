"""
Etherscan / BaseScan free-tier client.

Used by Agents 1 (token contract supply) and 4 (on-chain activity).
Free tier: 5 calls/sec with API key, 1/sec without.

Top-50 holder snapshots are NOT in the free Etherscan API — they're behind
the Pro plan ($199/mo). For Agent 1 we work around this by either:
  (a) scraping the public holder list page (legal grey, fragile, not done here),
  (b) calling Sonnet via research() to summarize the public Etherscan UI when
      the user opens it themselves,
  (c) deferring concentration analysis and flagging UNKNOWN.

We default to (c) and surface NOT_AVAILABLE_FREE_TIER. Agent 1 will pull what
it can: total supply, contract creator, contract age.
"""
from __future__ import annotations

import os
import requests

BASES = {
    "ethereum": "https://api.etherscan.io/api",
    "base":     "https://api.basescan.org/api",
}


def _key(chain: str) -> str:
    if chain == "base":
        return os.getenv("BASESCAN_API_KEY", "").strip()
    return os.getenv("ETHERSCAN_API_KEY", "").strip()


def total_supply(chain: str, contract: str) -> int | None:
    base = BASES.get(chain)
    if not base:
        return None
    r = requests.get(
        base,
        params={
            "module": "stats",
            "action": "tokensupply",
            "contractaddress": contract,
            "apikey": _key(chain) or "YourApiKeyToken",
        },
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "1":
        return None
    return int(j["result"])


def contract_metadata(chain: str, contract: str) -> dict:
    """Returns creator address + creation tx if available."""
    base = BASES.get(chain)
    if not base:
        return {}
    r = requests.get(
        base,
        params={
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": contract,
            "apikey": _key(chain) or "YourApiKeyToken",
        },
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "1" or not j.get("result"):
        return {}
    return j["result"][0]
