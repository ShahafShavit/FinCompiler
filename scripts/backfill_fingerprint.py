"""Backfill fingerprint column and fingerprint_db from compiled.csv (run from repo with venv)."""

from __future__ import annotations

import os
import sys

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

import pandas as pd

import config
from apps.qt_main import log_process
from logger import Logger
from pipeline.csv_handler import generate_transaction_fingerprint

logger = Logger()


@log_process
def backfill():
    """
    Generates fingerprints for all existing transactions, populates the 'category' column,
    and creates/updates the fingerprint_db.csv.
    """
    try:
        logger.log_process_ongoing(message=f"Loading main compiled file from: {config.compiled_file}")
        main_df = pd.read_csv(config.compiled_file)
    except FileNotFoundError:
        logger.log_process_finished(message="Compiled file not found. Nothing to backfill.")
        return

    if "fingerprint" not in main_df.columns or main_df["fingerprint"].isnull().any():
        logger.log_process_ongoing(message=f"Generating fingerprints for {len(main_df)} records...")
        main_df["fingerprint"] = main_df.apply(generate_transaction_fingerprint, axis=1)

    if "קטגוריה" not in main_df.columns:
        main_df["קטגוריה"] = ""
    main_df["קטגוריה"].fillna("", inplace=True)

    main_df.dropna(subset=["fingerprint"], inplace=True)

    fingerprint_db_df = main_df[["fingerprint", "מזהה עסקה", "קטגוריה"]].copy()
    fingerprint_db_df.rename(columns={"קטגוריה": "category"}, inplace=True)

    fingerprint_db_df["category"] = fingerprint_db_df["category"].astype(object).fillna("")
    fingerprint_db_df.sort_values(
        by=["fingerprint", "category"],
        ascending=[True, False],
        inplace=True,
    )
    fingerprint_db_df.drop_duplicates(subset=["fingerprint"], keep="first", inplace=True)

    logger.log_process_ongoing(
        message=f"Saving {len(fingerprint_db_df)} fingerprints to: {config.fingerprint_db_file}"
    )
    fingerprint_db_df.to_csv(config.fingerprint_db_file, index=False)

    main_df.to_csv(config.compiled_file, index=False)
    logger.log_process_finished(message="Fingerprint database backfilled successfully.")


if __name__ == "__main__":
    backfill()
