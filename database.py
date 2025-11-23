import sqlite3
import logging
from datetime import datetime
from config import DB_PATH, MSK

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.db_path = DB_PATH
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    notify INTEGER DEFAULT 1,
                    created_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    UNIQUE(user_id, name)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS last_prices (
                    user_id INTEGER,
                    product TEXT,
                    price INTEGER,
                    updated_at TEXT,
                    PRIMARY KEY (user_id, product),
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    product TEXT,
                    time TEXT,
                    old_price INTEGER,
                    new_price INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
        conn.close()

    def load_user_products(self, user_id):
        """Загрузка товаров конкретного пользователя"""
        user_products = {}
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT name, url, notify FROM products WHERE user_id=?", (user_id,))
        for name, url, notify in cur.fetchall():
            user_products[name] = url
        conn.close()
        return user_products

    def add_product(self, user_id, name, url):
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("""
                INSERT OR IGNORE INTO users (user_id, created_at) 
                VALUES (?, ?)
            """, (user_id, datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")))

            conn.execute("""
                INSERT INTO products (user_id, name, url, notify, created_at)
                VALUES (?, ?, ?, 1, ?)
            """, (user_id, name, url, datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")))
        conn.close()

    def delete_product(self, user_id, name):
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("DELETE FROM products WHERE user_id=? AND name=?", (user_id, name))
            conn.execute("DELETE FROM last_prices WHERE user_id=? AND product=?", (user_id, name))
            conn.execute("DELETE FROM history WHERE user_id=? AND product=?", (user_id, name))
        conn.close()

    def update_last_price(self, user_id, product, price):
        now_msk = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("""
                INSERT INTO last_prices(user_id, product, price, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, product)
                DO UPDATE SET price=excluded.price, updated_at=excluded.updated_at
            """, (user_id, product, price, now_msk))
        conn.close()

    def save_history_entry(self, user_id, product, old_price, new_price):
        now_msk = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("""
                INSERT INTO history(user_id, product, time, old_price, new_price)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, product, now_msk, old_price, new_price))
        conn.close()

    def toggle_notification(self, user_id, name):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT notify FROM products WHERE user_id=? AND name=?", (user_id, name))
        result = cur.fetchone()
        if result:
            new_status = 0 if result[0] else 1
            conn.execute("UPDATE products SET notify=? WHERE user_id=? AND name=?", (new_status, user_id, name))
            conn.commit()
        conn.close()
        return bool(new_status)

    def get_price_history(self, user_id, product, limit=10):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT time, old_price, new_price FROM history WHERE user_id=? AND product=? ORDER BY id DESC LIMIT ?",
            (user_id, product, limit))
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_chart_data(self, user_id, product):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT time, new_price FROM history WHERE user_id=? AND product=? ORDER BY id ASC",
                    (user_id, product))
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_last_prices(self):
        """Загрузка последних цен из БД"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        last_prices = {}
        try:
            cur.execute("SELECT user_id, product, price FROM last_prices")
            for user_id, product, price in cur.fetchall():
                last_prices[(user_id, product)] = price
        except sqlite3.OperationalError as e:
            logger.warning(f"Не удалось загрузить last_prices: {e}")
        finally:
            conn.close()
        return last_prices