import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import gspread
import pandas
import pandas as pd
import tabulate
from oauth2client.service_account import ServiceAccountCredentials
import config
from pipeline import compiler as compile_handler
import gspread.utils
from googleapiclient.discovery import build
pd.set_option('display.precision', 2)  # Sets display precision to two decimal places

tabulate.PRESERVE_WHITESPACE = True
pd.options.display.max_columns = None
pd.options.display.width = None
pd.options.styler.latex.multicol_align = 'c'
current_year = str(int(datetime.now().year))


def _safe_to_numeric(num):
    try:
        return pd.to_numeric(num)
    except Exception:
        return num


def _read_local_csv_for_sync(path: str) -> tuple[pd.DataFrame, str]:
    try:
        df = pd.read_csv(path)
    except FileNotFoundError:
        return pd.DataFrame(), "missing"
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), "empty"
    return df, "ok"


def _normalize_for_sync_compare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.apply(_safe_to_numeric)
    out.replace(np.nan, "", inplace=True)
    return out


class GoogleSheetsHandler:
    def __init__(self, credentials_file, sheet_id):
        self.credentials = None
        self.credentials_file = credentials_file
        self.sheet_id = sheet_id
        self.client = self.authenticate_google_sheets()
        self.spreadsheet = self.client.open_by_key(self.sheet_id)

    def authenticate_google_sheets(self):
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        self.credentials = ServiceAccountCredentials.from_json_keyfile_name(self.credentials_file, scope)
        return gspread.authorize(self.credentials)

    def update_sheet(self, dataframe, sheet_name, special=None):
        if special is None:
            special = []

        def safe_convert_to_datetime(date):
            try:
                return pd.to_datetime(date, errors='raise', dayfirst=True, format='mixed')
            except Exception:
                pass
            return date

        print(f"Started pushing {sheet_name}...")
        dataframe.replace([float('inf'), float('-inf')], float('nan'), inplace=True)  # Replace infinities with NaN
        dataframe.replace(np.nan, "", inplace=True)

        try:
            sheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = self.spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=100)

        sheet.clear()
        sheet.update([dataframe.columns.values.tolist()] + dataframe.values.tolist())

        print(f"Finished pushing {sheet.title}.")

    def get_sheet(self, sheet_name, range: str, rows = 1000):
        try:
            sheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"Worksheet named {sheet_name} is missing")
            sheet = self.spreadsheet.add_worksheet(title=sheet_name, rows=rows, cols=100)
        data = sheet.get(range)
        df = pd.DataFrame(data=data[1:], columns=data[0])
        return df

    def fetch_worksheet_as_dataframe(self, sheet_name: str, cell_range: str = "A1:ZZ") -> pd.DataFrame:
        """Load an existing worksheet into a DataFrame. Does not create missing sheets."""
        try:
            sheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound as e:
            raise FileNotFoundError(f"Worksheet {sheet_name!r} not found in spreadsheet") from e
        data = sheet.get(cell_range)
        if not data or len(data) < 2:
            return pd.DataFrame()
        return pd.DataFrame(data=data[1:], columns=data[0])


class GSLink:
    def __init__(self, gs_handler: GoogleSheetsHandler):
        self.handler = gs_handler

    def _sheet_pair_report(
        self,
        sheet_name: str,
        local_path: str,
        *,
        cell_range: str,
    ) -> tuple[dict, pd.DataFrame | None]:
        """
        Compare one local CSV to one worksheet. Returns a JSON-friendly report dict and an optional
        diff frame (non-empty only when there are value-level differences with aligned shape).
        """
        out: dict = {"sheet": sheet_name, "local_path": local_path}
        try:
            cloud_df = self.handler.fetch_worksheet_as_dataframe(sheet_name, cell_range)
        except FileNotFoundError as e:
            out["ok"] = False
            out["cloud_error"] = str(e)
            out["structural_issues"] = [str(e)]
            return out, None

        local_df, local_status = _read_local_csv_for_sync(local_path)
        out["local_status"] = local_status
        out["local_row_count"] = int(len(local_df))
        out["cloud_row_count"] = int(len(cloud_df))

        structural: list[str] = []

        if bool(local_df.columns.duplicated().any()):
            dups = [str(x) for x in local_df.columns[local_df.columns.duplicated()].tolist()]
            out["duplicate_local_columns"] = dups
            structural.append("duplicate column names in local CSV")

        if local_status == "missing":
            structural.append("local CSV is missing")
            out["ok"] = False
            out["structural_issues"] = structural
            return out, None

        if local_status == "empty" and cloud_df.empty:
            out["ok"] = True
            out["diff_row_count"] = 0
            return out, None

        if local_status == "empty" and not cloud_df.empty:
            structural.append("local CSV is empty but the cloud sheet has rows")
            out["ok"] = False
            out["structural_issues"] = structural
            return out, None

        if local_status == "ok" and local_df.empty and cloud_df.empty:
            out["ok"] = True
            out["diff_row_count"] = 0
            return out, None

        if not cloud_df.empty and not local_df.empty:
            lc = list(local_df.columns)
            cc = list(cloud_df.columns)
            if set(lc) != set(cc):
                only_l = sorted(set(lc) - set(cc), key=str)
                only_c = sorted(set(cc) - set(lc), key=str)
                out["columns_only_local"] = only_l
                out["columns_only_cloud"] = only_c
                structural.append("column headers do not match between local CSV and cloud sheet")
        elif cloud_df.empty and not local_df.empty:
            structural.append("cloud sheet has no data while local CSV is non-empty")

        if structural:
            out["ok"] = False
            out["structural_issues"] = structural
            return out, None

        column_order = list(local_df.columns)
        local_n = _normalize_for_sync_compare(local_df.copy())
        cloud_n = _normalize_for_sync_compare(cloud_df[column_order].copy())

        if local_n.shape != cloud_n.shape:
            out["ok"] = False
            out["structural_issues"] = [
                f"row/column shape mismatch after header align: local {local_n.shape} vs cloud {cloud_n.shape}"
            ]
            return out, None

        try:
            diff = local_n.compare(cloud_n).dropna(axis=0, how="all")
        except (ValueError, TypeError) as e:
            out["ok"] = False
            out["structural_issues"] = [f"could not diff values: {e}"]
            return out, None

        n_diff = int(len(diff))
        out["diff_row_count"] = n_diff
        if n_diff == 0:
            out["ok"] = True
            return out, None

        out["ok"] = False
        sample_rows = sorted({int(i) + 2 for i in diff.index.tolist()})[:40]
        out["sample_sheet_rows_with_differences"] = sample_rows
        return out, diff

    def analyze_sync(
        self,
        sheets: list,
        compiled_files: list,
        *,
        cell_range: str = "A1:ZZ",
        for_cli: bool = False,
    ) -> dict:
        """
        Structured comparison of local CSVs to cloud worksheets (same semantics as :meth:`sync_check`).

        ``for_cli`` reproduces console tables for the first worksheet that has value differences
        (matches the historical desktop behaviour).
        """
        sheets_out: list[dict] = []
        internal_diffs: list[pd.DataFrame | None] = []
        issues: list[str] = []
        all_ok = True

        for sheet_name, path in zip(sheets, compiled_files):
            rep, diff_df = self._sheet_pair_report(sheet_name, path, cell_range=cell_range)
            internal_diffs.append(diff_df)
            clean = {k: v for k, v in rep.items() if not str(k).startswith("_")}
            sheets_out.append(clean)
            if not rep.get("ok", False):
                all_ok = False
                for s in rep.get("structural_issues") or []:
                    issues.append(f"{sheet_name}: {s}")
                if rep.get("diff_row_count"):
                    issues.append(
                        f"{sheet_name}: {rep['diff_row_count']} row(s) differ in cell values "
                        f"(see sample_sheet_rows_with_differences)"
                    )

        if for_cli:
            if all_ok:
                print("All sheets are up to date with local data.")
            else:
                printed = False
                for rep, diff_df in zip(sheets_out, internal_diffs):
                    if diff_df is None or diff_df.empty:
                        continue
                    sheet = rep.get("sheet", "?")
                    indices_df = pd.DataFrame(
                        data={"Cloud Row": diff_df.index + 2, "Local Row": diff_df.index}
                    )
                    print(f"Total of {len(diff_df)} mismatched row(s) found in: {sheet}")
                    diff_tbl = diff_df.rename(columns={"self": "local", "other": "cloud"})
                    print(
                        tabulate.tabulate(
                            indices_df, headers="keys", showindex=False, tablefmt="simple", numalign="center"
                        )
                    )
                    diff_tbl = diff_tbl.copy()
                    diff_tbl.index = diff_tbl.index + 2
                    print(
                        tabulate.tabulate(
                            diff_tbl, headers="keys", showindex=False, tablefmt="simple", stralign="center"
                        )
                    )
                    printed = True
                    break
                if not printed and issues:
                    for line in issues:
                        print(line)

        return {"ok": all_ok, "issues": issues, "sheets": sheets_out}

    def push_local_csvs_to_cloud(
        self,
        sheets: list,
        compiled_files: list,
        special_columns=None,
        *,
        cell_range: str = "A1:ZZ",
        force: bool = False,
    ) -> tuple[bool, str, dict | None]:
        """
        Push local CSVs to the corresponding worksheets. When ``force`` is false, requires a clean
        :meth:`analyze_sync` first (same gate as :meth:`update_cloud`).

        When blocked, returns the structured :meth:`analyze_sync` report as the third element.
        """
        if special_columns is None:
            special_columns = []
        if not force:
            rep = self.analyze_sync(sheets, compiled_files, cell_range=cell_range, for_cli=False)
            if not rep["ok"]:
                return (
                    False,
                    "Sync check failed: push blocked until local and cloud match (or use force). "
                    "Preview in the web UI shows details.",
                    rep,
                )

        skipped: list[str] = []
        for sheet_name, compiled_file in zip(sheets, compiled_files):
            try:
                df = pd.read_csv(compiled_file)
            except FileNotFoundError:
                skipped.append(compiled_file)
                print(f"Missing file: {compiled_file}, skipping push to cloud.")
                continue
            self.handler.update_sheet(df, sheet_name, special_columns)

        if skipped:
            return True, f"Pushed sheets; skipped missing file(s): {', '.join(skipped)}", None
        return True, "Pushed all local CSVs to Google Sheets.", None

    def update_cloud(self, sheets, compiled_files, special_columns=None, *, confirm: bool = True):
        if special_columns is None:
            special_columns = []
        self.sync_check(sheets, compiled_files)
        if confirm:
            input("Are you sure you want to PUSH data the cloud?")
        for sheet_name, compiled_file in zip(sheets, compiled_files):
            try:
                df = pd.read_csv(compiled_file)
                self.handler.update_sheet(df, sheet_name, special_columns)
            except FileNotFoundError:
                print(f"Missing file: {compiled_file}, skipping push to cloud.")

    def sync_check(self, sheets, compiled_files):
        rep = self.analyze_sync(sheets, compiled_files, cell_range="A1:ZZ", for_cli=True)
        return bool(rep["ok"])


# DEPRECATED
# def sheets_push():
#     df_totals, df_holdings = None, None
#     try:
#         df_totals = pd.read_csv(config.compiled_file)
#     except FileNotFoundError as e:
#         print(f"Missing file: {e}")
#     try:
#         df_holdings = pd.read_csv(config.holdings_file)
#     except FileNotFoundError as e:
#         print(f"Missing file: {e}")
#     dfs = {
#         "Totals2": df_totals,
#         "Holdings2": df_holdings
#     }
#     GoogleAPI = GoogleSheetsHandler(<path from Settings/providers.json>, <worksheet id>)
#     new_df = GoogleAPI.get_sheet("Holdings2", "A1:M")
#     cols = new_df.columns.drop('תאריך')
#     new_df[cols] = new_df[cols].apply(pd.to_numeric)
#     new_df.to_csv(config.holdings_file, index=False)
#
#     # for sheet_name, df in dfs.items():
#     #     if df is not None:
#     #         GoogleAPI.write_sheet(df, sheet_name)
#     #         print(f"Written sheet {sheet_name} to Google Sheets")
#


def push_monthly_look(gsh):
    print("Pushing updated monthly look...")

    if os.path.isfile(config.ledger_db_file):
        from ledger import load_transactions_dataframe_from_ledger

        df = load_transactions_dataframe_from_ledger(config.ledger_db_file)
    else:
        df = pd.read_csv(config.compiled_file)
    df["תאריך"] = compile_handler.parse_post_ingest_date_column(df["תאריך"])
    df['Month-Year'] = df['תאריך'].dt.to_period('M')

    # Creating a new column to distinguish between expenses and income
    df['Type'] = df.apply(lambda row: 'Income' if row['בזכות'] > 0 else 'Expense', axis=1)
    df['Amount'] = df.apply(lambda row: row['בזכות'] if row['בזכות'] > 0 else row['בחובה'], axis=1)

    # Grouping by Month-Year, Category, and Type, and summing the Amounts
    grouped = df.groupby(['Month-Year', 'קטגוריה', 'Type'])['Amount'].sum().reset_index()

    # Create a complete range of months from January to December for the current year
    current_year = pd.Timestamp.today().year
    all_periods = pd.period_range(start=f'{current_year}-01', end=f'{current_year}-12', freq='M')

    # Create a list of all categories
    all_categories = df['קטגוריה'].unique()

    # Ensure both 'Expense' and 'Income' exist for every category and month-year
    all_combinations = pd.MultiIndex.from_product([all_categories, ['Expense', 'Income']],
                                                  names=['קטגוריה', 'Type'])

    # Pivot the table so that 'Type' (Expense/Income) becomes sub-rows instead of sub-columns
    pivoted = grouped.pivot_table(index=['קטגוריה', 'Type'], columns=['Month-Year'], values='Amount', fill_value=0)

    # Reindex to ensure all combinations of categories, types, and months are present, filling missing values with 0
    pivoted = pivoted.reindex(all_combinations, fill_value=0)
    new_columns = pivoted.columns.union(all_periods)
    pivoted = pivoted.reindex(columns=new_columns, fill_value=0)
    # Convert the pd.Period index columns to strings (for months)
    pivoted.columns = pivoted.columns.astype(str)
    pivoted: pandas.DataFrame

    # Flatten and format the DataFrame
    def flatten_dataframe(df):
        # Flatten the MultiIndex columns by ensuring any Period objects are converted to strings
        df.columns = [' '.join(map(str, col)).strip() if isinstance(col, tuple) else str(col) for col in
                      df.columns.values]

        # Ensure the index is included in the DataFrame for upload
        df.reset_index(inplace=True)

        return df

    flattened_df = flatten_dataframe(pivoted)

    # Reformat headers for Google Sheets
    headers = [list(flattened_df.columns)]

    # Data for Google Sheets
    data = [flattened_df.columns.values.tolist()] + flattened_df.values.tolist()  # Convert all data to string format

    spreadsheet = gsh.spreadsheet

    # Function to update the Google Sheet with merged cells
    def update_sheet_with_headers(spreadsheet, headers, data, sheet_name):
        try:
            sheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=100)

        spreadsheet_meta = spreadsheet.fetch_sheet_metadata()
        worksheet_meta = next(
            sheet for sheet in spreadsheet_meta['sheets'] if sheet['properties']['title'] == sheet_name)

        merge_ranges = worksheet_meta.get('merges', [])
        sheet: gspread.worksheet.Worksheet = sheet
        sheet.batch_clear(['A1:O60'])

        # Update headers and data, use the updated argument order for gspread's update function
        sheet.update(data, 'A1')

        merged_indexes = [(entry['startRowIndex'], entry['endRowIndex']) for entry in merge_ranges]

        for row in range(2, (len(data) - 1), 2):

            if (row - 1, row + 1) in merged_indexes:
                continue
            sheet.merge_cells(f'A{row}:A{row + 1}')
            print(f"Merging row {row / 2} of {(len(data) - 1) / 2}. sleeping for 2s")
            # Apply center alignment to the merged cells
            time.sleep(2)
        formats = [
            {
                "range": "C2:O100",
                "format": {
                    "textFormat": {
                        "bold": False
                    },
                    "numberFormat": {
                        "type": "CURRENCY",
                        "pattern": "[$₪]#,##0.00"  # ILS currency format
                    }                }
            },
            {
                "range": "A1:O100",
                "format":
                    {
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE"
                    }

            }
        ]

        sheet.batch_format(formats)
        # print("Initiated flood-protection cooldown.")
        # for i in range(30):
        #     time.sleep(1)
        #     print(f"{i} -> 30s")
        # print("Finished flood-protection cooldown.")
        #

    update_sheet_with_headers(spreadsheet, headers, data, 'מבט חודשי')
    print("Pushed updated monthly look.")
if __name__ == "__main__":
    from providers import google_api_user_path, google_worksheet_id

    gsh = GoogleSheetsHandler(google_api_user_path(), google_worksheet_id())

    gslink = GSLink(gsh)
    # gslink.update_cloud(['Holdings', 'Totals'], [config.holdings_file, config.compiled_file])
    # gslink.sync_check(['Holdings2', 'Totals2'], [config.holdings_file, config.compiled_file])
    # print(tabulate.tabulate(pivoted, headers='keys', showindex=True, tablefmt='plain'))
    # gsh.update_sheet(pivoted, "Monthly Look")
    push_monthly_look(gsh)
