import os
from dotenv import load_dotenv

load_dotenv()
input_dir = 'data\\input\\'
raw_dir = 'data\\raw\\'
cleaned_dir = 'data\\clean\\'
compiled_dir = 'export\\compiled\\'
compiled_file = compiled_dir + 'compiled.csv'
holdings_file = compiled_dir + 'holdings.csv'
transaction_category_file = compiled_dir + 'bak\\' + 'transaction_category.csv'
static_dir = 'data\\static\\'
stores_to_categories_file = static_dir + 'stores_to_categories.csv'
bank_username = os.getenv('bank_username')
bank_password = os.getenv('bank_password')
credit_username = os.getenv('credit_username')
credit_last6 = os.getenv('credit_last6')
credit_password = os.getenv('credit_password')
max_username = os.getenv('max_username')
max_password = os.getenv('max_password')
telegram_bot_key = os.getenv('telegram_bot_key')
PREDEFINED_USER_ID = os.getenv('PREDEFINED_USER_ID')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
DISCORD_USER_ID = os.getenv('DISCORD_USER_ID')
GOOGLE_API_USER = os.getenv('GOOGLE_API_USER')
GOOGLE_WORKSHEET_ID = os.getenv('GOOGLE_WORKSHEET_ID')
similar_categories_file = 'data\\static\\similar_pairs.csv'