"""Commit user-approved registry rows into shared/tokens.py.

Inputs:
  data/maa/proposed_registry.json   — resolved rows (user-edited if needed)
  reports/_maa_top20_2026-05-06.json — for dropped-symbols computation

Outputs:
  shared/tokens.py                  — patched in-place (new entries appended)
  data/maa/registry.committed.flag  — gate sentinel for run_conviction_batch.py
  data/maa/dropped_symbols.json     — top-20 entries NOT in the final registry

Run this MANUALLY after reviewing proposed_registry.json. The runner halts
until the .flag file exists.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

from shared import tokens as token_registry


def render_token_block(row):
    """Render one row as a Python string `"SYM": Token(...),` for tokens.py."""
    def py_repr(v):
        if v is None:
            return "None"
        return f"\"{v}\""

    return (
        f'    "{row["symbol"]}": Token(\n'
        f'        symbol="{row["symbol"]}",\n'
        f'        name="{row["name"]}",\n'
        f'        chain="{row["chain"]}",\n'
        f'        coingecko_id="{row["coingecko_id"]}",\n'
        f'        contract_address={py_repr(row.get("contract_address"))},\n'
        f'        defillama_protocol={py_repr(row.get("defillama_protocol"))},\n'
        f'        category="{row.get("category", "")}",\n'
        f'        notes="{row.get("notes", "")}",\n'
        f'    ),'
    )


def patch_tokens_py(path, blocks):
    """Insert blocks before the closing brace of REGISTRY dict."""
    src = path.read_text()
    reg_match = re.search(r"REGISTRY:\s*dict\[[^\]]+\]\s*=\s*\{", src)
    if not reg_match:
        raise RuntimeError(f"Couldn't find REGISTRY dict in {path}")
    start = reg_match.end()
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    insertion = "\n".join(blocks) + "\n"
    new_src = src[:i] + insertion + src[i:]
    path.write_text(new_src)


def compute_dropped_symbols(top20, resolved, existing):
    """A symbol is dropped if it's in top20 but not in (resolved ∪ existing)."""
    resolved_syms = {r["symbol"] for r in resolved}
    final_syms = resolved_syms | existing
    dropped = []
    for entry in top20:
        if entry["symbol"] not in final_syms:
            dropped.append({
                "symbol": entry["symbol"],
                "rank": entry.get("rank"),
                "name": entry.get("name"),
                "reason": "not_in_resolved_registry",
            })
    return dropped


def run(
    *,
    proposed_path,
    top20_path,
    tokens_py_path,
    flag_path,
    dropped_path,
    existing_symbols,
):
    proposed = json.loads(proposed_path.read_text())
    if not isinstance(proposed, list):
        raise RuntimeError("proposed_registry.json must be a JSON array")

    blocks = [render_token_block(r) for r in proposed]
    if blocks:
        patch_tokens_py(tokens_py_path, blocks)

    top20 = json.loads(top20_path.read_text())
    dropped = compute_dropped_symbols(top20, proposed, existing_symbols)
    dropped_path.parent.mkdir(parents=True, exist_ok=True)
    dropped_path.write_text(json.dumps(dropped, indent=2))

    flag_path.touch()


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--proposed", default="data/maa/proposed_registry.json",
                   type=pathlib.Path)
    p.add_argument("--top20", default="reports/_maa_top20_2026-05-06.json",
                   type=pathlib.Path)
    p.add_argument("--tokens-py", default="shared/tokens.py", type=pathlib.Path)
    p.add_argument("--flag", default="data/maa/registry.committed.flag",
                   type=pathlib.Path)
    p.add_argument("--dropped", default="data/maa/dropped_symbols.json",
                   type=pathlib.Path)
    args = p.parse_args(argv)

    if not args.proposed.exists() or not args.top20.exists():
        print(f"Missing: {args.proposed} or {args.top20}", file=sys.stderr)
        return 2

    existing = set(token_registry.REGISTRY.keys())
    run(proposed_path=args.proposed, top20_path=args.top20,
        tokens_py_path=args.tokens_py, flag_path=args.flag,
        dropped_path=args.dropped, existing_symbols=existing)

    n_resolved = len(json.loads(args.proposed.read_text()))
    n_dropped = len(json.loads(args.dropped.read_text()))
    print(f"Committed {n_resolved} new registry rows.")
    print(f"Flag created: {args.flag}")
    print(f"Dropped symbols ({n_dropped}): {args.dropped}")
    if n_dropped > 0:
        print("WARNING: Some top-20 symbols are NOT in the registry — they will be skipped at batch time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
