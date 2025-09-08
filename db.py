import asyncpg
from asyncpg.pool import Pool
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Глобальная переменная для пула соединений
db_pool: Pool = None

# Белый список разрешенных колонок для обновления
ALLOWED_USER_COLUMNS = {
    'username', 'first_name', 'language', 'captcha_passed',
    'ban_until', 'failed_payments', 'purchase_count', 'discount', 'balance'
}

# Глобальные кэши
texts_cache = {}
cities_cache = []
districts_cache = {}
products_cache = {}
delivery_types_cache = []

# Инициализация базы данных
async def init_db(database_url):
    global db_pool
    db_pool = await asyncpg.create_pool(database_url, ssl='require')
    
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        ''')
        
        # Проверяем существование столбца invoice_uuid и добавляем его, если нет
        try:
            await conn.execute("SELECT invoice_uuid FROM transactions LIMIT 1")
        except Exception as e:
            await conn.execute('ALTER TABLE transactions ADD COLUMN invoice_uuid TEXT')
            logger.info("Added invoice_uuid column to transactions table")
        
        # Таблица покупок
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
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        ''')
        
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
        
        # Новая таблица для товаров
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            city_id INTEGER REFERENCES cities(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            image_url TEXT,
            UNIQUE(city_id, name)
        )
        ''')
        
        # Новая таблица для типов доставки
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS delivery_types (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        )
        ''')
        
        # Заполняем таблицы начальными данными, если они пустые
        await init_default_data(conn)
        
    return db_pool

# Функция для заполнения начальных данных
async def init_default_data(conn):
    # Проверяем, есть ли уже данные в таблицах
    texts_count = await conn.fetchval('SELECT COUNT(*) FROM texts')
    if texts_count == 0:
        # Добавляем тексты
        default_texts = {
            'ru': {
                'welcome': 'Добро пожаловать!',
                'captcha': 'Для входа решите каптчу: {code}\nВведите 5 цифр:',
                'captcha_failed': 'Неверная каптча! Попробуйте снова:',
                'language_selected': 'Язык установлен: Русский',
                'main_menu': "👤 Имя: {name}\n📛 Юзернейм: @{username}\n🛒 Покупок: {purchases}\n🎯 Скидка: {discount}%\n💰 Баланс: {balance}$",
                'select_city': 'Выберите город:',
                'select_category': 'Выберите категорию:',
                'select_district': 'Выберите район:',
                'select_delivery': 'Выберите тип доставки:',
                'order_summary': "Информация о заказе:\n📦 Товар: {product}\n💵 Стоимость: {price}$\n🏙 Район: {district}\n🚚 Тип доставки: {delivery_type}\n\nВсё верно?",
                'select_crypto': 'Выберите криптовалюту для оплаты:',
                'payment_instructions': "Оплатите {amount} {currency} по адресу:\n`{payment_address}`\n\nОтсканируйте QR-код для оплаты:\nПосле оплаты товар будет выслан автоматически.",
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
                'no_orders': 'У вас еще не было заказов'
            },
            'en': {
                'welcome': 'Welcome!',
                'captcha': 'To enter, solve the captcha: {code}\nEnter 5 digits:',
                'captcha_failed': 'Invalid captcha! Try again:',
                'language_selected': 'Language set: English',
                'main_menu': "👤 Name: {name}\n📛 Username: @{username}\n🛒 Purchases: {purchases}\n🎯 Discount: {discount}%\n💰 Balance: {balance}$",
                'select_city': 'Select city:',
                'select_category': 'Select category:',
                'select_district': 'Select district:',
                'select_delivery': 'Select delivery type:',
                'order_summary': "Order information:\n📦 Product: {product}\n💵 Price: {price}$\n🏙 District: {district}\n🚚 Delivery type: {delivery_type}\n\nIs everything correct?",
                'select_crypto': 'Select cryptocurrency for payment:',
                'payment_instructions': "Pay {amount} {currency} to address:\n`{payment_address}`\n\nOr scan QR-code:\nAfter payment, the product will be sent automatically.",
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
                'no_orders': 'You have no orders yet'
            },
            'ka': {
                'welcome': 'კეთილი იყოს თქვენი მობრძანება!',
                'captcha': 'შესასვლელად გადაწყვიტეთ captcha: {code}\nშეიყვანეთ 5 ციფრი:',
                'captcha_failed': 'არასწორი captcha! სცადეთ თავიდან:',
                'language_selected': 'ენა დაყენებულია: ქართული',
                'main_menu': "👤 სახელი: {name}\n📛 მომხმარებლის სახელი: @{username}\n🛒 ყიდვები: {purchases}\n🎯 ფასდაკლება: {discount}%\n💰 ბალანსი: {balance}$",
                'select_city': 'აირჩიეთ ქალაქი:',
                'select_category': 'აირჩიეთ კატეგორია:',
                'select_district': 'აირჩიეთ რაიონი:',
                'select_delivery': 'აირჩიეთ მიწოდების ტიპი:',
                'order_summary': "შეკვეთის ინფორმაცია:\n📦 პროდუქტი: {product}\n💵 ფასი: {price}$\n🏙 რაიონი: {district}\n🚚 მიწოდების ტიპი: {delivery_type}\n\nყველაფერი სწორია?",
                'select_crypto': 'აირჩიეთ კრიპტოვალუტა გადასახდელად:',
                'payment_instructions': "გადაიხადეთ {amount} {currency} მისამართზე:\n`{payment_address}`\n\nან სკანირება QR-კოდი:\nგადახდის შემდეგ პროდუქტი გამოგეგზავნებათ ავტომატურად.",
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
                'no_orders': 'ჯერ არ გაქვთ შეკვეთები'
            }
        }
        
        for lang, translations in default_texts.items():
            for key, value in translations.items():
                await conn.execute('''
                INSERT INTO texts (lang, key, value)
                VALUES ($1, $2, $3)
                ON CONFLICT (lang, key) DO NOTHING
                ''', lang, key, value)
    
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
            
            # Добавляем товары для каждого города
            if city == 'Тбилиси':
                products = [
                    ('0.5 меф', 35, 'https://example.com/image1.jpg'),
                    ('1.0 меф', 70, 'https://example.com/image2.jpg'),
                    ('0.5 меф золотой', 50, 'https://example.com/image3.jpg'),
                    ('0.3 красный', 35, 'https://example.com/image4.jpg')
                ]
            else:
                products = [
                    ('0.5 меф', 35, 'https://example.com/image1.jpg'),
                    ('1.0 меф', 70, 'https://example.com/image2.jpg')
                ]
                
            for product_name, price, image_url in products:
                await conn.execute('''
                INSERT INTO products (city_id, name, price, image_url)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (city_id, name) DO NOTHING
                ''', city_id, product_name, price, image_url)
    
    # Проверяем и добавляем типы доставки
    delivery_count = await conn.fetchval('SELECT COUNT(*) FROM delivery_types')
    if delivery_count == 0:
        delivery_types = ['Подъезд', 'Прикоп', 'Магнит', 'Во дворах']
        for delivery_type in delivery_types:
            await conn.execute('''
            INSERT INTO delivery_types (name) VALUES ($1)
            ON CONFLICT (name) DO NOTHING
            ''', delivery_type)

# Функция для загрузки данных в кэш
async def load_cache():
    global texts_cache, cities_cache, districts_cache, products_cache, delivery_types_cache
    
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
            
            # Загрузка товаров
            products_cache = {}
            for city in cities_cache:
                products = await conn.fetch('''
                    SELECT p.name, p.price, p.image_url 
                    FROM products p 
                    WHERE p.city_id = $1 
                    ORDER BY p.name
                ''', city['id'])
                products_cache[city['name']] = {
                    product['name']: {
                        'price': product['price'], 
                        'image': product['image_url']
                    } for product in products
                }
            
            # Загрузка типов доставки
            delivery_types = await conn.fetch('SELECT * FROM delivery_types ORDER BY name')
            delivery_types_cache = [delivery_type['name'] for delivery_type in delivery_types]
            
        logger.info("Кэш успешно загружен")
    except Exception as e:
        logger.error(f"Ошибка загрузки кэша: {e}")
        raise

# Функция для получения текста
def get_text(lang, key, **kwargs):
    if lang not in texts_cache:
        lang = 'ru'
    if key not in texts_cache[lang]:
        return f"Текст не найден: {key}"
    
    text = texts_cache[lang][key]
    try:
        if kwargs:
            text = text.format(**kwargs)
        return text
    except KeyError as e:
        logger.error(f"Ошибка форматирования текста: {e}, ключ: {key}, аргументы: {kwargs}")
        return text

# Функции для работы с базой данных
async def get_user(user_id):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)

async def update_user(user_id, **kwargs):
    # Фильтруем только разрешенные колонки
    valid_updates = {k: v for k, v in kwargs.items() if k in ALLOWED_USER_COLUMNS}
    if not valid_updates:
        return
        
    set_clause = ", ".join([f"{k} = ${i+2}" for i, k in enumerate(valid_updates.keys())])
    values = list(valid_updates.values()) + [user_id]
    
    async with db_pool.acquire() as conn:
        await conn.execute(f'UPDATE users SET {set_clause} WHERE user_id = ${len(values)}', *values)

async def add_transaction(user_id, amount, currency, order_id, payment_url, expires_at, product_info, invoice_uuid):
    async with db_pool.acquire() as conn:
        await conn.execute('''
        INSERT INTO transactions (user_id, amount, currency, status, order_id, payment_url, expires_at, product_info, invoice_uuid)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ''', user_id, amount, currency, 'pending', order_id, payment_url, expires_at, product_info, invoice_uuid)

async def add_purchase(user_id, product, price, district, delivery_type):
    async with db_pool.acquire() as conn:
        # Атомарное обновление счетчика покупок
        await conn.execute('''
        WITH new_purchase AS (
            INSERT INTO purchases (user_id, product, price, district, delivery_type)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING user_id
        )
        UPDATE users 
        SET purchase_count = purchase_count + 1 
        WHERE user_id = $1
        ''', user_id, product, price, district, delivery_type)

async def get_pending_transactions():
    async with db_pool.acquire() as conn:
        return await conn.fetch('SELECT * FROM transactions WHERE status = $1 AND expires_at > NOW()', 'pending')

async def update_transaction_status(order_id, status):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE transactions SET status = $1 WHERE order_id = $2', status, order_id)

async def update_transaction_status_by_uuid(invoice_uuid, status):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE transactions SET status = $1 WHERE invoice_uuid = $2', status, invoice_uuid)

async def get_last_order(user_id):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow('SELECT * FROM purchases WHERE user_id = $1 ORDER BY purchase_time DESC LIMIT 1', user_id)

# Функция для проверки бана пользователя
async def is_banned(user_id):
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
