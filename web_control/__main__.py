"""Run the web control dashboard from the repo root: ``python -m web_control``."""

from __future__ import annotations

import logging
import os
import sys

# Repo root on sys.path when running from source without an editable install
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from logger import configure_pipeline_logging  # noqa: E402
from web_control.server import serve_forever  # noqa: E402

log = logging.getLogger(__name__)


def main() -> int:
    configure_pipeline_logging(logging.INFO)
    log.info("FINANCE_WORKSPACE_ROOT: %s", config.workspace_root() or "(unset; cwd layout)")
    serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
