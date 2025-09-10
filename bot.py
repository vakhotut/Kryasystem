import logging
import random
import time
import asyncio
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
import aiohttp

from db import (
    init_db, get_user, update_user, add_transaction, add_purchase, 
    get_pending_transactions, update_transaction_status, update_transaction_status_by_uuid, 
    get_last_order, is_banned, get_text, 
    load_cache,
    get_cities_cache, get_districts_cache, get_products_cache, get_delivery_types_cache, get_categories_cache,
    has_active_invoice
)
from ltc_hdwallet import ltc_wallet

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройки бота
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.environ.get('DATABASE_URL')

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
invoice_notifications = {}

# Доступные криптовалюты (только LTC)
CRYPTO_CURRENCIES = {
    'LTC': 'Litecoin'
}

# Функция для получения курса LTC с fallback значением
async def get_ltc_usd_rate():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.binance.com/api/v3/ticker/price?symbol=LTCUSDT') as response:
                data = await response.json()
                if 'price' in data:
                    return float(data['price'])
                else:
                    logger.warning("Binance API response missing 'price' field, using fallback price")
                    return 117.0  # Fallback цена LTC
    except Exception as e:
        logger.error(f"Error getting LTC rate: {e}, using fallback price")
        return 117.0  # Fallback цена LTC при ошибке

# Вспомогательная функция для удаления предыдущего сообщения
async def delete_previous_message(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

# Функция для уведомлений об инвойсе
async def invoice_notification_loop(user_id: int, order_id: str, lang: str):
    global invoice_notifications
    
    if user_id in invoice_notifications:
        invoice_notifications[user_id].cancel()
    
    async def notify():
        while True:
            async with db_pool.acquire() as conn:
                invoice = await conn.fetchrow(
                    "SELECT * FROM transactions WHERE order_id = $1 AND status = 'pending'",
                    order_id
                )
                
                if not invoice or invoice['expires_at'] <= datetime.now():
                    break
                
                time_left = invoice['expires_at'] - datetime.now()
                minutes_left = int(time_left.total_seconds() // 60)
                
                # Отправляем уведомление каждые 5 минут
                if minutes_left % 5 == 0 and minutes_left > 0:
                    try:
                        await bot.send_message(
                            user_id,
                            get_text(lang, 'invoice_time_left', time_left=f"{minutes_left} минут")
                        )
                    except Exception as e:
                        logger.error(f"Error sending notification: {e}")
                
                # Проверяем каждую минуту
                await asyncio.sleep(60)
            
            # После истечения времени
            if invoice and invoice['expires_at'] <= datetime.now():
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE transactions SET status = 'expired' WHERE order_id = $1",
                        order_id
                    )
                    
                    # Увеличиваем счетчик неудачных попыток
                    user = await conn.fetchrow(
                        "SELECT * FROM users WHERE user_id = $1", user_id
                    )
                    new_failed = (user['failed_payments'] or 0) + 1
                    await conn.execute(
                        "UPDATE users SET failed_payments = $1 WHERE user_id = $2",
                        new_failed, user_id
                    )
                    
                    # Проверяем на бан
                    if new_failed >= 3:
                        ban_until = datetime.now() + timedelta(hours=24)
                        await conn.execute(
                            "UPDATE users SET ban_until = $1 WHERE user_id = $2",
                            ban_until, user_id
                        )
                
                try:
                    user_data = await get_user(user_id)
                    lang = user_data['language'] or 'ru'
                    
                    await bot.send_message(
                        user_id,
                        get_text(lang, 'invoice_expired', failed_count=new_failed)
                    )
                    
                    if new_failed == 2:
                        await bot.send_message(
                            user_id,
                            get_text(lang, 'almost_banned', remaining=1)
                        )
                    elif new_failed >= 3:
                        await bot.send_message(
                            user_id,
                            get_text(lang, 'ban_message')
                        )
                except Exception as e:
                    logger.error(f"Error sending expiration message: {e}")
                
                break
    
    # Запускаем задачу и сохраняем ссылку для отмена
    task = asyncio.create_task(notify())
    invoice_notifications[user_id] = task

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
    builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_balance_menu"))
    
    await callback.message.edit_text(
        text=topup_info,
        reply_markup=builder.as_markup()
    )

# Функция для показа активного инвойса
async def show_active_invoice(callback: types.CallbackQuery, state: FSMContext, user_id: int, lang: str):
    async with db_pool.acquire() as conn:
        invoice = await conn.fetchrow(
            "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending' AND expires_at > NOW()",
            user_id
        )
    
    if invoice:
        expires_time = invoice['expires_at'].strftime("%d.%m.%Y, %H:%M:%S")
        time_left = invoice['expires_at'] - datetime.now()
        time_left_str = f"{int(time_left.total_seconds() // 60)} мин {int(time_left.total_seconds() % 60)} сек"
        
        payment_text = get_text(
            lang, 
            'active_invoice',
            crypto_address=invoice['crypto_address'],
            crypto_amount=invoice['crypto_amount'],
            amount=invoice['amount'],
            expires_time=expires_time,
            time_left=time_left_str
        )
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_invoice"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_invoice")
        )
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_main"))
        
        # Запускаем таймер уведомлений для этого инвойса
        asyncio.create_task(invoice_notification_loop(user_id, invoice['order_id'], lang))
        
        try:
            await callback.message.answer_photo(
                photo=invoice['payment_url'],
                caption=payment_text,
                reply_markup=builder.as_markup(),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error sending invoice: {e}")
            await callback.message.answer(
                text=payment_text,
                reply_markup=builder.as_markup(),
                parse_mode='Markdown'
            )

# Поток для проверки pending транзакций
async def check_pending_transactions_loop():
    while True:
        try:
            # В реальной реализации здесь нужно подключиться к LTC node
            # или использовать explorer API для проверки баланса
            # Это сложная задача, требующая отдельной реализации
            await asyncio.sleep(300)  # Проверяем каждые 5 минут
        except Exception as e:
            logger.error(f"Error in check_pending_transactions: {e}")
            await asyncio.sleep(300)

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
    
    # Проверяем есть ли активный инвойс
    async with db_pool.acquire() as conn:
        active_invoice = await conn.fetchrow(
            "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending' AND expires_at > NOW()",
            user_id
        )
    
    if active_invoice and data.startswith('city_'):
        # Показываем экран с инвойсом вместо перехода к выбору города
        await show_active_invoice(callback, state, user_id, lang)
        return
    
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
    elif data == 'topup_ltc':
        await state.update_data(topup_currency='LTC')
        
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
    
    # Для LTC
    if data == 'crypto_LTC':
        state_data = await state.get_data()
        city = state_data.get('city')
        product = state_data.get('product')
        price = state_data.get('price')
        district = state_data.get('district')
        delivery_type = state_data.get('delivery_type')
        
        product_info = f"{product} в {city}, район {district}, {delivery_type}"
        
        # Создаем заказ с LTC
        order_id = f"order_{int(time.time())}_{user_id}"
                # Получаем текущий курс LTC (теперь всегда возвращает число)
        ltc_rate = await get_ltc_usd_rate()
        
        # Конвертируем USD в LTC
        amount_ltc = price / ltc_rate
        
        # Генерируем новый LTC адрес
        try:
            address_data = ltc_wallet.generate_address()
        except Exception as e:
            logger.error(f"Error generating LTC address: {e}")
            await callback.message.answer(get_text(lang, 'error'))
            return
        
        # Создаем QR-код
        qr_code = ltc_wallet.get_qr_code(address_data['address'], amount_ltc)
        
        expires_at = datetime.now() + timedelta(minutes=30)
        await add_transaction(
            user_id,
            price,
            'LTC',
            order_id,
            qr_code,
            expires_at,
            product_info,
            order_id,  # Используем order_id как invoice_uuid
            address_data['address'],
            str(amount_ltc)  # Конвертируем float в string
        )
        
        payment_text = get_text(
            lang,
            'payment_instructions',
            amount=round(amount_ltc, 8),
            currency='LTC',
            payment_address=address_data['address']
        )
        
        try:
            await callback.message.answer_photo(
                photo=qr_code,
                caption=payment_text
            )
        except Exception as e:
            logger.error(f"Error sending QR code: {e}")
            await callback.message.answer(text=payment_text)
        
        # Запускаем уведомления для этого инвойса
        asyncio.create_task(invoice_notification_loop(user_id, order_id, lang))
        
        await state.set_state(Form.payment)
    else:
        await callback.message.answer("Currently only LTC is supported")

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
        
        # Получаем текущий курс LTC (теперь всегда возвращает число)
        ltc_rate = await get_ltc_usd_rate()
        
        # Конвертируем USD в LTC
        amount_ltc = amount / ltc_rate
        
        # Генерируем новый LTC адрес
        try:
            address_data = ltc_wallet.generate_address()
        except Exception as e:
            logger.error(f"Error generating LTC address: {e}")
            await message.answer(get_text(lang, 'error'))
            return
        
        # Создаем QR-код
        qr_code = ltc_wallet.get_qr_code(address_data['address'], amount_ltc)
        
        order_id = f"topup_{int(time.time())}_{user.id}"
        expires_at = datetime.now() + timedelta(minutes=30)
        
        await add_transaction(
            user.id,
            amount,
            'LTC',
            order_id,
            qr_code,
            expires_at,
            f"Пополнение баланса на {amount}$",
            order_id,
            address_data['address'],
            str(amount_ltc)  # Конвертируем float в string
        )
        
        # Форматируем время истечения
        expires_str = expires_at.strftime("%d.%m.%Y, %H:%M:%S")
        
        payment_text = f"""💳 Пополнение баланса

📝 Адрес для пополнения: `{address_data['address']}`

⏱ Действительно до: {expires_str}

❗️ Важно:
• Отправьте {round(amount_ltc, 8)} LTC на указанный адрес
• Все пополнения на этот адрес будут зачислены на ваш баланс
• После истечения времени адрес освобождается
• Для удобства используйте QR код выше"""
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="✅ Проверить", callback_data="check_invoice"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_invoice")
        )
        builder.row(InlineKeyboardButton(text=get_text(lang, 'back'), callback_data="back_to_topup_menu"))
        
        try:
            await message.answer_photo(
                photo=qr_code,
                caption=payment_text,
                reply_markup=builder.as_markup(),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error sending QR code: {e}")
            await message.answer(
                text=payment_text,
                reply_markup=builder.as_markup(),
                parse_mode='Markdown'
            )
            
        # Запускаем уведомления для этого инвойса
        asyncio.create_task(invoice_notification_loop(user.id, order_id, lang))
            
    except ValueError:
        await message.answer(get_text(lang, 'error'))

# Обработчики для кнопок инвойса
@dp.callback_query(F.data == "check_invoice")
async def check_invoice(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Проверка оплаты... Пожалуйста, подождите")
    # Заглушка для проверки платежа
    await callback.message.answer("Функция проверки находится в разработке")

@dp.callback_query(F.data == "cancel_invoice")
async def cancel_invoice(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_data = await get_user(user_id)
    lang = user_data['language'] or 'ru'
    
    async with db_pool.acquire() as conn:
        # Обновляем статус транзакции
        await conn.execute(
            "UPDATE transactions SET status = 'cancelled' WHERE user_id = $1 AND status = 'pending'",
            user_id
        )
        
        # Увеличиваем счетчик неудачных попыток
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        new_failed = (user['failed_payments'] or 0) + 1
        await conn.execute(
            "UPDATE users SET failed_payments = $1 WHERE user_id = $2",
            new_failed, user_id
        )
        
        # Проверяем на бан
        if new_failed >= 3:
            ban_until = datetime.now() + timedelta(hours=24)
            await conn.execute(
                "UPDATE users SET ban_until = $1 WHERE user_id = $2",
                ban_until, user_id
            )
    
    # Отменяем уведомления
    if user_id in invoice_notifications:
        invoice_notifications[user_id].cancel()
        del invoice_notifications[user_id]
    
    await callback.answer()
    await callback.message.edit_text(
        text=get_text(lang, 'invoice_cancelled', failed_count=new_failed)
    )
    
    if new_failed == 2:
        await callback.message.answer(
            text=get_text(lang, 'almost_banned', remaining=1)
        )
    elif new_failed >= 3:
        await callback.message.answer(
            text=get_text(lang, 'ban_message')
        )
    
    await show_main_menu(callback.message, state, user_id, lang)
    await state.set_state(Form.main_menu)

@dp.callback_query(F.data == "back_to_topup_menu")
async def back_to_topup_menu(callback: types.CallbackQuery, state: FSMContext):
    await show_topup_currency_menu(callback, state)
    await state.set_state(Form.topup_currency)

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

async def main():
    global db_pool
    
    # Удаляем вебхук перед запуском поллинга
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Инициализируем базу данных
    db_pool = await init_db(DATABASE_URL)
    
    # Принудительно загружаем кэш после инициализации БД
    await load_cache()
    
    # Запускаем проверку pending транзакций в фоне
    asyncio.create_task(check_pending_transactions_loop())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
