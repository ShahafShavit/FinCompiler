#!/usr/bin/env python3
"""
Run finance pipelines from the command line (browser-first; manual categorize via the local HTTP app).

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
from pathlib import Path

# Same layout as repo-root ``run_pipeline.py``: allow running this file directly
# (``python app/backend/apps/pipeline_cli.py …``) with imports resolving to ``app/backend``.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import config
import pipeline
from logger import configure_pipeline_logging

log = logging.getLogger(__name__)


def _run_cli_backup_first() -> None:
    from pipeline.backup import create_critical_paths_backup

    root, _manifest = create_critical_paths_backup()
    log.info("BACKUP: snapshot -> %s", root)


# ---------------------------------------------------------------------------
# Help text (shown with --help; keep lines reasonably short for 80-col terminals)
# ---------------------------------------------------------------------------

MAIN_DESCRIPTION = """\
Run the holdings (balances) and/or transactions pipelines from the CLI.

Data flow (short version):
  - Chrome / Selenium still download into the shared folder: data/input/
  - route sorts each spreadsheet into an isolated pipeline tree under data/pipeline/
  - holdings pipeline: inbox -> raw xlsx -> in-memory merge -> SQLite holdings_balance
  - transactions pipeline: inbox -> raw xlsx -> in-memory normalize -> SQLite ledger_transaction
  - Canonical data: data/ledger.sqlite only (no pipeline CSV staging in normal runs)
  - Set PIPELINE_DEBUG_DUMP=1 to also write normalized pipeline frames as pickle under pipeline/*/clean for debugging
  - Optional: set FINANCE_WORKSPACE_ROOT to a directory to use a separate data/ and web/
    tree (safe for tests or experiments; see config).

Pick a COMMAND below. Every command has its own options - use:
  python run_pipeline.py COMMAND --help
"""

MAIN_EPILOG = """\
commands:
  route          Classify files in data/input/*.xls* and MOVE them into pipeline inboxes.
  fetch-lti      Browser only: download Leumi LTI trade portfolio Excel into data/input/ (no route/compile).
  import-trade-portfolio  Parse SpreadsheetML אחזקות export -> data/ledger.sqlite (trade_portfolio_position)
  holdings       Balances: ingest -> normalize -> merge into data/ledger.sqlite (holdings_balance)
  transactions   Spending/income lines: ingest -> normalize -> upsert into data/ledger.sqlite
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
  python run_pipeline.py fetch-lti            # LTI portfolio Excel only -> data/input/
  python run_pipeline.py holdings --no-route
  python run_pipeline.py transactions --no-route

classification rule for route:
  - Filename contains trade-portfolio markers (אחזקות, trade-portfolio) -> data/pipeline/trade_portfolio/inbox
  - Filename contains bank balances marker (יתרות) -> holdings inbox
  - Any other .xls / .xlsx / .xlsm -> transactions inbox
  - Anything else -> data/input/unclassified/
"""


ROUTE_DESCRIPTION = """\
Scan the shared download folder (data/input/) for spreadsheet exports.

Each matching file is moved (not copied) into exactly one pipeline inbox so the two
pipelines never read each other's downloads. Safe to run after every browser session.
"""

ROUTE_EPILOG = """\
examples:
  python run_pipeline.py route
  python run_pipeline.py route --dry-run    # show what would move, change nothing
"""


FETCH_LTI_DESCRIPTION = """\
Open one Leumi browser session, log in with providers.json bank credentials, download the
LTI trade portfolio Excel export into the shared download folder (data/input/ by default).

Does not route, ingest, or compile. Disable the fetch in Settings (investment portfolio) or
providers.json ``investment_portfolio.enabled`` if you want to skip without editing code.
"""

FETCH_LTI_EPILOG = """\
examples:
  python run_pipeline.py fetch-lti

Requires Chrome and Selenium; same credentials as other Leumi fetches.
"""


HOLDINGS_DESCRIPTION = """\
Balances pipeline: workbooks that end up in data/pipeline/holdings/.

Steps (each can be skipped with --no-*):
  1. route   - move *.xls* from data/input into holdings vs transactions inboxes
  2. ingest  - normalize to .xlsx under holdings/raw
  3. (optional) If PIPELINE_DEBUG_DUMP=1: write cleaned pickle under holdings/clean
  4. compile - merge raw workbooks into data/ledger.sqlite (holdings_balance)
"""

HOLDINGS_EPILOG = """\
examples:
  python run_pipeline.py holdings                    # route + full pipeline
  python run_pipeline.py holdings --fetch            # download from bank first, then full pipeline
  python run_pipeline.py holdings --no-route         # files already in holdings/inbox
  python run_pipeline.py holdings --no-compile       # stop after ingest (no SQLite upsert)

note:
  --fetch opens a browser (Selenium). Requires bank credentials in .env.
"""


TRANSACTIONS_DESCRIPTION = """\
Transactions pipeline: card and account lines in data/pipeline/transactions/.

Steps (each can be skipped with --no-*):
  1. route   - move *.xls* from data/input into the right pipeline inbox
  2. ingest  - normalize to .xlsx under transactions/raw
  3. (optional) If PIPELINE_DEBUG_DUMP=1: write cleaned pickle under transactions/clean
  4. compile - normalize workbooks in memory and upsert into data/ledger.sqlite
  5. optional: --auto-categorize runs the automatic category pass (same as part of the old batch flow)
"""

TRANSACTIONS_EPILOG = """\
notes:
  - Bank credit + bank osh can run in one Leumi session (enable both flags).
  - --fetch-trade-portfolio opens a separate Leumi session for the LTI portfolio Excel export
    (use ``fetch-lti`` if you want download-only, no transactions pipeline).
  - --from-date / --to-date only affect --fetch-bank-osh (same strings as in the bank UI).
  - Row-drop rules for normalize live in ``data/private/transaction_drop_rules.json`` (Settings in the web app).
  - --categorize: after compile, run auto categorization; finish in the browser at /categorize/ (run ``python -m api.main``).

examples:
  python run_pipeline.py transactions
  python run_pipeline.py transactions --fetch-bank-credit --fetch-bank-osh
  python run_pipeline.py transactions --fetch-trade-portfolio   # also runs pipeline steps
  python run_pipeline.py fetch-lti                              download only (no compile)
  python run_pipeline.py transactions --fetch-max-isracard
  python run_pipeline.py transactions --no-route --auto-categorize
  python run_pipeline.py transactions --categorize
"""


ALL_DESCRIPTION = """\
Full portal downloads (unless --no-fetch), then one shared route, then BOTH pipelines.

Order:
  1. By default: download holdings, Max/Isracard exports, and Leumi credit + osh (one session
     for the bank flags). Use --fetch-trade-portfolio for an extra Leumi session (LTI trade portfolio Excel).
     Use --no-fetch to skip all browsers (same as placing files in data/input/ yourself).
  2. route - split everything in data/input into holdings vs transactions inboxes.
  3. Full holdings pipeline (no second route).
  4. Full transactions pipeline (no second route).
  5. Optional: --categorize runs auto categorization; open the web app (/categorize/) for any rows that still need a category.
"""

ALL_EPILOG = """\
  python run_pipeline.py all
  python run_pipeline.py all --fetch-trade-portfolio
  python run_pipeline.py all --no-fetch
  python run_pipeline.py all --categorize
  python run_pipeline.py all --no-fetch --categorize

--from-date / --to-date apply to the bank osh download inside the Leumi session.

--auto-categorize runs only the automatic category pass. --categorize runs auto plus points you
to the browser for anything left; do not combine with --auto-categorize.
"""


BOTH_PROCESS_DESCRIPTION = """\
Assume files are already in data/input/ (you downloaded manually). No browser.

  1. route - move spreadsheets into pipeline inboxes.
  2. Full holdings pipeline (ingest -> compile).
  3. Full transactions pipeline (ingest -> compile).

Does not delete files in data/input/ except by moving them during route (holdings and
transactions inboxes receive the files; shared inbox ends up empty of *.xls*).
"""

BOTH_PROCESS_EPILOG = """\
examples:
  python run_pipeline.py both-process
  python run_pipeline.py both-process --auto-categorize
  python run_pipeline.py both-process --categorize
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
        help="Sort downloads in data/input into trade-portfolio, holdings, and transactions inboxes.",
    )
    r.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which moves would happen; do not move any files.",
    )

    # --- import-trade-portfolio ---
    itp = sub.add_parser(
        "import-trade-portfolio",
        description=(
            "Read a trade-portfolio SpreadsheetML workbook (.xls XML) and upsert rows into "
            "data/ledger.sqlite table trade_portfolio_position (replace snapshot)."
        ),
        formatter_class=fmt,
        help="Import אחזקות / trade-portfolio export -> SQLite trade_portfolio_position.",
    )
    itp.add_argument(
        "--path",
        metavar="FILE",
        default=None,
        help="Workbook path. Default: newest SpreadsheetML .xls* in trade_portfolio inbox, then data/input.",
    )

    # --- fetch-lti (LTI trade portfolio download only) ---
    sub.add_parser(
        "fetch-lti",
        description=textwrap.dedent(FETCH_LTI_DESCRIPTION),
        epilog=textwrap.dedent(FETCH_LTI_EPILOG),
        formatter_class=fmt,
        help="Browser: Leumi LTI trade portfolio Excel -> data/input/ only (no pipeline).",
    )

    # --- holdings ---
    h = sub.add_parser(
        "holdings",
        description=textwrap.dedent(HOLDINGS_DESCRIPTION),
        epilog=textwrap.dedent(HOLDINGS_EPILOG),
        formatter_class=fmt,
        help="Balances pipeline -> data/ledger.sqlite (holdings_balance)",
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
        "--no-compile",
        action="store_true",
        help="Skip SQLite upsert for holdings (holdings_balance).",
    )

    # --- transactions ---
    t = sub.add_parser(
        "transactions",
        description=textwrap.dedent(TRANSACTIONS_DESCRIPTION),
        epilog=textwrap.dedent(TRANSACTIONS_EPILOG),
        formatter_class=fmt,
        help="Transactions pipeline -> data/ledger.sqlite",
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
        "--fetch-trade-portfolio",
        action="store_true",
        help="Separate Leumi session: download LTI trade portfolio Excel export.",
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
        "--no-compile",
        action="store_true",
        help="Skip merge into SQLite ledger (data/ledger.sqlite).",
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
        help="After compile: auto categorization; remaining rows → web app /categorize/ (run `python -m api.main`).",
    )
    tg_out.add_argument(
        "--backup-first",
        action="store_true",
        help="Before the pipeline: write a timestamped snapshot under data/_backups/ "
        "(export dir, static dir, web/data, ledger.sqlite when present).",
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
    ag.add_argument(
        "--fetch-trade-portfolio",
        action="store_true",
        help="Also download Leumi LTI trade portfolio Excel (extra Leumi browser session after holdings).",
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
        help="After compile: auto categorization; remaining rows → web app /categorize/ (run `python -m api.main`).",
    )
    ag2.add_argument(
        "--backup-first",
        action="store_true",
        help="Before the pipelines: snapshot export, static, ledger, and web/data (see transactions --help).",
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
        help="After compile: auto categorization; remaining rows → web app /categorize/ (run `python -m api.main`).",
    )
    b.add_argument(
        "--backup-first",
        action="store_true",
        help="Before the pipelines: snapshot export, static, ledger, and web/data (see transactions --help).",
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
    pipeline.ensure_pipeline_dirs()

    if args.command == "route":
        pipeline.route_inbox(dry_run=getattr(args, "dry_run", False))
        return 0

    if args.command == "import-trade-portfolio":
        from pipeline.trade_portfolio_import import import_trade_portfolio_file, resolve_default_trade_portfolio_path

        path: str | None = args.path
        if not path:
            path = resolve_default_trade_portfolio_path()
        if not path:
            log.error(
                "import-trade-portfolio: no SpreadsheetML workbook found under %s or %s",
                config.trade_portfolio_inbox_dir,
                config.download_inbox_dir,
            )
            return 1
        rep = import_trade_portfolio_file(path)
        log.info(
            "import-trade-portfolio: inserted=%s deleted_prior=%s snapshot=%s portfolio=%s",
            rep["inserted"],
            rep["deleted"],
            rep["snapshot_date"],
            rep["portfolio_account"],
        )
        return 0

    if args.command == "fetch-lti":
        pipeline.fetch_trade_portfolio()
        return 0

    if args.command == "holdings":
        pipeline.run_holdings_pipeline(
            fetch=args.fetch,
            route=not args.no_route,
            ingest=not args.no_ingest,
            compile_=not args.no_compile,
        )
        return 0

    if args.command == "transactions":
        if args.backup_first:
            _run_cli_backup_first()
        do_interactive_cat = args.categorize
        pipeline.run_transactions_pipeline(
            fetch_max_isracard=args.fetch_max_isracard,
            fetch_bank_credit=args.fetch_bank_credit,
            fetch_bank_osh=args.fetch_bank_osh,
            fetch_lti_portfolio=args.fetch_trade_portfolio,
            from_date=args.from_date,
            to_date=args.to_date,
            route=not args.no_route,
            ingest=not args.no_ingest,
            compile_=not args.no_compile,
            auto_categorize=args.auto_categorize and not do_interactive_cat,
        )
        if do_interactive_cat:
            pipeline.run_auto_categorize_with_web_remainder()
        return 0

    if args.command == "all":
        if args.backup_first:
            _run_cli_backup_first()
        if not args.no_fetch:
            pipeline.run_portal_fetches(
                holdings=True,
                trade_portfolio=args.fetch_trade_portfolio,
                max_isracard=True,
                bank_credit=True,
                bank_osh=True,
                from_date=args.from_date,
                to_date=args.to_date,
            )
        auto_only = args.auto_categorize and not args.categorize
        pipeline.run_all_pipelines_after_shared_downloads(
            auto_categorize=auto_only,
        )
        if args.categorize:
            pipeline.run_auto_categorize_with_web_remainder()
        return 0

    if args.command == "both-process":
        if args.backup_first:
            _run_cli_backup_first()
        auto_only = args.auto_categorize and not args.categorize
        pipeline.run_all_pipelines_after_shared_downloads(
            auto_categorize=auto_only,
        )
        if args.categorize:
            pipeline.run_auto_categorize_with_web_remainder()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
