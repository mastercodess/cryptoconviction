"""
One-shot cleanup for agents/04_onchain/data/onchain.db.

Removes pollution from the early collect.py runs that wrote string literals
("NOT_AVAILABLE_FREE_TIER" etc.) into REAL columns, and dedupes
onchain_research_note rows that had identical bodies before _upsert_note
was wired up.

Idempotent: running it twice is fine — the second run is a no-op.

    python -m scripts.clean_onchain_db
or
    python3 scripts/clean_onchain_db.py
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

DB = (pathlib.Path(__file__).resolve().parents[1]
      / "agents" / "04_onchain" / "data" / "onchain.db")


def _is_non_numeric(c: sqlite3.Connection, table: str, cols: list[str]) -> int:
    """Count rows where ANY of `cols` holds a non-numeric, non-null value."""
    where = " OR ".join(
        f"({col} IS NOT NULL AND typeof({col}) NOT IN ('integer','real'))"
        for col in cols
    )
    return c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def _delete_non_numeric(c: sqlite3.Connection, table: str, cols: list[str]) -> int:
    where = " OR ".join(
        f"({col} IS NOT NULL AND typeof({col}) NOT IN ('integer','real'))"
        for col in cols
    )
    cur = c.execute(f"DELETE FROM {table} WHERE {where}")
    return cur.rowcount


def _dedupe_notes(c: sqlite3.Connection) -> int:
    """For each (token_symbol, topic, body), keep only the row with the
    largest id; delete the rest."""
    cur = c.execute(
        """
        DELETE FROM onchain_research_note
        WHERE id NOT IN (
            SELECT MAX(id) FROM onchain_research_note
            GROUP BY token_symbol, topic, body
        )
        """
    )
    return cur.rowcount


def main() -> int:
    if not DB.exists():
        print(f"DB not found at {DB}", file=sys.stderr)
        return 1
    c = sqlite3.connect(DB)
    print(f"DB: {DB}\n")

    plan = {
        "exchange_flow": ["inflow_usd", "outflow_usd", "net_usd"],
        "holder_cohort": ["lth_supply_pct", "sth_supply_pct"],
        "activity_metric": [
            "dau", "wau", "mau", "dau_mau_ratio",
            "daily_tx_count", "new_addresses_7d",
        ],
    }

    print("--- BEFORE ---")
    for tbl, cols in plan.items():
        print(f"  {tbl:<18} non-numeric rows: {_is_non_numeric(c, tbl, cols)}")
    note_dups_before = c.execute(
        """SELECT COUNT(*) - COUNT(DISTINCT body || '|' || token_symbol || '|' || topic)
           FROM onchain_research_note"""
    ).fetchone()[0]
    print(f"  research_note duplicates: {note_dups_before}")

    print("\n--- CLEANING ---")
    total = 0
    for tbl, cols in plan.items():
        n = _delete_non_numeric(c, tbl, cols)
        total += n
        print(f"  {tbl:<18} deleted: {n}")
    n = _dedupe_notes(c)
    total += n
    print(f"  research_note dedupe deleted: {n}")
    c.commit()

    print("\n--- AFTER ---")
    for tbl, cols in plan.items():
        print(f"  {tbl:<18} non-numeric rows: {_is_non_numeric(c, tbl, cols)}")
    print(f"  research_note rows: "
          f"{c.execute('SELECT COUNT(*) FROM onchain_research_note').fetchone()[0]}")

    print(f"\nTotal rows removed: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
