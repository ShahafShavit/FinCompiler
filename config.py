import os
from dotenv import load_dotenv

load_dotenv()


def workspace_root() -> str:
    """
    Optional root for all workspace paths (data/, export/, web/).

    Set ``FINANCE_WORKSPACE_ROOT`` in the environment or ``.env`` to use a separate
    tree for experiments or automated tests so live ``data/`` and ``export/`` are
    untouched. Absolute or relative paths are allowed; relative paths are resolved
    from the process current working directory.

    When unset or empty, behavior matches the original layout (paths relative to CWD).
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

# Holdings pipeline workspace (balances / יתרות)
holdings_inbox_dir = _data("workspace", "holdings", "inbox")
holdings_raw_dir = _data("workspace", "holdings", "raw")
holdings_clean_dir = _data("workspace", "holdings", "clean")

# Transactions pipeline workspace (everything that is not classified as holdings)
transactions_inbox_dir = _data("workspace", "transactions", "inbox")
transactions_raw_dir = _data("workspace", "transactions", "raw")
transactions_clean_dir = _data("workspace", "transactions", "clean")

# Workbooks that are not *.xls* or need manual naming
unclassified_download_dir = _data("input", "unclassified")

_compiled_root = _w("export", "compiled")
compiled_dir = _compiled_root + os.sep
compiled_file = os.path.join(_compiled_root, "compiled.csv")
holdings_file = os.path.join(_compiled_root, "holdings.csv")
transaction_category_file = os.path.join(_compiled_root, "bak", "transaction_category.csv")
_static_root = _w("data", "static")
static_dir = _static_root + os.sep
stores_to_categories_file = os.path.join(_static_root, "stores_to_categories.csv")
bank_username = os.getenv("bank_username")
bank_password = os.getenv("bank_password")
credit_username = os.getenv("credit_username")
credit_last6 = os.getenv("credit_last6")
credit_password = os.getenv("credit_password")
max_username = os.getenv("max_username")
max_password = os.getenv("max_password")
telegram_bot_key = os.getenv("telegram_bot_key")
PREDEFINED_USER_ID = os.getenv("PREDEFINED_USER_ID")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")
GOOGLE_API_USER = os.getenv("GOOGLE_API_USER")
GOOGLE_WORKSHEET_ID = os.getenv("GOOGLE_WORKSHEET_ID")
similar_categories_file = os.path.join(_static_root, "similar_pairs.csv")
fingerprint_db_file = os.path.join(_static_root, "fingerprint_db.csv")
_web_root = _w("web")
web_dir = _web_root + os.sep
web_totals_file = os.path.join(_web_root, "data", "web_totals.csv")
expenses_web_file = os.path.join(_web_root, "expenses_web.html")
incomes_web_file = os.path.join(_web_root, "incomes_web.html")

# Manual categorization UI: "terminal" | "http" (browser on localhost)
categorize_ui_mode = os.environ.get("FINANCE_CATEGORIZE_UI", "terminal").strip().lower()
categorize_http_host = os.environ.get("FINANCE_CATEGORIZE_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
categorize_http_port = int(os.environ.get("FINANCE_CATEGORIZE_HTTP_PORT", "0") or "0")
_categorize_open = os.environ.get("FINANCE_CATEGORIZE_HTTP_OPEN_BROWSER", "1").strip().lower()
categorize_http_open_browser = _categorize_open not in ("0", "false", "no", "off")
