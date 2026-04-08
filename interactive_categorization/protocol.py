"""Protocol for pluggable categorization UIs (terminal, HTTP, future Discord, etc.)."""

from __future__ import annotations

from typing import Protocol

from interactive_categorization.prompts import FluidStorePrompt, NewStorePrompt, ResolveStaticPrompt


class CategorizationInteractionHandler(Protocol):
    def prompt_fluid_store(self, prompt: FluidStorePrompt) -> str:
        """Return chosen category string (existing dynamic or new)."""

    def prompt_resolve_static(self, prompt: ResolveStaticPrompt) -> int:
        """Return 0 (fluid) or 1 (static)."""

    def prompt_new_store(self, prompt: NewStorePrompt) -> tuple[str, int]:
        """Return (category, is_static) where is_static is 0 or 1."""
