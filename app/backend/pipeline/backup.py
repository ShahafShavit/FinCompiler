"""Timestamped local snapshots of critical ledger, static, and web data (migration MIG-B1, MIG-B3)."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Any

import config

log = logging.getLogger(__name__)

MANIFEST_FILENAME = "snapshot_manifest.json"
_SCHEMA_VERSION = 1


def _dir_size(root: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _copy_tree_or_file(src: str, dst: str) -> None:
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    elif os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def create_critical_paths_backup(
    *,
    parent_dir: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Copy minimum critical paths into ``parent_dir / <local_timestamp> /``.

    Includes (when present): ``data/export/compiled/``, ``data/static/``,
    ``web/data/`` (heatmap totals dir). Does not copy ``.env`` or ad-hoc secrets.

    Returns ``(backup_root_dir, manifest_dict)`` and writes ``snapshot_manifest.json``
    inside the backup root.
    """
    base_parent = parent_dir if parent_dir is not None else config.backup_parent_dir
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    root = os.path.join(base_parent, stamp)
    os.makedirs(root, exist_ok=True)

    # Destination layout mirrors workspace-relative paths for easy restore inspection.
    specs: list[tuple[str, str, str]] = [
        (config.compiled_dir.rstrip(os.sep), os.path.join(root, "data", "export", "compiled"), "data/export/compiled"),
        (config.static_dir.rstrip(os.sep), os.path.join(root, "data", "static"), "data/static"),
        (
            os.path.join(config.web_dir.rstrip(os.sep), "data"),
            os.path.join(root, "web", "data"),
            "web/data",
        ),
    ]

    included: list[str] = []
    for src, dst, logical in specs:
        if not os.path.exists(src):
            log.info("BACKUP: skip missing path %s", src)
            continue
        _copy_tree_or_file(src, dst)
        included.append(logical)

    ld = config.ledger_db_file
    if os.path.isfile(ld):
        dst_ledger = os.path.join(root, "data", os.path.basename(ld))
        _copy_tree_or_file(ld, dst_ledger)
        included.append("data/ledger.sqlite")

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_path = os.path.join(root, MANIFEST_FILENAME)
    manifest: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "created_at_utc": created_at,
        "tool": "FinCompiler-pipeline-backup",
        "included_top_level": included,
        "total_bytes": 0,
        "backup_root": os.path.abspath(root),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    manifest["total_bytes"] = _dir_size(root)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    log.info("BACKUP: wrote %s (%s)", manifest_path, ", ".join(included) or "(empty)")
    return root, manifest


def load_manifest(manifest_path: str) -> dict[str, Any]:
    """Load and minimally validate a snapshot manifest (for tests and tooling)."""
    with open(manifest_path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    if "schema_version" not in data or "included_top_level" not in data:
        raise ValueError("manifest missing required keys: schema_version, included_top_level")
    if data["schema_version"] != _SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {data['schema_version']!r}")
    return data
