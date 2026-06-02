# Sample outputs

The files in this directory are **synthetic illustrations** of what the
conviction system produces. They use plausible-looking but **fabricated**
numbers on a neutral, widely-known asset (a generic Layer-1 chain referred
to as `DEMO`). No file here reflects any real position, conviction, or
allocation by the project's operator.

If you want to reproduce real outputs against current market data, run the
full pipeline on a token of your choice — see the root `README.md` quick-
start.

## Files

- **`conviction_DEMO.md`** — full human-readable conviction report (verdict, bull/bear case, invalidation conditions, monitoring checklist)
- **`conviction_DEMO.json`** — the same content as structured data (the `FinalVerdict` Pydantic schema)
- **`agent_03_security_DEMO.json`** — one specialist agent's full output, showing the schema each of the seven specialists conforms to
- **`conviction_summary_DEMO.md`** — what the ranked multi-token summary looks like
