"""
Normalize downloaded workbooks to ``.xlsx`` under a pipeline ``raw`` directory.

Default target is legacy ``config.raw_dir``; prefer passing ``target_raw_dir`` from
``config.holdings_raw_dir`` or ``config.transactions_raw_dir`` after inbox routing.
"""
import glob
import logging
import os
import shutil

import pandas as pd

import config

log = logging.getLogger(__name__)


class RawDownloadedWorkbook:
    """One workbook path; converts or copies it into a target raw directory as ``.xlsx``."""

    def __init__(self, raw_file_path=None):
        if raw_file_path:
            self.raw_file_path = raw_file_path
            log.debug("RawDownloadedWorkbook: explicit path %s", raw_file_path)
        else:
            self._locate_first_in_download_inbox()

    def _locate_first_in_download_inbox(self):
        inbox = config.download_inbox_dir
        files = glob.glob(os.path.join(inbox, "*.xls*"))
        log.debug("Scanning download inbox %s for *.xls* — found %s files", inbox, len(files))
        if len(files) == 0:
            log.error("Download inbox is empty: %s", inbox)
            raise Exception("Download inbox folder is empty.")
        self.raw_file_path = files[0]
        log.info("Using first workbook in inbox: %s", self.raw_file_path)

    def to_xlsx(self, target_raw_dir=None):
        """Copy or convert ``raw_file_path`` into ``target_raw_dir`` (default ``config.raw_dir``) as ``.xlsx``."""
        raw_root = target_raw_dir or config.raw_dir
        os.makedirs(raw_root, exist_ok=True)
        base_name = os.path.basename(self.raw_file_path)
        _, ext = os.path.splitext(base_name)
        ext_lower = ext.lower()
        log.info("Normalizing workbook %s (ext=%s) -> %s", self.raw_file_path, ext_lower, raw_root)

        if ext_lower == ".xlsx":
            source = self.raw_file_path
            target = os.path.join(raw_root, base_name)
            shutil.copy2(source, raw_root)
            log.info("Copied xlsx to raw: %s -> %s", source, target)
        elif ext_lower == ".xls":
            new_file_name = base_name.replace(".xls", ".xlsx", 1)
            xlsx_file_path = os.path.join(raw_root, new_file_name)
            log.debug("Converting .xls via pandas.read_excel: %s", base_name)
            try:
                df = pd.read_excel(self.raw_file_path)
                log.debug("read_excel OK, rows=%s cols=%s", len(df), len(df.columns))
                with pd.ExcelWriter(xlsx_file_path, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False)
                log.info("Wrote normalized xlsx: %s", xlsx_file_path)
            except ValueError as e:
                log.warning("read_excel failed (%s); trying read_html", e)
                dfs = pd.read_html(self.raw_file_path)
                log.debug("read_html returned %s table(s)", len(dfs))
                if "יתרות" in self.raw_file_path:
                    dfs = pd.read_html(
                        self.raw_file_path,
                        match='יתרה בש"ח',
                        attrs={"class": "dataTable"},
                    )
                    dfs[0].to_excel(xlsx_file_path, index=False)
                    log.info("Holdings HTML table -> %s", xlsx_file_path)
                elif "תנועות" in self.raw_file_path:
                    for i, df in enumerate(dfs):
                        if len(df.columns) == 9:
                            log.debug("Using transactions HTML fragment index=%s (9 columns)", i)
                            df.columns = df.iloc[1]
                            df.drop([df.index[1], df.index[0]], inplace=True)
                            df.reset_index(drop=True, inplace=True)
                            df.to_excel(xlsx_file_path, index=False)
                            break
                    log.info("Transactions HTML -> %s", xlsx_file_path)
        else:
            log.error("Unsupported extension for %s", self.raw_file_path)
            raise Exception("Unhandled file type")
        log.info("Finished normalizing %s", base_name)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    file_list = glob.glob(os.path.join(config.download_inbox_dir, "*.xls*"))
    log.info("__main__: processing %s files from inbox", len(file_list))
    for path in file_list:
        wb = RawDownloadedWorkbook(path)
        wb.to_xlsx(target_raw_dir=config.raw_dir)
