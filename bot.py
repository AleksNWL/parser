import requests
from bs4 import BeautifulSoup
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)
import sqlite3
import os
import plotly.graph_objects as go
import io
from telegram.error import Forbidden
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv()

MSK = timezone(timedelta(hours=3))

# В update_last_price_in_db:
def update_last_price_in_db(self, product, price):
    now_msk = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(self.db_path)
    with conn:
        conn.execute("""
            INSERT INTO last_prices(product, price, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(product)
            DO UPDATE SET price=excluded.price, updated_at=excluded.updated_at
        """, (product, price, now_msk))
    conn.close()

# В save_history_entry:
def save_history_entry(self, product, old_price, new_price):
    now_msk = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(self.db_path)
    with conn:
        conn.execute("""
            INSERT INTO history (product, time, old_price, new_price)
            VALUES (?, ?, ?, ?)
        """, (product, now_msk, old_price, new_price))
    conn.close()


# === Настройки ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MAX_PRODUCTS = 10
CHECK_INTERVAL = 3600

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

# === Нижняя клавиатура ===
def get_bottom_keyboard():
    keyboard = [
        [KeyboardButton("📱 Меню"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("🛑 Остановить бота")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# === Класс бота ===
class PriceMonitor:
    def __init__(self):
        self.db_path = os.path.join(os.path.dirname(__file__), 'price_bot.db')
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

        self.last_prices = {}
        self.user_states = {}
        self.notifications = {}
        self.active_users = set()

        # БД
        self.init_db()
        self.load_products_from_db()
        self.load_state_from_db()
        self.load_notifications_from_db()

        # Хэндлеры
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("test_notify", self.test_notification_command))
        self.application.add_handler(CommandHandler("notify_status", self.notification_status_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_message_handler))
        self.application.job_queue.run_repeating(self.background_price_check, interval=CHECK_INTERVAL, first=10)

    # ---------- Работа с БД ----------
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    name TEXT PRIMARY KEY,
                    url TEXT
                )
            """)
            cur = conn.execute("PRAGMA table_info(products)")
            columns = [row[1] for row in cur.fetchall()]
            if "notify" not in columns:
                conn.execute("ALTER TABLE products ADD COLUMN notify INTEGER DEFAULT 1")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS last_prices (
                    product TEXT PRIMARY KEY,
                    price INTEGER,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product TEXT,
                    time TEXT,
                    old_price INTEGER,
                    new_price INTEGER
                )
            """)
        conn.close()

    def load_products_from_db(self):
        self.PRODUCTS = {}
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT name, url, notify FROM products")
        rows = cur.fetchall()
        if not rows:
            cur.executemany("INSERT INTO products (name, url, notify) VALUES (?, ?, 1)", [
                ("Samsung S25 Ultra", "https://msk.hi-stores.ru/catalog/samsung/smartfoni/galaxy-s/galaxy-s25-ultra/"),
                ("iPhone 17", "https://msk.hi-stores.ru/catalog/iphone/iphone-17/"),
                ("iPhone 17 Pro", "https://msk.hi-stores.ru/catalog/iphone/iphone-17-pro/"),
            ])
            conn.commit()
            cur.execute("SELECT name, url, notify FROM products")
            rows = cur.fetchall()
        for name, url, notify in rows:
            self.PRODUCTS[name] = url
            self.notifications[name] = bool(notify)
        conn.close()

    def add_product_to_db(self, name, url):
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("INSERT OR REPLACE INTO products (name, url, notify) VALUES (?, ?, 1)", (name, url))
        conn.close()
        self.PRODUCTS[name] = url
        self.notifications[name] = True

    def delete_product_from_db(self, name):
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("DELETE FROM products WHERE name=?", (name,))
            conn.execute("DELETE FROM last_prices WHERE product=?", (name,))
            conn.execute("DELETE FROM history WHERE product=?", (name,))
        conn.close()
        self.PRODUCTS.pop(name, None)
        self.last_prices.pop(name, None)
        self.notifications.pop(name, None)

    def load_state_from_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT product, price FROM last_prices")
        for product, price in cur.fetchall():
            self.last_prices[product] = price
        conn.close()

    def load_notifications_from_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT name, notify FROM products")
        for name, notify in cur.fetchall():
            self.notifications[name] = bool(notify)
        conn.close()

    def toggle_notification(self, name):
        new_status = not self.notifications.get(name, True)
        self.notifications[name] = new_status
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("UPDATE products SET notify=? WHERE name=?", (1 if new_status else 0, name))
        conn.close()
        return new_status

    def update_last_price_in_db(self, product, price):
        now_msk = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("""
                INSERT INTO last_prices(product, price, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(product)
                DO UPDATE SET price=excluded.price, updated_at=excluded.updated_at
            """, (product, price, now_msk))
        conn.close()

    def save_history_entry(self, product, old_price, new_price):
        now_msk = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("""
                INSERT INTO history (product, time, old_price, new_price)
                VALUES (?, ?, ?, ?)
            """, (product, now_msk, old_price, new_price))
        conn.close()

    # ---------- Клавиатуры ----------
    def get_products_keyboard(self, page=0, per_page=9):
        names = list(self.PRODUCTS.keys())
        start = page * per_page
        end = start + per_page
        page_items = names[start:end]

        keyboard = [[InlineKeyboardButton(name, callback_data=f"product|{name}")] for name in page_items]

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page|{page - 1}"))
        if end < len(names):
            nav_buttons.append(InlineKeyboardButton("➡️ Далее", callback_data=f"page|{page + 1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)

        keyboard.append([InlineKeyboardButton("➕ Добавить смартфон", callback_data="add_product")])
        return InlineKeyboardMarkup(keyboard)

    # ---------- Парсинг ----------
    def get_phone_prices(self, url):
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            phones = []
            for card in soup.find_all("div", class_="catalog_item"):
                name_link = card.find("a", class_="dark_link")
                name = name_link.text.strip() if name_link else "Неизвестно"

                price_elem = card.find("div", class_="price") or card.find("span", class_="price_value")
                price_text = price_elem.text.strip() if price_elem else "0"
                price_digits = "".join(filter(str.isdigit, price_text))
                price = int(price_digits) if price_digits else 0

                link_elem = card.find("a", href=True)
                link = "https://msk.hi-stores.ru" + link_elem["href"] if link_elem else url

                phones.append({"name": name, "price": price, "link": link})

            phones.sort(key=lambda x: x["price"])
            return phones[:MAX_PRODUCTS]
        except Exception as e:
            logger.error(f"Ошибка парсинга: {e}")
            return []

    # ---------- Команды для тестирования уведомлений ----------
    async def test_notification_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для тестирования уведомлений"""
        user_id = update.message.from_user.id
        if user_id not in self.active_users:
            await update.message.reply_text(
                "🛑 Бот остановлен. Для возобновления нажмите /start",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("/start")]], resize_keyboard=True)
            )
            return
        chat_id = update.message.chat_id

        # Сохраняем chat_id в user_data
        context.user_data["chat_id"] = chat_id

        test_msg = "🔔 ТЕСТОВОЕ УВЕДОМЛЕНИЕ\nЭто сообщение подтверждает, что уведомления работают!"

        try:
            await context.bot.send_message(chat_id=chat_id, text=test_msg)
            logger.info(f"Тестовое уведомление отправлено пользователю {chat_id}")
            await update.message.reply_text("✅ Тестовое уведомление отправлено!")
        except Exception as e:
            logger.error(f"Ошибка отправки тестового уведомления: {e}")
            await update.message.reply_text(f"❌ Ошибка отправки: {e}")

    async def notification_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для проверки статуса уведомлений"""
        user_id = update.message.from_user.id
        if user_id not in self.active_users:
            await update.message.reply_text(
                "🛑 Бот остановлен. Для возобновления нажмите /start",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("/start")]], resize_keyboard=True)
            )
            return
        chat_id = update.message.chat_id

        status_msg = f"""
            📊 СТАТУС УВЕДОМЛЕНИЙ:
            
👤 Ваш ID: {user_id}
💬 Chat ID: {chat_id}
📝 Сохранен в user_data: {'✅' if user_id in context.application.user_data else '❌'}

Товары с уведомлениями:
"""
        for name, notified in self.notifications.items():
            status = "🔔" if notified else "🔕"
            status_msg += f"{status} {name}\n"

        await update.message.reply_text(status_msg)


    # ---------- Telegram команды ----------
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id

        # Гарантированно сохраняем chat_id
        context.user_data["chat_id"] = chat_id

        # Добавляем пользователя в активные
        self.active_users.add(user_id)

        logger.info(f"Пользователь {user_id} запустил бота, chat_id: {chat_id}")

        await update.message.reply_text(
            "📱 Выберите товар:",
            reply_markup=self.get_products_keyboard(page=0)
        )
        await update.message.reply_text(
            "Вы можете использовать кнопки ниже:",
            reply_markup=get_bottom_keyboard()
        )

    async def text_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        text = update.message.text.strip()

        # Проверка, активен ли пользователь
        if user_id not in self.active_users and text != "/start":
            await update.message.reply_text(
                "🛑 Бот остановлен. Для возобновления нажмите /start",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("/start")]], resize_keyboard=True)
            )
            return

        # Нижняя клавиатура
        if text == "📱 Меню":
            await update.message.reply_text(
                "📱 Выберите товар:",
                reply_markup=self.get_products_keyboard(page=0)
            )
            return

        elif text == "❓ Помощь":
            help_text = (
                "💡 Команды бота:\n"
                "/start - показать товары\n"
                "/status - актуальные цены\n"
                "/test_notify - тест уведомлений\n"
                "/notify_status - статус уведомлений\n\n"
                "📌 Добавление товара:\n"
                "- Название и ссылка на каталог\n"
                "- Ссылка обязательно должна содержать:\n"
                "https://msk.hi-stores.ru/catalog/\n\n"
                "Вы можете включать/выключать уведомления, смотреть историю и графики цен."
            )
            await update.message.reply_text(help_text)
            return

        elif text == "🛑 Остановить бота":
            # Убираем пользователя из активных
            self.active_users.discard(user_id)
            # Меняем клавиатуру на одну кнопку /start
            keyboard = ReplyKeyboardMarkup(
                [[KeyboardButton("/start")]], resize_keyboard=True
            )
            await update.message.reply_text(
                "🛑 Бот остановлен. Для возобновления нажмите /start",
                reply_markup=keyboard
            )

            return

        elif text == "/start":
            # Возвращаем пользователя в активные
            self.active_users.add(user_id)
            await update.message.reply_text(
                "📱 Выберите товар:",
                reply_markup=self.get_products_keyboard(page=0)
            )
            await update.message.reply_text(
                "Вы можете использовать кнопки ниже:",
                reply_markup=get_bottom_keyboard()
            )
            return

        # Добавление нового товара
        if user_id not in self.user_states:
            return
        state = self.user_states[user_id]
        if state["step"] == "await_name":
            state["name"] = text
            state["step"] = "await_url"
            await update.message.reply_text("🔗 Теперь отправьте ссылку на каталог. Начало ссылки должно быть: \nhttps://msk.hi-stores.ru/catalog/")
        elif state["step"] == "await_url":
            name = state["name"]
            url = text
            if "https://msk.hi-stores.ru/catalog/" not in url:
                await update.message.reply_text(
                    "❌ Ссылка некорректна! Она должна содержать:\nhttps://msk.hi-stores.ru/catalog/"
                )
                return
            self.add_product_to_db(name, url)
            del self.user_states[user_id]
            await update.message.reply_text(f"✅ Смартфон <b>{name}</b> добавлен!", parse_mode="HTML")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        if user_id not in self.active_users:
            await update.message.reply_text(
                "🛑 Бот остановлен. Для возобновления нажмите /start",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("/start")]], resize_keyboard=True)
            )
            return
        message = "📊 <b>АКТУАЛЬНЫЕ ЦЕНЫ</b>\n\n"
        for name, url in self.PRODUCTS.items():
            phones = self.get_phone_prices(url)
            if phones:
                message += f"✅ <b>{name}</b> — от <b>{phones[0]['price']} ₽</b>\n"
            else:
                message += f"⚠️ {name}: нет данных\n"
        await update.message.reply_text(message, parse_mode="HTML")

    async def background_price_check(self, context: ContextTypes.DEFAULT_TYPE):
        logger.info("=== НАЧАЛО ФОНОВОЙ ПРОВЕРКИ ЦЕН ===")

        # Отладочная информация о пользователях
        active_users = len(context.application.user_data)
        logger.info(f"Активных пользователей в user_data: {active_users}")

        for user_id, data in context.application.bot_data:
            chat_id = data.get("chat_id")
            logger.info(f"Пользователь {user_id}, chat_id: {chat_id}")

        for name, url in self.PRODUCTS.items():
            logger.info(f"Проверка товара: {name}")
            logger.info(f"URL: {url}")
            logger.info(f"Уведомления включены: {self.notifications.get(name, True)}")

            phones = self.get_phone_prices(url)
            if not phones:
                logger.warning(f"Не удалось получить данные для товара: {name}")
                continue

            min_price = phones[0]["price"]
            old_price = self.last_prices.get(name)
            logger.info(f"Старая цена: {old_price}, Новая цена: {min_price}")

            if old_price is None:
                logger.info(f"Первая проверка для {name}, устанавливаем цену: {min_price}")
                self.last_prices[name] = min_price
                self.update_last_price_in_db(name, min_price)
                self.save_history_entry(name, min_price, min_price)
                continue

            if min_price != old_price:
                self.save_history_entry(name, old_price, min_price)
                self.update_last_price_in_db(name, min_price)
                self.last_prices[name] = min_price

                if self.notifications.get(name, True):
                    msg = (
                        f"{'⬇️' if min_price < old_price else '⬆️'} <b>{name}</b>\n"
                        f"Было: <b>{old_price} ₽</b>\n"
                        f"Стало: <b>{min_price} ₽</b>"
                    )
                    logger.info(f"Подготовлено сообщение для отправки: {msg}")

                    for user_id, data in context.application.user_data.items():
                        if user_id not in self.active_users:
                            continue
                        chat_id = data.get("chat_id")
                        if chat_id:
                            try:
                                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                                logger.info(f"Уведомление отправлено пользователю {chat_id}")
                            except Forbidden:
                                logger.warning(f"Пользователь {chat_id} заблокировал бота, удаляем...")
                                context.application.user_data.pop(user_id, None)
                            except Exception as e:
                                logger.error(f"Ошибка отправки уведомления {chat_id}: {e}")

        logger.info("=== ЗАВЕРШЕНИЕ ФОНОВОЙ ПРОВЕРКИ ЦЕН ===\n")

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data.split("|")
        action = data[0]
        chat_id = query.message.chat.id

        def safe_edit(text=None, reply_markup=None, parse_mode="HTML"):
            async def wrapper():
                try:
                    if text is not None:
                        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
                except Exception as e:
                    logger.warning(f"Не удалось редактировать сообщение: {e}")
                    if text is not None:
                        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup,
                                                       parse_mode=parse_mode)
            return wrapper()

        # --- Добавление нового смартфона ---
        if action == "add_product":
            user_id = query.from_user.id
            self.user_states[user_id] = {"step": "await_name"}
            await query.answer("✏️ Введите название смартфона")
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🔹 Введите название нового смартфона:\n\n"
                    "Например: iPhone 17 Pro"
                )
            )
            return

        # --- Навигация страниц ---
        if action == "page":
            page = int(data[1])
            await safe_edit("📱 Выберите товар:", reply_markup=self.get_products_keyboard(page))
            return

        # --- Просмотр товара ---
        if action == "product":
            product_name = data[1]
            notify_status = "🔔 Уведомления: Вкл" if self.notifications.get(product_name, True) else "🔕 Уведомления: Выкл"
            keyboard = [
                [
                    InlineKeyboardButton("Цена", callback_data=f"price|{product_name}"),
                    InlineKeyboardButton("История", callback_data=f"history|{product_name}"),
                    InlineKeyboardButton("📊 График", callback_data=f"chart|{product_name}")
                ],
                [
                    InlineKeyboardButton(notify_status, callback_data=f"toggle_notify|{product_name}"),
                    InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_confirm|{product_name}")
                ],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]
            ]
            await safe_edit(f"Выбрано: {product_name}", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # --- Включение/выключение уведомлений ---
        if action == "toggle_notify":
            product_name = data[1]
            new_status = self.toggle_notification(product_name)
            status_text = "🔔 включены" if new_status else "🔕 выключены"
            await query.answer(f"Уведомления для {product_name} {status_text}", show_alert=True)

            notify_status = "🔔 Уведомления: Вкл" if new_status else "🔕 Уведомления: Выкл"
            keyboard = [
                [
                    InlineKeyboardButton("Цена", callback_data=f"price|{product_name}"),
                    InlineKeyboardButton("История", callback_data=f"history|{product_name}"),
                    InlineKeyboardButton("📊 График", callback_data=f"chart|{product_name}")
                ],
                [
                    InlineKeyboardButton(notify_status, callback_data=f"toggle_notify|{product_name}"),
                    InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_confirm|{product_name}")
                ],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]
            ]
            await safe_edit(reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # --- Удаление ---
        if action == "delete_confirm":
            product_name = data[1]
            keyboard = [
                [
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"delete|{product_name}"),
                    InlineKeyboardButton("❌ Отмена", callback_data=f"product|{product_name}")
                ]
            ]
            await safe_edit(f"⚠️ Удалить <b>{product_name}</b> из списка?", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        if action == "delete":
            product_name = data[1]
            self.delete_product_from_db(product_name)
            await safe_edit(f"🗑 <b>{product_name}</b> удалён.")
            await context.bot.send_message(chat_id=chat_id, text="📱 Выберите товар:",
                                           reply_markup=self.get_products_keyboard(page=0))
            return

        # --- Цена ---
        if action == "price":
            product_name = data[1]
            phones = self.get_phone_prices(self.PRODUCTS[product_name])
            if phones:
                min_price = min(phone['price'] for phone in phones)
                message = f"📊 Цены на <b>{product_name}</b>:\n\n"
                for i, phone in enumerate(phones, 1):
                    price_formatted = "{:,}".format(phone['price']).replace(",", ".")
                    arrow = " ⬇️" if phone['price'] == min_price else ""
                    message += (
                        f"{i}. <b>{phone['name']}</b>\n"
                        f"Цена: <b>{price_formatted} ₽</b>{arrow}\n"
                        f"Ссылка: <a>{phone['link']}</a>\n"
                        "──────────────\n"
                    )
                keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"product|{product_name}")]]
                await safe_edit(message, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await safe_edit("❌ Нет данных по ценам")
            return

        # --- История ---
        if action == "history":
            product_name = data[1]
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT time, old_price, new_price FROM history WHERE product=? ORDER BY id DESC LIMIT 10",
                        (product_name,))
            rows = cur.fetchall()
            conn.close()
            if rows:
                message = f"📈 История цен <b>{product_name}</b>:\n\n"
                for time_, old, new in rows:
                    arrow = "⬇️" if new < old else "⬆️"
                    message += f"{time_}: {old} ₽ → {new} ₽ {arrow}\n"
                keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"product|{product_name}")]]
                await safe_edit(message, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await safe_edit("❌ Нет истории цен")
            return

        # --- График ---
        if action == "chart":
            product_name = data[1]
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT time, new_price FROM history WHERE product=? ORDER BY id ASC", (product_name,))
            rows = cur.fetchall()
            conn.close()
            if rows:
                times, prices = zip(*rows)
                fig = go.Figure()

                fig.add_trace(go.Scatter(
                    x=times,
                    y=prices,
                    mode="lines+markers",
                    line=dict(color="#00ccff", width=3),
                    marker=dict(size=9, color="#ffaa00", line=dict(width=1, color="black")),
                    name=product_name
                ))

                fig.update_layout(
                    title=f"📊 График цен {product_name}",
                    xaxis_title="Дата",
                    yaxis_title="Цена (₽)",
                    template="plotly_dark",  # 🌓 ТЁМНАЯ ТЕМА
                    plot_bgcolor="#111111",  # Цвет фона графика
                    paper_bgcolor="#0d0d0d",  # Цвет внешнего фона
                    font=dict(color="#f2f2f2", size=14),
                    xaxis=dict(showgrid=True, gridcolor="#333333", tickangle=-45),
                    yaxis=dict(showgrid=True, gridcolor="#333333"),
                    margin=dict(l=50, r=40, t=80, b=60),
                    hovermode="x unified"
                )

                # Сохраняем график в PNG
                buf = io.BytesIO()
                fig.write_image(buf, format="png")
                buf.seek(0)

                keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"product|{product_name}")]]
                await context.bot.send_photo(chat_id=chat_id, photo=buf, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await safe_edit("❌ Нет данных для графика")
            return

        # --- Назад к списку товаров ---
        if action == "back_main":
            await safe_edit("📱 Выберите товар:", reply_markup=self.get_products_keyboard(page=0))
            return

    # --- Запуск ---
    def run(self):
        logger.info("Бот запущен!")
        self.application.run_polling()


if __name__ == "__main__":
    bot = PriceMonitor()
    bot.run()
