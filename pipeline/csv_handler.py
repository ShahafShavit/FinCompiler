import glob
import hashlib
import logging
import os
import re

import pandas as pd
from tabulate import tabulate

import config

log = logging.getLogger(__name__)


def generate_transaction_fingerprint(row):
    """Creates a robust fingerprint for de-duplication purposes based on the transaction's essence."""
    try:
        normalized_date = pd.to_datetime(row['תאריך'], dayfirst=True, format='mixed').strftime('%Y-%m-%d')
        amount = row['בחובה'] if row['בחובה'] != 0 else row['בזכות']
        normalized_amount = f"{float(amount):.2f}"
        business_name = str(row['מקור עסקה']).lower().strip()
        business_name = re.sub(r'[^a-z0-9\u0590-\u05ff]', '', business_name)
        extract_data = ''
        try:
            extract_data += (str(row['פירוט נוסף'])).strip().lower()
        except KeyError:
            extract_data += "nan"
        try:
            extract_data += str(row['תאור מורחב']).strip().lower()
        except KeyError:
            extract_data += 'nan'

        extra_data = re.sub(r'[^a-z0-9\u0590-\u05ff]', '', extract_data)
        fingerprint_key = f"{normalized_date}:{normalized_amount}:{business_name}:{extra_data}"
        log.debug("Fingerprint key: %s", fingerprint_key)
        return fingerprint_key
        # return hashlib.sha256(fingerprint_key.encode()).hexdigest()

    except (ValueError, TypeError):
        return None
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
        self.file_df['fingerprint'] = self.file_df.apply(generate_transaction_fingerprint, axis=1)
        self.file_df.dropna(subset=['fingerprint'], inplace=True)
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
        SQLite/categorizer paths key off ``fingerprint`` via ``categorization.categorizer.stable_transaction_key``.
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

    def to_csv(self, output_clean_dir=None):
        out_dir = output_clean_dir or config.cleaned_dir
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(
            out_dir,
            os.path.basename(self.file_path).split(".")[0],
        ) + ".csv"
        self.file_df.to_csv(out, index=False)
        log.info("TransactionFile: wrote cleaned CSV %s rows=%s", out, len(self.file_df))


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
        self.file_df = self.file_df.apply(pd.to_numeric, errors='coerce')
        merged_row = self.file_df.max()
        self.file_df = pd.DataFrame([merged_row])

    def to_csv(self, output_clean_dir=None):
        out_dir = output_clean_dir or config.cleaned_dir
        os.makedirs(out_dir, exist_ok=True)
        filename = os.path.basename(self.file_path)
        date_pattern = re.compile(r'(\d{1,2}-\d{1,2}-\d{4})')

        match = date_pattern.search(filename)
        if match:
            date_str = match.group(1)
            formatted_date = date_str.replace('_', '-')
            self.file_df['תאריך'] = formatted_date
        else:
            formatted_date = "00-00-00 00:00:00"
        out_path = os.path.join(out_dir, f"Holdings_{formatted_date}.csv")
        self.file_df.to_csv(out_path, index=False)
        log.info("HoldingsFile: wrote %s rows=%s", out_path, len(self.file_df))


if __name__ == "__main__":
    file_list = glob.glob(os.path.join(config.raw_dir, '*.xls*'))
    for f in file_list:
        if 'יתרות' not in f:
            f = TransactionFile(f)
            f.drop_columns(
                ['סכום עסקה', 'מטבע חיוב', 'מטבע עסקה מקורי', 'מטבע מקור', 'מטבע לחיוב', 'סכום עסקה מקורי', 'סכום מקורי'
                    , 'מספר שובר', 'תאריך חיוב', 'שער המרה ממטבע מקור/התחשבנות לש"ח', 'אופן ביצוע ההעסקה', 'הערות',
                 'סוג עסקה', 'תאריך ערך', 'הערה', 'אסמכתא', 'קטגוריה', 'היתרה בש"ח'])
            f.drop_by_column_and_value('מקור עסקה', 'כרטיס דביט')
            f.drop_by_column_and_value('מקור עסקה', 'קניה-אינטרנט')
            f.drop_by_column_and_value('מקור עסקה', 'ישראכרט בע"מ-י')
            f.drop_by_column_and_value('מקור עסקה', 'מקס איט פיננ-י')
            f.drop_by_column_and_value('מקור עסקה', 'פקדון אינטר700')
            f.drop_by_column_and_value('מקור עסקה', 'פקדון אינטרנט')
            f.drop_by_column_and_value('מקור עסקה', 'פקדון אינטרנט')
            f.drop_by_column_and_value('מקור עסקה', 'שינוי בנ"ע')
            f.drop_by_column_and_value('מקור עסקה', 'נ"ע בבורסה')
            f.to_csv()
        else:
            hf = HoldingsFile(f)
            rename_map = {
                'נכון לתאריך': 'תאריך',
            }
            hf.unify_columns(rename_map)
            # print(hf.file_df.to_markdown())
            # print(tabulate(hf.file_df,headers='keys',))
            hf.to_csv()