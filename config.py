import os
from datetime import timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# === Настройки ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MAX_PRODUCTS = 10
CHECK_INTERVAL = 3600
MSK = timezone(timedelta(hours=3))
DB_PATH = os.path.join(os.path.dirname(__file__), 'price_bot.db')

# Состояния навигации
MAIN_MENU = "main_menu"
PRODUCT_VIEW = "product_view"
PRICE_VIEW = "price_view"
HISTORY_VIEW = "history_view"
ADD_PRODUCT = "add_product"