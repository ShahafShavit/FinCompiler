import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def workspace_root() -> str:
    """
    Optional root for all runtime data paths (``data/``, ``web/``).

    ``data/export/`` and ``data/pipeline/`` (per-pipeline workdirs) live under ``data/``.
    Set ``FINANCE_WORKSPACE_ROOT`` in the environment or ``.env`` to use a separate tree
    for experiments or tests so the default repo ``data/`` is untouched. Absolute or
    relative paths are allowed; relative paths are resolved from the process CWD.

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

_export_root = _data("export")
export_dir = _export_root + os.sep
# Historical folder name; may be empty. Backups still mirror this path when present.
compiled_dir = os.path.join(_export_root, "compiled") + os.sep

# Canonical SQLite ledger + static mappings + holdings (single file). Respects
# ``FINANCE_WORKSPACE_ROOT`` via ``_data``. The repo ``.gitignore`` entry ``/data/``
# keeps this file (and the rest of ``data/``) out of version control. WAL/SHM
# sidecars, if enabled, live beside the DB path and are likewise under ``data/``.
ledger_db_file = _data("ledger.sqlite")

_static_root = _w("data", "static")
static_dir = _static_root + os.sep

# UI-managed secrets (portal + Google Sheets path/id). See ``providers``.
private_dir = _data("private")
providers_file = os.path.join(private_dir, "providers.json")
# Transaction workbook row-drop rules (non-secret); created on first read if missing.
transaction_drop_rules_file = os.path.join(private_dir, "transaction_drop_rules.json")

# Timestamped pipeline snapshots (MIG-B). Lives under data/ (see .gitignore /data/).
backup_parent_dir = _data("_backups")
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


def desktop_sync_sheet_order() -> list[str]:
    """Worksheet titles for Sheets push (holdings first, totals second). Paths are not used."""
    return [desktop_holdings_sheet_name(), desktop_totals_sheet_name()]


expenses_web_file = os.path.join(_web_root, "expenses_web.html")
incomes_web_file = os.path.join(_web_root, "incomes_web.html")

# Local web control dashboard: pipeline at / and categorization at /categorize/ on the same port
control_http_host = os.environ.get("FINANCE_CONTROL_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
control_http_port = int(os.environ.get("FINANCE_CONTROL_HTTP_PORT", "8780") or "8780")
