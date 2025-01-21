import datetime
import glob
import os
import time
import sys
from openpyxl.descriptors import NoneSet
import compile_handler
import config
import csv_handler
import gs_handler
import import_handler
import input_handler
from categorizer import CategorizeFile
from PyQt6 import QtWidgets, uic
from logger import Logger
import functools
import inspect

logger = Logger()

def log_process(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.log_process_started(process_name=func.__name__)
        try:
            # Determine how many positional parameters the function actually accepts.
            sig = inspect.signature(func)
            # List of parameters that are positional or keyword (not *args/**kwargs)
            params = list(sig.parameters.values())
            if not params:
                result = func()
            else:
                result = func(*args, **kwargs)
        except Exception as e:
            logger.log_process_finished(
                process_name=func.__name__,
                message=f"Failed with: {e}"
            )
            raise
        else:
            logger.log_process_finished(process_name=func.__name__)
            return result
    return wrapper

class Process:
    def __init__(self, process_type: str):
        """
        :param process_type: can be only: credit, bank or holdings. anyting else returns an error.
        """
        if process_type.lower() not in ['credit', 'bank', 'holdings']:
            raise Exception("process type not Credit, Bank, Holdings.")
        self.type = process_type.lower()
        self.directories = {
            'input': config.input_dir,
            'xlsx': config.raw_dir,
            'csvs': config.cleaned_dir,
            'compiled': config.compiled_dir,
        }
        for directory in self.directories.values():
            os.makedirs(directory, exist_ok=True)
        os.makedirs(os.path.dirname(config.transaction_category_file), exist_ok=True)

    def clean_history(self, keys_to_ignore=None):
        if keys_to_ignore is None:
            keys_to_ignore = []
        for key, folder in self.directories.items():
            if not os.path.isdir(folder):
                logger.log_process_ongoing(message=f"The folder {folder} does not exist.")
                continue
            if key in keys_to_ignore:
                logger.log_process_ongoing(message=f"Skipping {folder} as requested.")
                continue
            if keys_to_ignore == ['All']:
                logger.log_process_ongoing(message=f"Not deleting any file.")
                return
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)

                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        logger.log_process_ongoing(message=f"Deleted file: {file_path}")
                    except Exception as e:
                        logger.log_process_ongoing(message=f"Failed to delete {file_path}. Reason: {e}")
        logger.log_process_ongoing(message="All used files has been removed.")

    def launch(self, from_date=None, to_date=None):
        def _convert_to_xlsx():
            logger.log_process_ongoing(message="CONVERT PROCESS START")
            file_list = glob.glob(self.directories['input'] + '*.xls*')
            for file in file_list:
                f = input_handler.File(file)
                f.to_xlsx()
            logger.log_process_ongoing(message="CONVERT PROCESS END")
        def _convert_to_csv(type):
            logger.log_process_ongoing(message="CSV CONVERSION START")
            file_list = glob.glob(self.directories['xlsx'] + '*.xls*')
            if type == 'holdings':
                logger.log_process_ongoing(message="CSV CONVERSION START")
                for f in file_list:
                    hf = csv_handler.HoldingsFile(f)
                    rename_map = {
                        'נכון לתאריך': 'תאריך',
                    }
                    hf.unify_columns(rename_map)
                    hf.to_csv()
                logger.log_process_ongoing(message="CSV CONVERSION END")

            elif type in ['credit', 'bank']:
                for file in file_list:
                    f = csv_handler.TransactionFile(file)
                    f.drop_columns(
                        ['סכום עסקה', 'מטבע חיוב', 'מטבע עסקה מקורי', 'מטבע מקור', 'מטבע לחיוב', 'סכום עסקה מקורי',
                         'סכום מקורי'
                            , 'מספר שובר', 'תאריך חיוב', 'שער המרה ממטבע מקור/התחשבנות לש"ח', 'אופן ביצוע ההעסקה',
                         'הערות',
                         'סוג עסקה', 'תאריך ערך', 'הערה', 'אסמכתא', 'קטגוריה', 'היתרה בש"ח'])
                    f.drop_by_column_and_value('מקור עסקה', 'כרטיס דביט')
                    f.drop_by_column_and_value('מקור עסקה', 'ישראכרט בע"מ-י')
                    f.drop_by_column_and_value('מקור עסקה', 'מקס איט פיננ-י')
                    f.drop_by_column_and_value('מקור עסקה', 'פקדון אינטר700')

                    f.to_csv()
                del file_list

            else:
                raise ValueError("Type must be 'holdings','credit','bank'.")
            logger.log_process_ongoing(message="CSV CONVERSION END")
        def _compile(type):
            if type == 'holdings':
                logger.log_process_ongoing(message="COMPILER START")
                d = compile_handler.Compiler(config.holdings_file)
                d.__compile_new__(self.directories['csvs'], suffix=type)
                d.compile_to_main()
                d.save_all()
                logger.log_process_ongoing(message="COMPILER END")
            elif type in ['credit', 'bank']:
                logger.log_process_ongoing(message="COMPILER START")
                c = compile_handler.Compiler(config.compiled_file)
                c.__compile_new__(self.directories['csvs'], suffix=type)
                c.compile_to_main()
                main_file, new_file = c.save_all()
                del c
                logger.log_process_ongoing(message="COMPILER END")
                categorizer = CategorizeFile(main_file)
                categorizer.auto_categorize()
            else:
                raise ValueError("Type must be 'holdings','credit','bank'.")

        self.import_(from_date=from_date, to_date=to_date)
        _convert_to_xlsx()
        _convert_to_csv(self.type)
        _compile(self.type)
        self.clean_history(keys_to_ignore=['compiled', 'input'])

    def import_(self, from_date=None, to_date=None):
        if self.type == 'credit':
            # FILE IMPORT START
            logger.log_process_ongoing(message="IMPORT PROCESS START")
            # importer = import_handler.MaxCredit(config.max_username, config.max_password)
            failed = True
            while failed:
                try:
                    importer = import_handler.MaxCredit(config.max_username, config.max_password)
                    try:
                        importer.download()
                        failed = False
                    except FileNotFoundError as e:
                        logger.log_process_ongoing(message=e)
                        logger.log_process_ongoing(message="Retrying download until success")
                except Exception as e:
                    logger.log_process_ongoing(message=e)
                    logger.log_process_ongoing(message="retrying untill success")

            del importer
            failed = True

            while failed:
                try:
                    importer = import_handler.IsracardCredit(config.credit_username, config.credit_password,
                                                             config.credit_last6)
                    try:
                        importer.download()
                        failed = False
                    except FileNotFoundError as e:
                        logger.log_process_ongoing(message=e)
                        logger.log_process_ongoing(message="Retrying download until success")
                except Exception as e:
                    logger.log_process_ongoing(message=e)
                    logger.log_process_ongoing(message="Retrying untill success")
            del importer
            logger.log_process_ongoing(message="IMPORT PROCESS END")
        elif self.type == 'bank':
            failed = True

            while failed:
                try:
                    b = import_handler.Bank(config.bank_username, config.bank_password)
                    try:
                        b.download('osh', from_date=from_date, to_date=to_date)
                        failed = False
                    except FileNotFoundError as e:
                        logger.log_process_ongoing(message=e)
                        logger.log_process_ongoing(message="Retrying file download.")
                except Exception as e:
                    logger.log_process_ongoing(message=e)
                    logger.log_process_ongoing(message="Retrying untill success")
        elif self.type == 'holdings':

            failed = True
            while failed:
                try:
                    b = import_handler.Bank(config.bank_username, config.bank_password)
                    try:
                        b.download('holdings')
                        failed = False
                    except FileNotFoundError as e:
                        logger.log_process_ongoing(message=e)
                        logger.log_process_ongoing(message="Retrying download until success")
                except Exception as e:
                    logger.log_process_ongoing(message=e)
                    logger.log_process_ongoing(message="Retrying untill success")
            del b

def ui():
    def delete_old_files():
        input_files = glob.glob(os.path.join(config.input_dir,'*.*'))
        raw_files = glob.glob(os.path.join(config.raw_dir,'*.*'))
        cleaned_files = glob.glob(os.path.join(config.cleaned_dir,'*.*'))
        testing = input_files
        testing += raw_files
        testing += cleaned_files
        for file in testing:
            try:
                os.remove(file)
                logger.log_process_ongoing(message=f"Removed old file -> {file}")
            except Exception as e:
                logger.log_process_ongoing(message=f"Failed removing {file}, error message: {e}")

    @log_process
    def grab_holdings():
        b = import_handler.Bank(config.bank_username, config.bank_password)
        b.download("holdings")

    @log_process
    def process_holdings():
        holdings = glob.glob(os.path.join(config.input_dir,  '*יתרות*.xls*'))
        for holding in holdings:
            logger.log_process_ongoing(message=f"Processing Holdings file to xlsx >> {holding}")
            f = input_handler.File(holding)
            f.to_xlsx()
            logger.log_process_ongoing(message=f"Finished converting to xlsx {holding}")
        holdings = glob.glob(os.path.join(config.raw_dir, '*יתרות*.xls*'))
        for holding in holdings:
            hf = csv_handler.HoldingsFile(holding)
            rename_map = {
                'נכון לתאריך': 'תאריך',
            }
            hf.unify_columns(rename_map)
            hf.to_csv()
            logger.log_process_ongoing(message=f"Finished converting to csv {holding}")

    @log_process
    def compile_holdings():
        holdings_file = glob.glob(config.cleaned_dir + '*Holdings*.csv')
        if len(holdings_file) == 1:
            d = compile_handler.Compiler(config.holdings_file)
            d.__compile_new__(config.cleaned_dir, suffix='holdings')
            d.compile_to_main()
            d.save_all()

    def push_holdings():
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.update_cloud(['Holdings'], [config.holdings_file], special_columns=[1,2,3,5])

    @log_process
    def grab_transactions():
        credit_grab_checkbox = MainWindow.findChild(QtWidgets.QCheckBox, 'CreditGrab')
        bank_grab_checkbox = MainWindow.findChild(QtWidgets.QCheckBox, 'BankGrab')
        if not credit_grab_checkbox or not bank_grab_checkbox:
            logger.log_process_ongoing(message="Error locating check boxes")
        if credit_grab_checkbox.isChecked() or bank_grab_checkbox.isChecked():
            logger.log_process_ongoing(message="Launching bank website..")
            downloader = import_handler.Bank(config.bank_username, config.bank_password)
            if credit_grab_checkbox.isChecked():

                logger.log_process_ongoing(message="Downloading credit details...")
                downloader.download("credit")
            if bank_grab_checkbox.isChecked():
                logger.log_process_ongoing(message="Downloading bank transactions details...")
                start_date = None
                end_date = None
                if MainWindow.findChild(QtWidgets.QCheckBox, 'grabByDate').isChecked():
                    start_date = None if MainWindow.findChild(QtWidgets.QLineEdit, 'startDate').text() == "" else MainWindow.findChild(QtWidgets.QLineEdit, 'startDate').text()
                    end_date = None if MainWindow.findChild(QtWidgets.QLineEdit, 'endDate').text() == "" else MainWindow.findChild(QtWidgets.QLineEdit, 'endDate').text()
                downloader.download(file="osh",from_date= start_date, to_date=end_date)
            del downloader

        else:
            logger.log_process_ongoing(message="No checkbox selected... not doing anything.")

    @log_process
    def process_transactions():
        files = glob.glob(os.path.join(config.input_dir,  '*.xls*'))
        for file in files:
            if 'יתרות' not in file:
                logger.log_process_ongoing(message=f"Processing Transactions file to xlsx >> {file}")
                f = input_handler.File(file)
                f.to_xlsx()

        files = glob.glob(os.path.join(config.raw_dir,  '*.xls*'))
        for file in files:
            if 'יתרות' not in file:
                f = csv_handler.TransactionFile(file)
                f.drop_columns(
                    ['סכום עסקה', 'מטבע חיוב', 'מטבע עסקה מקורי', 'מטבע מקור', 'מטבע לחיוב', 'סכום עסקה מקורי',
                     'סכום מקורי'
                        , 'מספר שובר', 'תאריך חיוב', 'שער המרה ממטבע מקור/התחשבנות לש"ח', 'אופן ביצוע ההעסקה', 'הערות',
                     'סוג עסקה', 'תאריך ערך', 'הערה', 'אסמכתא', 'קטגוריה', 'היתרה בש"ח'])
                f.drop_by_column_and_value('מקור עסקה', 'כרטיס דביט')
                f.drop_by_column_and_value('מקור עסקה', 'קניה-אינטרנט')
                f.drop_by_column_and_value('מקור עסקה', 'מכירה-אינטרנט')
                f.drop_by_column_and_value('מקור עסקה', 'ישראכרט בע"מ-י')
                f.drop_by_column_and_value('מקור עסקה', 'מקס איט פיננ-י')
                f.drop_by_column_and_value('מקור עסקה', 'פקדון אינטר700')
                f.drop_by_column_and_value('מקור עסקה', 'פקדון אינטרנט')
                f.drop_by_column_and_value('מקור עסקה', 'פקדון*')
                f.drop_by_column_and_value('מקור עסקה', 'קנית ני"ע')
                f.drop_by_column_and_value('מקור עסקה', 'מכירת ני"ע')
                f.drop_by_column_and_value('מקור עסקה', 'קנית ני""ע')
                f.drop_by_column_and_value('מקור עסקה', 'החלפת נייר ערך')
                f.to_csv()

    @log_process
    def compile_transactions():
        c = compile_handler.Compiler(config.compiled_file)
        c.__compile_new__(config.cleaned_dir, suffix='credit')
        c.compile_to_main()
        c.save_all()

    @log_process
    def push_transactions():
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.update_cloud(['Totals'], [config.compiled_file], special_columns=[3,5])

    @log_process
    def categorize_transactions():
        f = CategorizeFile(config.compiled_file)
        f.auto_categorize()
        f.manual_categorizer()

    @log_process
    def check_sync():
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.sync_check(['Holdings', 'Totals'], [config.holdings_file, config.compiled_file])

    @log_process
    def pull_data():
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.update_local(['Holdings', 'Totals'], [config.holdings_file, config.compiled_file])

    @log_process
    def push_data():
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.update_cloud(['Holdings', 'Totals'], [config.holdings_file, config.compiled_file])

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
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gs_handler.push_monthly_look(gsh)

    app = QtWidgets.QApplication(sys.argv)

    MainWindow = QtWidgets.QMainWindow()
    uic.loadUi('main.ui', MainWindow)

    deleteOldButton = MainWindow.findChild(QtWidgets.QPushButton, 'deleteOldFiles')
    deleteOldButton.clicked.connect(delete_old_files)

    holdingsGrab = MainWindow.findChild(QtWidgets.QPushButton, 'HoldingsGrab')
    holdingsGrab.clicked.connect(grab_holdings)


    processHoldingsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'HoldingsProcess')
    processHoldingsBtn.clicked.connect(process_holdings)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'HoldingsCompile')
    processTransactionsBtn.clicked.connect(compile_holdings)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'HoldingsPush')
    processTransactionsBtn.clicked.connect(push_holdings)

    transactionGrab = MainWindow.findChild(QtWidgets.QPushButton, 'TransactionsGrab')
    transactionGrab.clicked.connect(grab_transactions)


    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'TransactionsProcess')
    processTransactionsBtn.clicked.connect(process_transactions)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'TransactionsCompile')
    processTransactionsBtn.clicked.connect(compile_transactions)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'TransactionsPush')
    processTransactionsBtn.clicked.connect(push_transactions)

    categorizeTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'categorizeTransactions')
    categorizeTransactionsBtn.clicked.connect(categorize_transactions)

    check_sync_btn = MainWindow.findChild(QtWidgets.QPushButton, 'syncCheck')
    check_sync_btn.clicked.connect(check_sync)

    pull_data_btn = MainWindow.findChild(QtWidgets.QPushButton, 'pullData')
    pull_data_btn.clicked.connect(pull_data)

    push_data_btn = MainWindow.findChild(QtWidgets.QPushButton, 'pushData')
    push_data_btn.clicked.connect(push_data)

    fix_null_btn = MainWindow.findChild(QtWidgets.QPushButton, 'fixNullCategory')
    fix_null_btn.clicked.connect(fix_null_category)

    fix_similar_btn = MainWindow.findChild(QtWidgets.QPushButton, 'fix_similar_categories')
    fix_similar_btn.clicked.connect(fix_similar_categories)

    dupe_seeker_btn = MainWindow.findChild(QtWidgets.QPushButton, 'dupe_seeker')
    dupe_seeker_btn.clicked.connect(dupe_seeker)

    monthly_look_btn = MainWindow.findChild(QtWidgets.QPushButton, 'MonthlyLookPush')
    monthly_look_btn.clicked.connect(push_monthly_look)

    MainWindow.show()
    sys.exit(app.exec())



if __name__ == "__main__":
    ui()
