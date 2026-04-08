"""Run from the project root with the project venv, e.g. ``venv\\Scripts\\python.exe -m web_control`` (Windows) or ``venv/bin/python -m web_control`` (Unix), after ``pip install -r requirements.txt``."""

from __future__ import annotations

import logging
import os
import sys

# Ensure project root is importable when run as ``python -m web_control``
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from logger import configure_pipeline_logging  # noqa: E402
from web_control.server import serve_forever  # noqa: E402

log = logging.getLogger(__name__)


def main() -> int:
    configure_pipeline_logging(logging.INFO)
    log.info("Workspace root: %s", config.workspace_root() or "(default cwd layout)")
    serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
