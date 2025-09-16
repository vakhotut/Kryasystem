# database/__init__.py
# Упрощённый экспортер — чтобы импортировать всё как from database import ...

from .core import (
    init_db, load_cache, refresh_cache, get_text, get_bot_setting,
    db_pool, close_db, get_cities_cache, get_districts_cache,
    get_products_cache, get_delivery_types_cache, get_categories_cache,
    get_subcategories_cache, get_texts_cache, get_bot_settings_cache
)

# импортируем все публичные запросы
from .queries import *
# импортируем тексты (если нужно напрямую)
from .texts import DEFAULT_TEXTS, DEFAULT_SETTINGS
