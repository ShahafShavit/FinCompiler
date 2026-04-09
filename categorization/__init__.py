"""Pluggable manual categorization UIs (terminal, browser, …)."""

from categorization.interactive.factory import create_interaction_handler
from categorization.interactive.protocol import CategorizationInteractionHandler
from categorization.interactive.prompts import (
    FluidStorePrompt,
    NewStorePrompt,
    ResolveStaticPrompt,
)

__all__ = [
    "CategorizationInteractionHandler",
    "FluidStorePrompt",
    "NewStorePrompt",
    "ResolveStaticPrompt",
    "create_interaction_handler",
]
