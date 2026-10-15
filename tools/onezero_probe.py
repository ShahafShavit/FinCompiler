"""CLI entry for the One Zero client. The client lives in ``integrations.onezero``."""

from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import integrations.onezero as _oz

globals().update({k: v for k, v in vars(_oz).items() if not k.startswith("__")})


if __name__ == "__main__":
    raise SystemExit(main())
