import datetime
import glob
import logging
import re
import os

import pandas as pd
import pandas.errors

import config

log = logging.getLogger(__name__)

_ISO_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _standardize_date_separators(val: object) -> str | None:
    """Normalize ``-``/``.``/``/`` for parsing; return None if missing."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except TypeError:
        pass
    s = str(val).strip()
    if s.lower() in ("nan", "nat", "none", "<na>"):
        return None
    return re.sub(r"[-/.]", "-", s)


def parse_post_ingest_date_scalar(val: object) -> pd.Timestamp:
    """
    Parse a single ``תאריך`` cell after CSV / ledger round-trip (see :func:`parse_post_ingest_date_column`).
    """
    s = _standardize_date_separators(val)
    if s is None:
        return pd.NaT
    if _ISO_YMD.fullmatch(s):
        return pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    return pd.to_datetime(s, dayfirst=True, errors="coerce", format="mixed")


def parse_post_ingest_date_column(series: pd.Series) -> pd.Series:
    """
    Parse ``תאריך`` after CSV / ledger round-trip.

    Rows produced by this app are written as ISO dates (``datetime.date`` / ``%Y-%m-%d``).
    For those, parse with a fixed format — **never** pass ``dayfirst=True``, which on
    some pandas versions misreads ISO strings when combined with ``format=\"mixed\"``.

    Non-ISO strings (first ingest from bank Excel, legacy files) fall back to
    ``dayfirst=True`` + ``format=\"mixed\"`` like the old single-path behaviour.
    """
    return series.map(parse_post_ingest_date_scalar)


def update_category_in_fingerprint_db(fingerprint, category):
    """
    Updates ``קטגוריה`` on ``ledger_transaction`` for ``fingerprint``.

    Legacy name kept for callers; ``fingerprint_db.csv`` is deprecated.
    """
    try:
        from pipeline.ledger import update_category_by_fingerprint

        db_path = config.ledger_db_file
        if not os.path.exists(db_path):
            log.debug("update_category_in_fingerprint_db: no ledger at %s (skip)", db_path)
            return
        if fingerprint is None or (isinstance(fingerprint, float) and pd.isna(fingerprint)):
            return
        update_category_by_fingerprint(db_path, str(fingerprint).strip(), category)
        log.info("Ledger: set category for fingerprint (len=%s)", len(str(fingerprint)))
    except Exception as e:
        log.exception("update_category_in_fingerprint_db failed: %s", e)


class Compiler:
    def __init__(self, path_to_main, ledger_db: str | None = None):
        log.info("Compiler init main_file=%s ledger_db=%s", path_to_main, ledger_db)
        self.main_file = path_to_main
        self.ledger_db = ledger_db
        self.new_df = pd.DataFrame()
        self.added_transactions = pd.DataFrame()
        if ledger_db:
            from pipeline.ledger import load_transactions_dataframe_from_ledger
            from pipeline.ledger import migrate_ledger_db

            migrate_ledger_db(ledger_db)
            self.main_df = load_transactions_dataframe_from_ledger(ledger_db)
            log.debug("Loaded ledger rows=%s from %s", len(self.main_df), ledger_db)
        else:
            try:
                self.main_df = pd.read_csv(self.main_file)
                log.debug("Loaded main CSV rows=%s", len(self.main_df))
            except (FileNotFoundError, pandas.errors.EmptyDataError):
                log.info("Main file missing or empty; starting fresh: %s", path_to_main)
                self.main_df = pd.DataFrame()
                self.main_df.to_csv(self.main_file, index=False)

    def __compile_new__(self, path_to_files, suffix):
        log.info("__compile_new__: path=%s suffix=%s", path_to_files, suffix)
        self.suffix = suffix

        def standardize_date_format(date_str):
            date_str = str(date_str)
            return re.sub(r'[-/.]', '-', date_str)

        file_list = glob.glob(os.path.join(path_to_files, '*.csv'))
        log.debug("Found %s csv file(s) under %s", len(file_list), path_to_files)
        new_df_list = []
        for file in file_list:
            df = pd.read_csv(file)
            log.debug("Read %s rows=%s", file, len(df))
            new_df_list.append(df)
        if not new_df_list:
            log.warning("No CSV files to compile in %s", path_to_files)
            self.new_df = pd.DataFrame()
            return

        self.new_df = pd.concat(new_df_list, ignore_index=True)
        log.info("Concatenated new_df rows=%s", len(self.new_df))
        self.new_df['תאריך'] = self.new_df['תאריך'].apply(standardize_date_format)
        self.new_df['תאריך'] = parse_post_ingest_date_column(self.new_df['תאריך'])
        self.new_df['תאריך'] = self.new_df['תאריך'].dt.date
        self.new_df.sort_values(by='תאריך', inplace=True)
        self.new_df.reset_index(drop=True, inplace=True)

    def compile_to_main(self):
        log.info("compile_to_main: new_df empty=%s", self.new_df.empty)
        if self.new_df.empty:
            log.info("Nothing to merge into main (new_df empty).")
            return
        if "fingerprint" in self.new_df.columns:  # transactions branch (clean CSV from TransactionFile)
            log.debug("compile_to_main: transactions branch (fingerprints)")
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

            log.info(
                "De-duplication done: %s new transaction rows (by fingerprint)",
                len(self.added_transactions),
            )

            self.main_df['תאריך'] = parse_post_ingest_date_column(self.main_df['תאריך'])
            self.main_df.sort_values(by='תאריך', inplace=True)
            self.main_df.reset_index(drop=True, inplace=True)
            self.main_df['תאריך'] = self.main_df['תאריך'].dt.date
        else:  # In Holdings Mode
            log.debug("compile_to_main: holdings branch (dedupe by date)")
            concat_df = pd.concat([self.main_df, self.new_df], ignore_index=True)
            concat_df['תאריך'] = parse_post_ingest_date_column(concat_df['תאריך'])
            concat_df.drop_duplicates(subset=['תאריך'], ignore_index=True, inplace=True, keep='first')
            concat_df.fillna(value=0.0, inplace=True)
            self.main_df = concat_df
            self.main_df['תאריך'] = parse_post_ingest_date_column(self.main_df['תאריך'])
            self.main_df.sort_values(by='תאריך', inplace=True)
            self.main_df.reset_index(drop=True, inplace=True)
            self.main_df['תאריך'] = self.main_df['תאריך'].dt.date

    def update_fingerprint_db(self):
        """Deprecated: category data lives on ``ledger_transaction`` when using SQLite."""
        if self.ledger_db:
            log.debug("update_fingerprint_db: skipped (ledger mode)")
            return
        if self.added_transactions.empty:
            log.info("Fingerprint DB unchanged (no new transactions).")
            return

        # Legacy fingerprint_db.csv sidecar: still pairs fingerprint with old row-hash column when present.
        new_fingerprints_df = self.added_transactions[["fingerprint", "מזהה עסקה"]].copy()
        new_fingerprints_df["category"] = ""

        db_path = config.fingerprint_db_file
        db_dir = os.path.dirname(db_path)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)

        try:
            fingerprint_df = pd.read_csv(db_path)
            if "category" not in fingerprint_df.columns:
                fingerprint_df["category"] = ""
            updated_df = pd.concat([fingerprint_df, new_fingerprints_df], ignore_index=True)
        except FileNotFoundError:
            updated_df = new_fingerprints_df

        updated_df["category"] = updated_df["category"].astype(object).fillna("")
        updated_df.sort_values(by=["fingerprint", "category"], ascending=[True, False], inplace=True)
        updated_df.drop_duplicates(subset=["fingerprint"], keep="first", inplace=True)

        updated_df.to_csv(db_path, index=False)
        log.info("Fingerprint DB updated with %s new entries -> %s", len(new_fingerprints_df), db_path)

    def save_new(self):
        if self.ledger_db:
            log.debug("save_new: skipped (ledger mode; no slice CSV)")
            return ""
        today_date = f"{datetime.datetime.now().date()}_{datetime.datetime.now().hour}-{datetime.datetime.now().minute}"
        output_path = os.path.join(config.compiled_dir, f"new_{today_date}_{self.suffix}.csv")
        self.new_df.to_csv(output_path, index=False)
        log.info("Wrote new slice CSV %s (%s rows)", output_path, len(self.new_df))
        return output_path

    def save_main(self):
        if self.ledger_db:
            from pipeline.ledger import upsert_compiled_dataframe_to_ledger

            upsert_compiled_dataframe_to_ledger(self.main_df, self.ledger_db)
            log.info("Wrote main ledger SQLite %s (%s rows)", self.ledger_db, len(self.main_df))
            return self.ledger_db
        self.main_df.to_csv(self.main_file, index=False)
        log.info("Wrote main ledger %s (%s rows)", self.main_file, len(self.main_df))
        return self.main_file

    def save_all(self):
        log.debug("save_all: main + new")
        return self.save_main(), self.save_new()
