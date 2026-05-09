"""Interactive CSV-era maintenance and Sheets helpers (kept out of :mod:`categorization.categorizer`)."""

from __future__ import annotations

import logging
import os
from typing import Any

import difflib
import pandas as pd
from numpy import nan

import config
from categorization.text_display import terminal_bidi as _terminal_bidi

log = logging.getLogger(__name__)


def fix_null_category_status() -> None:
    fix_amount = 10
    while fix_amount > 0:
        if os.path.isfile(config.ledger_db_file):
            from pipeline.ledger import load_stores_dataframe_from_ledger

            stores_df = load_stores_dataframe_from_ledger(config.ledger_db_file)
        else:
            stores_df = pd.read_csv(config.stores_to_categories_file)
        fix_amount = len(stores_df[stores_df["is_static"] == -1])
        for _index, row in stores_df.iterrows():
            category = row["category"]
            store_name = row["store_name"]
            if row["is_static"] not in [1, 0]:
                log.info(
                    "fix_null_category_status: store=%s category=%s",
                    _terminal_bidi(row["store_name"]),
                    _terminal_bidi(row["category"]),
                )
                is_static_input = input(
                    f"Is this category: [{_terminal_bidi(category)}] for {_terminal_bidi(store_name)} static?"
                    f"\n (Type '0' if dynamic, type '1' if static): "
                )
                is_static = int(is_static_input) if int(is_static_input) in [1, 0] else -1
                stores_df.loc[stores_df["store_name"] == store_name, "is_static"] = is_static
                if os.path.isfile(config.ledger_db_file):
                    from pipeline.ledger import sync_stores_to_ledger_from_dataframe

                    sync_stores_to_ledger_from_dataframe(config.ledger_db_file, stores_df)
                else:
                    stores_df.to_csv(config.stores_to_categories_file, index=False)
                fix_amount -= 1
                break


def fix_nan_category() -> None:
    if os.path.isfile(config.ledger_db_file):
        from pipeline.ledger import load_stores_dataframe_from_ledger

        stores_df = load_stores_dataframe_from_ledger(config.ledger_db_file)
    else:
        stores_df = pd.read_csv(config.stores_to_categories_file)
    stores_df["category"].fillna("NULL")
    if os.path.isfile(config.ledger_db_file):
        from pipeline.ledger import sync_stores_to_ledger_from_dataframe

        sync_stores_to_ledger_from_dataframe(config.ledger_db_file, stores_df)
    else:
        stores_df.to_csv(config.stores_to_categories_file, index=False)


def fix_similar_categories_in_file() -> None:
    log.info("fix_similar_categories_in_file: starting")
    if os.path.isfile(config.ledger_db_file):
        from pipeline.ledger import (
            load_known_transactions_backup_from_ledger,
            load_stores_dataframe_from_ledger,
            load_transactions_dataframe_from_ledger,
        )

        stores_df = load_stores_dataframe_from_ledger(config.ledger_db_file)
        compiled_df = load_transactions_dataframe_from_ledger(config.ledger_db_file)
        backup_df = (
            load_known_transactions_backup_from_ledger(config.ledger_db_file)
            or pd.DataFrame(columns=["transaction_id", "category"])
        )
    else:
        stores_df = pd.read_csv(config.stores_to_categories_file)
        compiled_df = pd.read_csv(config.compiled_file)
        backup_df = pd.read_csv(config.transaction_category_file)
    stores_df["category"] = stores_df["category"].replace(nan, "NULL")
    categories_to_check = set(stores_df["category"].tolist())
    not_to_check: list[Any] = []

    for category in categories_to_check:
        if category not in not_to_check:
            ans = difflib.get_close_matches(category, categories_to_check, n=3)
            if len(ans) > 1:
                match_ratio = difflib.SequenceMatcher(None, ans[0], ans[1]).ratio()
                if match_ratio > 0.7:
                    log.info("Similar categories check for: %s", _terminal_bidi(category))
                    for i, option in enumerate(ans, 1):
                        log.info("  %s. %s", i, _terminal_bidi(option))
                    ans_input = input("Choose:\n1. Keep first\n2. Keep second\n3. Keep both\n")
                    if ans_input in ["1", "2"]:
                        choice = int(ans_input) - 1
                        category = ans[choice]
                        stores_df.loc[
                            ((stores_df["category"] == ans[0]) | (stores_df["category"] == ans[1])),
                            "category",
                        ] = category
                        backup_df.loc[
                            ((backup_df["category"] == ans[0]) | (backup_df["category"] == ans[1])),
                            "category",
                        ] = category
                        compiled_df.loc[
                            (compiled_df["קטגוריה"] == ans[0]) | (compiled_df["קטגוריה"] == ans[1]),
                            "קטגוריה",
                        ] = category
                    not_to_check.extend(ans)
    if os.path.isfile(config.ledger_db_file):
        from pipeline.ledger import sync_stores_to_ledger_from_dataframe
        from pipeline.ledger import upsert_compiled_dataframe_to_ledger

        sync_stores_to_ledger_from_dataframe(config.ledger_db_file, stores_df)
        upsert_compiled_dataframe_to_ledger(compiled_df, config.ledger_db_file)
    else:
        stores_df.to_csv(config.stores_to_categories_file, index=False)
        compiled_df.to_csv(config.compiled_file, index=False)
        backup_df.to_csv(config.transaction_category_file, index=False)


def rename_category(old_name: str, new_name: str) -> None:
    del old_name, new_name
    pass


def category_store_link_backup(transaction_id: Any, category: Any) -> None:
    if os.path.isfile(config.ledger_db_file):
        return
    if not os.path.isfile(config.transaction_category_file):
        data = {"transaction_id": [transaction_id], "category": [category]}
        df = pd.DataFrame(data=data)
        df.to_csv(config.transaction_category_file, index=False)
    else:
        data = {"transaction_id": transaction_id, "category": category}
        df = pd.read_csv(config.transaction_category_file)
        exists = df.loc[df["transaction_id"] == transaction_id]
        if exists.empty:
            df.loc[len(df)] = data
        else:
            df.loc[df["transaction_id"] == transaction_id, "category"] = category
        df.drop_duplicates(subset=["transaction_id"], inplace=True, keep="last")
        df.to_csv(config.transaction_category_file, index=False)


def update_store_category(store_name: str, category: str) -> None:
    if os.path.isfile(config.ledger_db_file):
        from pipeline.ledger import load_stores_dataframe_from_ledger

        df = load_stores_dataframe_from_ledger(config.ledger_db_file)
    else:
        df = pd.read_csv(config.stores_to_categories_file)
    match = df[df["store_name"] == store_name]
    if len(match) >= 2:
        df.loc[df["store_name"] == store_name, "is_static"] = 0
        if len(match.loc[match["category"] == category]) == 0:
            df.loc[len(df)] = [store_name, category, 0]
        else:
            df.drop_duplicates(subset=["store_name", "category"], inplace=True)
    elif len(match) == 1:
        if match["category"].item() != category:
            if match["is_static"].item() == 1:
                log.warning(
                    "Store %s: new category %s vs existing static %s",
                    _terminal_bidi(store_name),
                    _terminal_bidi(category),
                    _terminal_bidi(match["category"].item()),
                )
                ans = input(
                    "Type: \n1 to modify current static category for store. \n2 to change store to dynamic and add "
                    "category.\n3 to ignore.\n"
                )
                if ans == "1":
                    df.loc[df["store_name"] == store_name, "category"] = category
                elif ans == "2":
                    df.loc[df["store_name"] == store_name, "is_static"] = 0
                    df.loc[len(df)] = [store_name, category, 0]
                else:
                    log.info("User skipped category update (option 3 or invalid)")
                    return
            if match["is_static"].item() == 0:
                df.loc[len(df)] = [store_name, category, 0]
    else:
        log.error("update_store_category: store not in stores file (no match)")
        return
    if os.path.isfile(config.ledger_db_file):
        from pipeline.ledger import sync_stores_to_ledger_from_dataframe

        sync_stores_to_ledger_from_dataframe(config.ledger_db_file, df)
        return
    df.to_csv(config.stores_to_categories_file, index=False)


def dupe_seeker() -> None:
    if os.path.isfile(config.ledger_db_file):
        from pipeline.ledger import load_transactions_dataframe_from_ledger

        log.info("dupe_seeker: scanning ledger %s", config.ledger_db_file)
        df = load_transactions_dataframe_from_ledger(config.ledger_db_file)
    else:
        log.info("dupe_seeker: scanning %s", config.compiled_file)
        df = pd.read_csv(config.compiled_file)

    grouped_expenses = df[df["בחובה"] != 0].groupby(["תאריך", "בחובה"]).size().reset_index(name="counts")
    filtered_groups = grouped_expenses[grouped_expenses["counts"] > 1]
    result_expenses = pd.merge(df, filtered_groups[["תאריך", "בחובה"]], on=["תאריך", "בחובה"], how="inner")

    grouped_incomes = df[df["בזכות"] != 0].groupby(["תאריך", "בזכות"]).size().reset_index(name="counts")
    filtered_groups = grouped_incomes[grouped_incomes["counts"] > 1]
    result_incomes = pd.merge(df, filtered_groups[["תאריך", "בזכות"]], on=["תאריך", "בזכות"], how="inner")

    log.info("Duplicate expense rows (by date+amount): %s", len(result_expenses))
    log.debug("Expense dupes:\n%s", result_expenses)
    log.info("Duplicate income rows (by date+amount): %s", len(result_incomes))
    log.debug("Income dupes:\n%s", result_incomes)
