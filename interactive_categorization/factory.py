"""Construct the active categorization UI (terminal vs local HTTP)."""

from __future__ import annotations

import config
from interactive_categorization.http_server import HttpCategorizationHandler
from interactive_categorization.protocol import CategorizationInteractionHandler
from interactive_categorization.terminal import TerminalCategorizationHandler


def create_interaction_handler(
    mode: str | None = None,
    *,
    http_host: str | None = None,
    http_port: int | None = None,
    http_open_browser: bool | None = None,
) -> CategorizationInteractionHandler:
    m = (mode or getattr(config, "categorize_ui_mode", "terminal")).strip().lower()
    if m == "http":
        host = (
            http_host
            if http_host is not None
            else getattr(config, "categorize_http_host", "127.0.0.1")
        )
        port = (
            int(http_port)
            if http_port is not None
            else int(getattr(config, "categorize_http_port", 0))
        )
        open_br = (
            bool(http_open_browser)
            if http_open_browser is not None
            else bool(getattr(config, "categorize_http_open_browser", True))
        )
        return HttpCategorizationHandler(host=host, port=port, open_browser=open_br)
    return TerminalCategorizationHandler()
