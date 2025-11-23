def format_price(price):
    """Форматирование цены с разделителями"""
    return "{:,}".format(price).replace(",", ".")

def validate_url(url):
    """Проверка корректности URL"""
    return "https://msk.hi-stores.ru/catalog/" in url