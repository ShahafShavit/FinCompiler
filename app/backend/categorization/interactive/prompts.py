"""Structured prompts for manual categorization (replaces ad-hoc terminal I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _scalar_for_json(x: Any) -> Any:
    """Normalize CSV/pandas cell values so :func:`json.dumps` is browser-safe (no numpy types, no NaN)."""
    if x is None:
        return None
    try:
        import pandas as pd

        if isinstance(x, pd.Timestamp):
            if x.hour == 0 and x.minute == 0 and x.second == 0 and x.microsecond == 0:
                return x.strftime("%Y-%m-%d")
            return x.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    try:
        from datetime import date, datetime

        if isinstance(x, datetime):
            if x.hour == 0 and x.minute == 0 and x.second == 0 and x.microsecond == 0:
                return x.date().isoformat()
            return x.isoformat(timespec="seconds")
        if isinstance(x, date):
            return x.isoformat()
    except Exception:
        pass
    try:
        import numpy as np

        if isinstance(x, np.integer):
            return int(x)
        if isinstance(x, np.floating):
            v = float(x)
            if v != v or v in (float("inf"), float("-inf")):  # nan / inf
                return None
            return v
        if isinstance(x, np.bool_):
            return bool(x)
    except Exception:
        pass
    try:
        import pandas as pd

        if isinstance(x, float) and pd.isna(x):
            return None
    except Exception:
        pass
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    try:
        import pandas as pd

        if pd.isna(x) and not isinstance(x, (str, bytes)):
            return None
    except Exception:
        pass
    try:
        import json

        json.dumps(x)
        return x
    except (TypeError, ValueError):
        return str(x)


@dataclass(frozen=True)
class FluidStorePrompt:
    """Existing store with fluid (non-static) categories — pick or type a category."""

    store_name: str
    date: Any
    expense: Any
    income: Any
    details: Optional[Any]
    digits: Optional[Any]
    dynamic_categories: tuple[str, ...]
    all_categories: tuple[str, ...]
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "fluid",
            "prompt_id": self.prompt_id,
            "transaction_id": _scalar_for_json(self.transaction_id),
            "store_name": _scalar_for_json(self.store_name),
            "date": _scalar_for_json(self.date),
            "expense": _scalar_for_json(self.expense),
            "income": _scalar_for_json(self.income),
            "details": _scalar_for_json(self.details),
            "digits": _scalar_for_json(self.digits),
            "dynamic_categories": list(self.dynamic_categories),
            "all_categories": list(self.all_categories),
        }


@dataclass(frozen=True)
class ResolveStaticPrompt:
    """Ambiguous is_static flag on a store row — user picks 0 (fluid) or 1 (static)."""

    store_name: str
    category: str
    date: Any
    expense: Any
    income: Any
    details: Optional[Any]
    digits: Optional[Any]
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "resolve_static",
            "prompt_id": self.prompt_id,
            "transaction_id": _scalar_for_json(self.transaction_id),
            "store_name": _scalar_for_json(self.store_name),
            "category": _scalar_for_json(self.category),
            "date": _scalar_for_json(self.date),
            "expense": _scalar_for_json(self.expense),
            "income": _scalar_for_json(self.income),
            "details": _scalar_for_json(self.details),
            "digits": _scalar_for_json(self.digits),
        }


@dataclass(frozen=True)
class NewStorePrompt:
    """Store not in list — choose category and whether mapping is static or fluid."""

    store_name: str
    date: Any
    expense: Any
    income: Any
    details: Optional[Any]
    digits: Optional[Any]
    all_categories: tuple[str, ...]
    transaction_id: str = ""
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "new_store",
            "prompt_id": self.prompt_id,
            "transaction_id": _scalar_for_json(self.transaction_id),
            "store_name": _scalar_for_json(self.store_name),
            "date": _scalar_for_json(self.date),
            "expense": _scalar_for_json(self.expense),
            "income": _scalar_for_json(self.income),
            "details": _scalar_for_json(self.details),
            "digits": _scalar_for_json(self.digits),
            "all_categories": list(self.all_categories),
        }
