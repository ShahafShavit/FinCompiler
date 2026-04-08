#!/usr/bin/env python3
"""
Run finance pipelines without the Qt UI.

For full documentation run:

  python run_pipeline.py --help
  python run_pipeline.py <command> --help
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap

import config
import pipeline
from logger import configure_pipeline_logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Help text (shown with --help; keep lines reasonably short for 80-col terminals)
# ---------------------------------------------------------------------------

MAIN_DESCRIPTION = """\
Run the holdings (balances) and/or transactions pipelines without opening the GUI.

Data flow (short version):
  - Chrome / Selenium still download into the shared folder: data/input/
  - route sorts each spreadsheet into an isolated workspace under data/workspace/
  - holdings pipeline only touches .../workspace/holdings/{inbox,raw,clean}
  - transactions pipeline only touches .../workspace/transactions/{inbox,raw,clean}
  - Final CSVs are always: export/compiled/holdings.csv and export/compiled/compiled.csv
  - Optional: set FINANCE_WORKSPACE_ROOT to a directory to use a separate data/export/web
    tree (safe for tests or experiments; see config.py).

Pick a COMMAND below. Every command has its own options - use:
  python run_pipeline.py COMMAND --help
"""

MAIN_EPILOG = """\
commands:
  route          Classify files in data/input/*.xls* and MOVE them into workspace inboxes.
  holdings       Balances: ingest -> clean CSV -> merge into holdings.csv
  transactions   Spending/income lines: ingest -> clean CSV -> merge into compiled.csv
  all            Optional browser downloads, then route, then BOTH pipelines in one go.
  both-process   No browser: route whatever is already in data/input, then BOTH pipelines.

typical workflows:
  # Full run: all portal downloads, route, both pipelines (needs .env credentials)
  python run_pipeline.py all

  # Same pipelines without browsers (files already in data/input/)
  python run_pipeline.py all --no-fetch
  python run_pipeline.py both-process

  # Or explicitly: sort downloads, then run each side
  python run_pipeline.py route
  python run_pipeline.py holdings --no-route
  python run_pipeline.py transactions --no-route

classification rule for route:
  - Filename contains the bank balances marker (see inbox_router.HOLDINGS_MARKERS) -> holdings inbox
  - Any other .xls / .xlsx / .xlsm -> transactions inbox
  - Anything else -> data/input/unclassified/
"""


ROUTE_DESCRIPTION = """\
Scan the shared download folder (data/input/) for spreadsheet exports.

Each matching file is moved (not copied) into exactly one workspace inbox so the two
pipelines never read each other's downloads. Safe to run after every browser session.
"""

ROUTE_EPILOG = """\
examples:
  python run_pipeline.py route
  python run_pipeline.py route --dry-run    # show what would move, change nothing
"""


HOLDINGS_DESCRIPTION = """\
Balances pipeline: workbooks that end up in data/workspace/holdings/.

Steps (each can be skipped with --no-*):
  1. route   - move *.xls* from data/input into holdings vs transactions inboxes
  2. ingest  - normalize to .xlsx under holdings/raw
  3. csv     - build cleaned CSV under holdings/clean
  4. compile - merge into export/compiled/holdings.csv
"""

HOLDINGS_EPILOG = """\
examples:
  python run_pipeline.py holdings                    # route + full pipeline
  python run_pipeline.py holdings --fetch            # download from bank first, then full pipeline
  python run_pipeline.py holdings --no-route         # files already in holdings/inbox
  python run_pipeline.py holdings --no-compile       # stop after CSV step

note:
  --fetch opens a browser (Selenium). Requires bank credentials in .env.
"""


TRANSACTIONS_DESCRIPTION = """\
Transactions pipeline: card and account lines in data/workspace/transactions/.

Steps (each can be skipped with --no-*):
  1. route   - move *.xls* from data/input into the right workspace inbox
  2. ingest  - normalize to .xlsx under transactions/raw
  3. csv     - cleaned CSV under transactions/clean (column filtering depends on --drop-profile)
  4. compile - merge into export/compiled/compiled.csv (+ fingerprint DB)
  5. optional: --auto-categorize runs the automatic category pass (same as part of the old batch flow)
"""

TRANSACTIONS_EPILOG = """\
notes:
  - Bank credit + bank osh can run in one Leumi session (enable both flags).
  - --from-date / --to-date only affect --fetch-bank-osh (same strings as in the bank UI).
  - drop-profile: full = GUI-style filters; batch = smaller legacy drop set.
  - --categorize: interactive prompts after compile; set FINANCE_CATEGORIZE_UI=http for browser.

examples:
  python run_pipeline.py transactions
  python run_pipeline.py transactions --fetch-bank-credit --fetch-bank-osh
  python run_pipeline.py transactions --fetch-max-isracard
  python run_pipeline.py transactions --no-route --auto-categorize
  python run_pipeline.py transactions --categorize
"""


ALL_DESCRIPTION = """\
Full portal downloads (unless --no-fetch), then one shared route, then BOTH pipelines.

Order:
  1. By default: download holdings, Max/Isracard exports, and Leumi credit + osh (one session
     for the bank flags). Use --no-fetch to skip all browsers (same as placing files in
     data/input/ yourself).
  2. route - split everything in data/input into holdings vs transactions inboxes.
  3. Full holdings pipeline (no second route).
  4. Full transactions pipeline (no second route).
  5. Optional: --categorize for interactive prompts (set FINANCE_CATEGORIZE_UI=http for browser).
"""

ALL_EPILOG = """\
  python run_pipeline.py all
  python run_pipeline.py all --no-fetch
  python run_pipeline.py all --categorize
  python run_pipeline.py all --no-fetch --categorize

--from-date / --to-date apply to the bank osh download inside the Leumi session.

--auto-categorize runs only the automatic category pass (no prompts). --categorize runs
auto + manual prompts (HTTP or terminal); do not combine with --auto-categorize.
"""


BOTH_PROCESS_DESCRIPTION = """\
Assume files are already in data/input/ (you downloaded manually). No browser.

  1. route - move spreadsheets into workspace inboxes.
  2. Full holdings pipeline (ingest -> csv -> compile).
  3. Full transactions pipeline (ingest -> csv -> compile).

Does not delete files in data/input/ except by moving them during route (holdings and
transactions inboxes receive the files; shared inbox ends up empty of *.xls*).
"""

BOTH_PROCESS_EPILOG = """\
examples:
  python run_pipeline.py both-process
  python run_pipeline.py both-process --auto-categorize
  python run_pipeline.py both-process --categorize
  python run_pipeline.py both-process --drop-profile batch
"""


def _build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description=textwrap.dedent(MAIN_DESCRIPTION),
        epilog=textwrap.dedent(MAIN_EPILOG),
        formatter_class=fmt,
    )
    log_grp = p.add_mutually_exclusive_group()
    log_grp.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Minimal logging (WARNING): only warnings and errors.",
    )
    log_grp.add_argument(
        "--debug",
        action="store_true",
        help="Verbose logging (DEBUG): full pipeline diagnostics.",
    )

    sub = p.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="Pipeline to run. Use: python run_pipeline.py COMMAND --help",
    )

    # --- route ---
    r = sub.add_parser(
        "route",
        description=textwrap.dedent(ROUTE_DESCRIPTION),
        epilog=textwrap.dedent(ROUTE_EPILOG),
        formatter_class=fmt,
        help="Sort downloads in data/input into holdings/transactions workspace inboxes.",
    )
    r.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which moves would happen; do not move any files.",
    )

    # --- holdings ---
    h = sub.add_parser(
        "holdings",
        description=textwrap.dedent(HOLDINGS_DESCRIPTION),
        epilog=textwrap.dedent(HOLDINGS_EPILOG),
        formatter_class=fmt,
        help="Balances pipeline -> export/compiled/holdings.csv",
    )
    hg_fetch = h.add_argument_group("portal (optional)")
    hg_fetch.add_argument(
        "--fetch",
        action="store_true",
        help="Before anything else: open browser and download the holdings export into data/input/.",
    )
    hg_steps = h.add_argument_group("pipeline steps (default: all on; use --no-* to skip)")
    hg_steps.add_argument(
        "--no-route",
        action="store_true",
        help="Skip moving files from data/input: expect workbooks already in holdings/inbox.",
    )
    hg_steps.add_argument(
        "--no-ingest",
        action="store_true",
        help="Skip xlsx normalization (no inbox -> raw copy/convert).",
    )
    hg_steps.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip building cleaned CSV files from raw xlsx.",
    )
    hg_steps.add_argument(
        "--no-compile",
        action="store_true",
        help="Skip merging cleaned CSVs into export/compiled/holdings.csv.",
    )

    # --- transactions ---
    t = sub.add_parser(
        "transactions",
        description=textwrap.dedent(TRANSACTIONS_DESCRIPTION),
        epilog=textwrap.dedent(TRANSACTIONS_EPILOG),
        formatter_class=fmt,
        help="Transactions pipeline -> export/compiled/compiled.csv",
    )
    tg_fetch = t.add_argument_group("portal fetch (optional; any combination)")
    tg_fetch.add_argument(
        "--fetch-max-isracard",
        action="store_true",
        help="Download via Max and Isracard sites (separate logins).",
    )
    tg_fetch.add_argument(
        "--fetch-bank-credit",
        action="store_true",
        help="After Leumi login, download credit-card Excel exports.",
    )
    tg_fetch.add_argument(
        "--fetch-bank-osh",
        action="store_true",
        help="After Leumi login, download account transaction export (osh).",
    )
    tg_fetch.add_argument(
        "--from-date",
        metavar="DD.MM.YY",
        default=None,
        help="For --fetch-bank-osh only: start date filter (bank-specific format).",
    )
    tg_fetch.add_argument(
        "--to-date",
        metavar="DD.MM.YY",
        default=None,
        help="For --fetch-bank-osh only: end date filter (optional).",
    )
    tg_steps = t.add_argument_group("pipeline steps (default: all on; use --no-* to skip)")
    tg_steps.add_argument(
        "--no-route",
        action="store_true",
        help="Skip moving files from data/input: expect workbooks already in transactions/inbox.",
    )
    tg_steps.add_argument(
        "--no-ingest",
        action="store_true",
        help="Skip inbox -> raw xlsx normalization.",
    )
    tg_steps.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip raw xlsx -> cleaned CSV.",
    )
    tg_steps.add_argument(
        "--no-compile",
        action="store_true",
        help="Skip merge into compiled.csv and fingerprint DB update.",
    )
    tg_out = t.add_argument_group("after compile")
    tg_out.add_argument(
        "--auto-categorize",
        action="store_true",
        help="After compile: automatic category pass only (no prompts). Ignored if --categorize.",
    )
    tg_out.add_argument(
        "--categorize",
        action="store_true",
        help="After compile: auto + interactive prompts (FINANCE_CATEGORIZE_UI=http for browser).",
    )
    tg_out.add_argument(
        "--drop-profile",
        choices=("full", "batch"),
        default="full",
        metavar="PROFILE",
        help="'full' = same column drops as the GUI transaction processor (default). "
        "'batch' = smaller drop set for legacy imports.",
    )

    # --- all ---
    a = sub.add_parser(
        "all",
        description=textwrap.dedent(ALL_DESCRIPTION),
        epilog=textwrap.dedent(ALL_EPILOG),
        formatter_class=fmt,
        help="Default: fetch all portals, route, holdings + transactions. Use --no-fetch to skip downloads.",
    )
    ag = a.add_argument_group("portal fetches")
    ag.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip all browser downloads; use files already in data/input/ (same idea as both-process).",
    )
    ag.add_argument(
        "--from-date",
        metavar="DD.MM.YY",
        default=None,
        help="Leumi osh export: start date (same format as in the bank UI).",
    )
    ag.add_argument(
        "--to-date",
        metavar="DD.MM.YY",
        default=None,
        help="Leumi osh export: end date (optional).",
    )
    ag2 = a.add_argument_group("after route + pipelines")
    ag2.add_argument(
        "--auto-categorize",
        action="store_true",
        help="After compile: automatic category pass only (no prompts). Ignored if --categorize.",
    )
    ag2.add_argument(
        "--categorize",
        action="store_true",
        help="After compile: auto categorization then interactive prompts (terminal or HTTP via .env).",
    )
    ag2.add_argument(
        "--drop-profile",
        choices=("full", "batch"),
        default="full",
        metavar="PROFILE",
        help="Column-drop preset for the transactions side (see transactions --help).",
    )

    # --- both-process ---
    b = sub.add_parser(
        "both-process",
        description=textwrap.dedent(BOTH_PROCESS_DESCRIPTION),
        epilog=textwrap.dedent(BOTH_PROCESS_EPILOG),
        formatter_class=fmt,
        help="Route data/input, then run holdings and transactions (no browser).",
    )
    b.add_argument(
        "--auto-categorize",
        action="store_true",
        help="After compile: automatic category pass only (no prompts). Ignored if --categorize.",
    )
    b.add_argument(
        "--categorize",
        action="store_true",
        help="After compile: auto + interactive prompts (FINANCE_CATEGORIZE_UI=http for browser).",
    )
    b.add_argument(
        "--drop-profile",
        choices=("full", "batch"),
        default="full",
        metavar="PROFILE",
        help="Column-drop preset for transactions (see transactions --help).",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.quiet:
        log_level = logging.WARNING
    elif args.debug:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO
    configure_pipeline_logging(log_level)

    os.makedirs(config.compiled_dir, exist_ok=True)
    pipeline.ensure_workspace_dirs()

    if args.command == "route":
        pipeline.route_inbox(dry_run=getattr(args, "dry_run", False))
        return 0

    if args.command == "holdings":
        pipeline.run_holdings_pipeline(
            fetch=args.fetch,
            route=not args.no_route,
            ingest=not args.no_ingest,
            to_csv=not args.no_csv,
            compile_=not args.no_compile,
        )
        return 0

    if args.command == "transactions":
        do_interactive_cat = args.categorize
        pipeline.run_transactions_pipeline(
            fetch_max_isracard=args.fetch_max_isracard,
            fetch_bank_credit=args.fetch_bank_credit,
            fetch_bank_osh=args.fetch_bank_osh,
            from_date=args.from_date,
            to_date=args.to_date,
            route=not args.no_route,
            ingest=not args.no_ingest,
            to_csv=not args.no_csv,
            compile_=not args.no_compile,
            auto_categorize=args.auto_categorize and not do_interactive_cat,
            drop_profile=args.drop_profile,
        )
        if do_interactive_cat:
            pipeline.run_categorization_interactive()
        return 0

    if args.command == "all":
        if not args.no_fetch:
            pipeline.fetch_holdings()
            pipeline.fetch_transactions_max_isracard()
            pipeline.fetch_transactions_bank_credit_and_osh(
                credit=True,
                bank_osh=True,
                from_date=args.from_date,
                to_date=args.to_date,
            )
        auto_only = args.auto_categorize and not args.categorize
        pipeline.run_all_pipelines_after_shared_downloads(
            drop_profile=args.drop_profile,
            auto_categorize=auto_only,
        )
        if args.categorize:
            pipeline.run_categorization_interactive()
        return 0

    if args.command == "both-process":
        auto_only = args.auto_categorize and not args.categorize
        pipeline.run_all_pipelines_after_shared_downloads(
            drop_profile=args.drop_profile,
            auto_categorize=auto_only,
        )
        if args.categorize:
            pipeline.run_categorization_interactive()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
