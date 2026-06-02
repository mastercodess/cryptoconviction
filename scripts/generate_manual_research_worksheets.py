"""
Generate per-token manual-research worksheets.

Problem this addresses:
  shared/schemas.py forces every token through one Pydantic schema written
  for DeFi lending. AAVE fits fine; LINK (oracle), XMR (privacy L1), ORDI
  (BTC inscription), DOGE (meme), ENA (synthetic dollar) etc. measure
  different things with the same fields, or have null where the only
  relevant thesis-signal lives.

  This module emits a tailored worksheet per token capturing the
  category-specific datapoints the uniform schema misses. The worksheets
  are JSON with empty `value` and `notes` slots for the user to fill in
  manually after retrieving from each gap's documented source.

Output: data/manual_research/{SYMBOL}.json — one per registry token.

Re-run safely:
  python3 -m scripts.generate_manual_research_worksheets

  Existing user-entered values are preserved (values + notes carry over
  when a worksheet is regenerated). Template changes (new gaps, updated
  source URLs, updated verdict-impact text) overwrite freely.
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import tokens  # noqa: E402
from scripts.manual_research_delta_rules import get_rules  # noqa: E402

REPORTS_DIR = _REPO_ROOT / "reports"
OUTPUT_DIR = _REPO_ROOT / "data" / "manual_research"
TEMPLATE_VERSION = "2026-05-18"

# ──────────────────────────────────────────────────────────────────────
# TOKEN → CATEGORY MAPPING (drives which template gets applied)
# ──────────────────────────────────────────────────────────────────────

TOKEN_CATEGORY: dict[str, str] = {
    "AAVE": "defi-lending",
    "MORPHO": "defi-lending",
    "UNI": "defi-dex",
    "AERO": "defi-dex",
    "ORCA": "defi-dex",
    "DYDX": "defi-perps",
    "AVNT": "defi-perps",
    "HYPE": "defi-perps",
    "LINK": "oracle",
    "ONDO": "rwa",
    "ENA": "synthetic-dollar",
    "NEAR": "l1-general",
    "AVAX": "l1-general",
    "ADA": "l1-general",
    "TRX": "l1-general",
    "XRP": "l1-general",
    "XLM": "l1-general",
    "CFX": "l1-general",
    "TON": "l1-general",
    "SUI": "l1-general",
    "XMR": "l1-privacy",
    "STX": "l2",
    "BCH": "btc-fork",
    "ORDI": "btc-ordinals",
    "DOGE": "meme",
    "NOT": "telegram-game",
    "RNDR": "ai-infra",
    "NMR": "tournament",
    "OGN": "lst-aggregator",
    "W": "bridge",
    "LUNC": "failed-l1",
}

# ──────────────────────────────────────────────────────────────────────
# CATEGORY TEMPLATES
# ──────────────────────────────────────────────────────────────────────
#
# Each template captures:
#   - schema_fit_overall: how well shared/schemas.py serves this token class
#   - category_specific_notes: what the schema gets wrong by category
#   - gaps: list of fields the schema doesn't capture or measures wrongly
#
# Each gap entry includes:
#   - field: snake_case name for the missing datapoint
#   - what_it_measures: one-line definition
#   - why_schema_misses_it: which agent/field falls short and why
#   - where_to_retrieve: list of URLs / specific instructions
#   - verdict_impact_if_filled: how knowing this would change conviction
#
# Per-token additions: see TOKEN_ADDENDA at bottom.

CATEGORY_TEMPLATES: dict[str, dict[str, Any]] = {
    "defi-lending": {
        "schema_fit_overall": "GOOD",
        "category_specific_notes": (
            "RevenueOutput and TokenomicsOutput fit lending protocols well. "
            "Gaps are around bad-debt accumulation (not in any schema), "
            "borrower concentration (TokenomicsOutput captures HOLDER "
            "concentration, not BORROWER), and forward unlock cliffs beyond "
            "the 90-day window the schema reports."
        ),
        "gaps": [
            {
                "field": "real_yield_trailing_90d_pct",
                "what_it_measures": (
                    "Realized fee distribution to stakers / staked-token value, "
                    "trailing 90 days. Pure fees, no inflation."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.real_yield_apr is forward-projected from "
                    "current revenue * staker share, not realized."
                ),
                "where_to_retrieve": [
                    "Protocol governance forum (look for 'Quarterly Buyback Execution Report')",
                    "DefiLlama 'Fees' tab for trailing 90d revenue",
                    "Token Terminal (paid tier) for staker reward attribution",
                ],
                "verdict_impact_if_filled": (
                    "If realized < 2/3 of projected for 2 consecutive quarters → "
                    "revenue score downgraded one band (fee-switch durability lost). "
                    "If > 1.2x projected → upgrade (real-yield thesis strengthened)."
                ),
            },
            {
                "field": "top_3_borrower_concentration_pct",
                "what_it_measures": (
                    "Sum of largest 3 single-wallet borrows as % of total borrows."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput.top10_holding_pct measures TOKEN holders, "
                    "not protocol BORROWERS. The bad-debt risk lives in borrowers."
                ),
                "where_to_retrieve": [
                    "Protocol's risk dashboard (e.g., Chaos Labs, Block Analitica)",
                    "Dune Analytics protocol-specific queries",
                    "Forum risk reports",
                ],
                "verdict_impact_if_filled": (
                    "Top-3 > 30% on any single market → security score -1 tier "
                    "(CRV-2022 analog). Top-3 < 15% → security score confirmed."
                ),
            },
            {
                "field": "total_bad_debt_outstanding_usd",
                "what_it_measures": (
                    "Cumulative socialized debt across all protocol markets, "
                    "outstanding (not yet repaid)."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput.incident_history_severity is a discrete label "
                    "(MINOR/MODERATE/etc.) — doesn't capture cumulative bad-debt $$."
                ),
                "where_to_retrieve": [
                    "Governance forum 'bad debt' / 'shortfall event' search",
                    "Risk dashboard 'protocol losses' tab",
                ],
                "verdict_impact_if_filled": (
                    "Bad debt > 1% of TVL → security score -1. < 0.1% → confirmed."
                ),
            },
            {
                "field": "forward_unlock_cliff_3_to_12mo_pct",
                "what_it_measures": (
                    "% of supply unlocking in months 3-12 from now (the window "
                    "OUTSIDE the 90d the schema already reports)."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput.unlock_pressure_next_90d_pct only sees the "
                    "first 90d. A 25% unlock on month 4 is invisible to the schema."
                ),
                "where_to_retrieve": [
                    "https://token.unlocks.app/",
                    "https://cryptorank.io/upcoming-token-unlocks",
                    "Project's docs / vesting page",
                ],
                "verdict_impact_if_filled": (
                    "Cliff > 25% in months 3-12 → tokenomics score -1 band. "
                    "< 10% → confirmed."
                ),
            },
            {
                "field": "bridge_dependency_pct_tvl",
                "what_it_measures": (
                    "% of protocol TVL held on chains reached via 3rd-party "
                    "bridge (LayerZero, CCIP, Axelar, Wormhole, native), "
                    "weighted by bridge security profile."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput.single_points_of_failure is a free-text "
                    "list. Not quantified, not comparable across protocols."
                ),
                "where_to_retrieve": [
                    "DefiLlama protocol-by-chain TVL split",
                    "Bridge security ratings (L2BEAT for L2s)",
                ],
                "verdict_impact_if_filled": (
                    ">30% on non-canonical bridges → security -1. <5% → confirmed."
                ),
            },
        ],
    },
    "defi-dex": {
        "schema_fit_overall": "PARTIAL",
        "category_specific_notes": (
            "RevenueOutput captures fees but misses wash-trade signal, LP "
            "retention curves (the durability test), and whether the 'fee "
            "switch' is actually distributing to token holders or just "
            "promised on roadmap. P/S without wash adjustment over-rates DEXs."
        ),
        "gaps": [
            {
                "field": "wash_volume_ratio_30d",
                "what_it_measures": (
                    "Estimated wash-trade volume / total reported volume, "
                    "trailing 30 days."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.annualized_revenue_usd takes reported "
                    "volume * fee rate. Wash volume produces fees that aren't "
                    "durable — they vanish when incentives stop."
                ),
                "where_to_retrieve": [
                    "Messari 'Real Volume' metric (where available)",
                    "CER.live wash-trade detection",
                    "Manual: compare DEX volume to CEX volume for same pair; "
                    "ratio > 0.5 suggests inflated DEX volume",
                ],
                "verdict_impact_if_filled": (
                    "Wash > 40% → revenue score downgraded one band. <10% → confirmed."
                ),
            },
            {
                "field": "lp_retention_30_60_90d_pct",
                "what_it_measures": (
                    "% of LPs from N days ago that are still active. Curve over "
                    "30/60/90 day windows."
                ),
                "why_schema_misses_it": (
                    "OnChainOutput.retention_health_grade (A-F) is too coarse "
                    "and applies to token holders, not LPs."
                ),
                "where_to_retrieve": [
                    "Dune Analytics: protocol-specific LP retention queries",
                    "Project's analytics page",
                ],
                "verdict_impact_if_filled": (
                    "90d LP retention < 25% → moat score -1 (LPs are mercenaries). "
                    "> 50% → confirmed durable."
                ),
            },
            {
                "field": "volume_durability_without_incentives_pct",
                "what_it_measures": (
                    "% of current volume that would remain if emissions = 0. "
                    "Inferred from historical events when emissions paused."
                ),
                "why_schema_misses_it": (
                    "No agent quantifies 'subsidy intensity' on DEX volume."
                ),
                "where_to_retrieve": [
                    "Historical volume around emission-rate change events",
                    "Comparison to non-incentivized DEX volume",
                ],
                "verdict_impact_if_filled": (
                    "< 30% durable → moat downgrade (mercenary liquidity). "
                    "> 70% → real organic demand."
                ),
            },
            {
                "field": "fee_switch_realized_distribution_usd_90d",
                "what_it_measures": (
                    "Actual USD distributed to token holders/stakers in last 90d "
                    "(zero if fee switch off)."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.real_yield_apr can be non-zero even when the "
                    "fee switch is off — modeled on theoretical share, not realized."
                ),
                "where_to_retrieve": [
                    "Governance forum (search 'fee switch', 'rev share', 'buyback')",
                    "On-chain token-distribution transactions to staking contracts",
                ],
                "verdict_impact_if_filled": (
                    "Realized = $0 with promised switch → revenue -1 band. "
                    "Realized > $10M/90d on a $1B mcap → real yield thesis confirmed."
                ),
            },
        ],
    },
    "defi-perps": {
        "schema_fit_overall": "PARTIAL",
        "category_specific_notes": (
            "Perp DEXs need perp-specific risk metrics: insurance fund vs. OI, "
            "real-vs-wash volume, funding-rate competitiveness. The uniform "
            "schema treats them like spot DEXs and misses the blowup risk."
        ),
        "gaps": [
            {
                "field": "real_volume_vs_wash_30d_pct",
                "what_it_measures": (
                    "Estimated real / total volume on the perp venue, 30d."
                ),
                "why_schema_misses_it": (
                    "Perp volume is the easiest crypto metric to inflate. "
                    "RevenueOutput just takes reported numbers."
                ),
                "where_to_retrieve": [
                    "Coinglass derivatives ranking + venue OI to volume ratio",
                    "Compare to Binance/Bybit volume for same pairs",
                ],
                "verdict_impact_if_filled": (
                    "Real volume < 30% of reported → revenue downgrade -1. "
                    "> 70% → revenue confirmed."
                ),
            },
            {
                "field": "insurance_fund_usd_vs_peak_oi_usd_ratio",
                "what_it_measures": (
                    "Current insurance fund size / 24h peak open interest. "
                    "Ratio of cushion to extreme exposure."
                ),
                "why_schema_misses_it": (
                    "Nowhere in the schema. SecurityOutput has audit metrics "
                    "but doesn't quantify economic-blowup buffer."
                ),
                "where_to_retrieve": [
                    "Venue's stats / transparency page",
                    "Coinglass insurance fund tracker (for major venues)",
                ],
                "verdict_impact_if_filled": (
                    "Ratio < 0.5% → security -1 (one bad cascade = insolvency). "
                    "> 2% → security confirmed."
                ),
            },
            {
                "field": "funding_rate_competitiveness_vs_binance_bps",
                "what_it_measures": (
                    "Trailing 7d average taker funding spread vs. Binance perps "
                    "on the same major pairs (BTC, ETH, SOL)."
                ),
                "why_schema_misses_it": (
                    "MoatOutput.competitive_threat is a label, not a metric. "
                    "Funding spread = quantitative test of venue's edge."
                ),
                "where_to_retrieve": [
                    "Coinglass funding rate dashboard",
                    "Venue's own funding history",
                ],
                "verdict_impact_if_filled": (
                    "Funding > Binance + 5bps consistently → moat -1 (price-takers). "
                    "< Binance → moat confirmed (price-makers)."
                ),
            },
            {
                "field": "maker_taker_fee_distribution_breakdown",
                "what_it_measures": (
                    "Where fees go: % to LPs/stakers, % to treasury, % to team. "
                    "Distinct from a single 'real_yield_apr' number."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput collapses this into one number; user can't "
                    "tell whether 'real yield' is durable or extractive."
                ),
                "where_to_retrieve": [
                    "Tokenomics documentation",
                    "Governance proposals on fee distribution",
                ],
                "verdict_impact_if_filled": (
                    "> 70% to team/treasury → revenue downgrade -1. "
                    "> 70% to token holders/LPs → revenue confirmed."
                ),
            },
        ],
    },
    "oracle": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "Oracles have no TVL, no protocol revenue in the DeFi sense, no "
            "P/S. RevenueOutput is essentially null. The moat = TVS (Total "
            "Value Secured), staking participation, and integration count "
            "— none of which the schema captures. Replace RevenueOutput "
            "interpretation with this overlay."
        ),
        "gaps": [
            {
                "field": "tvs_total_value_secured_usd",
                "what_it_measures": (
                    "Aggregate USD value the oracle's price feeds secure across "
                    "all integrations. THE oracle-economy moat metric."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.tvl_usd assumes the protocol custodies "
                    "user assets. Oracles don't — they secure other protocols' "
                    "assets. Different concept entirely."
                ),
                "where_to_retrieve": [
                    "Chainlink Labs quarterly reports",
                    "https://chain.link/economics",
                    "DefiLlama oracle category (limited coverage)",
                ],
                "verdict_impact_if_filled": (
                    "TVS growth flat YoY → moat downgrade. "
                    "TVS > $20B and rising → moat confirmed Tier 1."
                ),
            },
            {
                "field": "staking_v02_participation_pct",
                "what_it_measures": (
                    "% of circulating LINK staked in the v0.2 system."
                ),
                "why_schema_misses_it": (
                    "OnChainOutput.holder_quality_rating is a 1-10 score with "
                    "no specific staking participation measurement."
                ),
                "where_to_retrieve": [
                    "https://stake.chain.link/",
                    "Chainlink Labs reports",
                ],
                "verdict_impact_if_filled": (
                    "< 15% → tokenomics downgrade (low alignment). "
                    "> 30% → tokenomics confirmed strong."
                ),
            },
            {
                "field": "ccip_message_volume_quarterly_count",
                "what_it_measures": (
                    "# of CCIP cross-chain messages routed per quarter. The "
                    "real growth metric for Chainlink's forward thesis."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.growth_trend is qualitative. CCIP volume "
                    "growth is the specific datapoint that drives the thesis."
                ),
                "where_to_retrieve": [
                    "https://ccip.chain.link/ explorer",
                    "Chainlink Labs blog quarterly reports",
                ],
                "verdict_impact_if_filled": (
                    "QoQ growth < 20% → revenue downgrade. "
                    "QoQ > 100% → revenue confirmed accelerating."
                ),
            },
            {
                "field": "direct_link_token_consumption_rate_per_quarter",
                "what_it_measures": (
                    "LINK tokens burned/consumed in oracle requests per quarter "
                    "(if staking v0.2 introduced token sinks)."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput captures supply/unlocks but not real-time "
                    "consumption as token sink."
                ),
                "where_to_retrieve": [
                    "Chainlink Labs reserve dashboards",
                    "On-chain LINK transfer analysis (Arkham, Etherscan)",
                ],
                "verdict_impact_if_filled": (
                    "Consumption < emission rate → tokenomics neutral. "
                    "Consumption > emission → deflationary, tokenomics +1."
                ),
            },
            {
                "field": "chainlink_labs_reserve_outflow_30d_link",
                "what_it_measures": (
                    "Net LINK outflow from Chainlink Labs wallets, trailing 30d. "
                    "Proxy for team-side sell pressure."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput.unlock_pressure_next_90d_pct is forward-"
                    "looking; this is backward-looking on actual outflows."
                ),
                "where_to_retrieve": [
                    "Arkham custom watchlist on Chainlink Labs known addresses",
                    "Nansen labeled Chainlink wallets (paid)",
                ],
                "verdict_impact_if_filled": (
                    "Outflow > 20M LINK/30d → tokenomics -1 (heavy distribution). "
                    "Net inflow or stable → tokenomics confirmed."
                ),
            },
        ],
    },
    "rwa": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "RWA is the most schema-misaligned category. The thesis is "
            "compliance + AUM + custody — none in the schema. RevenueOutput "
            "captures on-chain protocol revenue but misses the off-chain "
            "T-bill yield that backs the entire product. SecurityOutput "
            "audits smart contracts but misses the legal/regulatory layer."
        ),
        "gaps": [
            {
                "field": "underlying_aum_usd",
                "what_it_measures": (
                    "Off-chain US Treasury / money-market holdings backing "
                    "the on-chain tokens (OUSG, USDY, etc.). THE thesis."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.tvl_usd captures on-chain TVL only. RWA "
                    "value lives in the off-chain backing."
                ),
                "where_to_retrieve": [
                    "Ondo monthly transparency reports",
                    "Broker-dealer custody statements (sometimes published)",
                    "Etherscan total supply * NAV per token",
                ],
                "verdict_impact_if_filled": (
                    "AUM growth flat → revenue downgrade. "
                    "AUM > $1B and growing > 20% QoQ → revenue confirmed accelerating."
                ),
            },
            {
                "field": "custodial_relationships_list",
                "what_it_measures": (
                    "Counterparties holding the off-chain underlying: prime "
                    "brokers, custodians, transfer agents."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput focuses on smart contracts. RWA primary "
                    "security = custodian solvency + segregation of assets."
                ),
                "where_to_retrieve": [
                    "Ondo terms of service / offering circular",
                    "SEC filings (if any)",
                ],
                "verdict_impact_if_filled": (
                    "Single custodian → security -1. "
                    "Multiple T-1 custodians (BNY, State Street, etc.) → security confirmed."
                ),
            },
            {
                "field": "sec_letter_rulings_no_action_count",
                "what_it_measures": (
                    "Number of SEC no-action letters / private rulings the "
                    "issuer holds confirming regulatory status."
                ),
                "why_schema_misses_it": (
                    "TeamOutput.legal_exposure_flag is a boolean; doesn't "
                    "capture positive regulatory standing."
                ),
                "where_to_retrieve": [
                    "https://www.sec.gov/cgi-bin/browse-edgar (search filings)",
                    "Issuer's regulatory disclosures",
                ],
                "verdict_impact_if_filled": (
                    "Zero rulings + active SEC enforcement risk → team -1. "
                    "Multiple no-action letters → team confirmed Tier 1."
                ),
            },
            {
                "field": "redemption_gate_history_count",
                "what_it_measures": (
                    "Count of redemption-pause incidents in product history "
                    "(per token like USDY, OUSG)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. USDC depegged in SVB; USDY could face "
                    "similar Treasury-market shock."
                ),
                "where_to_retrieve": [
                    "Ondo transparency / incident reports",
                    "USDY peg history on CoinGecko / DEX charts",
                ],
                "verdict_impact_if_filled": (
                    "Any gate event → security -1. Zero → confirmed."
                ),
            },
            {
                "field": "geographic_restrictions_eligible_jurisdictions_count",
                "what_it_measures": (
                    "# of jurisdictions where the product is offered to retail "
                    "(vs. accredited-only)."
                ),
                "why_schema_misses_it": (
                    "MoatOutput.regulatory_relative_risk is qualitative."
                ),
                "where_to_retrieve": [
                    "Ondo offering documents / docs.ondo.finance",
                    "On-chain whitelisting contract (if applicable)",
                ],
                "verdict_impact_if_filled": (
                    "<5 jurisdictions or US-restricted → moat -1 (limited TAM). "
                    "Global accessibility → moat confirmed."
                ),
            },
        ],
    },
    "synthetic-dollar": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "USDe's stability and ENA's value derive entirely from a "
            "funding-rate carry trade. None of the schema fields encode "
            "this — RevenueOutput treats 'real yield' as static when in "
            "reality it's a regime-dependent carry that flips negative in "
            "spot bear markets."
        ),
        "gaps": [
            {
                "field": "funding_rate_carry_90d_pct",
                "what_it_measures": (
                    "Annualized trailing 90d carry on the delta-neutral "
                    "perp-short position backing USDe."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.real_yield_apr doesn't distinguish carry "
                    "yield from fee yield. Carry is regime-dependent."
                ),
                "where_to_retrieve": [
                    "https://ethena.fi/dashboard",
                    "Coinglass aggregated funding history",
                ],
                "verdict_impact_if_filled": (
                    "Carry < 5% APR for 30d → revenue downgrade -1 band. "
                    "Carry > 15% APR → revenue confirmed."
                ),
            },
            {
                "field": "backing_composition_collateral_split_pct",
                "what_it_measures": (
                    "% of backing in each: stETH, BTC, ETH-perp shorts, USDT, "
                    "T-bills (USDtb), cash."
                ),
                "why_schema_misses_it": (
                    "Not modeled. The composition determines tail-risk (e.g., "
                    "stETH depeg + funding flip = double whammy)."
                ),
                "where_to_retrieve": [
                    "Ethena transparency page",
                    "Chaos Labs Ethena risk reports",
                ],
                "verdict_impact_if_filled": (
                    "> 40% stETH or correlated long → security -1. "
                    "Diversified across uncorrelated collateral → confirmed."
                ),
            },
            {
                "field": "insurance_fund_vs_usde_supply_ratio_pct",
                "what_it_measures": (
                    "Insurance fund USD / total USDe outstanding."
                ),
                "why_schema_misses_it": (
                    "Not modeled. This is the depeg cushion."
                ),
                "where_to_retrieve": [
                    "Ethena reserve fund disclosure",
                    "Monthly transparency report",
                ],
                "verdict_impact_if_filled": (
                    "< 1% → security -1. > 3% → security confirmed."
                ),
            },
            {
                "field": "usde_peg_deviation_30d_max_bps",
                "what_it_measures": (
                    "Max deviation of USDe from $1 in last 30d, in bps."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Trust signal for the entire mechanism."
                ),
                "where_to_retrieve": [
                    "USDe / USDC curve LP charts",
                    "CoinGecko USDe history",
                ],
                "verdict_impact_if_filled": (
                    "Max > 100bps in 30d → security -1. < 25bps → confirmed."
                ),
            },
            {
                "field": "ena_unlock_schedule_12mo_pct",
                "what_it_measures": (
                    "% of total ENA supply unlocking next 12 months."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput captures 90d only."
                ),
                "where_to_retrieve": [
                    "https://token.unlocks.app/ethena",
                    "Ethena foundation tokenomics page",
                ],
                "verdict_impact_if_filled": (
                    "> 25% unlocking 12mo → tokenomics -1. < 10% → confirmed."
                ),
            },
        ],
    },
    "l1-general": {
        "schema_fit_overall": "PARTIAL",
        "category_specific_notes": (
            "L1 chains have radically different security models from smart-"
            "contract protocols (validator decentralization, not audits) and "
            "different revenue dynamics (most 'real yield' is inflationary "
            "subsidy, not fee income). The schema flags audits as gold-standard "
            "and treats inflation neutrally — both wrong for L1s."
        ),
        "gaps": [
            {
                "field": "active_validators_count",
                "what_it_measures": (
                    "Live, signing validators on the chain right now."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput.single_points_of_failure is a free-text list."
                ),
                "where_to_retrieve": [
                    "Chain's official validator dashboard",
                    "https://www.stakingrewards.com/asset/{coingecko-id}",
                ],
                "verdict_impact_if_filled": (
                    "Cross-reference with Nakamoto coefficient below."
                ),
            },
            {
                "field": "nakamoto_coefficient",
                "what_it_measures": (
                    "Minimum # of entities required to halt or censor the chain "
                    "(typically 1/3 or 1/2 of stake, depending on consensus)."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput.security_tier is a 1-5 label; Nakamoto "
                    "coefficient is the L1-specific quantitative test."
                ),
                "where_to_retrieve": [
                    "https://nakaflow.io/",
                    "https://nakamoto.report/",
                    "Chain-specific dashboards (Sui Foundation, Avascan, etc.)",
                ],
                "verdict_impact_if_filled": (
                    "NC < 5 → security tier downgraded -1. "
                    "NC > 20 → security tier confirmed."
                ),
            },
            {
                "field": "real_fee_revenue_vs_subsidy_pct",
                "what_it_measures": (
                    "% of staker rewards from actual transaction fees vs. "
                    "inflationary subsidy. (e.g. SUI is ~92% subsidy / 8% fees.)"
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.real_yield_apr and inflationary_yield_apr "
                    "are separately reported but often filled inconsistently."
                ),
                "where_to_retrieve": [
                    "Token Terminal L1 chain revenue dashboards",
                    "https://www.stakingrewards.com/asset/{coingecko-id}",
                    "Chain's tokenomics documentation (look for subsidy schedule)",
                ],
                "verdict_impact_if_filled": (
                    "< 20% fees → revenue downgrade -1 (subsidies will deplete). "
                    "> 50% fees → revenue confirmed (sustainable)."
                ),
            },
            {
                "field": "ecosystem_grant_runway_months",
                "what_it_measures": (
                    "Foundation treasury USD / current monthly grant spend. "
                    "How long until the ecosystem grants stop."
                ),
                "why_schema_misses_it": (
                    "TeamOutput captures team credibility but not foundation "
                    "treasury runway, which determines L1 dev-ecosystem durability."
                ),
                "where_to_retrieve": [
                    "Foundation annual reports / disclosures",
                    "Chain explorer foundation address balance",
                    "Recent grant announcements (estimate spend rate)",
                ],
                "verdict_impact_if_filled": (
                    "Runway < 12mo → team/moat -1. > 36mo → confirmed."
                ),
            },
            {
                "field": "top_application_dau",
                "what_it_measures": (
                    "DAU of the single biggest application on the chain "
                    "(not the chain's RPC-level DAU which can include bots)."
                ),
                "why_schema_misses_it": (
                    "OnChainOutput.organic_activity_score is 1-10; this is the "
                    "specific datapoint behind it."
                ),
                "where_to_retrieve": [
                    "https://dappradar.com/",
                    "https://tokenterminal.com/",
                    "Chain-specific analytics",
                ],
                "verdict_impact_if_filled": (
                    "Top app DAU < 5k → onchain -1 (no organic adoption). "
                    "> 100k → onchain confirmed."
                ),
            },
        ],
    },
    "l1-privacy": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "By design, Monero (and similar privacy L1s) cannot expose holder "
            "concentration, smart-money flows, or holder cohorts. The On-Chain "
            "agent will always return placeholders. Security = ASIC resistance "
            "+ RandomX hashrate + ring-signature integrity — totally different "
            "from smart-contract audit coverage. The schema's most important "
            "fields are structurally unfillable here."
        ),
        "gaps": [
            {
                "field": "randomx_hashrate_gh_s_current_vs_ath",
                "what_it_measures": (
                    "Current RandomX hashrate / all-time high. Proxy for "
                    "miner economics & 51% resistance."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput has no PoW hashrate field; assumes "
                    "smart-contract audit coverage as security proxy."
                ),
                "where_to_retrieve": [
                    "https://miningpoolstats.stream/monero",
                    "https://www.coinwarz.com/mining/monero/hashrate-chart",
                ],
                "verdict_impact_if_filled": (
                    "Current < 25% of ATH → security -1 (miner abandonment). "
                    "Current > 75% of ATH → security confirmed."
                ),
            },
            {
                "field": "asic_resistance_status",
                "what_it_measures": (
                    "Has anyone announced or deployed an ASIC for RandomX? "
                    "Status of the next algo-update if so."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Existential for XMR."
                ),
                "where_to_retrieve": [
                    "Monero dev mailing list / GitHub",
                    "r/Monero discussions",
                    "Bitmain / WhatsMiner announcements",
                ],
                "verdict_impact_if_filled": (
                    "ASIC announced and no algo-update planned → security -2 "
                    "(centralization existential). Resistance confirmed → no change."
                ),
            },
            {
                "field": "cex_availability_active_count",
                "what_it_measures": (
                    "Number of T-1 / T-2 CEXs still listing XMR (Binance, "
                    "Kraken, OKX, Bitfinex, Gate.io, etc.)."
                ),
                "why_schema_misses_it": (
                    "MoatOutput.regulatory_relative_risk is qualitative; the "
                    "concrete metric is delisting count."
                ),
                "where_to_retrieve": [
                    "https://www.coingecko.com/en/coins/monero (markets tab)",
                    "Monitor Binance/Kraken/OKX delisting announcements",
                ],
                "verdict_impact_if_filled": (
                    "T-1 CEX count = 0 → moat -1 (liquidity collapse). "
                    "> 3 T-1 CEXs → moat confirmed."
                ),
            },
            {
                "field": "tail_emission_effective_inflation_pct",
                "what_it_measures": (
                    "Annualized inflation from 0.6 XMR/block tail emission as "
                    "% of current circulating supply."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput.inflation_pressure_score is 1-10; this "
                    "is the underlying number behind it for XMR."
                ),
                "where_to_retrieve": [
                    "Math: (0.6 XMR * 720 blocks/day * 365) / circulating_supply",
                    "https://moneroblocks.info/",
                ],
                "verdict_impact_if_filled": (
                    "Inflation > 1.5% → tokenomics -1. < 0.8% (currently ~0.7%) → confirmed."
                ),
            },
            {
                "field": "atomic_swap_monthly_volume_usd_btc_xmr",
                "what_it_measures": (
                    "Volume of BTC↔XMR atomic swaps (Haveno, COMIT). The only "
                    "non-CEX route."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Critical given CEX delisting risk."
                ),
                "where_to_retrieve": [
                    "https://haveno.com/ statistics",
                    "COMIT network dashboards",
                ],
                "verdict_impact_if_filled": (
                    "Volume < $500k/month → moat -1 (no DEX alternative). "
                    "> $5M/month → moat confirmed (resilient to CEX delisting)."
                ),
            },
            {
                "field": "seraphis_upgrade_status",
                "what_it_measures": (
                    "Status of the Seraphis / Jamtis transaction-protocol "
                    "upgrade (next major privacy upgrade)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Major forward catalyst."
                ),
                "where_to_retrieve": [
                    "https://github.com/UkoeHB/Seraphis",
                    "Monero dev calls / mailing list",
                ],
                "verdict_impact_if_filled": (
                    "Mainnet date confirmed → catalyst noted in monitoring. "
                    "Stalled > 12mo → moat -1 (privacy parity erosion)."
                ),
            },
        ],
    },
    "l2": {
        "schema_fit_overall": "PARTIAL",
        "category_specific_notes": (
            "L2s share L1 issues (validator decentralization, fee revenue vs. "
            "subsidy) but add L2-specific dynamics: settlement frequency to "
            "the L1, bridge security, and L2-asset (e.g. sBTC) ratios."
        ),
        "gaps": [
            {
                "field": "sbtc_or_l2_bridged_asset_tvl_usd",
                "what_it_measures": (
                    "Total BTC (or other L1 asset) bridged into the L2 via "
                    "the native bridge."
                ),
                "why_schema_misses_it": (
                    "MoatOutput.network_effect_type doesn't distinguish bridged "
                    "TVL from native TVL."
                ),
                "where_to_retrieve": [
                    "https://l2beat.com/ (or chain-specific dashboard)",
                    "sBTC dashboard for Stacks",
                ],
                "verdict_impact_if_filled": (
                    "Bridged TVL growth flat → moat downgrade. "
                    "Growing > 50% QoQ → moat confirmed accelerating."
                ),
            },
            {
                "field": "stacking_yield_btc_vs_stx_paid_pct",
                "what_it_measures": (
                    "For PoX consensus chains (Stacks): % of stacking yield "
                    "paid in BTC vs. STX. BTC-paid is real yield."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.real_yield_apr can be filled with "
                    "inflationary STX rewards counted as real yield."
                ),
                "where_to_retrieve": [
                    "Stacks PoX dashboard",
                    "https://stacking.club/",
                ],
                "verdict_impact_if_filled": (
                    "< 30% BTC-paid → revenue downgrade -1. "
                    "> 70% → revenue confirmed."
                ),
            },
            {
                "field": "settlement_frequency_l1_blocks_per_anchor",
                "what_it_measures": (
                    "How often the L2 settles state to L1 (in L1 blocks). "
                    "Determines economic finality lag."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput doesn't quantify L1-anchoring cadence."
                ),
                "where_to_retrieve": [
                    "L2 documentation / L2BEAT",
                ],
                "verdict_impact_if_filled": (
                    "> 144 L1 blocks/anchor (24h+) → security -1. "
                    "< 30 → security confirmed."
                ),
            },
            {
                "field": "consensus_upgrade_status",
                "what_it_measures": (
                    "Status of the next major consensus upgrade (e.g. Stacks "
                    "Nakamoto release): nodes upgraded, schedule, risks."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Forward catalyst."
                ),
                "where_to_retrieve": [
                    "Chain's GitHub releases",
                    "Foundation blog announcements",
                ],
                "verdict_impact_if_filled": (
                    "Upgrade live → catalyst noted. "
                    "Stalled > 6mo past announced date → moat -1."
                ),
            },
        ],
    },
    "btc-fork": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "BCH-like forks have no smart contracts (so audits are not the "
            "right security frame), no protocol revenue, no foundation team "
            "in the typical sense. Security = hashrate share vs. parent chain "
            "(BTC). Utility thesis = merchant adoption + privacy features "
            "(CashFusion for BCH)."
        ),
        "gaps": [
            {
                "field": "fork_to_parent_hashrate_ratio_pct",
                "what_it_measures": (
                    "Fork's hashrate / BTC hashrate. Proximity to 51%-attack "
                    "feasibility (BTC miners can rent SHA-256 hashpower)."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput audit-focused. Forks live or die on "
                    "hashrate share, not audits."
                ),
                "where_to_retrieve": [
                    "https://coin.dance/blocks/today/btc and /bch (or fork)",
                    "https://bitinfocharts.com/comparison/hashrate-btc-bch.html",
                ],
                "verdict_impact_if_filled": (
                    "Ratio < 0.5% → security -1 (rentable attack). "
                    "> 5% → security confirmed."
                ),
            },
            {
                "field": "cashfusion_or_privacy_monthly_volume_usd",
                "what_it_measures": (
                    "Volume through CashFusion (BCH) or equivalent privacy "
                    "feature on the fork."
                ),
                "why_schema_misses_it": (
                    "Not modeled. The privacy thesis (if any) requires real volume."
                ),
                "where_to_retrieve": [
                    "https://cashfusion.org/ stats",
                    "https://fullstack.cash/cashfusion (analytics)",
                ],
                "verdict_impact_if_filled": (
                    "Volume rising → moat strengthened. "
                    "Volume < $1M/month → privacy thesis dead."
                ),
            },
            {
                "field": "merchant_acceptance_count_vs_2017_peak_pct",
                "what_it_measures": (
                    "Active merchants accepting fork payments / 2017 peak count. "
                    "(2017 = utility-thesis peak.)"
                ),
                "why_schema_misses_it": (
                    "Not modeled. Bitcoin-fork utility thesis = payments."
                ),
                "where_to_retrieve": [
                    "https://acceptbitcoin.cash/",
                    "BitPay merchant list",
                ],
                "verdict_impact_if_filled": (
                    "< 30% of peak → moat -1 (thesis decayed). "
                    "> 70% → moat confirmed."
                ),
            },
            {
                "field": "block_time_variance_30d_pct",
                "what_it_measures": (
                    "% variance in actual vs. target block time over 30d. "
                    "Proxy for difficulty-adjustment thrashing under variable hashrate."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Symptom of hashrate volatility."
                ),
                "where_to_retrieve": [
                    "Fork's block explorer (Blockchair, BCH-explorer)",
                ],
                "verdict_impact_if_filled": (
                    "Variance > 30% → security -1 (chain instability). "
                    "< 10% → confirmed."
                ),
            },
        ],
    },
    "btc-ordinals": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "ORDI is a BRC-20 token. It has NO smart contract — supply is "
            "tracked by off-chain indexers (UniSat, OKX, Magic Eden). "
            "'Security' here = whether indexers agree on supply right now. "
            "Audit coverage is meaningless. The schema confidently rates "
            "'security_tier' but for ORDI the right frame is 'indexer "
            "consensus risk' which has no equivalent field."
        ),
        "gaps": [
            {
                "field": "indexer_consensus_status",
                "what_it_measures": (
                    "Do the major BRC-20 indexers (UniSat, OKX, Magic Eden, "
                    "Geniidata) agree on circulating ORDI supply right now?"
                ),
                "why_schema_misses_it": (
                    "SecurityOutput assumes smart-contract security. BRC-20s "
                    "have no smart contract; off-chain indexer consensus IS "
                    "the security model."
                ),
                "where_to_retrieve": [
                    "https://unisat.io/brc20/ORDI",
                    "https://www.okx.com/web3/marketplace/ordinals/inscription/ORDI",
                    "https://geniidata.com/ordinals/brc20",
                ],
                "verdict_impact_if_filled": (
                    "Any indexer disagreement on supply right now → security -2 "
                    "(existential risk). Full agreement + 90d history clean → confirmed."
                ),
            },
            {
                "field": "daily_inscription_count_30d_trend",
                "what_it_measures": (
                    "New BRC-20 inscriptions/day on Bitcoin L1, 30-day moving "
                    "average. Proxy for ecosystem demand."
                ),
                "why_schema_misses_it": (
                    "OnChainOutput.organic_activity_score is 1-10 generic; "
                    "doesn't capture this category-specific metric."
                ),
                "where_to_retrieve": [
                    "https://ord.io/inscriptions",
                    "https://dune.com/dataalways/brc20",
                ],
                "verdict_impact_if_filled": (
                    "Trend declining → moat downgrade (ecosystem dying). "
                    "Trend rising → moat confirmed."
                ),
            },
            {
                "field": "marketplace_volume_30d_usd",
                "what_it_measures": (
                    "ORDI trading volume on Magic Eden + OKX (the two main "
                    "BRC-20 marketplaces), 30-day."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput is null for ORDI (no protocol revenue); "
                    "marketplace volume is the closest demand proxy."
                ),
                "where_to_retrieve": [
                    "https://magiceden.io/ordinals (token stats)",
                    "OKX inscriptions market stats",
                ],
                "verdict_impact_if_filled": (
                    "Volume < $500k/day → moat -1 (demand collapse). "
                    "> $5M/day → confirmed."
                ),
            },
            {
                "field": "binance_hot_wallet_pct_of_supply",
                "what_it_measures": (
                    "% of ORDI total supply held in Binance hot wallet."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput.top10_holding_pct may not break out "
                    "by CEX vs. user wallets — Binance hot wallet is a SPOF."
                ),
                "where_to_retrieve": [
                    "Binance proof-of-reserves disclosures",
                    "BRC-20 holder dashboards (filter by Binance hot wallet)",
                ],
                "verdict_impact_if_filled": (
                    "> 40% on Binance → tokenomics -1 (single-exchange SPOF). "
                    "< 20% → confirmed."
                ),
            },
        ],
    },
    "meme": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "Memes don't have protocol revenue, can't be 'audited' in a "
            "meaningful sense (DOGE is a UTXO chain, no smart contracts), "
            "have no team in the schema's sense (Billy Markus left in 2015). "
            "The thesis is cultural attention + merged-mining security + "
            "speculative-cycle catalyst. The schema mostly returns nulls; "
            "fill in the alt-frame here."
        ),
        "gaps": [
            {
                "field": "musk_or_top_influencer_mentions_30d_count",
                "what_it_measures": (
                    "For DOGE: Elon Musk tweets/posts mentioning DOGE in "
                    "trailing 30d. For other memes: equivalent top influencer."
                ),
                "why_schema_misses_it": (
                    "Sentiment / attention is the thesis. No agent measures it."
                ),
                "where_to_retrieve": [
                    "https://twitter.com/search (manual count)",
                    "https://socialmonitor.crypto (paid)",
                ],
                "verdict_impact_if_filled": (
                    "Zero mentions in 30d → moat -1 (catalyst absent). "
                    "Active engagement → moat confirmed."
                ),
            },
            {
                "field": "merged_mining_parent_hashrate_share_pct",
                "what_it_measures": (
                    "For DOGE: % of LTC hashrate that also mines DOGE "
                    "(merged-mining adoption). DOGE has no independent security."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Existential — DOGE's security IS LTC's."
                ),
                "where_to_retrieve": [
                    "https://coin.dance/blocks/today/doge",
                    "https://www.litecoin.com/ (merged mining stats)",
                ],
                "verdict_impact_if_filled": (
                    "Share < 60% → security -1. > 90% → confirmed."
                ),
            },
            {
                "field": "daily_organic_txn_count_30d_avg",
                "what_it_measures": (
                    "Daily on-chain transactions, 30d moving average. Excludes "
                    "mining-reward outputs."
                ),
                "why_schema_misses_it": (
                    "OnChainOutput.organic_activity_score is generic; this is "
                    "the concrete utility measurement."
                ),
                "where_to_retrieve": [
                    "https://bitinfocharts.com/comparison/transactions-doge.html",
                    "https://dogechain.info/",
                ],
                "verdict_impact_if_filled": (
                    "< 25k/day → onchain -1. > 100k/day → confirmed."
                ),
            },
            {
                "field": "payments_integration_status",
                "what_it_measures": (
                    "Is DOGE accepted at major retailers / payment platforms "
                    "(Tesla, X Payments, AMC, etc.) — status active vs. announced vs. dead."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Major catalyst."
                ),
                "where_to_retrieve": [
                    "DOGE acceptance trackers",
                    "Tesla, AMC, X official payment pages",
                ],
                "verdict_impact_if_filled": (
                    "X Payments live with DOGE → moat +1 catalyst. "
                    "All announced integrations dead → moat -1."
                ),
            },
        ],
    },
    "telegram-game": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "Notcoin's user base lives in Telegram, not on-chain. The "
            "on-chain DAU vastly under-counts the actual user activity. "
            "Tokenomics is also unusual — most supply is from gameplay "
            "(tap-to-earn) not vesting, so the schema's unlock-pressure "
            "field doesn't apply the same way."
        ),
        "gaps": [
            {
                "field": "telegram_bot_wau_count",
                "what_it_measures": (
                    "Weekly Active Users in the project's Telegram bot."
                ),
                "why_schema_misses_it": (
                    "OnChainOutput measures on-chain activity. The user base "
                    "is off-chain."
                ),
                "where_to_retrieve": [
                    "Official project blog / monthly reports",
                    "TON Apps / Telegram analytics partners",
                ],
                "verdict_impact_if_filled": (
                    "WAU < 500k → onchain -1 (audience collapsing). "
                    "> 5M → confirmed."
                ),
            },
            {
                "field": "quest_completion_rate_30d_pct",
                "what_it_measures": (
                    "% of WAU completing the weekly quest/reward cycle."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Retention proxy."
                ),
                "where_to_retrieve": [
                    "Project's monthly engagement reports",
                ],
                "verdict_impact_if_filled": (
                    "< 20% completion → retention -1. > 60% → confirmed."
                ),
            },
            {
                "field": "game_to_token_holder_retention_30d_pct",
                "what_it_measures": (
                    "% of users who earned tokens via gameplay 30 days ago "
                    "and still hold non-zero balance now."
                ),
                "why_schema_misses_it": (
                    "OnChainOutput.retention_health_grade is A-F generic; "
                    "this is the category-specific test."
                ),
                "where_to_retrieve": [
                    "TON-side wallet analysis (custom Dune-equivalent query)",
                    "Project's transparency reports",
                ],
                "verdict_impact_if_filled": (
                    "< 10% → onchain -1 (everyone dumps). > 40% → confirmed."
                ),
            },
            {
                "field": "competing_apps_wau_share_pct",
                "what_it_measures": (
                    "Project's WAU share of the Telegram tap-to-earn category "
                    "(vs. Hamster Kombat, Catizen, etc.)."
                ),
                "why_schema_misses_it": (
                    "MoatOutput.competitive_threat is a label; share data "
                    "quantifies it."
                ),
                "where_to_retrieve": [
                    "TON Apps directory rankings",
                    "Telegram bot popularity trackers",
                ],
                "verdict_impact_if_filled": (
                    "Share < 20% and declining → moat -1. > 50% → confirmed."
                ),
            },
        ],
    },
    "ai-infra": {
        "schema_fit_overall": "PARTIAL",
        "category_specific_notes": (
            "DePIN / AI infra tokens like RNDR derive value from GPU rental "
            "demand, not from on-chain protocol revenue. RevenueOutput's "
            "P/S compares apples to oranges — RNDR's denominator should be "
            "GPU-hours rendered, not protocol fees."
        ),
        "gaps": [
            {
                "field": "compute_units_rendered_monthly",
                "what_it_measures": (
                    "GPU-hours / Octane render-hours / inference-hours "
                    "consumed via the network, monthly."
                ),
                "why_schema_misses_it": (
                    "Not modeled. THE demand metric for DePIN AI tokens."
                ),
                "where_to_retrieve": [
                    "Render Network monthly transparency reports",
                    "https://rendernetwork.com/stats",
                    "io.net dashboard for IO",
                ],
                "verdict_impact_if_filled": (
                    "Trend flat → revenue downgrade. "
                    "QoQ > 30% growth → revenue confirmed accelerating."
                ),
            },
            {
                "field": "supply_side_node_count_active",
                "what_it_measures": (
                    "Number of active GPU/node operators contributing to "
                    "the network."
                ),
                "why_schema_misses_it": (
                    "MoatOutput.network_effect_type doesn't capture supply-"
                    "side scale (which is the moat for DePIN)."
                ),
                "where_to_retrieve": [
                    "Project's transparency dashboards",
                ],
                "verdict_impact_if_filled": (
                    "Nodes declining → moat -1. Growing > 20% QoQ → confirmed."
                ),
            },
            {
                "field": "migration_status_chain_consolidation_pct",
                "what_it_measures": (
                    "% of token supply migrated to the canonical chain (e.g. "
                    "RNDR ETH→SOL migration % complete)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Tokenomics overhang from un-migrated supply."
                ),
                "where_to_retrieve": [
                    "Migration contract balances on source + target chain",
                ],
                "verdict_impact_if_filled": (
                    "< 50% migrated and stalled → tokenomics -1 (fragmentation). "
                    "> 90% → confirmed unified."
                ),
            },
            {
                "field": "competing_network_share_loss_pct",
                "what_it_measures": (
                    "Estimated supply-side share lost to competing DePIN "
                    "networks (io.net, Akash, Bittensor, etc.)."
                ),
                "why_schema_misses_it": (
                    "MoatOutput.competitive_threat is a label; this is the metric."
                ),
                "where_to_retrieve": [
                    "io.net public stats",
                    "Akash analytics dashboard",
                    "Crunchbase / industry reports",
                ],
                "verdict_impact_if_filled": (
                    "Sustained share loss > 10%/quarter → moat -1. "
                    "Stable or gaining → confirmed."
                ),
            },
            {
                "field": "enterprise_partnership_active_count",
                "what_it_measures": (
                    "Active named enterprise / studio partnerships in the "
                    "last 6 months (not just announcement deals)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. B2B thesis indicator."
                ),
                "where_to_retrieve": [
                    "Project blog (count active, not just announced)",
                    "LinkedIn for case studies",
                ],
                "verdict_impact_if_filled": (
                    "Zero active → moat -1. > 5 active T-1 partnerships → confirmed."
                ),
            },
        ],
    },
    "tournament": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "Numeraire is a data-science tournament. NMR's value lives in "
            "stake-on-model-prediction → burn on bad predictions. There is "
            "no TVL, no protocol revenue in the conventional sense, no "
            "smart-contract audit risk that maps to lending-style audits. "
            "The institutional thesis is Numerai Hedge Fund's AUM."
        ),
        "gaps": [
            {
                "field": "active_model_submissions_per_round_count",
                "what_it_measures": (
                    "# of unique models submitted per weekly tournament round."
                ),
                "why_schema_misses_it": (
                    "OnChainOutput.organic_activity_score is generic; this "
                    "is the specific liquidity-for-tournament metric."
                ),
                "where_to_retrieve": [
                    "https://numer.ai/leaderboard (count active models)",
                    "Numerai forum statistics",
                ],
                "verdict_impact_if_filled": (
                    "Declining < 5k submissions → moat -1 (tournament drying up). "
                    "> 10k → confirmed."
                ),
            },
            {
                "field": "numerai_fund_aum_usd",
                "what_it_measures": (
                    "AUM of the Numerai Hedge Fund (institutional client side)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. The institutional thesis."
                ),
                "where_to_retrieve": [
                    "SEC Form ADV filings for Numerai Hedge Fund LLC",
                    "Numerai blog (semi-annual updates)",
                ],
                "verdict_impact_if_filled": (
                    "Fund AUM declining → moat -1 (institutional thesis fading). "
                    "Growing > 20% YoY → moat confirmed."
                ),
            },
            {
                "field": "net_stake_burn_vs_emission_rate_pct",
                "what_it_measures": (
                    "Net NMR change: burned-on-bad-predictions vs. emission "
                    "to good predictors. Indicates token sink mechanics."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput.inflation_pressure_score is generic; "
                    "this captures NMR's specific burn mechanism."
                ),
                "where_to_retrieve": [
                    "On-chain NMR contract burn events",
                    "Numerai weekly burn announcements",
                ],
                "verdict_impact_if_filled": (
                    "Net emission positive → tokenomics -1. "
                    "Net deflation → tokenomics confirmed."
                ),
            },
            {
                "field": "signals_product_active_users_count",
                "what_it_measures": (
                    "Active users on Numerai Signals (lower-friction product "
                    "vs. main tournament)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Secondary growth driver."
                ),
                "where_to_retrieve": [
                    "Numerai Signals leaderboard",
                ],
                "verdict_impact_if_filled": (
                    "< 500 → onchain -1 (secondary product failing). "
                    "> 2000 → confirmed growing."
                ),
            },
        ],
    },
    "lst-aggregator": {
        "schema_fit_overall": "PARTIAL",
        "category_specific_notes": (
            "Origin Protocol-style LST aggregators (OETH, OUSD, OARM) compete "
            "on yield-per-LST and the % of yield captured by token holders. "
            "The schema's RevenueOutput partially fits but doesn't break out "
            "yield by product or stratify by underlying LST mix."
        ),
        "gaps": [
            {
                "field": "product_yields_apr_by_product",
                "what_it_measures": (
                    "Current APR for each product (OETH, OUSD, OARM, etc.) "
                    "vs. naive holding the underlying."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.real_yield_apr collapses everything into one "
                    "number; needs product-level breakdown."
                ),
                "where_to_retrieve": [
                    "https://app.originprotocol.com/",
                    "DefiLlama product-level yields",
                ],
                "verdict_impact_if_filled": (
                    "Product yields < naive LST yields → moat -1 (no value-add). "
                    "Yields > naive by 1%+ → moat confirmed."
                ),
            },
            {
                "field": "underlying_lst_mix_pct",
                "what_it_measures": (
                    "% of each underlying LST inside the aggregator (e.g., "
                    "stETH, rETH, wstETH split inside OETH)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Correlated collateral = correlated tail risk."
                ),
                "where_to_retrieve": [
                    "Origin Protocol transparency dashboard",
                ],
                "verdict_impact_if_filled": (
                    "> 70% in one LST → security -1 (single-LST tail risk). "
                    "Diversified → confirmed."
                ),
            },
            {
                "field": "yield_capture_pct_to_ogn_or_token_stakers",
                "what_it_measures": (
                    "% of protocol yield captured by OGN/token stakers "
                    "(real-yield to token holders)."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.real_yield_apr doesn't always break out "
                    "what fraction is real yield to TOKEN holders vs. product holders."
                ),
                "where_to_retrieve": [
                    "Origin governance proposals on fee splits",
                    "veOGN dashboards",
                ],
                "verdict_impact_if_filled": (
                    "< 10% to OGN stakers → revenue -1 (token has no claim). "
                    "> 30% → revenue confirmed."
                ),
            },
            {
                "field": "combined_product_tvl_total_usd",
                "what_it_measures": (
                    "Total TVL across OETH + OUSD + OARM + any new products."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput.tvl_usd may report only a single product."
                ),
                "where_to_retrieve": [
                    "DefiLlama Origin protocol",
                    "Origin Protocol dashboard",
                ],
                "verdict_impact_if_filled": (
                    "TVL < $100M → moat -1 (sub-scale). "
                    "> $500M → confirmed."
                ),
            },
        ],
    },
    "bridge": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "Bridge TVL is at-risk capital, not productive capital. The "
            "schema treats bridge TVL the same as lending TVL, which mis-"
            "rates the risk. Bridge security model = guardian/validator "
            "set + insurance fund; the audit-coverage schema fields are "
            "secondary."
        ),
        "gaps": [
            {
                "field": "daily_bridge_volume_by_route_top3_usd",
                "what_it_measures": (
                    "Top 3 bridge routes by daily volume (e.g., SOL↔ETH, "
                    "ETH↔ARB, BTC↔ETH) — concentration."
                ),
                "why_schema_misses_it": (
                    "RevenueOutput aggregates all volume; route concentration "
                    "is the risk."
                ),
                "where_to_retrieve": [
                    "https://wormholescan.io/",
                    "https://layerzeroscan.com/",
                ],
                "verdict_impact_if_filled": (
                    "Top route > 60% of volume → revenue -1 (concentration risk). "
                    "Diversified → confirmed."
                ),
            },
            {
                "field": "reserve_composition_collateral_split_pct",
                "what_it_measures": (
                    "Composition of bridge reserves backing wrapped/locked "
                    "assets (USDC, USDT, ETH, etc.)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Reserve quality = recovery upside in exploit."
                ),
                "where_to_retrieve": [
                    "Bridge's transparency dashboard",
                    "On-chain reserve addresses",
                ],
                "verdict_impact_if_filled": (
                    "> 30% in long-tail or wrapped assets → security -1. "
                    "Diversified across uncorrelated → confirmed."
                ),
            },
            {
                "field": "guardian_or_validator_set_count_rotation_history",
                "what_it_measures": (
                    "Current # of guardians/validators + rotation cadence "
                    "(any rotation in 12mo?)."
                ),
                "why_schema_misses_it": (
                    "SecurityOutput.single_points_of_failure is qualitative; "
                    "guardian count is the quantitative test."
                ),
                "where_to_retrieve": [
                    "Bridge documentation",
                    "Governance forum",
                ],
                "verdict_impact_if_filled": (
                    "< 13 guardians or zero rotations in 12mo → security -1. "
                    "> 19 with active rotation → confirmed."
                ),
            },
            {
                "field": "insurance_fund_vs_peak_tvl_pct",
                "what_it_measures": (
                    "Insurance fund USD / peak 24h bridge TVL. The blowup "
                    "cushion."
                ),
                "why_schema_misses_it": (
                    "Not modeled."
                ),
                "where_to_retrieve": [
                    "Bridge's insurance fund disclosure",
                    "Reserve transparency reports",
                ],
                "verdict_impact_if_filled": (
                    "< 1% → security -1 (one bad exploit = total loss). "
                    "> 5% → confirmed."
                ),
            },
            {
                "field": "competitor_share_loss_lz_ccip_axelar_pct",
                "what_it_measures": (
                    "Bridge's volume share trend vs. LayerZero, CCIP, Axelar, "
                    "Across, etc. (specific competitor list per bridge)."
                ),
                "why_schema_misses_it": (
                    "MoatOutput.competitive_threat is a label."
                ),
                "where_to_retrieve": [
                    "DefiLlama bridges category",
                    "https://dune.com/whale_hunter/bridges-comparisons",
                ],
                "verdict_impact_if_filled": (
                    "Share declining > 5%/quarter → moat -1. "
                    "Holding or gaining → confirmed."
                ),
            },
        ],
    },
    "failed-l1": {
        "schema_fit_overall": "POOR",
        "category_specific_notes": (
            "Terra Classic and similar post-collapse L1s have one thesis: "
            "deflationary burn from on-chain activity. The schema's "
            "TokenomicsOutput captures supply/unlocks but misses the "
            "burn mechanism that's the only value-accrual driver. Team and "
            "moat agents return ~null (no team, no moat) — those weights "
            "should arguably be removed entirely for this class."
        ),
        "gaps": [
            {
                "field": "monthly_burn_volume_vs_emission_net_supply_change",
                "what_it_measures": (
                    "Net token supply change per month: burn from 1.2% tx tax "
                    "minus staking-reward emission. The deflation thesis."
                ),
                "why_schema_misses_it": (
                    "TokenomicsOutput.inflation_pressure_score is a 1-10 "
                    "label, not net supply change."
                ),
                "where_to_retrieve": [
                    "https://terra-classic.tools/burns",
                    "https://stake.id/?#/validator/...",
                    "Atomscan Terra Classic dashboard",
                ],
                "verdict_impact_if_filled": (
                    "Net inflationary → tokenomics -1. "
                    "Net deflationary > 1%/month → tokenomics confirmed (thesis works)."
                ),
            },
            {
                "field": "active_validator_count_trend_30d",
                "what_it_measures": (
                    "Active validators today vs. 30d ago. Direction matters."
                ),
                "why_schema_misses_it": (
                    "Not in schema."
                ),
                "where_to_retrieve": [
                    "https://stake.id/?#/list",
                    "Terra Classic explorer",
                ],
                "verdict_impact_if_filled": (
                    "Validator count dropping → security -1 (chain abandonment). "
                    "Stable or rising → security confirmed."
                ),
            },
            {
                "field": "governance_proposal_participation_pct",
                "what_it_measures": (
                    "Turnout on recent governance proposals (as % of staked supply)."
                ),
                "why_schema_misses_it": (
                    "Not in schema. Low participation = capture risk on a "
                    "chain where 4 validators control 65%."
                ),
                "where_to_retrieve": [
                    "Terra Classic governance dashboard",
                    "https://station.terra.money/",
                ],
                "verdict_impact_if_filled": (
                    "Turnout < 30% → security -1 (capture risk). "
                    "> 60% → confirmed."
                ),
            },
            {
                "field": "active_cex_listing_count",
                "what_it_measures": (
                    "Number of T-1 / T-2 CEXs still listing the token "
                    "(not just memorializing the husk)."
                ),
                "why_schema_misses_it": (
                    "Not modeled. Delisting = liquidity death."
                ),
                "where_to_retrieve": [
                    "https://www.coingecko.com/en/coins/terra-luna (markets)",
                    "Binance, OKX listing pages",
                ],
                "verdict_impact_if_filled": (
                    "T-1 CEX count = 0 → moat -1 (no exit liquidity). "
                    "> 3 → moat confirmed."
                ),
            },
        ],
    },
}

# ──────────────────────────────────────────────────────────────────────
# PER-TOKEN ADDENDA
# Token-specific gaps that go BEYOND the category template.
# Empty list = category template is sufficient.
# ──────────────────────────────────────────────────────────────────────

TOKEN_ADDENDA: dict[str, list[dict[str, Any]]] = {
    "AAVE": [
        {
            "field": "gho_peg_deviation_30d_max_bps",
            "what_it_measures": (
                "Max GHO stablecoin deviation from $1, trailing 30d, in bps."
            ),
            "why_schema_misses_it": (
                "AAVE-specific: GHO is a native product, not in the standard schema."
            ),
            "where_to_retrieve": [
                "https://app.aave.com/markets/?marketName=proto_mainnet_v3",
                "Curve GHO/USDC pool data",
            ],
            "verdict_impact_if_filled": (
                "Max > 200bps → security -1 (mechanism strain). "
                "< 50bps → security confirmed."
            ),
        },
        {
            "field": "anti_gho_buyback_execution_status_90d",
            "what_it_measures": (
                "Anti-GHO buyback program: actual buyback USD executed vs. "
                "$1M/week target over trailing 90 days."
            ),
            "why_schema_misses_it": (
                "AAVE-specific real-yield mechanism."
            ),
            "where_to_retrieve": [
                "Aave governance forum 'Anti-GHO' tag",
                "On-chain Anti-GHO contract transactions",
            ],
            "verdict_impact_if_filled": (
                "< 75% execution rate → revenue -1. "
                "> 100% (over-execution) → revenue confirmed strong."
            ),
        },
    ],
    "MORPHO": [
        {
            "field": "vault_curator_concentration_top3_pct",
            "what_it_measures": (
                "Top 3 vault curators' share of Morpho Blue TVL. Curator "
                "decisions drive isolated-pool risk."
            ),
            "why_schema_misses_it": (
                "Morpho-specific: the Blue model puts risk in curator hands."
            ),
            "where_to_retrieve": [
                "https://app.morpho.org/?network=mainnet&view=vaults",
                "Morpho's analytics dashboard",
            ],
            "verdict_impact_if_filled": (
                "Top 3 > 60% → security -1 (curator concentration). "
                "Diversified → confirmed."
            ),
        },
    ],
    "UNI": [
        {
            "field": "uni_fee_switch_status_activated_or_promised",
            "what_it_measures": (
                "Has the UNI fee switch (long-promised since 2022) actually "
                "activated, and what's the trailing 90d distribution?"
            ),
            "why_schema_misses_it": (
                "UNI-specific: the entire investment thesis."
            ),
            "where_to_retrieve": [
                "Uniswap governance forum 'fee switch' search",
                "https://app.uniswap.org/governance",
            ],
            "verdict_impact_if_filled": (
                "Still not activated → revenue downgrade (UNI is a token "
                "with no claim on cash flow). Activated + distributing → "
                "revenue confirmed strong."
            ),
        },
    ],
    "AERO": [
        {
            "field": "veaero_bribe_self_dealing_pct",
            "what_it_measures": (
                "Estimated % of weekly veAERO bribes coming from insider/"
                "self-dealing wallets vs. external protocols paying to direct emissions."
            ),
            "why_schema_misses_it": (
                "Bribe gauge games are AERO-specific."
            ),
            "where_to_retrieve": [
                "https://aerodrome.finance/governance",
                "Manual: trace top bribe sources to their on-chain identities",
            ],
            "verdict_impact_if_filled": (
                "> 50% self-dealing → tokenomics -1 (recycling). "
                "Majority external → confirmed."
            ),
        },
    ],
    "ORCA": [
        {
            "field": "orca_sol_dex_share_pct",
            "what_it_measures": (
                "Orca's share of Solana DEX volume vs. Raydium, Phoenix, "
                "Lifinity, etc."
            ),
            "why_schema_misses_it": (
                "Category share on Solana is the moat test."
            ),
            "where_to_retrieve": [
                "https://dune.com/SolanaFloor/solana-dex-aggregator-volume",
                "DefiLlama Solana DEX TVL",
            ],
            "verdict_impact_if_filled": (
                "Share < 15% and declining → moat -1. > 30% → confirmed."
            ),
        },
    ],
    "DYDX": [
        {
            "field": "dydx_chain_validator_decentralization_top10_share",
            "what_it_measures": (
                "Top 10 validators' share of dYdX Chain stake."
            ),
            "why_schema_misses_it": (
                "Post v4 migration, dYdX is a sovereign Cosmos chain."
            ),
            "where_to_retrieve": [
                "https://mintscan.io/dydx",
                "https://dydx.observatory.zone/",
            ],
            "verdict_impact_if_filled": (
                "Top 10 > 70% → security -1. < 45% → confirmed."
            ),
        },
    ],
    "HYPE": [
        {
            "field": "hyperliquid_labs_admin_keys_status",
            "what_it_measures": (
                "Status of HL Labs admin keys: can the team unilaterally halt "
                "the chain, modify order book, or change rules?"
            ),
            "why_schema_misses_it": (
                "Centralization risk specific to early-stage perp DEXs."
            ),
            "where_to_retrieve": [
                "Hyperliquid documentation",
                "Independent decentralization audits",
            ],
            "verdict_impact_if_filled": (
                "Multisig only / no decentralization plan → security -1. "
                "Stage 1+ decentralized → confirmed."
            ),
        },
    ],
    "NEAR": [
        {
            "field": "near_ai_agent_real_adoption_count",
            "what_it_measures": (
                "Count of production AI agents using NEAR's AI infrastructure "
                "(Phala TEE, AITP) — not just announcements."
            ),
            "why_schema_misses_it": (
                "NEAR-specific narrative thesis."
            ),
            "where_to_retrieve": [
                "NEAR official AI agents directory",
                "Phala Network case studies",
            ],
            "verdict_impact_if_filled": (
                "< 10 production agents → moat -1 (narrative > reality). "
                "> 50 + transaction volume → confirmed."
            ),
        },
    ],
    "AVAX": [
        {
            "field": "avalanche_l1_subnet_activity_vs_c_chain_pct",
            "what_it_measures": (
                "% of total Avalanche-9000 ecosystem activity (tx, TVL, fees) "
                "on L1s/subnets vs. the canonical C-Chain."
            ),
            "why_schema_misses_it": (
                "AVAX value increasingly lives in subnets that the schema "
                "treats as separate chains."
            ),
            "where_to_retrieve": [
                "https://subnets.avax.network/",
                "DefiLlama Avalanche subnets",
            ],
            "verdict_impact_if_filled": (
                "C-Chain > 90% (subnets failing) → moat -1. "
                "Subnets > 30% → confirmed."
            ),
        },
    ],
    "ADA": [
        {
            "field": "cardano_defi_tvl_vs_market_cap_pct",
            "what_it_measures": (
                "Cardano DeFi TVL / ADA market cap. ADA has historically "
                "had a market cap orders of magnitude larger than its DeFi TVL."
            ),
            "why_schema_misses_it": (
                "Schema doesn't compute this ratio; ADA-specific gap."
            ),
            "where_to_retrieve": [
                "https://defillama.com/chain/Cardano",
                "CoinGecko ADA market cap",
            ],
            "verdict_impact_if_filled": (
                "TVL/mcap < 0.5% → moat -1 (mcap unjustified by usage). "
                "> 2% → confirmed."
            ),
        },
        {
            "field": "hydra_adoption_active_heads_count",
            "what_it_measures": (
                "Number of active Hydra L2 heads in production."
            ),
            "why_schema_misses_it": (
                "Hydra is Cardano's scaling bet; not in the schema."
            ),
            "where_to_retrieve": [
                "https://hydra.family/head-protocol/use-cases",
                "https://book.world.dev.cardano.org/environments.html",
            ],
            "verdict_impact_if_filled": (
                "< 5 production heads → moat -1 (Hydra thesis dead). "
                "> 20 → confirmed."
            ),
        },
    ],
    "TRX": [
        {
            "field": "usdt_on_tron_share_of_total_usdt_pct",
            "what_it_measures": (
                "USDT issued on Tron / total USDT outstanding. THE Tron thesis."
            ),
            "why_schema_misses_it": (
                "Tron's real revenue is USDT transfer fees, not modeled directly."
            ),
            "where_to_retrieve": [
                "https://defillama.com/stablecoin/tether (per-chain breakdown)",
                "Tether transparency page",
            ],
            "verdict_impact_if_filled": (
                "Share declining (vs. ETH/Solana) → moat -1. "
                "Holding > 40% → confirmed."
            ),
        },
        {
            "field": "justin_sun_multisig_centralization_status",
            "what_it_measures": (
                "Status of Justin Sun's control: super-rep concentration, "
                "treasury keys, public statements vs. operational reality."
            ),
            "why_schema_misses_it": (
                "TeamOutput.doxxed=True doesn't capture concentration of power."
            ),
            "where_to_retrieve": [
                "Tron governance forum",
                "Justin Sun's public addresses (Arkham labeled)",
            ],
            "verdict_impact_if_filled": (
                "Single-point control confirmed → team -1. "
                "Genuine SR distribution → confirmed."
            ),
        },
    ],
    "XRP": [
        {
            "field": "ripple_escrow_release_schedule_12mo_xrp",
            "what_it_measures": (
                "Forward 12-month Ripple escrow release schedule (1B XRP/month "
                "released, varies how much returned)."
            ),
            "why_schema_misses_it": (
                "TokenomicsOutput captures 90d unlock only."
            ),
            "where_to_retrieve": [
                "https://xrpl.org/data-api.html (escrow data)",
                "Ripple's monthly quarterly XRP markets reports",
            ],
            "verdict_impact_if_filled": (
                "Net release (after returns) > 5B XRP/year → tokenomics -1. "
                "Net release < 3B → confirmed managed."
            ),
        },
        {
            "field": "rlusd_adoption_circulating_usd",
            "what_it_measures": (
                "Circulating RLUSD (Ripple's stablecoin) — adoption proxy for "
                "the Ripple payments ecosystem."
            ),
            "why_schema_misses_it": (
                "Not in the schema; RLUSD is the forward catalyst."
            ),
            "where_to_retrieve": [
                "RLUSD transparency dashboard",
                "On-chain RLUSD total supply",
            ],
            "verdict_impact_if_filled": (
                "< $100M circulating → moat -1 (Ripple ecosystem stalled). "
                "> $1B → confirmed catalyst live."
            ),
        },
    ],
    "XLM": [
        {
            "field": "stellar_anchor_volume_usdc_moneygram_monthly_usd",
            "what_it_measures": (
                "Monthly volume through Stellar Anchors (USDC-Stellar, "
                "MoneyGram corridor). Stellar's only meaningful utility."
            ),
            "why_schema_misses_it": (
                "Not modeled. Stellar-specific."
            ),
            "where_to_retrieve": [
                "Stellar Anchor analytics",
                "Circle USDC-Stellar reports",
                "MoneyGram crypto-cash corridor reports",
            ],
            "verdict_impact_if_filled": (
                "Volume declining → moat -1. Growing > 20% YoY → confirmed."
            ),
        },
    ],
    "CFX": [
        {
            "field": "conflux_mainland_china_regulatory_positioning",
            "what_it_measures": (
                "Conflux's status with PRC regulators (Shanghai government "
                "ties, any 2026 policy shifts, ICP partnerships)."
            ),
            "why_schema_misses_it": (
                "MoatOutput.regulatory_relative_risk doesn't carve out the "
                "PRC-specific asymmetry."
            ),
            "where_to_retrieve": [
                "Conflux Foundation announcements",
                "Shanghai municipal blockchain initiatives press releases",
            ],
            "verdict_impact_if_filled": (
                "Active PRC government partnership → moat +1 (unique asymmetry). "
                "Lost positioning → moat -1."
            ),
        },
    ],
    "TON": [
        {
            "field": "telegram_wau_to_ton_wallet_conversion_pct",
            "what_it_measures": (
                "% of Telegram users who have a TON wallet active in 30d."
            ),
            "why_schema_misses_it": (
                "TON's thesis is Telegram → TON conversion; not in the schema."
            ),
            "where_to_retrieve": [
                "TON Foundation transparency reports",
                "https://defillama.com/chain/TON (DAU stats)",
                "Telegram official engagement reports",
            ],
            "verdict_impact_if_filled": (
                "Conversion < 1% → moat -1 (Telegram audience not converting). "
                "> 5% → confirmed."
            ),
        },
    ],
    "SUI": [
        {
            "field": "sui_team_unlock_12_24mo_pct_of_total_supply",
            "what_it_measures": (
                "Team/investor unlock schedule for months 12-24 (the cliff "
                "completing May 2027 = ~20% of total supply)."
            ),
            "why_schema_misses_it": (
                "TokenomicsOutput captures 90d only."
            ),
            "where_to_retrieve": [
                "https://token.unlocks.app/sui",
                "Sui Foundation tokenomics page",
            ],
            "verdict_impact_if_filled": (
                "> 25% supply unlocking 12-24mo → tokenomics -1. "
                "< 15% → confirmed."
            ),
        },
        {
            "field": "walrus_deep_token_launches_fee_impact",
            "what_it_measures": (
                "Trailing 90d SUI fee revenue attributable to Walrus + DEEP "
                "token launches and related airdrop activity."
            ),
            "why_schema_misses_it": (
                "Event-driven fee spikes obscure baseline fee growth."
            ),
            "where_to_retrieve": [
                "Sui Foundation quarterly reports",
                "DefiLlama Sui fees",
            ],
            "verdict_impact_if_filled": (
                "If majority of recent fee growth was event-driven → revenue downgrade "
                "(non-recurring). Sustainable baseline growth → confirmed."
            ),
        },
    ],
    "STX": [
        # category template already covers sbtc_tvl and stacking yield breakdown
    ],
}

# ──────────────────────────────────────────────────────────────────────
# WORKSHEET ASSEMBLY
# ──────────────────────────────────────────────────────────────────────

def _read_conviction(symbol: str) -> dict[str, Any]:
    """Pre-populate current orchestrator state from reports/{SYMBOL}/conviction.json."""
    path = REPORTS_DIR / symbol / "conviction.json"
    if not path.exists():
        return {
            "agents_loaded": 0,
            "weighted_conviction": None,
            "verdict": None,
            "blocking_red_flag": "no conviction report exists yet",
            "category_scorecard": {},
        }
    try:
        c = json.loads(path.read_text())
    except Exception as e:                                     # noqa: BLE001
        return {
            "agents_loaded": 0,
            "weighted_conviction": None,
            "verdict": None,
            "blocking_red_flag": f"conviction.json parse error: {e}",
            "category_scorecard": {},
        }
    return {
        "agents_loaded": 7 - len(c.get("missing_agents", []) or []),
        "weighted_conviction": c.get("weighted_conviction"),
        "verdict": c.get("final_verdict"),
        "blocking_red_flag": c.get("auto_reject_reason"),
        "category_scorecard": c.get("category_scorecard", {}),
        "stale_agents": c.get("stale_agents", []),
        "fallback_agents": c.get("fallback_agents", []),
        "missing_agents": c.get("missing_agents", []),
    }


def _merge_existing_user_values(
    new_gaps: list[dict[str, Any]],
    output_path: pathlib.Path,
) -> list[dict[str, Any]]:
    """If an existing worksheet has user-filled values, preserve them."""
    if not output_path.exists():
        return new_gaps
    try:
        existing = json.loads(output_path.read_text())
    except Exception:                                          # noqa: BLE001
        return new_gaps
    existing_by_field = {g["field"]: g for g in existing.get("schema_overlay_gaps", [])}
    for gap in new_gaps:
        prev = existing_by_field.get(gap["field"])
        if prev:
            gap["value"] = prev.get("value")
            gap["notes"] = prev.get("notes", "")
            gap["retrieved_at"] = prev.get("retrieved_at")
            # Preserve any user-added manual_adjustment overrides
            if "manual_adjustment" in prev:
                gap["manual_adjustment"] = prev["manual_adjustment"]
    return new_gaps


def build_worksheet(symbol: str) -> dict[str, Any]:
    category = TOKEN_CATEGORY.get(symbol)
    if not category:
        raise ValueError(f"{symbol}: no category mapping in TOKEN_CATEGORY")
    template = CATEGORY_TEMPLATES.get(category)
    if not template:
        raise ValueError(f"{symbol}: category '{category}' has no template")

    addenda = TOKEN_ADDENDA.get(symbol, [])
    all_gaps_meta = list(template["gaps"]) + list(addenda)

    # Annotate each gap with the user-facing fields (value, notes, retrieved_at)
    # AND attach structured delta_rules for the reconciler.
    gaps_with_user_fields: list[dict[str, Any]] = []
    for g in all_gaps_meta:
        gaps_with_user_fields.append({
            **g,
            "delta_rules": get_rules(category, symbol, g["field"]),
            "value": None,
            "notes": "",
            "retrieved_at": None,
        })

    output_path = OUTPUT_DIR / f"{symbol}.json"
    gaps_with_user_fields = _merge_existing_user_values(gaps_with_user_fields, output_path)

    conviction = _read_conviction(symbol)

    try:
        token_meta = tokens.get(symbol)
        token_info = {
            "name": token_meta.name,
            "chain": token_meta.chain,
            "registry_category": token_meta.category,
            "coingecko_id": token_meta.coingecko_id,
            "contract_address": token_meta.contract_address,
        }
    except KeyError:
        token_info = {"name": symbol, "registry_category": "UNKNOWN"}

    return {
        "symbol": symbol,
        "category": category,
        "template_version": TEMPLATE_VERSION,
        "token_info": token_info,
        "schema_fit_overall": template["schema_fit_overall"],
        "category_specific_notes": template["category_specific_notes"],
        "current_orchestrator_state": {
            "agents_loaded": conviction["agents_loaded"],
            "weighted_conviction": conviction["weighted_conviction"],
            "verdict": conviction["verdict"],
            "blocking_red_flag": conviction["blocking_red_flag"],
            "current_score_components": conviction.get("category_scorecard", {}),
            "stale_agents": conviction.get("stale_agents", []),
            "fallback_agents": conviction.get("fallback_agents", []),
            "missing_agents": conviction.get("missing_agents", []),
        },
        "schema_overlay_gaps": gaps_with_user_fields,
        "highest_priority_gap_field": (
            gaps_with_user_fields[0]["field"] if gaps_with_user_fields else None
        ),
        "instructions": (
            "Retrieve each gap's value from the documented source(s), fill "
            "in `value` and (optionally) `notes`, and set `retrieved_at` to "
            "today's ISO date. The `verdict_impact_if_filled` field tells "
            "you how to adjust the relevant agent's composite score band "
            "when manually reconciling. Once all gaps for a token are "
            "filled, you can derive a manually-reconciled conviction by "
            "applying the verdict_impact deltas to current_score_components "
            "and recomputing the weighted sum from config.yaml."
        ),
    }


def write_all(symbols: list[str] | None = None) -> list[str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = symbols or list(TOKEN_CATEGORY.keys())
    written: list[str] = []
    for sym in symbols:
        ws = build_worksheet(sym)
        path = OUTPUT_DIR / f"{sym}.json"
        path.write_text(json.dumps(ws, indent=2))
        written.append(str(path))
    return written


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("symbols", nargs="*", help="Specific symbols (default: all)")
    args = p.parse_args(argv)
    syms = [s.upper() for s in args.symbols] if args.symbols else None
    written = write_all(syms)
    print(f"Wrote {len(written)} worksheets to {OUTPUT_DIR}")
    for w in written[:5]:
        print(f"  {w}")
    if len(written) > 5:
        print(f"  ... and {len(written) - 5} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
