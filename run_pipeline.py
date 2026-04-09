#!/usr/bin/env python3
"""Pipeline CLI shim. Implements :mod:`apps.pipeline_cli`."""

import sys

from apps.pipeline_cli import main

if __name__ == "__main__":
    sys.exit(main())
