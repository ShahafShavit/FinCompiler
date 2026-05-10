import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def workspace_root() -> str:
    """
    Optional root for all runtime data paths (``data/``, ``web/``).

    ``data/export/`` (compiled CSVs) and ``data/pipeline/`` (per-pipeline workdirs)
    live under ``data/``. Set ``FINANCE_WORKSPACE_ROOT`` in the environment or ``.env``
    to use a separate tree for experiments or tests so the default repo ``data/`` is
    untouched. Absolute or relative paths are allowed; relative paths are resolved
    from the process current working directory.

    When unset or empty, paths are relative to the process CWD.
    """
    r = os.environ.get("FINANCE_WORKSPACE_ROOT", "").strip()
    if not r:
        return ""
    return os.path.normpath(os.path.expanduser(os.path.expandvars(r)))


def _w(*parts: str) -> str:
    """Path under ``FINANCE_WORKSPACE_ROOT``, or under CWD when the root is unset."""
    root = workspace_root()
    if root:
        return os.path.normpath(os.path.join(root, *parts))
    return os.path.normpath(os.path.join(*parts))


def _data(*parts: str) -> str:
    return _w("data", *parts)


# Shared browser download directory (Chrome / Selenium)
input_dir = _data("input") + os.sep
download_inbox_dir = _data("input")

# Legacy flat layout (still used by some __main__ blocks in modules)
raw_dir = _data("raw") + os.sep
cleaned_dir = _data("clean") + os.sep

# Holdings pipeline workdirs (balances / יתרות)
holdings_inbox_dir = _data("pipeline", "holdings", "inbox")
holdings_raw_dir = _data("pipeline", "holdings", "raw")
holdings_clean_dir = _data("pipeline", "holdings", "clean")

# Transactions pipeline (everything that is not classified as holdings)
transactions_inbox_dir = _data("pipeline", "transactions", "inbox")
transactions_raw_dir = _data("pipeline", "transactions", "raw")
transactions_clean_dir = _data("pipeline", "transactions", "clean")

# Workbooks that are not *.xls* or need manual naming
unclassified_download_dir = _data("input", "unclassified")

_compiled_root = _data("export", "compiled")
compiled_dir = _compiled_root + os.sep
# Legacy path (deprecated for data plane). Pipeline writes transactions/holdings to
# ``ledger_db_file`` only; some tooling may still reference this path when no DB exists.
compiled_file = os.path.join(_compiled_root, "compiled.csv")
# Legacy wide balances filename; canonical balances live in ``holdings_balance`` on ``ledger_db_file``.
holdings_file = os.path.join(_compiled_root, "holdings.csv")
# Canonical SQLite ledger + static mappings + holdings (single file). Respects
# ``FINANCE_WORKSPACE_ROOT`` via ``_data``. The repo ``.gitignore`` entry ``/data/``
# keeps this file (and the rest of ``data/``) out of version control. WAL/SHM
# sidecars, if enabled, live beside the DB path and are likewise under ``data/``.
ledger_db_file = _data("ledger.sqlite")
transaction_category_file = os.path.join(_compiled_root, "bak", "transaction_category.csv")
_static_root = _w("data", "static")
static_dir = _static_root + os.sep

# Timestamped pipeline snapshots (MIG-B). Lives under data/ (see .gitignore /data/).
backup_parent_dir = _data("_backups")
stores_to_categories_file = os.path.join(_static_root, "stores_to_categories.csv")
bank_username = os.getenv("bank_username")
bank_password = os.getenv("bank_password")
credit_username = os.getenv("credit_username")
credit_last6 = os.getenv("credit_last6")
credit_password = os.getenv("credit_password")
max_username = os.getenv("max_username")
max_password = os.getenv("max_password")
GOOGLE_API_USER = os.getenv("GOOGLE_API_USER")
GOOGLE_WORKSHEET_ID = os.getenv("GOOGLE_WORKSHEET_ID")
fingerprint_db_file = os.path.join(_static_root, "fingerprint_db.csv")
_web_root = _w("web")
web_dir = _web_root + os.sep
# Google Sheet tab for heatmap and desktop Totals push (single all-time tab).
totals_sheet_name = os.environ.get("FINANCE_TOTALS_SHEET_NAME", "Totals").strip() or "Totals"


def _calendar_year_str() -> str:
    return str(int(datetime.now().year))


def desktop_holdings_sheet_name() -> str:
    """
    Holdings worksheet title for local → Google Sheets push (calendar year suffix by default).

    Override with ``FINANCE_DESKTOP_HOLDINGS_SHEET`` when the sheet title is non-standard.
    """
    v = os.environ.get("FINANCE_DESKTOP_HOLDINGS_SHEET", "").strip()
    return v or f"Holdings{_calendar_year_str()}"


def desktop_totals_sheet_name() -> str:
    """
    Totals / full-ledger worksheet title for local → Google Sheets push.

    Defaults to ``totals_sheet_name`` (``FINANCE_TOTALS_SHEET_NAME``, usually ``Totals``) so
    web heatmap and Sheets push preview use **one** all-time worksheet — not a year-suffixed tab.

    Override with ``FINANCE_DESKTOP_TOTALS_SHEET`` for a non-standard title.
    """
    v = os.environ.get("FINANCE_DESKTOP_TOTALS_SHEET", "").strip()
    return v or totals_sheet_name


def desktop_sync_sheet_pairs() -> list[tuple[str, str]]:
    """``(worksheet_title, local_csv_path)`` for Sheets push (holdings first, totals second)."""
    return [
        (desktop_holdings_sheet_name(), holdings_file),
        (desktop_totals_sheet_name(), compiled_file),
    ]
expenses_web_file = os.path.join(_web_root, "expenses_web.html")
incomes_web_file = os.path.join(_web_root, "incomes_web.html")

# Local web control dashboard: pipeline at / and categorization at /categorize/ on the same port
control_http_host = os.environ.get("FINANCE_CONTROL_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
control_http_port = int(os.environ.get("FINANCE_CONTROL_HTTP_PORT", "8780") or "8780")
