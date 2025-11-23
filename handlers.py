import logging
import io
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import plotly.graph_objects as go

from database import Database
from parser import Parser
from keyboards import (
    get_products_keyboard, get_product_keyboard, get_delete_confirmation_keyboard,
    get_bottom_keyboard, get_price_keyboard, get_history_keyboard, get_chart_keyboard
)
from config import MAIN_MENU, PRODUCT_VIEW, PRICE_VIEW, HISTORY_VIEW, ADD_PRODUCT

logger = logging.getLogger(__name__)

db = Database()
parser = Parser()


class Handlers:
    def __init__(self, price_monitor):
        self.price_monitor = price_monitor
        # Храним историю состояний пользователей: {user_id: [state1, state2, ...]}
        self.user_navigation_history = {}

    def _add_to_history(self, user_id, state):
        """Добавляем состояние в историю навигации"""
        if user_id not in self.user_navigation_history:
            self.user_navigation_history[user_id] = []
        self.user_navigation_history[user_id].append(state)

        # Ограничиваем историю 10 состояниями
        if len(self.user_navigation_history[user_id]) > 10:
            self.user_navigation_history[user_id] = self.user_navigation_history[user_id][-10:]

    def _get_previous_state(self, user_id):
        """Получаем предыдущее состояние"""
        if user_id in self.user_navigation_history and len(self.user_navigation_history[user_id]) > 1:
            return self.user_navigation_history[user_id].pop()  # Удаляем текущее состояние
        return MAIN_MENU

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id

        self.price_monitor.user_chat_ids[user_id] = chat_id
        self.price_monitor.active_users.add(user_id)
        # Сбрасываем историю навигации
        self.user_navigation_history[user_id] = [MAIN_MENU]

        await update.message.reply_text(
            "📱 Выберите товар:",
            reply_markup=get_products_keyboard(user_id, page=0)
        )
        await update.message.reply_text(
            "Вы можете использовать кнопки ниже:",
            reply_markup=get_bottom_keyboard()
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        if user_id not in self.price_monitor.active_users:
            await update.message.reply_text(
                "🛑 Бот остановлен. Для возобновления нажмите /start",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("/start")]], resize_keyboard=True)
            )
            return

        user_products = db.load_user_products(user_id)
        message = "📊 <b>АКТУАЛЬНЫЕ ЦЕНЫ</b>\n\n"
        for name, url in user_products.items():
            phones = parser.get_phone_prices(url)
            if phones:
                message += f"✅ <b>{name}</b> — от <b>{phones[0]['price']} ₽</b>\n"
            else:
                message += f"⚠️ {name}: нет данных\n"
        await update.message.reply_text(message, parse_mode="HTML")

    async def test_notification_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для тестирования уведомлений"""
        user_id = update.message.from_user.id
        if user_id not in self.price_monitor.active_users:
            await update.message.reply_text("🛑 Бот остановлен. Для возобновления нажмите /start")
            return

        test_msg = "🔔 ТЕСТОВОЕ УВЕДОМЛЕНИЕ\nЭто сообщение подтверждает, что уведомления работают!"
        await update.message.reply_text(test_msg)

    async def notification_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для проверки статуса уведомлений"""
        user_id = update.message.from_user.id
        if user_id not in self.price_monitor.active_users:
            await update.message.reply_text("🛑 Бот остановлен. Для возобновления нажмите /start")
            return

        user_products = db.load_user_products(user_id)
        status_msg = f"📊 СТАТУС УВЕДОМЛЕНИЙ для пользователя {user_id}:\n\n"

        for name in user_products.keys():
            status = "🔔" if self.price_monitor.notifications.get((user_id, name), True) else "🔕"
            status_msg += f"{status} {name}\n"

        await update.message.reply_text(status_msg)

    async def text_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id
        text = update.message.text.strip()

        if user_id not in self.price_monitor.active_users and text != "/start":
            await update.message.reply_text(
                "🛑 Бот остановлен. Для возобновления нажмите /start",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("/start")]], resize_keyboard=True)
            )
            return

        if text == "📱 Меню":
            self.user_navigation_history[user_id] = [MAIN_MENU]
            await update.message.reply_text(
                "📱 Выберите товар:",
                reply_markup=get_products_keyboard(user_id, page=0)
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
            self.price_monitor.active_users.discard(user_id)
            keyboard = ReplyKeyboardMarkup([[KeyboardButton("/start")]], resize_keyboard=True)
            await update.message.reply_text("🛑 Бот остановлен. Для возобновления нажмите /start", reply_markup=keyboard)
            return
        elif text == "/start":
            self.price_monitor.active_users.add(user_id)
            self.price_monitor.user_chat_ids[user_id] = chat_id
            self.user_navigation_history[user_id] = [MAIN_MENU]
            await update.message.reply_text(
                "📱 Выберите товар:",
                reply_markup=get_products_keyboard(user_id, page=0)
            )
            await update.message.reply_text(
                "Вы можете использовать кнопки ниже:",
                reply_markup=get_bottom_keyboard()
            )
            return

        # Добавление нового товара
        if user_id not in self.price_monitor.user_states:
            return
        state = self.price_monitor.user_states[user_id]
        if state["step"] == "await_name":
            state["name"] = text
            state["step"] = "await_url"
            await update.message.reply_text(
                "🔗 Теперь отправьте ссылку на каталог. Начало ссылки должно быть: \nhttps://msk.hi-stores.ru/catalog/"
            )
        elif state["step"] == "await_url":
            name = state["name"]
            url = text
            if "https://msk.hi-stores.ru/catalog/" not in url:
                await update.message.reply_text(
                    "❌ Ссылка некорректна! Она должна содержать:\nhttps://msk.hi-stores.ru/catalog/"
                )
                return
            db.add_product(user_id, name, url)
            del self.price_monitor.user_states[user_id]

            # Вместо простого сообщения показываем страницу товара
            await update.message.reply_text(
                f"✅ Смартфон <b>{name}</b> добавлен! Вот страница товара:",
                parse_mode="HTML"
            )
            # Показываем страницу товара с кнопками
            await update.message.reply_text(
                f"Выбрано: {name}",
                reply_markup=get_product_keyboard(user_id, name, self.price_monitor.notifications)
            )
            self._add_to_history(user_id, PRODUCT_VIEW)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()
        data = query.data.split("|")
        action = data[0]

        chat_id = query.message.chat.id

        async def safe_edit(text=None, reply_markup=None, parse_mode="HTML"):
            try:
                if text is not None:
                    await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception as e:
                logger.warning(f"Не удалось редактировать сообщение: {e}")
                if text is not None:
                    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup,
                                                   parse_mode=parse_mode)

        # --- Обработка кнопки Назад ---
        if action == "back":
            previous_state = data[1] if len(data) > 1 else MAIN_MENU
            product_name = data[2] if len(data) > 2 else None

            if previous_state == MAIN_MENU:
                await safe_edit("📱 Выберите товар:", reply_markup=get_products_keyboard(user_id, page=0))
                self._add_to_history(user_id, MAIN_MENU)
            elif previous_state == PRODUCT_VIEW and product_name:
                await safe_edit(
                    f"Выбрано: {product_name}",
                    reply_markup=get_product_keyboard(user_id, product_name, self.price_monitor.notifications)
                )
                self._add_to_history(user_id, PRODUCT_VIEW)
            return

        # --- Добавление нового смартфона ---
        if action == "add_product":
            self.price_monitor.user_states[user_id] = {"step": "await_name"}
            self._add_to_history(user_id, ADD_PRODUCT)
            await query.answer("✏️ Введите название смартфона")
            await context.bot.send_message(
                chat_id=chat_id,
                text="🔹 Введите название нового смартфона:\n\nНапример: iPhone 17 Pro"
            )
            return

        # --- Навигация страниц ---
        if action == "page":
            page = int(data[1])
            await safe_edit("📱 Выберите товар:", reply_markup=get_products_keyboard(user_id, page))
            self._add_to_history(user_id, MAIN_MENU)
            return

        # --- Просмотр товара ---
        if action == "product":
            product_name = data[1]
            await safe_edit(
                f"Выбрано: {product_name}",
                reply_markup=get_product_keyboard(user_id, product_name, self.price_monitor.notifications)
            )
            self._add_to_history(user_id, PRODUCT_VIEW)
            return

        # --- Включение/выключение уведомлений ---
        if action == "toggle_notify":
            product_name = data[1]
            new_status = db.toggle_notification(user_id, product_name)
            self.price_monitor.notifications[(user_id, product_name)] = new_status
            status_text = "🔔 включены" if new_status else "🔕 выключены"
            await query.answer(f"Уведомления для {product_name} {status_text}", show_alert=True)

            await safe_edit(
                reply_markup=get_product_keyboard(user_id, product_name, self.price_monitor.notifications)
            )
            return

        # --- Удаление ---
        if action == "delete_confirm":
            product_name = data[1]
            await safe_edit(
                f"⚠️ Удалить <b>{product_name}</b> из списка?",
                reply_markup=get_delete_confirmation_keyboard(product_name)
            )
            return

        if action == "delete":
            product_name = data[1]
            db.delete_product(user_id, product_name)
            # Удаляем из внутренних структур
            self.price_monitor.last_prices.pop((user_id, product_name), None)
            self.price_monitor.notifications.pop((user_id, product_name), None)

            await safe_edit(f"🗑 <b>{product_name}</b> удалён.")
            await context.bot.send_message(
                chat_id=chat_id,
                text="📱 Выберите товар:",
                reply_markup=get_products_keyboard(user_id, page=0)
            )
            self._add_to_history(user_id, MAIN_MENU)
            return

        # --- Цена ---
        if action == "price":
            product_name = data[1]
            user_products = db.load_user_products(user_id)
            url = user_products.get(product_name)
            if url:
                phones = parser.get_phone_prices(url)
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
                    await safe_edit(message, reply_markup=get_price_keyboard(product_name))
                    self._add_to_history(user_id, PRICE_VIEW)
                else:
                    await safe_edit("❌ Нет данных по ценам", reply_markup=get_price_keyboard(product_name))
            else:
                await safe_edit("❌ Товар не найден", reply_markup=get_price_keyboard(product_name))
            return

        # --- История ---
        if action == "history":
            product_name = data[1]
            rows = db.get_price_history(user_id, product_name)
            if rows:
                message = f"📈 История цен <b>{product_name}</b>:\n\n"
                for time_, old, new in rows:
                    arrow = "⬇️" if new < old else "⬆️"
                    message += f"{time_}: {old} ₽ → {new} ₽ {arrow}\n"
                await safe_edit(message, reply_markup=get_history_keyboard(product_name))
                self._add_to_history(user_id, HISTORY_VIEW)
            else:
                await safe_edit("❌ Нет истории цен", reply_markup=get_history_keyboard(product_name))
            return

        # --- График ---
        if action == "chart":
            product_name = data[1]
            rows = db.get_chart_data(user_id, product_name)
            if rows:
                times, prices = zip(*rows)
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=times, y=prices, mode="lines+markers",
                    line=dict(color="#00ccff", width=3),
                    marker=dict(size=9, color="#ffaa00", line=dict(width=1, color="black")),
                    name=product_name
                ))
                fig.update_layout(
                    title=f"📊 График цен {product_name}",
                    xaxis_title="Дата", yaxis_title="Цена (₽)",
                    template="plotly_dark",
                    plot_bgcolor="#111111", paper_bgcolor="#0d0d0d",
                    font=dict(color="#f2f2f2", size=14),
                    xaxis=dict(showgrid=True, gridcolor="#333333", tickangle=-45),
                    yaxis=dict(showgrid=True, gridcolor="#333333"),
                    margin=dict(l=50, r=40, t=80, b=60),
                    hovermode="x unified"
                )
                buf = io.BytesIO()
                fig.write_image(buf, format="png")
                buf.seek(0)
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=buf,
                    caption=f"📊 График цен для {product_name}",
                    reply_markup=get_chart_keyboard(product_name)
                )
                self._add_to_history(user_id, "chart_view")
            else:
                await safe_edit("❌ Нет данных для графика", reply_markup=get_chart_keyboard(product_name))
            return