"""Extra ledgers. The active book selects the SQLite file. Queries stay the same."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

import config

BOOK_PERSONAL = "personal"
BOOK_SHARED = "shared"

_active_book: ContextVar[str] = ContextVar("finance_book", default=BOOK_PERSONAL)


def _specs() -> dict[str, dict[str, Any]]:
    """Read paths at call time so tests can reload ``config``."""
    return {
        BOOK_PERSONAL: {
            "id": BOOK_PERSONAL,
            "label": "אישי",
            "path": config.ledger_db_file,
            "fetcher": None,
            "backfill": False,
        },
        BOOK_SHARED: {
            "id": BOOK_SHARED,
            "label": "One Zero",
            "path": config.shared_ledger_file,
            "fetcher": "onezero",
            "backfill": True,
        },
    }


def list_books() -> list[dict[str, Any]]:
    specs = _specs()
    return [
        {k: spec[k] for k in ("id", "label", "fetcher", "backfill")}
        for spec in (specs[BOOK_PERSONAL], specs[BOOK_SHARED])
    ]


def book_spec(book_id: str) -> dict[str, Any] | None:
    return _specs().get(book_id)


def ledger_path(book_id: str) -> str:
    spec = book_spec(book_id)
    if spec is None:
        raise KeyError(book_id)
    return str(spec["path"])


def active_book_id() -> str:
    return _active_book.get()


def active_book() -> dict[str, Any]:
    spec = book_spec(active_book_id())
    if spec is None:
        return _specs()[BOOK_PERSONAL]
    return spec


def active_ledger_path() -> str:
    return str(active_book()["path"])


def personal_ledger_path() -> str:
    return config.ledger_db_file


def set_active_book(book_id: str) -> Token[str]:
    if book_spec(book_id) is None:
        raise KeyError(book_id)
    return _active_book.set(book_id)


def reset_active_book(token: Token[str]) -> None:
    _active_book.reset(token)
