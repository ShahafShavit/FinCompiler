"""HTTP API for installments + recurring fees page."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import parse_qs

import config
from ledger import connect_ledger
from ledger.installment_logic import (
    build_plans_payload,
    plan_section,
    split_override_base_id,
    upsert_override,
)

log = logging.getLogger(__name__)


def _ledger_path() -> str:
    return config.ledger_db_file


def list_installment_plans(qs: dict[str, list[str]] | None = None) -> tuple[int, dict[str, Any]]:
    path = _ledger_path()
    if not os.path.isfile(path):
        return 200, {
            "ok": True,
            "as_of": None,
            "installments": {
                "summary": {
                    "active": 0,
                    "due_this_month": 0.0,
                    "remaining_total": 0.0,
                    "nearest_last_payment": None,
                },
                "plans": [],
            },
            "recurring": {
                "summary": {"active": 0, "monthly_burn": 0.0, "lapsed": 0},
                "fees": [],
            },
            "ledger_exists": False,
        }

    qs = qs or {}
    include_hidden = (qs.get("include_hidden") or ["0"])[0] in ("1", "true", "yes")
    q = (qs.get("q") or [""])[0].strip() or None
    as_of = (qs.get("as_of") or [""])[0].strip() or None
    summary_only = (qs.get("summary") or ["0"])[0] in ("1", "true", "yes")

    conn = connect_ledger(path)
    try:
        payload = build_plans_payload(
            conn, include_hidden=include_hidden, q=q, as_of=as_of
        )
        payload["ledger_exists"] = True
        if summary_only:
            payload["installments"] = {
                "summary": payload.get("installments", {}).get("summary") or {},
                "plans": [],
            }
            payload["recurring"] = {
                "summary": payload.get("recurring", {}).get("summary") or {},
                "fees": [],
            }
        return 200, payload
    finally:
        conn.close()


def handle_list_request(query: str) -> tuple[int, dict[str, Any]]:
    qs = parse_qs(query, keep_blank_values=False)
    return list_installment_plans(qs)


def patch_installment_plan(plan_id: str, raw: bytes) -> tuple[int, dict[str, Any]]:
    plan_id = (plan_id or "").strip()
    if not plan_id:
        return 400, {"ok": False, "error": "missing_plan_id", "message": "plan_id required"}

    try:
        body = json.loads(raw.decode("utf-8") if raw else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "invalid_json", "message": "Body must be JSON"}

    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid_body", "message": "Body must be an object"}

    allowed = {"hidden", "merged_into", "split_by", "settled"}
    unknown = set(body.keys()) - allowed
    if unknown:
        return 400, {
            "ok": False,
            "error": "unknown_fields",
            "message": f"Unknown fields: {sorted(unknown)}",
        }

    if not any(k in body for k in allowed):
        return 400, {
            "ok": False,
            "error": "empty_patch",
            "message": "Provide hidden, merged_into, split_by, and/or settled",
        }

    section = plan_section(plan_id)
    if section is None:
        return 400, {
            "ok": False,
            "error": "bad_plan_id",
            "message": "plan_id must start with inst: or rec:",
        }

    hidden = body.get("hidden") if "hidden" in body else None
    if hidden is not None and not isinstance(hidden, bool) and hidden not in (0, 1):
        return 400, {"ok": False, "error": "bad_hidden", "message": "hidden must be boolean"}

    settled = body.get("settled") if "settled" in body else None
    if settled is not None:
        if section != "installment":
            return 400, {
                "ok": False,
                "error": "settled_installments_only",
                "message": "settled applies only to installment plans",
            }
        if not isinstance(settled, bool) and settled not in (0, 1):
            return 400, {"ok": False, "error": "bad_settled", "message": "settled must be boolean"}

    merged_into: Any = ...
    if "merged_into" in body:
        mi = body["merged_into"]
        if mi is None or mi == "":
            merged_into = None
        elif not isinstance(mi, str):
            return 400, {"ok": False, "error": "bad_merged_into", "message": "merged_into must be string or null"}
        else:
            mi = mi.strip()
            if mi == plan_id:
                return 400, {
                    "ok": False,
                    "error": "self_merge",
                    "message": "Cannot merge a plan into itself",
                }
            other = plan_section(mi)
            if other is None or other != section:
                return 400, {
                    "ok": False,
                    "error": "cross_section_merge",
                    "message": "Merge only within the same section (installment or recurring)",
                }
            merged_into = mi

    split_by: Any = ...
    if "split_by" in body:
        sb = body["split_by"]
        if sb is None or sb == "":
            split_by = None
        elif sb not in ("last4", "amount"):
            return 400, {
                "ok": False,
                "error": "bad_split_by",
                "message": "split_by must be last4, amount, or null",
            }
        else:
            split_by = sb

    path = _ledger_path()
    if not os.path.isfile(path):
        return 404, {"ok": False, "error": "no_ledger", "message": "Ledger database not found"}

    conn = connect_ledger(path)
    try:
        if hidden is not None or merged_into is not ... or settled is not None:
            upsert_override(
                conn,
                plan_id,
                hidden=(1 if hidden else 0) if hidden is not None else None,
                merged_into=merged_into,
                settled=(1 if settled else 0) if settled is not None else None,
            )
        if split_by is not ...:
            upsert_override(
                conn,
                split_override_base_id(plan_id),
                split_by=split_by,
            )
        conn.commit()
        payload = build_plans_payload(conn, include_hidden=True)
        payload["ok"] = True
        payload["patched"] = plan_id
        return 200, payload
    except Exception:  # noqa: BLE001
        log.exception("patch_installment_plan failed for %s", plan_id)
        conn.rollback()
        return 500, {
            "ok": False,
            "error": "server_error",
            "message": "Failed to patch plan override",
        }
    finally:
        conn.close()
