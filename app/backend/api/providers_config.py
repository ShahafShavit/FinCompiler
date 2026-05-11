"""JSON handlers for ``/api/providers-config`` (GET redacted, PUT merge, import-env)."""

from __future__ import annotations

import json
import logging
from typing import Any

import providers

log = logging.getLogger(__name__)


def get_config() -> dict[str, Any]:
    doc = providers.load_document()
    return providers.document_for_api_get(doc)


def put_config(raw_body: bytes) -> tuple[int, dict[str, Any]]:
    try:
        body = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Invalid JSON body"}
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "validation_error", "message": "Body must be a JSON object"}

    bank = body.get("bank")
    if isinstance(bank, dict) and bank.get("provider") is not None:
        pid = str(bank.get("provider") or "").strip().lower()
        try:
            providers.bank_class(pid)
        except ValueError as e:
            return 400, {"ok": False, "error": "validation_error", "message": str(e)}

    current = providers.load_document()
    try:
        merged = providers.merge_put_update(current, body)
    except Exception:  # noqa: BLE001
        log.exception("providers merge_put_update failed")
        return 500, {"ok": False, "error": "server_error", "message": "Failed to merge providers (see server log)."}

    try:
        providers.save_document_atomic(merged)
    except OSError as e:
        log.exception("providers save failed")
        return 500, {"ok": False, "error": "server_error", "message": str(e)}

    return 200, {"ok": True, "config": providers.document_for_api_get(merged)}


def import_dotenv() -> tuple[int, dict[str, Any]]:
    try:
        merged = providers.import_legacy_env_from_dotenv(save=True)
    except OSError as e:
        return 500, {"ok": False, "error": "server_error", "message": str(e)}
    except Exception:  # noqa: BLE001
        log.exception("import_legacy_env_from_dotenv failed")
        return 500, {"ok": False, "error": "server_error", "message": "Import failed (see server log)."}
    return 200, {
        "ok": True,
        "message": "Merged .env into providers.json. Remove secret keys from .env when verified.",
        "config": providers.document_for_api_get(merged),
    }
