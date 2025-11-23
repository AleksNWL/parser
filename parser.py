import requests
from bs4 import BeautifulSoup
import logging
from config import MAX_PRODUCTS

logger = logging.getLogger(__name__)

class Parser:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

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