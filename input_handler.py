import hashlib
import os
import shutil
import pandas as pd
import config
import glob
import openpyxl


class File:
    def __init__(self, raw_file_path=None):
        if raw_file_path:
            self.raw_file_path = raw_file_path
        else:
            self.__locate_file()

    def __locate_file(self):
        files = glob.glob(os.path.join(config.input_dir, '*.xls*'))
        if len(files) == 0:
            raise Exception("Input folder is empty.")
        self.raw_file_path = files[0]

    def to_xlsx(self):
        if os.path.basename(self.raw_file_path).split('.')[1] == 'xlsx':
            source = os.path.join(config.input_dir, os.path.basename(self.raw_file_path))
            target = os.path.join(config.raw_dir, os.path.basename(self.raw_file_path))
            shutil.copy2(source, config.raw_dir)
            print(f"Copied {source} -> {target}")
        elif os.path.basename(self.raw_file_path).split('.')[1] == 'xls':
            base_name = os.path.basename(self.raw_file_path)
            new_file_name = base_name.replace('.xls', '.xlsx')
            xlsx_file_path = os.path.join(config.raw_dir, new_file_name)
            print(f"Testing read_excel on {base_name}.")
            try:
                df = pd.read_excel(self.raw_file_path)
                # df['מזהה עסקה'] = df.apply(hash_row, axis=1)
                print(f"Read excel succeeded.")
                with pd.ExcelWriter(xlsx_file_path, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False)
            except ValueError as e:
                print(f"Failed. Conversion returned {ValueError.__name__}: {e}")
                print(f"Trying to read as html.")
                dfs = pd.read_html(self.raw_file_path)
                # with open(self.raw_file_path, "r", encoding='utf-8') as f:
                #     print(f.readlines())
                if 'יתרות' in self.raw_file_path:
                    dfs = pd.read_html(self.raw_file_path, match='יתרה בש"ח', attrs={'class': 'dataTable'})
                    dfs[0].to_excel(xlsx_file_path, index=False)
                elif 'תנועות' in self.raw_file_path:
                    for i, df in enumerate(dfs):
                        if len(df.columns) == 9:
                            df.columns = df.iloc[1]
                            df.drop([df.index[1], df.index[0]], inplace=True)
                            df.reset_index(drop=True, inplace=True)
                            df.to_excel(xlsx_file_path, index=False)
        else:
            raise Exception("Unhandled file type")
        print(f"Successfully handled {os.path.basename(self.raw_file_path)}")


if __name__ == "__main__":
    file_list = glob.glob(os.path.join(config.input_dir, '*.xls*'))
    for f in file_list:
        f = File(f)
        f.to_xlsx()
