"""
``ingested_at`` on ``ledger_transaction`` (schema v8+).

**Pipeline upsert** sets ``ingested_at`` to the calendar date the row is first written
(:func:`ingested_at_for_new_ledger_row` — local ``date.today()``). On conflict, SQLite keeps
the existing ``ingested_at`` (first insert wins).

There is no separate heuristic import path; the ledger is populated only via the pipeline and
explicit tooling — not from a ``web_totals`` CSV.
"""

from __future__ import annotations

from datetime import date


def ingested_at_for_new_ledger_row() -> str:
    """``YYYY-MM-DD`` for a row being inserted by the live pipeline (wall-clock local date)."""
    return date.today().isoformat()
