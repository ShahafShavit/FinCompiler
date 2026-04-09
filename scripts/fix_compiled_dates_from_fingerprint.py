"""
Rewrite תאריך in compiled.csv from the leading YYYY-MM-DD segment of each fingerprint.

Use when תאריך drifted (e.g. month/day confusion) but fingerprint still encodes the correct day
(fingerprint format: ``YYYY-MM-DD:amount:store:extra`` — see
``pipeline.csv_handler.generate_transaction_fingerprint``).

Examples:
  python scripts/fix_compiled_dates_from_fingerprint.py --dry-run
  python scripts/fix_compiled_dates_from_fingerprint.py
  python scripts/fix_compiled_dates_from_fingerprint.py --csv path/to/compiled.csv
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

import pandas as pd

import config
from pipeline.compiler import parse_post_ingest_date_scalar

_FP_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2}):")


def _date_from_fingerprint(fp: object) -> str | None:
    if fp is None or (isinstance(fp, float) and pd.isna(fp)):
        return None
    s = str(fp).strip()
    if not s:
        return None
    m = _FP_DATE_PREFIX.match(s)
    if not m:
        return None
    ymd = m.group(1)
    try:
        datetime.strptime(ymd, "%Y-%m-%d")
    except ValueError:
        return None
    return ymd


def _normalize_existing_date(val: object) -> str | None:
    """Calendar day as YYYY-MM-DD for comparison (same rules as compile post-ingest parse)."""
    ts = parse_post_ingest_date_scalar(val)
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--csv",
        type=Path,
        default=Path(config.compiled_file),
        help=f"compiled ledger (default: {config.compiled_file})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print changes only; do not write or backup",
    )
    p.add_argument(
        "--no-backup",
        action="store_true",
        help="skip copying the input to .bak before overwrite (not recommended)",
    )
    args = p.parse_args()
    path: Path = args.csv.expanduser().resolve()

    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    df = pd.read_csv(path)
    df.columns = df.columns.astype(str).str.replace("\ufeff", "", regex=False).str.strip()

    if "fingerprint" not in df.columns:
        print("No fingerprint column — nothing to do.", file=sys.stderr)
        return 1
    if "תאריך" not in df.columns:
        print("No תאריך column — nothing to do.", file=sys.stderr)
        return 1

    loc_date = df.columns.get_loc("תאריך")
    if not isinstance(loc_date, int):
        print(
            "Multiple columns named תאריך — fix headers in the CSV, then re-run.",
            file=sys.stderr,
        )
        return 1

    df = df.reset_index(drop=True)

    fixes: list[tuple[int, str, str, str]] = []
    skipped_fp = 0
    skipped_no_change = 0

    for pos in range(len(df)):
        fp = df.iloc[pos]["fingerprint"]
        want = _date_from_fingerprint(fp)
        if want is None:
            skipped_fp += 1
            continue
        cur_raw = df.iat[pos, loc_date]
        cur_iso = _normalize_existing_date(cur_raw)
        if cur_iso == want:
            skipped_no_change += 1
            continue
        old_repr = repr(cur_raw)
        fixes.append(
            (
                pos,
                str(fp)[:48] + ("…" if len(str(fp)) > 48 else ""),
                old_repr,
                want,
            )
        )

    print(f"{path}: {len(df)} rows")
    print(f"  already match fingerprint date: {skipped_no_change}")
    print(f"  skipped (missing / non-standard fingerprint): {skipped_fp}")
    print(f"  rows to update: {len(fixes)}")

    for pos, fp_snip, old_repr, new_iso in fixes[:50]:
        print(f"  row {pos}: תאריך {old_repr} → {new_iso}  (fp {fp_snip})")
    if len(fixes) > 50:
        print(f"  … and {len(fixes) - 50} more")

    if args.dry_run:
        print("--dry-run: no file written.")
        return 0

    if not fixes:
        print("Nothing to write.")
        return 0

    if not args.no_backup:
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        print(f"Backup: {bak}")

    for pos, _fp_snip, _old_repr, new_iso in fixes:
        df.iat[pos, loc_date] = new_iso

    df.to_csv(path, index=False)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
