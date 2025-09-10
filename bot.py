import logging
import random
import time
import asyncio
import os
import hmac
import hashlib
import jwt
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
import aiohttp
from aiohttp import web
import asyncpg

from db import (
    init_db, get_user, update_user, add_transaction, add_purchase, 
    get_pending_transactions, update_transaction_status, update_transaction_status_by_uuid, 
    get_last_order, is_banned, get_text, 
    load_cache,
    get_cities_cache, get_districts_cache, get_products_cache, get_delivery_types_cache, get_categories_cache
)
from cryptocloud import create_cryptocloud_invoice, get_cryptocloud_invoice_status, check_payment_status_periodically, cancel_cryptocloud_invoice

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройки бота
TOKEN = os.getenv("BOT_TOKEN")
CRYPTOCLOUD_API_KEY = os.getenv("CRYPTOCLOUD_API_KEY")
CRYPTOCLOUD_SHOP_ID = os.getenv("CRYPTOCLOUD_SHOP_ID")
CRYPTOCLOUD_SECRET_KEY = os.getenv("CRYPTOCLOUD_SECRET_KEY")  # Добавлен секретный ключ
DATABASE_URL = os.environ.get('DATABASE_URL')
POSTBACK_SECRET = os.getenv("POSTBACK_SECRET", CRYPTOCLOUD_SECRET_KEY)

# Состояния разговора
class Form(StatesGroup):
    captcha = State()
    language = State()
    main_menu = State()
    city = State()
    category = State()
    district = State()
    delivery = State()
    confirmation = State()
    crypto_currency = State()
    payment = State()
    balance = State()
    balance_menu = State()
    topup_currency = State()

# Глобальные переменные
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db_pool = None

# Доступные криптовалюты
CRYPTO_CURRENCIES = {
    'BTC': 'Bitcoin',
    'ETH': 'Ethereum',
    'USDT': 'Tether (TRC20)',
    'LTC': 'Litecoin'
}

# Вспомогательная функция для удаления предыдущего сообщения
async def delete_previous_message(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

# Поток для проверки pending транзакций
async def check_pending_transactions_loop():
    while True:
        try:
            transactions = await get_pending_transactions()
            for transaction in transactions:
                invoice_uuid = transaction['invoice_uuid']
                status_info = await get_cryptocloud_invoice_status(invoice_uuid)
                
                if status_info and status_info.get('status') == 'success' and len(status_info['result']) > 0:
                    invoice = status_info['result'][0]
                    invoice_status = invoice['status']
                    if invoice_status == 'paid':
                        await update_transaction_status_by_uuid(invoice_uuid, 'paid')
                        
                        user_id = transaction['user_id']
                        product_info = transaction['product_info']
                        
                        product_parts = product_info.split(' в ')[0] if ' в ' in product_info else product_info
                        city = transaction['product_info'].split(' в ')[1].split(',')[0] if ' в ' in product_info else 'Тбилиси'
                        
                        products_cache = get_products_cache()
                        product_image = products_cache.get(city, {}).get(product_parts, {}).get('image', 'https://example.com/default.jpg')
                        
                        await bot.send_message(
                            chat_id=user_id,
                            text=get_text('ru', 'payment_success', product_image=product_image)
                        )
                        
                    elif invoice_status in ['expired', 'canceled']:
                        await update_transaction_status_by_uuid(invoice_uuid, invoice_status)
            
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Error in check_pending_transactions: {e}")
            await asyncio.sleep(60)

# Обработчик POSTBACK уведомлений от CryptoCloud (ИСПРАВЛЕННАЯ ВЕРСИЯ)
async def handle_cryptocloud_postback(request):
    try:
        # Читаем данные в формате x-www-form-urlencoded
        data = await request.post()
        data_dict = dict(data)
        
        logger.info(f"Received CryptoCloud postback: {data_dict}")
        
        # Проверяем наличие обязательных полей
        if 'token' not in data_dict:
            logger.warning("No token in CryptoCloud postback")
            return web.json_response({'status': 'error', 'message': 'No token'}, status=400)
            
        if 'status' not in data_dict:
            logger.warning("No status in CryptoCloud postback")
            return web.json_response({'status': 'error', 'message': 'No status'}, status=400)
            
        if 'order_id' not in data_dict:
            logger.warning("No order_id in CryptoCloud postback")
            return web.json_response({'status': 'error', 'message': 'No order_id'}, status=400)

        # Проверяем JWT токен с использованием SECRET_KEY
        token = data_dict['token']
        if not CRYPTOCLOUD_SECRET_KEY:
            logger.error("CRYPTOCLOUD_SECRET_KEY is not set in environment variables")
            return web.json_response({'status': 'error', 'message': 'Server misconfiguration'}, status=500)
            
        try:
            # Декодируем JWT с использованием SECRET_KEY
            decoded = jwt.decode(token, CRYPTOCLOUD_SECRET_KEY, algorithms=['HS256'])
            logger.info(f"Successfully decoded JWT token: {decoded}")
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired in CryptoCloud postback")
            return web.json_response({'status': 'error', 'message': 'Token expired'}, status=403)
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token in CryptoCloud postback: {e}")
            return web.json_response({'status': 'error', 'message': 'Invalid token'}, status=403)

        # Обработка успешного платежа
        status = data_dict['status']
        order_id = data_dict['order_id']
        
        if status == 'success' and order_id:
            # Запускаем обработку в фоне
            asyncio.create_task(process_successful_payment(order_id))
            logger.info(f"Successfully received postback for order {order_id}")
        
        return web.json_response({'status': 'ok'})
        
    except Exception as e:
        logger.error(f"Error processing CryptoCloud postback: {e}")
        return web.json_response({'status': 'error', 'message': str(e)}, status=500)

async def process_successful_payment(order_id):
    try:
        async with db_pool.acquire() as conn:
            transaction = await conn.fetchrow('SELECT * FROM transactions WHERE order_id = $1', order_id)
        
        if not transaction:
            logger.error(f"Transaction not found for order_id: {order_id}")
            return
            
        await update_transaction_status(order_id, 'paid')
        
        user_id = transaction['user_id']
        product_info = transaction['product_info']
        price = transaction['amount']
        
        # Проверяем, является ли это пополнением баланса
        if "Пополнение баланса" in product_info:
            # Пополняем баланс пользователя
            user = await get_user(user_id)
            new_balance = (user['balance'] or 0) + price
            await update_user(user_id, balance=new_balance)
            
            lang = user['language'] or 'ru' if user else 'ru'
            
            await bot.send_message(
                chat_id=user_id,
                text=get_text(lang, 'balance_add_success', amount=price, balance=new_balance)
            )
            
            logger.info(f"Balance topped up for user {user_id}: +{price}$, new balance: {new_balance}$")
        else:
            # Обычная покупка товара
            await add_purchase(
                user_id,
                product_info,
                price,
                '',
                ''
            )
            
            product_parts = product_info.split(' в ')[0] if ' в ' in product_info else product_info
            city = product_info.split(' в ')[1].split(',')[0] if ' в ' in product_info else 'Тбилиси'
            
            user = await get_user(user_id)
            lang = user['language'] or 'ru' if user else 'ru'
            
            products_cache = get_products_cache()
            product_image = products_cache.get(city, {}).get(product_parts, {}).get('image', 'https://example.com/default.jpg')
            
            await bot.send_message(
                chat_id=user_id,
                text=get_text(lang, 'payment_success', product_image=product_image)
            )
            
            logger.info(f"Successfully processed purchase for user {user_id}, order {order_id}")
    
    except Exception as e:
        logger.error(f"Error processing successful payment: {e}")

# Функция для показа меню баланса
async def show_balance_menu(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    
    balance_text = get_text(lang, 'balance_instructions', balance=user_data['balance'] or 0)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup_balance"))
    builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_main"))
    
    image_url = "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true"
    
    await callback.message.answer_photo(
        photo=image_url,
        caption=balance_text,
        reply_markup=builder.as_markup()
    )

# Функция для показа меню выбора валюты пополнения
async def show_topup_currency_menu(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    
    topup_info = get_text(lang, 'balance_topup_info')
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="LTC", callback_data="topup_ltc"))
    builder.row(InlineKeyboardButton(text="BTC", callback_data="topup_btc"))
    builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_balance_menu"))
    
    image_url = "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true"
    
    await callback.message.answer_photo(
        photo=image_url,
        caption=topup_info,
        reply_markup=builder.as_markup()
    )

# Обработчики команд и состояний
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    user = message.from_user
    user_id = user.id
    
    if await is_banned(user_id):
        await message.answer("Вы забанены. Обратитесь к поддержке.")
        return
    
    existing_user = await get_user(user_id)
    if existing_user:
        if existing_user['captcha_passed']:
            lang = existing_user['language'] or 'ru'
            await message.answer(get_text(lang, 'welcome'))
            await show_main_menu(message, state, user_id, lang)
            await state.set_state(Form.main_menu)
            return
    
    captcha_code = ''.join(random.choices('0123456789', k=5))
    await state.update_data(captcha=captcha_code)
    
    await message.answer(
        get_text('ru', 'captcha', code=captcha_code)
    )
    await state.set_state(Form.captcha)

@dp.message(Form.captcha)
async def process_captcha(message: types.Message, state: FSMContext):
    user_input = message.text
    user = message.from_user
    data = await state.get_data()
    
    if user_input == data.get('captcha'):
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO users (user_id, username, first_name, captcha_passed) VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO UPDATE SET captcha_passed = $5',
                user.id, user.username, user.first_name, 1, 1
            )
        
        builder = InlineKeyboardBuilder()
        builder.add(
            InlineKeyboardButton(text="Русский", callback_data='ru'),
            InlineKeyboardButton(text="English", callback_data='en'),
            InlineKeyboardButton(text="ქართული", callback_data='ka')
        )
        builder.adjust(1)
        
        await message.answer('Выберите язык / Select language / აირჩიეთ ენა:', reply_markup=builder.as_markup())
        await state.set_state(Form.language)
    else:
        await message.answer(get_text('ru', 'captcha_failed'))

@dp.callback_query(Form.language)
async def process_language(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    lang_code = callback.data
    
    await update_user(user_id, language=lang_code)
    
    await callback.answer()
    await callback.message.edit_text(text=get_text(lang_code, 'language_selected'))
    
    await show_main_menu(callback.message, state, user_id, lang_code)
    await state.set_state(Form.main_menu)

async def show_main_menu(message: types.Message, state: FSMContext, user_id: int, lang: str):
    user = await get_user(user_id)
    if not user:
        return
    
    # Получаем актуальные кэши
    cities_cache = get_cities_cache()
    
    # Используем новый текст описания магазина
    shop_description = get_text(lang, 'main_menu_description') + "\n\n"
    
    user_info_text = get_text(
        lang, 
        'main_menu', 
        name=user['first_name'] or 'N/A',
        username=user['username'] or 'N/A',
        purchases=user['purchase_count'] or 0,
        discount=user['discount'] or 0,
        balance=user['balance'] or 0
    )
    
    full_text = shop_description + user_info_text
    
    builder = InlineKeyboardBuilder()
    for city in cities_cache:
        builder.row(InlineKeyboardButton(text=city['name'], callback_data=f"city_{city['name']}"))
    builder.row(
        InlineKeyboardButton(text=f"💰 {get_text(lang, 'balance', balance=user['balance'] or 0)}", callback_data="balance"),
        InlineKeyboardButton(text="📦 Последний заказ", callback_data="last_order")
    )
    builder.row(
        InlineKeyboardButton(text="🎁 Бонусы", callback_data="bonuses"),
        InlineKeyboardButton(text="📚 Правила", callback_data="rules")
    )
    builder.row(
        InlineKeyboardButton(text="👨‍💻 Оператор", callback_data="operator"),
        InlineKeyboardButton(text="🔧 Техподдержка", callback_data="support")
    )
    builder.row(InlineKeyboardButton(text="📢 Наш канал", callback_data="channel"))
    builder.row(InlineKeyboardButton(text="⭐ Отзывы", callback_data="reviews"))
    builder.row(InlineKeyboardButton(text="🌐 Наш сайт", callback_data="website"))
    builder.row(InlineKeyboardButton(text="🤖 Личный бот", callback_data="personal_bot"))
    
    image_url = "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true"
    
    data = await state.get_data()
    if 'last_message_id' in data:
        await delete_previous_message(user_id, data['last_message_id'])
    
    sent_message = await message.answer_photo(
        photo=image_url,
        caption=full_text,
        reply_markup=builder.as_markup()
    )
    
    await state.update_data(last_message_id=sent_message.message_id)

@dp.callback_query(Form.main_menu)
async def process_main_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = callback.data
    
    state_data = await state.get_data()
    if 'last_message_id' in state_data:
        await delete_previous_message(user_id, state_data['last_message_id'])
    
    if data.startswith('city_'):
        city = data.replace('city_', '')
        await state.update_data(city=city)
        
        # Получаем актуальные кэши
        categories_cache = get_categories_cache()
        
        builder = InlineKeyboardBuilder()
        for category in categories_cache:
            builder.row(InlineKeyboardButton(text=category['name'], callback_data=f"cat_{category['name']}"))
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_main"))
        
        sent_message = await callback.message.answer(
            text=get_text(lang, 'select_category'),
            reply_markup=builder.as_markup()
        )
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.category)
    elif data == 'balance':
        await show_balance_menu(callback, state)
        await state.set_state(Form.balance_menu)
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
            sent_message = await callback.message.answer(
                text=order_text
            )
        else:
            sent_message = await callback.message.answer(
                text=get_text(lang, 'no_orders')
            )
        await state.update_data(last_message_id=sent_message.message_id)
    elif data == 'bonuses':
        sent_message = await callback.message.answer(
            text=get_text(lang, 'bonuses')
        )
        await state.update_data(last_message_id=sent_message.message_id)
    elif data == 'rules':
        sent_message = await callback.message.answer(
            text=get_text(lang, 'rules')
        )
        await state.update_data(last_message_id=sent_message.message_id)
    elif data == 'operator' or data == 'support':
        sent_message = await callback.message.answer(
            text=get_text(lang, 'support')
        )
        await state.update_data(last_message_id=sent_message.message_id)
    elif data == 'channel':
        sent_message = await callback.message.answer(
            text="https://t.me/your_channel"
        )
        await state.update_data(last_message_id=sent_message.message_id)
    elif data == 'reviews':
        sent_message = await callback.message.answer(
            text=get_text(lang, 'reviews')
        )
        await state.update_data(last_message_id=sent_message.message_id)
    elif data == 'website':
        sent_message = await callback.message.answer(
            text="https://yourwebsite.com"
        )
        await state.update_data(last_message_id=sent_message.message_id)
    elif data == 'personal_bot':
        sent_message = await callback.message.answer(
            text="https://t.me/your_bot"
        )
        await state.update_data(last_message_id=sent_message.message_id)
    elif data == 'back_to_main':
        await show_main_menu(callback.message, state, user_id, lang)
        await state.set_state(Form.main_menu)

@dp.callback_query(Form.balance_menu)
async def process_balance_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = callback.data
    
    if data == 'topup_balance':
        await show_topup_currency_menu(callback, state)
        await state.set_state(Form.topup_currency)
    elif data == 'back_to_main':
        await show_main_menu(callback.message, state, user_id, lang)
        await state.set_state(Form.main_menu)

@dp.callback_query(Form.topup_currency)
async def process_topup_currency(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = callback.data
    
    if data == 'back_to_balance_menu':
        await show_balance_menu(callback, state)
        await state.set_state(Form.balance_menu)
    elif data in ['topup_ltc', 'topup_btc']:
        currency = 'LTC' if data == 'topup_ltc' else 'BTC'
        await state.update_data(topup_currency=currency)
        
        await callback.message.answer(get_text(lang, 'balance_add'))
        await state.set_state(Form.balance)

@dp.callback_query(Form.category)
async def process_category(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = callback.data
    
    state_data = await state.get_data()
    if 'last_message_id' in state_data:
        await delete_previous_message(user_id, state_data['last_message_id'])
    
    if data == 'back_to_main':
        await show_main_menu(callback.message, state, user_id, lang)
        await state.set_state(Form.main_menu)
        return
    
    category = data.replace('cat_', '')
    city_data = await state.get_data()
    city = city_data.get('city')
    
    # Получаем актуальные кэши
    products_cache = get_products_cache()
    
    if city not in products_cache:
        sent_message = await callback.message.answer(
            text=get_text(lang, 'error')
        )
        await state.update_data(last_message_id=sent_message.message_id)
        return
    
    # Фильтруем товары по категории
    category_products = {}
    for product_name, product_info in products_cache[city].items():
        if product_info['category'] == category:
            category_products[product_name] = product_info
    
    if not category_products:
        sent_message = await callback.message.answer(
            text=get_text(lang, 'error')
        )
        await state.update_data(last_message_id=sent_message.message_id)
        return
    
    await state.update_data(category=category)
    
    builder = InlineKeyboardBuilder()
    for product_name in category_products.keys():
        price = category_products[product_name]['price']
        builder.row(InlineKeyboardButton(text=f"{product_name} - ${price}", callback_data=f"prod_{product_name}"))
    builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_city"))
    
    sent_message = await callback.message.answer(
        text="Выберите товар:",
        reply_markup=builder.as_markup()
    )
    await state.update_data(last_message_id=sent_message.message_id)
    await state.set_state(Form.district)

@dp.callback_query(Form.district)
async def process_district(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = callback.data
    
    state_data = await state.get_data()
    if 'last_message_id' in state_data:
        await delete_previous_message(user_id, state_data['last_message_id'])
    
    if data == 'back_to_city':
        city_data = await state.get_data()
        city = city_data.get('city')
        
        # Получаем актуальные кэши
        categories_cache = get_categories_cache()
        
        builder = InlineKeyboardBuilder()
        for category in categories_cache:
            builder.row(InlineKeyboardButton(text=category['name'], callback_data=f"cat_{category['name']}"))
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_main"))
        
        sent_message = await callback.message.answer(
            text=get_text(lang, 'select_category'),
            reply_markup=builder.as_markup()
        )
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.category)
        return
    
    if data.startswith('prod_'):
        product_name = data.replace('prod_', '')
        city_data = await state.get_data()
        city = city_data.get('city')
        category = city_data.get('category')
        
        # Получаем актуальные кэши
        products_cache = get_products_cache()
        
        if city not in products_cache or product_name not in products_cache[city]:
            sent_message = await callback.message.answer(
                text=get_text(lang, 'error')
            )
            await state.update_data(last_message_id=sent_message.message_id)
            return
        
        product_info = products_cache[city][product_name]
        await state.update_data(product=product_name)
        await state.update_data(price=product_info['price'])
        
        # Получаем актуальные кэши
        districts_cache = get_districts_cache()
        districts = districts_cache.get(city, [])
        
        builder = InlineKeyboardBuilder()
        for district in districts:
            builder.row(InlineKeyboardButton(text=district, callback_data=f"dist_{district}"))
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_category"))
        
        sent_message = await callback.message.answer(
            text=get_text(lang, 'select_district'),
            reply_markup=builder.as_markup()
        )
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.district)
    elif data.startswith('dist_'):
        district = data.replace('dist_', '')
        await state.update_data(district=district)
        
        # Получаем актуальные кэши
        delivery_types_cache = get_delivery_types_cache()
        
        builder = InlineKeyboardBuilder()
        for del_type in delivery_types_cache:
            builder.row(InlineKeyboardButton(text=del_type, callback_data=f"del_{del_type}"))
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_district"))
        
        sent_message = await callback.message.answer(
            text=get_text(lang, 'select_delivery'),
            reply_markup=builder.as_markup()
        )
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.delivery)

@dp.callback_query(Form.delivery)
async def process_delivery(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = callback.data
    
    state_data = await state.get_data()
    if 'last_message_id' in state_data:
        await delete_previous_message(user_id, state_data['last_message_id'])
    
    if data == 'back_to_district':
        city_data = await state.get_data()
        city = city_data.get('city')
        
        # Получаем актуальные кэши
        districts_cache = get_districts_cache()
        districts = districts_cache.get(city, [])
        
        builder = InlineKeyboardBuilder()
        for district in districts:
            builder.row(InlineKeyboardButton(text=district, callback_data=f"dist_{district}"))
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_category"))
        
        sent_message = await callback.message.answer(
            text=get_text(lang, 'select_district'),
            reply_markup=builder.as_markup()
        )
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.district)
        return
    
    delivery_type = data.replace('del_', '')
    
    # Получаем актуальные кэши
    delivery_types_cache = get_delivery_types_cache()
    
    if delivery_type not in delivery_types_cache:
        sent_message = await callback.message.answer(
            text=get_text(lang, 'error')
        )
        await state.update_data(last_message_id=sent_message.message_id)
        return
    
    await state.update_data(delivery_type=delivery_type)
    
    state_data = await state.get_data()
    city = state_data.get('city')
    product = state_data.get('product')
    price = state_data.get('price')
    district = state_data.get('district')
    
    order_text = get_text(
        lang, 
        'order_summary',
        product=product,
        price=price,
        district=district,
        delivery_type=delivery_type
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes"))
    builder.row(InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no"))
    builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_delivery"))
    
    sent_message = await callback.message.answer(
        text=order_text,
        reply_markup=builder.as_markup()
    )
    await state.update_data(last_message_id=sent_message.message_id)
    await state.set_state(Form.confirmation)

@dp.callback_query(Form.confirmation)
async def process_confirmation(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = callback.data
    
    state_data = await state.get_data()
    if 'last_message_id' in state_data:
        await delete_previous_message(user_id, state_data['last_message_id'])
    
    if data == 'back_to_delivery':
        # Получаем актуальные кэши
        delivery_types_cache = get_delivery_types_cache()
        
        builder = InlineKeyboardBuilder()
        for del_type in delivery_types_cache:
            builder.row(InlineKeyboardButton(text=del_type, callback_data=f"del_{del_type}"))
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_district"))
        
        sent_message = await callback.message.answer(
            text=get_text(lang, 'select_delivery'),
            reply_markup=builder.as_markup()
        )
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.delivery)
        return
    
    if data == 'confirm_yes':
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="BTC", callback_data="crypto_BTC"))
        builder.row(InlineKeyboardButton(text="ETH", callback_data="crypto_ETH"))
        builder.row(InlineKeyboardButton(text="USDT", callback_data="crypto_USDT"))
        builder.row(InlineKeyboardButton(text="LTC", callback_data="crypto_LTC"))
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_confirmation"))
        
        sent_message = await callback.message.answer(
            text=get_text(lang, 'select_crypto'),
            reply_markup=builder.as_markup()
        )
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.crypto_currency)
    else:
        await show_main_menu(callback.message, state, user_id, lang)
        await state.set_state(Form.main_menu)

@dp.callback_query(Form.crypto_currency)
async def process_crypto_currency(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    data = callback.data
    
    state_data = await state.get_data()
    if 'last_message_id' in state_data:
        await delete_previous_message(user_id, state_data['last_message_id'])
    
    if data == 'back_to_confirmation':
        state_data = await state.get_data()
        city = state_data.get('city')
        product = state_data.get('product')
        price = state_data.get('price')
        district = state_data.get('district')
        delivery_type = state_data.get('delivery_type')
        
        order_text = get_text(
            lang, 
            'order_summary',
            product=product,
            price=price,
            district=district,
            delivery_type=delivery_type
        )
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes"))
        builder.row(InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no"))
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_delivery"))
        
        sent_message = await callback.message.answer(
            text=order_text,
            reply_markup=builder.as_markup()
        )
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.confirmation)
        return
    
    crypto_currency = data.replace('crypto_', '')
    await state.update_data(crypto_currency=crypto_currency)
    
    state_data = await state.get_data()
    city = state_data.get('city')
    product = state_data.get('product')
    price = state_data.get('price')
    district = state_data.get('district')
    delivery_type = state_data.get('delivery_type')
    
    product_info = f"{product} в {city}, район {district}, {delivery_type}"
    
    # Создаем заказ в CryptoCloud
    order_id = f"order_{int(time.time())}_{user_id}"
    price_usd = price
    
    invoice_resp = await create_cryptocloud_invoice(price_usd, crypto_currency, order_id, test_mode=False)

    if not invoice_resp:
        logger.error("create_cryptocloud_invoice returned None")
        await callback.message.answer(get_text(lang, 'error'))
        return

    if invoice_resp.get('status') != 'success' or not invoice_resp.get('result'):
        logger.error(f"Invoice creation failed: {invoice_resp}")
        await callback.message.answer(get_text(lang, 'error'))
        return

    invoice_data = invoice_resp['result']
    invoice_uuid = invoice_data.get('uuid')
    payment_url = invoice_data.get('link') or invoice_data.get('pay_url')

    address = invoice_data.get('address') or ''
    if not address and invoice_uuid:
        info = await get_cryptocloud_invoice_status(invoice_uuid)
        if info and info.get('status') == 'success' and len(info.get('result', [])) > 0:
            address = info['result'][0].get('address', '') or ''

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
            await callback.message.answer_photo(
                photo=qr_code_url,
                caption=payment_text
            )
        except Exception as e:
            logger.error(f"Error sending QR code: {e}")
            await callback.message.answer(
                text=payment_text
            )
    else:
        logger.error(f"No address generated for invoice: {invoice_resp}")
        fallback_text = f"Не удалось получить адрес напрямую. Откройте страницу оплаты: {payment_url}"
        await callback.message.answer(text=fallback_text)

    await state.set_state(Form.payment)

@dp.message(Form.balance)
async def process_balance(message: types.Message, state: FSMContext):
    user = message.from_user
    user_data = await get_user(user.id)
    lang = user_data['language'] or 'ru'
    amount_text = message.text
    
    try:
        amount = float(amount_text)
        if amount <= 0:
            await message.answer(get_text(lang, 'error'))
            return
        
        # Получаем выбранную валюту из состояния
        state_data = await state.get_data()
        currency = state_data.get('topup_currency', 'LTC')
        
        # Создаем инвойс для пополнения баланса
        order_id = f"topup_{int(time.time())}_{user.id}"
        
        invoice_resp = await create_cryptocloud_invoice(amount, currency, order_id, test_mode=False)
        
        if not invoice_resp or invoice_resp.get('status') != 'success' or not invoice_resp.get('result'):
            await message.answer(get_text(lang, 'error'))
            return
            
        invoice_data = invoice_resp['result']
        invoice_uuid = invoice_data.get('uuid')
        payment_url = invoice_data.get('link') or invoice_data.get('pay_url')
        address = invoice_data.get('address') or ''
        
        expires_at = datetime.now() + timedelta(minutes=30)
        await add_transaction(
            user.id,
            amount,
            currency,
            order_id,
            payment_url,
            expires_at,
            f"Пополнение баланса на {amount}$",
            invoice_uuid
        )
        
        if address:
            qr_code_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={address}"
            payment_text = get_text(
                lang,
                'payment_instructions',
                amount=amount,
                currency=currency,
                payment_address=address
            )
            
            try:
                await message.answer_photo(
                    photo=qr_code_url,
                    caption=payment_text
                )
            except Exception as e:
                logger.error(f"Error sending QR code: {e}")
                await message.answer(text=payment_text)
        else:
            fallback_text = f"Не удалось получить адрес напрямую. Откройте страницу оплаты: {payment_url}"
            await message.answer(text=fallback_text)
            
    except ValueError:
        await message.answer(get_text(lang, 'error'))

@dp.message(Command("menu"))
async def cmd_menu(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    await show_main_menu(message, state, user_id, lang)
    await state.set_state(Form.main_menu)

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    text = message.text
    
    if text.isdigit():
        await state.update_data(balance_amount=float(text))
        await process_balance(message, state)
    else:
        await show_main_menu(message, state, user_id, lang)
        await state.set_state(Form.main_menu)

async def on_startup(app):
    # Запускаем проверку pending транзакций в фоне
    asyncio.create_task(check_pending_transactions_loop())

async def main():
    global db_pool
    
    # Удаляем вебхук перед запуском поллинга
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Инициализируем базу данных
    db_pool = await init_db(DATABASE_URL)
    
    # Принудительно загружаем кэш после инициализации БД
    await load_cache()
    
    # Создаем aiohttp приложение для обработки postback-ов
    postback_app = web.Application()
    postback_app.router.add_post('/cryptocloud_postback', handle_cryptocloud_postback)
    
    # Запускаем aiohttp сервер в фоне
    runner = web.AppRunner(postback_app)
    await runner.setup()
    
    port = int(os.environ.get('POSTBACK_PORT', 5001))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    # Добавляем задержку для избежания конфликта поллинга
    await asyncio.sleep(2)
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
