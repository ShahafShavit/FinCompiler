"""JSON handlers for ``/api/transaction-drop-rules`` (GET / PUT)."""

from __future__ import annotations

import json
import logging
from typing import Any

from pipeline.transaction_drop_rules import load_document_from_disk, save_document_atomic, validate_document

log = logging.getLogger(__name__)


def get_config() -> dict[str, Any]:
    return load_document_from_disk()


def put_config(raw_body: bytes) -> tuple[int, dict[str, Any]]:
    try:
        body = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Invalid JSON body"}
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "validation_error", "message": "Body must be a JSON object"}
    try:
        normalized = validate_document(body)
    except ValueError as e:
        return 400, {"ok": False, "error": "validation_error", "message": str(e)}
    try:
        save_document_atomic(normalized)
    except OSError as e:
        log.exception("transaction drop rules save failed")
        return 500, {"ok": False, "error": "server_error", "message": str(e)}
    return 200, {"ok": True, "config": normalized}
