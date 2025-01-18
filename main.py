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
from PyQt6.QtWidgets import QApplication


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

    def clean_history(self, keys_to_ignore=[]):
        for key, folder in self.directories.items():
            if not os.path.isdir(folder):
                print(f"The folder {folder} does not exist.")
                continue
            if key in keys_to_ignore:
                print(f"Skipping {folder} as requested.")
                continue
            if keys_to_ignore == ['All']:
                print(f"Not deleting any file.")
                return
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)

                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        print(f"Deleted file: {file_path}")
                    except Exception as e:
                        print(f"Failed to delete {file_path}. Reason: {e}")
        print("All used files has been removed.")

    def launch(self, from_date=None, to_date=None):

        def _convert_to_xlsx():
            print("CONVERT PROCESS START")
            file_list = glob.glob(self.directories['input'] + '*.xls*')
            for file in file_list:
                f = input_handler.File(file)
                f.to_xlsx()
            print("CONVERT PROCESS END")

        def _convert_to_csv(type):
            print("CSV CONVERSION START")
            file_list = glob.glob(self.directories['xlsx'] + '*.xls*')
            if type == 'holdings':
                print("CSV CONVERSION START")
                for f in file_list:
                    hf = csv_handler.HoldingsFile(f)
                    rename_map = {
                        'נכון לתאריך': 'תאריך',
                    }
                    hf.unify_columns(rename_map)
                    hf.to_csv()
                print("CSV CONVERSION END")

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
            print("CSV CONVERSION END")

        def _compile(type):
            if type == 'holdings':
                print("COMPILER START")
                d = compile_handler.Compiler(config.holdings_file)
                d.__compile_new__(self.directories['csvs'], suffix=type)
                d.compile_to_main()
                d.save_all()
                print("COMPILER END")
            elif type in ['credit', 'bank']:
                print("COMPILER START")
                c = compile_handler.Compiler(config.compiled_file)
                c.__compile_new__(self.directories['csvs'], suffix=type)
                c.compile_to_main()
                main_file, new_file = c.save_all()
                del c
                print("COMPILER END")
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
            print("IMPORT PROCESS START")
            # importer = import_handler.MaxCredit(config.max_username, config.max_password)
            failed = True
            while failed:
                try:
                    importer = import_handler.MaxCredit(config.max_username, config.max_password)
                    try:
                        importer.download()
                        failed = False
                    except FileNotFoundError as e:
                        print(e)
                        print("Retrying download until success")
                except Exception as e:
                    print(e)
                    print("retrying untill success")

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
                        print(e)
                        print("Retrying download until success")
                except Exception as e:
                    print(e)
                    print("Retrying untill success")
            del importer
            print("IMPORT PROCESS END")
        elif self.type == 'bank':
            failed = True

            while failed:
                try:
                    b = import_handler.Bank(config.bank_username, config.bank_password)
                    try:
                        b.download('osh', from_date=from_date, to_date=to_date)
                        failed = False
                    except FileNotFoundError as e:
                        print(e)
                        print("Retrying file download.")
                except Exception as e:
                    print(e)
                    print("Retrying untill success")
        elif self.type == 'holdings':

            failed = True
            while failed:
                try:
                    b = import_handler.Bank(config.bank_username, config.bank_password)
                    try:
                        b.download('holdings')
                        failed = False
                    except FileNotFoundError as e:
                        print(e)
                        print("Retrying download until success")
                except Exception as e:
                    print(e)
                    print("Retrying untill success")
            del b


def main():
    current_day = datetime.datetime.now().day
    if current_day in [1, 7, 14, 21]:
        p = Process("bank")
        p.launch()
    if current_day == 1:
        p = Process("holdings")
        p.launch()
    if current_day == 11:
        p = Process("credit")
        p.launch()


def job():
    main()


def compile_all():
    # p = Process("credit")
    # p.launch()
    # p = Process("bank")
    # p.launch()
    p = Process("holdings")
    p.launch()


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
                print(f"Removed old file -> {file}")
            except Exception as e:
                print(f"Failed removing {file}, error message: {e}")



    def grabHoldings():
        print("***Started holdings data scrape process")
        b = import_handler.Bank(config.bank_username, config.bank_password)
        b.download("holdings")
        print("***Finished holdings data scrape process")

    def processHoldings():
        print("***Started holdings data refining process")
        holdings = glob.glob(os.path.join(config.input_dir,  '*יתרות*.xls*'))
        for holding in holdings:
            print(f"Processing Holdings file to xlsx >> {holding}")
            f = input_handler.File(holding)
            f.to_xlsx()
            print(f"Finished converting to xlsx {holding}")
            time.sleep(0.3)

        holdings = glob.glob(os.path.join(config.raw_dir, '*יתרות*.xls*'))
        for holding in holdings:
            hf = csv_handler.HoldingsFile(holding)
            rename_map = {
                'נכון לתאריך': 'תאריך',
            }
            hf.unify_columns(rename_map)
            hf.to_csv()
            print(f"Finished converting to csv {holding}")
            time.sleep(0.3)
        print("***Finished holdings data scrape process")

    def compileHoldings():
        print("***Compiling Holdings...")
        holdings_file = glob.glob(config.cleaned_dir + '*Holdings*.csv')
        if len(holdings_file) == 1:
            d = compile_handler.Compiler(config.holdings_file)
            d.__compile_new__(config.cleaned_dir, suffix='holdings')
            d.compile_to_main()
            d.save_all()
        print("***Compiled Holdings to main")

    def pushHoldings():
        print("***Pushing Holdings To Cloud")
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.update_cloud(['Holdings'], [config.holdings_file], [1,2,3,5])
        print("***Finished Pushing Holdings To Cloud")

    def grabTransactions():
        def print_children(widget, level=0):
            for child in widget.children():
                print("  " * level + child.objectName())
                print_children(child, level + 1)

        print("***Started transactions data scrape process")
        credit_grab_checkbox = MainWindow.findChild(QtWidgets.QCheckBox, 'CreditGrab')
        bank_grab_checkbox = MainWindow.findChild(QtWidgets.QCheckBox, 'BankGrab')
        if not credit_grab_checkbox or not bank_grab_checkbox:
            print("Error locating check boxes")
        if credit_grab_checkbox.isChecked() or bank_grab_checkbox.isChecked():
            print("Launching bank website..")
            downloader = import_handler.Bank(config.bank_username, config.bank_password)
            if credit_grab_checkbox.isChecked():
                print("Downloading credit details...")
                downloader.download("credit")
            if bank_grab_checkbox.isChecked():
                print("Downloading bank transactions details...")
                start_date = None
                end_date = None
                if MainWindow.findChild(QtWidgets.QCheckBox, 'grabByDate').isChecked():
                    start_date = None if MainWindow.findChild(QtWidgets.QLineEdit, 'startDate').text() == "" else MainWindow.findChild(QtWidgets.QLineEdit, 'startDate').text()
                    end_date = None if MainWindow.findChild(QtWidgets.QLineEdit, 'endDate').text() == "" else MainWindow.findChild(QtWidgets.QLineEdit, 'endDate').text()
                downloader.download(file="osh",from_date= start_date, to_date=end_date)
            del downloader

        else:
            print("No checkbox selected... not doing anything.")
        print("***Finished transactions data scrape process")
    def processTransactions():
        print("***Started transactions data refinement process")
        files = glob.glob(os.path.join(config.input_dir,  '*.xls*'))
        for file in files:
            if 'יתרות' not in file:
                print(f"Processing Transactions file to xlsx >> {file}")
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
        print("***Finished transactions data refinement process")

    def compileTransactions():
        print("***Started compiling transactions to a single file")
        c = compile_handler.Compiler(config.compiled_file)
        c.__compile_new__(config.cleaned_dir, suffix='credit')
        c.compile_to_main()
        c.save_all()
        print("***Finished compiling transactions to a single file")

    def pushTransactions():
        print("***Pushing transactions file to cloud process initiated")
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.update_cloud(['Totals'], [config.compiled_file], special_columns=[3,5])
        print("***Pushing transactions file to cloud process FINISHED")

    def categorizeTransactions():
        print("***Started categorization process")
        f = CategorizeFile(config.compiled_file)
        f.auto_categorize()
        f.manual_categorizer()
        print("***Finished categorizing")

    def checkSync():
        print("***Started sync check")
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.sync_check(['Holdings', 'Totals'], [config.holdings_file, config.compiled_file])
        print("***Finished sync check")

    def pullData():
        print("***Started pulling data process.")
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.update_local(['Holdings', 'Totals'], [config.holdings_file, config.compiled_file])
        print("***Finished pulling data process.")
    def pushData():
        print("***Started pushing data process.")
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gslink = gs_handler.GSLink(gsh)
        gslink.update_cloud(['Holdings', 'Totals'], [config.holdings_file, config.compiled_file])
        print("***Finished pushing data process.")
    def fix_null_cate():
        print("***Started fix null category process")
        CategorizeFile.fix_null_category_status()
        print("***Finished fix null category process")
    def fix_similar_categories():
        print("***Started fix similar category process")
        CategorizeFile.fix_similar_categories_in_file()
        print("***Finished fix similar category process")
    def dupe_seeker():
        print("***Started dupe seeking process")
        CategorizeFile.dupe_seeker()
        print("***Finished dupe seeking process")

    def push_monthly_look():
        print("***Started generating and pushing monthly look.")
        gsh = gs_handler.GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
        gs_handler.push_monthly_look(gsh)
        print("***Finished generating and pushing monthly look.")

    app = QtWidgets.QApplication(sys.argv)
    MainWindow = QtWidgets.QMainWindow()
    uic.loadUi('main.ui', MainWindow)

    deleteOldButton = MainWindow.findChild(QtWidgets.QPushButton, 'deleteOldFiles')
    deleteOldButton.clicked.connect(delete_old_files)

    holdingsGrab = MainWindow.findChild(QtWidgets.QPushButton, 'HoldingsGrab')
    holdingsGrab.clicked.connect(grabHoldings)


    processHoldingsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'HoldingsProcess')
    processHoldingsBtn.clicked.connect(processHoldings)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'HoldingsCompile')
    processTransactionsBtn.clicked.connect(compileHoldings)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'HoldingsPush')
    processTransactionsBtn.clicked.connect(pushHoldings)

    transactionGrab = MainWindow.findChild(QtWidgets.QPushButton, 'TransactionsGrab')
    transactionGrab.clicked.connect(grabTransactions)

    # transactionsDate = MainWindow.findChild(QtWidgets.QLineEdit, 'startDate')

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'TransactionsProcess')
    processTransactionsBtn.clicked.connect(processTransactions)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'TransactionsCompile')
    processTransactionsBtn.clicked.connect(compileTransactions)

    processTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'TransactionsPush')
    processTransactionsBtn.clicked.connect(pushTransactions)

    categorizeTransactionsBtn = MainWindow.findChild(QtWidgets.QPushButton, 'categorizeTransactions')
    categorizeTransactionsBtn.clicked.connect(categorizeTransactions)

    check_sync_btn = MainWindow.findChild(QtWidgets.QPushButton, 'syncCheck')
    check_sync_btn.clicked.connect(checkSync)

    pull_data_btn = MainWindow.findChild(QtWidgets.QPushButton, 'pullData')
    pull_data_btn.clicked.connect(pullData)

    push_data_btn = MainWindow.findChild(QtWidgets.QPushButton, 'pushData')
    push_data_btn.clicked.connect(pushData)

    fix_null_btn = MainWindow.findChild(QtWidgets.QPushButton, 'fixNullCategory')
    fix_null_btn.clicked.connect(fix_null_cate)

    fix_similar_btn = MainWindow.findChild(QtWidgets.QPushButton, 'fix_similar_categories')
    fix_similar_btn.clicked.connect(fix_similar_categories)

    dupe_seeker_btn = MainWindow.findChild(QtWidgets.QPushButton, 'dupe_seeker')
    dupe_seeker_btn.clicked.connect(dupe_seeker)

    monthly_look_btn = MainWindow.findChild(QtWidgets.QPushButton, 'MonthlyLookPush')
    monthly_look_btn.clicked.connect(push_monthly_look)

    MainWindow.show()
    sys.exit(app.exec())


def test():
    print("Try")


if __name__ == "__main__":
    # compile_all()
    ui()
