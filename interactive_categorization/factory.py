"""Construct the active categorization UI (terminal vs local HTTP)."""

from __future__ import annotations

import config
from interactive_categorization.http_server import HttpCategorizationHandler
from interactive_categorization.protocol import CategorizationInteractionHandler
from interactive_categorization.terminal import TerminalCategorizationHandler


def create_interaction_handler(
    mode: str | None = None,
) -> CategorizationInteractionHandler:
    m = (mode or getattr(config, "categorize_ui_mode", "terminal")).strip().lower()
    if m == "http":
        return HttpCategorizationHandler(
            host=getattr(config, "categorize_http_host", "127.0.0.1"),
            port=int(getattr(config, "categorize_http_port", 0)),
            open_browser=bool(getattr(config, "categorize_http_open_browser", True)),
        )
    return TerminalCategorizationHandler()
