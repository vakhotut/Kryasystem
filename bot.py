import logging
import random
import time
import asyncio
import os
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes
)
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from threading import Thread

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройки бота
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
COINGATE_API_TOKEN = os.getenv("COINGATE_API_TOKEN", "YOUR_COINGATE_API_TOKEN")
COINGATE_API_URL = "https://api.coingate.com/api/v2/orders"
DATABASE_URL = os.environ['DATABASE_URL']

# Состояния разговора
CAPTCHA, LANGUAGE, MAIN_MENU, CITY, CATEGORY, DISTRICT, DELIVERY, CONFIRMATION, PAYMENT, BALANCE = range(10)

# Инициализация базы данных
def init_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
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
    cursor.execute('''
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
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # Таблица покупок
    cursor.execute('''
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
    
    conn.commit()
    conn.close()

# Функции для работы с базой данных
def get_user(user_id):
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def update_user(user_id, **kwargs):
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    
    for key, value in kwargs.items():
        cursor.execute(f'UPDATE users SET {key} = %s WHERE user_id = %s', (value, user_id))
    
    conn.commit()
    conn.close()

def add_transaction(user_id, amount, currency, order_id, payment_url, expires_at, product_info):
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT INTO transactions (user_id, amount, currency, status, order_id, payment_url, expires_at, product_info)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (user_id, amount, currency, 'pending', order_id, payment_url, expires_at, product_info))
    
    conn.commit()
    conn.close()

def add_purchase(user_id, product, price, district, delivery_type):
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT INTO purchases (user_id, product, price, district, delivery_type)
    VALUES (%s, %s, %s, %s, %s)
    ''', (user_id, product, price, district, delivery_type))
    
    # Обновляем счетчик покупок
    cursor.execute('SELECT purchase_count FROM users WHERE user_id = %s', (user_id,))
    result = cursor.fetchone()
    purchase_count = result[0] + 1 if result else 1
    cursor.execute('UPDATE users SET purchase_count = %s WHERE user_id = %s', (purchase_count, user_id))
    
    conn.commit()
    conn.close()

def get_pending_transactions():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM transactions WHERE status = %s AND expires_at > NOW()', ('pending',))
    transactions = cursor.fetchall()
    conn.close()
    return transactions

def update_transaction_status(order_id, status):
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET status = %s WHERE order_id = %s', (status, order_id))
    conn.commit()
    conn.close()

# Инициализация базы данных при запуске
init_db()

# Тексты на разных языках
TEXTS = {
    'ru': {
        'welcome': 'Добро пожаловать!',
        'captcha': 'Для входа решите каптчу: {code}\nВведите 5 цифр:',
        'captcha_failed': 'Неверная каптча! Попробуйте снова:',
        'language_selected': 'Язык установлен: Русский',
        'main_menu': (
            "👤 Имя: {name}\n"
            "📛 Юзернейм: @{username}\n"
            "🛒 Покупок: {purchases}\n"
            "🎯 Скидка: {discount}%\n"
            "💰 Баланс: {balance} лари"
        ),
        'select_city': 'Выберите город:',
        'select_category': 'Выберите категорию:',
        'select_district': 'Выберите район:',
        'select_delivery': 'Выберите тип доставки:',
        'order_summary': (
            "Информация о заказе:\n"
            "📦 Товар: {product}\n"
            "💵 Стоимость: {price} лари\n"
            "🏙 Район: {district}\n"
            "🚚 Тип доставки: {delivery_type}\n\n"
            "Всё верно?"
        ),
        'payment_method': 'Выберите метод оплаты:',
        'payment_instructions': (
            "Оплатите {amount} {currency} по адресу:\n"
            "`{payment_address}`\n\n"
            "У вас есть 30 минут для оплаты. После оплаты товар будет выслан автоматически."
        ),
        'payment_timeout': 'Время оплаты истекло. Заказ отменен.',
        'payment_success': 'Оплата получена! Ваш товар:\n\n{product_image}',
        'balance': 'Ваш баланс: {balance} лари',
        'balance_add': 'Введите сумму для пополнения баланса:',
        'balance_add_success': 'Баланс пополнен на {amount} лари. Текущий баланс: {balance} лари',
        'support': 'По всем вопросам обращайтесь к @support_username',
        'bonuses': 'Бонусная система:\n- За каждую 5-ю покупку скидка 10%\n- Пригласи друга и получи 50 лари на баланс',
        'rules': 'Правила:\n1. Не сообщайте никому данные о заказе\n2. Оплата только в течение 30 минут\n3. При нарушении правил - бан',
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
        'main_menu': (
            "👤 Name: {name}\n"
            "📛 Username: @{username}\n"
            "🛒 Purchases: {purchases}\n"
            "🎯 Discount: {discount}%\n"
            "💰 Balance: {balance} lari"
        ),
        'select_city': 'Select city:',
        'select_category': 'Select category:',
        'select_district': 'Select district:',
        'select_delivery': 'Select delivery type:',
        'order_summary': (
            "Order information:\n"
            "📦 Product: {product}\n"
            "💵 Price: {price} lari\n"
            "🏙 District: {district}\n"
            "🚚 Delivery type: {delivery_type}\n\n"
            "Is everything correct?"
        ),
        'payment_method': 'Select payment method:',
        'payment_instructions': (
            "Pay {amount} {currency} to address:\n"
            "`{payment_address}`\n\n"
            "You have 30 minutes to pay. After payment, the product will be sent automatically."
        ),
        'payment_timeout': 'Payment time has expired. Order canceled.',
        'payment_success': 'Payment received! Your product:\n\n{product_image}',
        'balance': 'Your balance: {balance} lari',
        'balance_add': 'Enter the amount to top up your balance:',
        'balance_add_success': 'Balance topped up by {amount} lari. Current balance: {balance} lari',
        'support': 'For all questions contact @support_username',
        'bonuses': 'Bonus system:\n- 10% discount for every 5th purchase\n- Invite a friend and get 50 lari on your balance',
        'rules': 'Rules:\n1. Do not share order information with anyone\n2. Payment only within 30 minutes\n3. Ban for breaking the rules',
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
        'main_menu': (
            "👤 სახელი: {name}\n"
            "📛 მომხმარებლის სახელი: @{username}\n"
            "🛒 ყიდვები: {purchases}\n"
            "🎯 ფასდაკლება: {discount}%\n"
            "💰 ბალანსი: {balance} ლარი"
        ),
        'select_city': 'აირჩიეთ ქალაქი:',
        'select_category': 'აირჩიეთ კატეგორია:',
        'select_district': 'აირჩიეთ რაიონი:',
        'select_delivery': 'აირჩიეთ მიწოდების ტიპი:',
        'order_summary': (
            "შეკვეთის ინფორმაცია:\n"
            "📦 პროდუქტი: {product}\n"
            "💵 ფასი: {price} ლარი\n"
            "🏙 რაიონი: {district}\n"
            "🚚 მიწოდების ტიპი: {delivery_type}\n\n"
            "ყველაფერი სწორია?"
        ),
        'payment_method': 'აირჩიეთ გადახდის მეთოდი:',
        'payment_instructions': (
            "გადაიხადეთ {amount} {currency} მისამართზე:\n"
            "`{payment_address}`\n\n"
            "გადახდისთვის გაქვთ 30 წუთი. გადახდის შემდეგ პროდუქტი გამოგეგზავნებათ ავტომატურად."
        ),
        'payment_timeout': 'გადახდის დრო ამოიწურა. შეკვეთა გაუქმებულია.',
        'payment_success': 'გადახდა მიღებულია! თქვენი პროდუქტი:\n\n{product_image}',
        'balance': 'თქვენი ბალანსი: {balance} ლარი',
        'balance_add': 'შეიყვანეთ ბალანსის შევსების რაოდენობა:',
        'balance_add_success': 'ბალანსი შეივსო {amount} ლარით. მიმდინარე ბალანსი: {balance} ლარი',
        'support': 'ყველა კითხვისთვის დაუკავშირდით @support_username',
        'bonuses': 'ბონუს სისტემა:\n- ყოველ მე-5 ყიდვაზე 10% ფასდაკლება\n- მოიწვიე მეგობარი და მიიღე 50 ლარი ბალანსზე',
        'rules': 'წესები:\n1. არავის არ შეახოთ შეკვეთის ინფორმაცია\n2. გადახდა მხოლოდ 30 წუთის განმავლობაში\n3. წესების დარღვევაზე - ბანი',
        'reviews': 'ჩვენი მიმოხილვები: @reviews_channel',
        'error': 'მოხდა შეცდომა. სცადეთ მოგვიანებით.',
        'ban_message': '3 წარუმატებელი გადახდის მცდელობის გამო თქვენ დაბლოკილი ხართ 24 საათის განმავლობაში.',
        'back': '⬅️ უკან',
        'main_menu_button': '🏠 მთავარი მენიუ',
        'last_order': 'ბოლო შეკვეთის ინფორმაცია',
        'no_orders': 'ჯერ არ გაქვთ შეკვეთები'
    }
}

# Данные о продуктах
PRODUCTS = {
    'Тбилиси': {
        '0.5 меф': {'price': 100, 'image': 'https://example.com/image1.jpg'},
        '1.0 меф': {'price': 200, 'image': 'https://example.com/image2.jpg'},
        '0.5 меф золотой': {'price': 150, 'image': 'https://example.com/image3.jpg'},
        '0.3 красный': {'price': 100, 'image': 'https://example.com/image4.jpg'}
    },
    'Гори': {
        '0.5 меф': {'price': 100, 'image': 'https://example.com/image1.jpg'},
        '1.0 меф': {'price': 200, 'image': 'https://example.com/image2.jpg'}
    },
    'Кутаиси': {
        '0.5 меф': {'price': 100, 'image': 'https://example.com/image1.jpg'},
        '1.0 меф': {'price': 200, 'image': 'https://example.com/image2.jpg'}
    },
    'Батуми': {
        '0.5 меф': {'price': 100, 'image': 'https://example.com/image1.jpg'},
        '1.0 меф': {'price': 200, 'image': 'https://example.com/image2.jpg'}
    }
}

DISTRICTS = {
    'Тбилиси': ['Церетели', 'Центр', 'Сабуртало'],
    'Гори': ['Центр', 'Западный', 'Восточный'],
    'Кутаиси': ['Центр', 'Западный', 'Восточный'],
    'Батуми': ['Центр', 'Бульвар', 'Старый город']
}

DELIVERY_TYPES = ['Подъезд', 'Прикоп', 'Магнит', 'Во дворах']

# Функция для получения текста на нужном языке
def get_text(lang, key, **kwargs):
    if lang not in TEXTS:
        lang = 'ru'
    if key not in TEXTS[lang]:
        return f"Текст не найден: {key}"
    
    text = TEXTS[lang][key]
    try:
        if kwargs:
            text = text.format(**kwargs)
        return text
    except KeyError as e:
        logger.error(f"Ошибка форматирования текста: {e}, ключ: {key}, аргументы: {kwargs}")
        return text

# Функция для проверки бана пользователя
def is_banned(user_id):
    user = get_user(user_id)
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

# Функция для создания платежа через CoinGate
def create_coingate_order(amount, currency, description):
    headers = {
        'Authorization': f'Token {COINGATE_API_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    data = {
        'order_id': f'order_{int(time.time())}',
        'price_amount': amount,
        'price_currency': 'USD',
        'receive_currency': currency,
        'title': description,
        'callback_url': 'https://yourdomain.com/callback',
        'cancel_url': 'https://yourdomain.com/cancel',
        'success_url': 'https://yourdomain.com/success'
    }
    
    try:
        response = requests.post(COINGATE_API_URL, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error creating CoinGate order: {e}")
        return None

# Функция для проверки статуса платежа
def check_payment_status(order_id):
    headers = {
        'Authorization': f'Token {COINGATE_API_TOKEN}'
    }
    
    try:
        response = requests.get(f'{COINGATE_API_URL}/{order_id}', headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error checking payment status: {e}")
        return None

# Функция для получения последнего заказа пользователя
def get_last_order(user_id):
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM purchases WHERE user_id = %s ORDER BY purchase_time DESC LIMIT 1', (user_id,))
    order = cursor.fetchone()
    conn.close()
    return order

# Поток для проверки pending транзакций
def check_pending_transactions(app):
    while True:
        try:
            transactions = get_pending_transactions()
            for transaction in transactions:
                order_id = transaction['order_id']
                status_info = check_payment_status(order_id)
                
                if status_info:
                    status = status_info.get('status')
                    if status == 'paid':
                        update_transaction_status(order_id, 'paid')
                        
                        user_id = transaction['user_id']
                        product_info = transaction['product_info']
                        
                        asyncio.run_coroutine_threadsafe(
                            app.bot.send_message(
                                chat_id=user_id,
                                text=get_text('ru', 'payment_success', product_image=PRODUCTS['Тбилиси']['0.5 меф']['image'])
                            ),
                            app.loop
                        )
                        
                    elif status == 'expired' or status == 'canceled':
                        update_transaction_status(order_id, status)
            
            time.sleep(60)
        except Exception as e:
            logger.error(f"Error in check_pending_transactions: {e}")
            time.sleep(60)

# Вспомогательная функция для удаления предыдущего сообщения
async def delete_previous_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

# Обработчики команд и состояний
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_id = user.id
    
    if is_banned(user_id):
        await update.message.reply_text("Вы забанены. Обратитесь к поддержке.")
        return ConversationHandler.END
    
    existing_user = get_user(user_id)
    if existing_user:
        if existing_user['captcha_passed']:
            lang = existing_user['language'] or 'ru'
            await update.message.reply_text(get_text(lang, 'welcome'))
            await show_main_menu(update, context, user_id, lang)
            return MAIN_MENU
    
    captcha_code = ''.join(random.choices('0123456789', k=5))
    context.user_data['captcha'] = captcha_code
    
    await update.message.reply_text(
        get_text('ru', 'captcha', code=captcha_code)
    )
    return CAPTCHA

async def check_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text
    user = update.message.from_user
    
    if user_input == context.user_data.get('captcha'):
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO users (user_id, username, first_name, captcha_passed) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET captcha_passed = %s',
            (user.id, user.username, user.first_name, 1, 1)
        )
        conn.commit()
        conn.close()
        
        keyboard = [
            [InlineKeyboardButton("Русский", callback_data='ru')],
            [InlineKeyboardButton("English", callback_data='en')],
            [InlineKeyboardButton("ქართული", callback_data='ka')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text('Выберите язык / Select language / აირჩიეთ ენა:', reply_markup=reply_markup)
        return LANGUAGE
    else:
        await update.message.reply_text(get_text('ru', 'captcha_failed'))
        return CAPTCHA

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    lang_code = query.data
    
    update_user(user_id, language=lang_code)
    
    await query.answer()
    await query.edit_message_text(text=get_text(lang_code, 'language_selected'))
    
    await show_main_menu(update, context, user_id, lang_code)
    return MAIN_MENU

async def show_main_menu(update, context, user_id, lang):
    user = get_user(user_id)
    if not user:
        return
    
    # Описание магазина
    shop_description = "🏪 AutoShop - лучшие товары с доставкой по Грузии\n\n"
    
    # Текст с информацией о пользователе
    user_info_text = get_text(
        lang, 
        'main_menu', 
        name=user['first_name'] or 'N/A',
        username=user['username'] or 'N/A',
        purchases=user['purchase_count'] or 0,
        discount=user['discount'] or 0,
        balance=user['balance'] or 0
    )
    
    # Полное сообщение с описанием магазина и информацией пользователя
    full_text = shop_description + user_info_text
    
    # Создаем клавиатуру
    keyboard = [
        [InlineKeyboardButton("Тбилиси", callback_data="city_Тбилиси")],
        [InlineKeyboardButton("Гори", callback_data="city_Гори")],
        [InlineKeyboardButton("Кутаиси", callback_data="city_Кутаиси")],
        [InlineKeyboardButton("Батуми", callback_data="city_Батуми")],
        [
            InlineKeyboardButton(f"💰 Баланс: {user['balance'] or 0} лари", callback_data="balance"),
            InlineKeyboardButton("📦 Последний заказ", callback_data="last_order")
        ],
        [
            InlineKeyboardButton("🎁 Бонусы", callback_data="bonuses"),
            InlineKeyboardButton("📚 Правила", callback_data="rules")
        ],
        [InlineKeyboardButton("👨‍💻 Оператор", callback_data="operator")],
        [InlineKeyboardButton("🔧 Техподдержка", callback_data="support")],
        [InlineKeyboardButton("📢 Наш канал", callback_data="channel")],
        [InlineKeyboardButton("⭐ Отзывы", callback_data="reviews")],
        [InlineKeyboardButton("🌐 Наш сайт", callback_data="website")],
        [InlineKeyboardButton("🤖 Личный бот", callback_data="personal_bot")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # URL изображения
    image_url = "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true"
    
    # Удаляем предыдущее сообщение, если есть
    if 'last_message_id' in context.user_data:
        await delete_previous_message(context, user_id, context.user_data['last_message_id'])
    
    # Отправляем новое сообщение с фото
    message = await context.bot.send_photo(
        chat_id=user_id,
        photo=image_url,
        caption=full_text,
        reply_markup=reply_markup
    )
    
    # Сохраняем ID сообщения для возможного удаления в будущем
    context.user_data['last_message_id'] = message.message_id

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = query.data
    
    # Удаляем предыдущее сообщение с меню
    if 'last_message_id' in context.user_data:
        await delete_previous_message(context, user_id, context.user_data['last_message_id'])
    
    if data.startswith('city_'):
        city = data.replace('city_', '')
        context.user_data['city'] = city
        
        keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in PRODUCTS[city].keys()]
        keyboard.append([InlineKeyboardButton(get_text(lang, 'back'), callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'select_category'),
            reply_markup=reply_markup
        )
        context.user_data['last_message_id'] = message.message_id
        return CATEGORY
    elif data == 'balance':
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'balance_add')
        )
        context.user_data['last_message_id'] = message.message_id
        return BALANCE
    elif data == 'last_order':
        last_order = get_last_order(user_id)
        if last_order:
            order_text = (
                f"📦 Товар: {last_order['product']}\n"
                f"💵 Стоимость: {last_order['price']} лари\n"
                f"🏙 Район: {last_order['district']}\n"
                f"🚚 Тип доставки: {last_order['delivery_type']}\n"
                f"🕐 Время заказа: {last_order['purchase_time']}\n"
                f"📊 Статус: {last_order['status']}"
            )
            message = await context.bot.send_message(
                chat_id=user_id,
                text=order_text
            )
        else:
            message = await context.bot.send_message(
                chat_id=user_id,
                text=get_text(lang, 'no_orders')
            )
        context.user_data['last_message_id'] = message.message_id
        return MAIN_MENU
    elif data == 'bonuses':
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'bonuses')
        )
        context.user_data['last_message_id'] = message.message_id
        return MAIN_MENU
    elif data == 'rules':
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'rules')
        )
        context.user_data['last_message_id'] = message.message_id
        return MAIN_MENU
    elif data == 'operator' or data == 'support':
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'support')
        )
        context.user_data['last_message_id'] = message.message_id
        return MAIN_MENU
    elif data == 'channel':
        message = await context.bot.send_message(
            chat_id=user_id,
            text="https://t.me/your_channel"
        )
        context.user_data['last_message_id'] = message.message_id
        return MAIN_MENU
    elif data == 'reviews':
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'reviews')
        )
        context.user_data['last_message_id'] = message.message_id
        return MAIN_MENU
    elif data == 'website':
        message = await context.bot.send_message(
            chat_id=user_id,
            text="https://yourwebsite.com"
        )
        context.user_data['last_message_id'] = message.message_id
        return MAIN_MENU
    elif data == 'personal_bot':
        message = await context.bot.send_message(
            chat_id=user_id,
            text="https://t.me/your_bot"
        )
        context.user_data['last_message_id'] = message.message_id
        return MAIN_MENU
    elif data == 'back_to_main':
        await show_main_menu(update, context, user_id, lang)
        return MAIN_MENU
    
    return MAIN_MENU

async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = query.data
    
    # Удаляем предыдущее сообщение
    if 'last_message_id' in context.user_data:
        await delete_previous_message(context, user_id, context.user_data['last_message_id'])
    
    if data == 'back_to_main':
        await show_main_menu(update, context, user_id, lang)
        return MAIN_MENU
    
    category = data.replace('cat_', '')
    city = context.user_data.get('city')
    
    if city not in PRODUCTS or category not in PRODUCTS[city]:
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'error')
        )
        context.user_data['last_message_id'] = message.message_id
        return CATEGORY
    
    context.user_data['category'] = category
    context.user_data['price'] = PRODUCTS[city][category]['price']
    
    districts = DISTRICTS.get(city, [])
    keyboard = [[InlineKeyboardButton(district, callback_data=f"dist_{district}")] for district in districts]
    keyboard.append([InlineKeyboardButton(get_text(lang, 'back'), callback_data="back_to_category")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await context.bot.send_message(
        chat_id=user_id,
        text=get_text(lang, 'select_district'),
        reply_markup=reply_markup
    )
    context.user_data['last_message_id'] = message.message_id
    return DISTRICT

async def handle_district(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = query.data
    
    # Удаляем предыдущее сообщение
    if 'last_message_id' in context.user_data:
        await delete_previous_message(context, user_id, context.user_data['last_message_id'])
    
    if data == 'back_to_category':
        city = context.user_data.get('city')
        keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in PRODUCTS[city].keys()]
        keyboard.append([InlineKeyboardButton(get_text(lang, 'back'), callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'select_category'),
            reply_markup=reply_markup
        )
        context.user_data['last_message_id'] = message.message_id
        return CATEGORY
    
    district = data.replace('dist_', '')
    city = context.user_data.get('city')
    
    if city not in DISTRICTS or district not in DISTRICTS[city]:
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'error')
        )
        context.user_data['last_message_id'] = message.message_id
        return DISTRICT
    
    context.user_data['district'] = district
    
    keyboard = [[InlineKeyboardButton(del_type, callback_data=f"del_{del_type}")] for del_type in DELIVERY_TYPES]
    keyboard.append([InlineKeyboardButton(get_text(lang, 'back'), callback_data="back_to_district")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await context.bot.send_message(
        chat_id=user_id,
        text=get_text(lang, 'select_delivery'),
        reply_markup=reply_markup
    )
    context.user_data['last_message_id'] = message.message_id
    return DELIVERY

async def handle_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = query.data
    
    # Удаляем предыдущее сообщение
    if 'last_message_id' in context.user_data:
        await delete_previous_message(context, user_id, context.user_data['last_message_id'])
    
    if data == 'back_to_district':
        city = context.user_data.get('city')
        districts = DISTRICTS.get(city, [])
        keyboard = [[InlineKeyboardButton(district, callback_data=f"dist_{district}")] for district in districts]
        keyboard.append([InlineKeyboardButton(get_text(lang, 'back'), callback_data="back_to_category")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'select_district'),
            reply_markup=reply_markup
        )
        context.user_data['last_message_id'] = message.message_id
        return DISTRICT
    
    delivery_type = data.replace('del_', '')
    
    if delivery_type not in DELIVERY_TYPES:
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'error')
        )
        context.user_data['last_message_id'] = message.message_id
        return DELIVERY
    
    context.user_data['delivery_type'] = delivery_type
    
    city = context.user_data.get('city')
    category = context.user_data.get('category')
    price = context.user_data.get('price')
    district = context.user_data.get('district')
    
    order_text = get_text(
        lang, 
        'order_summary',
        product=category,
        price=price,
        district=district,
        delivery_type=delivery_type
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Да", callback_data="confirm_yes")],
        [InlineKeyboardButton("❌ Нет", callback_data="confirm_no")],
        [InlineKeyboardButton(get_text(lang, 'back'), callback_data="back_to_delivery")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await context.bot.send_message(
        chat_id=user_id,
        text=order_text,
        reply_markup=reply_markup
    )
    context.user_data['last_message_id'] = message.message_id
    return CONFIRMATION

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = query.data
    
    # Удаляем предыдущее сообщение
    if 'last_message_id' in context.user_data:
        await delete_previous_message(context, user_id, context.user_data['last_message_id'])
    
    if data == 'back_to_delivery':
        keyboard = [[InlineKeyboardButton(del_type, callback_data=f"del_{del_type}")] for del_type in DELIVERY_TYPES]
        keyboard.append([InlineKeyboardButton(get_text(lang, 'back'), callback_data="back_to_district")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'select_delivery'),
            reply_markup=reply_markup
        )
        context.user_data['last_message_id'] = message.message_id
        return DELIVERY
    
    if data == 'confirm_yes':
        city = context.user_data.get('city')
        category = context.user_data.get('category')
        price = context.user_data.get('price')
        district = context.user_data.get('district')
        delivery_type = context.user_data.get('delivery_type')
        
        product_info = f"{category} в {city}, район {district}, {delivery_type}"
        
        order = create_coingate_order(price, 'USD', product_info)
        
        if order:
            expires_at = datetime.now() + timedelta(minutes=30)
            add_transaction(
                user_id,
                price,
                'USD',
                order['id'],
                order['payment_url'],
                expires_at,
                product_info
            )
            
            payment_text = get_text(
                lang,
                'payment_instructions',
                amount=price,
                currency='USD',
                payment_address=order['payment_url']
            )
            
            message = await context.bot.send_message(
                chat_id=user_id,
                text=payment_text,
                parse_mode='Markdown'
            )
            context.user_data['last_message_id'] = message.message_id
            
            context.job_queue.run_once(
                check_payment,
                1800,
                context={
                    'user_id': user_id,
                    'order_id': order['id'],
                    'chat_id': user_id,
                    'product_info': product_info,
                    'lang': lang
                }
            )
            
            return PAYMENT
        else:
            message = await context.bot.send_message(
                chat_id=user_id,
                text=get_text(lang, 'error')
            )
            context.user_data['last_message_id'] = message.message_id
            return CONFIRMATION
    else:
        await show_main_menu(update, context, user_id, lang)
        return MAIN_MENU

async def check_payment(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    order_id = job.context['order_id']
    user_id = job.context['user_id']
    chat_id = job.context['chat_id']
    lang = job.context['lang']
    
    status_info = check_payment_status(order_id)
    
    if status_info and status_info.get('status') == 'paid':
        product_info = job.context['product_info']
        
        add_purchase(
            user_id,
            product_info,
            status_info['price_amount'],
            '',
            ''
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_text(lang, 'payment_success', product_image=PRODUCTS['Тбилиси']['0.5 меф']['image'])
        )
        
        update_transaction_status(order_id, 'paid')
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_text(lang, 'payment_timeout')
        )
        
        user = get_user(user_id)
        failed_payments = user['failed_payments'] + 1
        update_user(user_id, failed_payments=failed_payments)
        
        if failed_payments >= 3:
            ban_until = datetime.now() + timedelta(hours=24)
            update_user(user_id, ban_until=ban_until.strftime('%Y-%m-%d %H:%M:%S'))
            await context.bot.send_message(
                chat_id=chat_id,
                text=get_text(lang, 'ban_message')
            )
        
        update_transaction_status(order_id, 'expired')

async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data['language'] or 'ru'
    amount_text = update.message.text
    
    try:
        amount = float(amount_text)
        if amount <= 0:
            await update.message.reply_text(get_text(lang, 'error'))
            return BALANCE
        
        current_balance = user_data['balance'] or 0
        new_balance = current_balance + amount
        update_user(user.id, balance=new_balance)
        
        await update.message.reply_text(
            get_text(lang, 'balance_add_success', amount=amount, balance=new_balance)
        )
        
        await show_main_menu(update, context, user.id, lang)
        return MAIN_MENU
    except ValueError:
        await update.message.reply_text(get_text(lang, 'error'))
        return BALANCE

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    user_data = get_user(user_id)
    lang = user_data['language'] or 'ru'
    await show_main_menu(update, context, user_id, lang)
    return MAIN_MENU

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    user_data = get_user(user_id)
    lang = user_data['language'] or 'ru'
    text = update.message.text
    
    # Если текст является числом - пополняем баланс
    if text.isdigit():
        context.user_data['balance_amount'] = float(text)
        return await handle_balance(update, context)
    else:
        # Любой другой текст возвращает в главное меню
        await show_main_menu(update, context, user_id, lang)
        return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data['language'] or 'ru'
    
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    
    if update is None:
        logger.error("Update is None, cannot process error")
        return
    
    try:
        chat_id = None
        user = None
        
        if update.message:
            chat_id = update.message.chat_id
            user = update.message.from_user
        elif update.callback_query and update.callback_query.message:
            chat_id = update.callback_query.message.chat_id
            user = update.callback_query.from_user
        elif update.callback_query:
            chat_id = update.callback_query.from_user.id
            user = update.callback_query.from_user
        elif update.effective_chat:
            chat_id = update.effective_chat.id
            user = update.effective_user
        
        if chat_id is None:
            logger.error("Cannot determine chat_id for error message")
            return
        
        user_data = get_user(user.id) if user else None
        lang = user_data['language'] or 'ru' if user_data else 'ru'
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_text(lang, 'error')
        )
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")

def main():
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start), CommandHandler('menu', menu_command)],
        states={
            CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_captcha)],
            LANGUAGE: [CallbackQueryHandler(set_language)],
            MAIN_MENU: [CallbackQueryHandler(handle_main_menu)],
            CATEGORY: [CallbackQueryHandler(handle_category)],
            DISTRICT: [CallbackQueryHandler(handle_district)],
            DELIVERY: [CallbackQueryHandler(handle_delivery)],
            CONFIRMATION: [CallbackQueryHandler(handle_confirmation)],
            BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_balance)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(error)
    
    # Запускаем проверку pending транзакций в отдельном потоке
    Thread(target=check_pending_transactions, args=(application,), daemon=True).start()
    
    # Определяем порт для Render
    port = int(os.environ.get('PORT', 5000))
    
    # Используем вебхуки на Render, поллинг локально
    if 'RENDER' in os.environ:
        # На Render - используем вебхуки
        webhook_url = os.environ.get('RENDER_EXTERNAL_URL', '')
        if webhook_url:
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=TOKEN,
                webhook_url=f"{webhook_url}/{TOKEN}",
                drop_pending_updates=True
            )
        else:
            application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    else:
        # Локально - используем поллинг
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
