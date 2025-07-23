import datetime
import glob
import re
import os
import pandas as pd
import pandas.errors
import config


class Compiler:
    def __init__(self, path_to_main):
        self.main_file = path_to_main
        self.new_df = pd.DataFrame()  # Initialize new_df
        self.added_transactions = pd.DataFrame()  # To track what's new
        try:
            self.main_df = pd.read_csv(self.main_file)
        except (FileNotFoundError, pandas.errors.EmptyDataError):
            self.main_df = pd.DataFrame()
            self.main_df.to_csv(self.main_file, index=False)

    def __compile_new__(self, path_to_files, suffix):
        self.suffix = suffix

        def standardize_date_format(date_str):
            # Replace all delimiters with a uniform delimiter
            date_str = re.sub(r'[-/.]', '-', date_str)
            return date_str

        file_list = glob.glob(path_to_files + '*.csv')
        new_df_list = []
        for file in file_list:
            df = pd.read_csv(file)
            new_df_list.append(df)
        if not new_df_list:  # Handle case with no new files
            print("No new files to compile.")
            self.new_df = pd.DataFrame()
            return

        self.new_df = pd.concat(new_df_list, ignore_index=True)
        self.new_df['תאריך'] = self.new_df['תאריך'].apply(standardize_date_format)
        self.new_df['תאריך'] = pd.to_datetime(self.new_df['תאריך'], dayfirst=True, errors='coerce', format='mixed')
        self.new_df['תאריך'] = self.new_df['תאריך'].dt.date
        self.new_df.sort_values(by='תאריך', inplace=True)
        self.new_df.reset_index(drop=True, inplace=True)
        self.new_df['תאריך עדכון'] = datetime.date.today()

    def compile_to_main(self):
        if self.new_df.empty:
            print("No new transactions to process.")
            return

        try:
            fingerprint_df = pd.read_csv(config.fingerprint_db_file)
            existing_fingerprints = set(fingerprint_df['fingerprint'])
            print(f"Loaded {len(existing_fingerprints)} existing fingerprints.")
        except FileNotFoundError:
            existing_fingerprints = set()
            print("Fingerprint database not found. Assuming all transactions are new.")

        original_count = len(self.new_df)
        self.new_df = self.new_df[~self.new_df['fingerprint'].isin(existing_fingerprints)]
        filtered_count = len(self.new_df)
        print(f"Removed {original_count - filtered_count} transactions found in fingerprint database.")
        self.added_transactions = self.new_df.copy()
        concat_df = pd.concat([self.main_df, self.new_df], ignore_index=True)
        if 'מזהה עסקה' in concat_df.columns:
            concat_df.drop_duplicates(subset=['fingerprint'], ignore_index=True, inplace=True, keep='first')
            concat_df.drop_duplicates(subset=['מזהה עסקה'], ignore_index=True, inplace=True, keep='first')
        elif 'תאריך' in concat_df.columns and 'מזהה עסקה' not in concat_df.columns:
            concat_df['תאריך'] = pd.to_datetime(concat_df['תאריך'], dayfirst=True, format='mixed')
            concat_df.drop_duplicates(subset=['תאריך'], ignore_index=True, inplace=True, keep='first')
            concat_df.fillna(value=0.0, inplace=True)
        self.main_df = concat_df
        self.main_df['תאריך'] = pd.to_datetime(self.main_df['תאריך'], dayfirst=True, errors='coerce', format='mixed')
        self.main_df.sort_values(by='תאריך', inplace=True)
        self.main_df.reset_index(drop=True, inplace=True)
        self.main_df['תאריך'] = self.main_df['תאריך'].dt.date

    def update_fingerprint_db(self):
        """Appends newly added transactions to the fingerprint database."""
        if self.added_transactions.empty:
            print("No new transactions were added, fingerprint database is up-to-date.")
            return

        new_fingerprints_df = self.added_transactions[['fingerprint', 'מזהה עסקה']].copy()

        db_dir = os.path.dirname(config.fingerprint_db_file)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)

        try:
            fingerprint_df = pd.read_csv(config.fingerprint_db_file)
            updated_df = pd.concat([fingerprint_df, new_fingerprints_df], ignore_index=True)
        except FileNotFoundError:
            updated_df = new_fingerprints_df

        updated_df.drop_duplicates(subset=['fingerprint'], keep='first', inplace=True)
        updated_df.to_csv(config.fingerprint_db_file, index=False)
        print(f"Updated fingerprint database with {len(new_fingerprints_df)} new entries.")

    def save_new(self):
        today_date = f"{datetime.datetime.now().date()} {datetime.datetime.now().hour} {datetime.datetime.now().minute}"
        self.new_df.to_csv(f"{self.main_file.split('.')[0]}_{today_date}.csv", index=False)

        return f"{self.main_file.split('.')[0]}_{today_date}_{self.suffix}.csv"

    def save_main(self):
        self.main_df.to_csv(self.main_file, index=False)
        return self.main_file

    def save_all(self):
        return self.save_main(), self.save_new()


if __name__ == "__main__":
    c = Compiler(config.compiled_file)
    c.__compile_new__(config.cleaned_dir, suffix='credit')
    c.compile_to_main()
    c.save_all()
    c.update_fingerprint_db()