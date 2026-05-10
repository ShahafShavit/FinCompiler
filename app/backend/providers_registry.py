"""Map provider ids from ``providers.json`` to Selenium portal classes."""

from __future__ import annotations

from typing import Type

from pipeline.portal_fetch import Bank, IsracardCredit, MaxCredit

BankFactory = Type[Bank]

BANK_PROVIDERS: dict[str, BankFactory] = {
    "leumi": Bank,
}

CREDIT_PROVIDERS: dict[str, type[MaxCredit] | type[IsracardCredit]] = {
    "max": MaxCredit,
    "isracard": IsracardCredit,
}


def bank_class(provider_id: str) -> BankFactory:
    pid = (provider_id or "").strip().lower()
    cls = BANK_PROVIDERS.get(pid)
    if cls is None:
        raise ValueError(f"Unsupported bank provider {provider_id!r}; supported: {sorted(BANK_PROVIDERS)}")
    return cls


def assert_credit_provider(provider_id: str) -> None:
    pid = (provider_id or "").strip().lower()
    if pid not in CREDIT_PROVIDERS:
        raise ValueError(
            f"Unsupported credit provider {provider_id!r}; supported: {sorted(CREDIT_PROVIDERS)}"
        )
