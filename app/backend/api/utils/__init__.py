"""Control-server utilities (JSON safety, HTTP helpers, SPA, process state)."""

from __future__ import annotations

from .helpers import (
    ControlState,
    EventHub,
    StateDep,
    SPA_INDEX_MISSING_BYTES,
    assert_control_port_available,
    content_type_for,
    fail_port_in_use,
    address_already_in_use,
    get_control_state,
    normalize_http_path,
    safe_subpath,
    spa_assets_dir,
    spa_dist_dir,
    spa_index_bytes,
    SPA_ROUTES,
)
from .json_safe import json_bytes_strict, sanitize_for_json

__all__ = [
    "ControlState",
    "EventHub",
    "StateDep",
    "SPA_INDEX_MISSING_BYTES",
    "SPA_ROUTES",
    "address_already_in_use",
    "assert_control_port_available",
    "content_type_for",
    "fail_port_in_use",
    "get_control_state",
    "json_bytes_strict",
    "normalize_http_path",
    "safe_subpath",
    "sanitize_for_json",
    "spa_assets_dir",
    "spa_dist_dir",
    "spa_index_bytes",
]
