import asyncpg
from asyncpg.pool import Pool
from datetime import datetime
import logging
import uuid
import traceback

logger = logging.getLogger(__name__)

# Глобальная переменная для пула соединений
db_pool: Pool = None

# Белый список разрешенных колонки для обновления
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
                        await conn.execute(f'ALTER TABLE transactions ADD COLUMN {column} INTEGER')
                    elif column == 'crypto_amount':
                        await conn.execute(f'ALTER TABLE transactions ADD COLUMN {column} REAL')
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
                        await conn.execute(f'ALTER TABLE purchases ADD COLUMN {column} INTEGER')
                    else:
                        await conn.execute(f'ALTER TABLE purchases ADD COLUMN {column} TEXT')
                    logger.info(f"Added {column} column to purchases table")
            
            # Новая таблида для текстов
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
                id SERial PRIMARY KEY,
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
            
            # Новая таблица для товаров
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
            
            # Добавляем недостающие столбцы, если они еще не существуют
            columns_to_check = [
                'category_id', 'district_id', 'delivery_type_id', 'uuid', 'description', 'subcategory_id'
            ]
            
            for column in columns_to_check:
                try:
                    await conn.execute(f"SELECT {column} FROM products LIMIT 1")
                except Exception:
                    if column == 'uuid':
                        await conn.execute(f'ALTER TABLE products ADD COLUMN {column} TEXT UNIQUE')
                    elif column == 'description':
                        await conn.execute(f'ALTER TABLE products ADD COLUMN {column} TEXT')
                    elif column == 'subcategory_id':
                        await conn.execute(f'ALTER TABLE products ADD COLUMN {column} INTEGER REFERENCES subcategories(id)')
                    else:
                        await conn.execute(f'ALTER TABLE products ADD COLUMN {column} INTEGER REFERENCES {column.split("_")[0] + "s"}(id)')
                    logger.info(f"Added {column} column to products table")
            
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
• Все пополнения на этот адрес будут зачислены на ваш баланс
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
• 3 წარუმატებელი მცდელობა - 24 საათიანი ბანი''',
                'invoice_time_left': '⏱ ინვოისის გაუქმებამდე დარჩა: {time_left}',
                'invoice_cancelled': '❌ ინვოისი გაუქმებულია. წარუ�მატებელი მცდელობები: {failed_count}/3',
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
            delivery_types = await conn.fetch('SELECT * FROM delivery_types ORDER BY name')
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

# Функции для работы с базой данных
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
            
        # Формируем SET часть запроса с правильной нумерацией параметров
        set_parts = []
        values = []
        for i, (k, v) in enumerate(valid_updates.items(), start=1):
            set_parts.append(f"{k} = ${i}")
            values.append(v)
        
        # Добавляем user_id в конец списка значений
        values.append(user_id)
        set_clause = ", ".join(set_parts)
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                f'UPDATE users SET {set_clause} WHERE user_id = ${len(values)}',
                *values
            )
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}")

async def add_transaction(user_id, amount, currency, order_id, payment_url, expires_at, product_info, invoice_uuid, crypto_address=None, crypto_amount=None, product_id=None):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
            INSERT INTO transactions (user_id, amount, currency, status, order_id, payment_url, expires_at, product_info, invoice_uuid, crypto_address, crypto_amount, product_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ''', user_id, amount, currency, 'pending', order_id, payment_url, expires_at, product_info, invoice_uuid, crypto_address, crypto_amount, product_id)
    except Exception as e:
        logger.error(f"Error adding transaction for user {user_id}: {e}")

async def add_purchase(user_id, product, price, district, delivery_type, product_id=None, image_url=None, description=None):
    try:
        async with db_pool.acquire() as conn:
            # Преобразуем product_id в строку если он не None
            product_id_str = str(product_id) if product_id is not None else None
            
            # Атомарное обновление счетчика покупок и возврат ID покупки
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
        async with db_pool.acquire() as conn:
            await conn.execute('UPDATE transactions SET status = $1 WHERE order_id = $2', status, order_id)
    except Exception as e:
        logger.error(f"Error updating transaction status for order {order_id}: {e}")

async def update_transaction_status_by_uuid(invoice_uuid, status):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('UPDATE transactions SET status = $1 WHERE invoice_uuid = $2', status, invoice_uuid)
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
async def increment_api_request(api_name):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
            UPDATE explorer_api_stats 
            SET total_requests = total_requests + 1 
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

# Добавленные функции для работы с количеством товаров
async def get_product_quantity(product_id):
    """Получить количество товара по его ID"""
    try:
        async with db_pool.acquire() as conn:
            # Сначала получаем subcategory_id из продукта
            subcategory_id = await conn.fetchval(
                'SELECT subcategory_id FROM products WHERE id = $1', 
                product_id
            )
            if not subcategory_id:
                return 0
                
            # Затем получаем количество из подкатегории
            return await conn.fetchval(
                'SELECT quantity FROM subcategories WHERE id = $1',
                subcategory_id
            )
    except Exception as e:
        logger.error(f"Error getting product quantity: {e}")
        return 0

async def reserve_product(product_id):
    """Забронировать товар (уменьшить количество на 1)"""
    try:
        async with db_pool.acquire() as conn:
            # Получаем subcategory_id продукта
            subcategory_id = await conn.fetchval(
                'SELECT subcategory_id FROM products WHERE id = $1',
                product_id
            )
            if not subcategory_id:
                return False
                
            # Уменьшаем количество в подкатегории
            result = await conn.execute('''
                UPDATE subcategories 
                SET quantity = quantity - 1 
                WHERE id = $1 AND quantity > 0
            ''', subcategory_id)
            
            return "UPDATE 1" in str(result)
    except Exception as e:
        logger.error(f"Error reserving product: {e}")
        return False

async def release_product(product_id):
    """Вернуть товар (увеличить количество на 1)"""
    try:
        async with db_pool.acquire() as conn:
            # Получаем subcategory_id продукта
            subcategory_id = await conn.fetchval(
                'SELECT subcategory_id FROM products WHERE id = $1',
                product_id
            )
            if not subcategory_id:
                return False
                
            # Увеличиваем количество в подкатегории
            await conn.execute('''
                UPDATE subcategories 
                SET quantity = quantity + 1 
                WHERE id = $1
            ''', subcategory_id)
            return True
    except Exception as e:
        logger.error(f"Error releasing product: {e}")
        return False
