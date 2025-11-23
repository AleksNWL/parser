from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from database import Database
from config import MAIN_MENU, PRODUCT_VIEW

db = Database()


def get_bottom_keyboard():
    keyboard = [
        [KeyboardButton("📱 Меню"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("🛑 Остановить бота")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_products_keyboard(user_id, page=0, per_page=9, back_state=None):
    user_products = db.load_user_products(user_id)
    names = list(user_products.keys())
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

    # Добавляем кнопку "Назад" если указано предыдущее состояние
    if back_state:
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{back_state}")])

    return InlineKeyboardMarkup(keyboard)


def get_product_keyboard(user_id, product_name, notifications, back_state=MAIN_MENU):
    notify_status = "🔔 Уведомления: Вкл" if notifications.get((user_id, product_name), True) else "🔕 Уведомления: Выкл"
    keyboard = [
        [
            InlineKeyboardButton("Цена", callback_data=f"price|{product_name}"),
            InlineKeyboardButton("История", callback_data=f"history|{product_name}"),
            InlineKeyboardButton("📊 График", callback_data=f"chart|{product_name}")
        ],
        [
            InlineKeyboardButton(notify_status, callback_data=f"toggle_notify|{product_name}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_confirm|{product_name}")
        ]
    ]

    # Добавляем кнопку "Назад"
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{back_state}")])

    return InlineKeyboardMarkup(keyboard)


def get_delete_confirmation_keyboard(product_name, back_state=PRODUCT_VIEW):
    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"delete|{product_name}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"product|{product_name}")
        ]
    ]

    # Добавляем кнопку "Назад"
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{back_state}|{product_name}")])

    return InlineKeyboardMarkup(keyboard)


def get_back_button(back_state, product_name=None):
    """Универсальная кнопка назад"""
    if product_name:
        return [InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{back_state}|{product_name}")]
    else:
        return [InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{back_state}")]


def get_price_keyboard(product_name):
    """Клавиатура для страницы цен"""
    keyboard = [
        get_back_button(PRODUCT_VIEW, product_name)[0]
    ]
    return InlineKeyboardMarkup([keyboard])


def get_history_keyboard(product_name):
    """Клавиатура для страницы истории"""
    keyboard = [
        get_back_button(PRODUCT_VIEW, product_name)[0]
    ]
    return InlineKeyboardMarkup([keyboard])


def get_chart_keyboard(product_name):
    """Клавиатура для страницы графика"""
    keyboard = [
        get_back_button(PRODUCT_VIEW, product_name)[0]
    ]
    return InlineKeyboardMarkup([keyboard])