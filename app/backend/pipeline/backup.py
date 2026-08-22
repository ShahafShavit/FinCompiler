"""Timestamped local snapshots of critical ledger, static, and web data (migration MIG-B1, MIG-B3).

MIG-E4: manifests may include a ``ledger_fingerprint`` (size + mtime + sha256 + row count)
for local divergence checks vs the live ledger.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
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


def ledger_file_fingerprint(db_path: str | None = None) -> dict[str, Any] | None:
    """Cheap identity for the live ledger file (MIG-E4 local divergence)."""
    path = db_path if db_path is not None else config.ledger_db_file
    if not path or not os.path.isfile(path):
        return None
    st = os.stat(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    row_count: int | None = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT COUNT(*) FROM ledger_transaction").fetchone()
            row_count = int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        row_count = None
    return {
        "path": os.path.abspath(path),
        "size_bytes": int(st.st_size),
        "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
        "sha256": h.hexdigest(),
        "row_count": row_count,
    }


def create_critical_paths_backup(
    *,
    parent_dir: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Copy minimum critical paths into ``parent_dir / <local_timestamp> /``.

    Includes (when present): ``data/export/compiled/`` (historical export folder), ``data/static/``,
    ``data/private/`` (UI-managed providers + Google path config), ``web/data/``
    (heatmap totals dir). Does not copy ``.env``.

    Returns ``(backup_root_dir, manifest_dict)`` and writes ``snapshot_manifest.json``
    inside the backup root.
    """
    base_parent = parent_dir if parent_dir is not None else config.backup_parent_dir
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    root = os.path.join(base_parent, stamp)
    os.makedirs(root, exist_ok=True)

    specs: list[tuple[str, str, str]] = [
        (config.compiled_dir.rstrip(os.sep), os.path.join(root, "data", "export", "compiled"), "data/export/compiled"),
        (config.static_dir.rstrip(os.sep), os.path.join(root, "data", "static"), "data/static"),
        (
            config.private_dir,
            os.path.join(root, "data", "private"),
            "data/private",
        ),
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
    ledger_fp = ledger_file_fingerprint(ld) if os.path.isfile(ld) else None
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
    if ledger_fp is not None:
        manifest["ledger_fingerprint"] = ledger_fp
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


def find_latest_backup_manifest(parent_dir: str | None = None) -> str | None:
    """Newest ``snapshot_manifest.json`` under the backup parent (by directory name)."""
    base = parent_dir if parent_dir is not None else config.backup_parent_dir
    if not os.path.isdir(base):
        return None
    candidates: list[str] = []
    for name in os.listdir(base):
        mp = os.path.join(base, name, MANIFEST_FILENAME)
        if os.path.isfile(mp):
            candidates.append(mp)
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0]


def compare_ledger_to_manifest(
    manifest: dict[str, Any],
    *,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Compare live ledger fingerprint to ``manifest['ledger_fingerprint']`` (MIG-E4)."""
    current = ledger_file_fingerprint(db_path)
    recorded = manifest.get("ledger_fingerprint")
    if current is None:
        return {
            "ok": False,
            "match": False,
            "error": "no_ledger",
            "message": "Live ledger file missing",
            "current": None,
            "recorded": recorded,
        }
    if not isinstance(recorded, dict) or "sha256" not in recorded:
        return {
            "ok": True,
            "match": False,
            "error": "no_recorded_fingerprint",
            "message": "Manifest has no ledger_fingerprint (older backup)",
            "current": current,
            "recorded": recorded,
        }
    match = (
        current.get("sha256") == recorded.get("sha256")
        and current.get("size_bytes") == recorded.get("size_bytes")
        and current.get("row_count") == recorded.get("row_count")
    )
    return {
        "ok": True,
        "match": bool(match),
        "current": current,
        "recorded": recorded,
        "backup_root": manifest.get("backup_root"),
        "created_at_utc": manifest.get("created_at_utc"),
    }


def divergence_report(*, parent_dir: str | None = None) -> dict[str, Any]:
    """Compare live ledger to the latest local backup manifest."""
    mp = find_latest_backup_manifest(parent_dir)
    if mp is None:
        return {
            "ok": True,
            "match": False,
            "error": "no_backup",
            "message": "No local backup manifest found",
            "manifest_path": None,
        }
    try:
        man = load_manifest(mp)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return {
            "ok": False,
            "match": False,
            "error": "bad_manifest",
            "message": str(e),
            "manifest_path": mp,
        }
    out = compare_ledger_to_manifest(man)
    out["manifest_path"] = mp
    return out
