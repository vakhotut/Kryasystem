import logging
import random
import time
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup
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
from threading import Thread

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройки бота
TOKEN = "YOUR_BOT_TOKEN"
COINGATE_API_TOKEN = "YOUR_COINGATE_API_TOKEN"
COINGATE_API_URL = "https://api.coingate.com/api/v2/orders"

# Состояния разговора
CAPTCHA, LANGUAGE, MAIN_MENU, CITY, CATEGORY, DISTRICT, DELIVERY, CONFIRMATION, PAYMENT, BALANCE, SUPPORT, BONUSES, RULES, REVIEWS = range(14)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
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
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def update_user(user_id, **kwargs):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    for key, value in kwargs.items():
        cursor.execute(f'UPDATE users SET {key} = ? WHERE user_id = ?', (value, user_id))
    
    conn.commit()
    conn.close()

def add_transaction(user_id, amount, currency, order_id, payment_url, expires_at, product_info):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT INTO transactions (user_id, amount, currency, status, order_id, payment_url, expires_at, product_info)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, amount, currency, 'pending', order_id, payment_url, expires_at, product_info))
    
    conn.commit()
    conn.close()

def add_purchase(user_id, product, price, district, delivery_type):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT INTO purchases (user_id, product, price, district, delivery_type)
    VALUES (?, ?, ?, ?, ?)
    ''', (user_id, product, price, district, delivery_type))
    
    # Обновляем счетчик покупок
    cursor.execute('SELECT purchase_count FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    purchase_count = result[0] + 1 if result else 1
    cursor.execute('UPDATE users SET purchase_count = ? WHERE user_id = ?', (purchase_count, user_id))
    
    conn.commit()
    conn.close()

def get_pending_transactions():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM transactions WHERE status = "pending" AND expires_at > datetime("now")')
    transactions = cursor.fetchall()
    conn.close()
    return transactions

def update_transaction_status(order_id, status):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET status = ? WHERE order_id = ?', (status, order_id))
    conn.commit()
    conn.close()

# Инициализация базы данных при запуске
init_db()

# Тексты на разных языках
TEXTS = {
    'ru': {
        'welcome': 'Добро пожаловать!',
        'captcha': 'Для входа решите каптчу: {}\nВведите 5 цифр:',
        'captcha_failed': 'Неверная каптча! Попробуйте снова:',
        'language_selected': 'Язык установлен: Русский',
        'main_menu': (
            "🏪 Магазин AutoShop\n"
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
        'ban_message': 'Вы забанены на 24 часа из-за 3 неудачных попыток оплаты.'
    },
    'en': {
        'welcome': 'Welcome!',
        'captcha': 'To enter, solve the captcha: {}\nEnter 5 digits:',
        'captcha_failed': 'Invalid captcha! Try again:',
        'language_selected': 'Language set: English',
        'main_menu': (
            "🏪 AutoShop Store\n"
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
        'ban_message': 'You are banned for 24 hours due to 3 failed payment attempts.'
    },
    'ka': {
        'welcome': 'კეთილი იყოს თქვენი მობრძანება!',
        'captcha': 'შესასვლელად გადაწყვიტეთ captcha: {}\nშეიყვანეთ 5 ციფრი:',
        'captcha_failed': 'არასწორი captcha! სცადეთ თავიდან:',
        'language_selected': 'ენა დაყენებულია: ქართული',
        'main_menu': (
            "🏪 AutoShop მაღაზია\n"
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
        'ban_message': '3 წარუმატებელი გადახდის მცდელობის გამო თქვენ დაბლოკილი ხართ 24 საათის განმავლობაში.'
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
    'Кутаиси': ['Центр', 'Западный', 'Восточный'],
    'Батуми': ['Центр', 'Бульвар', 'Старый город']
}

DELIVERY_TYPES = ['Подъезд', 'Прикоп', 'Магнит', 'Во дворах']

# Функция для получения текста на нужном языке
def get_text(lang, key, **kwargs):
    text = TEXTS[lang][key]
    if kwargs:
        text = text.format(**kwargs)
    return text

# Функция для проверки бана пользователя
def is_banned(user_id):
    user = get_user(user_id)
    if user and user[5]:  # ban_until
        ban_until = datetime.strptime(user[5], '%Y-%m-%d %H:%M:%S')
        if ban_until > datetime.now():
            return True
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
        'callback_url': 'https://yourdomain.com/callback',  # Замените на ваш URL
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

# Поток для проверки pending транзакций
def check_pending_transactions(app):
    while True:
        try:
            transactions = get_pending_transactions()
            for transaction in transactions:
                order_id = transaction[5]
                status_info = check_payment_status(order_id)
                
                if status_info:
                    status = status_info.get('status')
                    if status == 'paid':
                        # Обновляем статус транзакции
                        update_transaction_status(order_id, 'paid')
                        
                        # Находим пользователя и отправляем товар
                        user_id = transaction[1]
                        product_info = transaction[8]
                        
                        # Отправляем сообщение о успешной оплате
                        asyncio.run_coroutine_threadsafe(
                            app.bot.send_message(
                                chat_id=user_id,
                                text=get_text('ru', 'payment_success', product_image=PRODUCTS['Тбилиси']['0.5 меф']['image'])
                            ),
                            app.loop
                        )
                        
                    elif status == 'expired' or status == 'canceled':
                        update_transaction_status(order_id, status)
            
            time.sleep(60)  # Проверяем каждую минуту
        except Exception as e:
            logger.error(f"Error in check_pending_transactions: {e}")
            time.sleep(60)

# Обработчики команд и состояний
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_id = user.id
    
    # Проверяем, не забанен ли пользователь
    if is_banned(user_id):
        await update.message.reply_text("Вы забанены. Обратитесь к поддержке.")
        return ConversationHandler.END
    
    # Проверяем, есть ли пользователь в базе
    existing_user = get_user(user_id)
    if existing_user:
        # Если пользователь уже проходил каптчу
        if existing_user[4]:  # captcha_passed
            lang = existing_user[3] or 'ru'
            await update.message.reply_text(get_text(lang, 'welcome'))
            await show_main_menu(update, context, user_id, lang)
            return MAIN_MENU
    
    # Генерируем каптчу для новых пользователей
    captcha_code = ''.join(random.choices('0123456789', k=5))
    context.user_data['captcha'] = captcha_code
    
    await update.message.reply_text(
        get_text('ru', 'captcha', code=captcha_code),
        reply_markup=ReplyKeyboardRemove()
    )
    return CAPTCHA

async def check_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text
    user = update.message.from_user
    
    if user_input == context.user_data.get('captcha'):
        # Сохраняем пользователя в базу
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR IGNORE INTO users (user_id, username, first_name, captcha_passed) VALUES (?, ?, ?, ?)',
            (user.id, user.username, user.first_name, 1)
        )
        conn.commit()
        conn.close()
        
        # Предлагаем выбрать язык
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
    
    # Обновляем язык пользователя в базе
    update_user(user_id, language=lang_code)
    
    await query.answer()
    await query.edit_message_text(text=get_text(lang_code, 'language_selected'))
    
    await show_main_menu(update, context, user_id, lang_code)
    return MAIN_MENU

async def show_main_menu(update, context, user_id, lang):
    user = get_user(user_id)
    text = get_text(
        lang, 
        'main_menu', 
        name=user[2],  # first_name
        username=user[1] or 'N/A',  # username
        purchases=user[7] or 0,  # purchase_count
        discount=user[8] or 0,  # discount
        balance=user[9] or 0  # balance
    )
    
    buttons = [
        ['🛒 Купить', '💳 Пополнить баланс'],
        ['🎁 Бонусы', '📚 Правила'],
        ['👨‍💻 Оператор', '🔧 Техподдержка'],
        ['📢 Наш канал', '⭐ Отзывы'],
        ['🌐 Наш сайт', '🤖 Личный бот']
    ]
    
    if hasattr(update, 'message'):
        await update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    else:
        await update.callback_query.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    text = update.message.text
    
    if text == '🛒 Купить':
        cities_keyboard = [[city] for city in PRODUCTS.keys()]
        await update.message.reply_text(
            get_text(lang, 'select_city'),
            reply_markup=ReplyKeyboardMarkup(cities_keyboard, resize_keyboard=True)
        )
        return CITY
    elif text == '💳 Пополнить баланс':
        await update.message.reply_text(
            get_text(lang, 'balance_add'),
            reply_markup=ReplyKeyboardRemove()
        )
        return BALANCE
    elif text == '🎁 Бонусы':
        await update.message.reply_text(get_text(lang, 'bonuses'))
        return MAIN_MENU
    elif text == '📚 Правила':
        await update.message.reply_text(get_text(lang, 'rules'))
        return MAIN_MENU
    elif text == '👨‍💻 Оператор':
        await update.message.reply_text(get_text(lang, 'support'))
        return MAIN_MENU
    elif text == '🔧 Техподдержка':
        await update.message.reply_text(get_text(lang, 'support'))
        return MAIN_MENU
    elif text == '📢 Наш канал':
        await update.message.reply_text("https://t.me/your_channel")  # Замените на ваш канал
        return MAIN_MENU
    elif text == '⭐ Отзывы':
        await update.message.reply_text(get_text(lang, 'reviews'))
        return MAIN_MENU
    elif text == '🌐 Наш сайт':
        await update.message.reply_text("https://yourwebsite.com")  # Замените на ваш сайт
        return MAIN_MENU
    elif text == '🤖 Личный бот':
        await update.message.reply_text("https://t.me/your_bot")  # Замените на вашего бота
        return MAIN_MENU
    
    return MAIN_MENU

async def handle_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    city = update.message.text
    
    if city not in PRODUCTS:
        await update.message.reply_text(get_text(lang, 'error'))
        return CITY
    
    context.user_data['city'] = city
    
    # Создаем клавиатуру с категориями товаров
    categories = list(PRODUCTS[city].keys())
    categories_keyboard = [[cat] for cat in categories]
    
    await update.message.reply_text(
        get_text(lang, 'select_category'),
        reply_markup=ReplyKeyboardMarkup(categories_keyboard, resize_keyboard=True)
    )
    return CATEGORY

async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    category = update.message.text
    city = context.user_data.get('city')
    
    if city not in PRODUCTS or category not in PRODUCTS[city]:
        await update.message.reply_text(get_text(lang, 'error'))
        return CATEGORY
    
    context.user_data['category'] = category
    context.user_data['price'] = PRODUCTS[city][category]['price']
    
    # Создаем клавиатуру с районами
    districts = DISTRICTS.get(city, [])
    districts_keyboard = [[district] for district in districts]
    
    await update.message.reply_text(
        get_text(lang, 'select_district'),
        reply_markup=ReplyKeyboardMarkup(districts_keyboard, resize_keyboard=True)
    )
    return DISTRICT

async def handle_district(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    district = update.message.text
    city = context.user_data.get('city')
    
    if city not in DISTRICTS or district not in DISTRICTS[city]:
        await update.message.reply_text(get_text(lang, 'error'))
        return DISTRICT
    
    context.user_data['district'] = district
    
    # Создаем клавиатуру с типами доставки
    delivery_keyboard = [[del_type] for del_type in DELIVERY_TYPES]
    
    await update.message.reply_text(
        get_text(lang, 'select_delivery'),
        reply_markup=ReplyKeyboardMarkup(delivery_keyboard, resize_keyboard=True)
    )
    return DELIVERY

async def handle_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    delivery_type = update.message.text
    
    if delivery_type not in DELIVERY_TYPES:
        await update.message.reply_text(get_text(lang, 'error'))
        return DELIVERY
    
    context.user_data['delivery_type'] = delivery_type
    
    # Формируем информацию о заказе
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
    
    # Кнопки подтверждения
    keyboard = [
        ['✅ Да', '❌ Нет']
    ]
    
    await update.message.reply_text(
        order_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return CONFIRMATION

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    confirmation = update.message.text
    
    if confirmation == '✅ Да':
        # Создаем заказ в CoinGate
        city = context.user_data.get('city')
        category = context.user_data.get('category')
        price = context.user_data.get('price')
        district = context.user_data.get('district')
        delivery_type = context.user_data.get('delivery_type')
        
        product_info = f"{category} в {city}, район {district}, {delivery_type}"
        
        # Создаем заказ в CoinGate
        order = create_coingate_order(price, 'USD', product_info)
        
        if order:
            # Сохраняем транзакцию в базу
            expires_at = datetime.now() + timedelta(minutes=30)
            add_transaction(
                user.id,
                price,
                'USD',
                order['id'],
                order['payment_url'],
                expires_at,
                product_info
            )
            
            # Показываем инструкции по оплате
            payment_text = get_text(
                lang,
                'payment_instructions',
                amount=price,
                currency='USD',
                payment_address=order['payment_url']
            )
            
            await update.message.reply_text(
                payment_text,
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardRemove()
            )
            
            # Запускаем таймер для проверки оплаты
            context.job_queue.run_once(
                check_payment,
                1800,  # 30 минут
                context={
                    'user_id': user.id,
                    'order_id': order['id'],
                    'chat_id': update.message.chat_id,
                    'product_info': product_info,
                    'lang': lang
                }
            )
            
            return PAYMENT
        else:
            await update.message.reply_text(get_text(lang, 'error'))
            return CONFIRMATION
    else:
        await show_main_menu(update, context, user.id, lang)
        return MAIN_MENU

async def check_payment(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    order_id = job.context['order_id']
    user_id = job.context['user_id']
    chat_id = job.context['chat_id']
    lang = job.context['lang']
    
    # Проверяем статус платежа
    status_info = check_payment_status(order_id)
    
    if status_info and status_info.get('status') == 'paid':
        # Платеж получен, отправляем товар
        product_info = job.context['product_info']
        
        # Добавляем покупку в историю
        add_purchase(
            user_id,
            product_info,
            status_info['price_amount'],
            '',  # district
            ''   # delivery_type
        )
        
        # Отправляем сообщение о успешной оплате
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_text(lang, 'payment_success', product_image=PRODUCTS['Тбилиси']['0.5 меф']['image'])
        )
        
        # Обновляем статус транзакции
        update_transaction_status(order_id, 'paid')
    else:
        # Платеж не получен
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_text(lang, 'payment_timeout')
        )
        
        # Увеличиваем счетчик неудачных платежей
        user = get_user(user_id)
        failed_payments = user[6] + 1  # failed_payments
        update_user(user_id, failed_payments=failed_payments)
        
        # Если 3 неудачных платежа, бан на 24 часа
        if failed_payments >= 3:
            ban_until = datetime.now() + timedelta(hours=24)
            update_user(user_id, ban_until=ban_until.strftime('%Y-%m-%d %H:%M:%S'))
            await context.bot.send_message(
                chat_id=chat_id,
                text=get_text(lang, 'ban_message')
            )
        
        # Обновляем статус транзакции
        update_transaction_status(order_id, 'expired')

async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    amount_text = update.message.text
    
    try:
        amount = float(amount_text)
        if amount <= 0:
            await update.message.reply_text(get_text(lang, 'error'))
            return BALANCE
        
        # Обновляем баланс пользователя
        current_balance = user_data[9] or 0
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

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    
    await update.message.reply_text(
        "Операция отменена.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    
    try:
        user = update.message.from_user
        user_data = get_user(user.id)
        lang = user_data[3] or 'ru'
        await update.message.reply_text(get_text(lang, 'error'))
    except:
        await update.message.reply_text("Произошла ошибка. Попробуйте позже.")

def main():
    # Создаем Application и передаем ему токен бота
    application = Application.builder().token(TOKEN).build()
    
    # Определяем обработчик разговоров
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_captcha)],
            LANGUAGE: [CallbackQueryHandler(set_language)],
            MAIN_MENU: [MessageHandler(filters.TEXT, handle_main_menu)],
            CITY: [MessageHandler(filters.TEXT, handle_city)],
            CATEGORY: [MessageHandler(filters.TEXT, handle_category)],
            DISTRICT: [MessageHandler(filters.TEXT, handle_district)],
            DELIVERY: [MessageHandler(filters.TEXT, handle_delivery)],
            CONFIRMATION: [MessageHandler(filters.TEXT, handle_confirmation)],
            BALANCE: [MessageHandler(filters.TEXT, handle_balance)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Добавляем обработчик разговоров в диспетчер
    application.add_handler(conv_handler)
    
    # Добавляем обработчик ошибок
    application.add_error_handler(error)
    
    # Запускаем поток проверки транзакций
    Thread(target=check_pending_transactions, args=(application,), daemon=True).start()
    
    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()# Состояния разговора
CAPTCHA, LANGUAGE, MAIN_MENU, CITY, CATEGORY, DISTRICT, DELIVERY, CONFIRMATION, PAYMENT, BALANCE, SUPPORT, BONUSES, RULES, REVIEWS = range(14)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
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
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def update_user(user_id, **kwargs):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    for key, value in kwargs.items():
        cursor.execute(f'UPDATE users SET {key} = ? WHERE user_id = ?', (value, user_id))
    
    conn.commit()
    conn.close()

def add_transaction(user_id, amount, currency, order_id, payment_url, expires_at, product_info):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT INTO transactions (user_id, amount, currency, status, order_id, payment_url, expires_at, product_info)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, amount, currency, 'pending', order_id, payment_url, expires_at, product_info))
    
    conn.commit()
    conn.close()

def add_purchase(user_id, product, price, district, delivery_type):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT INTO purchases (user_id, product, price, district, delivery_type)
    VALUES (?, ?, ?, ?, ?)
    ''', (user_id, product, price, district, delivery_type))
    
    conn.commit()
    conn.close()
    
    # Обновляем счетчик покупок
    cursor.execute('SELECT purchase_count FROM users WHERE user_id = ?', (user_id,))
    purchase_count = cursor.fetchone()[0] + 1
    cursor.execute('UPDATE users SET purchase_count = ? WHERE user_id = ?', (purchase_count, user_id))
    
    conn.commit()
    conn.close()

def get_pending_transactions():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM transactions WHERE status = "pending" AND expires_at > datetime("now")')
    transactions = cursor.fetchall()
    conn.close()
    return transactions

def update_transaction_status(order_id, status):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET status = ? WHERE order_id = ?', (status, order_id))
    conn.commit()
    conn.close()

# Инициализация базы данных при запуске
init_db()

# Тексты на разных языках
TEXTS = {
    'ru': {
        'welcome': 'Добро пожаловать!',
        'captcha': 'Для входа решите каптчу: {}\nВведите 5 цифр:',
        'captcha_failed': 'Неверная каптча! Попробуйте снова:',
        'language_selected': 'Язык установлен: Русский',
        'main_menu': (
            "🏪 Магазин AutoShop\n"
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
        'error': 'Произошла ошибка. Попробуйте позже.'
    },
    'en': {
        'welcome': 'Welcome!',
        'captcha': 'To enter, solve the captcha: {}\nEnter 5 digits:',
        'captcha_failed': 'Invalid captcha! Try again:',
        'language_selected': 'Language set: English',
        'main_menu': (
            "🏪 AutoShop Store\n"
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
        'error': 'An error occurred. Please try again later.'
    },
    'ka': {
        'welcome': 'კეთილი იყოს თქვენი მობრძანება!',
        'captcha': 'შესასვლელად გადაწყვიტეთ captcha: {}\nშეიყვანეთ 5 ციფრი:',
        'captcha_failed': 'არასწორი captcha! სცადეთ თავიდან:',
        'language_selected': 'ენა დაყენებულია: ქართული',
        'main_menu': (
            "🏪 AutoShop მაღაზია\n"
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
        'error': 'მოხდა შეცდომა. სცადეთ მოგვიანებით.'
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
    'Кутаиси': ['Центр', 'Западный', 'Восточный'],
    'Батуми': ['Центр', 'Бульвар', 'Старый город']
}

DELIVERY_TYPES = ['Подъезд', 'Прикоп', 'Магнит', 'Во дворах']

# Функция для получения текста на нужном языке
def get_text(lang, key, **kwargs):
    text = TEXTS[lang][key]
    if kwargs:
        text = text.format(**kwargs)
    return text

# Функция для проверки бана пользователя
def is_banned(user_id):
    user = get_user(user_id)
    if user and user[5]:  # ban_until
        ban_until = datetime.strptime(user[5], '%Y-%m-%d %H:%M:%S')
        if ban_until > datetime.now():
            return True
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
        'callback_url': 'https://yourdomain.com/callback',  # Замените на ваш URL
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

# Поток для проверки pending транзакций
def check_pending_transactions():
    while True:
        try:
            transactions = get_pending_transactions()
            for transaction in transactions:
                order_id = transaction[5]
                status_info = check_payment_status(order_id)
                
                if status_info:
                    status = status_info.get('status')
                    if status == 'paid':
                        # Обновляем статус транзакции
                        update_transaction_status(order_id, 'paid')
                        
                        # Находим пользователя и отправляем товар
                        user_id = transaction[1]
                        product_info = transaction[8]
                        
                        # Здесь должен быть код отправки товара пользователю
                        # ...
                        
                    elif status == 'expired' or status == 'canceled':
                        update_transaction_status(order_id, status)
            
            time.sleep(60)  # Проверяем каждую минуту
        except Exception as e:
            logger.error(f"Error in check_pending_transactions: {e}")
            time.sleep(60)

# Запускаем поток проверки транзакций
Thread(target=check_pending_transactions, daemon=True).start()

# Обработчики команд и состояний
def start(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_id = user.id
    
    # Проверяем, не забанен ли пользователь
    if is_banned(user_id):
        update.message.reply_text("Вы забанены. Обратитесь к поддержке.")
        return ConversationHandler.END
    
    # Проверяем, есть ли пользователь в базе
    existing_user = get_user(user_id)
    if existing_user:
        # Если пользователь уже проходил каптчу
        if existing_user[4]:  # captcha_passed
            lang = existing_user[3] or 'ru'
            update.message.reply_text(get_text(lang, 'welcome'))
            show_main_menu(update, context, user_id, lang)
            return MAIN_MENU
    
    # Генерируем каптчу для новых пользователей
    captcha_code = ''.join(random.choices('0123456789', k=5))
    context.user_data['captcha'] = captcha_code
    
    update.message.reply_text(
        get_text('ru', 'captcha', code=captcha_code),
        reply_markup=ReplyKeyboardRemove()
    )
    return CAPTCHA

def check_captcha(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text
    user = update.message.from_user
    
    if user_input == context.user_data.get('captcha'):
        # Сохраняем пользователя в базу
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR IGNORE INTO users (user_id, username, first_name, captcha_passed) VALUES (?, ?, ?, ?)',
            (user.id, user.username, user.first_name, 1)
        )
        conn.commit()
        conn.close()
        
        # Предлагаем выбрать язык
        keyboard = [
            [InlineKeyboardButton("Русский", callback_data='ru')],
            [InlineKeyboardButton("English", callback_data='en')],
            [InlineKeyboardButton("ქართული", callback_data='ka')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text('Выберите язык / Select language / აირჩიეთ ენა:', reply_markup=reply_markup)
        return LANGUAGE
    else:
        update.message.reply_text(get_text('ru', 'captcha_failed'))
        return CAPTCHA

def set_language(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    lang_code = query.data
    
    # Обновляем язык пользователя в базе
    update_user(user_id, language=lang_code)
    
    query.answer()
    query.edit_message_text(text=get_text(lang_code, 'language_selected'))
    
    show_main_menu(update, context, user_id, lang_code)
    return MAIN_MENU

def show_main_menu(update, context, user_id, lang):
    user = get_user(user_id)
    text = get_text(
        lang, 
        'main_menu', 
        name=user[2],  # first_name
        username=user[1] or 'N/A',  # username
        purchases=user[7] or 0,  # purchase_count
        discount=user[8] or 0,  # discount
        balance=user[9] or 0  # balance
    )
    
    buttons = [
        ['🛒 Купить', '💳 Пополнить баланс'],
        ['🎁 Бонусы', '📚 Правила'],
        ['👨‍💻 Оператор', '🔧 Техподдержка'],
        ['📢 Наш канал', '⭐ Отзывы'],
        ['🌐 Наш сайт', '🤖 Личный бот']
    ]
    
    if hasattr(update, 'message'):
        update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    else:
        update.callback_query.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))

def handle_main_menu(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    text = update.message.text
    
    if text == '🛒 Купить':
        cities_keyboard = [[city] for city in PRODUCTS.keys()]
        update.message.reply_text(
            get_text(lang, 'select_city'),
            reply_markup=ReplyKeyboardMarkup(cities_keyboard, resize_keyboard=True)
        )
        return CITY
    elif text == '💳 Пополнить баланс':
        update.message.reply_text(
            get_text(lang, 'balance_add'),
            reply_markup=ReplyKeyboardRemove()
        )
        return BALANCE
    elif text == '🎁 Бонусы':
        update.message.reply_text(get_text(lang, 'bonuses'))
        return MAIN_MENU
    elif text == '📚 Правила':
        update.message.reply_text(get_text(lang, 'rules'))
        return MAIN_MENU
    elif text == '👨‍💻 Оператор':
        update.message.reply_text(get_text(lang, 'support'))
        return MAIN_MENU
    elif text == '🔧 Техподдержка':
        update.message.reply_text(get_text(lang, 'support'))
        return MAIN_MENU
    elif text == '📢 Наш канал':
        update.message.reply_text("https://t.me/your_channel")  # Замените на ваш канал
        return MAIN_MENU
    elif text == '⭐ Отзывы':
        update.message.reply_text(get_text(lang, 'reviews'))
        return MAIN_MENU
    elif text == '🌐 Наш сайт':
        update.message.reply_text("https://yourwebsite.com")  # Замените на ваш сайт
        return MAIN_MENU
    elif text == '🤖 Личный бот':
        update.message.reply_text("https://t.me/your_bot")  # Замените на вашего бота
        return MAIN_MENU
    
    return MAIN_MENU

def handle_city(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    city = update.message.text
    
    if city not in PRODUCTS:
        update.message.reply_text(get_text(lang, 'error'))
        return CITY
    
    context.user_data['city'] = city
    
    # Создаем клавиатуру с категориями товаров
    categories = list(PRODUCTS[city].keys())
    categories_keyboard = [[cat] for cat in categories]
    
    update.message.reply_text(
        get_text(lang, 'select_category'),
        reply_markup=ReplyKeyboardMarkup(categories_keyboard, resize_keyboard=True)
    )
    return CATEGORY

def handle_category(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    category = update.message.text
    city = context.user_data.get('city')
    
    if city not in PRODUCTS or category not in PRODUCTS[city]:
        update.message.reply_text(get_text(lang, 'error'))
        return CATEGORY
    
    context.user_data['category'] = category
    context.user_data['price'] = PRODUCTS[city][category]['price']
    
    # Создаем клавиатуру с районами
    districts = DISTRICTS.get(city, [])
    districts_keyboard = [[district] for district in districts]
    
    update.message.reply_text(
        get_text(lang, 'select_district'),
        reply_markup=ReplyKeyboardMarkup(districts_keyboard, resize_keyboard=True)
    )
    return DISTRICT

def handle_district(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    district = update.message.text
    city = context.user_data.get('city')
    
    if city not in DISTRICTS or district not in DISTRICTS[city]:
        update.message.reply_text(get_text(lang, 'error'))
        return DISTRICT
    
    context.user_data['district'] = district
    
    # Создаем клавиатуру с типами доставки
    delivery_keyboard = [[del_type] for del_type in DELIVERY_TYPES]
    
    update.message.reply_text(
        get_text(lang, 'select_delivery'),
        reply_markup=ReplyKeyboardMarkup(delivery_keyboard, resize_keyboard=True)
    )
    return DELIVERY

def handle_delivery(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    delivery_type = update.message.text
    
    if delivery_type not in DELIVERY_TYPES:
        update.message.reply_text(get_text(lang, 'error'))
        return DELIVERY
    
    context.user_data['delivery_type'] = delivery_type
    
    # Формируем информацию о заказе
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
    
    # Кнопки подтверждения
    keyboard = [
        ['✅ Да', '❌ Нет']
    ]
    
    update.message.reply_text(
        order_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return CONFIRMATION

def handle_confirmation(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    confirmation = update.message.text
    
    if confirmation == '✅ Да':
        # Создаем заказ в CoinGate
        city = context.user_data.get('city')
        category = context.user_data.get('category')
        price = context.user_data.get('price')
        district = context.user_data.get('district')
        delivery_type = context.user_data.get('delivery_type')
        
        product_info = f"{category} в {city}, район {district}, {delivery_type}"
        
        # Создаем заказ в CoinGate
        order = create_coingate_order(price, 'USD', product_info)
        
        if order:
            # Сохраняем транзакцию в базу
            expires_at = datetime.now() + timedelta(minutes=30)
            add_transaction(
                user.id,
                price,
                'USD',
                order['id'],
                order['payment_url'],
                expires_at,
                product_info
            )
            
            # Показываем инструкции по оплате
            payment_text = get_text(
                lang,
                'payment_instructions',
                amount=price,
                currency='USD',
                payment_address=order['payment_url']
            )
            
            update.message.reply_text(
                payment_text,
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardRemove()
            )
            
            # Запускаем таймер для проверки оплаты
            context.job_queue.run_once(
                check_payment,
                1800,  # 30 минут
                context={
                    'user_id': user.id,
                    'order_id': order['id'],
                    'chat_id': update.message.chat_id,
                    'message_id': update.message.message_id,
                    'product_info': product_info,
                    'lang': lang
                }
            )
            
            return PAYMENT
        else:
            update.message.reply_text(get_text(lang, 'error'))
            return CONFIRMATION
    else:
        show_main_menu(update, context, user.id, lang)
        return MAIN_MENU

def check_payment(context: CallbackContext):
    job = context.job
    order_id = job.context['order_id']
    user_id = job.context['user_id']
    chat_id = job.context['chat_id']
    lang = job.context['lang']
    
    # Проверяем статус платежа
    status_info = check_payment_status(order_id)
    
    if status_info and status_info.get('status') == 'paid':
        # Платеж получен, отправляем товар
        product_info = job.context['product_info']
        
        # Добавляем покупку в историю
        add_purchase(
            user_id,
            product_info,
            status_info['price_amount'],
            '',  # district
            ''   # delivery_type
        )
        
        # Отправляем сообщение о успешной оплате
        context.bot.send_message(
            chat_id=chat_id,
            text=get_text(lang, 'payment_success', product_image=PRODUCTS[job.context['city']][job.context['category']]['image'])
        )
        
        # Обновляем статус транзакции
        update_transaction_status(order_id, 'paid')
    else:
        # Платеж не получен
        context.bot.send_message(
            chat_id=chat_id,
            text=get_text(lang, 'payment_timeout')
        )
        
        # Увеличиваем счетчик неудачных платежей
        user = get_user(user_id)
        failed_payments = user[6] + 1  # failed_payments
        update_user(user_id, failed_payments=failed_payments)
        
        # Если 3 неудачных платежа, бан на 24 часа
        if failed_payments >= 3:
            ban_until = datetime.now() + timedelta(hours=24)
            update_user(user_id, ban_until=ban_until)
            context.bot.send_message(
                chat_id=chat_id,
                text=get_text(lang, 'ban_message')
            )
        
        # Обновляем статус транзакции
        update_transaction_status(order_id, 'expired')

def handle_balance(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    amount_text = update.message.text
    
    try:
        amount = float(amount_text)
        if amount <= 0:
            update.message.reply_text(get_text(lang, 'error'))
            return BALANCE
        
        # Обновляем баланс пользователя
        current_balance = user_data[9] or 0
        new_balance = current_balance + amount
        update_user(user.id, balance=new_balance)
        
        update.message.reply_text(
            get_text(lang, 'balance_add_success', amount=amount, balance=new_balance)
        )
        
        show_main_menu(update, context, user.id, lang)
        return MAIN_MENU
    except ValueError:
        update.message.reply_text(get_text(lang, 'error'))
        return BALANCE

def cancel(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    user_data = get_user(user.id)
    lang = user_data[3] or 'ru'
    
    update.message.reply_text(
        get_text(lang, 'cancel_text'),
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def error(update: Update, context: CallbackContext):
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    
    try:
        user = update.message.from_user
        user_data = get_user(user.id)
        lang = user_data[3] or 'ru'
        update.message.reply_text(get_text(lang, 'error'))
    except:
        update.message.reply_text("Произошла ошибка. Попробуйте позже.")

def main():
    # Создаем Updater и передаем ему токен бота
    updater = Updater(TOKEN, use_context=True)
    
    # Получаем диспетчер для регистрации обработчиков
    dp = updater.dispatcher
    
    # Определяем обработчик разговоров
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CAPTCHA: [MessageHandler(Filters.text & ~Filters.command, check_captcha)],
            LANGUAGE: [CallbackQueryHandler(set_language)],
            MAIN_MENU: [MessageHandler(Filters.text, handle_main_menu)],
            CITY: [MessageHandler(Filters.text, handle_city)],
            CATEGORY: [MessageHandler(Filters.text, handle_category)],
            DISTRICT: [MessageHandler(Filters.text, handle_district)],
            DELIVERY: [MessageHandler(Filters.text, handle_delivery)],
            CONFIRMATION: [MessageHandler(Filters.text, handle_confirmation)],
            BALANCE: [MessageHandler(Filters.text, handle_balance)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Добавляем обработчик разговоров в диспетчер
    dp.add_handler(conv_handler)
    
    # Добавляем обработчик ошибок
    dp.add_error_handler(error)
    
    # Запускаем бота
    updater.start_polling()
    
    # Запускаем бота до тех пор, пока пользователь не нажмет Ctrl-C
    updater.idle()

if __name__ == '__main__':
    main()
