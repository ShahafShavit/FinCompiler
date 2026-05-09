#!/usr/bin/env python3
"""Pipeline CLI shim. Same entrypoint as ``run_pipeline.py`` — see ``apps.pipeline_cli``."""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent / "app" / "backend"
sys.path.insert(0, str(_BACKEND))

from apps.pipeline_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
