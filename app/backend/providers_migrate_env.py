"""
One-shot migration: copy legacy portal / Google vars from ``.env`` into ``data/private/providers.json``.

Usage (from repo root, with PYTHONPATH including app/backend)::

    python -m providers_migrate_env

Then remove secret lines from your ``.env`` (see README).
"""

from __future__ import annotations

import sys


def main() -> int:
    from providers_store import import_legacy_env_from_dotenv, providers_file_path

    import_legacy_env_from_dotenv(save=True)
    print(f"Wrote merged providers to {providers_file_path()!r}")
    print("Remove bank_*, credit_*, max_*, GOOGLE_API_USER, GOOGLE_WORKSHEET_ID from .env when done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
