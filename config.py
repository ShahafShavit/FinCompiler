import os
from dotenv import load_dotenv

load_dotenv()


def _data(*parts: str) -> str:
    return os.path.normpath(os.path.join("data", *parts))


# Shared browser download directory (Chrome / Selenium)
input_dir = os.path.join("data", "input") + os.sep
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

compiled_dir = "export\\compiled\\"
compiled_file = compiled_dir + "compiled.csv"
holdings_file = compiled_dir + "holdings.csv"
transaction_category_file = compiled_dir + "bak\\" + "transaction_category.csv"
static_dir = "data\\static\\"
stores_to_categories_file = static_dir + "stores_to_categories.csv"
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
similar_categories_file = "data\\static\\similar_pairs.csv"
fingerprint_db_file = os.path.join("data\\static", "fingerprint_db.csv")
web_dir = "web\\"
web_totals_file = web_dir + "data\\web_totals.csv"
expenses_web_file = web_dir + "expenses_web.html"
incomes_web_file = web_dir + "incomes_web.html"
