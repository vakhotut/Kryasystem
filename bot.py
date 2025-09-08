import logging
import random
import time
import asyncio
import os
import hmac
import hashlib
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
import asyncpg
from asyncpg.pool import Pool
from threading import Thread
from flask import Flask, request, jsonify
import threading

# Импорт функций из других файлов
from db import init_db, get_user, update_user, add_transaction, add_purchase, get_pending_transactions, update_transaction_status, update_transaction_status_by_uuid, get_last_order, is_banned
from texts import TEXTS, get_text
from products import PRODUCTS
from districts import DISTRICTS, DELIVERY_TYPES
from cryptocloud import create_cryptocloud_invoice, get_cryptocloud_invoice_status, check_payment_status_periodically, cancel_cryptocloud_invoice

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройки бота
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
CRYPTOCLOUD_API_KEY = os.getenv("CRYPTOCLOUD_API_KEY", "YOUR_CRYPTOCLOUD_API_KEY")
CRYPTOCLOUD_SHOP_ID = os.getenv("CRYPTOCLOUD_SHOP_ID", "YOUR_CRYPTOCLOUD_SHOP_ID")
DATABASE_URL = os.environ['DATABASE_URL']
POSTBACK_SECRET = os.getenv("POSTBACK_SECRET", CRYPTOCLOUD_API_KEY)  # Секрет для проверки подписи

# Состояния разговора
CAPTCHA, LANGUAGE, MAIN_MENU, CITY, CATEGORY, DISTRICT, DELIVERY, CONFIRMATION, CRYPTO_CURRENCY, PAYMENT, BALANCE = range(11)

# Создаем Flask приложение для обработки POSTBACK
postback_app = Flask(__name__)

# Глобальная переменная для хранения приложения бота и пула БД
global_bot_app = None
db_pool: Pool = None

# Доступные криптовалюты
CRYPTO_CURRENCIES = {
    'BTC': 'Bitcoin',
    'ETH': 'Ehereum',
    'USDT': 'Tether (TRC20)',
    'LTC': 'Litecoin'
}

# Вспомогательная функция для удаления предыдущего сообщения
async def delete_previous_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

# Поток для проверки pending транзакций
async def check_pending_transactions_loop():
    while True:
        try:
            transactions = await get_pending_transactions()
            for transaction in transactions:
                invoice_uuid = transaction['invoice_uuid']
                status_info = get_cryptocloud_invoice_status(invoice_uuid)
                
                if status_info and status_info.get('status') == 'success' and len(status_info['result']) > 0:
                    invoice = status_info['result'][0]  # Берем первый счет из массива
                    invoice_status = invoice['status']
                    if invoice_status == 'paid':
                        await update_transaction_status_by_uuid(invoice_uuid, 'paid')
                        
                        user_id = transaction['user_id']
                        product_info = transaction['product_info']
                        
                        # Получаем информацию о продукте для изображения
                        product_parts = product_info.split(' в ')[0] if ' в ' in product_info else product_info
                        city = transaction['product_info'].split(' в ')[1].split(',')[0] if ' in ' in product_info else 'Тбилиси'
                        
                        product_image = PRODUCTS.get(city, {}).get(product_parts, {}).get('image', 'https://example.com/default.jpg')
                        
                        if global_bot_app and global_bot_app.loop:
                            asyncio.run_coroutine_threadsafe(
                                global_bot_app.bot.send_message(
                                    chat_id=user_id,
                                    text=get_text('ru', 'payment_success', product_image=product_image)
                                ),
                                global_bot_app.loop
                            )
                        
                    elif invoice_status in ['expired', 'canceled']:
                        await update_transaction_status_by_uuid(invoice_uuid, invoice_status)
            
            await asyncio.sleep(60)  # Проверяем каждые 60 секунд
        except Exception as e:
            logger.error(f"Error in check_pending_transactions: {e}")
            await asyncio.sleep(60)

# Обработчик POSTBACK уведомлений от CryptoCloud
@postback_app.route('/cryptocloud_postback', methods=['POST'])
def handle_cryptocloud_postback():
    try:
        # Получаем данные из POST запроса
        data = request.form.to_dict()
        
        # Проверяем подпись (если предоставлена)
        if 'signature' in data:
            signature = data['signature']
            # Удаляем подпись из данных для проверки
            verify_data = data.copy()
            del verify_data['signature']
            
            # Сортируем данные и создаем строку для проверки
            sorted_data = sorted(verify_data.items())
            message = "&".join([f"{k}={v}" for k, v in sorted_data]) + POSTBACK_SECRET
            expected_signature = hashlib.sha256(message.encode()).hexdigest()
            
            if not hmac.compare_digest(signature, expected_signature):
                logger.warning("Invalid signature in CryptoCloud postback")
                return jsonify({'status': 'error', 'message': 'Invalid signature'}), 403
        
        # Логируем полученные данные для отладки
        logger.info(f"Received CryptoCloud postback: {data}")
        
        # Извлекаем необходимые данные
        status = data.get('status')
        invoice_id = data.get('invoice_id')
        amount_crypto = data.get('amount_crypto')
        currency = data.get('currency')
        order_id = data.get('order_id')
        token = data.get('token')
        
        # Дополнительная проверка токена, если нет подписи
        if 'signature' not in data and token != POSTBACK_SECRET:
            logger.warning("Invalid token in CryptoCloud postback")
            return jsonify({'status': 'error', 'message': 'Invalid token'}), 403
        
        # Проверяем, что это успешный платеж
        if status == 'success' and order_id:
            # Обрабатываем в отдельной асинхронной задаче
            if global_bot_app and global_bot_app.loop:
                asyncio.run_coroutine_threadsafe(
                    process_successful_payment(order_id),
                    global_bot_app.loop
                )
        
        return jsonify({'status': 'ok'}), 200
        
    except Exception as e:
        logger.error(f"Error processing CryptoCloud postback: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

async def process_successful_payment(order_id):
    """Асинхронная обработка успешного платежа"""
    try:
        # Находим транзакцию по order_id
        async with db_pool.acquire() as conn:
            transaction = await conn.fetchrow('SELECT * FROM transactions WHERE order_id = $1', order_id)
        
        if transaction:
            # Обновляем статус транзакции
            await update_transaction_status(order_id, 'paid')
            
            # Добавляем покупку
            user_id = transaction['user_id']
            product_info = transaction['product_info']
            price = transaction['amount']
            
            await add_purchase(
                user_id,
                product_info,
                price,
                '',
                ''
            )
            
            # Получаем информацию о продукте
            product_parts = product_info.split(' в ')[0] if ' в ' in product_info else product_info
            city = product_info.split(' в ')[1].split(',')[0] if ' в ' in product_info else 'Тбилиси'
            
            # Получаем пользователя для определения языка
            user = await get_user(user_id)
            lang = user['language'] or 'ru' if user else 'ru'
            
            # Получаем изображение товара
            product_image = PRODUCTS.get(city, {}).get(product_parts, {}).get('image', 'https://example.com/default.jpg')
            
            # Отправляем уведомление пользователю
            if global_bot_app and global_bot_app.loop:
                asyncio.run_coroutine_threadsafe(
                    global_bot_app.bot.send_message(
                        chat_id=user_id,
                        text=get_text(lang, 'payment_success', product_image=product_image)
                    ),
                    global_bot_app.loop
                )
            
            logger.info(f"Successfully processed postback for order {order_id}")
    
    except Exception as e:
        logger.error(f"Error processing successful payment: {e}")

# Обработчики команд и состояний
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Сбрасываем состояние пользователя
    context.user_data.clear()
    
    user = update.message.from_user
    user_id = user.id
    
    if await is_banned(user_id):
        await update.message.reply_text("Вы забанены. Обратитесь к поддержке.")
        return ConversationHandler.END
    
    existing_user = await get_user(user_id)
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
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO users (user_id, username, first_name, captcha_passed) VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO UPDATE SET captcha_passed = $5',
                user.id, user.username, user.first_name, 1, 1
            )
        
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
    
    await update_user(user_id, language=lang_code)
    
    await query.answer()
    await query.edit_message_text(text=get_text(lang_code, 'language_selected'))
    
    await show_main_menu(update, context, user_id, lang_code)
    return MAIN_MENU

async def show_main_menu(update, context, user_id, lang):
    user = await get_user(user_id)
    if not user:
        return
    
    # Описание магазина
    shop_description = "🏪 AutoShop - лучшие товары с доставка по Грузии\n\n"
    
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
            InlineKeyboardButton(f"💰 Баланс: {user['balance'] or 0}$", callback_data="balance"),
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
    user_data = await get_user(user_id)
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
        last_order = await get_last_order(user_id)
        if last_order:
            order_text = (
                f"📦 Товар: {last_order['product']}\n"
                f"💵 Стоимость: {last_order['price']}$\n"
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
    user_data = await get_user(user_id)
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
    user_data = await get_user(user_id)
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
    user_data = await get_user(user_id)
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
    user_data = await get_user(user_id)
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
        # Переходим к выбору криптовалюты
        keyboard = [
            [InlineKeyboardButton("BTC", callback_data="crypto_BTC")],
            [InlineKeyboardButton("ETH", callback_data="crypto_ETH")],
            [InlineKeyboardButton("USDT", callback_data="crypto_USDT")],
            [InlineKeyboardButton("LTC", callback_data="crypto_LTC")],
            [InlineKeyboardButton(get_text(lang, 'back'), callback_data="back_to_confirmation")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await context.bot.send_message(
            chat_id=user_id,
            text=get_text(lang, 'select_crypto'),
            reply_markup=reply_markup
        )
        context.user_data['last_message_id'] = message.message_id
        return CRYPTO_CURRENCY
    else:
        await show_main_menu(update, context, user_id, lang)
        return MAIN_MENU

async def handle_crypto_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = query.data
    
    # Удаляем предыдущее сообщение
    if 'last_message_id' in context.user_data:
        await delete_previous_message(context, user_id, context.user_data['last_message_id'])
    
    if data == 'back_to_confirmation':
        city = context.user_data.get('city')
        category = context.user_data.get('category')
        price = context.user_data.get('price')
        district = context.user_data.get('district')
        delivery_type = context.user_data.get('delivery_type')
        
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
    
    crypto_currency = data.replace('crypto_', '')
    context.user_data['crypto_currency'] = crypto_currency
    
    city = context.user_data.get('city')
    category = context.user_data.get('category')
    price = context.user_data.get('price')
    district = context.user_data.get('district')
    delivery_type = context.user_data.get('delivery_type')
    
    product_info = f"{category} в {city}, район {district}, {delivery_type}"
    
    # Создаем заказ в CryptoCloud
    order_id = f"order_{int(time.time())}_{user_id}"
    price_usd = price  # если цена уже в USD
    
    invoice_resp = create_cryptocloud_invoice(price_usd, crypto_currency, order_id)

    if not invoice_resp:
        logger.error("create_cryptocloud_invoice returned None")
        await context.bot.send_message(chat_id=user_id, text=get_text(lang, 'error'))
        return CRYPTO_CURRENCY

    # проверяем статус и результат
    if invoice_resp.get('status') != 'success' or not invoice_resp.get('result'):
        logger.error(f"Invoice creation failed: {invoice_resp}")
        await context.bot.send_message(chat_id=user_id, text=get_text(lang, 'error'))
        return CRYPTO_CURRENCY

    invoice_data = invoice_resp['result']
    invoice_uuid = invoice_data.get('uuid')
    payment_url = invoice_data.get('link') or invoice_data.get('pay_url')  # разные версии API

    # Попытка получить адрес
    address = invoice_data.get('address') or ''
    # Если адрес всё ещё пустой — попробуем дополнительный запрос статуса (если ещё не делали)
    if not address and invoice_uuid:
        info = get_cryptocloud_invoice_status(invoice_uuid)
        if info and info.get('status') == 'success' and len(info.get('result', [])) > 0:
            address = info['result'][0].get('address', '') or ''

    # Сохраняем информацию о транзакции
    expires_at = datetime.now() + timedelta(minutes=60)
    await add_transaction(
        user_id,
        price,
        crypto_currency,
        order_id,
        payment_url,
        expires_at,
        product_info,
        invoice_uuid
    )

    if address:
        qr_code_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={address}"
        payment_text = get_text(
            lang,
            'payment_instructions',
            amount=price,
            currency=crypto_currency,
            payment_address=address
        )
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=qr_code_url,
                caption=payment_text,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error sending QR code: {e}")
            await context.bot.send_message(
                chat_id=user_id,
                text=payment_text,
                parse_mode='Markdown'
            )
    else:
        # Если адрес всё ещё не пришёл — уведомляем и показываем ссылку (как fallback) и логируем
        logger.error(f"No address generated for invoice: {invoice_resp}")
        fallback_text = f"Не удалось получить адрес напрямую. Откройте страницу оплаты: {payment_url}"
        await context.bot.send_message(chat_id=user_id, text=fallback_text)

    # Запускаем проверку оплаты
    if hasattr(context, 'job_queue') and context.job_queue:
        context.job_queue.run_repeating(
            check_payment,
            interval=60,
            first=60,
            context={
                'user_id': user_id,
                'order_id': order_id,
                'invoice_uuid': invoice_uuid,
                'chat_id': user_id,
                'product_info': product_info,
                'lang': lang
            }
        )
    else:
        logger.warning("JobQueue is not available, payment checking won't be scheduled")
    
    return PAYMENT

async def check_payment(context: ContextTypes.DEFAULT_TYPE):
    try:
        job = context.job
        invoice_uuid = job.context['invoice_uuid']
        user_id = job.context['user_id']
        chat_id = job.context['chat_id']
        lang = job.context['lang']
        product_info = job.context['product_info']
        
        status_info = get_cryptocloud_invoice_status(invoice_uuid)
        
        if status_info and status_info.get('status') == 'success' and len(status_info['result']) > 0:
            invoice = status_info['result'][0]
            invoice_status = invoice['status']
            if invoice_status == 'paid':
                # Оплата получена
                price = invoice['amount_usd']
                
                await add_purchase(
                    user_id,
                    product_info,
                    price,
                    '',
                    ''
                )
                
                # Получаем изображение товара
                product_parts = product_info.split(' в ')[0] if ' в ' in product_info else product_info
                city = product_info.split(' в ')[1].split(',')[0] if ' в ' in product_info else 'Тбилиси'
                
                product_image = PRODUCTS.get(city, {}).get(product_parts, {}).get('image', 'https://example.com/default.jpg')
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=get_text(lang, 'payment_success', product_image=product_image)
                )
                
                await update_transaction_status_by_uuid(invoice_uuid, 'paid')
                
                # Останавливаем задачу
                job.schedule_removal()
            elif invoice_status in ['expired', 'canceled']:
                # Инвойс просрочен или отменен
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=get_text(lang, 'payment_timeout')
                )
                
                user = await get_user(user_id)
                failed_payments = user['failed_payments'] + 1
                await update_user(user_id, failed_payments=failed_payments)
                
                if failed_payments >= 3:
                    ban_until = datetime.now() + timedelta(hours=24)
                    await update_user(user_id, ban_until=ban_until.strftime('%Y-%m-%d %H:%M:%S'))
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=get_text(lang, 'ban_message')
                    )
                
                await update_transaction_status_by_uuid(invoice_uuid, invoice_status)
                
                # Останавливаем задачу
                job.schedule_removal()
    except Exception as e:
        logger.error(f"Error in check_payment: {e}")

async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    user_data = await get_user(user.id)
    lang = user_data['language'] or 'ru'
    amount_text = update.message.text
    
    try:
        amount = float(amount_text)
        if amount <= 0:
            await update.message.reply_text(get_text(lang, 'error'))
            return BALANCE
        
        current_balance = user_data['balance'] or 0
        new_balance = current_balance + amount
        await update_user(user.id, balance=new_balance)
        
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
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    await show_main_menu(update, context, user_id, lang)
    return MAIN_MENU

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    user_data = await get_user(user_id)
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
    user_data = await get_user(user.id)
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
        
        user_data = await get_user(user.id) if user else None
        lang = user_data['language'] or 'ru' if user_data else 'ru'
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_text(lang, 'error')
        )
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")

def run_postback_server():
    """Запускает сервер для обработки POSTBACK уведомлений"""
    port = int(os.environ.get('POSTBACK_PORT', 5001))
    postback_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    global global_bot_app, db_pool
    
    # Создаем и настраиваем приложение
    application = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(True)
        .build()
    )
    global_bot_app = application
    
    # Инициализируем базу данных синхронно
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())
    
    # Создаем обработчики
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
            CRYPTO_CURRENCY: [CallbackQueryHandler(handle_crypto_currency)],
            PAYMENT: [CallbackQueryHandler(handle_main_menu)],  # В состоянии оплаты просто возвращаем в главное меню
            BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_balance)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(error)
    
    # Запускаем проверку pending транзакций в отдельной асинхронной задаче
    loop.create_task(check_pending_transactions_loop())
    
    # Запускаем сервер для обработки POSTBACK уведомлений в отдельном потоке
    Thread(target=run_postback_server, daemon=True).start()
    
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
