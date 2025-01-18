import datetime
import glob
import re

import pandas as pd
import pandas.errors

import config


class Compiler:
    def __init__(self, path_to_main):
        self.main_file = path_to_main
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
            # if len(df.columns) > 4:
            new_df_list.append(df)
            # else:
            #     pass
            # raise FileExistsError("Found Holdings file while processing transactions.")

        self.new_df = pd.concat(new_df_list, ignore_index=True)
        self.new_df['תאריך'] = self.new_df['תאריך'].apply(standardize_date_format)
        self.new_df['תאריך'] = pd.to_datetime(self.new_df['תאריך'], dayfirst=True, errors='coerce', format='mixed')
        self.new_df['תאריך'] = self.new_df['תאריך'].dt.date
        self.new_df.sort_values(by='תאריך', inplace=True)
        self.new_df.reset_index(drop=True, inplace=True)
        if 'מזהה עסקה' in self.new_df.columns and 'מזהה עסקה' in self.main_df.columns:
            merge = pd.merge(self.new_df, self.main_df, on='מזהה עסקה')
            num_duplicates = merge.shape[0]
            print(f"Dupe remover: Identified {num_duplicates} dupes crossing main compile and new compile")
            self.new_df = self.new_df[~self.new_df['מזהה עסקה'].isin(self.main_df['מזהה עסקה'])]

    def compile_to_main(self):
        concat_df = pd.concat([self.main_df, self.new_df], ignore_index=True)
        if 'מזהה עסקה' in concat_df.columns:
            concat_df.drop_duplicates(subset=['מזהה עסקה'], ignore_index=True, inplace=True, keep='first')
        elif 'תאריך' in concat_df.columns and 'מזהה עסקה' not in concat_df.columns:
            concat_df['תאריך'] = pd.to_datetime(concat_df['תאריך'],dayfirst=True, format='mixed')
            concat_df.drop_duplicates(subset=['תאריך'], ignore_index=True, inplace=True, keep='first')
            concat_df.fillna(value=0.0,inplace=True)
        self.main_df = concat_df
        self.main_df['תאריך'] = pd.to_datetime(self.main_df['תאריך'], dayfirst=True, errors='coerce', format='mixed')
        self.main_df.sort_values(by='תאריך', inplace=True)
        self.main_df.reset_index(drop=True, inplace=True)
        self.main_df['תאריך'] = self.main_df['תאריך'].dt.date

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
    # holdings_file = glob.glob(config.cleaned_dir + '*Holdings*.csv')
    # if len(holdings_file) == 1:
    #     d = Compiler(config.holdings_file)
    #     d.__compile_new__(config.cleaned_dir, suffix='holdings')
    #     d.compile_to_main()
    #     d.save_all()

