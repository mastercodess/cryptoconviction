# Crypto Conviction System

A multi-agent research system that produces systematic 0–100 conviction scores for individual cryptocurrencies, grounded in seven independent specialist analyses and a weighted synthesis. Each specialist uses a **Recursive Language Model** loop ([Zhang, Kraska, Khattab, Jan 2026](https://arxiv.org/abs/2512.24601)) so long source documents — whitepapers, audit reports, holder snapshots, unlock schedules, fee histories — never have to fit in the root model's context window.

## Why this exists

A single token's full dossier is easily 100k–500k tokens. Stuffing all of it into a single prompt produces what the RLM paper calls a "flawed scaffold": the model loses track of what matters before it finishes reading. Real research analysts don't read everything either — they query, filter, and follow up on what's interesting.

This system implements that workflow:

- The **root model** writes Python that peeks at the dossier loaded as a REPL variable
- It filters with SQL/regex/pandas and delegates dense reads to a cheaper **sub-LLM**
- Only the stitched, summarized results return to the root's history
- The **orchestrator** combines seven specialists' verdicts via configurable weights and red-flag rules

The result: each token's research stays grounded in primary data without bloating the conversation, and every score traces back to a citable rationale.

## Architecture

```
                            ┌─────────────────────────────┐
                            │  Token registry (31 assets) │
                            │  shared/tokens.py           │
                            └──────────────┬──────────────┘
                                           │
       ┌───────────────────────────────────┴─────────────────────────────────┐
       │  collect.py per agent                                               │
       │   • CoinGecko / DefiLlama / Etherscan / Dune / FRED / GitHub        │
       │   • Sub-LLM research() for narrative-heavy fields                   │
       │   • Persists to per-agent SQLite + JSON sidecars                    │
       └───────────────────────────────────┬─────────────────────────────────┘
                                           │
       ┌──────┬──────┬──────┬──────┬───────┼──────┬──────┐
       ▼      ▼      ▼      ▼      ▼       ▼      ▼
   Agent 1  2      3      4      5       6      7
   Token   Rev   Sec   On-   Team    Moat   Macro    ← analyze.py per agent
   ecnmcs  TVL   Audit  chain  VC                       each runs an RLM:
                                                         • root writes Python
                                                         • sub_lm() drills deep
                                                         • REPL holds dossier
       └──────┴──────┴──────┴──────┴───────┴──────┘
                                           │
                                           ▼
                          Agent 8 — Orchestrator
                          reads 7 specialist JSONs,
                          applies weighted score +
                          red-flag rules from config.yaml,
                          emits conviction.{json,md}
```

## What each specialist measures

| Agent | Focus | Key output fields |
|---|---|---|
| **1. Tokenomics** | Supply, vesting, holder concentration, unlock pressure | `fdv_risk_rating`, `top10_holding_pct`, `unlock_pressure_next_90d_pct` |
| **2. Revenue** | Protocol revenue, P/S, P/TVL, real vs inflationary yield | `annualized_revenue_usd`, `p_s_ratio`, `real_yield_apr`, `growth_trend` |
| **3. Security** | Audit history, exploits, upgrade mechanism, SPOFs | `security_tier`, `audit_coverage_score`, `incident_history_severity` |
| **4. On-chain** | DAU/MAU, capital flows, holder cohorts, smart money | `organic_activity_score`, `capital_flow_direction`, `smart_money_stance` |
| **5. Team & investors** | Founder credibility, VC overhang, legal exposure | `founder_credibility_score`, `vc_overhang_risk`, `trust_tier` |
| **6. Competitive moat** | Category rank, network-effect type, TVL share | `moat_strength_score`, `network_effect_type`, `competitive_threat` |
| **7. Macro & cycle** | Cycle phase, BTC correlation, leverage warnings | `cycle_phase`, `entry_timing_risk`, `macro_rating` |

Schemas are validated by Pydantic in `shared/schemas.py`. The orchestrator refuses to emit a score if too few agents converged (configurable in `config.yaml` via `min_agent_coverage_pct`).

## Quick start

```bash
# 1. Install deps (Python 3.11+)
pip install -r requirements.txt

# 2. Set your LLM provider key (Anthropic by default)
cp .env.example .env
# edit .env, fill in ANTHROPIC_API_KEY

# 3. Verify the pipeline (no API key needed for the smoke test)
python -m pytest tests/

# 4. Run agent 1 on one token (requires API key)
python -m agents.01_tokenomics.collect LINK
python -m agents.01_tokenomics.analyze LINK
cat reports/LINK/agent_01_tokenomics.json
```

## Running the full pipeline for one token

```bash
SYMBOL=LINK

# Collect (refreshes the per-agent DBs)
for n in 01_tokenomics 02_revenue 03_security 04_onchain 05_team 06_moat 07_macro; do
  python -m agents.$n.collect $SYMBOL
done

# Analyze (each call is one RLM trajectory)
for n in 01_tokenomics 02_revenue 03_security 04_onchain 05_team 06_moat 07_macro; do
  python -m agents.$n.analyze $SYMBOL
done

# Synthesize
python -m agents.08_orchestrator.orchestrator $SYMBOL

# Read it
cat reports/$SYMBOL/conviction.md
```

## Beyond the orchestrator: manual reconciliation

The uniform per-agent schema fits DeFi protocols cleanly but mis-measures categories like privacy L1s, oracles, RWA tokens, and memes — each of which has unique value drivers the schema can't capture. The repository ships a **manual research overlay** that:

- Generates per-token JSON worksheets enumerating category-specific gaps the schema misses (`scripts/generate_manual_research_worksheets.py`)
- Pre-fills worksheet values from existing agent sidecars where data already exists (`scripts/prefill_from_sidecars.py`)
- Surfaces the highest-impact unfilled gaps per token (`scripts/critical_path.py`)
- Reconciles user-supplied values back into a revised conviction score via structured `delta_rules` (`scripts/reconcile_manual_research.py`)
- Ranks all tokens in a single sortable summary (`scripts/conviction_summary.py`)

See `samples/` for an illustrative end-to-end conviction report.

## Repository layout

```
CryptoConvictionSystem/
├── README.md
├── LICENSE
├── config.yaml                  ← orchestrator weights + red-flag rules
├── requirements.txt
├── .env.example
├── shared/
│   ├── rlm.py                   ← Recursive Language Model scaffold
│   ├── llm_client.py            ← sub_lm + research helpers
│   ├── tokens.py                ← token registry (31 assets across 15+ categories)
│   ├── schemas.py               ← Pydantic output schemas (one per agent)
│   ├── freshness.py             ← per-agent staleness gating
│   ├── pricing.py               ← per-call cost computation
│   ├── phase_status.py          ← per-phase run-status persistence
│   ├── db_helpers.py            ← SQLite read/write conventions
│   └── data_sources/            ← CoinGecko / DefiLlama / Dune / FRED / alt.me clients
├── agents/
│   ├── 01_tokenomics/           ← supply, holders, vesting
│   ├── 02_revenue/              ← protocol fees, P/S, real yield
│   ├── 03_security/             ← audits, exploits, upgrade mechanism
│   ├── 04_onchain/              ← DAU/MAU, capital flows, cohorts
│   ├── 05_team/                 ← founder credibility, VC overhang, legal
│   ├── 06_moat/                 ← category rank, network effects
│   ├── 07_macro/                ← cycle phase, BTC correlation, sentiment
│   └── 08_orchestrator/         ← weighted synthesis + red-flag rules
├── scripts/
│   ├── generate_manual_research_worksheets.py
│   ├── manual_research_delta_rules.py
│   ├── prefill_from_sidecars.py
│   ├── critical_path.py
│   ├── reconcile_manual_research.py
│   └── conviction_summary.py
├── samples/                     ← illustrative output (synthetic data)
└── tests/                       ← 274 tests across data sources, RLM, freshness, agents
```

## Configuration

Open `config.yaml` to tune:

- `agent_weights` — how much each specialist contributes to the final 0–100 score (must sum to 1.0)
- `red_flags` — auto-reject rules:
  - `reject_if_security_below` — minimum tier on the 1–5 security scale
  - `reject_if_holder_concentration_above` — top-10 share threshold
  - `reject_if_unlock_pressure_next_90d_above` — supply unlocking next 90d
  - `min_agent_coverage_pct` — minimum fraction of intended weight that must load
  - `max_data_age_hours` (+ per-agent overrides) — freshness gating
- `horizon`, `risk_tolerance`, `target_position_pct` — sizing inputs

## Costs

One full token run (collect + analyze across all 7 agents + orchestrator narrative):

- **Typical**: $0.30–$1.50
- **Distribution**: high variance — sub-LLM call counts depend on how many drill-downs the RLM trajectory needs
- The orchestrator's narrative synthesis is a fixed ~$0.05

Use a smaller model for `RLM_SUB_MODEL` if you want to cap costs further.

## What this system intentionally does NOT do

- **It does not execute trades or move funds.** It only produces conviction scores and rationales.
- **It does not replace judgment.** Every `composite_score` ships with a `rationale` field and a `monitoring_checklist`. The score without the rationale is cargo-cult quant.
- **It does not auto-update.** You run `collect.py` when you want fresh data. Scheduling that is on the operator.
- **It does not backtest.** That's downstream work (correlation of historical conviction snapshots vs realized return).

## Tests

```bash
python -m pytest tests/                    # 274 tests
python -m pytest tests/test_rlm_*.py       # RLM scaffold
python -m pytest tests/test_freshness.py   # staleness gating
python -m pytest tests/test_data_sources_*.py  # external API clients
```

## License

MIT. See `LICENSE`.
