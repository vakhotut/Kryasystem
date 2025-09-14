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
from datetime import datetime, timedelta
from functools import lru_cache
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InputFile
from aiogram.exceptions import TelegramConflictError, TelegramRetryAfter, TelegramBadRequest, TelegramNetworkError
import aiohttp
import traceback
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

from db import (
    init_db, get_user, update_user, add_transaction, add_purchase, 
    get_pending_transactions, update_transaction_status, update_transaction_status_by_uuid, 
    get_last_order, is_banned, get_text, 
    load_cache, get_user_orders,
    get_cities_cache, get_districts_cache, get_products_cache, get_delivery_types_cache, get_categories_cache,
    has_active_invoice, add_sold_product, get_product_quantity, reserve_product, release_product,
    get_product_by_name_city, get_product_by_id, get_purchase_with_product,
    get_api_limits, increment_api_request, reset_api_limits,
    is_district_available, is_delivery_type_available,
    add_user_referral, generate_referral_code, db_connection, refresh_cache
)
from ltc_hdwallet import ltc_wallet
from api import get_ltc_usd_rate, check_ltc_transaction, get_key_usage_stats

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
bot = Bot(token=TOKEN, timeout=30)  # Увеличенный таймаут
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db_pool = None
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

# Доступные криптовалюты (только LTC)
CRYPTO_CURRENCIES = {
    'LTC': 'Litecoin'
}

# Кеширование для часто используемых данных
@lru_cache(maxsize=100)
def get_cached_text(lang, key, **kwargs):
    return get_text(lang, key, **kwargs)

# Функция для получения настроек (в будущем можно заменить на загрузку из БД)
def get_bot_setting(key):
    return BOT_SETTINGS.get(key, "")

# Функция для генерации капчи в виде изображения
def generate_captcha_image(text):
    # Создаем изображение
    width, height = 200, 100
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    # Используем стандартный шрифт или загружаем свой
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except:
        font = ImageFont.load_default()
    
    # Рисуем текст
    draw.text((10, 10), text, fill=(0, 0, 0), font=font)
    
    # Добавляем немного шума
    for _ in range(100):
        x = random.randint(0, width-1)
        y = random.randint(0, height-1)
        draw.point((x, y), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
    
    # Сохраняем в буфер
    buf = BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    return buf

# Проверка на единственный экземпляр бота
def singleton_check():
    try:
        # Пытаемся занять порт для проверки уникальности экземпляра
        test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_socket.bind(("127.0.0.1", 17891))
        test_socket.close()
        return True
    except socket.error:
        logger.error("Another instance of the bot is already running!")
        return False

# Безопасная отправка сообщений
async def safe_send_message(chat_id, text, reply_markup=None, parse_mode=None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

# Получение языка пользователя
async def get_user_language(user_id):
    user_data = await get_user(user_id)
    return user_data['language'] or 'ru'

# Проверка бана пользователя
async def check_ban(user_id):
    if await is_banned(user_id):
        lang = await get_user_language(user_id)
        await safe_send_message(user_id, get_cached_text(lang, 'ban_message'))
        return True
    return False

# Проверка активных инвойсов
async def check_active_invoice(user_id: int) -> bool:
    return await has_active_invoice(user_id)

# Вспомогательная функция для удаления предыдущего сообщения
async def delete_previous_message(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        # Игнорируем ошибку "message not found"
        if "message to delete not found" not in str(e):
            logger.error(f"Error deleting message: {e}")

# Безопасное удаление предыдущего сообщения с очисткой состояния
async def safe_delete_previous_message(chat_id: int, message_id: int, state: FSMContext):
    if message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            if "message to delete not found" not in str(e):
                logger.error(f"Error deleting message: {e}")
    
    # Очищаем ID сообщения из состояния
    await state.update_data(last_message_id=None)

# Функция для показа меню с изображением [УЛУЧШЕНА ОБРАБОТКА ОШИБОК]
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
                # Fallback to text message
                sent_message = await message.answer(
                    text=caption,
                    reply_markup=keyboard
                )
            else:
                raise
        except Exception as e:
            logger.error(f"Error sending photo: {e}")
            # Fallback to text message
            sent_message = await message.answer(
                text=caption,
                reply_markup=keyboard
            )
        
        await state.update_data(last_message_id=sent_message.message_id)
        return sent_message
    except Exception as e:
        logger.error(f"Error showing menu with image: {e}")
        # Fallback to text message if image fails
        sent_message = await message.answer(
            text=caption,
            reply_markup=keyboard
        )
        await state.update_data(last_message_id=sent_message.message_id)
        return sent_message

# Функция для уведомлений об инвойсе [ДОБАВЛЕНА ОБРАБОТКА ОТМЕНЫ]
async def invoice_notification_loop(user_id: int, order_id: str, lang: str):
    global invoice_notifications
    
    if user_id in invoice_notifications:
        invoice_notifications[user_id].cancel()
        del invoice_notifications[user_id]
    
    async def notify():
        try:
            while True:
                try:
                    async with db_connection() as conn:
                        invoice = await conn.fetchrow(
                            "SELECT * FROM transactions WHERE order_id = $1 AND status = 'pending'",
                            order_id
                        )
                        
                        if not invoice or invoice['expires_at'] <= datetime.now():
                            break
                        
                        time_left = invoice['expires_at'] - datetime.now()
                        minutes_left = int(time_left.total_seconds() // 60)
                        
                        # Отправляем уведомление каждые 5 минут
                        if minutes_left > 0 and minutes_left % 5 == 0:
                            try:
                                if "Пополнение баланса" in invoice['product_info']:
                                    notification_text = get_cached_text(lang, 'balance_invoice_time_left', time_left=f"{minutes_left} минут")
                                else:
                                    notification_text = get_cached_text(lang, 'invoice_time_left', time_left=f"{minutes_left} минут")
                                    
                                await safe_send_message(user_id, notification_text)
                            except Exception as e:
                                logger.error(f"Error sending notification: {e}")
                        
                        # Проверяем каждую минуту
                        await asyncio.sleep(60)
                    
                    # После истечения времени
                    if invoice and invoice['expires_at'] <= datetime.now():
                        async with db_connection() as conn:
                            await conn.execute(
                                "UPDATE transactions SET status = 'expired' WHERE order_id = $1",
                                order_id
                            )
                            
                            # Возвращаем товар, если это покупка
                            if invoice and invoice.get('product_id') and "Пополнение баланса" not in invoice['product_info']:
                                await release_product(invoice['product_id'])
                                logger.info(f"Product {invoice['product_id']} released due to expiration")
                            
                            # Увеличиваем счетчик неудачных попыток только при истечении времени
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
                            
                            await safe_send_message(
                                user_id,
                                get_cached_text(lang, 'invoice_expired', failed_count=new_failed)
                            )
                            
                            if new_failed == 2:
                                await safe_send_message(
                                    user_id,
                                    get_cached_text(lang, 'almost_banned', remaining=1)
                                )
                            elif new_failed >= 3:
                                await safe_send_message(
                                    user_id,
                                    get_cached_text(lang, 'ban_message')
                                )
                        except Exception as e:
                            logger.error(f"Error sending expiration message: {e}")
                        
                        break
                except Exception as e:
                    logger.error(f"Error in invoice notification loop: {e}")
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info(f"Invoice notification task for user {user_id} was cancelled")
        except Exception as e:
            logger.error(f"Error in invoice notification loop: {e}")
        finally:
            # Убедиться, что задача удалена из глобального словаря
            if user_id in invoice_notifications:
                del invoice_notifications[user_id]
    
    # Запускаем задачу и сохраняем ссылку для отмены
    task = asyncio.create_task(notify())
    invoice_notifications[user_id] = task

# Функция для показа меню баланса
async def show_balance_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        balance_text = get_cached_text(lang, 'balance_instructions', balance=user_data['balance'] or 0)
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup_balance"))
        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
        
        # Используем функцию для показа меню с изображением
        await show_menu_with_image(
            callback.message,
            balance_text,
            builder.as_markup(),
            get_bot_setting('balance_menu_image'),
            state
        )
    except Exception as e:
        logger.error(f"Error showing balance menu: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

# Функция для показа меню выбора валюты пополнения
async def show_topup_currency_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        topup_info = get_cached_text(lang, 'balance_topup_info')
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="LTC", callback_data="topup_ltc"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_balance_menu"))
        
        # Используем функцию для показа меню с изображением
        await show_menu_with_image(
            callback.message,
            topup_info,
            builder.as_markup(),
            get_bot_setting('balance_menu_image'),
            state
        )
    except Exception as e:
        logger.error(f"Error showing topup currency menu: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

# Функция для показа активного инвойса
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
            
            # Определяем, это инвойс на покупку или пополнение
            if "Пополнение баланса" in invoice['product_info']:
                text_key = 'active_invoice'
            else:
                text_key = 'purchase_invoice'
            
            payment_text = get_cached_text(
                lang, 
                text_key,
                product=invoice['product_info'],
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
            builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
            
            # Запускаем таймер уведомлений для этого инвойса
            asyncio.create_task(invoice_notification_loop(user_id, invoice['order_id'], lang))
            
            try:
                # Проверяем, является ли payment_url действительным URL изображения
                if invoice['payment_url'] and invoice['payment_url'].startswith('http'):
                    await callback.message.answer_photo(
                        photo=invoice['payment_url'],
                        caption=payment_text,
                        reply_markup=builder.as_markup(),
                        parse_mode='Markdown'
                    )
                else:
                    # Если это не URL, отправляем как текст
                    await callback.message.answer(
                        text=payment_text,
                        reply_markup=builder.as_markup(),
                        parse_mode='Markdown'
                    )
            except Exception as e:
                logger.error(f"Error sending invoice with photo: {e}")
                # Fallback: отправляем только текст
                await callback.message.answer(
                    text=payment_text,
                    reply_markup=builder.as_markup(),
                    parse_mode='Markdown'
                )
    except Exception as e:
        logger.error(f"Error showing active invoice: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

# Функция для получения курса LTC с кешированием на 1 час
async def get_ltc_usd_rate_cached():
    global LAST_RATE_UPDATE
    current_time = time.time()
    
    # Если прошло больше часа, обновляем курс
    if current_time - LAST_RATE_UPDATE > RATE_UPDATE_INTERVAL:
        rate = await get_ltc_usd_rate()
        LAST_RATE_UPDATE = current_time
        return rate
    
    # Используем кешированный курс
    from api import get_cached_rate
    cached_rate, from_cache = await get_cached_rate()
    if from_cache:
        return cached_rate
    
    # Если кеш пуст, получаем новый курс
    rate = await get_ltc_usd_rate()
    LAST_RATE_UPDATE = current_time
    return rate

# Поток для проверки pending транзакций [УЛУЧШЕНА ОБРАБОТКА ОШИБОК]
async def check_pending_transactions_loop():
    while True:
        try:
            # Получаем все pending транзакции
            transactions = await get_pending_transactions()
            
            for transaction in transactions:
                created_at = transaction['created_at']
                # Проверяем, прошло ли 10 минут с момента создания
                if (datetime.now() - created_at).total_seconds() >= TRANSACTION_CHECK_DELAY:
                    # Проверяем транзакцию через API
                    is_paid = await check_ltc_transaction(
                        transaction['crypto_address'],
                        float(transaction['crypto_amount'])
                    )
                    
                    if is_paid:
                        # Обновляем статус транзакции
                        await update_transaction_status(transaction['order_id'], 'completed')
                        
                        # Обрабатываем успешную оплату
                        await process_successful_payment(transaction)
            
            await asyncio.sleep(60)  # Проверяем каждую минуту
        except Exception as e:
            logger.error(f"Error in check_pending_transactions: {e}")
            await asyncio.sleep(60)

# Функция для обработки успешной оплаты
async def process_successful_payment(transaction):
    try:
        user_id = transaction['user_id']
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        # Если это покупка, добавляем в истории
        if "Пополнение баланса" not in transaction['product_info']:
            # Извлекаем информацию о покупке из product_info
            # Формат: "Товар в городе, район район, тип доставки"
            parts = transaction['product_info'].split(', ')
            if len(parts) >= 3:
                product = parts[0]
                district = parts[1].replace('район ', '')
                delivery_type = parts[2]
                
                # Получаем product_id из transaction
                product_id = transaction.get('product_id')
                
                # Получаем информацию о товаре для сохранения в purchase
                product_info = None
                if product_id:
                    product_info = await get_product_by_id(product_id)
                
                # Добавляем покупку
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
                    # Добавляем запись о проданном товаре
                    await add_sold_product(
                        product_id, 
                        product_info['subcategory_id'], 
                        user_id, 
                        1, 
                        transaction['amount'], 
                        purchase_id
                    )
                    
                    # Получаем информацию о товаре для отправки
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
        
        # Если это пополнение баланса, обновляем баланс
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
        logger.error(f"Error processing successful payment: {e}")

# Поток для сброса API лимитов [УЛУЧШЕНА ОБРАБОТКА ОШИБОК]
async def reset_api_limits_loop():
    while True:
        try:
            # Сбрасываем лимиты каждый день
            await reset_api_limits()
            await asyncio.sleep(86400)  # 24 часа
        except Exception as e:
            logger.error(f"Error resetting API limits: {e}")
            await asyncio.sleep(3600)  # Повторяем через час при ошибке

# Обработчики команд и состояний
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    try:
        await state.clear()
        
        user = message.from_user
        user_id = user.id
        
        if await check_ban(user_id):
            return
        
        # Проверяем есть ли реферальный код в параметрах
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
            # Добавляем реферала если это новый пользователь
            if referrer_code:
                await add_user_referral(user_id, referrer_code)
        
        # Сначала показываем выбор языка
        builder = InlineKeyboardBuilder()
        builder.add(
            InlineKeyboardButton(text="Русский", callback_data='lang_ru'),
            InlineKeyboardButton(text="English", callback_data='lang_en'),
            InlineKeyboardButton(text="ქართული", callback_data='lang_ka')
        )
        builder.adjust(1)
        
        await message.answer('Выберите язык / Select language / აირჩიეთ ენა:', reply_markup=builder.as_markup())
        await state.set_state(Form.language)
    except Exception as e:
        logger.error(f"Error in start command: {e}")
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
        # Отправляем новое сообщение вместо редактирования
        await callback.message.answer(text=get_cached_text(lang_code, 'language_selected'))
        
        # Генерируем капчу
        captcha_code = ''.join(random.choices('0123456789', k=5))
        await state.update_data(captcha=captcha_code)
        
        # Генерируем изображение капчи
        captcha_image = generate_captcha_image(captcha_code)
        
        # Отправляем изображение капчи
        try:
            # Создаем InputFile из BytesIO
            input_file = InputFile(captcha_image, filename="captcha.png")
            await callback.message.answer_photo(
                photo=input_file,
                caption=get_cached_text(lang_code, 'captcha_enter')
            )
        except Exception as e:
            logger.error(f"Error sending captcha image: {e}")
            # Fallback: отправляем капчу текстом
            await callback.message.answer(
                text=f"{get_cached_text(lang_code, 'captcha_enter')}\n\nКод: {captcha_code}"
            )
        
        await state.set_state(Form.captcha)
    except Exception as e:
        logger.error(f"Error processing language: {e}")
        logger.error(traceback.format_exc())
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
        logger.error(f"Error processing captcha: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

async def show_main_menu(message: types.Message, state: FSMContext, user_id: int, lang: str):
    try:
        user = await get_user(user_id)
        if not user:
            return
        
        if await check_ban(user_id):
            return
            
        # Генерируем реферальный код если его нет
        if not user.get('referral_code'):
            referral_code = await generate_referral_code(user_id)
        else:
            referral_code = user['referral_code']
        
        # Формируем реферальную ссылку
        bot_username = (await bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={referral_code}"
        
        # Используем новый текст описания магазина
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
        
        # Добавляем информацию о рефералах
        referral_info = f"\n👥 Приглашено друзей: {user.get('referral_count', 0)}"
        referral_info += f"\n💰 Заработано с рефералов: ${user.get('earned_from_referrals', 0)}"
        referral_info += f"\n🔗 Реферальная ссылка: {referral_link}"
        
        full_text = shop_description + user_info_text + referral_info
        
        builder = InlineKeyboardBuilder()
        cities = get_cities_cache()
        for city in cities:
            builder.row(InlineKeyboardButton(text=city['name'], callback_data=f"city_{city['name']}"))
        builder.row(
            InlineKeyboardButton(text=f"💰 {get_cached_text(lang, 'balance', balance=user['balance'] or 0)}", callback_data="balance"),
            InlineKeyboardButton(text="📦 История заказов", callback_data="order_history")
        )
        
        # Добавляем кнопки с ссылками
        builder.row(
            InlineKeyboardButton(text="🎁 Бонусы", callback_data="bonuses"),
            InlineKeyboardButton(text="📚 Правила", url=get_bot_setting('rules_link'))
        )
        builder.row(
            InlineKeyboardButton(text="👨‍💻 Оператор", url=get_bot_setting('operator_link')),
            InlineKeyboardButton(text="🔧 Техподдержка", url=get_bot_setting('support_link'))
        )
        builder.row(InlineKeyboardButton(text="📢 Наш канал", url=get_bot_setting('channel_link')))
        builder.row(InlineKeyboardButton(text="⭐ Отзывы", url=get_bot_setting('reviews_link')))
        builder.row(InlineKeyboardButton(text="🌐 Наш сайт", url=get_bot_setting('website_link')))
        builder.row(InlineKeyboardButton(text="🌐 Смена языка", callback_data="change_language"))
        
        # Используем функцию для показа меню с изображением
        await show_menu_with_image(
            message,
            full_text,
            builder.as_markup(),
            get_bot_setting('main_menu_image'),
            state
        )
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
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
        
        # Проверяем есть ли активный инвойс
        if await check_active_invoice(user_id) and data.startswith('city_'):
            # Показываем экран с инвойсом вместо перехода к выбору города
            await show_active_invoice(callback, state, user_id, lang)
            return
        
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        if data.startswith('city_'):
            city = data.replace('city_', '')
            
            # Проверяем есть ли товары в этом городе
            products_cache = get_products_cache()
            if city not in products_cache or not any(product_info.get('quantity', 0) > 0 for product_info in products_cache[city].values()):
                await callback.message.answer(
                    "🛒 Этот город пока пустой. Ожидайте пополнения. Следите за нашим каналом в ожидании пополнения."
                )
                return
            
            await state.update_data(city=city)
            
            # Получаем актуальные кэши
            categories_cache = get_categories_cache()
            
            builder = InlineKeyboardBuilder()
            for category in categories_cache:
                builder.row(InlineKeyboardButton(text=category['name'], callback_data=f"cat_{category['name']}"))
            builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
            
            # Используем функцию для показа меню с изображением
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_category'),
                builder.as_markup(),
                get_bot_setting('category_menu_image'),
                state
            )
            await state.set_state(Form.category)
        elif data == 'balance':
            await show_balance_menu(callback, state)
            await state.set_state(Form.balance_menu)
        elif data == 'order_history':
            await show_order_history(callback, state)
        elif data == 'bonuses':
            sent_message = await callback.message.answer(
                text=get_cached_text(lang, 'bonuses')
            )
            await state.update_data(last_message_id=sent_message.message_id)
        elif data == 'rules':
            # Открываем ссылку на правила
            await callback.message.answer("Переходим к правилам...")
        elif data == 'operator' or data == 'support':
            # Открываем ссылку на оператора/поддержку
            await callback.message.answer("Связываемся с оператором...")
        elif data == 'channel':
            # Открываем ссылку на канал
            await callback.message.answer("Переходим в канал...")
        elif data == 'reviews':
            # Открываем ссылку на отзывы
            await callback.message.answer("Переходим к отзываы...")
        elif data == 'website':
            # Открываем ссылку на сайт
            await callback.message.answer("Переходим на сайт...")
        elif data == 'change_language':
            # Показываем выбор языка
            builder = InlineKeyboardBuilder()
            builder.add(
                InlineKeyboardButton(text="Русский", callback_data='lang_ru'),
                InlineKeyboardButton(text="English", callback_data='lang_en'),
                InlineKeyboardButton(text="ქართული", callback_data='lang_ka')
            )
            builder.adjust(1)
            
            await callback.message.answer('Выберите язык / Select language / აირჩიეთ ენა:', reply_markup=builder.as_markup())
            await state.set_state(Form.language)
        elif data == 'main_menu':
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
    except Exception as e:
        logger.error(f"Error processing main menu: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

# Новая функция для отображения истории заказов
@dp.callback_query(F.data == "order_history")
async def show_order_history(callback: types.CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        # Получаем историю заказов
        orders = await get_user_orders(user_id, 15)  # Ограничим 15 последними заказами
        
        if not orders:
            await callback.answer(get_cached_text(lang, 'no_orders'))
            return
            
        builder = InlineKeyboardBuilder()
        
        # Создаем кнопки для каждого заказа
        for order in orders:
            # Форматируем дату и время
            order_time = order['purchase_time'].strftime("%d.%m %H:%M")
            
            # Сокращаем название товара если слишком длинное
            product_name = order['product']
            if len(product_name) > 15:
                product_name = product_name[:12] + "..."
            
            # Формируем текст кнопки
            btn_text = f"{order_time} - {product_name} - {order['price']}$"
            
            builder.row(InlineKeyboardButton(
                text=btn_text, 
                callback_data=f"view_order_{order['id']}"
            ))
        
        # Добавляем кнопку возврата
        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
        
        # Удаляем предыдущее сообщение если есть
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(user_id, state_data['last_message_id'], state)
        
        # Отправляем сообщение с кнопками заказов
        sent_message = await callback.message.answer(
            text="📋 История ваших заказов:",
            reply_markup=builder.as_markup()
        )
        
        await state.update_data(last_message_id=sent_message.message_id)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error showing order history: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

# Обработчик для просмотра деталей заказа
@dp.callback_query(F.data.startswith("view_order_"))
async def view_order_details(callback: types.CallbackQuery, state: FSMContext):
    try:
        order_id = int(callback.data.replace("view_order_", ""))
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        # Получаем полную информацию о заказе с данными о продукте
        async with db_connection() as conn:
            order = await conn.fetchrow('''
                SELECT p.*, pr.description as product_description, 
                       pr.image_url as product_image, c.name as city_name
                FROM purchases p
                LEFT JOIN products pr ON p.product_id::integer = pr.id
                LEFT JOIN cities c ON pr.city_id = c.id
                WHERE p.id = $1
            ''', order_id)
        
        if not order:
            await callback.answer("Заказ не найден")
            return
            
        # Форматируем дату и время
        order_time = order['purchase_time'].strftime("%d.%m.%Y %H:%M:%S")
        
        # Формируем текст с полной информацией о заказе
        order_text = (
            f"🆔 <b>ID заказа:</b> {order['id']}\n"
            f"📦 <b>Товар:</b> {order['product']}\n"
            f"💵 <b>Цена:</b> {order['price']}$\n"
            f"🏙 <b>Город:</b> {order.get('city_name', 'Не указан')}\n"
            f"📍 <b>Район:</b> {order['district']}\n"
            f"🚚 <b>Тип доставки:</b> {order['delivery_type']}\n"
            f"📝 <b>Описание:</b> {order.get('product_description', 'Нет описания')}\n"
            f"🕐 <b>Время заказа:</b> {order_time}\n"
            f"📊 <b>Статус:</b> {order['status']}"
        )
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="⬅️ Назад к истории", callback_data="order_history"))
        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
        
        # Удаляем предыдущее сообщение
        state_data = await state.get_data()
        if 'last_message_id' in state_data:
            await safe_delete_previous_message(callback.message.chat.id, state_data['last_message_id'], state)
        
        # Если есть изображение товара, отправляем его
        if order.get('product_image'):
            try:
                sent_message = await callback.message.answer_photo(
                    photo=order['product_image'],
                    caption=order_text,
                    reply_markup=builder.as_markup(),
                    parse_mode='HTML'
                )
                await state.update_data(last_message_id=sent_message.message_id)
            except Exception as e:
                logger.error(f"Error sending photo: {e}")
                # Fallback - отправляем только текст
                sent_message = await callback.message.answer(
                    text=order_text,
                    reply_markup=builder.as_markup(),
                    parse_mode='HTML'
                )
                await state.update_data(last_message_id=sent_message.message_id)
        else:
            sent_message = await callback.message.answer(
                text=order_text,
                reply_markup=builder.as_markup(),
                parse_mode='HTML'
            )
            await state.update_data(last_message_id=sent_message.message_id)
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in view order handler: {e}")
        await callback.answer("Произошла ошибка при получении информации о заказе")

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
            await show_topup_currency_menu(callback, state)
            await state.set_state(Form.topup_currency)
        elif data == 'main_menu':
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
    except Exception as e:
        logger.error(f"Error processing balance menu: {e}")
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
            await state.update_data(topup_currency='LTC')
            
            await callback.message.answer(get_cached_text(lang, 'balance_add'))
            await state.set_state(Form.balance)
    except Exception as e:
        logger.error(f"Error processing topup currency: {e}")
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
        
        # Получаем актуальные кэши
        products_cache = get_products_cache()
        
        if city not in products_cache:
            sent_message = await callback.message.answer(
                text=get_cached_text(lang, 'error')
            )
            await state.update_data(last_message_id=sent_message.message_id)
            return
        
        # Фильтруем товары по категории и наличию
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
        
        builder = InlineKeyboardBuilder()
        for product_name in category_products.keys():
            price = category_products[product_name]['price']
            builder.row(InlineKeyboardButton(text=f"{product_name} - ${price}", callback_data=f"prod_{product_name}"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_city"))
        
        # Используем функцию для показа меню с изображением
        await show_menu_with_image(
            callback.message,
            "Выберите товар:",
            builder.as_markup(),
            get_bot_setting('category_menu_image'),
            state
        )
        await state.set_state(Form.district)
    except Exception as e:
        logger.error(f"Error processing category: {e}")
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
            
            # Получаем актуальные кэши
            categories_cache = get_categories_cache()
            
            builder = InlineKeyboardBuilder()
            for category in categories_cache:
                builder.row(InlineKeyboardButton(text=category['name'], callback_data=f"cat_{category['name']}"))
            builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
            
            # Используем функцию для показа меню с изображением
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_category'),
                builder.as_markup(),
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
            
            # Получаем актуальные кэши
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
            
            # Получаем доступные районы для этого города
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
            
            builder = InlineKeyboardBuilder()
            for district in districts:
                builder.row(InlineKeyboardButton(text=district, callback_data=f"dist_{district}"))
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_category"))
            
                       # Используем функцию для показа меню с изображением
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_delivery'),
                builder.as_markup(),
                get_bot_setting('delivery_menu_image'),
                state
            )
            await state.set_state(Form.delivery)
    except Exception as e:
        logger.error(f"Error processing district: {e}")
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
            
            # Получаем доступные районы для этого города
            districts = []
            for district in get_districts_cache().get(city, []):
                if await is_district_available(city, district):
                    districts.append(district)
            
            builder = InlineKeyboardBuilder()
            for district in districts:
                builder.row(InlineKeyboardButton(text=district, callback_data=f"dist_{district}"))
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_category"))
            
            # Используем функцию для показа меню с изображением
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_district'),
                builder.as_markup(),
                get_bot_setting('district_menu_image'),
                state
            )
            await state.set_state(Form.district)
            return
        
        delivery_type = data.replace('del_', '')
        
        # ИСПРАВЛЕННАЯ СТРОКА: используем delivery_type вместо del_type
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
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes"))
        builder.row(InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_delivery"))
        
        # Используем функцию для показа меню с изображением
        await show_menu_with_image(
            callback.message,
            order_text,
            builder.as_markup(),
            get_bot_setting('confirmation_menu_image'),
            state
        )
        await state.set_state(Form.confirmation)
    except Exception as e:
        logger.error(f"Error processing delivery: {e}")
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
            # Получаем доступные типы доставки
            delivery_types = []
            for del_type in get_delivery_types_cache():
                if await is_delivery_type_available(del_type):
                    delivery_types.append(del_type)
            
            builder = InlineKeyboardBuilder()
            for del_type in delivery_types:
                builder.row(InlineKeyboardButton(text=del_type, callback_data=f"del_{del_type}"))
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_district"))
            
            # Используем функцию для показа меню с изображением
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_delivery'),
                builder.as_markup(),
                get_bot_setting('delivery_menu_image'),
                state
            )
            await state.set_state(Form.delivery)
            return
        
        if data == 'confirm_yes':
            # Получаем данные о заказе
            state_data = await state.get_data()
            city = state_data.get('city')
            product_name = state_data.get('product')
            price = state_data.get('price')
            district = state_data.get('district')
            delivery_type = state_data.get('delivery_type')
            
            product_info = f"{product_name} в {city}, район {district}, {delivery_type}"
            
            # Проверяем баланс пользователя
            user_balance = user_data['balance'] or 0
            
            builder = InlineKeyboardBuilder()
            
            # Добавляем кнопку оплаты балансом, если средств достаточно
            if user_balance >= price:
                builder.row(InlineKeyboardButton(
                    text=f"💰 Оплатить балансом (${user_balance})", 
                    callback_data="pay_with_balance"
                ))
            
            # Добавляем кнопки криптовалют
            builder.row(InlineKeyboardButton(text="LTC", callback_data="crypto_LTC"))
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_confirmation"))
            
            # Используем функцию для показа меню с изображением
            await show_menu_with_image(
                callback.message,
                get_cached_text(lang, 'select_crypto'),
                builder.as_markup(),
                get_bot_setting('confirmation_menu_image'),
            state
            )
            await state.set_state(Form.crypto_currency)
        else:
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
    except Exception as e:
        logger.error(f"Error processing confirmation: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

# Новая функция для обработки оплаты балансом
@dp.callback_query(F.data == "pay_with_balance")
async def pay_with_balance(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        
        user_id = callback.from_user.id
        
        if await check_ban(user_id):
            return
            
        user_data = await get_user(user_id)
        lang = user_data['language'] or 'ru'
        
        # Получаем данные о заказе
        state_data = await state.get_data()
        city = state_data.get('city')
        product_name = state_data.get('product')
        price = state_data.get('price')
        district = state_data.get('district')
        delivery_type = state_data.get('delivery_type')
        
        # Проверяем достаточно ли средств на балансе
        if (user_data['balance'] or 0) < price:
            await callback.message.answer("Недостаточно средств на балансе")
            return
        
        # Получаем информацию о товаре
        async with db_connection() as conn:
            product_row = await conn.fetchrow(
                "SELECT * FROM products WHERE name = $1 AND city_id = (SELECT id FROM cities WHERE name = $2) LIMIT 1",
                product_name, city
            )
            
            if not product_row:
                await callback.message.answer("Ошибка: товар не найден")
                return
            
            # Проверяем доступное количество
            if product_row['quantity'] <= 0:
                await callback.message.answer(get_cached_text(lang, 'product_out_of_stock'))
                return

            # Бронируем товар
            if not await reserve_product(product_row['id']):
                await callback.message.answer(get_cached_text(lang, 'product_out_of_stock'))
                return

            product_id = product_row['id']
        
        try:
            # Списание средств
            async with db_connection() as conn:
                await conn.execute(
                    "UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                    price, user_id
                )
                
                # Добавляем покупку
                purchase_id = await add_purchase(
                    user_id, product_name, price, district, delivery_type,
                    product_id, product_row['image_url'], product_row['description']
                )
                
                if purchase_id:
                    # Добавляем запись о проданном товаре
                    await add_sold_product(
                        product_row['id'], 
                        product_row['subcategory_id'],  # Добавлен subcategory_id
                        user_id, 
                        1, 
                        price, 
                        purchase_id
                    )
            
            # Уведомляем пользователя
            await callback.message.answer(
                f"✅ Оплата прошла успешно! Товар {product_name} будет доставлен."
            )
            
            # Отправляем фото и описание товара
            if product_row['image_url']:
                caption = f"{product_row['name']}\n\n{product_row['description']}\n\nЦена: ${price}"
                await callback.message.answer_photo(
                    photo=product_row['image_url'],
                    caption=caption
                )
            else:
                await callback.message.answer(
                    f"{product_row['name']}\n\n{product_row['description']}\n\nЦена: ${price}"
                )
            
            # Возвращаем в главное меню
            await show_main_menu(callback.message, state, user_id, lang)
            await state.set_state(Form.main_menu)
            
        except Exception as e:
            # В случае ошибки возвращаем товар
            await release_product(product_row['id'])
            logger.error(f"Error in pay_with_balance: {e}")
            await callback.answer("Произошла ошибка. Попробуйте позже.")
        
    except Exception as e:
        logger.error(f"Error in pay_with_balance: {e}")
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
            
            order_text = get_cached_text(
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
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_delivery"))
            
            # Используем функцию для показа меню с изображением
            await show_menu_with_image(
                callback.message,
                order_text,
                builder.as_markup(),
                get_bot_setting('confirmation_menu_image'),
                state
            )
            await state.set_state(Form.confirmation)
            return
        
        # Для LTC
        if data == 'crypto_LTC':
            state_data = await state.get_data()
            city = state_data.get('city')
            product_name = state_data.get('product')
            price = state_data.get('price')
            district = state_data.get('district')
            delivery_type = state_data.get('delivery_type')
            
            product_info = f"{product_name} в {city}, район {district}, {delivery_type}"
            
            order_id = f"order_{int(time.time())}_{user_id}"
            ltc_rate = await get_ltc_usd_rate_cached()
            amount_ltc = price / ltc_rate
            
            # Получаем product_id для добавления в sold_products после оплаты
            async with db_connection() as conn:
                product_row = await conn.fetchrow(
                    "SELECT * FROM products WHERE name = $1 AND city_id = (SELECT id FROM cities WHERE name = $2) LIMIT 1",
                    product_name, city
                )
                
                if not product_row:
                    await callback.message.answer("Ошибка: товар не найден")
                    return
                
                # Проверяем доступное количество
                if product_row['quantity'] <= 0:
                    await callback.message.answer(get_cached_text(lang, 'product_out_of_stock'))
                    return

                # Бронируем товар
                if not await reserve_product(product_row['id']):
                    await callback.message.answer(get_cached_text(lang, 'product_out_of_stock'))
                    return

                product_id = product_row['id']
            
            try:
                address_data = ltc_wallet.generate_address()
            except Exception as e:
                logger.error(f"Error generating LTC address: {e}")
                await callback.message.answer(get_cached_text(lang, 'error'))
                return
            
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
                order_id,
                address_data['address'],
                amount_ltc,
                product_id
            )
            
            # Сохраняем product_id в state для использования после оплата
            await state.update_data(product_id=product_id)
            
            # Новый формат текста для покупки
            expires_time = expires_at.strftime("%d.%m.%Y, %H:%M:%S")
            time_left = expires_at - datetime.now()
            time_left_str = f"{int(time_left.total_seconds() // 60)} мин {int(time_left.total_seconds() % 60)} сек"
            
            payment_text = get_cached_text(
                lang,
                'purchase_invoice',
                product=product_name,
                crypto_address=address_data['address'],
                crypto_amount=round(amount_ltc, 8),
                amount=price,
                expires_time=expires_time,
                time_left=time_left_str
            )
            
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_invoice"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_invoice")
            )
            
            try:
                await callback.message.answer_photo(
                    photo=qr_code,
                    caption=payment_text,
                    reply_markup=builder.as_markup(),
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Error sending QR code: {e}")
                await callback.message.answer(
                    text=payment_text,
                    reply_markup=builder.as_markup(),
                    parse_mode='Markdown'
                )
            
            # Запускаем отложенную проверку через 10 минут
            await asyncio.sleep(TRANSACTION_CHECK_DELAY)
            asyncio.create_task(check_invoice_after_delay(order_id, user_id, lang))
            
            asyncio.create_task(invoice_notification_loop(user_id, order_id, lang))
            await state.set_state(Form.payment)
        else:
            await callback.message.answer("Currently only LTC is supported")
    except Exception as e:
        logger.error(f"Error processing crypto currency: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

# Функция для отложенной проверки инвойса
async def check_invoice_after_delay(order_id, user_id, lang):
    """Проверяет инвойс через 10 минут после создания"""
    await asyncio.sleep(TRANSACTION_CHECK_DELAY)
    
    # Получаем информацию о транзакции
    async with db_connection() as conn:
        invoice = await conn.fetchrow(
            "SELECT * FROM transactions WHERE order_id = $1",
            order_id
        )
    
    if invoice and invoice['status'] == 'pending':
        # Проверяем оплату через API
        is_paid = await check_ltc_transaction(
            invoice['crypto_address'],
            float(invoice['crypto_amount'])
        )
        
        if is_paid:
            await update_transaction_status(order_id, 'completed')
            await process_successful_payment(invoice)
        else:
            # Если оплаты нет, отправляем уведомление пользователю
            try:
                await bot.send_message(
                    user_id,
                    "⏰ Время оплаты истекло. Если вы уже отправили средства, они будут зачислены после подтверждения сети."
                )
            except Exception as e:
                logger.error(f"Error sending delay notification: {e}")

@dp.message(Form.balance)
async def process_balance(message: types.Message, state: FSMContext):
    try:
        user = message.from_user
        
        if await check_ban(user.id):
            return
            
        user_data = await get_user(user.id)
        lang = user_data['language'] or 'ru'
        amount_text = message.text
        
        try:
            amount = float(amount_text)
            if amount <= 0:
                await message.answer(get_cached_text(lang, 'error'))
                return
            
            # Получаем текущий курс LTC с кеширования
            ltc_rate = await get_ltc_usd_rate_cached()
            
            # Конвертируем USD в LTC
            amount_ltc = amount / ltc_rate
            
            # Генерируем новый LTC адрес
            try:
                address_data = ltc_wallet.generate_address()
            except Exception as e:
                logger.error(f"Error generating LTC address: {e}")
                await message.answer(get_cached_text(lang, 'error'))
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
                amount_ltc
            )
            
            # Форматируем время истечения
            expires_str = expires_at.strftime("%d.%m.%Y, %H:%M:%S")
            time_left = expires_at - datetime.now()
            time_left_str = f"{int(time_left.total_seconds() // 60)} мин {int(time_left.total_seconds() % 60)} сек"
            
            payment_text = get_cached_text(
                lang,
                'active_invoice',
                crypto_address=address_data['address'],
                crypto_amount=round(amount_ltc, 8),
                amount=amount,
                expires_time=expires_str,
                time_left=time_left_str
            )
            
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="✅ Проверить", callback_data="check_invoice"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_invoice")
            )
            builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_topup_menu"))
            
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
            
            # Запускаем отложенную проверку через 10 минут
            asyncio.create_task(check_invoice_after_delay(order_id, user.id, lang))
                
        except ValueError:
            await message.answer(get_cached_text(lang, 'error'))
    except Exception as e:
        logger.error(f"Error processing balance: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

# Обработчики для кнопок инвойса
@dp.callback_query(F.data == "check_invoice")
async def check_invoice(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer("Проверка оплаты... Пожалуйста, подождите")
        
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
        
        if invoice:
            # Используем API для проверки транзакции
            is_paid = await check_ltc_transaction(
                invoice['crypto_address'],
                float(invoice['crypto_amount'])
            )
            
            if is_paid:
                await update_transaction_status(invoice['order_id'], 'completed')
                await callback.message.answer("✅ Оплата подтверждена! Транзакция обрабатывается.")
                
                # Обрабатываем успешную оплату
                await process_successful_payment(invoice)
            else:
                await callback.message.answer("❌ Оплата еще не получена. Попробуйте позже.")
        else:
            await callback.message.answer("❌ Активный инвойс не найден")
            
    except Exception as e:
        logger.error(f"Error checking invoice: {e}")
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
            # Получаем информацию о транзакции для возврата товара
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE user_id = $1 AND status = 'pending'",
                user_id
            )
            
            # Обновляем статус транзакции (только отмена, без увеличения счетчика попыток)
            await conn.execute(
                "UPDATE transactions SET status = 'cancelled' WHERE user_id = $1 AND status = 'pending'",
                user_id
            )
            
            # Возвращаем товар если это покупка
            if invoice and invoice.get('product_id') and "Пополнение баланса" not in invoice['product_info']:
                await release_product(invoice['product_id'])
                logger.info(f"Product {invoice['product_id']} released back to stock")
        
        # Отменяем уведомления
        if user_id in invoice_notifications:
            invoice_notifications[user_id].cancel()
            del invoice_notifications[user_id]
        
        await callback.answer()
        
        # Удаляем сообщение с инвойсом (может содержать фото)
        try:
            await callback.message.delete()
        except Exception as e:
            logger.error(f"Error deleting invoice message: {e}")
        
        # Отправляем новое текстовое сообщение
        await callback.message.answer("❌ Инвойс отменен. Товар возвращен в продажу.")
        
        await show_main_menu(callback.message, state, user_id, lang)
        await state.set_state(Form.main_menu)
    except Exception as e:
        logger.error(f"Error cancelling invoice: {e}")
        await callback.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(F.data == "back_to_topup_menu")
async def back_to_topup_menu(callback: types.CallbackQuery, state: FSMContext):
    try:
        # Удаляем текущее сообщение
        try:
            await callback.message.delete()
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
        
        await show_topup_currency_menu(callback, state)
        await state.set_state(Form.topup_currency)
    except Exception as e:
        logger.error(f"Error going back to topup menu: {e}")
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
            # Создаем новую задачу для показа меню вместо прямого вызова
            asyncio.create_task(show_main_menu(message, state, user_id, lang))
            await state.set_state(Form.main_menu)
    except Exception as e:
        logger.error(f"Error handling text: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

async def main():
    # Проверка на единственный экземпляр
    if not singleton_check():
        logger.error("Another instance is already running. Exiting.")
        return
    
    global db_pool
    
    try:
        # Увеличиваем количество повторных попыток для удаления вебхука
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
        
        # Инициализируем базу данных
        db_pool = await init_db(DATABASE_URL)
        
        # Принудительно загружаем кэш после инициализации БД
        await load_cache()
        
        # Запускаем проверку pending транзакций в фоне
        asyncio.create_task(check_pending_transactions_loop())
        
        # Запускаем сброс API лимитов
        asyncio.create_task(reset_api_limits_loop())
        
        # Запускаем бота с обработкой ошибок
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
                logger.error(f"Unexpected error: {e}. Restarting in 5 seconds...")
                logger.error(traceback.format_exc())
                await asyncio.sleep(5)
                
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        logger.error(traceback.format_exc())
    finally:
        if db_pool:
            await db_pool.close()

if __name__ == "__main__":
    asyncio.run(main())

