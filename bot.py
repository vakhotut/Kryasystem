# bot.py
import logging
import random
import time
import asyncio
import os
import socket
import sys
import contextlib
import inspect
import signal
from datetime import datetime, timedelta
from functools import lru_cache
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, BufferedInputFile
from aiogram.exceptions import TelegramConflictError, TelegramRetryAfter, TelegramBadRequest, TelegramNetworkError
import aiohttp
from aiohttp import web
import traceback
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

from db import (
    init_db, get_user, update_user, add_transaction, add_purchase, 
    get_pending_transactions, update_transaction_status, update_transaction_status_by_uuid, 
    get_last_order, is_banned, get_text as db_get_text, 
    load_cache, get_user_orders,
    get_cities_cache, get_districts_cache, get_products_cache, get_delivery_types_cache, get_categories_cache,
    has_active_invoice, add_sold_product, get_product_quantity, reserve_product, release_product,
    get_product_by_name_city, get_product_by_id, get_purchase_with_product,
    get_api_limits, increment_api_request, reset_api_limits,
    is_district_available, is_delivery_type_available,
    add_user_referral, generate_referral_code, db_connection, refresh_cache,
    add_generated_address, update_address_balance, get_deposit_address, create_deposit, update_deposit_confirmations
)
from ltc_hdwallet import ltc_wallet
from apispace import get_ltc_usd_rate, check_ltc_transaction, get_key_usage_stats, monitor_deposits
from apispace import check_ltc_transaction_enhanced, validate_ltc_address, log_transaction_event, get_cached_rate, start_deposit_monitoring

# Импортируем сцены и состояния
from scene import Form, TEXTS, create_language_keyboard, create_main_menu_keyboard, create_balance_menu_keyboard, create_topup_currency_keyboard, create_category_keyboard, create_products_keyboard, create_districts_keyboard, create_delivery_types_keyboard, create_confirmation_keyboard, create_payment_keyboard, create_invoice_keyboard, create_order_history_keyboard, create_order_details_keyboard, create_deposit_address_keyboard, get_text, get_bot_setting

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройки бота
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.environ.get('DATABASE_URL')

# Глобальные переменные для управления временными интервалами
LAST_RATE_UPDATE = 0
RATE_UPDATE_INTERVAL = 3600  # 1 час
TRANSACTION_CHECK_DELAY = 600  # 10 минут
CONFIRMATIONS_REQUIRED = 3  # Требуемое количество подтверждений

# Глобальные переменные
bot = Bot(token=TOKEN, timeout=30)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db_conn_pool = None
invoice_notifications = {}

# Глобальные переменные для настроек
BOT_SETTINGS = {
    'main_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'balance_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'category_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'district_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'delivery_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'confirmation_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'rules_link': "https://t.me/your_rules",
    'operator_link': "https://t.me/your_operator",
    'support_link': "https://t.me/your_support",
    'channel_link': "https://t.me/your_channel",
    'reviews_link': "https://t.me/your_reviews",
    'website_link': "https://yourwebsite.com"
}

# Доступные криптовалюты
CRYPTO_CURRENCIES = {
    'LTC': 'Litecoin'
}

# Кеширование для часто используемых данных
@lru_cache(maxsize=100)
def get_cached_text(lang, key, **kwargs):
    return get_text(lang, key, **kwargs)

def generate_captcha_image(text):
    width, height = 200, 100
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    try:
        font = ImageFont.truetype("arial.ttf", 36) if os.path.exists("arial.ttf") else ImageFont.load_default().font_variant(size=36)
    except:
        font = ImageFont.load_default()
    
    draw.text((10, 10), text, fill=(0, 0, 0), font=font)
    
    for _ in range(100):
        x = random.randint(0, width-1)
        y = random.randint(0, height-1)
        draw.point((x, y), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
    
    buf = BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    return buf

def singleton_check():
    try:
        test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_socket.bind("127.0.0.1", 17891))
        test_socket.close()
        return True
    except socket.error:
        logger.error("Another instance of the bot is already running!")
        return False

async def safe_send_message(chat_id, text, reply_markup=None, parse_mode=None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.exception("Error sending message")
        return None

async def get_user_language(user_id):
    user_data = await get_user(user_id)
    return user_data['language'] or 'ru'

async def check_ban(user_id):
    if await is_banned(user_id):
        lang = await get_user_language(user_id)
        await safe_send_message(user_id, get_cached_text(lang, 'ban_message'))
        return True
    return False

async def check_active_invoice(user_id: int) -> bool:
    return await has_active_invoice(user_id)

async def delete_previous_message(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        if "message to delete not found" not in str(e):
            logger.exception("Error deleting message")

async def safe_delete_previous_message(chat_id: int, message_id: int, state: FSMContext):
    if message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            if "message to delete not found" not in str(e):
                logger.exception("Error deleting message")
    
    await state.update_data(last_message_id=None)

async def show_menu_with_image(message, caption, keyboard, image_url, state):
    try:
        user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
        
        if await check_ban(user_id):
            return None
            
        data = await state.get_data()
        
        if 'last_message_id' in data:
            await safe_delete_previous_message(user_id, data['last_message_id'], state)
        
        try:
            sent_message = await message.answer_photo(
                photo=image_url,
                caption=caption,
                reply_markup=keyboard
            )
        except TelegramBadRequest as e:
            if "wrong file identifier" in str(e).lower() or "failed to get HTTP URL content" in str(e).lower():
                logger.warning(f"Invalid image URL: {image_url}, falling back to text")
                sent_message = await message.answer(
                    text=caption,
                    reply_markup=keyboard
                )
            else:
                raise
        except Exception as e:
            logger.exception("Error sending photo")
            sent_message = await message.answer(
                text=caption,
                reply_markup=keyboard
            )
        
        await state.update_data(last_message_id=sent_message.message_id)
        return sent_message
    except Exception as e:
        logger.exception("Error showing menu with image")
        sent_message = await message.answer(
            text=caption,
            reply_markup=keyboard
        )
        await state.update_data(last_message_id=sent_message.message_id)
        return sent_message

async def show_balance_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        balance_text = get_cached_text(lang, 'balance_instructions', balance=user_data['balance'] or 0)
        
        await show_menu_with_image(
            callback.message,
            balance_text,
            create_balance_menu_keyboard(lang),
            get_bot_setting('balance_menu_image'),
            state
        )
    except Exception as e:
        logger.exception("Error showing balance menu")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

async def show_topup_currency_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        topup_info = get_cached_text(lang, 'balance_topup_info')
        
        await show_menu_with_image(
            callback.message,
            topup_info,
            create_topup_currency_keyboard(),
            get_bot_setting('balance_menu_image'),
            state
        )
    except Exception as e:
        logger.exception("Error showing topup currency menu")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

async def show_active_invoice(callback: types.CallbackQuery, state: FSMContext, user_id: int, lang: str):
    try:
        async with db_connection() as conn:
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending' AND expires_at > NOW()",
                user_id
            )
        
        if invoice:
            expires_time = invoice['expires_at'].strftime("%d.%m.%Y, %H:%M:%S")
            time_left = invoice['expires_at'] - datetime.now()
            time_left_str = f"{int(time_left.total_seconds() // 60)} мин {int(time_left.total_seconds() % 60)} сек"
            
            if "Пополнение баланса" in invoice['product_info']:
                text_key = 'active_invoice'
                crypto_currency = 'LTC'
            else:
                text_key = 'purchase_invoice'
                crypto_currency = 'LTC'
            
            payment_text = get_cached_text(
                lang, 
                text_key,
                product=invoice['product_info'],
                crypto_address=invoice['crypto_address'],
                crypto_amount=round(float(invoice['crypto_amount']), 8),
                crypto=crypto_currency,
                amount=invoice['amount'],
                expires_time=expires_time,
                time_left=time_left_str
            )
            
            asyncio.create_task(invoice_notification_loop(user_id, invoice['order_id'], lang))
            
            try:
                if invoice['payment_url'] and invoice['payment_url'].startswith('http'):
                    await callback.message.answer_photo(
                        photo=invoice['payment_url'],
                        caption=payment_text,
                        reply_markup=create_invoice_keyboard(),
                        parse_mode='Markdown'
                    )
                else:
                    await callback.message.answer(
                        text=payment_text,
                        reply_markup=create_invoice_keyboard(),
                        parse_mode='Markdown'
                    )
            except Exception as e:
                logger.exception("Error sending invoice with photo")
                await callback.message.answer(
                    text=payment_text,
                    reply_markup=create_invoice_keyboard(),
                    parse_mode='Markdown'
                )
    except Exception as e:
        logger.exception("Error showing active invoice")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

async def get_ltc_usd_rate_cached():
    global LAST_RATE_UPDATE
    current_time = time.time()
    
    if current_time - LAST_RATE_UPDATE > RATE_UPDATE_INTERVAL:
        rate = await get_ltc_usd_rate()
        LAST_RATE_UPDATE = current_time
        return rate
    
    # Используем функцию из apispace.py
    cached_rate, from_cache = await get_cached_rate()
    if from_cache:
        return cached_rate
    
    rate = await get_ltc_usd_rate()
    LAST_RATE_UPDATE = current_time
    return rate

async def invoice_notification_loop(user_id: int, order_id: str, lang: str):
    """Цикл уведомлений о времени жизни инвойса"""
    try:
        async with db_connection() as conn:
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE order_id = $1",
                order_id
            )
        
        if not invoice:
            return
            
        expires_at = invoice['expires_at']
        notification_intervals = [1800, 900, 300, 60]  # 30, 15, 5, 1 минута в секундах
        
        while True:
            now = datetime.now()
            if now >= expires_at:
                break
                
            time_left = (expires_at - now).total_seconds()
            
            # Проверяем, нужно ли отправить уведомление
            for interval in notification_intervals:
                if time_left <= interval:
                    # Удаляем этот интервал, чтобы не отправлять повторно
                    notification_intervals.remove(interval)
                    
                    time_left_str = f"{int(time_left // 60)} мин {int(time_left % 60)} сек"
                    await safe_send_message(
                        user_id,
                        get_cached_text(lang, 'invoice_time_left', time_left=time_left_str)
                    )
                    break
                    
            await asyncio.sleep(10)
            
    except Exception as e:
        logger.exception("Error in invoice notification loop")

async def check_pending_transactions_loop():
    while True:
        try:
            transactions = await get_pending_transactions()
            
            for transaction in transactions:
                # Пропускаем транзакции пополнения баланса
                if "Пополнение баланса" in transaction['product_info']:
                    continue
                    
                created_at = transaction['created_at']
                if (datetime.now() - created_at).total_seconds() >= TRANSACTION_CHECK_DELAY:
                    is_paid = await check_ltc_transaction(
                        transaction['crypto_address'],
                        float(transaction['crypto_amount'])
                    )
                    
                    if is_paid:
                        await update_transaction_status(transaction['order_id'], 'completed')
                        await process_successful_payment(transaction)
            
            await asyncio.sleep(60)
        except Exception as e:
            logger.exception("Error in check_pending_transactions")
            await asyncio.sleep(60)

async def get_confirmations_count(txid: str) -> int:
    """Получить количество подтверждений транзакции по её txid"""
    try:
        from apispace import get_address_transactions
        # Найдем адрес по txid из таблицы deposits
        async with db_connection() as conn:
            deposit = await conn.fetchrow("SELECT address FROM deposits WHERE txid = $1", txid)
            if not deposit:
                return 0
            address = deposit['address']
        
        # Получим все транзакции для этого адреса
        transactions = await get_address_transactions(address)
        for tx in transactions:
            if tx.get('txid') == txid:
                return tx.get('confirmations', 0)
        return 0
    except Exception as e:
        logger.exception(f"Error getting confirmations for txid {txid}")
        return 0

async def process_successful_payment(transaction):
    try:
        user_id = transaction['user_id']
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        if "Пополнение баланса" not in transaction['product_info']:
            parts = transaction['product_info'].split(', ')
            if len(parts) >= 3:
                product = parts[0]
                district = parts[1].replace('район ', '')
                delivery_type = parts[2]
                
                product_id = transaction.get('product_id')
                
                product_info = None
                if product_id:
                    product_info = await get_product_by_id(product_id)
                
                purchase_id = await add_purchase(
                    user_id,
                    product,
                    transaction['amount'],
                    district,
                    delivery_type,
                    product_id,
                    product_info['image_url'] if product_info else None,
                    product_info['description'] if product_info else None
                )
                
                if purchase_id and product_id and product_info:
                    await add_sold_product(
                        product_id, 
                        product_info['subcategory_id'], 
                        user_id, 
                        1, 
                        transaction['amount'], 
                        purchase_id
                    )
                    
                    caption = f"{product_info['name']}\n\n{product_info['description']}\n\nЦена: ${transaction['amount']}"
                    if product_info['image_url']:
                        await bot.send_photo(
                            chat_id=user_id,
                            photo=product_info['image_url'],
                            caption=caption
                        )
                    else:
                        await bot.send_message(
                            chat_id=user_id,
                            text=caption
                        )
        else:
            async with db_connection() as conn:
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                    transaction['amount'], user_id
                )
                await safe_send_message(
                    user_id,
                    get_cached_text(lang, 'balance_add_success', 
                            amount=transaction['amount'], 
                            balance=user_data['balance'] + transaction['amount'])
                )
                
    except Exception as e:
        logger.exception("Error processing successful payment")

async def reset_api_limits_loop():
    while True:
        try:
            await reset_api_limits()
            await asyncio.sleep(86400)
        except Exception as e:
            logger.exception("Error resetting API limits")
            await asyncio.sleep(3600)

async def check_active_invoice_for_user(user_id, invoice_type="any"):
    async with db_connection() as conn:
        if invoice_type == "topup":
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending' AND expires_at > NOW() AND product_info LIKE '%Пополнение баланса%'",
                user_id
            )
        elif invoice_type == "purchase":
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending' AND expires_at > NOW() AND product_info NOT LIKE '%Пopолнение баланса%'",
                user_id
            )
        else:
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending' AND expires_at > NOW()",
                user_id
            )
    return invoice is not None

async def cleanup_invalid_addresses():
    """Очистка невалидных адресов из базы данных"""
    async with db_connection() as conn:
        addresses = await conn.fetch("SELECT address FROM generated_addresses")
        for addr in addresses:
            # ИСПРАВЛЕНИЕ: добавлен await перед validate_ltc_address
            if not await validate_ltc_address(addr['address']):
                logger.warning(f"Removing invalid address: {addr['address']}")
                await conn.execute("DELETE FROM generated_addresses WHERE address = $1", addr['address'])

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    try:
        await state.clear()
        
        user = message.from_user
        user_id = user.id
        
        if await check_ban(user_id):
            return
        
        referrer_code = None
        if len(message.text.split()) > 1:
            referrer_code = message.text.split()[1]
        
        existing_user = await get_user(user_id)
        if existing_user:
            if existing_user['captcha_passed']:
                lang = existing_user['language'] or 'ru'
                await message.answer(get_cached_text(lang, 'welcome'))
                await show_main_menu(message, state, user_id, lang)
                await state.set_state(Form.main_menu)
                return
        else:
            if referrer_code:
                await add_user_referral(user_id, referrer_code)
        
        await message.answer('Выберите язык / Select language / აირჩიეთ ენა:', reply_markup=create_language_keyboard())
        await state.set_state(Form.language)
    except Exception as e:
        logger.exception("Error in start command")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.language)
async def process_language(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        lang_code = callback.data.replace('lang_', '')
        
        await update_user(user_id, language=lang_code)
        
        await callback.answer()
        await callback.message.answer(text=get_cached_text(lang_code, 'language_selected'))
        
        captcha_code = ''.join(random.choices('0123456789', k=5))
        await state.update_data(captcha=captcha_code)
        
        captcha_image = generate_captcha_image(captcha_code)
        
        try:
            # ИСПРАВЛЕНИЕ: используем BufferedInputFile вместо InputFile
            input_file = BufferedInputFile(captcha_image.getvalue(), filename="captcha.png")
            await callback.message.answer_photo(
                photo=input_file,
                caption=get_cached_text(lang_code, 'captcha_enter')
            )
        except Exception as e:
            logger.exception("Error sending captcha image")
            await callback.message.answer(
                text=f"{get_cached_text(lang_code, 'captcha_enter')}\n\nКод: {captcha_code}"
            )
        
        await state.set_state(Form.captcha)
    except Exception as e:
        logger.exception("Error processing language")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.message(Form.captcha)
async def process_captcha(message: types.Message, state: FSMContext):
    try:
        user_input = message.text
        user = message.from_user
        
        if await check_ban(user.id):
            return
            
        data = await state.get_data()
        
        if user_input == data.get('captcha'):
            async with db_connection() as conn:
                await conn.execute(
                    'INSERT INTO users (user_id, username, first_name, captcha_passed) VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO UPDATE SET captcha_passed = $5',
                    user.id, user.username, user.first_name, 1, 1
                )
            
            user_data = await get_user(user.id)
            lang = user_data['language'] or 'ru'
            await message.answer(get_cached_text(lang, 'captcha_success'))
            await show_main_menu(message, state, user.id, lang)
            await state.set_state(Form.main_menu)
        else:
            user_data = await get_user(user.id)
            lang = user_data['language'] or 'ru'
            await message.answer(get_cached_text(lang, 'captcha_failed'))
    except Exception as e:
        logger.exception("Error processing captcha")
        await message.answer("Произошла ошибка. Попробуйте позже.")

async def show_main_menu(message: types.Message, state: FSMContext, user_id: int, lang: str):
    try:
        user = await get_user(user_id)
        if not user:
            return
        
        if await check_ban(user_id):
            return
            
        if not user.get('referral_code'):
            referral_code = await generate_referral_code(user_id)
        else:
            referral_code = user['referral_code']
        
        bot_username = (await bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={referral_code}"
        
        shop_description = get_cached_text(lang, 'main_menu_description') + "\n\n"
        
        user_info_text = get_cached_text(
            lang, 
            'main_menu', 
            name=user['first_name'] or 'N/A',
            username=user['username'] or 'N/A',
            purchases=user['purchase_count'] or 0,
            discount=user['discount'] or 0,
            balance=user['balance'] or 0
        )
        
        referral_info = f"\n👥 Приглашено друзей: {user.get('referral_count', 0)}"
        referral_info += f"\n💰 Заработано с рефералов: ${user.get('earned_from_referrals', 0)}"
        referral_info += f"\n🔗 Реферальная ссылка: {referral_link}"
        
        full_text = shop_description + user_info_text + referral_info
        
        cities = get_cities_cache()
        
        await show_menu_with_image(
            message,
            full_text,
            create_main_menu_keyboard(user, cities, lang),
            get_bot_setting('main_menu_image'),
            state
        )
    except Exception as e:
        logger.exception("Error showing main menu")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.main_menu)
async def process_main_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        data = callback.data
        
        if await check_active_invoice(user_id) and data.startswith('city_'):
            await show_active_invoice(callback, state, user_id, lang)
            return
        
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        if data.startswith('city_'):
            city = data.replace('city_', '')
            
            products_cache = get_products_cache()
            if city not in products_cache or not any(product_info.get('quantity', 0) > 0 for product_info in products_cache[city].values()):
                await callback.message.answer(
                    "🛒 Этот город пока пустой. Ожидайте пополнения. Следите за нашим канал в ожидании пополнения."
                )
                return
        
            await state.update_data(city=city)
            
            categories_cache = get_categories_cache()
            
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_category'),
                create_category_keyboard(categories_cache),
                get_bot_setting('category_menu_image'),
                state
            )
            await state.set_state(Form.category)
        elif data == 'balance':
            if await check_active_invoice_for_user(user_id, "topup"):
                await show_active_invoice(callback, state, user_id, lang)
                return
            await show_balance_menu(callback, state)
            await state.set_state(Form.balance_menu)
        elif data == 'order_history':
            await show_order_history(callback, state)
        elif data == 'bonuses':
            sent_message = await callback.message.answer(
                text=get_cached_text(lang, 'bonuses')
            )
            await state.update_data(last_message_id=sent_message.message_id)
        elif data == 'change_language':
            await callback.message.answer('Выберите язык / Select language / აირჩიეთ ენა:', reply_markup=create_language_keyboard())
            await state.set_state(Form.language)
        elif data == 'main_menu':
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
        elif data.startswith('view_order_'):
            await view_order_details(callback, state)
    except Exception as e:
        logger.exception("Error processing main menu")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(F.data == "order_history")
async def show_order_history(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        orders = await get_user_orders(user_id, 15)
        
        if not orders:
            await callback.answer(get_cached_text(lang, 'no_orders'))
            return
            
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        sent_message = await callback.message.answer(
            text="📋 История ваших заказов:",
            reply_markup=create_order_history_keyboard(orders)
        )
        
        await state.update_data(last_message_id=sent_message.message_id)
        await state.set_state(Form.order_history)
        await callback.answer()
        
    except Exception as e:
        logger.exception("Error showing order history")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.order_history, F.data.startswith("view_order_"))
async def view_order_details(callback: types.CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback.data.replace("view_order_", ""))
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        async with db_connection() as conn:
            order = await conn.fetchrow('''
                SELECT 
                    p.*, 
                    CASE 
                        WHEN p.product_id IS NOT NULL AND p.product_id ~ '^[0-9]+$' 
                        THEN pr.description 
                        ELSE NULL 
                    END as product_description,
                    CASE 
                        WHEN p.product_id IS NOT NULL AND p.product_id ~ '^[0-9]+$' 
                        THEN pr.image_url 
                        ELSE NULL 
                    END as product_image,
                    CASE 
                        WHEN p.product_id IS NOT NULL AND p.product_id ~ '^[0-9]+$' 
                        THEN c.name 
                        ELSE NULL 
                    END as city_name
                FROM purchases p
                LEFT JOIN products pr ON 
                    p.product_id IS NOT NULL AND 
                    p.product_id ~ '^[0-9]+$' AND 
                    CAST(p.product_id AS INTEGER) = pr.id
                LEFT JOIN cities c ON pr.city_id = c.id
                WHERE p.id = $1 AND p.user_id = $2
            ''', order_id, user_id)
        
        if not order:
            await callback.answer("Заказ не найден или у вас нет доступа к этому заказу")
            return
            
        order_time = order['purchase_time'].strftime("%d.%m.%Y %H:%M:%S")
        
        order_text = (
            f"🆔 <b>ID заказа:</b> {order['id']}\n"
            f"📦 <b>Товар:</b> {order['product']}\n"
            f"💵 <b>Цена:</b> {order['price']}$\n"
        )
        
        if order.get('city_name'):
            order_text += f"🏙 <b>Город:</b> {order['city_name']}\n"
            
        order_text += (
            f"📍 <b>Район:</b> {order['district']}\n"
            f"🚚 <b>Тип доставки:</b> {order['delivery_type']}\n"
        )
        
        if order.get('product_description'):
            description = order['product_description']
            if len(description) > 200:
                description = description[:197] + "..."
            order_text += f"📝 <b>Описание:</b> {description}\n"
            
        order_text += (
            f"🕐 <b>Время заказа:</b> {order_time}\n"
            f"📊 <b>Статус:</b> {order['status']}"
        )
        
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(callback.message.chat.id, state_data['last_message_id'], state)
        
        if order.get('product_image'):
            try:
                sent_message = await callback.message.answer_photo(
                    photo=order['product_image'],
                    caption=order_text,
                    reply_markup=create_order_details_keyboard(),
                    parse_mode='HTML'
                )
                await state.update_data(last_message_id=sent_message.message_id)
            except Exception as e:
                logger.exception("Error sending order photo, falling back to text")
                sent_message = await callback.message.answer(
                    text=order_text,
                    reply_markup=create_order_details_keyboard(),
                    parse_mode='HTML'
                )
                await state.update_data(last_message_id=sent_message.message_id)
        else:
            sent_message = await callback.message.answer(
                text=order_text,
                reply_markup=create_order_details_keyboard(),
                parse_mode='HTML'
            )
            await state.update_data(last_message_id=sent_message.message_id)
        
        await callback.answer()
        
    except ValueError:
        await callback.answer("Неверный формат ID заказа")
    except Exception as e:
        logger.exception("Error in view order handler")
        await callback.answer("Произошла ошибка при получении информации о заказе")

@dp.callback_query(Form.order_history, F.data == "main_menu")
async def process_order_history_main_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        await show_main_menu(callback.message, state, user_id, lang)
        await state.set_state(Form.main_menu)
        await callback.answer()
    except Exception as e:
        logger.exception("Error processing main menu from order history")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.order_history, F.data == "order_history")
async def process_back_to_order_history(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        await show_order_history(callback, state)
    except Exception as e:
        logger.exception("Error going back to order history")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.balance_menu)
async def process_balance_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        data = callback.data
        
        if data == 'topup_balance':
            if await check_active_invoice_for_user(user_id, "topup"):
                await show_active_invoice(callback, state, user_id, lang)
                return
            await show_topup_currency_menu(callback, state)
            await state.set_state(Form.topup_currency)
        elif data == 'main_menu':
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
    except Exception as e:
        logger.exception("Error processing balance menu")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.topup_currency)
async def process_topup_currency(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        data = callback.data
        
        if data == 'back_to_balance_menu':
            await show_balance_menu(callback, state)
            await state.set_state(Form.balance_menu)
        elif data == 'topup_ltc':
            # Запрашиваем ввод суммы
            await callback.message.answer(get_cached_text(lang, 'enter_topup_amount'))
            await state.set_state(Form.topup_amount)
    except Exception as e:
        logger.exception("Error processing topup currency")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.message(Form.topup_amount)
async def process_topup_amount(message: types.Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        try:
            amount = float(message.text)
            if amount <= 0:
                await message.answer(get_cached_text(lang, 'invalid_amount'))
                return
                
            # Сохраняем сумму в state
            await state.update_data(topup_amount=amount)
            
            # Генерируем адрес для пополнения
            address_data = ltc_wallet.generate_address()
            address = address_data['address']
            index = address_data['index']
            
            await add_generated_address(
                address=address,
                index=index,
                user_id=user_id,
                label=f"Balance topup {amount} USD"
            )
            
            # Получаем курс LTC
            ltc_rate = await get_ltc_usd_rate_cached()
            amount_ltc = amount / ltc_rate
            
            # Создаем инвойс
            order_id = f"topup_{int(time.time())}_{user_id}"
            expires_at = datetime.now() + timedelta(minutes=30)
            
            # ИЗМЕНЕНИЕ: убрали сумму из product_info
            await add_transaction(
                user_id,
                amount,
                'LTC',
                order_id,
                None,  # QR-код будет сгенерирован позже
                expires_at,
                "Пополнение баланса",  # Было: f"Пополнение баланса на {amount}$"
                order_id,
                address,
                amount_ltc
            )
            
            # Генерируем QR-код
            qr_code = ltc_wallet.get_qr_code(address, amount_ltc)
            
            expires_str = expires_at.strftime("%d.%m.%Y, %H:%M:%S")
            time_left = expires_at - datetime.now()
            time_left_str = f"{int(time_left.total_seconds() // 60)} мин {int(time_left.total_seconds() % 60)} сек"
            
            payment_text = get_cached_text(
                lang,
                'active_invoice',
                crypto_address=address,
                crypto_amount=round(amount_ltc, 8),
                crypto='LTC',
                amount=amount,
                expires_time=expires_str,
                time_left=time_left_str
            )
            
            try:
                # ИСПРАВЛЕНИЕ: используем BufferedInputFile для QR-кода
                photo = BufferedInputFile(qr_code, filename="qr.png")
                await message.answer_photo(
                    photo=photo,
                    caption=payment_text,
                    reply_markup=create_invoice_keyboard(),
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.exception("Error sending QR code")
                await message.answer(
                    text=payment_text,
                    reply_markup=create_invoice_keyboard(),
                    parse_mode='Markdown'
                )
                
            asyncio.create_task(invoice_notification_loop(user_id, order_id, lang))
            asyncio.create_task(check_invoice_after_delay(order_id, user_id, lang))
            
            await state.set_state(Form.deposit_address)
                
        except ValueError:
            await message.answer(get_cached_text(lang, 'invalid_amount'))
            
    except Exception as e:
        logger.exception("Error processing topup amount")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.deposit_address, F.data == "check_deposit_status")
async def check_deposit_status(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer("Проверяем статус депозита...")
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        # Получаем последний адрес пользователя
        address = await get_deposit_address(user_id)
        
        if not address:
            await callback.message.answer("❌ Адрес для пополнения не найден")
            return
            
        # Проверяем транзакции для этого адреса
        from apispace import get_address_transactions
        transactions = await get_address_transactions(address)
        
        if not transactions:
            await callback.message.answer("📭 На адрес еще не поступали транзакции")
            return
            
        # Ищем неподтвержденные депозиты
        async with db_connection() as conn:
            deposits = await conn.fetch(
                "SELECT * FROM deposits WHERE address = $1 AND user_id = $2 ORDER BY created_at DESC",
                address, user_id
            )
            
        if not deposits:
            await callback.message.answer("📭 Транзакции найдены, но еще не обработаны системой")
            return
            
        for deposit in deposits:
            if deposit['status'] == 'confirmed':
                await callback.message.answer(
                    f"✅ Депозит подтвержден! Зачислено: ${deposit['amount_usd']:.2f}"
                )
                return
            elif deposit['status'] == 'pending':
                # ИСПРАВЛЕНИЕ: используем новую функцию для получения подтверждений
                confirmations = await get_confirmations_count(deposit['txid'])
                await callback.message.answer(
                    f"⏳ Депозит в обработке: {confirmations}/{CONFIRMATIONS_REQUIRED} подтверждений\n"
                    f"💰 Сумма: ${deposit['amount_usd']:.2f}"
                )
                return
                
        await callback.message.answer("📭 Нет активных депозитов для этого адреса")
        
    except Exception as e:
        logger.exception("Error checking deposit status")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.category)
async def process_category(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        data = callback.data
        
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        if data == 'main_menu':
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
            return
        
        category = data.replace('cat_', '')
        city_data = await state.get_data()
        city = city_data.get('city')
        
        products_cache = get_products_cache()
        
        if city not in products_cache:
            sent_message = await callback.message.answer(
                text=get_cached_text(lang, 'error')
            )
            await state.update_data(last_message_id=sent_message.message_id)
            return
        
        category_products = {}
        for product_name, product_info in products_cache[city].items():
            if product_info['category'] == category and product_info.get('quantity', 1) > 0:
                category_products[product_name] = product_info
        
        if not category_products:
            sent_message = await callback.message.answer(
                text=get_cached_text(lang, 'error')
            )
            await state.update_data(last_message_id=sent_message.message_id)
            return
        
        await state.update_data(category=category)
        
        await show_menu_with_image(
            callback.message,
            "Выберите товар:",
            create_products_keyboard(category_products),
            get_bot_setting('category_menu_image'),
            state
        )
        await state.set_state(Form.district)
    except Exception as e:
        logger.exception("Error processing category")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.district)
async def process_district(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        data = callback.data
        
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        if data == 'back_to_city':
            city_data = await state.get_data()
            city = city_data.get('city')
            
            categories_cache = get_categories_cache()
            
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_category'),
                create_category_keyboard(categories_cache),
                get_bot_setting('category_menu_image'),
                state
            )
            await state.set_state(Form.category)
            return
        
        if data.startswith('prod_'):
            product_name = data.replace('prod_', '')
            city_data = await state.get_data()
            city = city_data.get('city')
            category = city_data.get('category')
            
            products_cache = get_products_cache()
            
            if city not in products_cache or product_name not in products_cache[city]:
                sent_message = await callback.message.answer(
                text=get_cached_text(lang, 'error')
                )
                await state.update_data(last_message_id=sent_message.message_id)
                return
            
            product_info = products_cache[city][product_name]
            await state.update_data(product=product_name)
            await state.update_data(price=product_info['price'])
            
            districts = []
            for district in get_districts_cache().get(city, []):
                if await is_district_available(city, district):
                    districts.append(district)
            
            if not districts:
                sent_message = await callback.message.answer(
                    text="Нет доступных районов для этого города"
                )
                await state.update_data(last_message_id=sent_message.message_id)
                return
            
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_district'),
                create_districts_keyboard(districts),
                get_bot_setting('district_menu_image'),
                state
            )
            await state.set_state(Form.delivery)
    except Exception as e:
        logger.exception("Error processing district")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.delivery)
async def process_delivery(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        data = callback.data
        
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        if data == 'back_to_district':
            city_data = await state.get_data()
            city = city_data.get('city')
            
            districts = []
            for district in get_districts_cache().get(city, []):
                if await is_district_available(city, district):
                    districts.append(district)
            
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_district'),
                create_districts_keyboard(districts),
                get_bot_setting('district_menu_image'),
            )
            await state.set_state(Form.district)
            return
        
        if data.startswith('dist_'):
            district = data.replace('dist_', '')
            await state.update_data(district=district)
            
            delivery_types = []
            for del_type in get_delivery_types_cache():
                if await is_delivery_type_available(del_type):
                    delivery_types.append(del_type)
            
            if not delivery_types:
                sent_message = await callback.message.answer(
                    text="Нет доступных типов доставки"
                )
                await state.update_data(last_message_id=sent_message.message_id)
                return
            
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_delivery'),
                create_delivery_types_keyboard(delivery_types),
                get_bot_setting('delivery_menu_image'),
                state
            )
            await state.set_state(Form.delivery)
        
        elif data.startswith('del_'):
            delivery_type = data.replace('del_', '')
            
            if not await is_delivery_type_available(delivery_type):
                sent_message = await callback.message.answer(
                    text="Этот тип доставки временно недоступен"
                )
                await state.update_data(last_message_id=sent_message.message_id)
                return
            
            await state.update_data(delivery_type=delivery_type)
            
            state_data = await state.get_data()
            city = state_data.get('city')
            product = state_data.get('product')
            price = state_data.get('price')
            district = state_data.get('district')
            
            order_text = get_cached_text(
                lang, 
                'order_summary',
                product=product,
                price=price,
                district=district,
                delivery_type=delivery_type
            )
            
            await show_menu_with_image(
                callback.message,
                order_text,
                create_confirmation_keyboard(),
                get_bot_setting('confirmation_menu_image'),
                state
            )
            await state.set_state(Form.confirmation)
    except Exception as e:
        logger.exception("Error processing delivery")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.confirmation)
async def process_confirmation(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        data = callback.data
        
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        if data == 'back_to_delivery':
            delivery_types = []
            for del_type in get_delivery_types_cache():
                if await is_delivery_type_available(del_type):
                    delivery_types.append(del_type)
            
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_delivery'),
                create_delivery_types_keyboard(delivery_types),
                get_bot_setting('delivery_menu_image'),
                state
            )
            await state.set_state(Form.delivery)
            return
        
        if data == 'confirm_yes':
            state_data = await state.get_data()
            city = state_data.get('city')
            product_name = state_data.get('product')
            price = state_data.get('price')
            district = state_data.get('district')
            delivery_type = state_data.get('delivery_type')
            
            # Рассчитываем итоговую цену с учетом скидки
            discount = user_data.get('discount', 0)
            final_price = price * (1 - discount / 100)
            
            # Сохраняем итоговую цену
            await state.update_data(final_price=final_price)
            
            # Формируем текст подтверждения
            confirmation_text = get_cached_text(
                lang, 
                'order_confirmation',
                product=product_name,
                price=price,
                discount=discount,
                final_price=final_price,
                district=district,
                delivery_type=delivery_type
            )
            
            # Добавляем информацию о балансе, если он есть
            user_balance = user_data['balance'] or 0
            if user_balance > 0:
                confirmation_text += f"\n\n💰 Ваш баланс: ${user_balance}"
                if user_balance >= final_price:
                    confirmation_text += f"\n✅ Достаточно для оплаты"
                else:
                    confirmation_text += f"\n❌ Недостаточно для оплаты (нужно ${final_price})"
            
            await show_menu_with_image(
                callback.message,
                confirmation_text,
                create_payment_keyboard(user_balance, final_price),
                get_bot_setting('confirmation_menu_image'),
                state
            )
            await state.set_state(Form.crypto_currency)
        else:
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
    except Exception as e:
        logger.exception("Error processing confirmation")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(F.data == "pay_with_balance")
async def pay_with_balance(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        state_data = await state.get_data()
        city = state_data.get('city')
        product_name = state_data.get('product')
        price = state_data.get('price')
        district = state_data.get('district')
        delivery_type = state_data.get('delivery_type')
        final_price = state_data.get('final_price', price)
        
        if (user_data['balance'] or 0) < final_price:
            await callback.message.answer("Недостаточно средств на балансе")
            return
        
        async with db_connection() as conn:
            product_row = await conn.fetchrow(
                "SELECT * FROM products WHERE name = $1 AND city_id = (SELECT id FROM cities WHERE name = $2) LIMIT 1",
                product_name, city
            )
            
            if not product_row:
                await callback.message.answer("Ошибка: товар не найден")
                return
            
            if product_row['quantity'] <= 0:
                await callback.message.answer(get_cached_text(lang, 'product_out_of_stock'))
                return

            if not await reserve_product(product_row['id']):
                await callback.message.answer(get_cached_text(lang, 'product_out_of_stock'))
                return

            product_id = product_row['id']
        
        try:
            async with db_connection() as conn:
                await conn.execute(
                    "UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                    final_price, user_id
                )
                
                purchase_id = await add_purchase(
                    user_id, product_name, final_price, district, delivery_type,
                    product_id, product_row['image_url'], product_row['description']
                )
                
                if purchase_id:
                    await add_sold_product(
                        product_row['id'], 
                        product_row['subcategory_id'],
                        user_id, 
                        1, 
                        final_price, 
                        purchase_id
                    )
            
            await callback.message.answer(
                f"✅ Оплата прошла успешно! Товар {product_name} будет доставлен."
            )
            
            if product_row['image_url']:
                caption = f"{product_row['name']}\n\n{product_row['description']}\n\nЦена: ${final_price}"
                await callback.message.answer_photo(
                    photo=product_row['image_url'],
                    caption=caption
                )
            else:
                await callback.message.answer(
                    f"{product_row['name']}\n\n{product_row['description']}\n\nЦена: ${final_price}"
                )
            
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
            
        except Exception as e:
            await release_product(product_row['id'])
            logger.exception("Error in pay_with_balance")
            await callback.answer("Произошла ошибка. Попробуйте позже.")
        
    except Exception as e:
        logger.exception("Error in pay_with_balance")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(Form.crypto_currency)
async def process_crypto_currency(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        data = callback.data
        
        # Добавляем проверку для check_invoice
        if data == "check_invoice":
            await check_invoice_enhanced(callback, state)
            return
            
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        if data == 'back_to_confirmation':
            state_data = await state.get_data()
            city = state_data.get('city')
            product = state_data.get('product')
            price = state_data.get('price')
            district = state_data.get('district')
            delivery_type = state_data.get('delivery_type')
            
            # Рассчитываем итоговую цену с учетом скидки
            discount = user_data.get('discount', 0)
            final_price = price * (1 - discount / 100)
            
            # Формируем текст подтверждения
            confirmation_text = get_cached_text(
                lang, 
                'order_confirmation',
                product=product,
                price=price,
                discount=discount,
                final_price=final_price,
                district=district,
                delivery_type=delivery_type
            )
            
            # Добавляем информацию о балансе, если он есть
            user_balance = user_data['balance'] or 0
            if user_balance > 0:
                confirmation_text += f"\n\n💰 Ваш баланс: ${user_balance}"
                if user_balance >= final_price:
                    confirmation_text += f"\n✅ Достаточно для оплаты"
                else:
                    confirmation_text += f"\n❌ Недостаточно для оплаты (нужно ${final_price})"
            
            await show_menu_with_image(
                callback.message,
                confirmation_text,
                create_payment_keyboard(user_balance, final_price),
                get_bot_setting('confirmation_menu_image'),
                state
            )
            await state.set_state(Form.confirmation)
            return
        
        if data == 'crypto_LTC':
            if await check_active_invoice_for_user(user_id, "purchase"):
                await show_active_invoice(callback, state, user_id, lang)
                return
            
            state_data = await state.get_data()
            city = state_data.get('city')
            product_name = state_data.get('product')
            price = state_data.get('price')
            district = state_data.get('district')
            delivery_type = state_data.get('delivery_type')
            final_price = state_data.get('final_price', price)
            
            product_info = f"{product_name} в {city}, район {district}, {delivery_type}"
            
            order_id = f"order_{int(time.time())}_{user_id}"
            ltc_rate = await get_ltc_usd_rate_cached()
            amount_ltc = final_price / ltc_rate
            
            async with db_connection() as conn:
                product_row = await conn.fetchrow(
                    "SELECT * FROM products WHERE name = $1 AND city_id = (SELECT id FROM cities WHERE name = $2) LIMIT 1",
                    product_name, city
                )
                
                if not product_row:
                    await callback.message.answer("Ошибка: товар не найден")
                    return
                
                if product_row['quantity'] <= 0:
                    await callback.message.answer(get_cached_text(lang, 'product_out_of_stock'))
                    return

                if not await reserve_product(product_row['id']):
                    await callback.message.answer(get_cached_text(lang, 'product_out_of_stock'))
                    return

                product_id = product_row['id']
            
            try:
                address_data = ltc_wallet.generate_address()
            except Exception as e:
                logger.exception("Error generating LTC address")
                await callback.message.answer(get_cached_text(lang, 'error'))
                return
            
            qr_code = ltc_wallet.get_qr_code(address_data['address'], amount_ltc)
            expires_at = datetime.now() + timedelta(minutes=30)
            
            await add_transaction(
                user_id,
                final_price,
                'LTC',
                order_id,
                qr_code,
                expires_at,
                product_info,
                order_id,
                address_data['address'],
                amount_ltc,
                product_id
            )
            
            await state.update_data(product_id=product_id)
            
            expires_time = expires_at.strftime("%d.%m.%Y, %H:%M:%S")
            time_left = expires_at - datetime.now()
            time_left_str = f"{int(time_left.total_seconds() // 60)} мин {int(time_left.total_seconds() % 60)} сек"
            
            payment_text = get_cached_text(
                lang,
                'purchase_invoice',
                product=product_name,
                crypto_address=address_data['address'],
                crypto_amount=round(amount_ltc, 8),
                crypto='LTC',
                amount=final_price,
                expires_time=expires_time,
                time_left=time_left_str
            )
            
            try:
                # ИСПРАВЛЕНИЕ: используем BufferedInputFile для QR-кода
                photo = BufferedInputFile(qr_code, filename="qr.png")
                await callback.message.answer_photo(
                    photo=photo,
                    caption=payment_text,
                    reply_markup=create_invoice_keyboard(),
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.exception("Error sending QR code")
                await callback.message.answer(
                    text=payment_text,
                    reply_markup=create_invoice_keyboard(),
                    parse_mode='Markdown'
                )
            
            await asyncio.sleep(TRANSACTION_CHECK_DELAY)
            asyncio.create_task(check_invoice_after_delay(order_id, user_id, lang))
            
            asyncio.create_task(invoice_notification_loop(user_id, order_id, lang))
            await state.set_state(Form.payment)
        else:
            await callback.message.answer(get_cached_text(lang, 'only_ltc_supported'))
    except Exception as e:
        logger.exception("Error processing crypto currency")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

async def check_invoice_after_delay(order_id, user_id, lang):
    await asyncio.sleep(TRANSACTION_CHECK_DELAY)
    
    async with db_connection() as conn:
        invoice = await conn.fetchrow(
            "SELECT * FROM transactions WHERE order_id = $1",
            order_id
        )
    
    if invoice and invoice['status'] == 'pending':
        # Используем улучшенную проверку транзакций
        tx_check = await check_ltc_transaction_enhanced(
            invoice['crypto_address'],
            float(invoice['crypto_amount'])
        )
        
        if tx_check['confirmed'] and tx_check['confirmations'] >= CONFIRMATIONS_REQUIRED:
            await update_transaction_status(order_id, 'completed')
            await process_successful_payment(invoice)
        elif tx_check['unconfirmed']:
            # Транзакция есть в мемпуле, но еще не подтверждена
            try:
                await bot.send_message(
                    user_id,
                    f"⏳ Транзакция обнаружена в мемпуле. Ожидаем подтверждений ({tx_check.get('confirmations', 0)}/{CONFIRMATIONS_REQUIRED})"
                )
            except Exception as e:
                logger.exception("Error sending mempool notification")
        else:
            try:
                await bot.send_message(
                    user_id,
                    "⏰ Время оплата истекло. Если вы уже отправили средства, они будут зачислены после подтверждения сети."
                )
            except Exception as e:
                logger.exception("Error sending delay notification")

@dp.callback_query(F.data == "check_invoice")
async def check_invoice_enhanced(callback: types.CallbackQuery, state: FSMContext):
    """Улучшенная проверка инвойса с детальным логированием"""
    try:
        await callback.answer("Проверка оплата...")
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        async with db_connection() as conn:
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending'",
                user_id
            )
        
        if not invoice:
            log_transaction_event(
                "unknown", "unknown", 0, 
                "NOT_FOUND", "Active invoice not found", "WARNING"
            )
            await callback.message.answer("❌ Активный инвойс не найден")
            return
        
        # Детальная проверка транзакции
        tx_check = await check_ltc_transaction_enhanced(
            invoice['crypto_address'], 
            float(invoice['crypto_amount'])
        )
        
        log_transaction_event(
            invoice['order_id'], invoice['crypto_address'],
            float(invoice['crypto_amount']), 
            "CHECKED", f"Transaction check result: {tx_check}", "INFO"
        )
        
        if tx_check['confirmed'] and tx_check['confirmations'] >= CONFIRMATIONS_REQUIRED:
            # Обновляем статус транзакции
            await update_transaction_status(invoice['order_id'], 'completed')
            await process_successful_payment(invoice)
            
            log_transaction_event(
                invoice['order_id'], invoice['crypto_address'],
                float(invoice['crypto_amount']), 
                "CONFIRMED", f"Transaction confirmed with {tx_check['confirmations']} confirmations", "INFO"
            )
            
            await callback.message.answer("✅ Оплата подтверждена! Транзакция обрабатывается.")
            
        elif tx_check['unconfirmed']:
            # Транзакция есть в mempool, но еще не подтверждена
            log_transaction_event(
                invoice['order_id'], invoice['crypto_address'],
                float(invoice['crypto_amount']), 
                "UNCONFIRMED", "Transaction is in mempool but not yet confirmed", "INFO"
            )
            
            await callback.message.answer(
                f"⏳ Транзакция получена, но еще не подтверждена сетью. "
                f"Ождаем подтверждений ({tx_check.get('confirmations', 0)}/{CONFIRMATIONS_REQUIRED})"
            )
            
        else:
            # Транзакция не найдена
            log_transaction_event(
                invoice['order_id'], invoice['crypto_address'],
                float(invoice['crypto_amount']), 
                "NOT_FOUND", "Transaction not found in blockchain or mempool", "WARNING"
            )
            
            await callback.message.answer("❌ Транзакция не найдена. Попробуйте позже.")
            
    except Exception as e:
        logger.exception("Error in enhanced invoice check")
        log_transaction_event(
            "unknown", "unknown", 0, 
            "ERROR", f"Exception in invoice check: {str(e)}", "ERROR"
        )
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(F.data == "cancel_invoice")
async def cancel_invoice(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        async with db_connection() as conn:
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending'",
                user_id
            )
            
            await conn.execute(
                "UPDATE transactions SET status = 'cancelled' WHERE user_id = $1 AND status = 'pending'",
                user_id
            )
            
            if invoice and invoice.get('product_id') and "Пополнение баланса" not in invoice['product_info']:
                await release_product(invoice['product_id'])
                logger.info(f"Product {invoice['product_id']} released back to stock")
        
        if user_id in invoice_notifications:
            invoice_notifications[user_id].cancel()
            del invoice_notifications[user_id]
        
        await callback.answer()
        
        try:
            await callback.message.delete()
        except Exception as e:
            logger.exception("Error deleting invoice message")
        
        await callback.message.answer("❌ Инвойс отменен. Товар возвращен в продажу.")
        
        await show_main_menu(callback.message, state, user_id, lang)
        await state.set_state(Form.main_menu)
    except Exception as e:
        logger.exception("Error cancelling invoice")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(F.data == "back_to_topup_menu")
async def back_to_topup_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        try:
            await callback.message.delete()
        except Exception as e:
            logger.exception("Error deleting message")
        
        await show_topup_currency_menu(callback, state)
        await state.set_state(Form.topup_currency)
    except Exception as e:
        logger.exception("Error going back to topup menu")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        text = message.text
        
        if text.isdigit():
            await state.update_data(balance_amount=float(text))
            await process_balance(message, state)
        else:
            await show_main_menu(message, state, user_id, lang)
            await state.set_state(Form.main_menu)
    except Exception as e:
        logger.exception("Error handling text")
        await message.answer("Произошла ошибка. Попробуйте позже.")

def handle_sigterm():
    logger.info("Received SIGTERM signal, shutting down gracefully...")
    # Остановка всех задач и соединений

async def init_litecoinspace_api():
    """Инициализация LitecoinSpace API"""
    # Здесь может быть код инициализации, если он нужен
    logger.info("LitecoinSpace API initialized")

async def close_litecoinspace_api():
    """Завершение работы LitecoinSpace API"""
    # Здесь может быть код завершения, если он нужен
    logger.info("LitecoinSpace API closed")

async def main():
    if not singleton_check():
        logger.error("Another instance of the bot is already running. Exiting.")
        return
    
    try:
        max_retries = 5
        for attempt in range(max_retries):
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                break
            except (TelegramNetworkError, asyncio.TimeoutError) as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed to delete webhook: {e}")
                await asyncio.sleep(5)
        
        await asyncio.sleep(1)
        
        await init_db(DATABASE_URL)
        await load_cache()
        
        # Инициализация LitecoinSpace API
        await init_litecoinspace_api()
        
        # Добавьте в начало main() функции:
        port = os.environ.get('PORT', 8000)
        if port:
            # Создаем простой HTTP-сервер для удовлетворения требований Render
            app = web.Application()
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', int(port))
            await site.start()
            logger.info(f"HTTP server started on port {port}")
        
        # Очистка невалидных адресов при старте
        await cleanup_invalid_addresses()
        
        asyncio.create_task(check_pending_transactions_loop())
        asyncio.create_task(reset_api_limits_loop())
        
        # Запускаем мониторинг неподтвержденных транзакций
        start_deposit_monitoring()
        
        while True:
            try:
                await dp.start_polling(bot)
            except TelegramConflictError:
                logger.error("Bot conflict detected. Waiting 10 seconds before restart...")
                await asyncio.sleep(10)
            except TelegramRetryAfter as e:
                logger.error(f"Rate limit exceeded. Waiting {e.retry_after} seconds...")
                await asyncio.sleep(e.retry_after)
            except asyncio.CancelledError:
                logger.info("Bot task was cancelled")
                break
            except Exception as e:
                logger.exception("Unexpected error")
                await asyncio.sleep(5)
                
    except Exception as e:
        logger.exception("Failed to start bot")
    finally:
        # Завершение работы LitecoinSpace API
        await close_litecoinspace_api()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, handle_sigterm)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
