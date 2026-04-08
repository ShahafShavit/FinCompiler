"""Pluggable manual categorization UIs (terminal, browser, …)."""

from interactive_categorization.factory import create_interaction_handler
from interactive_categorization.protocol import CategorizationInteractionHandler
from interactive_categorization.prompts import FluidStorePrompt, NewStorePrompt, ResolveStaticPrompt

__all__ = [
    "CategorizationInteractionHandler",
    "FluidStorePrompt",
    "NewStorePrompt",
    "ResolveStaticPrompt",
    "create_interaction_handler",
]
