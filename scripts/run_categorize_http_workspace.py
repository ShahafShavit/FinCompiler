#!/usr/bin/env python3
"""
Run the real categorization stack with HTTP UI using your data tree (see FINANCE_WORKSPACE_ROOT).

Calls ``pipeline.run_categorization_interactive()`` (same as ``run_pipeline.py ... --categorize``).

**Workspace:** loads ``.env`` from the repo root, then uses ``FINANCE_WORKSPACE_ROOT`` if set
(see ``config.py``). If it is still unset, defaults to the ``testing/`` directory next to this
repo — the same tree as in ``.env`` with ``FINANCE_WORKSPACE_ROOT=testing/`` when you run from
the repo root.

Uses ``data/ledger.sqlite`` (and store mappings in the same DB) under that workspace root.

Environment (optional):
  FINANCE_CATEGORIZE_HTTP_PORT   default 9777
  FINANCE_CATEGORIZE_HTTP_HOST   default 127.0.0.1

Usage (from repo root so ``testing/`` and ``.env`` paths resolve as usual):
  set FINANCE_CATEGORIZE_HTTP_OPEN_BROWSER=0
  python scripts/run_categorize_http_workspace.py
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo not in sys.path:
        sys.path.insert(0, _repo)

    from dotenv import load_dotenv

    load_dotenv(os.path.join(_repo, ".env"))

    if not os.environ.get("FINANCE_WORKSPACE_ROOT", "").strip():
        os.environ["FINANCE_WORKSPACE_ROOT"] = os.path.abspath(os.path.join(_repo, "testing"))

    os.environ.setdefault("FINANCE_CATEGORIZE_UI", "http")
    os.environ.setdefault("FINANCE_CATEGORIZE_HTTP_HOST", "127.0.0.1")
    os.environ.setdefault("FINANCE_CATEGORIZE_HTTP_PORT", "9777")
    os.environ.setdefault("FINANCE_CATEGORIZE_HTTP_OPEN_BROWSER", "0")

    import config

    print(f"FINANCE_WORKSPACE_ROOT={config.workspace_root() or '(cwd-relative default)'}", flush=True)
    print(f"ledger_db_file={config.ledger_db_file}", flush=True)

    from pipeline.ledger import migrate_ledger_db

    migrate_ledger_db()

    import pipeline

    pipeline.run_categorization_interactive()
    return 0


if __name__ == "__main__":
    sys.exit(main())
