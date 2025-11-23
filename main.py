import logging
import sqlite3
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from config import TELEGRAM_BOT_TOKEN, CHECK_INTERVAL
from database import Database
from parser import Parser
from handlers import Handlers

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class PriceMonitor:
    def __init__(self):
        self.db = Database()
        self.parser = Parser()
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Инициализация обработчиков
        self.handlers = Handlers(self)

        # Внутренние состояния
        self.last_prices = self.db.get_last_prices()
        self.user_states = {}
        self.notifications = self._load_notifications()
        self.active_users = set()
        self.user_chat_ids = {}

        # Регистрация хэндлеров
        self._setup_handlers()

        # Фоновая задача
        self.application.job_queue.run_repeating(
            self.background_price_check,
            interval=CHECK_INTERVAL,
            first=10
        )

    def _load_notifications(self):
        """Загрузка статусов уведомлений из БД"""
        notifications = {}
        conn = sqlite3.connect(self.db.db_path)
        cur = conn.cursor()
        cur.execute("SELECT user_id, name, notify FROM products")
        for user_id, name, notify in cur.fetchall():
            notifications[(user_id, name)] = bool(notify)
        conn.close()
        return notifications

    def _setup_handlers(self):
        """Настройка обработчиков"""
        self.application.add_handler(CommandHandler("start", self.handlers.start_command))
        self.application.add_handler(CommandHandler("status", self.handlers.status_command))
        self.application.add_handler(CommandHandler("test_notify", self.handlers.test_notification_command))
        self.application.add_handler(CommandHandler("notify_status", self.handlers.notification_status_command))
        self.application.add_handler(CallbackQueryHandler(self.handlers.button_callback))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.text_message_handler))

    def get_last_price(self, user_id, product):
        return self.last_prices.get((user_id, product))

    async def background_price_check(self, context: ContextTypes.DEFAULT_TYPE):
        logger.info("=== НАЧАЛО ФОНОВОЙ ПРОВЕРКИ ЦЕН ===")
        conn = sqlite3.connect(self.db.db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT user_id FROM products")
        user_ids = [row[0] for row in cur.fetchall()]
        conn.close()

        for user_id in user_ids:
            user_products = self.db.load_user_products(user_id)
            chat_id = self.user_chat_ids.get(user_id)

            if not chat_id:
                user_data = context.application.user_data.get(user_id, {})
                chat_id = user_data.get("chat_id")
                if chat_id:
                    self.user_chat_ids[user_id] = chat_id

            for name, url in user_products.items():
                phones = self.parser.get_phone_prices(url)
                if not phones:
                    continue
                min_price = phones[0]["price"]
                old_price = self.get_last_price(user_id, name)
                if old_price is None:
                    self.db.update_last_price(user_id, name, min_price)
                    self.last_prices[(user_id, name)] = min_price
                    continue
                if min_price != old_price:
                    self.db.save_history_entry(user_id, name, old_price, min_price)
                    self.db.update_last_price(user_id, name, min_price)
                    self.last_prices[(user_id, name)] = min_price
                    if self.notifications.get((user_id, name), True) and chat_id:
                        msg = f"{'⬇️' if min_price < old_price else '⬆️'} <b>{name}</b>\nБыло: <b>{old_price} ₽</b>\nСтало: <b>{min_price} ₽</b>"
                        try:
                            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")

        logger.info("=== ЗАВЕРШЕНИЕ ФОНОВОЙ ПРОВЕРКИ ЦЕН ===\n")

    def run(self):
        logger.info("Бот запущен!")
        self.application.run_polling()


if __name__ == "__main__":
    bot = PriceMonitor()
    bot.run()