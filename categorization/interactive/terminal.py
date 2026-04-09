"""Terminal-based categorization prompts (legacy behavior, bidi-safe display)."""

from __future__ import annotations

import logging
import re

import pandas as pd
from bidi.algorithm import get_display

from categorization.interactive.prompts import (
    FluidStorePrompt,
    NewStorePrompt,
    ResolveStaticPrompt,
)

log = logging.getLogger(__name__)
_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")


def _terminal_bidi(s):
    if s is None:
        return ""
    if isinstance(s, float) and pd.isna(s):
        return "nan"
    text = str(s)
    if _HEBREW_RE.search(text):
        return get_display(text)
    return text


def _terminal_bidi_seq(seq, left="{", right="}", *, sort=True):
    items = sorted(seq, key=lambda v: str(v)) if sort else list(seq)
    parts = []
    for x in items:
        if isinstance(x, float) and pd.isna(x):
            parts.append("nan")
        else:
            parts.append(repr(_terminal_bidi(str(x))))
    return left + ", ".join(parts) + right


class TerminalCategorizationHandler:
    """stdin/stdout prompts matching the original categorizer behavior."""

    def prompt_fluid_store(self, prompt: FluidStorePrompt) -> str:
        all_categories = set(prompt.all_categories)
        dynamic_categories = list(prompt.dynamic_categories)
        log.info("Transaction details (interactive categorize)")
        log.info(
            "Transaction source: %s | Date: %s | Expense: %s | Income: %s | Details: %s | Digits: %s",
            _terminal_bidi(prompt.store_name),
            prompt.date,
            prompt.expense,
            prompt.income,
            _terminal_bidi(prompt.details),
            _terminal_bidi(prompt.digits),
        )
        log.info(
            "Past categories for %s: %s",
            _terminal_bidi(prompt.store_name),
            _terminal_bidi_seq(dynamic_categories, "[", "]", sort=False),
        )
        log.info("All categories: %s", _terminal_bidi_seq(all_categories))
        category_input = input(
            f"Choose a category for {_terminal_bidi(prompt.store_name)} from the categories, or type a new one: "
        ).strip()
        return category_input

    def prompt_resolve_static(self, prompt: ResolveStaticPrompt) -> int:
        log.info("Transaction details (fix static flag)")
        log.info(
            "Transaction source: %s | Date: %s | Expense: %s | Income: %s | Details: %s | Digits: %s",
            _terminal_bidi(prompt.store_name),
            prompt.date,
            prompt.expense,
            prompt.income,
            _terminal_bidi(prompt.details),
            _terminal_bidi(prompt.digits),
        )
        is_static_input = input(
            f"Is this category: [{_terminal_bidi(prompt.category)}] for {_terminal_bidi(prompt.store_name)} static?"
            f"\n (Type '0' if dynamic, type '1' if static): "
        ).strip()
        try:
            v = int(is_static_input)
        except ValueError:
            return -1
        return v if v in (0, 1) else -1

    def prompt_new_store(self, prompt: NewStorePrompt) -> tuple[str, int]:
        all_categories = set(prompt.all_categories)
        log.info("Transaction details (new store)")
        log.info(
            "Transaction source: %s | Date: %s | Expense: %s | Income: %s | Details: %s | Digits: %s",
            _terminal_bidi(prompt.store_name),
            prompt.date,
            prompt.expense,
            prompt.income,
            _terminal_bidi(prompt.details),
            _terminal_bidi(prompt.digits),
        )
        log.info("All categories: %s", _terminal_bidi_seq(all_categories))
        category_input = input(
            f"{_terminal_bidi(prompt.store_name)} is not in the store list. "
            f"Choose a category from the list or type a new one: "
        ).strip()
        is_static_input = input(
            f"Should {_terminal_bidi(prompt.store_name)} be under static category? Type 1 for static and 0 for fluid: "
        ).strip()
        return category_input, int(is_static_input)
