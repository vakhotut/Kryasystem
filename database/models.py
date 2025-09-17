import logging
from datetime import datetime
import uuid
import traceback
from .connection import db_pool

logger = logging.getLogger(__name__)

async def init_tables():
    """Инициализация таблиц базы данных"""
    try:
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
            
            # Новая таблица для подкатегорий товары
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
                user_id BIGINT REFERENCES users(user_id),
                index INTEGER NOT NULL,
                label TEXT,
                expected_amount REAL,
                balance REAL DEFAULT 0.0,
                transaction_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Проверяем и добавляем столбец user_id если его нет
            try:
                await conn.execute("SELECT user_id FROM generated_addresses LIMIT 1")
            except Exception:
                await conn.execute('ALTER TABLE generated_addresses ADD COLUMN user_id BIGINT REFERENCES users(user_id)')
                logger.info("Added user_id column to generated_addresses table")
            
            # Таблица для депозитов
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS deposits (
                id SERIAL PRIMARY KEY,
                txid TEXT UNIQUE NOT NULL,
                address TEXT NOT NULL,
                user_id BIGINT NOT NULL REFERENCES users(user_id),
                amount_ltc REAL NOT NULL,
                amount_usd REAL NOT NULL,
                confirmations INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Заполняем таблицы начальными данными, если они пустые
            await init_default_data(conn)
            
        logger.info("All tables initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing tables: {e}")
        logger.error(traceback.format_exc())
        raise

async def init_default_data(conn):
    """Заполнение начальных данных"""
    try:
        # Импортируем тексты из texts.py
        from texts import default_texts, default_settings
        
        # Заполняем тексты
        for lang, translations in default_texts.items():
            for key, value in translations.items():
                await conn.execute('''
                INSERT INTO texts (lang, key, value)
                VALUES ($1, $2, $3)
                ON CONFLICT (lang, key) DO UPDATE SET value = EXCLUDED.value
                ''', lang, key, value)
        
        # Заполняем настройки
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
