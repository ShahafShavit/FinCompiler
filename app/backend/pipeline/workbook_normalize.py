"""Load and normalize bank transaction / holdings Excel workbooks for the compile pipeline."""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import re

import pandas as pd

import config
from pipeline.fingerprint import generate_transaction_fingerprint

from .compiler import parse_post_ingest_date_column

log = logging.getLogger(__name__)


class TransactionFile:
    def __init__(self, file_path):
        log.info("TransactionFile: loading %s", file_path)
        rename_map = {
            'סכום חיוב': 'בחובה',
            'תאריך עסקה': 'תאריך',
            'תאריך רכישה': 'תאריך',
            'שם בית עסק': 'מקור עסקה',
            'שם בית העסק': 'מקור עסקה',
            'תיאור': 'מקור עסקה',
            '4 ספרות אחרונות של כרטיס האשראי': '4 ספרות',
            'הערות': 'פירוט נוסף'
        }
        self.file_path = file_path
        self.file_df = self.__load_data__()
        self.clean_nan_rows()
        self.unique_identifier()
        self.unify_columns(rename_map)
        self.file_df["fingerprint"] = self.file_df.apply(generate_transaction_fingerprint, axis=1)
        self.file_df.dropna(subset=["fingerprint"], inplace=True)
        log.debug("TransactionFile: after fingerprint rows=%s", len(self.file_df))

    def __load_data__(self):
        def unify_dataframe(df, expected_headers):
            # List to hold all sub-dataframes
            sub_dfs = []
            current_df = None

            def process_current_df(current_df):
                if current_df is not None and not current_df.empty:
                    for idx, row in current_df.iterrows():
                        if any(header in row.values for header in expected_headers):
                            # Set the headers to the values of this row
                            current_df.columns = row
                            # Drop the row containing the headers and all previous rows
                            current_df = current_df.iloc[idx + 1:].reset_index(drop=True)
                            # Remove duplicates and NaN columns
                            current_df = current_df.loc[:, ~current_df.columns.duplicated()]
                            current_df = current_df.dropna(axis=1, how='all')
                            sub_dfs.append(current_df)
                            break

            # Iterate through each row in the DataFrame
            for index, row in df.iterrows():
                if any(header in row.values for header in expected_headers):
                    process_current_df(current_df)
                    current_df = pd.DataFrame(columns=row)
                if current_df is not None:
                    current_df = pd.concat(
                        [current_df, row.to_frame().T], ignore_index=True
                    )

            # Process the last chunk
            process_current_df(current_df)

            # Concatenate all cleaned DataFrames
            if sub_dfs:
                unified_df = pd.concat(sub_dfs, ignore_index=True)
                return unified_df
            else:
                return None

        dfs = pd.read_excel(self.file_path, None)
        log.debug("TransactionFile __load_data__: sheets=%s", list(dfs.keys()))

        file_df = pd.DataFrame()
        for index, df in enumerate(dfs.values()):
            new_df = unify_dataframe(df, ['שם בית עסק', 'שם בית העסק', 'יתרה משוערכת'])
            if new_df is not None:
                new_df.columns.name = None
                file_df = pd.concat([file_df, new_df], ignore_index=True)
            else:
                file_df = df

        return file_df

    def unique_identifier(self):
        """Attach legacy **מזהה עסקה** (SHA-256 of row text) for old CSV workflows only.

        This is **not** the pipeline ``fingerprint`` and is **not** stored on ``ledger_transaction``.
        SQLite/categorizer paths key off ``fingerprint`` (canonical dedupe string).
        """
        def hash_row(row):
            row_string = ''.join(row.values.astype(str))
            return hashlib.sha256(row_string.encode()).hexdigest()

        self.file_df['מזהה עסקה'] = self.file_df.apply(hash_row, axis=1)

    def clean_nan_rows(self):
        df = self.file_df
        df = df[df.isnull().sum(axis=1) < 4]
        self.file_df = df


    def drop_columns(self, column_list):
        current_columns = self.file_df.columns
        columns_to_drop = [col for col in column_list if col in current_columns]
        self.file_df.drop(columns=columns_to_drop, inplace=True)

    def drop_by_column_and_value(self, column, value):
        current_columns = self.file_df.columns
        if column in current_columns:
            self.file_df = self.file_df[self.file_df[column] != value]

    def unify_columns(self, map):
        self.file_df.rename(columns=map, inplace=True)

        def update(row):
            if row['בחובה'] < 0:
                return abs(row['בחובה'])
            else:
                return row['בזכות'] if 'בזכות' in row and pd.notnull(row['בזכות']) else float(0)

        self.file_df['בזכות'] = self.file_df.apply(update, axis=1)
        self.file_df['בחובה'] = self.file_df['בחובה'].apply(lambda x: float(0) if x < 0 else float(x))


def load_transaction_clean_dataframe(
    file_path: str,
    *,
    drop_columns: list[str],
    drop_sources: list[tuple[str, str]],
) -> pd.DataFrame:
    """Normalize one transaction workbook to the same columns as legacy clean CSV (no disk write)."""
    f = TransactionFile(file_path)
    f.drop_columns(drop_columns)
    for col, val in drop_sources:
        f.drop_by_column_and_value(col, val)
    return f.file_df.copy()


class HoldingsFile:
    def __init__(self, file_path):
        log.info("HoldingsFile: loading %s", file_path)
        self.file_path = file_path
        self.file_df = pd.read_excel(file_path)
        self.file_df = self.file_df[self.file_df['סוג פעילות'] != 'כ.א. חוץ בנקאיים']
        self.file_df = self.file_df[self.file_df['סוג פעילות'] != 'סה"כ']
        self.file_df = self.file_df.pivot(index='נכון לתאריך', columns='סוג פעילות', values='יתרה בש"ח')
        self.file_df = self.file_df.groupby('נכון לתאריך').first().reset_index()
        log.debug("HoldingsFile: pivoted shape=%s", self.file_df.shape)

    def drop_columns(self, column_list):
        current_columns = self.file_df.columns
        columns_to_drop = [col for col in column_list if col in current_columns]
        self.file_df.drop(columns=columns_to_drop, inplace=True)

    def unify_columns(self, map):
        self.file_df.rename(columns=map, inplace=True)
        if 'תאריך' in self.file_df.columns:
            self.file_df['תאריך'] = parse_post_ingest_date_column(self.file_df['תאריך'])
        balance_cols = [c for c in self.file_df.columns if c != 'תאריך']
        if balance_cols:
            self.file_df[balance_cols] = self.file_df[balance_cols].apply(pd.to_numeric, errors='coerce')
        merged_row = self.file_df.max(numeric_only=False)
        self.file_df = pd.DataFrame([merged_row])


def load_holdings_unified_wide(
    file_path: str,
    *,
    rename_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Wide holdings row(s) after column rename / numeric merge (same as pipeline clean CSV)."""
    hf = HoldingsFile(file_path)
    rm = rename_map or {"נכון לתאריך": "תאריך"}
    hf.unify_columns(rm)
    return hf.file_df.copy()


if __name__ == "__main__":
    _drop_cols = [
        "סכום עסקה",
        "מטבע חיוב",
        "מטבע עסקה מקורי",
        "מטבע מקור",
        "מטבע לחיוב",
        "סכום עסקה מקורי",
        "סכום מקורי",
        "מספר שובר",
        "תאריך חיוב",
        'שער המרה ממטבע מקור/התחשבנות לש"ח',
        "אופן ביצוע ההעסקה",
        "הערות",
        "סוג עסקה",
        "תאריך ערך",
        "הערה",
        "אסמכתא",
        "קטגוריה",
        'היתרה בש"ח',
    ]
    _drop_pairs = [
        ("מקור עסקה", "כרטיס דביט"),
        ("מקור עסקה", "קניה-אינטרנט"),
        ('מקור עסקה', 'ישראכרט בע"מ-י'),
        ("מקור עסקה", "מקס איט פיננ-י"),
        ("מקור עסקה", "פקדון אינטר700"),
        ("מקור עסקה", "פקדון אינטרנט"),
        ("מקור עסקה", "שינוי בנ\"ע"),
        ("מקור עסקה", 'נ"ע בבורסה'),
    ]
    os.makedirs(config.cleaned_dir, exist_ok=True)
    file_list = glob.glob(os.path.join(config.raw_dir, "*.xls*"))
    for f in file_list:
        if "יתרות" not in f:
            df = load_transaction_clean_dataframe(f, drop_columns=_drop_cols, drop_sources=_drop_pairs)
            stem = os.path.splitext(os.path.basename(f))[0]
            df.to_pickle(os.path.join(config.cleaned_dir, f"{stem}_clean.pkl"))
        else:
            df = load_holdings_unified_wide(f, rename_map={"נכון לתאריך": "תאריך"})
            stem = os.path.splitext(os.path.basename(f))[0]
            df.to_pickle(os.path.join(config.cleaned_dir, f"{stem}_holdings_clean.pkl"))
