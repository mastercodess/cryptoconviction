# Crypto Conviction System

A multi-agent research system that produces a systematic 0–100 conviction
score for individual cryptocurrencies, grounded in seven independent
specialist analyses + a weighted orchestrator. Each agent uses the
**Recursive Language Model** pattern (Zhang/Kraska/Khattab, Jan 2026) so
long source documents — whitepapers, audit PDFs, unlock CSVs, Dune query
exports — never flood the root model's context.

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │  Initial coverage: LINK AAVE ONDO ENA SUI    │
                    │   AERO OGN NMR AVNT XMR  (extend in tokens.py)│
                    └──────────────────────────────────────────────┘
                                          │
       ┌──────────────────────────────────┴──────────────────────────────┐
       │                   collect.py per agent                          │
       │   pulls data from CoinGecko / DefiLlama / Etherscan / GitHub    │
       │   + Sonnet research() for narrative / unlocks / mechanism       │
       │   stores: SQLite (structured) + JSON sidecars (raw)             │
       └──────────────────────────────────┬──────────────────────────────┘
                                          │
       ┌──────┬──────┬──────┬──────┬──────┼──────┬──────┐
       ▼      ▼      ▼      ▼      ▼      ▼      ▼
   Agent 1  2      3      4      5      6      7
   Token   Rev   Sec   On-   Team  Moat  Macro    ← analyze.py per agent
   ecnmcs  /TVL  /Audt chain  /VC                   each runs an RLM:
                                                    • root = Opus 4.x
                                                    • sub_lm() = Sonnet 4.x
                                                    • REPL with token's DB
                                                      loaded as variable
       └──────┴──────┴──────┴──────┴──────┴──────┘
                                          │
                                          ▼
                              Agent 8 — Orchestrator
                              reads 7 JSONs, applies
                              weighted score + red-flag
                              rules from config.yaml,
                              produces final verdict +
                              bull/bear/invalidation/monitoring
                                          │
                                          ▼
                              reports/{SYMBOL}/conviction.{json,md}
```

## Why RLM?

A single token's full dossier — whitepaper, multiple audit PDFs, top-50
holder snapshots, unlock CSVs, monthly fee history, governance proposals,
team bios, peer-comparison tables — is easily 100k–500k tokens. Stuffing
that into the root model's context is what the paper calls a "flawed
scaffold": you hit context-rot before you finish reading.

In RLM, the root model writes Python that peeks at the dossier (loaded as
a REPL variable), filters with SQL/regex/pandas, and delegates dense
reads to `sub_lm()` running Sonnet — only stitched results return to the
root's history. See `shared/rlm.py` for the implementation.

## Quick start

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Set your Anthropic API key
cp .env.example .env
# edit .env, fill in ANTHROPIC_API_KEY

# 3. (Already done for you) Seed sidecars are in agents/01_tokenomics/data/sidecars/
#    Initial SQLite DB is populated. Run this if starting fresh:
python -m agents.01_tokenomics.ingest_seed_data

# 4. Verify the pipeline (no API key needed for the smoke test):
python scripts/smoke_test.py

# 5. Produce a real Agent 1 report (requires API key):
python -m agents.01_tokenomics.analyze LINK
cat reports/LINK/agent_01_tokenomics.json
```

## Running the full system for one token

```bash
SYMBOL=LINK

# Collect (refreshes the per-agent DBs)
for n in 01_tokenomics 02_revenue 03_security 04_onchain 05_team 06_moat 07_macro; do
    python -m agents.$n.collect $SYMBOL
done

# Analyze (each call is one RLM trajectory; Opus root + Sonnet sub-calls)
for n in 01_tokenomics 02_revenue 03_security 04_onchain 05_team 06_moat 07_macro; do
    python -m agents.$n.analyze $SYMBOL
done

# Synthesize
python -m agents.08_orchestrator.orchestrator $SYMBOL

# Read it
cat reports/$SYMBOL/conviction.md
```

## Running from inside Claude Code

The Claude Code subagent configs in `.claude/agents/` mean you can drop
into Claude Code in this directory and just say:

> "Run agent-01-tokenomics on LINK"
> "Then agent-08-orchestrator"

Each subagent knows how to invoke its Python module and surface results.
They default to Sonnet to keep cost down; the planning brain (you, in the
top-level Claude Code session) is Opus.

## What's in this repo

```
CryptoConvictionSystem/
├── README.md                      ← you are here
├── config.yaml                    ← orchestrator weights, risk tier, red-flag rules
├── requirements.txt
├── .env.example
├── .claude/agents/                ← Claude Code subagent configs (8 of them)
├── shared/
│   ├── rlm.py                     ← RLM scaffold (Algorithm 1 from the paper)
│   ├── llm_client.py              ← sub_lm + research_json helpers
│   ├── tokens.py                  ← single-source-of-truth token registry
│   ├── schemas.py                 ← Pydantic output schemas (one per agent)
│   └── data_sources/              ← CoinGecko / DefiLlama / Etherscan clients
├── agents/
│   ├── 01_tokenomics/             ← FULLY IMPLEMENTED + DATA LOADED
│   ├── 02_revenue/                ← scaffolded, run collect to populate
│   ├── 03_security/               ← scaffolded
│   ├── 04_onchain/                ← scaffolded
│   ├── 05_team/                   ← scaffolded
│   ├── 06_moat/                   ← scaffolded
│   ├── 07_macro/                  ← scaffolded
│   └── 08_orchestrator/           ← FULLY IMPLEMENTED, runs once 1-7 produce JSONs
├── reports/{SYMBOL}/              ← per-token agent_0X.json + conviction.{json,md}
└── scripts/smoke_test.py          ← end-to-end pipeline test (no API key required)
```

## Phase 1 status (what's seeded)

Agent 1 (Token Economics) is **fully running** with collected data for
all 10 tokens. Verify with:

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("agents/01_tokenomics/data/tokenomics.db")
for sym, mc, circ, mx in c.execute(
    "SELECT token_symbol, market_cap_usd, circulating, max_supply "
    "FROM supply_snapshot ORDER BY market_cap_usd DESC"
):
    print(f"{sym:6} ${mc/1e6:>7.0f}M  circ={circ/1e6:>9.1f}M  max={mx/1e6 if mx else '?':>9} M")
PY
```

You should see all 10 rows. Unlock events (16 across LINK / ONDO / ENA /
SUI / AERO), mechanism rows (10), and 16 research notes are all in the DB.

The other agents have their schemas, collect.py, analyze.py, and
subagent configs in place. Run `python -m agents.NAME.collect SYMBOL` to
fetch data for any one of them.

## Phase 2 — what's next

Things you'll likely want to do once you've kicked the tires:

1. **Expand the token list.** Add to `shared/tokens.py` REGISTRY. Look up
   the CoinGecko ID and contract address before adding.
2. **Tune `config.yaml` weights** to match your investing thesis. The
   defaults are a balanced 7-way split.
3. **Plug in paid sources where they're worth it.**
   - **Token Terminal Pro** ($) gives you clean P/S, P/TVL, real-yield numbers
     directly. Wire into `agents/02_revenue/collect.py` via the new env var.
   - **Nansen** ($$) gives smart-money labels for Agent 4. Worth it if you
     trade short-to-medium horizon.
   - **Glassnode** ($$) for proper LTH supply curves. Replaces the Sonnet
     research approximation.
   - Etherscan Pro ($199/mo) gives you free top-holder snapshots; this is
     the biggest single Agent 1 free-tier gap. The schema's
     `holder_snapshot` table is ready to receive them.
4. **Pre-run the full pipeline weekly** with a cron job so reports are
   always fresh. The `schedule` skill works for this.
5. **Build a comparison view.** I haven't built `compare.py` yet — it
   would walk `reports/*/conviction.json`, sort by weighted_conviction,
   render a single Markdown table.
6. **Backtest the conviction score against price action.** Save
   conviction.json to a versioned archive (e.g. `reports/_history/{date}/`)
   so 6 months from now you can correlate score vs return.

## Cost expectations

A full run for one token (Agent 1–7 collect + analyze + orchestrator):
roughly **$0.30–$1.50** depending on how many sub_lm() calls each RLM
trajectory makes (variance is high — see Observation 4 in the paper).
The orchestrator narrative call is fixed at one Sonnet shot, ~$0.05.

The root LLM is Opus by default (per your request), Sonnet for sub-calls
and research. You can flip both via `RLM_ROOT_MODEL` / `RLM_SUB_MODEL` in
`.env`.

## What this system intentionally does NOT do

- It does NOT execute trades or move funds. It only produces conviction.
- It does NOT replace your judgment. Every `composite_score` has a
  `rationale` field — read them. The score without the rationale is
  cargo-cult quant.
- It does NOT auto-update. You run `collect.py` when you want fresh data.
  Scheduling that is on you (or on the `schedule` skill).
- It does NOT track P/L on positions you take. The monitoring_checklist
  in conviction.md is a list of things to watch — manual.

---

_Built with the Recursive Language Model pattern from
[Zhang, Kraska, Khattab (2026)](https://arxiv.org/abs/2512.24601).
Root model: Claude Opus 4.6/4.7. Sub-LLM: Claude Sonnet 4.6._
