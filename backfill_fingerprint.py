import pandas as pd
import config
from csv_handler import generate_transaction_fingerprint
from logger import Logger
from main import log_process

logger = Logger()

@log_process
def backfill():
    """
    Generates fingerprints for all existing transactions in the compiled file
    and creates the initial fingerprint_db.csv.
    """
    try:
        logger.log_process_ongoing(message=f"Loading main compiled file from: {config.compiled_file}")
        main_df = pd.read_csv(config.compiled_file)
    except FileNotFoundError:
        logger.log_process_finished(message="Compiled file not found. Nothing to backfill.")
        return

    if 'מזהה עסקה' not in main_df.columns:
        logger.log_process_finished(message="Column 'מזהה עסקה' not in compiled file. Cannot proceed.")
        return

    logger.log_process_ongoing(message=f"Generating fingerprints for {len(main_df)} existing records...")
    main_df['fingerprint'] = main_df.apply(generate_transaction_fingerprint, axis=1)

    # Filter out any rows where a fingerprint could not be made
    #  main_df.dropna(subset=['fingerprint'], inplace=True)

    # Create the database from the two essential columns
    fingerprint_db_df = main_df[['fingerprint', 'מזהה עסקה']]

    # Remove any potential duplicates to ensure a clean database
    fingerprint_db_df.drop_duplicates(subset=['fingerprint'], keep='first', inplace=True)

    logger.log_process_ongoing(message=f"Saving {len(fingerprint_db_df)} fingerprints to: {config.fingerprint_db_file}")
    fingerprint_db_df.to_csv(config.fingerprint_db_file, index=False)
    main_df.to_csv(config.compiled_file, index=False)
    logger.log_process_finished(message="Fingerprint database created successfully.")

if __name__ == "__main__":
    backfill()