import asyncio
import difflib
import os.path
from numpy import nan
import pandas as pd
import config
from config import similar_categories_file


class CategorizeFile:  # PRE COMPILER.. DATA FROM CLEAN DIR
    def __init__(self, file_path):
        self.stores_df = None
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        self.file_df = pd.read_csv(file_path)
        if 'קטגוריה' not in self.file_df.columns:
            self.file_df['קטגוריה'] = ""
        self.awaiting_df = pd.DataFrame(columns=self.file_df.columns)

    def load_stores(self):
        self.stores_df = pd.read_csv(config.stores_to_categories_file)

    def save_stores(self):
        self.stores_df.to_csv(config.stores_to_categories_file, index=False)

    def save_progress(self):
        self.file_df.to_csv(self.file_path, index=False)

    def categorize_storename(self, row_data, method='auto'):
        store_name: str = row_data['מקור עסקה']
        date = row_data['תאריך']
        expense = row_data['בחובה']
        income = row_data['בזכות']
        details = None
        digits = None
        if 'תאור מורחב' in row_data.keys():
            details = row_data['תאור מורחב']
        if '4 ספרות' in row_data.keys():
            digits = row_data['4 ספרות']

        if method == 'auto':
            for _, store_row in self.stores_df.iterrows():
                recorded_store, category_, is_static = store_row['store_name'], store_row['category'], store_row[
                    'is_static']

                if recorded_store == store_name and is_static == 1:
                    return category_
                return None
            return None
        elif method == 'input':
            all_categories = set(self.stores_df['category'].tolist())
            for _, row in self.stores_df.iterrows():
                recorded_store, category, is_static = row['store_name'], row['category'], row['is_static']
                if store_name == recorded_store:
                    if is_static == 1:
                        return category
                    elif is_static == 0:
                        dynamic_categories = self.stores_df[self.stores_df['store_name'] == store_name][
                            'category'].tolist()
                        print("Transaction details:")
                        print(
                            f"Transaction source: {store_name}\nDate: {date}\nExpense: {expense}\n"
                            f"Income: {income}\nAdditional details: {details}\nDigits: {digits}")
                        print(f"Past categories for {store_name}: {dynamic_categories}")
                        print(f"All categories: {all_categories}")

                        category_input = input(
                            f"Choose a category for {store_name} from the categories, or type a new one: ").strip()
                        if category_input in dynamic_categories:
                            return category_input
                        else:
                            print("Added to list")
                            new_row = {'store_name': store_name, 'category': category_input, 'is_static': is_static}
                            self.stores_df.loc[len(self.stores_df)] = new_row
                            self.save_stores()
                            return category_input
                    else:  # is_static is fucked (-1 or anything else)
                        print("Transaction details:")
                        print(
                            f"Transaction source: {store_name}\nDate: {date}\nExpense: {expense}\n"
                            f"Income: {income}\nAdditional details: {details}\nDigits: {digits}")

                        is_static_input = input(
                            f"Is this category: [{category}] for {store_name} static?"
                            f"\n (Type '0' if dynamic, type '1' if static): ").strip()
                        is_static = int(is_static_input) if int(is_static_input) == 1 or 0 else -1
                        self.stores_df.loc[self.stores_df['store_name'] == store_name, 'is_static'] = is_static
                        self.save_stores()
                        return category
            print("Transaction details:")
            print(
                f"Transaction source: {store_name}\nDate: {date}\nExpense: {expense}\n"
                f"Income: {income}\nAdditional details: {details}\nDigits: {digits}")
            print(f"All categories: {all_categories}")
            category_input = input(
                f"{store_name} is not in the store list. Choose a category from the list or type a new one: ").strip()

            is_static_input = input(
                f"Should {store_name} be under static category? Type 1 for static and 0 for fluid: ").strip()

            new_row = {'store_name': store_name, 'category': category_input, 'is_static': int(is_static_input)}
            self.stores_df.loc[len(self.stores_df)] = new_row
            self.save_stores()
            return category_input

    def auto_categorize(self):
        k_t = self.load_known_transactions()
        if k_t is None:
            k_t = pd.DataFrame(columns=['transaction_id', 'category'])
        self.load_stores()
        for index, row in self.file_df.iterrows():
            transaction_id = row['מזהה עסקה']
            if len(k_t[k_t['transaction_id'] == transaction_id]) == 1:
                category = k_t[k_t['transaction_id'] == transaction_id]['category'].values[0]
                if category != row['קטגוריה']:
                    self.file_df.loc[index, 'קטגוריה'] = category
                    print(f"Category loaded for ID from backup {transaction_id}.")
                    self.category_store_link_backup(transaction_id, category)

            if row['קטגוריה'] == "" or row['קטגוריה'] == "awaiting" or pd.isna(row['קטגוריה']):
                category = self.categorize_storename(row, method='auto')
                self.file_df.loc[index, 'קטגוריה'] = category
                self.save_progress()
                if category is None:
                    self.awaiting_df.loc[len(self.awaiting_df)] = row
                if category is not None:
                    self.category_store_link_backup(transaction_id, category)
            else:
                self.category_store_link_backup(transaction_id, row['קטגוריה'])

    def manual_categorizer(self, through='input'):
        if through.lower() not in ['input', 'discord']:
            raise ValueError("you must specify an engine manually (input, discord)")
        self.load_stores()
        for index, row in self.awaiting_df.iterrows():
            if row['קטגוריה'] == "" or row['קטגוריה'] == "awaiting" or pd.isna(row['קטגוריה']):
                category = self.categorize_storename(row, method='input')

                self.file_df.loc[self.file_df['מזהה עסקה'] == row['מזהה עסקה'], 'קטגוריה'] = category
                self.save_progress()
                self.awaiting_df.drop(index=index, axis=1)
        # DISCORD BOT TRY
        # if through == 'discord':
        #     print("Launching discord bot...")
        #     bot = DiscordBot(config.DISCORD_BOT_TOKEN, config.DISCORD_USER_ID)
        #
        #     async def main():
        #         for index, transaction_row in self.awaiting_df.iterrows():
        #             print(transaction_row)
        #             for idx, store_row in self.stores_df.iterrows():
        #                 store_name, category, is_static = store_row['store_name'], store_row['category'], store_row[
        #                     'is_static']
        #                 print(store_name)
        #                 if store_name == transaction_row['מקור עסקה'] and is_static == 0:
        #                     dynamic_categories = set(self.stores_df['category'].tolist())
        #                     await bot.send(f"Transaction details:\n"
        #                                    f"מקור עסקה: {transaction_row['מקור עסקה']}\n"
        #                                    f"תאריך: {transaction_row['תאריך']}\n"
        #                                    f"בזכות: {transaction_row['בזכות']}\n"
        #                                    f"בחובה: {transaction_row['בחובה']}\n"
        #                                    f"קטגוריות: {dynamic_categories}\n"
        #                                    f"Please enter category:")
        #                     response = await bot.receive()
        #                     if response:
        #                         print(f"Chosen category: {response}")
        #                         self.awaiting_df.loc[index, 'קטגוריה'] = response
        #                         self.stores_df.loc[self.stores_df['store_name'] == store_name, 'category'] = response
        #                         save_stores()
        #                 else:
        #                     print("Got to the what else..")
        #         await asyncio.sleep(1)  # Add a short delay to avoid spamming
        #
        #     bot.run()
        #     asyncio.get_event_loop().create_task(main())

    @staticmethod
    def fix_null_category_status():
        fix_amount = 10
        while fix_amount > 0:
            stores_df = pd.read_csv(config.stores_to_categories_file)
            fix_amount = len(stores_df[stores_df['is_static'] == -1])
            for index, row in stores_df.iterrows():
                category = row['category']
                store_name = row['store_name']
                if row['is_static'] not in [1, 0]:
                    print(f"Fixing store {row['store_name']} with category {row['category']}.")
                    is_static_input = input(
                        f"Is this category: [{category}] for {store_name} static?"
                        f"\n (Type '0' if dynamic, type '1' if static): ")
                    is_static = int(is_static_input) if int(is_static_input) in [1, 0] else -1
                    stores_df.loc[stores_df['store_name'] == store_name, 'is_static'] = is_static
                    stores_df.to_csv(config.stores_to_categories_file)
                    fix_amount -= 1
                    break

    @staticmethod
    def fix_nan_category():
        stores_df = pd.read_csv(config.stores_to_categories_file)
        categories_to_check = set(stores_df['category'].tolist())
        stores_df['category'].fillna("NULL")
        stores_df.to_csv(config.stores_to_categories_file, index=False)

    @staticmethod
    def fix_similar_categories_in_file():
        stores_df = pd.read_csv(config.stores_to_categories_file)
        stores_df['category'].replace(nan, "NULL", inplace=True)

        compiled_df = pd.read_csv(config.compiled_file)
        backup_df = pd.read_csv(config.transaction_category_file)
        categories_to_check = set(stores_df['category'].tolist())
        not_to_check = []
        linked_pairs = pd.read_csv(similar_categories_file)

        for category in categories_to_check:
            if category not in not_to_check:
                ans = difflib.get_close_matches(category, categories_to_check, n=3)
                if len(ans) > 1:
                    match_ratio = difflib.SequenceMatcher(None, ans[0], ans[1]).ratio()
                    if match_ratio > 0.7:
                        pair = [ans[0], ans[1]]
                        if pair in linked_pairs.values.tolist() or list(reversed(pair)) in linked_pairs.values.tolist():
                            continue
                        print(f"Checking {category}:")
                        for i, option in enumerate(ans, 1):
                            print(f"{i}. {option}")
                        ans_input = input(f"Choose:\n1. Keep first\n2. Keep second\n3. Keep both\n")
                        if ans_input in ['1', '2']:
                            choice = int(ans_input) - 1
                            category = ans[choice]
                            stores_df.loc[((stores_df['category'] == ans[0]) | (
                                    stores_df['category'] == ans[1])), 'category'] = category
                            backup_df.loc[((backup_df['category'] == ans[0]) | (
                                    backup_df['category'] == ans[1])), 'category'] = category
                            compiled_df.loc[(compiled_df['קטגוריה'] == ans[0]) | (
                                    compiled_df['קטגוריה'] == ans[1]), 'קטגוריה'] = category
                        not_to_check.extend(ans)
                        if ans_input == '3':
                            linked_pairs.loc[len(linked_pairs)] = pair
        linked_pairs.to_csv(config.similar_categories_file, index=False)
        stores_df.to_csv(config.stores_to_categories_file, index=False)
        compiled_df.to_csv(config.compiled_file, index=False)
        backup_df.to_csv(config.transaction_category_file, index=False)
        print("Done fixing similar categories.")

    @staticmethod
    def rename_category(old_name, new_name):
        pass

    @staticmethod
    def category_store_link_backup(transaction_id, category):
        if not os.path.isfile(config.transaction_category_file):
            data = {'transaction_id': [transaction_id], 'category': [category]}
            df = pd.DataFrame(data=data)
            df.to_csv(config.transaction_category_file, index=False)
        else:
            data = {'transaction_id': transaction_id, 'category': category}
            df = pd.read_csv(config.transaction_category_file)
            exists = df.loc[df['transaction_id'] == transaction_id]
            if exists.empty:
                df.loc[len(df)] = data
            else:
                df.loc[df['transaction_id'] == transaction_id, 'category'] = category
            df.drop_duplicates(subset=['transaction_id'], inplace=True, keep='last')
            df.to_csv(config.transaction_category_file, index=False)

    @staticmethod
    def load_known_transactions():
        if os.path.isfile(config.transaction_category_file):
            df = pd.read_csv(config.transaction_category_file)
            df.drop_duplicates(subset=['transaction_id'], inplace=True, keep='first')
            return df
        return None

    @staticmethod
    def update_store_category(store_name, category):
        df = pd.read_csv(config.stores_to_categories_file)
        match = df[df['store_name'] == store_name]
        if len(match) >= 2:
            df.loc[df['store_name'] == store_name, 'is_static'] = 0
            if len(match.loc[match['category'] == category]) == 0:
                df.loc[len(df)] = [store_name, category, 0]
            else:
                df.drop_duplicates(subset=['store_name', 'category'], inplace=True)
        elif len(match) == 1:
            if match['category'].item() != category:
                if match['is_static'].item() == 1:
                    print(
                        f"Found new category '{category}' for {store_name}, which was previously defined as {match['category'].item()}")
                    ans = input(
                        "Type: \n1 to modify current static category for store. \n2 to change store to dynamic and add "
                        "category.\n3 to ignore.\n")
                    if ans == '1':
                        df.loc[df['store_name'] == store_name, 'category'] = category
                    elif ans == '2':
                        df.loc[df['store_name'] == store_name, 'is_static'] = 0
                        df.loc[len(df)] = [store_name, category, 0]
                    else:
                        print("Typed 3 or invalid input, either way- not doing anything.")
                        return
                if match['is_static'].item() == 0:
                    df.loc[len(df)] = [store_name, category, 0]
        else:
            print("STORE NOT CACHED IN STORE_CATEGORY file. PEBKAC")
            return
        df.to_csv(config.stores_to_categories_file, index=False)

    @staticmethod
    def dupe_seeker():
        print("Got here")
        df = pd.read_csv(config.compiled_file)

        grouped_expenses = df[df['בחובה'] != 0].groupby(['תאריך', 'בחובה']).size().reset_index(name='counts')
        filtered_groups = grouped_expenses[grouped_expenses['counts'] > 1]
        result_expenses = pd.merge(df, filtered_groups[['תאריך', 'בחובה']], on=['תאריך', 'בחובה'], how='inner')

        grouped_incomes = df[df['בזכות'] != 0].groupby(['תאריך', 'בזכות']).size().reset_index(name='counts')
        filtered_groups = grouped_incomes[grouped_incomes['counts'] > 1]
        result_incomes = pd.merge(df, filtered_groups[['תאריך', 'בזכות']], on=['תאריך', 'בזכות'], how='inner')

        # Display the results
        print("Expenses:")
        print(result_expenses)
        print("\nIncomes:")
        print(result_incomes)


if __name__ == "__main__":
    # main_df = pd.read_csv(config.compiled_file)
    # kt_df = pd.read_csv(config.transaction_category_file)
    # missing_transactions = main_df[~main_df['מזהה עסקה'].isin(kt_df['transaction_id'])]
    # print(missing_transactions)
    f = CategorizeFile(config.compiled_file)
    # f.fix_categories()
    # f.auto_categorize()
    # f.manual_categorizer()
