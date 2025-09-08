import asyncpg
from asyncpg.pool import Pool
from datetime import datetime

# Глобальная переменная для пула соединений
db_pool: Pool = None

# Белый список разрешенных колонок для обновления
ALLOWED_USER_COLUMNS = {
    'username', 'first_name', 'language', 'captcha_passed',
    'ban_until', 'failed_payments', 'purchase_count', 'discount', 'balance'
}

# Инициализация базы данных
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, ssl='require')
    
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
