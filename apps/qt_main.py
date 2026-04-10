import datetime
import functools
import inspect
import logging
import os
import sys
import webbrowser

from PyQt6 import QtWidgets, uic

import config
import pipeline
from categorization import create_interaction_handler
from categorization.categorizer import CategorizeFile
from integrations import google_sheets
from logger import Logger, configure_pipeline_logging

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN_UI = os.path.join(_REPO_ROOT, "main.ui")

logger = Logger()
_py_log = logging.getLogger("main.ui")


def _pipeline_sink(message: str) -> None:
    logger.log_process_ongoing(message=message)


def _materialize_totals_csv_for_sheets() -> str:
    """Export the SQLite ledger to a temp CSV for ``GSLink`` (API still expects file paths)."""
    import tempfile

    from pipeline.ledger_dataframe import export_transactions_dataframe_to_csv

    fd, path = tempfile.mkstemp(prefix="ledger_totals_", suffix=".csv")
    os.close(fd)
    export_transactions_dataframe_to_csv(config.ledger_db_file, path)
    return path


def log_process(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _py_log.debug("UI action started: %s", func.__name__)
        logger.log_process_started(process_name=func.__name__)
        try:
            sig = inspect.signature(func)
            params = list(sig.parameters.values())
            if not params:
                result = func()
            else:
                result = func(*args, **kwargs)
        except Exception as e:
            _py_log.exception("UI action failed: %s", func.__name__)
            logger.log_process_finished(
                process_name=func.__name__,
                message=f"Failed with: {e}",
            )
            raise
        else:
            _py_log.debug("UI action finished: %s", func.__name__)
            logger.log_process_finished(process_name=func.__name__)
            return result

    return wrapper


def _clear_files_in_dir(folder: str) -> None:
    folder = os.path.normpath(folder)
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
                logger.log_process_ongoing(message=f"Removed old file -> {path}")
            except Exception as e:
                logger.log_process_ongoing(message=f"Failed removing {path}, error message: {e}")


def ui():
    current_year = str(int(datetime.datetime.now().year))
    holdings_sheetname = "Holdings" + current_year
    totals_sheetname = "Totals" + current_year

    def delete_old_files():
        check_sync()
        pipeline.ensure_pipeline_dirs()
        os.makedirs(config.unclassified_download_dir, exist_ok=True)
        for folder in (
            config.download_inbox_dir,
            config.unclassified_download_dir,
            config.holdings_inbox_dir,
            config.holdings_raw_dir,
            config.holdings_clean_dir,
            config.transactions_inbox_dir,
            config.transactions_raw_dir,
            config.transactions_clean_dir,
            config.raw_dir.rstrip(os.sep),
            config.cleaned_dir.rstrip(os.sep),
        ):
            _clear_files_in_dir(folder)

    @log_process
    def grab_holdings():
        pipeline.fetch_holdings(sink=_pipeline_sink)

    @log_process
    def process_holdings():
        pipeline.ensure_pipeline_dirs()
        pipeline.route_inbox(sink=_pipeline_sink)
        pipeline.ingest_holdings_inbox(sink=_pipeline_sink)
        pipeline.csv_from_raw_holdings(sink=_pipeline_sink)

    @log_process
    def compile_holdings():
        pipeline.compile_holdings_main(sink=_pipeline_sink)

    def push_holdings():
        gsh = google_sheets.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = google_sheets.GSLink(gsh)
        gslink.update_cloud([holdings_sheetname], [config.holdings_file], special_columns=[1, 2, 3, 5])

    @log_process
    def grab_transactions():
        credit_grab_checkbox = MainWindow.findChild(QtWidgets.QCheckBox, "CreditGrab")
        bank_grab_checkbox = MainWindow.findChild(QtWidgets.QCheckBox, "BankGrab")
        if not credit_grab_checkbox or not bank_grab_checkbox:
            logger.log_process_ongoing(message="Error locating check boxes")
        if credit_grab_checkbox.isChecked() or bank_grab_checkbox.isChecked():
            start_date = None
            end_date = None
            if MainWindow.findChild(QtWidgets.QCheckBox, "grabByDate").isChecked():
                start_date = (
                    None
                    if MainWindow.findChild(QtWidgets.QLineEdit, "startDate").text() == ""
                    else MainWindow.findChild(QtWidgets.QLineEdit, "startDate").text()
                )
                end_date = (
                    None
                    if MainWindow.findChild(QtWidgets.QLineEdit, "endDate").text() == ""
                    else MainWindow.findChild(QtWidgets.QLineEdit, "endDate").text()
                )
            pipeline.fetch_transactions_bank_credit_and_osh(
                credit=credit_grab_checkbox.isChecked(),
                bank_osh=bank_grab_checkbox.isChecked(),
                from_date=start_date,
                to_date=end_date,
                sink=_pipeline_sink,
            )
        else:
            logger.log_process_ongoing(message="No checkbox selected... not doing anything.")

    @log_process
    def process_transactions():
        pipeline.ensure_pipeline_dirs()
        pipeline.route_inbox(sink=_pipeline_sink)
        pipeline.ingest_transactions_inbox(sink=_pipeline_sink)
        pipeline.csv_from_raw_transactions(drop_profile="full", sink=_pipeline_sink)

    @log_process
    def compile_transactions():
        pipeline.compile_transactions_main(run_auto_categorize=False, sink=_pipeline_sink)

    @log_process
    def push_transactions():
        gsh = google_sheets.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = google_sheets.GSLink(gsh)
        path = _materialize_totals_csv_for_sheets()
        try:
            gslink.update_cloud([totals_sheetname], [path], special_columns=[3, 5])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @log_process
    def categorize_transactions():
        f = CategorizeFile(ledger_db_path=config.ledger_db_file, interaction_handler=create_interaction_handler())
        f.auto_categorize()
        f.manual_categorizer()

    @log_process
    def check_sync():
        gsh = google_sheets.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = google_sheets.GSLink(gsh)
        path = _materialize_totals_csv_for_sheets()
        try:
            gslink.sync_check(
                [holdings_sheetname, totals_sheetname],
                [config.holdings_file, path],
            )
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @log_process
    def pull_data():
        import pandas as pd

        from pipeline.ledger_compile_upsert import upsert_compiled_dataframe_to_ledger

        gsh = google_sheets.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = google_sheets.GSLink(gsh)
        path = _materialize_totals_csv_for_sheets()
        try:
            gslink.update_local(
                [holdings_sheetname, totals_sheetname],
                [config.holdings_file, path],
            )
        finally:
            if os.path.isfile(path):
                try:
                    df = pd.read_csv(path)
                    upsert_compiled_dataframe_to_ledger(df, config.ledger_db_file)
                except Exception:
                    _py_log.exception("Importing pulled totals into ledger failed")
                try:
                    os.remove(path)
                except OSError:
                    pass

    @log_process
    def push_data():
        gsh = google_sheets.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = google_sheets.GSLink(gsh)
        path = _materialize_totals_csv_for_sheets()
        try:
            gslink.update_cloud(
                [holdings_sheetname, totals_sheetname],
                [config.holdings_file, path],
            )
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @log_process
    def fix_null_category():
        CategorizeFile.fix_null_category_status()

    @log_process
    def fix_similar_categories():
        CategorizeFile.fix_similar_categories_in_file()

    @log_process
    def dupe_seeker():
        CategorizeFile.dupe_seeker()

    @log_process
    def push_monthly_look():
        gsh = google_sheets.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        google_sheets.push_monthly_look(gsh)

    @log_process
    def launch_sheet():
        webbrowser.open(f"https://docs.google.com/spreadsheets/d/{config.GOOGLE_WORKSHEET_ID}")

    configure_pipeline_logging()
    app = QtWidgets.QApplication(sys.argv)

    MainWindow = QtWidgets.QMainWindow()
    uic.loadUi(_MAIN_UI, MainWindow)

    deleteOldButton = MainWindow.findChild(QtWidgets.QPushButton, "deleteOldFiles")
    deleteOldButton.clicked.connect(delete_old_files)

    holdingsGrab = MainWindow.findChild(QtWidgets.QPushButton, "HoldingsGrab")
    holdingsGrab.clicked.connect(grab_holdings)

    processHoldingsBtn = MainWindow.findChild(QtWidgets.QPushButton, "HoldingsProcess")
    processHoldingsBtn.clicked.connect(process_holdings)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, "HoldingsCompile")
    processTransactionsBtn.clicked.connect(compile_holdings)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, "HoldingsPush")
    processTransactionsBtn.clicked.connect(push_holdings)

    transactionGrab = MainWindow.findChild(QtWidgets.QPushButton, "TransactionsGrab")
    transactionGrab.clicked.connect(grab_transactions)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, "TransactionsProcess")
    processTransactionsBtn.clicked.connect(process_transactions)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, "TransactionsCompile")
    processTransactionsBtn.clicked.connect(compile_transactions)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, "TransactionsPush")
    processTransactionsBtn.clicked.connect(push_transactions)

    categorizeTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, "categorizeTransactions")
    categorizeTransactionsBtn.clicked.connect(categorize_transactions)

    check_sync_btn = MainWindow.findChild(QtWidgets.QPushButton, "syncCheck")
    check_sync_btn.clicked.connect(check_sync)

    pull_data_btn = MainWindow.findChild(QtWidgets.QPushButton, "pullData")
    pull_data_btn.clicked.connect(pull_data)

    push_data_btn = MainWindow.findChild(QtWidgets.QPushButton, "pushData")
    push_data_btn.clicked.connect(push_data)

    fix_null_btn = MainWindow.findChild(QtWidgets.QPushButton, "fixNullCategory")
    fix_null_btn.clicked.connect(fix_null_category)

    fix_similar_btn = MainWindow.findChild(QtWidgets.QPushButton, "fix_similar_categories")
    fix_similar_btn.clicked.connect(fix_similar_categories)

    dupe_seeker_btn = MainWindow.findChild(QtWidgets.QPushButton, "dupe_seeker")
    dupe_seeker_btn.clicked.connect(dupe_seeker)

    monthly_look_btn = MainWindow.findChild(QtWidgets.QPushButton, "MonthlyLookPush")
    monthly_look_btn.clicked.connect(push_monthly_look)

    gs_launch_btn = MainWindow.findChild(QtWidgets.QPushButton, "launch_sheets")
    gs_launch_btn.clicked.connect(launch_sheet)

    MainWindow.show()
    sys.exit(app.exec())


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("--pipeline", "-p"):
        from apps.pipeline_cli import main as pipeline_cli_main

        raise SystemExit(pipeline_cli_main(sys.argv[2:]))
    ui()


if __name__ == "__main__":
    main()
