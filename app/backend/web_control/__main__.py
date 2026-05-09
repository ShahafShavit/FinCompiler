"""Run the web control dashboard.

From the **repository root**, put ``app/backend`` on ``PYTHONPATH`` so Python resolves the
``web_control`` package, then::

    PYTHONPATH=app/backend python -m web_control   # POSIX

PowerShell: ``$env:PYTHONPATH='app/backend'; python -m web_control``

This module also inserts ``app/backend`` onto ``sys.path`` so imports like ``config`` resolve
once the package has been located.
"""

from __future__ import annotations

import logging
import os
import sys

# ``.../app/backend`` — sibling packages live alongside ``web_control`` here.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

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
