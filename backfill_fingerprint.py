import pandas as pd
import config
from csv_handler import generate_transaction_fingerprint
from logger import Logger
from main import log_process

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

    # Generate fingerprints if they don't exist or are missing
    if 'fingerprint' not in main_df.columns or main_df['fingerprint'].isnull().any():
        logger.log_process_ongoing(message=f"Generating fingerprints for {len(main_df)} records...")
        main_df['fingerprint'] = main_df.apply(generate_transaction_fingerprint, axis=1)

    # Ensure category column exists
    if 'קטגוריה' not in main_df.columns:
        main_df['קטגוריה'] = ''
    main_df['קטגוריה'].fillna('', inplace=True)

    # Drop rows where a fingerprint could not be generated
    main_df.dropna(subset=['fingerprint'], inplace=True)

    # Create the database from the three essential columns
    fingerprint_db_df = main_df[['fingerprint', 'מזהה עסקה', 'קטגוריה']].copy()
    fingerprint_db_df.rename(columns={'קטגוריה': 'category'}, inplace=True)

    # De-duplicate, prioritizing keeping entries with categories
    fingerprint_db_df['category'] = fingerprint_db_df['category'].astype(object).fillna('')
    fingerprint_db_df.sort_values(by=['fingerprint', 'category'], ascending=[True, False],
                                  inplace=True)  # Non-empty categories first
    fingerprint_db_df.drop_duplicates(subset=['fingerprint'], keep='first', inplace=True)

    logger.log_process_ongoing(message=f"Saving {len(fingerprint_db_df)} fingerprints to: {config.fingerprint_db_file}")
    fingerprint_db_df.to_csv(config.fingerprint_db_file, index=False)

    # Save the main file in case new fingerprints were generated
    main_df.to_csv(config.compiled_file, index=False)
    logger.log_process_finished(message="Fingerprint database backfilled successfully.")


if __name__ == "__main__":
    backfill()