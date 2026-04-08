"""Structured prompts for manual categorization (replaces ad-hoc terminal I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _scalar_for_json(x: Any) -> Any:
    if x is None:
        return None
    try:
        import pandas as pd

        if isinstance(x, float) and pd.isna(x):
            return None
    except Exception:
        pass
    return x


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
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "fluid",
            "prompt_id": self.prompt_id,
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
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "resolve_static",
            "prompt_id": self.prompt_id,
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
    prompt_id: str = ""

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "kind": "new_store",
            "prompt_id": self.prompt_id,
            "store_name": _scalar_for_json(self.store_name),
            "date": _scalar_for_json(self.date),
            "expense": _scalar_for_json(self.expense),
            "income": _scalar_for_json(self.income),
            "details": _scalar_for_json(self.details),
            "digits": _scalar_for_json(self.digits),
            "all_categories": list(self.all_categories),
        }
