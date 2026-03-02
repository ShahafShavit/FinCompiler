import datetime
import glob
import re
import os
import pandas as pd
import pandas.errors
import config


def update_category_in_fingerprint_db(fingerprint, category):
    """
    Updates the category for a given fingerprint in the central database.
    This can be called from the categorizer after a category is assigned.
    """
    try:
        db_path = config.fingerprint_db_file
        if not os.path.exists(db_path):
            # This can happen if the categorizer runs before any compilation created the DB.
            return

        db_df = pd.read_csv(db_path)
        if 'category' not in db_df.columns:
            db_df['category'] = ''

        db_df['category'] = db_df['category'].astype(object).fillna('')

        # Find the row with the given fingerprint and update its category
        db_df.loc[db_df['fingerprint'] == fingerprint, 'category'] = category
        db_df.to_csv(db_path, index=False)
    except Exception as e:
        print(f"Error updating category in fingerprint DB: {e}")


class Compiler:
    def __init__(self, path_to_main):
        self.main_file = path_to_main
        self.new_df = pd.DataFrame()
        self.added_transactions = pd.DataFrame()
        try:
            self.main_df = pd.read_csv(self.main_file)
        except (FileNotFoundError, pandas.errors.EmptyDataError):
            self.main_df = pd.DataFrame()
            self.main_df.to_csv(self.main_file, index=False)

    def __compile_new__(self, path_to_files, suffix):
        self.suffix = suffix

        def standardize_date_format(date_str):
            date_str = str(date_str)
            return re.sub(r'[-/.]', '-', date_str)

        file_list = glob.glob(os.path.join(path_to_files, '*.csv'))
        new_df_list = []
        for file in file_list:
            df = pd.read_csv(file)
            new_df_list.append(df)
        if not new_df_list:
            print("No new files to compile.")
            self.new_df = pd.DataFrame()
            return

        self.new_df = pd.concat(new_df_list, ignore_index=True)
        self.new_df['תאריך'] = self.new_df['תאריך'].apply(standardize_date_format)
        self.new_df['תאריך'] = pd.to_datetime(self.new_df['תאריך'], dayfirst=True, errors='coerce', format='mixed')
        self.new_df['תאריך'] = self.new_df['תאריך'].dt.date
        self.new_df.sort_values(by='תאריך', inplace=True)
        self.new_df.reset_index(drop=True, inplace=True)
        if 'מזהה עסקה' in self.new_df.columns:
            self.new_df['תאריך עדכון'] = datetime.date.today()

    def compile_to_main(self):

        if self.new_df.empty:
            print("Nothing to process.")
            return
        if 'מזהה עסקה' in self.new_df.columns:  # transactions branch
            original_fingerprints = set()
            if not self.main_df.empty and 'fingerprint' in self.main_df.columns:
                original_fingerprints = set(self.main_df['fingerprint'].dropna())

            # 1. Combine old and new data
            concat_df = pd.concat([self.main_df, self.new_df], ignore_index=True)

            # Drop rows where fingerprint could not be generated
            # concat_df.dropna(subset=['fingerprint'], inplace=True)

            if 'קטגוריה' not in concat_df.columns:
                concat_df['קטגוריה'] = ''
            concat_df['קטגוריה'] = concat_df['קטגוריה'].astype(object).fillna('')

            # 2. Prioritize categorized rows for de-duplication
            concat_df['sort_key'] = concat_df['קטגוריה'].apply(lambda x: 0 if x != '' else 1)

            # 3. Sort to bring categorized rows to the top of each duplicate group
            concat_df.sort_values(by=['fingerprint', 'sort_key'], ascending=[True, True], inplace=True)

            # 4. Drop duplicates, keeping the first entry (which is now the prioritized one)
            self.main_df = concat_df.drop_duplicates(subset=['fingerprint'], keep='first').copy()

            # 5. Clean up the temporary sort key
            self.main_df.drop(columns=['sort_key'], inplace=True)

            # Identify what was actually added to update the fingerprint DB correctly
            current_fingerprints = set(self.main_df['fingerprint'])
            newly_added_fingerprints = current_fingerprints - original_fingerprints
            self.added_transactions = self.main_df[self.main_df['fingerprint'].isin(newly_added_fingerprints)].copy()

            print(f"After intelligent de-duplication, {len(self.added_transactions)} new transactions were added.")

            self.main_df['תאריך'] = pd.to_datetime(self.main_df['תאריך'], dayfirst=True, errors='coerce',
                                                   format='mixed')
            self.main_df.sort_values(by='תאריך', inplace=True)
            self.main_df.reset_index(drop=True, inplace=True)
            self.main_df['תאריך'] = self.main_df['תאריך'].dt.date
        else:  # In Holdings Mode
            concat_df = pd.concat([self.main_df, self.new_df], ignore_index=True)
            concat_df['תאריך'] = pd.to_datetime(concat_df['תאריך'], dayfirst=True, format='mixed')
            concat_df.drop_duplicates(subset=['תאריך'], ignore_index=True, inplace=True, keep='first')
            concat_df.fillna(value=0.0, inplace=True)
            self.main_df = concat_df
            self.main_df['תאריך'] = pd.to_datetime(self.main_df['תאריך'], dayfirst=True, errors='coerce',
                                                   format='mixed')
            self.main_df.sort_values(by='תאריך', inplace=True)
            self.main_df.reset_index(drop=True, inplace=True)
            self.main_df['תאריך'] = self.main_df['תאריך'].dt.date

    def update_fingerprint_db(self):
        if self.added_transactions.empty:
            print("No new transactions were added, fingerprint database is up-to-date.")
            return

        new_fingerprints_df = self.added_transactions[['fingerprint', 'מזהה עסקה']].copy()
        new_fingerprints_df['category'] = ''

        db_path = config.fingerprint_db_file
        db_dir = os.path.dirname(db_path)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)

        try:
            fingerprint_df = pd.read_csv(db_path)
            if 'category' not in fingerprint_df.columns:
                fingerprint_df['category'] = ''
            updated_df = pd.concat([fingerprint_df, new_fingerprints_df], ignore_index=True)
        except FileNotFoundError:
            updated_df = new_fingerprints_df

        updated_df['category'] = updated_df['category'].astype(object).fillna('')
        updated_df.sort_values(by=['fingerprint', 'category'], ascending=[True, False], inplace=True)
        updated_df.drop_duplicates(subset=['fingerprint'], keep='first', inplace=True)

        updated_df.to_csv(db_path, index=False)
        print(f"Updated fingerprint database with {len(new_fingerprints_df)} new entries.")

    def save_new(self):
        today_date = f"{datetime.datetime.now().date()}_{datetime.datetime.now().hour}-{datetime.datetime.now().minute}"
        output_path = os.path.join(config.compiled_dir, f"new_{today_date}_{self.suffix}.csv")
        self.new_df.to_csv(output_path, index=False)
        return output_path

    def save_main(self):
        self.main_df.to_csv(self.main_file, index=False)
        return self.main_file

    def save_all(self):
        return self.save_main(), self.save_new()
