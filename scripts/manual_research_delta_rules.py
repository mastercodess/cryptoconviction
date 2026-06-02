"""
Structured delta-rules for manual-research worksheet gaps.

Each rule maps a (category-or-symbol, field) pair to one or more conditional
score adjustments. When the user fills in a gap's `value` and runs the
reconciler, each rule's `when` expression is evaluated against `value`; if
True, `delta_points` are applied to the named agent's composite_score.

Why structured: the worksheet's `verdict_impact_if_filled` field is human-
readable freeform text. To mechanically reconcile filled values back into
revised conviction scores, the impact rules need a machine-evaluable form.

Lookup order (in scripts/generate_manual_research_worksheets.py):
    1. (category, field) — e.g., ("defi-lending", "real_yield_trailing_90d_pct")
    2. (symbol, field)   — e.g., ("AAVE", "gho_peg_deviation_30d_max_bps")
    3. []                — no auto-rules; reconciler shows verdict_impact text
                           and accepts a manual override via the `notes` field
                           in the worksheet.

Convention for delta_points:
    -20: severe downgrade (~one verdict-band shift for a top-weighted agent)
    -10: one band down within the agent score
    -5:  meaningful penalty
    +5:  meaningful confirmation
    +10: strong confirmation
    +20: thesis-defining positive signal

Rules cap aggregate per-agent deltas at ±25 (set in reconciler) so a single
worksheet can't make a 50→90 swing.

Adding rules:
    - Use Python expressions in `when`. Available identifier: `value`.
    - For list/dict values, helpers like `len(value)` and `max(value.values())`
      are allowed (eval namespace whitelists `len`, `max`, `min`, `sum`).
    - Keep thresholds aligned with the corresponding gap's verdict_impact text
      in scripts/generate_manual_research_worksheets.py.
"""
from __future__ import annotations

DELTA_RULES: dict[tuple[str, str], list[dict]] = {

    # ── defi-lending ────────────────────────────────────────────────
    ("defi-lending", "real_yield_trailing_90d_pct"): [
        {"when": "value < 2.0", "agent_score": "revenue", "delta_points": -10,
         "reason": "Realized real yield < 2% — fee-switch durability lost"},
        {"when": "value > 4.0", "agent_score": "revenue", "delta_points": +5,
         "reason": "Realized real yield > 4% — thesis strengthened"},
    ],
    ("defi-lending", "top_3_borrower_concentration_pct"): [
        {"when": "value > 0.30", "agent_score": "security", "delta_points": -10,
         "reason": "Top-3 borrower concentration > 30% — CRV-2022 analog risk"},
        {"when": "value < 0.15", "agent_score": "security", "delta_points": +5,
         "reason": "Borrower base diversified"},
    ],
    ("defi-lending", "total_bad_debt_outstanding_usd"): [
        {"when": "value > 50_000_000", "agent_score": "security", "delta_points": -10,
         "reason": "Bad debt > $50M — significant unfilled hole"},
        {"when": "value < 5_000_000", "agent_score": "security", "delta_points": +5,
         "reason": "Bad debt < $5M — confirmed clean"},
    ],
    ("defi-lending", "forward_unlock_cliff_3_to_12mo_pct"): [
        {"when": "value > 0.25", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "> 25% supply unlocking months 3-12"},
        {"when": "value < 0.10", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "< 10% supply unlocking months 3-12"},
    ],
    ("defi-lending", "bridge_dependency_pct_tvl"): [
        {"when": "value > 0.30", "agent_score": "security", "delta_points": -10,
         "reason": "> 30% of TVL bridged via non-canonical routes"},
        {"when": "value < 0.05", "agent_score": "security", "delta_points": +5,
         "reason": "< 5% bridge dependency — confirmed minimal"},
    ],

    # AAVE-specific (token-keyed, not category-keyed)
    ("AAVE", "gho_peg_deviation_30d_max_bps"): [
        {"when": "value > 200", "agent_score": "security", "delta_points": -10,
         "reason": "GHO deviation > 200bps — mechanism strain"},
        {"when": "value < 50", "agent_score": "security", "delta_points": +5,
         "reason": "GHO peg tight"},
    ],
    ("AAVE", "anti_gho_buyback_execution_status_90d"): [
        {"when": "value < 0.75", "agent_score": "revenue", "delta_points": -10,
         "reason": "Anti-GHO execution < 75% of target"},
        {"when": "value > 1.0", "agent_score": "revenue", "delta_points": +5,
         "reason": "Anti-GHO over-executing target"},
    ],

    # ── defi-dex ────────────────────────────────────────────────────
    ("defi-dex", "wash_volume_ratio_30d"): [
        {"when": "value > 0.40", "agent_score": "revenue", "delta_points": -10,
         "reason": "Wash volume > 40%"},
        {"when": "value < 0.10", "agent_score": "revenue", "delta_points": +5,
         "reason": "Wash volume < 10% — confirmed organic"},
    ],
    ("defi-dex", "lp_retention_30_60_90d_pct"): [
        {"when": "value < 0.25", "agent_score": "moat", "delta_points": -10,
         "reason": "90d LP retention < 25% — mercenary liquidity"},
        {"when": "value > 0.50", "agent_score": "moat", "delta_points": +5,
         "reason": "90d LP retention > 50% — durable"},
    ],
    ("defi-dex", "volume_durability_without_incentives_pct"): [
        {"when": "value < 0.30", "agent_score": "moat", "delta_points": -10,
         "reason": "< 30% volume durable without incentives"},
        {"when": "value > 0.70", "agent_score": "moat", "delta_points": +5,
         "reason": "> 70% volume organic"},
    ],
    ("defi-dex", "fee_switch_realized_distribution_usd_90d"): [
        {"when": "value == 0", "agent_score": "revenue", "delta_points": -10,
         "reason": "Fee switch promised but $0 distributed"},
        {"when": "value > 10_000_000", "agent_score": "revenue", "delta_points": +10,
         "reason": "> $10M distributed to holders in 90d"},
    ],

    # UNI specific
    ("UNI", "uni_fee_switch_status_activated_or_promised"): [
        # categorical — handled below as enum match
        {"when": "value == 'NOT_ACTIVATED'", "agent_score": "revenue", "delta_points": -10,
         "reason": "UNI fee switch still not activated"},
        {"when": "value == 'ACTIVE_DISTRIBUTING'", "agent_score": "revenue", "delta_points": +10,
         "reason": "UNI fee switch live and distributing"},
    ],
    # AERO specific
    ("AERO", "veaero_bribe_self_dealing_pct"): [
        {"when": "value > 0.50", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "> 50% bribes self-dealing"},
        {"when": "value < 0.20", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "Bribes majority external"},
    ],
    # ORCA specific
    ("ORCA", "orca_sol_dex_share_pct"): [
        {"when": "value < 0.15", "agent_score": "moat", "delta_points": -10,
         "reason": "Solana DEX share < 15%"},
        {"when": "value > 0.30", "agent_score": "moat", "delta_points": +5,
         "reason": "Solana DEX share > 30%"},
    ],

    # ── defi-perps ──────────────────────────────────────────────────
    ("defi-perps", "real_volume_vs_wash_30d_pct"): [
        {"when": "value < 0.30", "agent_score": "revenue", "delta_points": -10,
         "reason": "Real volume < 30% of reported"},
        {"when": "value > 0.70", "agent_score": "revenue", "delta_points": +5,
         "reason": "Real volume > 70%"},
    ],
    ("defi-perps", "insurance_fund_usd_vs_peak_oi_usd_ratio"): [
        {"when": "value < 0.005", "agent_score": "security", "delta_points": -10,
         "reason": "Insurance fund < 0.5% of peak OI"},
        {"when": "value > 0.02", "agent_score": "security", "delta_points": +5,
         "reason": "Insurance fund > 2% of peak OI"},
    ],
    ("defi-perps", "funding_rate_competitiveness_vs_binance_bps"): [
        {"when": "value > 5", "agent_score": "moat", "delta_points": -10,
         "reason": "Funding > Binance +5bps — price takers"},
        {"when": "value < 0", "agent_score": "moat", "delta_points": +5,
         "reason": "Funding < Binance — price makers"},
    ],
    ("DYDX", "dydx_chain_validator_decentralization_top10_share"): [
        {"when": "value > 0.70", "agent_score": "security", "delta_points": -10,
         "reason": "Top 10 validators > 70% stake"},
        {"when": "value < 0.45", "agent_score": "security", "delta_points": +5,
         "reason": "Top 10 < 45% — decentralized"},
    ],

    # ── oracle (LINK) ───────────────────────────────────────────────
    ("oracle", "tvs_total_value_secured_usd"): [
        {"when": "value < 5_000_000_000", "agent_score": "moat", "delta_points": -10,
         "reason": "TVS < $5B — moat eroding"},
        {"when": "value > 20_000_000_000", "agent_score": "moat", "delta_points": +5,
         "reason": "TVS > $20B — moat confirmed Tier 1"},
    ],
    ("oracle", "staking_v02_participation_pct"): [
        {"when": "value < 0.15", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "Staking participation < 15% — low alignment"},
        {"when": "value > 0.30", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "Staking > 30% — confirmed strong"},
    ],
    ("oracle", "chainlink_labs_reserve_outflow_30d_link"): [
        {"when": "value > 20_000_000", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "Labs outflow > 20M LINK / 30d — heavy distribution"},
        {"when": "value <= 0", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "Net inflow or stable — confirmed"},
    ],

    # ── rwa (ONDO) ──────────────────────────────────────────────────
    ("rwa", "underlying_aum_usd"): [
        {"when": "value < 500_000_000", "agent_score": "revenue", "delta_points": -10,
         "reason": "AUM < $500M — sub-scale"},
        {"when": "value > 1_000_000_000", "agent_score": "revenue", "delta_points": +5,
         "reason": "AUM > $1B — confirmed at scale"},
    ],
    ("rwa", "custodial_relationships_list"): [
        {"when": "len(value) < 2", "agent_score": "security", "delta_points": -10,
         "reason": "Single custodian"},
        {"when": "len(value) >= 3", "agent_score": "security", "delta_points": +5,
         "reason": "Multi-custodian — confirmed"},
    ],
    ("rwa", "sec_letter_rulings_no_action_count"): [
        {"when": "value == 0", "agent_score": "team", "delta_points": -10,
         "reason": "Zero SEC rulings — regulatory ambiguity"},
        {"when": "value >= 1", "agent_score": "team", "delta_points": +5,
         "reason": "≥1 SEC no-action letter — confirmed standing"},
    ],
    ("rwa", "redemption_gate_history_count"): [
        {"when": "value > 0", "agent_score": "security", "delta_points": -10,
         "reason": "Redemption gate event in history"},
    ],
    ("rwa", "geographic_restrictions_eligible_jurisdictions_count"): [
        {"when": "value < 5", "agent_score": "moat", "delta_points": -10,
         "reason": "< 5 jurisdictions — limited TAM"},
        {"when": "value > 30", "agent_score": "moat", "delta_points": +5,
         "reason": "Global accessibility"},
    ],

    # ── synthetic-dollar (ENA) ──────────────────────────────────────
    ("synthetic-dollar", "funding_rate_carry_90d_pct"): [
        {"when": "value < 5", "agent_score": "revenue", "delta_points": -10,
         "reason": "Carry < 5% APR — thesis weakening"},
        {"when": "value > 15", "agent_score": "revenue", "delta_points": +5,
         "reason": "Carry > 15% APR — confirmed"},
    ],
    ("synthetic-dollar", "backing_composition_collateral_split_pct"): [
        {"when": "max(value.values()) > 0.40", "agent_score": "security", "delta_points": -10,
         "reason": "Single collateral > 40% — correlated tail risk"},
        {"when": "max(value.values()) <= 0.40", "agent_score": "security", "delta_points": +5,
         "reason": "Collateral diversified"},
    ],
    ("synthetic-dollar", "insurance_fund_vs_usde_supply_ratio_pct"): [
        {"when": "value < 1", "agent_score": "security", "delta_points": -10,
         "reason": "Insurance < 1% of USDe supply"},
        {"when": "value > 3", "agent_score": "security", "delta_points": +5,
         "reason": "Insurance > 3% — confirmed cushion"},
    ],
    ("synthetic-dollar", "usde_peg_deviation_30d_max_bps"): [
        {"when": "value > 100", "agent_score": "security", "delta_points": -10,
         "reason": "USDe peg deviation > 100bps in 30d"},
        {"when": "value < 25", "agent_score": "security", "delta_points": +5,
         "reason": "USDe peg deviation < 25bps — confirmed"},
    ],
    ("synthetic-dollar", "ena_unlock_schedule_12mo_pct"): [
        {"when": "value > 0.25", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "> 25% ENA unlocking next 12mo"},
        {"when": "value < 0.10", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "< 10% unlocking — confirmed"},
    ],

    # ── l1-general ──────────────────────────────────────────────────
    ("l1-general", "nakamoto_coefficient"): [
        {"when": "value < 5", "agent_score": "security", "delta_points": -10,
         "reason": "Nakamoto coefficient < 5 — centralization risk"},
        {"when": "value > 20", "agent_score": "security", "delta_points": +5,
         "reason": "Nakamoto coefficient > 20 — decentralized"},
    ],
    ("l1-general", "real_fee_revenue_vs_subsidy_pct"): [
        {"when": "value < 0.20", "agent_score": "revenue", "delta_points": -10,
         "reason": "< 20% from fees — subsidy-dependent"},
        {"when": "value > 0.50", "agent_score": "revenue", "delta_points": +5,
         "reason": "> 50% from fees — sustainable"},
    ],
    ("l1-general", "ecosystem_grant_runway_months"): [
        {"when": "value < 12", "agent_score": "team", "delta_points": -10,
         "reason": "Grant runway < 12 months"},
        {"when": "value > 36", "agent_score": "team", "delta_points": +5,
         "reason": "Grant runway > 36 months — confirmed"},
    ],
    ("l1-general", "top_application_dau"): [
        {"when": "value < 5000", "agent_score": "onchain", "delta_points": -10,
         "reason": "Top-app DAU < 5k — no organic adoption"},
        {"when": "value > 100000", "agent_score": "onchain", "delta_points": +5,
         "reason": "Top-app DAU > 100k — confirmed"},
    ],
    # L1 token-specific
    ("AVAX", "avalanche_l1_subnet_activity_vs_c_chain_pct"): [
        {"when": "value < 0.10", "agent_score": "moat", "delta_points": -10,
         "reason": "C-Chain > 90% of activity — subnet thesis failing"},
        {"when": "value > 0.30", "agent_score": "moat", "delta_points": +5,
         "reason": "Subnets > 30% — confirmed"},
    ],
    ("ADA", "cardano_defi_tvl_vs_market_cap_pct"): [
        {"when": "value < 0.005", "agent_score": "moat", "delta_points": -10,
         "reason": "TVL/mcap < 0.5% — mcap unjustified"},
        {"when": "value > 0.02", "agent_score": "moat", "delta_points": +5,
         "reason": "TVL/mcap > 2% — confirmed"},
    ],
    ("ADA", "hydra_adoption_active_heads_count"): [
        {"when": "value < 5", "agent_score": "moat", "delta_points": -10,
         "reason": "< 5 Hydra heads — thesis dead"},
        {"when": "value > 20", "agent_score": "moat", "delta_points": +5,
         "reason": "> 20 Hydra heads — adoption confirmed"},
    ],
    ("TRX", "usdt_on_tron_share_of_total_usdt_pct"): [
        {"when": "value < 0.30", "agent_score": "moat", "delta_points": -10,
         "reason": "USDT-on-Tron share < 30% — losing"},
        {"when": "value > 0.40", "agent_score": "moat", "delta_points": +5,
         "reason": "USDT-on-Tron share > 40% — confirmed"},
    ],
    ("XRP", "ripple_escrow_release_schedule_12mo_xrp"): [
        {"when": "value > 5_000_000_000", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "Net release > 5B XRP/year"},
        {"when": "value < 3_000_000_000", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "Net release < 3B — managed"},
    ],
    ("XRP", "rlusd_adoption_circulating_usd"): [
        {"when": "value < 100_000_000", "agent_score": "moat", "delta_points": -10,
         "reason": "RLUSD < $100M circulating — ecosystem stalled"},
        {"when": "value > 1_000_000_000", "agent_score": "moat", "delta_points": +5,
         "reason": "RLUSD > $1B — catalyst live"},
    ],
    ("TON", "telegram_wau_to_ton_wallet_conversion_pct"): [
        {"when": "value < 0.01", "agent_score": "moat", "delta_points": -10,
         "reason": "Telegram→wallet conversion < 1%"},
        {"when": "value > 0.05", "agent_score": "moat", "delta_points": +5,
         "reason": "Conversion > 5% — confirmed"},
    ],
    ("SUI", "sui_team_unlock_12_24mo_pct_of_total_supply"): [
        {"when": "value > 0.25", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "> 25% supply unlocking 12-24mo"},
        {"when": "value < 0.15", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "< 15% unlocking — managed"},
    ],

    # ── l1-privacy (XMR) ────────────────────────────────────────────
    ("l1-privacy", "randomx_hashrate_gh_s_current_vs_ath"): [
        {"when": "value < 0.25", "agent_score": "security", "delta_points": -10,
         "reason": "Hashrate < 25% of ATH — miner abandonment"},
        {"when": "value > 0.75", "agent_score": "security", "delta_points": +5,
         "reason": "Hashrate > 75% of ATH — confirmed"},
    ],
    ("l1-privacy", "asic_resistance_status"): [
        {"when": "value == 'ASIC_DEPLOYED_NO_FORK'", "agent_score": "security", "delta_points": -20,
         "reason": "ASIC deployed with no algo-update planned — existential"},
        {"when": "value == 'RESISTANT'", "agent_score": "security", "delta_points": +5,
         "reason": "ASIC resistance confirmed"},
    ],
    ("l1-privacy", "cex_availability_active_count"): [
        {"when": "value == 0", "agent_score": "moat", "delta_points": -10,
         "reason": "Zero T-1 CEX listings — liquidity collapse"},
        {"when": "value > 3", "agent_score": "moat", "delta_points": +5,
         "reason": "> 3 T-1 CEX listings — confirmed"},
    ],
    ("l1-privacy", "tail_emission_effective_inflation_pct"): [
        {"when": "value > 1.5", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "Effective inflation > 1.5%"},
        {"when": "value < 0.8", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "Inflation < 0.8% — confirmed"},
    ],
    ("l1-privacy", "atomic_swap_monthly_volume_usd_btc_xmr"): [
        {"when": "value < 500_000", "agent_score": "moat", "delta_points": -10,
         "reason": "Atomic swap volume < $500k/mo — no DEX route"},
        {"when": "value > 5_000_000", "agent_score": "moat", "delta_points": +5,
         "reason": "Atomic swap volume > $5M/mo — resilient"},
    ],
    ("l1-privacy", "seraphis_upgrade_status"): [
        {"when": "value == 'MAINNET_CONFIRMED'", "agent_score": "moat", "delta_points": +5,
         "reason": "Seraphis mainnet date confirmed"},
        {"when": "value == 'STALLED_OVER_12MO'", "agent_score": "moat", "delta_points": -10,
         "reason": "Seraphis stalled > 12mo"},
    ],

    # ── l2 (STX) ────────────────────────────────────────────────────
    ("l2", "sbtc_or_l2_bridged_asset_tvl_usd"): [
        {"when": "value < 50_000_000", "agent_score": "moat", "delta_points": -10,
         "reason": "Bridged asset TVL < $50M"},
        {"when": "value > 500_000_000", "agent_score": "moat", "delta_points": +5,
         "reason": "Bridged TVL > $500M — confirmed"},
    ],
    ("l2", "stacking_yield_btc_vs_stx_paid_pct"): [
        {"when": "value < 0.30", "agent_score": "revenue", "delta_points": -10,
         "reason": "< 30% BTC-paid yield"},
        {"when": "value > 0.70", "agent_score": "revenue", "delta_points": +5,
         "reason": "> 70% BTC-paid — confirmed"},
    ],
    ("l2", "settlement_frequency_l1_blocks_per_anchor"): [
        {"when": "value > 144", "agent_score": "security", "delta_points": -10,
         "reason": "Settlement frequency > 144 blocks (24h)"},
        {"when": "value < 30", "agent_score": "security", "delta_points": +5,
         "reason": "Settlement < 30 blocks — confirmed"},
    ],
    ("l2", "consensus_upgrade_status"): [
        {"when": "value == 'LIVE'", "agent_score": "moat", "delta_points": +5,
         "reason": "Consensus upgrade live"},
        {"when": "value == 'STALLED_OVER_6MO'", "agent_score": "moat", "delta_points": -10,
         "reason": "Consensus upgrade stalled > 6mo"},
    ],

    # ── btc-fork (BCH) ──────────────────────────────────────────────
    ("btc-fork", "fork_to_parent_hashrate_ratio_pct"): [
        {"when": "value < 0.005", "agent_score": "security", "delta_points": -10,
         "reason": "Hashrate ratio < 0.5% — rentable attack"},
        {"when": "value > 0.05", "agent_score": "security", "delta_points": +5,
         "reason": "Hashrate ratio > 5% — confirmed"},
    ],
    ("btc-fork", "cashfusion_or_privacy_monthly_volume_usd"): [
        {"when": "value < 1_000_000", "agent_score": "moat", "delta_points": -10,
         "reason": "Privacy mixer volume < $1M/mo"},
        {"when": "value > 10_000_000", "agent_score": "moat", "delta_points": +5,
         "reason": "Privacy mixer volume > $10M/mo"},
    ],
    ("btc-fork", "merchant_acceptance_count_vs_2017_peak_pct"): [
        {"when": "value < 0.30", "agent_score": "moat", "delta_points": -10,
         "reason": "< 30% of 2017 merchant base"},
        {"when": "value > 0.70", "agent_score": "moat", "delta_points": +5,
         "reason": "> 70% of peak merchants"},
    ],
    ("btc-fork", "block_time_variance_30d_pct"): [
        {"when": "value > 0.30", "agent_score": "security", "delta_points": -10,
         "reason": "Block time variance > 30%"},
        {"when": "value < 0.10", "agent_score": "security", "delta_points": +5,
         "reason": "Block time variance < 10% — stable"},
    ],

    # ── btc-ordinals (ORDI) ─────────────────────────────────────────
    ("btc-ordinals", "indexer_consensus_status"): [
        {"when": "value == 'DISAGREEMENT_NOW'", "agent_score": "security", "delta_points": -20,
         "reason": "Indexer disagreement — existential risk"},
        {"when": "value == 'CLEAN_90D'", "agent_score": "security", "delta_points": +5,
         "reason": "Indexer consensus clean 90d"},
    ],
    ("btc-ordinals", "daily_inscription_count_30d_trend"): [
        {"when": "value == 'DECLINING'", "agent_score": "moat", "delta_points": -10,
         "reason": "Inscription trend declining"},
        {"when": "value == 'RISING'", "agent_score": "moat", "delta_points": +5,
         "reason": "Inscription trend rising"},
    ],
    ("btc-ordinals", "marketplace_volume_30d_usd"): [
        {"when": "value < 15_000_000", "agent_score": "moat", "delta_points": -10,
         "reason": "Marketplace volume < $15M/30d"},
        {"when": "value > 150_000_000", "agent_score": "moat", "delta_points": +5,
         "reason": "Marketplace volume > $150M/30d"},
    ],
    ("btc-ordinals", "binance_hot_wallet_pct_of_supply"): [
        {"when": "value > 0.40", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "> 40% on Binance — SPOF"},
        {"when": "value < 0.20", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "< 20% on Binance — distributed"},
    ],

    # ── meme (DOGE) ─────────────────────────────────────────────────
    ("meme", "musk_or_top_influencer_mentions_30d_count"): [
        {"when": "value == 0", "agent_score": "moat", "delta_points": -10,
         "reason": "Zero influencer mentions — catalyst absent"},
        {"when": "value > 5", "agent_score": "moat", "delta_points": +5,
         "reason": "Active influencer engagement"},
    ],
    ("meme", "merged_mining_parent_hashrate_share_pct"): [
        {"when": "value < 0.60", "agent_score": "security", "delta_points": -10,
         "reason": "Merged-mining share < 60%"},
        {"when": "value > 0.90", "agent_score": "security", "delta_points": +5,
         "reason": "Merged-mining share > 90% — confirmed"},
    ],
    ("meme", "daily_organic_txn_count_30d_avg"): [
        {"when": "value < 25_000", "agent_score": "onchain", "delta_points": -10,
         "reason": "Daily txn < 25k"},
        {"when": "value > 100_000", "agent_score": "onchain", "delta_points": +5,
         "reason": "Daily txn > 100k — real utility"},
    ],
    ("meme", "payments_integration_status"): [
        {"when": "value == 'LIVE_MAJOR_RETAILER'", "agent_score": "moat", "delta_points": +10,
         "reason": "Major retailer / X Payments live"},
        {"when": "value == 'ALL_DEAD'", "agent_score": "moat", "delta_points": -10,
         "reason": "All announced integrations dead"},
    ],

    # ── telegram-game (NOT) ─────────────────────────────────────────
    ("telegram-game", "telegram_bot_wau_count"): [
        {"when": "value < 500_000", "agent_score": "onchain", "delta_points": -10,
         "reason": "WAU < 500k — audience collapsing"},
        {"when": "value > 5_000_000", "agent_score": "onchain", "delta_points": +5,
         "reason": "WAU > 5M — confirmed"},
    ],
    ("telegram-game", "quest_completion_rate_30d_pct"): [
        {"when": "value < 0.20", "agent_score": "onchain", "delta_points": -10,
         "reason": "Quest completion < 20%"},
        {"when": "value > 0.60", "agent_score": "onchain", "delta_points": +5,
         "reason": "Quest completion > 60%"},
    ],
    ("telegram-game", "game_to_token_holder_retention_30d_pct"): [
        {"when": "value < 0.10", "agent_score": "onchain", "delta_points": -10,
         "reason": "30d holder retention < 10%"},
        {"when": "value > 0.40", "agent_score": "onchain", "delta_points": +5,
         "reason": "30d retention > 40%"},
    ],
    ("telegram-game", "competing_apps_wau_share_pct"): [
        {"when": "value < 0.20", "agent_score": "moat", "delta_points": -10,
         "reason": "Category share < 20%"},
        {"when": "value > 0.50", "agent_score": "moat", "delta_points": +5,
         "reason": "Category share > 50%"},
    ],

    # ── ai-infra (RNDR) ─────────────────────────────────────────────
    ("ai-infra", "compute_units_rendered_monthly"): [
        {"when": "value < 100_000", "agent_score": "revenue", "delta_points": -10,
         "reason": "Compute units < 100k/mo"},
        {"when": "value > 1_000_000", "agent_score": "revenue", "delta_points": +5,
         "reason": "Compute units > 1M/mo"},
    ],
    ("ai-infra", "supply_side_node_count_active"): [
        {"when": "value < 1000", "agent_score": "moat", "delta_points": -10,
         "reason": "Active nodes < 1000"},
        {"when": "value > 10_000", "agent_score": "moat", "delta_points": +5,
         "reason": "Active nodes > 10k"},
    ],
    ("ai-infra", "migration_status_chain_consolidation_pct"): [
        {"when": "value < 0.50", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "Migration < 50% — fragmentation"},
        {"when": "value > 0.90", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "Migration > 90% — unified"},
    ],
    ("ai-infra", "competing_network_share_loss_pct"): [
        {"when": "value > 0.10", "agent_score": "moat", "delta_points": -10,
         "reason": "Losing > 10%/quarter share"},
        {"when": "value < 0", "agent_score": "moat", "delta_points": +5,
         "reason": "Gaining share"},
    ],
    ("ai-infra", "enterprise_partnership_active_count"): [
        {"when": "value == 0", "agent_score": "moat", "delta_points": -10,
         "reason": "Zero active enterprise partnerships"},
        {"when": "value > 5", "agent_score": "moat", "delta_points": +5,
         "reason": "> 5 active T-1 partnerships"},
    ],

    # ── tournament (NMR) ────────────────────────────────────────────
    ("tournament", "active_model_submissions_per_round_count"): [
        {"when": "value < 5000", "agent_score": "moat", "delta_points": -10,
         "reason": "Submissions < 5k/round"},
        {"when": "value > 10_000", "agent_score": "moat", "delta_points": +5,
         "reason": "Submissions > 10k/round"},
    ],
    ("tournament", "numerai_fund_aum_usd"): [
        {"when": "value < 100_000_000", "agent_score": "moat", "delta_points": -10,
         "reason": "Fund AUM < $100M"},
        {"when": "value > 500_000_000", "agent_score": "moat", "delta_points": +5,
         "reason": "Fund AUM > $500M"},
    ],
    ("tournament", "net_stake_burn_vs_emission_rate_pct"): [
        {"when": "value < 0", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "Net inflationary (more emission than burn)"},
        {"when": "value > 0", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "Net deflationary"},
    ],
    ("tournament", "signals_product_active_users_count"): [
        {"when": "value < 500", "agent_score": "onchain", "delta_points": -10,
         "reason": "Signals users < 500"},
        {"when": "value > 2000", "agent_score": "onchain", "delta_points": +5,
         "reason": "Signals users > 2000"},
    ],

    # ── lst-aggregator (OGN) ────────────────────────────────────────
    ("lst-aggregator", "underlying_lst_mix_pct"): [
        {"when": "max(value.values()) > 0.70", "agent_score": "security", "delta_points": -10,
         "reason": "Single LST > 70% — concentrated"},
        {"when": "max(value.values()) <= 0.70", "agent_score": "security", "delta_points": +5,
         "reason": "Diversified LST mix"},
    ],
    ("lst-aggregator", "yield_capture_pct_to_ogn_or_token_stakers"): [
        {"when": "value < 0.10", "agent_score": "revenue", "delta_points": -10,
         "reason": "< 10% to token stakers — no claim"},
        {"when": "value > 0.30", "agent_score": "revenue", "delta_points": +5,
         "reason": "> 30% to token stakers"},
    ],
    ("lst-aggregator", "combined_product_tvl_total_usd"): [
        {"when": "value < 100_000_000", "agent_score": "moat", "delta_points": -10,
         "reason": "Combined TVL < $100M"},
        {"when": "value > 500_000_000", "agent_score": "moat", "delta_points": +5,
         "reason": "Combined TVL > $500M"},
    ],

    # ── bridge (W) ──────────────────────────────────────────────────
    ("bridge", "insurance_fund_vs_peak_tvl_pct"): [
        {"when": "value < 1", "agent_score": "security", "delta_points": -10,
         "reason": "Insurance < 1% of peak TVL"},
        {"when": "value > 5", "agent_score": "security", "delta_points": +5,
         "reason": "Insurance > 5% — confirmed cushion"},
    ],
    ("bridge", "competitor_share_loss_lz_ccip_axelar_pct"): [
        {"when": "value > 0.05", "agent_score": "moat", "delta_points": -10,
         "reason": "Losing > 5%/quarter share"},
        {"when": "value < 0", "agent_score": "moat", "delta_points": +5,
         "reason": "Gaining share"},
    ],

    # ── failed-l1 (LUNC) ────────────────────────────────────────────
    ("failed-l1", "monthly_burn_volume_vs_emission_net_supply_change"): [
        {"when": "value < 0", "agent_score": "tokenomics", "delta_points": -10,
         "reason": "Net inflationary"},
        {"when": "value > 0.01", "agent_score": "tokenomics", "delta_points": +5,
         "reason": "Net deflation > 1%/month"},
    ],
    ("failed-l1", "active_validator_count_trend_30d"): [
        {"when": "value < 0", "agent_score": "security", "delta_points": -10,
         "reason": "Validator count declining"},
        {"when": "value >= 0", "agent_score": "security", "delta_points": +5,
         "reason": "Validator count stable or rising"},
    ],
    ("failed-l1", "governance_proposal_participation_pct"): [
        {"when": "value < 0.30", "agent_score": "security", "delta_points": -10,
         "reason": "Governance turnout < 30%"},
        {"when": "value > 0.60", "agent_score": "security", "delta_points": +5,
         "reason": "Governance turnout > 60%"},
    ],
    ("failed-l1", "active_cex_listing_count"): [
        {"when": "value == 0", "agent_score": "moat", "delta_points": -10,
         "reason": "Zero T-1 CEX listings"},
        {"when": "value > 3", "agent_score": "moat", "delta_points": +5,
         "reason": "> 3 T-1 CEX listings"},
    ],
}


def get_rules(category: str, symbol: str, field: str) -> list[dict]:
    """Lookup rules for a (category|symbol, field) pair. Symbol-keyed rules
    override category-keyed rules — for token-specific addenda."""
    if (symbol, field) in DELTA_RULES:
        return DELTA_RULES[(symbol, field)]
    return DELTA_RULES.get((category, field), [])
