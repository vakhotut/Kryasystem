import asyncpg
from asyncpg.pool import Pool
from datetime import datetime
import logging
import uuid
import traceback
from functools import lru_cache, wraps
from typing import Dict, List, Any, Optional, Tuple
import time

logger = logging.getLogger(__name__)

# Глобальная переменная для пула соединений
db_pool: Pool = None

# Белый список разрешенных колонок для обновления
ALLOWED_USER_COLUMNS = {
    'username', 'first_name', 'language', 'captcha_passed',
    'ban_until', 'failed_payments', 'purchase_count', 'discount', 'balance',
    'referrer_id', 'referral_code', 'referral_count', 'earned_from_referrals'
}

# Глобальные кэши
texts_cache = {}
cities_cache = []
districts_cache = {}
products_cache = {}
delivery_types_cache = []
categories_cache = []
subcategories_cache = {}
bot_settings_cache = {}

# Декоратор для кэширования с временем жизни
def timed_lru_cache(seconds: int, maxsize: int = 128):
    def wrapper_cache(func):
        func = lru_cache(maxsize=maxsize)(func)
        func.lifetime = time.time() + seconds
        func.expiration = func.lifetime
        
        @wraps(func)
        def wrapped_func(*args, **kwargs):
            if time.time() >= func.expiration:
                func.cache_clear()
                func.expiration = time.time() + func.lifetime
            return func(*args, **kwargs)
        return wrapped_func
    return wrapper_cache

# Инициализация базы данных
async def init_db(database_url):
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(database_url, ssl='require', min_size=1, max_size=10)
        logger.info("Database pool created successfully")
        
        async with db_pool.acquire() as conn:
            # Таблица пользователей
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                language TEXT DEFAULT 'ru',
                captcha_passed INTEGER DEFAULT 0,
                ban_until TIMESTAMP NULL,
                failed_payments INTEGER DEFAULT 0,
                purchase_count INTEGER DEFAULT 0,
                discount INTEGER DEFAULT 0,
                balance REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                referrer_id BIGINT NULL,
                referral_code TEXT UNIQUE,
                referral_count INTEGER DEFAULT 0,
                earned_from_referrals REAL DEFAULT 0.0,
                FOREIGN KEY (referrer_id) REFERENCES users (user_id)
            )
            ''')
            
            # Проверяем и добавляем недостающие колонки в users
            user_columns_to_check = ['referral_code', 'referral_count', 'earned_from_referrals', 'referrer_id']
            for column in user_columns_to_check:
                try:
                    await conn.execute(f"SELECT {column} FROM users LIMIT 1")
                except Exception:
                    if column == 'referrer_id':
                        await conn.execute('ALTER TABLE users ADD COLUMN referrer_id BIGINT REFERENCES users(user_id)')
                    elif column in ['referral_count', 'earned_from_referrals']:
                        await conn.execute(f'ALTER TABLE users ADD COLUMN {column} INTEGER DEFAULT 0')
                    else:
                        await conn.execute(f'ALTER TABLE users ADD COLUMN {column} TEXT')
                    logger.info(f"Added {column} column to users table")
            
            # Таблица транзакций
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                amount REAL,
                currency TEXT,
                status TEXT,
                order_id TEXT,
                payment_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                product_info TEXT,
                invoice_uuid TEXT,
                crypto_address TEXT,
                crypto_amount REAL,
                product_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            ''')
            
            # Проверяем существование столбцов и добавляем их, если нет
            columns_to_check = [
                'invoice_uuid', 'crypto_address', 'crypto_amount', 'product_id'
            ]
            
            for column in columns_to_check:
                try:
                    await conn.execute(f"SELECT {column} FROM transactions LIMIT 1")
                except Exception:
                    if column == 'product_id':
                        await conn.execute('ALTER TABLE transactions ADD COLUMN product_id INTEGER')
                    elif column == 'crypto_amount':
                        await conn.execute('ALTER TABLE transactions ADD COLUMN crypto_amount REAL')
                    else:
                        await conn.execute(f'ALTER TABLE transactions ADD COLUMN {column} TEXT')
                    logger.info(f"Added {column} column to transactions table")
            
            # Таблица покупки
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product TEXT,
                price REAL,
                district TEXT,
                delivery_type TEXT,
                purchase_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'completed',
                product_id INTEGER,
                image_url TEXT,
                description TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            ''')
            
            # Проверяем и добавляем недостающие колонки в purchases
            purchase_columns_to_check = ['product_id', 'image_url', 'description']
            for column in purchase_columns_to_check:
                try:
                    await conn.execute(f"SELECT {column} FROM purchases LIMIT 1")
                except Exception:
                    if column == 'product_id':
                        await conn.execute('ALTER TABLE purchases ADD COLUMN product_id INTEGER')
                    else:
                        await conn.execute(f'ALTER TABLE purchases ADD COLUMN {column} TEXT')
                    logger.info(f"Added {column} column to purchases table")
            
            # Новая таблица для текстов
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS texts (
                id SERIAL PRIMARY KEY,
                lang TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                UNIQUE(lang, key)
            )
            ''')
            
            # Новая таблица для городов
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS cities (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            )
            ''')
            
            # Новая таблица для районов
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS districts (
                id SERIAL PRIMARY KEY,
                city_id INTEGER REFERENCES cities(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                UNIQUE(city_id, name)
            )
            ''')
            
            # Новая таблица для категорий товаров
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Новая таблица для подкатегорий товаров
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS subcategories (
                id SERIAL PRIMARY KEY,
                category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                quantity INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category_id, name)
            )
            ''')
            
            # Новая таблица для типов доставки
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS delivery_types (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            )
            ''')
            
            # Новая таблица для товары
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                uuid TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                image_url TEXT,
                category_id INTEGER REFERENCES categories(id),
                subcategory_id INTEGER REFERENCES subcategories(id),
                city_id INTEGER REFERENCES cities(id),
                district_id INTEGER REFERENCES districts(id),
                delivery_type_id INTEGER REFERENCES delivery_types(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Добавляем недостающие столбцы, если они еще не существуют
            columns_to_check = [
                'category_id', 'district_id', 'delivery_type_id', 'uuid', 'description', 'subcategory_id'
            ]
            
            for column in columns_to_check:
                try:
                    await conn.execute(f"SELECT {column} FROM products LIMIT 1")
                except Exception:
                    if column == 'uuid':
                        await conn.execute('ALTER TABLE products ADD COLUMN uuid TEXT UNIQUE')
                    elif column == 'description':
                        await conn.execute('ALTER TABLE products ADD COLUMN description TEXT')
                    elif column == 'subcategory_id':
                        await conn.execute('ALTER TABLE products ADD COLUMN subcategory_id INTEGER REFERENCES subcategories(id)')
                    else:
                        ref_table = column.split('_')[0] + 's'
                        await conn.execute(f'ALTER TABLE products ADD COLUMN {column} INTEGER REFERENCES {ref_table}(id)')
                    logger.info(f"Added {column} column to products table")
            
            # Таблица проданных товаров
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS sold_products (
                id SERIAL PRIMARY KEY,
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                subcategory_id INTEGER REFERENCES subcategories(id),
                user_id BIGINT REFERENCES users(user_id),
                quantity INTEGER DEFAULT 1,
                sold_price REAL NOT NULL,
                sold_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                purchase_id INTEGER REFERENCES purchases(id)
            )
            ''')
            
            # Добавляем столбец subcategory_id если его нет
            try:
                await conn.execute("SELECT subcategory_id FROM sold_products LIMIT 1")
            except Exception:
                await conn.execute('ALTER TABLE sold_products ADD COLUMN subcategory_id INTEGER REFERENCES subcategories(id)')
                logger.info("Added subcategory_id column to sold_products table")
            
            # Таблица для настроек бота
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                id SERIAL PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL
            )
            ''')
            
            # Таблица для хранения статистики использования API
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS explorer_api_stats (
                id SERIAL PRIMARY KEY,
                explorer_name TEXT NOT NULL,
                total_requests INTEGER DEFAULT 0,
                successful_requests INTEGER DEFAULT 0,
                last_used TIMESTAMP NULL,
                daily_limit INTEGER DEFAULT 1000,
                remaining_daily_requests INTEGER DEFAULT 1000,
                last_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(explorer_name)
            )
            ''')
            
            # Проверяем и добавляем столбец last_reset если его нет
            try:
                await conn.execute("SELECT last_reset FROM explorer_api_stats LIMIT 1")
            except Exception:
                await conn.execute('ALTER TABLE explorer_api_stats ADD COLUMN last_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
                logger.info("Added last_reset column to explorer_api_stats table")
            
            # Таблица для сгенерированных адресов
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS generated_addresses (
                id SERIAL PRIMARY KEY,
                address TEXT UNIQUE NOT NULL,
                index INTEGER NOT NULL,
                label TEXT,
                balance REAL DEFAULT 0.0,
                transaction_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Заполняем таблицы начальными данными, если они пустые
            await init_default_data(conn)
            
        return db_pool
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        logger.error(traceback.format_exc())
        raise

# Функция для заполнения начальных данных
async def init_default_data(conn):
    try:
        # Всегда обновляем тексты, даже если они уже существуют
        default_texts = {
            'ru': {
                'welcome': 'Добро пожаловать!',
                'captcha': 'Для входа решите каптчу: {code}\nВведите 5 цифр:',
                'captcha_enter': 'Введите 5 цифр с изображения:',
                'captcha_failed': 'Неверная каптча! Попробуйте снова:',
                'language_selected': 'Язык установлен: Русский',
                'main_menu': "👤 Имя: {name}\n📛 Юзернейм: @{username}\n🛒 Покупок: {purchases}\n🎯 Скидка: {discount}%\n💰 Баланс: {balance}$",
                'select_city': 'Выберите город:',
                'select_category': 'Выберите категорию:',
                'select_subcategory': 'Выберите подкатегорию:',
                'select_district': 'Выберите район:',
                'select_delivery': 'Выберите тип доставки:',
                'order_summary': "Информация о заказе:\n📦 Товар: {product}\n💵 Стоимость: {price}$\n🏙 Район: {district}\n🚚 Тип доставки: {delivery_type}\n\nВсё верно?",
                'select_crypto': 'Выберите криптовалюту для оплата:',
                'payment_instructions': "Оплатите {amount} {currency} на адрес:\n`{payment_address}`\n\nОтсканируйте QR-код для оплаты:\nПосле подтверждения 3 сетевых подтверждений товар будет выслан автоматически.",
                'payment_timeout': 'Время оплата истекло. Заказ отменен.',
                'payment_success': 'Оплата получена! Ваш товар:\n\n{product_image}',
                'balance': 'Ваш баланс: {balance}$',
                'balance_add': 'Введите сумму для пополнения баланса в $:',
                'balance_add_success': 'Баланс пополнен на {amount}$. Текущий баланс: {balance}$',
                'support': 'По всем вопросам обращайтесь к @support_username',
                'bonuses': 'Бонусная система:\n- За каждую 5-ю покупку скидка 10%\n- Пригласи друга и получи 50$ на баланс',
                'rules': 'Правила:\n1. Не сообщайте никому данные о заказе\n2. Оплата только в течение 60 минут\n3. При нарушении правил - бан',
                'reviews': 'Наши отзывы: @reviews_channel',
                'error': 'Произошла ошибка. Попробуйте позже.',
                'ban_message': 'Вы забанены на 24 часа из-за 3 неудачных попыток оплаты.',
                'back': '⬅️ Назад',
                'main_menu_button': '🏠 Главное меню',
                'last_order': 'Информация о последнем заказе',
                'no_orders': 'У вас еще не было заказов',
                'main_menu_description': '''Добро пожаловать в магазин!

Это телеграмм бот для быстрых покупки. 🛒 Так же есть официальный магазин Mega, нажимайте перейти и выбирайте среди огромного ассортимента! 🪏

❗️ Мы соблюдаем полную конфиденциальность наших клиентов. Мусора бляди! 🤙🏼💪''',
                'balance_instructions': '''Ваш баланс: {balance}$

Инструкция по пополнению баланса:
Русский: https://telegra.ph/RU-Kak-popolnit-balans-cherez-Litecoin-LTC-06-15
English: https://telegra.ph/EN-How-to-Top-Up-Balance-via-Litecoin-LTC-06-15
ქартули: https://telegra.ph/KA-როგორ-შევავსოთ-ბალანსი-Litecoin-ით-LTC-06-15''',
                'balance_topup_info': '''💳 Пополнение баланса

❗️ Важная информация:
• Минимальная сумма пополнения: $1
• Адрес кошелька резервируется на 30 минут
• Все пополнения на этот адрес будут зачисленны на ваш баланс
• После истечения времени адрес освобождается''',
                'active_invoice': '''💳 Активный инвойс

📝 Адрес для оплаты: `{crypto_address}`
💎 Сумма к оплате: {crypto_amount} LTC
💰 Сумма в USD: ${amount}

⏱ Действительно до: {expires_time}
❗️ Осталось времени: {time_left}

⚠️ Важно:
• Отправьте точную сумму на указанный адрес
• После 3 подтверждений сети товар будет отправлен
• При отмене или истечении времени - +1 неудачная попытка
• 3 неудачные попытки - бан на 24 часа''',
                'purchase_invoice': '''💳 Оплата заказа

📦 Товар: {product}
📝 Адрес для оплаты: `{crypto_address}`
💎 Сумма к оплате: {crypto_amount} LTC
💰 Сумма в USD: ${amount}

⏱ Действительно до: {expires_time}
❗️ Осталось времени: {time_left}

⚠️ Важно:
• Отправьте точную сумму на указанный адрес
• После 3 подтверждений сети товар будет отправлен
• При отмене или истечении времени - +1 неудачная попытка
• 3 неудачные попытки - бан на 24 часа''',
                'invoice_time_left': '⏱ До отмены инвойса осталось: {time_left}',
                'invoice_cancelled': '❌ Инвойс отменен. Неудачных попыток: {failed_count}/3',
                'invoice_expired': '⏰ Время инвойса истекло. Неудачных попыток: {failed_count}/3',
                'almost_banned': '⚠️ Внимание! После еще {remaining} неудачных попыток вы будете забанены на 24 часа!',
                'product_out_of_stock': '❌ Товар временно отсутствует',
                'product_reserved': '✅ Товар забронирован',
                'product_released': '✅ Товар возвращен в продажу'
            },
            'en': {
                'welcome': 'Welcome!',
                'captcha': 'To enter, solve the captcha: {code}\nEnter 5 digits:',
                'captcha_enter': 'Enter 5 digits from the image:',
                'captcha_failed': 'Invalid captcha! Try again:',
                'language_selected': 'Language set: English',
                'main_menu': "👤 Name: {name}\n📛 Username: @{username}\n🛒 Purchases: {purchases}\n🎯 Discount: {discount}%\n💰 Balance: {balance}$",
                'select_city': 'Select city:',
                'select_category': 'Select category:',
                'select_subcategory': 'Select subcategory:',
                'select_district': 'Select district:',
                'select_delivery': 'Select delivery type:',
                'order_summary': "Order information:\n📦 Product: {product}\n💵 Price: {price}$\n🏙 District: {district}\n🚚 Delivery type: {delivery_type}\n\nIs everything correct?",
                'select_crypto': 'Select cryptocurrency for payment:',
                'payment_instructions': "Pay {amount} {currency} to address:\n`{payment_address}`\n\nOr scan QR-code:\nAfter 3 network confirmations, the product will be sent automatically.",
                'payment_timeout': 'Payment time has expired. Order canceled.',
                'payment_success': 'Payment received! Your product:\n\n{product_image}',
                'balance': 'Your balance: {balance}$',
                'balance_add': 'Enter the amount to top up your balance in $:',
                'balance_add_success': 'Balance topped up by {amount}$. Current balance: {balance}$',
                'support': 'For all questions contact @support_username',
                'bonuses': 'Bonus system:\n- 10% discount for every 5th purchase\n- Invite a friend and get 50$ on your balance',
                'rules': 'Rules:\n1. Do not share order information with anyone\n2. Payment only within 60 minutes\n3. Ban for breaking the rules',
                'reviews': 'Our reviews: @reviews_channel',
                'error': 'An error occurred. Please try again later.',
                'ban_message': 'You are banned for 24 hours due to 3 failed payment attempts.',
                'back': '⬅️ Back',
                'main_menu_button': '🏠 Main Menu',
                'last_order': 'Information about last order',
                'no_orders': 'You have no orders yet',
                'main_menu_description': '''Welcome to the store!

This is a telegram bot for quick purchases. 🛒 There is also an official Mega store, click go and choose from a huge assortment! 🪏

❗️ We maintain complete confidentiality of our customers. Pig cops! 🤙🏼💪''',
                'balance_instructions': '''Your balance: {balance}$

Balance top-up instructions:
Russian: https://telegra.ph/RU-Kak-popolnit-balans-cherez-Litecoin-LTC-06-15
English: https://telegra.ph/EN-How-to-Top-Up-Balance-via-Litecoin-LTC-06-15
Georgian: https://telegra.ph/KA-როგორ-შევავსოთ-ბალანსი-Litecoin-ით-LTC-06-15''',
                'balance_topup_info': '''💳 Balance top-up

❗️ Important information:
• Minimum top-up amount: $1
• Wallet address is reserved for 30 minutes
• All top-ups to this address will be credited to your balance
• After the time expires, the address is released''',
                'active_invoice': '''💳 Active Invoice

📝 Payment address: `{crypto_address}`
💎 Amount to pay: {crypto_amount} LTC
💰 Amount in USD: ${amount}

⏱ Valid until: {expires_time}
❗️ Time left: {time_left}

⚠️ Important:
• Send the exact amount to the specified address
• After 3 network confirmations the product will be sent
• On cancellation or timeout - +1 failed attempt
• 3 failed attempts - 24 hour ban''',
                'purchase_invoice': '''💳 Order Payment

📦 Product: {product}
📝 Payment address: `{crypto_address}`
💎 Amount to pay: {crypto_amount} LTC
💰 Amount in USD: ${amount}

⏱ Valid until: {expires_time}
❗️ Time left: {time_left}

⚠️ Important:
• Send the exact amount to the specified address
• After 3 network confirmations the product will be sent
• On cancellation or timeout - +1 failed attempt
• 3 failed attempts - 24 hour ban''',
                'invoice_time_left': '⏱ Time until invoice cancellation: {time_left}',
                'invoice_cancelled': '❌ Invoice cancelled. Failed attempts: {failed_count}/3',
                'invoice_expired': '⏰ Invoice expired. Failed attempts: {failed_count}/3',
                'almost_banned': '⚠️ Warning! After {remaining} more failed attempts you will be banned for 24 hours!',
                'product_out_of_stock': '❌ Product temporarily out of stock',
                'product_reserved': '✅ Product reserved',
                'product_released': '✅ Product returned to stock'
            },
            'ka': {
                'welcome': 'კეთილი იყოს თქვენი მობრძანება!',
                'captcha': 'შესასვლელად გადაწყვიტეთ captcha: {code}\nშეიყვანეთ 5 ციფრი:',
                'captcha_enter': 'შეიყვანეთ 5 ციფრი სურათიდან:',
                'captcha_failed': 'არასწორი captcha! სცადეთ თავიდან:',
                'language_selected': 'ენა დაყენებულია: ქართული',
                'main_menu': "👤 სახელი: {name}\n📛 მომხმარებლის სახელი: @{username}\n🛒 ყიდვები: {purchases}\n🎯 ფასდაკლება: {discount}%\n💰 ბალანსი: {balance}$",
                'select_city': 'აირჩიეთ ქალაქი:',
                'select_category': 'აირჩიეთ კატეგორია:',
                'select_subcategory': 'აირჩიეთ ქვეკატეგორია:',
                'select_district': 'აირჩიეთ რაიონი:',
                'select_delivery': 'აირჩიეთ მიწოდების ტიპი:',
                'order_summary': "შეკვეთის ინფორმაცია:\n📦 პროდუქტი: {product}\n💵 ფასი: {price}$\n🏙 რაიონი: {district}\n🚚 მიწოდების ტიპი: {delivery_type}\n\nყველაფერი სწორია?",
                'select_crypto': 'აირჩიეთ კრიპტოვალუტა გადასახდელად:',
                'payment_instructions': "გადაიხადეთ {amount} {currency} მისამართზე:\n`{payment_address}`\n\nან სკანირება QR-კოდი:\n3 ქსელური დადასტურების შემდეგ პროდუქტი გამოგეგზავნებათ ავტომატურად.",
                'payment_timeout': 'გადახდის დრო ამოიწურა. შეკვეთა გაუქმებულია.',
                'payment_success': 'გადახდა მიღებულია! თქვენი პროდუქტი:\n\n{product_image}',
                'balance': 'თქვენი ბალანსი: {balance}$',
                'balance_add': 'შეიყვანეთ ბალანსის შევსების რაოდენობა $:',
                'balance_add_success': 'ბალანსი შეივსო {amount}$-ით. მიმდინარე ბალანსი: {balance}$',
                'support': 'ყველა კითხვისთვის დაუკავშირდით @support_username',
                'bonuses': 'ბონუს სისტემა:\n- ყოველ მე-5 ყიდვაზე 10% ფასდაკლება\n- მოიწვიე მეგობარი და მიიღე 50$ ბალანსზე',
                'rules': 'წესები:\n1. არავის არ შეახოთ შეკვეთის ინფორმაცია\n2. გადახდა მხოლოდ 60 წუთის განმავლობაში\n3. წესების დარღვევაზე - ბანი',
                'reviews': 'ჩვენი მიმოხილვები: @reviews_channel',
                'error': 'მოხდა შეცდომა. სცადეთ მოგვიანებით.',
                'ban_message': '3 წარუმატებელი გადახდის მცდელობის გამო თქვენ დაბლოკილი ხართ 24 საათის განმავლობაში.',
                'back': '⬅️ უკან',
                'main_menu_button': '🏠 მთავარი მენიუ',
                'last_order': 'ბოლო შეკვეთის ინფორმაცია',
                'no_orders': 'ჯერ არ გაქვთ შეკვეთები',
                'main_menu_description': '''მაღაზიაში მოგესალმებით!

ეს არის ტელეგრამ ბოტი სწრაფი შესყიდვებისთვის. 🛒 ასევე არის ოფიციალური Mega მაღაზია, დააჭირეთ გადასვლას და აირჩიეთ უზარმაზარი ასორტიმენტიდან! 🪏

❗️ ჩვენ ვიცავთ ჩვენი კლიენტების სრულ კონფიდენციალურობას. ღორის პოლიციელები! 🤙🏼💪''',
                'balance_instructions': '''თქვენი ბალანსი: {balance}$

ბალანსის შევსების ინსტრუქცია:
Русский: https://telegra.ph/RU-Kak-popolnit-balans-cherez-Litecoin-LTC-06-15
English: https://telegra.ph/EN-How-to-Top-Up-Balance-via-Litecoin-LTC-06-15
ქართული: https://telegra.ph/KA-როგორ-შევავსოთ-ბალანსი-Litecoin-ით-LTC-06-15''',
                'balance_topup_info': '''💳 ბალანსის შევსება

❗️ მნიშვნელოვანი ინფორმაცია:
• მინიმალური შევსების რაოდენობა: $1
• საფულის მისამართი იყიდება 30 წუთის განმავლობაში
• ყველა შევსება ამ მისამართზე ჩაირიცხება თქვენს ბალანსზე
• დროის ამოწურვის შემდეგ მისამართი გათავისუფლდება''',
                'active_invoice': '''💳 აქტიური ინვოისი

📝 გადახდის მისამართი: `{crypto_address}`
💎 გადასახდელი რაოდენობა: {crypto_amount} LTC
💰 რაოდენობა USD-ში: ${amount}

⏱ მოქმედებს: {expires_time}
❗️ დარჩენილი დრო: {time_left}

⚠️ მნიშვნელოვანი:
• გადაიხადეთ ზუსტი რაოდენობა მითითებულ მისამართზე
• 3 ქსელური დადასტურების შემდეგ პროდუქტი გაიგზავნება
• გაუქმების ან დროის ამოწურვის შემთხვევაში - +1 წარუმატებელი მცდელობა
• 3 წარუმატებელი მცდელობა - 24 საათიანი ბანი''',
                'purchase_invoice': '''💳 შეკვეთის გადახდა

📦 პროდუქტი: {product}
📝 გადახდის მისამართი: `{crypto_address}`
💎 გადასახდელი რაოდენობა: {crypto_amount} LTC
💰 რაოდენობა USD-ში: ${amount}

⏱ მოქმედებს: {expires_time}
❗️ დარჩენილი დრო: {time_left}

⚠️ მნიშვნელოვანი:
• გადაიხადეთ ზუსტი რაოდენობა მითითებულ მისამართზე
• 3 ქსელური დადასტურების შემდეგ პროდუქტი გაიგზავნება
• გაუქმების ან დროის ამოწურვის შემთხვევაში - +1 წარუმატებელი მცდელობა
• 3 წარუ�муატებელი მცდელობა - 24 საათიანი ბანი''',
                'invoice_time_left': '⏱ ინვოისის გაუქმებამდე დარჩა: {time_left}',
                'invoice_cancelled': '❌ ინვოისი გაუქმებულია. წარუმატებელი მცდელობები: {failed_count}/3',
                'invoice_expired': '⏰ ინვოისის დრო ამოიწურა. წარუმატებელი მცდელობები: {failed_count}/3',
                'almost_banned': '⚠️ გაფრთხილება! კიდევ {remaining} წარუმატებელი მცდელობის შემდეგ დაბლოკილი იქნებით 24 საათის განმავლობაში!',
                'product_out_of_stock': '❌ პროდუქტი დროებით არ არის მარაგში',
                'product_reserved': '✅ პროდუქტი დაჯავშნულია',
                'product_released': '✅ პროდუქტი დაბრუნდა მარაგში'
            }
        }
        
        for lang, translations in default_texts.items():
            for key, value in translations.items():
                await conn.execute('''
                INSERT INTO texts (lang, key, value)
                VALUES ($1, $2, $3)
                ON CONFLICT (lang, key) DO UPDATE SET value = EXCLUDED.value
                ''', lang, key, value)
        
        # Добавляем настройки бота по умолчанию
        default_settings = {
            'main_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
            'balance_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
            'category_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
            'subcategory_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
            'district_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
            'delivery_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
            'confirmation_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
            'rules_link': "https://t.me/your_rules",
            'operator_link': "https://t.me/your_operator",
            'support_link': "https://t.me/your_support",
            'channel_link': "https://t.me/your_channel",
            'reviews_link': "https://t.me/your_reviews",
            'website_link': "https://yourwebsite.com",
            'personal_bot_link': "https://t.me/your_bot"
        }
        
        for key, value in default_settings.items():
            await conn.execute('''
            INSERT INTO bot_settings (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            ''', key, value)
        
        # Добавляем начальные данные для API
        apis = ['blockchair', 'nownodes', 'sochain', 'coingecko', 'binance', 'okx', 'kraken']
        for api in apis:
            await conn.execute('''
            INSERT INTO explorer_api_stats (explorer_name, total_requests, successful_requests, daily_limit, remaining_daily_requests, last_reset)
            VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
            ON CONFLICT (explorer_name) DO NOTHING
            ''', api, 0, 0, 1000, 1000)
        
        # Проверяем и добавляем города
        cities_count = await conn.fetchval('SELECT COUNT(*) FROM cities')
        if cities_count == 0:
            cities = ['Тбилиси', 'Гори', 'Кутаиси', 'Батуми']
            for city in cities:
                city_id = await conn.fetchval('''
                INSERT INTO cities (name) VALUES ($1) 
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                ''', city)
                
                # Добавляем районы для каждого города
                if city == 'Тбилиси':
                    districts = ['Церетели', 'Центр', 'Сабуртало']
                else:
                    districts = ['Центр', 'Западный', 'Восточный']
                    
                for district in districts:
                    await conn.execute('''
                    INSERT INTO districts (city_id, name)
                    VALUES ($1, $2)
                    ON CONFLICT (city_id, name) DO NOTHING
                    ''', city_id, district)
                
                # Добавляем категории товаров
                categories = ['Мефедрон', 'Амфетамин', 'Кокаин', 'Гашиш']
                for category in categories:
                    category_id = await conn.fetchval('''
                    INSERT INTO categories (name) VALUES ($1)
                    ON CONFLICT (name) DO NOTHING
                    RETURNING id
                    ''', category)
                    
                    # Добавляем подкатегории для каждой категории
                    if category == 'Мефедрон':
                        subcategories = [
                            ('0.5 г', 10),
                            ('1.0 г', 5),
                            ('Золотой 0.5 г', 3)
                        ]
                    elif category == 'Амфетамин':
                        subcategories = [
                            ('0.3 г Красный', 8),
                            ('0.5 г Белый', 6)
                        ]
                    else:
                        subcategories = [
                            ('0.5 г', 5),
                            ('1.0 г', 3)
                        ]
                    
                    for sub_name, quantity in subcategories:
                        subcategory_id = await conn.fetchval('''
                        INSERT INTO subcategories (category_id, name, quantity)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (category_id, name) DO NOTHING
                        RETURNING id
                        ''', category_id, sub_name, quantity)
                
                # Добавляем типы доставки
                delivery_types = ['Подъезд', 'Прикоп', 'Магнит', 'Во дворах']
                for delivery_type in delivery_types:
                    await conn.execute('''
                    INSERT INTO delivery_types (name) VALUES ($1)
                    ON CONFLICT (name) DO NOTHING
                    ''', delivery_type)
                
                # Добавляем товары для каждого города
                if city == 'Тбилиси':
                    products = [
                        ('0.5 меф', 'Высококачественный мефедрон', 35, 'https://example.com/image1.jpg', 'Мефедрон', '0.5 г', 'Центр', 'Подъезд'),
                        ('1.0 меф', 'Высококачественный мефедрон', 70, 'https://example.com/image2.jpg', 'Мефедрон', '1.0 г', 'Центр', 'Подъезд'),
                        ('0.5 меф золотой', 'Премиум мефедрон', 50, 'https://example.com/image3.jpg', 'Мефедрон', 'Золотой 0.5 г', 'Центр', 'Подъезд'),
                        ('0.3 красный', 'Красный фосфор', 35, 'https://example.com/image4.jpg', 'Амфетамин', '0.3 г Красный', 'Центр', 'Подъезд')
                    ]
                else:
                    products = [
                        ('0.5 меф', 'Высококачественный мефедрон', 35, 'https://example.com/image1.jpg', 'Мефедрон', '0.5 г', 'Центр', 'Подъезд'),
                        ('1.0 меф', 'Высококачественный мефедрон', 70, 'https://example.com/image2.jpg', 'Мефедрон', '1.0 г', 'Центр', 'Подъезд')
                    ]
                    
                # Получаем ID категорий, подкатегорий, районов и типов доставки
                categories_dict = {}
                categories_rows = await conn.fetch('SELECT * FROM categories')
                for row in categories_rows:
                    categories_dict[row['name']] = row['id']
                    
                subcategories_dict = {}
                subcategories_rows = await conn.fetch('SELECT * FROM subcategories')
                for row in subcategories_rows:
                    subcategories_dict[(row['category_id'], row['name'])] = row['id']
                    
                districts_dict = {}
                districts_rows = await conn.fetch('SELECT * FROM districts WHERE city_id = $1', city_id)
                for row in districts_rows:
                    districts_dict[row['name']] = row['id']
                    
                delivery_types_dict = {}
                delivery_types_rows = await conn.fetch('SELECT * FROM delivery_types')
                for row in delivery_types_rows:
                    delivery_types_dict[row['name']] = row['id']
                    
                # Добавляем товары
                for product_name, description, price, image_url, category_name, subcategory_name, district_name, delivery_type_name in products:
                    category_id = categories_dict.get(category_name)
                    subcategory_id = subcategories_dict.get((category_id, subcategory_name))
                    district_id = districts_dict.get(district_name)
                    delivery_type_id = delivery_types_dict.get(delivery_type_name)
                    
                    if category_id and subcategory_id and district_id and delivery_type_id:
                        product_uuid = str(uuid.uuid4())
                        await conn.execute('''
                        INSERT INTO products (uuid, name, description, price, image_url, category_id, subcategory_id, city_id, district_id, delivery_type_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        ON CONFLICT (uuid) DO NOTHING
                        ''', product_uuid, product_name, description, price, image_url, category_id, subcategory_id, city_id, district_id, delivery_type_id)
        
        logger.info("Default data initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing default data: {e}")
        logger.error(traceback.format_exc())
        raise

# Функция для загрузки данных в кэш
async def load_cache():
    global texts_cache, cities_cache, districts_cache, products_cache, delivery_types_cache, categories_cache, subcategories_cache, bot_settings_cache
    
    try:
        async with db_pool.acquire() as conn:
            # Загрузка текстов
            texts_cache = {}
            for lang in ['ru', 'en', 'ka']:
                rows = await conn.fetch('SELECT key, value FROM texts WHERE lang = $1', lang)
                texts_cache[lang] = {row['key']: row['value'] for row in rows}
            
            # Загрузка городов
            cities_rows = await conn.fetch('SELECT * FROM cities ORDER BY name')
            cities_cache = [dict(row) for row in cities_rows]
            
            # Загрузка районов
            districts_cache = {}
            for city in cities_cache:
                districts = await conn.fetch('SELECT * FROM districts WHERE city_id = $1 ORDER BY name', city['id'])
                districts_cache[city['name']] = [district['name'] for district in districts]
            
            # Загрузка категорий
            categories_rows = await conn.fetch('SELECT * FROM categories ORDER BY name')
            categories_cache = [dict(row) for row in categories_rows]
            
            # Загрузка подкатегорий
            subcategories_rows = await conn.fetch('SELECT * FROM subcategories ORDER BY name')
            subcategories_cache = {}
            for row in subcategories_rows:
                if row['category_id'] not in subcategories_cache:
                    subcategories_cache[row['category_id']] = []
                subcategories_cache[row['category_id']].append(dict(row))
            
            # Загрузка товары
            products_cache = {}
            for city in cities_cache:
                products = await conn.fetch('''
                    SELECT p.id, p.name, p.description, p.price, p.image_url, 
                           c.name as category_name, s.name as subcategory_name, s.quantity
                    FROM products p 
                    LEFT JOIN categories c ON p.category_id = c.id
                    LEFT JOIN subcategories s ON p.subcategory_id = s.id
                    WHERE p.city_id = $1 
                    ORDER BY p.name
                ''', city['id'])
                products_cache[city['name']] = {
                    product['name']: {
                        'id': product['id'],
                        'description': product['description'],
                        'price': product['price'], 
                        'image': product['image_url'],
                        'category': product['category_name'],
                        'subcategory': product['subcategory_name'],
                        'quantity': product['quantity']
                    } for product in products
                }
            
            # Загрузка типов доставки
            delivery_types = await conn.fetch('SELECT * FROM delivery_types ORDER by name')
            delivery_types_cache = [delivery_type['name'] for delivery_type in delivery_types]
            
            # Загрузка настроек бота
            settings_rows = await conn.fetch('SELECT * FROM bot_settings')
            bot_settings_cache = {row['key']: row['value'] for row in settings_rows}
            
        logger.info("Кэш успешно загружен")
    except Exception as e:
        logger.error(f"Ошибка загрузки кэша: {e}")
        logger.error(traceback.format_exc())
        raise

# Функция для получения текста
def get_text(lang, key, **kwargs):
    try:
        if lang not in texts_cache:
            logger.warning(f"Language {lang} not found in cache, using 'ru'")
            lang = 'ru'
        if key not in texts_cache[lang]:
            logger.warning(f"Text key {key} not found for language {lang}. Available keys: {list(texts_cache[lang].keys())}")
            return f"Текст не найден: {key}"
        
        text = texts_cache[lang][key]
        try:
            if kwargs:
                text = text.format(**kwargs)
            return text
        except KeyError as e:
            logger.error(f"Ошибка форматирования текста: {e}, ключ: {key}, аргументы: {kwargs}")
            return text
    except Exception as e:
        logger.error(f"Error in get_text: {e}")
        return "Ошибка загрузки текста"

# Функция для получения настройки бота
def get_bot_setting(key):
    try:
        return bot_settings_cache.get(key, "")
    except Exception as e:
        logger.error(f"Error getting bot setting {key}: {e}")
        return ""

# Универсальная функция выполнения запросов с обработкой ошибок
async def db_execute(query, *args, timeout=2.0):
    """Выполняет запрос с обработкой ошибок и логированием"""
    start_time = time.time()
    try:
        async with db_pool.acquire() as conn:
            result = await conn.execute(query, *args)
            duration = time.time() - start_time
            if duration > timeout:
                logger.warning(f"Slow query executed in {duration:.2f}s: {query}")
            return result
    except asyncpg.PostgresError as e:
        logger.error(f"Database error in query '{query}': {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in query '{query}': {e}")
        raise

# Функции для работы с базой данных
@timed_lru_cache(300)  # Кэширование на 5 минут
async def get_user(user_id):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        return None

async def update_user(user_id, **kwargs):
    try:
        # Фильтруем только разрешенные колонки
        valid_updates = {k: v for k, v in kwargs.items() if k in ALLOWED_USER_COLUMNS}
        if not valid_updates:
            return
            
        # Формируем SET часть запроса
        set_clause = ', '.join([f"{k} = ${i+1}" for i, k in enumerate(valid_updates.keys())])
        values = list(valid_updates.values())
        values.append(user_id)  # Добавляем user_id в конец для WHERE
        
        query = f'UPDATE users SET {set_clause} WHERE user_id = ${len(values)}'
        await db_execute(query, *values)
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}")

async def add_transaction(user_id, amount, currency, order_id, payment_url, expires_at, product_info, invoice_uuid, crypto_address=None, crypto_amount=None, product_id=None):
    try:
        # Преобразуем crypto_amount в строку для сохранения точности
        crypto_amount_str = str(crypto_amount) if crypto_amount is not None else None
        
        await db_execute('''
        INSERT INTO transactions (user_id, amount, currency, status, order_id, payment_url, expires_at, product_info, invoice_uuid, crypto_address, crypto_amount, product_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ''', user_id, amount, currency, 'pending', order_id, payment_url, expires_at, product_info, invoice_uuid, crypto_address, crypto_amount_str, product_id)
    except Exception as e:
        logger.error(f"Error adding transaction for user {user_id}: {e}")

async def add_purchase(user_id, product, price, district, delivery_type, product_id=None, image_url=None, description=None):
    try:
        async with db_pool.acquire() as conn:
            # Преобразуем product_id в строку если он не None
            product_id_str = str(product_id) if product_id is not None else None
            
            # Атомарное обновление счетчика покупки и возврат ID покупки
            purchase_id = await conn.fetchval('''
            INSERT INTO purchases (user_id, product, price, district, delivery_type, product_id, image_url, description)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            ''', user_id, product, price, district, delivery_type, product_id_str, image_url, description)
            
            # Обновляем счетчик покупки пользователя
            await conn.execute('''
            UPDATE users SET purchase_count = purchase_count + 1 WHERE user_id = $1
            ''', user_id)
            
            return purchase_id
    except Exception as e:
        logger.error(f"Error adding purchase for user {user_id}: {e}")
        return None

async def add_sold_product(product_id, subcategory_id, user_id, quantity, sold_price, purchase_id):
    try:
        async with db_pool.acquire() as conn:
            # Уменьшаем количество в подкатегории
            await conn.execute('''
            UPDATE subcategories 
            SET quantity = quantity - $1 
            WHERE id = $2 AND quantity >= $1
            ''', quantity, subcategory_id)
            
            # Добавляем запись о проданном товаре
            await conn.execute('''
            INSERT INTO sold_products (product_id, subcategory_id, user_id, quantity, sold_price, purchase_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ''', product_id, subcategory_id, user_id, quantity, sold_price, purchase_id)
            
            # Проверяем, осталось ли количество в подкатегории
            current_quantity = await conn.fetchval('SELECT quantity FROM subcategories WHERE id = $1', subcategory_id)
            
            # Если количество стало 0, удаляем все товары этой подкатегории
            if current_quantity <= 0:
                await conn.execute('DELETE FROM products WHERE subcategory_id = $1', subcategory_id)
            
            return True
    except Exception as e:
        logger.error(f"Error adding sold product for user {user_id}: {e}")
        return False

async def get_pending_transactions():
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetch('SELECT * FROM transactions WHERE status = $1 AND expires_at > NOW()', 'pending')
    except Exception as e:
        logger.error(f"Error getting pending transactions: {e}")
        return []

async def update_transaction_status(order_id, status):
    try:
        await db_execute('UPDATE transactions SET status = $1 WHERE order_id = $2', status, order_id)
    except Exception as e:
        logger.error(f"Error updating transaction status for order {order_id}: {e}")

async def update_transaction_status_by_uuid(invoice_uuid, status):
    try:
        await db_execute('UPDATE transactions SET status = $1 WHERE invoice_uuid = $2', status, invoice_uuid)
    except Exception as e:
        logger.error(f"Error updating transaction status for invoice {invoice_uuid}: {e}")

async def get_last_order(user_id):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchrow('SELECT * FROM purchases WHERE user_id = $1 ORDER BY purchase_time DESC LIMIT 1', user_id)
    except Exception as e:
        logger.error(f"Error getting last order for user {user_id}: {e}")
        return None

# Функция для получения истории заказов пользователя
async def get_user_orders(user_id, limit=10):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetch(
                'SELECT * FROM purchases WHERE user_id = $1 ORDER BY purchase_time DESC LIMIT $2',
                user_id, limit
            )
    except Exception as e:
        logger.error(f"Error getting user orders: {e}")
        return []

# Функция для проверки бана пользователя
async def is_banned(user_id):
    try:
        user = await get_user(user_id)
        if user and user['ban_until']:
            try:
                ban_until = user['ban_until']
                if isinstance(ban_until, str):
                    ban_until = datetime.strptime(ban_until, '%Y-%m-%d %H:%M:%S')
                if ban_until > datetime.now():
                    return True
            except ValueError:
                return False
        return False
    except Exception as e:
        logger.error(f"Error checking ban status for user {user_id}: {e}")
        return False

# Функция для проверки активного инвойса на пополнение баланса
async def has_active_invoice(user_id):
    try:
        async with db_pool.acquire() as conn:
            active_invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending' AND expires_at > NOW()",
                user_id
            )
            return active_invoice is not None
    except Exception as e:
        logger.error(f"Error checking active invoice for user {user_id}: {e}")
        return False

# Функции-геттеры для доступа к актуальным кэшам
def get_cities_cache():
    return cities_cache

def get_districts_cache():
    return districts_cache

def get_products_cache():
    return products_cache

def get_delivery_types_cache():
    return delivery_types_cache

def get_categories_cache():
    return categories_cache

def get_subcategories_cache():
    return subcategories_cache

def get_texts_cache():
    return texts_cache

def get_bot_settings_cache():
    return bot_settings_cache

# Функции для работы с проданными товарами
async def get_sold_products(page=1, per_page=20):
    try:
        offset = (page - 1) * per_page
        async with db_pool.acquire() as conn:
            sold_products = await conn.fetch('''
                SELECT sp.*, p.name as product_name, s.name as subcategory_name, 
                       u.user_id, u.username, s.quantity as remaining_quantity
                FROM sold_products sp
                LEFT JOIN products p ON sp.product_id = p.id
                LEFT JOIN subcategories s ON sp.subcategory_id = s.id
                LEFT JOIN users u ON sp.user_id = u.user_id
                ORDER BY sp.sold_at DESC
                LIMIT $1 OFFSET $2
            ''', per_page, offset)
            
            total = await conn.fetchval('SELECT COUNT(*) FROM sold_products')
            return sold_products, total
    except Exception as e:
        logger.error(f"Error getting sold products: {e}")
        return [], 0

# Функции для работы с количеством товаров
async def get_subcategory_quantity(subcategory_id):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchval('SELECT quantity FROM subcategories WHERE id = $1', subcategory_id)
    except Exception as e:
        logger.error(f"Error getting subcategory quantity: {e}")
        return 0

async def reserve_subcategory(subcategory_id, quantity=1):
    try:
        async with db_pool.acquire() as conn:
            result = await conn.execute('''
                UPDATE subcategories 
                SET quantity = quantity - $1 
                WHERE id = $2 AND quantity >= $1
            ''', quantity, subcategory_id)
            
            # Проверяем, была ли обновлена хотя бы одна строка
            return "UPDATE 1" in str(result)
    except Exception as e:
        logger.error(f"Error reserving subcategory: {e}")
        return False

async def release_subcategory(subcategory_id, quantity=1):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE subcategories 
                SET quantity = quantity + $1 
                WHERE id = $2
            ''', quantity, subcategory_id)
            return True
    except Exception as e:
        logger.error(f"Error releasing subcategory: {e}")
        return False

# Добавляем недостающие функции
async def get_product_quantity(product_id):
    try:
        async with db_pool.acquire() as conn:
            # Получаем количество товара через его подкатегорию
            return await conn.fetchval('''
                SELECT s.quantity 
                FROM products p
                JOIN subcategories s ON p.subcategory_id = s.id
                WHERE p.id = $1
            ''', product_id)
    except Exception as e:
        logger.error(f"Error getting product quantity: {e}")
        return 0

async def reserve_product(product_id):
    """Резервирование товара (уменьшение количества на 1)"""
    try:
        async with db_pool.acquire() as conn:
            # Получаем subcategory_id продукта
            subcategory_id = await conn.fetchval(
                'SELECT subcategory_id FROM products WHERE id = $1',
                product_id
            )
            if subcategory_id:
                return await reserve_subcategory(subcategory_id, 1)
            return False
    except Exception as e:
        logger.error(f"Error reserving product: {e}")
        return False

async def release_product(product_id):
    """Освобождение товара (увеличение количества на 1)"""
    try:
        async with db_pool.acquire() as conn:
            # Получаем subcategory_id продукта
            subcategory_id = await conn.fetchval(
                'SELECT subcategory_id FROM products WHERE id = $1',
                product_id
            )
            if subcategory_id:
                return await release_subcategory(subcategory_id, 1)
            return False
    except Exception as e:
        logger.error(f"Error releasing product: {e}")
        return False

async def get_product_by_name_city(product_name, city_name):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchrow('''
                SELECT p.*, s.quantity as subcategory_quantity
                FROM products p
                JOIN cities c ON p.city_id = c.id
                JOIN subcategories s ON p.subcategory_id = s.id
                WHERE p.name = $1 AND c.name = $2
            ''', product_name, city_name)
    except Exception as e:
        logger.error(f"Error getting product: {e}")
        return None

async def get_product_by_id(product_id):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchrow('''
                SELECT p.*, s.quantity as subcategory_quantity
                FROM products p
                JOIN subcategories s ON p.subcategory_id = s.id
                WHERE p.id = $1
            ''', product_id)
    except Exception as e:
        logger.error(f"Error getting product by ID: {e}")
        return None

async def get_purchase_with_product(purchase_id):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchrow('''
                SELECT p.*, pr.image_url, pr.description 
                FROM purchases p
                LEFT JOIN products pr ON p.product_id = pr.id
                WHERE p.id = $1
            ''', purchase_id)
    except Exception as e:
        logger.error(f"Error getting purchase with product: {e}")
        return None

# Функции для работы с настройками бота
async def update_bot_setting(key, value):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO bot_settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            ''', key, value)
            
            # Обновляем кэш
            bot_settings_cache[key] = value
            
        return True
    except Exception as e:
        logger.error(f"Error updating bot setting {key}: {e}")
        return False

async def get_all_bot_settings():
    try:
        async with db_pool.acquire() as conn:
            settings = await conn.fetch('SELECT * FROM bot_settings')
            return {row['key']: row['value'] for row in settings}
    except Exception as e:
        logger.error(f"Error getting all bot settings: {e}")
        return {}

# Функции для работы с API лимитами
async def increment_api_request(api_name, success=True):
    try:
        async with db_pool.acquire() as conn:
            if success:
                await conn.execute('''
                UPDATE explorer_api_stats 
                SET total_requests = total_requests + 1,
                    successful_requests = successful_requests + 1,
                    remaining_daily_requests = remaining_daily_requests - 1,
                    last_used = CURRENT_TIMESTAMP
                WHERE explorer_name = $1
                ''', api_name)
            else:
                await conn.execute('''
                UPDATE explorer_api_stats 
                SET total_requests = total_requests + 1,
                    last_used = CURRENT_TIMESTAMP
                WHERE explorer_name = $1
                ''', api_name)
    except Exception as e:
        logger.error(f"Error incrementing API request count for {api_name}: {e}")

async def get_api_limits():
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetch('SELECT * FROM explorer_api_stats')
    except Exception as e:
        logger.error(f"Error getting API limits: {e}")
        return []

async def reset_api_limits():
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
            UPDATE explorer_api_stats 
            SET remaining_daily_requests = daily_limit, 
                last_reset = CURRENT_TIMESTAMP
            WHERE last_reset < CURRENT_DATE OR last_reset IS NULL
            ''')
    except Exception as e:
        logger.error(f"Error resetting API limits: {e}")

# Функции для работы с сгенерированными адресами
async def add_generated_address(address, index, label=None):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO generated_addresses (address, index, label)
                VALUES ($1, $2, $3)
                ON CONFLICT (address) DO UPDATE SET label = EXCLUDED.label
            ''', address, index, label)
            return True
    except Exception as e:
        logger.error(f"Error adding generated address: {e}")
        return False

async def update_address_balance(address, balance, transaction_count):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE generated_addresses 
                SET balance = $1, transaction_count = $2 
                WHERE address = $3
            ''', balance, transaction_count, address)
            return True
    except Exception as e:
        logger.error(f"Error updating address balance: {e}")
        return False

async def get_generated_addresses(limit=50, offset=0):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetch('''
                SELECT * FROM generated_addresses 
                ORDER BY created_at DESC 
                LIMIT $1 OFFSET $2
            ''', limit, offset)
    except Exception as e:
        logger.error(f"Error getting generated addresses: {e}")
        return []

async def update_api_limits(explorer_name, daily_limit):
    """Обновление дневного лимита для API"""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE explorer_api_stats 
                SET daily_limit = $1,
                    remaining_daily_requests = LEAST(remaining_daily_requests, $1),
                    updated_at = NOW()
                WHERE explorer_name = $2
            ''', daily_limit, explorer_name)
        
        return True
    except Exception as e:
        logging.error(f"Error updating API limits for {explorer_name}: {e}")
        return False

async def reset_daily_limits():
    """Ежедневный сброс лимитов API"""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE explorer_api_stats 
                SET remaining_daily_requests = daily_limit,
                    last_reset = NOW()
                WHERE last_reset < CURRENT_DATE OR last_reset IS NULL
            ''')
        
        logging.info("Daily API limits reset successfully")
        return True
    except Exception as e:
        logging.error(f"Error resetting daily limits: {e}")
        return False

async def get_api_config():
    """Получение конфигурации API из базы данных"""
    try:
        async with db_pool.acquire() as conn:
            settings = await conn.fetch("SELECT key, value FROM bot_settings WHERE key LIKE '%api%'")
            
            api_config = {}
            for setting in settings:
                if 'blockchair' in setting['key'].lower():
                    api_config['blockchair_key'] = setting['value']
                elif 'nownodes' in setting['key'].lower():
                    api_config['nownodes_key'] = setting['value']
                elif 'coingecko' in setting['key'].lower():
                    api_config['coingecko_key'] = setting['value']
            
            return api_config
    except Exception as e:
        logging.error(f"Error getting API config: {e}")
        return {}

async def update_api_config(key, value):
    """Обновление конфигурации API в базе данных"""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO bot_settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            ''', key, value)
        
        logging.info(f"API config updated: {key}")
        return True
    except Exception as e:
        logging.error(f"Error updating API config: {e}")
        return False

# Функции для проверки доступности районов и типов доставки
async def is_district_available(city_name, district_name):
    try:
        async with db_pool.acquire() as conn:
            # Проверяем есть ли доступные товары в этом районе
            count = await conn.fetchval('''
                SELECT COUNT(*) 
                FROM products p
                JOIN cities c ON p.city_id = c.id
                JOIN districts d ON p.district_id = d.id
                JOIN subcategories s ON p.subcategory_id = s.id
                WHERE c.name = $1 AND d.name = $2 AND s.quantity > 0
            ''', city_name, district_name)
            return count > 0
    except Exception as e:
        logger.error(f"Error checking district availability: {e}")
        return False

async def is_delivery_type_available(delivery_type_name):
    try:
        async with db_pool.acquire() as conn:
            # Проверяем есть ли доступные товары с этим типом доставки
            count = await conn.fetchval('''
                SELECT COUNT(*) 
                FROM products p
                JOIN delivery_types dt ON p.delivery_type_id = dt.id
                JOIN subcategories s ON p.subcategory_id = s.id
                WHERE dt.name = $1 AND s.quantity > 0
            ''', delivery_type_name)
            return count > 0
    except Exception as e:
        logger.error(f"Error checking delivery type availability: {e}")
        return False

# Функции для работы с подкатегориями
async def get_subcategories_by_category(category_id):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetch('SELECT * FROM subcategories WHERE category_id = $1 ORDER BY name', category_id)
    except Exception as e:
        logger.error(f"Error getting subcategories for category {category_id}: {e}")
        return []

async def add_subcategory(category_id, name, quantity=0):
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchval('''
                INSERT INTO subcategories (category_id, name, quantity)
                VALUES ($1, $2, $3)
                RETURNING id
            ''', category_id, name, quantity)
    except Exception as e:
        logger.error(f"Error adding subcategory: {e}")
        return None

async def update_subcategory(subcategory_id, name=None, quantity=None):
    try:
        updates = []
        values = []
        
        if name is not None:
            updates.append("name = $1")
            values.append(name)
        
        if quantity is not None:
            updates.append("quantity = $2")
            values.append(quantity)
        
        if not updates:
            return True
            
        values.append(subcategory_id)
        
        async with db_pool.acquire() as conn:
            await conn.execute(f'''
                UPDATE subcategories 
                SET {', '.join(updates)}
                WHERE id = ${len(values)}
            ''', *values)
            
            return True
    except Exception as e:
        logger.error(f"Error updating subcategory {subcategory_id}: {e}")
        return False

async def delete_subcategory(subcategory_id):
    try:
        async with db_pool.acquire() as conn:
            # Удаляем все товары этой подкатегории
            await conn.execute('DELETE FROM products WHERE subcategory_id = $1', subcategory_id)
            
            # Удаляем подкатегорию
            await conn.execute('DELETE FROM subcategories WHERE id = $1', subcategory_id)
            
            return True
    except Exception as e:
        logger.error(f"Error deleting subcategory {subcategory_id}: {e}")
        return False

# Функции для работы с реферальной системой
async def add_user_referral(user_id, referrer_code=None):
    try:
        async with db_pool.acquire() as conn:
            # Если есть реферальный код, находим того кто пригласил
            if referrer_code:
                referrer = await conn.fetchrow(
                    'SELECT user_id FROM users WHERE referral_code = $1', 
                    referrer_code
                )
                if referrer:
                    await conn.execute(
                        'UPDATE users SET referrer_id = $1 WHERE user_id = $2',
                        referrer['user_id'], user_id
                    )
                    # Начисляем 1$ пригласившему сразу
                    await conn.execute(
                        'UPDATE users SET balance = balance + 1, earned_from_referrals = earned_from_referrals + 1, referral_count = referral_count + 1 WHERE user_id = $1',
                        referrer['user_id']
                    )
                    return True
        return False
    except Exception as e:
        logger.error(f"Error adding referral: {e}")
        return False

async def generate_referral_code(user_id):
    try:
        code = str(uuid.uuid4())[:8].upper()
        async with db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE users SET referral_code = $1 WHERE user_id = $2',
                code, user_id
            )
        return code
    except Exception as e:
        logger.error(f"Error generating referral code: {e}")
        return None

# Функция для массового обновления пользователей
async def bulk_update_users(updates):
    """Массовое обновление пользователей"""
    try:
        async with db_pool.acquire() as conn:
            await conn.executemany('''
                UPDATE users SET 
                username = $2, first_name = $3 
                WHERE user_id = $1
            ''', [(u['user_id'], u['username'], u['first_name']) for u in updates])
    except Exception as e:
        logger.error(f"Error in bulk update: {e}")

# Функция для безопасного выполнения запросов
async def safe_query(conn, query, params):
    """Безопасное выполнение запроса с проверкой на инъекции"""
    dangerous_patterns = [';', '--', '/*', '*/', 'xp_', 'exec', 'union']
    if any(pattern in query.lower() for pattern in dangerous_patterns):
        raise ValueError("Potential SQL injection detected")
    return await conn.execute(query, *params)

# Функция для получения статистики использования API
async def get_api_usage_stats():
    """Получение статистики использования API"""
    try:
        async with db_pool.acquire() as conn:
            stats = await conn.fetch('''
                SELECT explorer_name, total_requests, successful_requests, 
                       daily_limit, remaining_daily_requests, last_used, last_reset
                FROM explorer_api_stats 
                ORDER BY total_requests DESC
            ''')
            return stats
    except Exception as e:
        logger.error(f"Error getting API usage stats: {e}")
        return []

# Функция для получения информации о пользователе с расширенной статистикой
async def get_user_extended_stats(user_id):
    """Получение расширенной статистики пользователя"""
    try:
        async with db_pool.acquire() as conn:
            user_stats = await conn.fetchrow('''
                SELECT 
                    u.*,
                    COUNT(p.id) as total_purchases,
                    COALESCE(SUM(p.price), 0) as total_spent,
                    COUNT(DISTINCT t.id) as total_transactions,
                    COUNT(CASE WHEN t.status = 'completed' THEN 1 END) as completed_transactions,
                    COUNT(CASE WHEN t.status = 'pending' THEN 1 END) as pending_transactions,
                    COUNT(CASE WHEN t.status = 'failed' THEN 1 END) as failed_transactions
                FROM users u
                LEFT JOIN purchases p ON u.user_id = p.user_id
                LEFT JOIN transactions t ON u.user_id = t.user_id
                WHERE u.user_id = $1
                GROUP BY u.user_id
            ''', user_id)
            return user_stats
    except Exception as e:
        logger.error(f"Error getting user extended stats: {e}")
        return None

# Функция для получения популярных товаров
async def get_popular_products(limit=10):
    """Получение самых популярных товаров"""
    try:
        async with db_pool.acquire() as conn:
            popular_products = await conn.fetch('''
                SELECT 
                    p.name,
                    p.description,
                    p.price,
                    c.name as city_name,
                    COUNT(sp.id) as sales_count,
                    SUM(sp.sold_price) as total_revenue
                FROM sold_products sp
                JOIN products p ON sp.product_id = p.id
                JOIN cities c ON p.city_id = c.id
                GROUP BY p.id, p.name, p.description, p.price, c.name
                ORDER BY sales_count DESC
                LIMIT $1
            ''', limit)
            return popular_products
    except Exception as e:
        logger.error(f"Error getting popular products: {e}")
        return []

# Функция для получения ежедневной статистики
async def get_daily_stats(date=None):
    """Получение ежедневной статистики продаж"""
    try:
        if date is None:
            date = datetime.now().date()
        
        async with db_pool.acquire() as conn:
            daily_stats = await conn.fetchrow('''
                SELECT 
                    COUNT(*) as total_orders,
                    COUNT(DISTINCT user_id) as unique_customers,
                    SUM(price) as total_revenue,
                    AVG(price) as average_order_value
                FROM purchases
                WHERE DATE(purchase_time) = $1
            ''', date)
            return daily_stats
    except Exception as e:
        logger.error(f"Error getting daily stats: {e}")
        return None

# Функция для получения трендов продаж
async def get_sales_trends(days=30):
    """Получение трендов продаж за указанный период"""
    try:
        async with db_pool.acquire() as conn:
            sales_trends = await conn.fetch('''
                SELECT 
                    DATE(purchase_time) as date,
                    COUNT(*) as order_count,
                    SUM(price) as daily_revenue,
                    COUNT(DISTINCT user_id) as daily_customers
                FROM purchases
                WHERE purchase_time >= CURRENT_DATE - INTERVAL '1 day' * $1
                GROUP BY DATE(purchase_time)
                ORDER BY date DESC
            ''', days)
            return sales_trends
    except Exception as e:
        logger.error(f"Error getting sales trends: {e}")
        return []

# Функция для получения географического распределения продаж
async def get_geographic_sales():
    """Получение географического распределения продаж по городам"""
    try:
        async with db_pool.acquire() as conn:
            geographic_sales = await conn.fetch('''
                SELECT 
                    c.name as city_name,
                    COUNT(p.id) as order_count,
                    SUM(pur.price) as total_revenue,
                    COUNT(DISTINCT pur.user_id) as unique_customers
                FROM cities c
                LEFT JOIN products p ON c.id = p.city_id
                LEFT JOIN purchases pur ON p.id = pur.product_id::integer
                GROUP BY c.id, c.name
                ORDER BY total_revenue DESC
            ''')
            return geographic_sales
    except Exception as e:
        logger.error(f"Error getting geographic sales: {e}")
        return []

# Функция для получения информации о инвойсах
async def get_invoice_stats():
    """Получение статистики по инвойсам"""
    try:
        async with db_pool.acquire() as conn:
            invoice_stats = await conn.fetch('''
                SELECT 
                    status,
                    COUNT(*) as count,
                    AVG(amount) as average_amount,
                    SUM(amount) as total_amount,
                    MIN(created_at) as oldest,
                    MAX(created_at) as newest
                FROM transactions
                GROUP BY status
            ''')
            return invoice_stats
    except Exception as e:
        logger.error(f"Error getting invoice stats: {e}")
        return []

# Функция для поиска пользователей
async def search_users(query, limit=50, offset=0):
    """Поиск пользователей по имени, username или ID"""
    try:
        async with db_pool.acquire() as conn:
            users = await conn.fetch('''
                SELECT *
                FROM users
                WHERE user_id::text LIKE $1 OR username ILIKE $1 OR first_name ILIKE $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
            ''', f'%{query}%', limit, offset)
            return users
    except Exception as e:
        logger.error(f"Error searching users: {e}")
        return []

# Функция для получения транзакций пользователя
async def get_user_transactions(user_id, limit=50, offset=0):
    """Получение транзакций пользователя"""
    try:
        async with db_pool.acquire() as conn:
            transactions = await conn.fetch('''
                SELECT *
                FROM transactions
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
            ''', user_id, limit, offset)
            return transactions
    except Exception as e:
        logger.error(f"Error getting user transactions: {e}")
        return []

# Функция для получения детальной информации о транзакции
async def get_transaction_details(transaction_id):
    """Получение детальной информации о транзакции"""
    try:
        async with db_pool.acquire() as conn:
            transaction = await conn.fetchrow('''
                SELECT t.*, u.username, u.first_name
                FROM transactions t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.id = $1
            ''', transaction_id)
            return transaction
    except Exception as e:
        logger.error(f"Error getting transaction details: {e}")
        return None

# Функция для обновления нескольких настроек одновременно
async def bulk_update_settings(settings_dict):
    """Массовое обновление настроек бота"""
    try:
        async with db_pool.acquire() as conn:
            for key, value in settings_dict.items():
                await conn.execute('''
                    INSERT INTO bot_settings (key, value)
                    VALUES ($1, $2)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                ''', key, value)
            
            # Обновляем кэш
            bot_settings_cache.update(settings_dict)
            
        return True
    except Exception as e:
        logger.error(f"Error bulk updating settings: {e}")
        return False

# Функция для очистки старых данных
async def cleanup_old_data(days=30):
    """Очистка старых данных из базы"""
    try:
        async with db_pool.acquire() as conn:
            # Удаляем завершенные транзакции старше указанного количества дней
            deleted_count = await conn.execute('''
                DELETE FROM transactions 
                WHERE status IN ('completed', 'expired', 'cancelled') 
                AND created_at < NOW() - INTERVAL '1 day' * $1
            ''', days)
            
            logger.info(f"Cleaned up {deleted_count} old transactions")
            return deleted_count
    except Exception as e:
        logger.error(f"Error cleaning up old data: {e}")
        return 0

# Функция для создания резервной копии важных данных
async def create_backup():
    """Создание резервной копии важных данных"""
    try:
        backup_data = {
            'users': [],
            'settings': [],
            'api_stats': []
        }
        
        async with db_pool.acquire() as conn:
            # Резервное копирование пользователей
            users = await conn.fetch('SELECT * FROM users')
            backup_data['users'] = [dict(user) for user in users]
            
            # Резервное копирование настроек
            settings = await conn.fetch('SELECT * FROM bot_settings')
            backup_data['settings'] = [dict(setting) for setting in settings]
            
            # Резервное копирование статистики API
            api_stats = await conn.fetch('SELECT * FROM explorer_api_stats')
            backup_data['api_stats'] = [dict(stat) for stat in api_stats]
        
        return backup_data
    except Exception as e:
        logger.error(f"Error creating backup: {e}")
        return None

# Функция для восстановления данных из резервной копии
async def restore_backup(backup_data):
    """Восстановление данных из резервной копии"""
    try:
        async with db_pool.acquire() as conn:
            # Восстановление пользователей
            for user in backup_data.get('users', []):
                await conn.execute('''
                    INSERT INTO users (user_id, username, first_name, language, captcha_passed, 
                                      ban_until, failed_payments, purchase_count, discount, balance,
                                      created_at, referrer_id, referral_code, referral_count, earned_from_referrals)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    language = EXCLUDED.language,
                    captcha_passed = EXCLUDED.captcha_passed,
                    ban_until = EXCLUDED.ban_until,
                    failed_payments = EXCLUDED.failed_payments,
                    purchase_count = EXCLUDED.purchase_count,
                    discount = EXCLUDED.discount,
                    balance = EXCLUDED.balance,
                    referrer_id = EXCLUDED.referrer_id,
                    referral_code = EXCLUDED.referral_code,
                    referral_count = EXCLUDED.referral_count,
                    earned_from_referrals = EXCLUDED.earned_from_referrals
                ''', user['user_id'], user['username'], user['first_name'], user['language'], 
                   user['captcha_passed'], user['ban_until'], user['failed_payments'], 
                   user['purchase_count'], user['discount'], user['balance'], user['created_at'],
                   user['referrer_id'], user['referral_code'], user['referral_count'], 
                   user['earned_from_referrals'])
            
            # Восстановление настроек
            for setting in backup_data.get('settings', []):
                await conn.execute('''
                    INSERT INTO bot_settings (key, value)
                    VALUES ($1, $2)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                ''', setting['key'], setting['value'])
            
            # Восстановление статистики API
            for stat in backup_data.get('api_stats', []):
                await conn.execute('''
                    INSERT INTO explorer_api_stats (explorer_name, total_requests, successful_requests, 
                                                   daily_limit, remaining_daily_requests, last_used, last_reset)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (explorer_name) DO UPDATE SET
                    total_requests = EXCLUDED.total_requests,
                    successful_requests = EXCLUDED.successful_requests,
                    daily_limit = EXCLUDED.daily_limit,
                    remaining_daily_requests = EXCLUDED.remaining_daily_requests,
                    last_used = EXCLUDED.last_used,
                    last_reset = EXCLUDED.last_reset
                ''', stat['explorer_name'], stat['total_requests'], stat['successful_requests'],
                   stat['daily_limit'], stat['remaining_daily_requests'], stat['last_used'], 
                   stat['last_reset'])
        
        # Обновляем кэш после восстановления
        await load_cache()
        
        return True
    except Exception as e:
        logger.error(f"Error restoring backup: {e}")
        return False

# Функция для получения информации о системе
async def get_system_info():
    """Получение информации о системе"""
    try:
        async with db_pool.acquire() as conn:
            info = {}
            
            # Количество пользователей
            info['total_users'] = await conn.fetchval('SELECT COUNT(*) FROM users')
            
            # Количество активных пользователей (сделавших хотя бы одну покупку)
            info['active_users'] = await conn.fetchval('SELECT COUNT(DISTINCT user_id) FROM purchases')
            
            # Общее количество покупок
            info['total_purchases'] = await conn.fetchval('SELECT COUNT(*) FROM purchases')
            
            # Общая выручка
            info['total_revenue'] = await conn.fetchval('SELECT COALESCE(SUM(price), 0) FROM purchases')
            
            # Количество pending транзакций
            info['pending_transactions'] = await conn.fetchval('SELECT COUNT(*) FROM transactions WHERE status = $1', 'pending')
            
            # Количество активных товаров
            info['active_products'] = await conn.fetchval('''
                SELECT COUNT(*) 
                FROM products p
                JOIN subcategories s ON p.subcategory_id = s.id
                WHERE s.quantity > 0
            ''')
            
            return info
    except Exception as e:
        logger.error(f"Error getting system info: {e}")
        return {}

# Функция для получения топ пользователей по покупкам
async def get_top_users_by_purchases(limit=10):
    """Получение топ пользователей по количеству покупок"""
    try:
        async with db_pool.acquire() as conn:
            top_users = await conn.fetch('''
                SELECT 
                    u.user_id,
                    u.username,
                    u.first_name,
                    COUNT(p.id) as purchase_count,
                    COALESCE(SUM(p.price), 0) as total_spent
                FROM users u
                LEFT JOIN purchases p ON u.user_id = p.user_id
                GROUP BY u.user_id, u.username, u.first_name
                ORDER BY purchase_count DESC, total_spent DESC
                LIMIT $1
            ''', limit)
            return top_users
    except Exception as e:
        logger.error(f"Error getting top users: {e}")
        return []

# Функция для получения топ пользователей по расходу
async def get_top_users_by_spending(limit=10):
    """Получение топ пользователей по сумме расходов"""
    try:
        async with db_pool.acquire() as conn:
            top_users = await conn.fetch('''
                SELECT 
                    u.user_id,
                    u.username,
                    u.first_name,
                    COUNT(p.id) as purchase_count,
                    COALESCE(SUM(p.price), 0) as total_spent
                FROM users u
                LEFT JOIN purchases p ON u.user_id = p.user_id
                GROUP BY u.user_id, u.username, u.first_name
                ORDER BY total_spent DESC, purchase_count DESC
                LIMIT $1
            ''', limit)
            return top_users
    except Exception as e:
        logger.error(f"Error getting top users by spending: {e}")
        return []

# Функция для получения статистики по городам
async def get_city_stats():
    """Получение статистики по городам"""
    try:
        async with db_pool.acquire() as conn:
            city_stats = await conn.fetch('''
                SELECT 
                    c.name as city_name,
                    COUNT(p.id) as product_count,
                    COUNT(DISTINCT pur.user_id) as customer_count,
                    COUNT(pur.id) as purchase_count,
                    COALESCE(SUM(pur.price), 0) as total_revenue
                FROM cities c
                LEFT JOIN products p ON c.id = p.city_id
                LEFT JOIN purchases pur ON p.id = pur.product_id::integer
                GROUP BY c.id, c.name
                ORDER BY total_revenue DESC
            ''')
            return city_stats
    except Exception as e:
        logger.error(f"Error getting city stats: {e}")
        return []

# Функция для получения статистики по категориям
async def get_category_stats():
    """Получение статистики по категориям товаров"""
    try:
        async with db_pool.acquire() as conn:
            category_stats = await conn.fetch('''
                SELECT 
                    cat.name as category_name,
                    COUNT(p.id) as product_count,
                    COUNT(sp.id) as sales_count,
                    COALESCE(SUM(sp.sold_price), 0) as total_revenue
                FROM categories cat
                LEFT JOIN products p ON cat.id = p.category_id
                LEFT JOIN sold_products sp ON p.id = sp.product_id
                GROUP BY cat.id, cat.name
                ORDER BY total_revenue DESC
            ''')
            return category_stats
    except Exception as e:
        logger.error(f"Error getting category stats: {e}")
        return []

# Функция для получения статистики по подкатегориям
async def get_subcategory_stats():
    """Получение статистики по подкатегориям товаров"""
    try:
        async with db_pool.acquire() as conn:
            subcategory_stats = await conn.fetch('''
                SELECT 
                    cat.name as category_name,
                    sub.name as subcategory_name,
                    sub.quantity as remaining_quantity,
                    COUNT(sp.id) as sales_count,
                    COALESCE(SUM(sp.sold_price), 0) as total_revenue
                FROM subcategories sub
                LEFT JOIN categories cat ON sub.category_id = cat.id
                LEFT JOIN sold_products sp ON sub.id = sp.subcategory_id
                GROUP BY cat.id, cat.name, sub.id, sub.name, sub.quantity
                ORDER BY total_revenue DESC
            ''')
            return subcategory_stats
    except Exception as e:
        logger.error(f"Error getting subcategory stats: {e}")
        return []

# Функция для получения статистики по доставке
async def get_delivery_stats():
    """Получение статистики по типам доставки"""
    try:
        async with db_pool.acquire() as conn:
            delivery_stats = await conn.fetch('''
                SELECT 
                    dt.name as delivery_type,
                    COUNT(pur.id) as purchase_count,
                    COALESCE(SUM(pur.price), 0) as total_revenue
                FROM delivery_types dt
                LEFT JOIN products p ON dt.id = p.delivery_type_id
                LEFT JOIN purchases pur ON p.id = pur.product_id::integer
                GROUP BY dt.id, dt.name
                ORDER BY total_revenue DESC
            ''')
            return delivery_stats
    except Exception as e:
        logger.error(f"Error getting delivery stats: {e}")
        return []

# Функция для получения ежедневной выручки
async def get_daily_revenue(days=30):
    """Получение ежедневной выручки за указанный период"""
    try:
        async with db_pool.acquire() as conn:
            daily_revenue = await conn.fetch('''
                SELECT 
                    DATE(purchase_time) as date,
                    COUNT(*) as order_count,
                    COALESCE(SUM(price), 0) as daily_revenue
                FROM purchases
                WHERE purchase_time >= CURRENT_DATE - INTERVAL '1 day' * $1
                GROUP BY DATE(purchase_time)
                ORDER BY date DESC
            ''', days)
            return daily_revenue
    except Exception as e:
        logger.error(f"Error getting daily revenue: {e}")
        return []

# Функция для получения среднего чека
async def get_average_order_value(days=30):
    """Получение среднего чека за указанный период"""
    try:
        async with db_pool.acquire() as conn:
            avg_order_value = await conn.fetchrow('''
                SELECT 
                    COUNT(*) as order_count,
                    COALESCE(SUM(price), 0) as total_revenue,
                    COALESCE(AVG(price), 0) as average_order_value
                FROM purchases
                WHERE purchase_time >= CURRENT_DATE - INTERVAL '1 day' * $1
            ''', days)
            return avg_order_value
    except Exception as e:
        logger.error(f"Error getting average order value: {e}")
        return None

# Функция для получения повторных покупок
async def get_repeat_customers():
    """Получение статистики по повторным покупкам"""
    try:
        async with db_pool.acquire() as conn:
            repeat_customers = await conn.fetch('''
                SELECT 
                    purchase_count,
                    COUNT(*) as customer_count
                FROM (
                    SELECT 
                        user_id,
                        COUNT(*) as purchase_count
                    FROM purchases
                    GROUP BY user_id
                ) as customer_purchases
                GROUP BY purchase_count
                ORDER BY purchase_count
            ''')
            return repeat_customers
    except Exception as e:
        logger.error(f"Error getting repeat customers: {e}")
        return []

# Функция для получения временных метрик
async def get_time_metrics():
    """Получение временных метрик системы"""
    try:
        async with db_pool.acquire() as conn:
            metrics = {}
            
            # Время работы системы
            metrics['uptime'] = await conn.fetchval('SELECT NOW() - MIN(created_at) FROM users')
            
            # Время последней покупки
            metrics['last_purchase'] = await conn.fetchval('SELECT MAX(purchase_time) FROM purchases')
            
            # Время последней транзакции
            metrics['last_transaction'] = await conn.fetchval('SELECT MAX(created_at) FROM transactions')
            
            # Время последнего обновления кэша
            metrics['cache_last_updated'] = datetime.now()
            
            return metrics
    except Exception as e:
        logger.error(f"Error getting time metrics: {e}")
        return {}

# Функция для проверки здоровья базы данных
async def check_database_health():
    """Проверка здоровья базы данных"""
    try:
        async with db_pool.acquire() as conn:
            # Проверяем соединение
            await conn.execute('SELECT 1')
            
            # Проверяем основные таблицы
            tables = ['users', 'transactions', 'purchases', 'products', 'bot_settings']
            health = {}
            
            for table in tables:
                try:
                    count = await conn.fetchval(f'SELECT COUNT(*) FROM {table}')
                    health[table] = {'status': 'healthy', 'count': count}
                except Exception as e:
                    health[table] = {'status': 'error', 'error': str(e)}
            
            return health
    except Exception as e:
        logger.error(f"Error checking database health: {e}")
        return {'status': 'error', 'error': str(e)}

# Функция для оптимизации базы данных
async def optimize_database():
    """Оптимизация базы данных"""
    try:
        async with db_pool.acquire() as conn:
            # Анализируем таблицы
            await conn.execute('ANALYZE')
            
            # Очищаем неиспользуемое пространство
            await conn.execute('VACUUM')
            
            logger.info("Database optimization completed successfully")
            return True
    except Exception as e:
        logger.error(f"Error optimizing database: {e}")
        return False

# Функция для получения размера базы данных
async def get_database_size():
    """Получение размера базы данных"""
    try:
        async with db_pool.acquire() as conn:
            size = await conn.fetchval('''
                SELECT pg_size_pretty(pg_database_size(current_database()))
            ''')
            return size
    except Exception as e:
        logger.error(f"Error getting database size: {e}")
        return "Unknown"

# Функция для получения информации о таблицах
async def get_table_info():
    """Получение информации о таблицах базы данных"""
    try:
        async with db_pool.acquire() as conn:
            tables = await conn.fetch('''
                SELECT 
                    table_name,
                    pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) as size,
                    (SELECT COUNT(*) FROM information_schema.columns WHERE table_name = t.table_name) as columns,
                    (SELECT COUNT(*) FROM information_schema.constraints WHERE table_name = t.table_name) as constraints
                FROM information_schema.tables t
                WHERE table_schema = 'public'
                ORDER BY pg_total_relation_size(quote_ident(table_name)) DESC
            ''')
            return tables
    except Exception as e:
        logger.error(f"Error getting table info: {e}")
        return []

# Функция для экспорта данных
async def export_data(table_name, format='json'):
    """Экспорт данных из указанной таблицы"""
    try:
        async with db_pool.acquire() as conn:
            if format == 'json':
                data = await conn.fetch(f'SELECT * FROM {table_name}')
                return [dict(row) for row in data]
            elif format == 'csv':
                # Здесь можно реализовать экспорт в CSV
                return f"CSV export for {table_name} not implemented yet"
            else:
                return f"Unsupported format: {format}"
    except Exception as e:
        logger.error(f"Error exporting data from {table_name}: {e}")
        return None

# Функция для импорта данных
async def import_data(table_name, data, format='json'):
    """Импорт данных в указанную таблицу"""
    try:
        async with db_pool.acquire() as conn:
            if format == 'json':
                for item in data:
                    columns = ', '.join(item.keys())
                    values = ', '.join([f"${i+1}" for i in range(len(item))])
                    await conn.execute(f'''
                        INSERT INTO {table_name} ({columns})
                        VALUES ({values})
                        ON CONFLICT DO NOTHING
                    ''', *item.values())
                return True
            else:
                logger.error(f"Unsupported format: {format}")
                return False
    except Exception as e:
        logger.error(f"Error importing data to {table_name}: {e}")
        return False

# Функция для получения логов ошибок
async def get_error_logs(limit=100, offset=0):
    """Получение логов ошибок из базы данных"""
    try:
        # В реальной системе логи могут храниться в отдельной таблице
        # Здесь мы просто возвращаем пустой массив, так как у нас нет таблицы логов
        return []
    except Exception as e:
        logger.error(f"Error getting error logs: {e}")
        return []

# Функция для очистки логов
async def clear_logs(days=30):
    """Очистка старых логов"""
    try:
        # В реальной системе это бы очищало таблицу логов
        # Здесь просто возвращаем успех, так как у нас нет таблицы логов
        return True
    except Exception as e:
        logger.error(f"Error clearing logs: {e}")
        return False

# Закрытие пула соединений при завершении работы
async def close_db():
    """Закрытие пула соединений с базой данных"""
    try:
        if db_pool:
            await db_pool.close()
            logger.info("Database pool closed successfully")
    except Exception as e:
        logger.error(f"Error closing database pool: {e}")
