"""Category suggestions for the categorize queue (read stored CatBoost scores)."""

from __future__ import annotations

from typing import Any, Mapping, Union

import pandas as pd

from ledger.category_model import (
    CategorySuggestion,
    feature_row_from_mapping,
    load_stored_suggestions,
)

# Re-export for call sites / tests.
__all__ = [
    "CategorySuggestion",
    "suggest_categories_for_row",
]


def suggest_categories_for_row(
    db_path: str,
    query_row: Union[pd.Series, Mapping[str, Any]],
    *,
    k: int = 15,  # noqa: ARG001 — kept for call-site compatibility; unused
    top_n: int = 3,  # noqa: ARG001
    min_confidence: float = 0.35,  # noqa: ARG001
) -> list[CategorySuggestion]:
    """Return stored top category suggestions for the row fingerprint.

    Scores are written by :mod:`ledger.category_suggest_job` at ingest and after
    manual saves. This function does not train, embed, or write ``קטגוריה``.
    """
    row = feature_row_from_mapping(query_row)
    suggestions = load_stored_suggestions(db_path, row.fingerprint)
    if top_n > 0:
        return suggestions[:top_n]
    return suggestions
