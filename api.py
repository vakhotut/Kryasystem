# api.py
import aiohttp
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import time
from db import db_connection, update_user, add_generated_address, update_address_balance
import base58
import hashlib
import re

logger = logging.getLogger(__name__)

# Конфигурация API
PRIMARY_API_URL = "https://ltc.bitaps.com"
FALLBACK_API_URL = "https://api.litecoinspace.org"
LTC_NETWORK = "mainnet"
CONFIRMATIONS_REQUIRED = 3  # Требуемое количество подтверждений

# Кэш для хранения данных о транзакциях
transaction_cache = {}
address_cache = {}

# Конфигурация логирования
payment_logger = logging.getLogger('payment_system')
payment_logger.setLevel(logging.INFO)

# Создаем файловый обработчик для детального лога
file_handler = logging.FileHandler('payment_detailed.log')
file_handler.setLevel(logging.INFO)

# Создаем форматтер с структурированными данными
detailed_formatter = logging.Formatter(
    '%(asctime)s|%(levelname)s|%(message)s|%(transaction_id)s|%(address)s|%(amount)s|%(status)s'
)
file_handler.setFormatter(detailed_formatter)
payment_logger.addHandler(file_handler)

def log_transaction_event(transaction_id: str, address: str, amount: float, 
                         status: str, message: str, level: str = 'INFO'):
    """Структурированное логирование событий транзакций"""
    extra = {
        'transaction_id': transaction_id or 'N/A',
        'address': address or 'N/A',
        'amount': amount or 0.0,
        'status': status or 'UNKNOWN'
    }
    
    if level == 'INFO':
        payment_logger.info(f"{message} [TX: {transaction_id}]", extra=extra)
    elif level == 'WARNING':
        payment_logger.warning(f"{message} [TX: {transaction_id}]", extra=extra)
    elif level == 'ERROR':
        payment_logger.error(f"{message} [TX: {transaction_id}]", extra=extra)
    elif level == 'DEBUG':
        payment_logger.debug(f"{message} [TX: {transaction_id}]", extra=extra)

def log_address_validation(address: str, is_valid: bool, context: str = ''):
    """Логирование валидации адресов"""
    status = "VALID" if is_valid else "INVALID"
    extra = {
        'transaction_id': 'N/A',
        'address': address,
        'amount': 0.0,
        'status': status
    }
    payment_logger.info(
        f"Address validation: {status} - {address} {context}",
        extra=extra
    )

def log_api_request(api_name: str, success: bool, response_time: float, 
                   details: str = ''):
    """Логирование запросов к API"""
    status = "SUCCESS" if success else "FAILED"
    extra = {
        'transaction_id': 'N/A',
        'address': 'N/A',
        'amount': 0.0,
        'status': status
    }
    payment_logger.info(
        f"API {api_name} request {status} - {response_time:.2f}ms {details}",
        extra=extra
    )

def validate_ltc_address(address: str) -> bool:
    """
    Валидация Litecoin адресов различных форматов:
    - Bech32 (ltc1...)
    - P2SH (M...)
    - P2PKH (L...)
    - Legacy (3...)
    """
    # Bech32 адреса (начинаются с ltc1)
    if address.startswith('ltc1'):
        if not (40 <= len(address) <= 62):
            return False
        if not re.match(r'^ltc1[ac-hj-np-z02-9]+$', address.lower()):
            return False
        return True
    
    # P2SH адреса (начинаются с M)
    elif address.startswith('M'):
        return validate_base58_address(address, 'M')
    
    # P2PKH адреса (начинаются с L)
    elif address.startswith('L'):
        return validate_base58_address(address, 'L')
    
    # Legacy адреса (начинаются с 3)
    elif address.startswith('3'):
        return validate_base58_address(address, '3')
    
    return False

def validate_base58_address(address: str, expected_prefix: str) -> bool:
    """Валидация Base58 адресов с проверкой контрольной суммы"""
    try:
        if not address.startswith(expected_prefix):
            return False
        
        decoded = base58.b58decode(address)
        
        if len(decoded) != 25:
            return False
        
        payload = decoded[:-4]
        checksum = decoded[-4:]
        
        first_sha = hashlib.sha256(payload).digest()
        second_sha = hashlib.sha256(first_sha).digest()
        calculated_checksum = second_sha[:4]
        
        return checksum == calculated_checksum
        
    except Exception:
        return False

async def get_ltc_usd_rate() -> float:
    """Получение курса LTC через BitAPS"""
    try:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{PRIMARY_API_URL}/market/ticker") as response:
                if response.status == 200:
                    data = await response.json()
                    rate = float(data['data']['last'])
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('bitaps_rate', True, response_time, f"Rate: {rate}")
                    return rate
    except Exception as e:
        logger.error(f"BitAPS rate error: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('bitaps_rate', False, response_time, f"Exception: {str(e)}")
        
        # Fallback to litecoinspace.org
        try:
            start_time_fallback = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{FALLBACK_API_URL}/v1/exchange-rates") as response:
                    if response.status == 200:
                        data = await response.json()
                        rate = float(data['rates']['USD'])
                        response_time = (time.time() - start_time_fallback) * 1000
                        log_api_request('litecoinspace_rate', True, response_time, f"Rate: {rate}")
                        return rate
        except Exception as fallback_error:
            logger.error(f"Litecoinspace rate error: {fallback_error}")
            response_time = (time.time() - start_time_fallback) * 1000
            log_api_request('litecoinspace_rate', False, response_time, f"Exception: {str(fallback_error)}")
    
    return 65.0  # Fallback value

async def get_address_transactions(address: str) -> List[Dict]:
    """Получение транзакций адреса через BitAPS"""
    try:
        start_time = time.time()
        
        if not validate_ltc_address(address):
            log_address_validation(address, False, "API request blocked")
            return []
            
        log_address_validation(address, True, "API request")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{PRIMARY_API_URL}/address/{address}") as response:
                if response.status == 200:
                    data = await response.json()
                    transactions = data.get('data', {}).get('transactions', [])
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('bitaps_address_txs', True, response_time, 
                                  f"Found {len(transactions)} transactions")
                    return transactions
    except Exception as e:
        logger.error(f"BitAPS address error: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('bitaps_address_txs', False, response_time, f"Exception: {str(e)}")
        
        # Fallback to litecoinspace.org
        try:
            start_time_fallback = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{FALLBACK_API_URL}/v1/address/{address}/transactions") as response:
                    if response.status == 200:
                        data = await response.json()
                        transactions = data.get('transactions', [])
                        response_time = (time.time() - start_time_fallback) * 1000
                        log_api_request('litecoinspace_address_txs', True, response_time, 
                                      f"Found {len(transactions)} transactions")
                        return transactions
        except Exception as fallback_error:
            logger.error(f"Litecoinspace address error: {fallback_error}")
            response_time = (time.time() - start_time_fallback) * 1000
            log_api_request('litecoinspace_address_txs', False, response_time, f"Exception: {str(fallback_error)}")
    
    return []

async def check_ltc_transaction_enhanced(address: str, expected_amount: float) -> Dict[str, Any]:
    """Улучшенная проверка транзакций через BitAPS"""
    try:
        start_time = time.time()
        transactions = await get_address_transactions(address)
        
        result = {
            'confirmed': False,
            'unconfirmed': False,
            'confirmations': 0,
            'amount': 0.0,
            'txids': []
        }
        
        for tx in transactions:
            # Принимаем любую положительную сумму
            if tx.get('amount', 0) > 0:
                amount_ltc = tx['amount'] / 100000000  # Конвертация из сатоши
                if abs(amount_ltc - expected_amount) < 0.00000001:
                    result['unconfirmed'] = not tx.get('confirmed', False)
                    result['confirmed'] = tx.get('confirmed', False)
                    result['confirmations'] = tx.get('confirmations', 0)
                    result['amount'] = amount_ltc
                    result['txids'].append(tx['txid'])
                    break
        
        response_time = (time.time() - start_time) * 1000
        log_api_request('enhanced_check', True, response_time, 
                       f"Confirmed: {result['confirmed']}, Unconfirmed: {result['unconfirmed']}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error in enhanced LTC transaction check: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('enhanced_check', False, response_time, f"Exception: {str(e)}")
        return {
            'confirmed': False,
            'unconfirmed': False,
            'confirmations': 0,
            'amount': 0.0,
            'txids': []
        }

async def check_ltc_transaction(address: str, expected_amount: float) -> bool:
    """
    Проверка наличия транзакции на указанный адрес с ожидаемой суммой
    Возвращает True если транзакция найдена и имеет достаточное количество подтверждений
    """
    try:
        tx_check = await check_ltc_transaction_enhanced(address, expected_amount)
        return tx_check['confirmed'] and tx_check['confirmations'] >= CONFIRMATIONS_REQUIRED
    except Exception as e:
        logger.error(f"Error checking LTC transaction for address {address}: {e}")
        return False

async def get_all_tracked_addresses():
    """Получение всех отслеживаемых адресов"""
    try:
        # Сначала проверяем существование столбца user_id
        async with db_connection() as conn:
            # Проверяем наличие столбца
            column_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'generated_addresses' 
                    AND column_name = 'user_id'
                )
            """)
            
            if not column_exists:
                return []
                
            return await conn.fetch("SELECT * FROM generated_addresses WHERE user_id IS NOT NULL")
    except Exception as e:
        logger.error(f"Error getting tracked addresses: {e}")
        return []

async def is_transaction_processed(txid: str) -> bool:
    """Проверка, была ли уже обработана транзакция"""
    try:
        async with db_connection() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM deposits WHERE txid = $1", txid)
            return count > 0
    except Exception as e:
        logger.error(f"Error checking if transaction processed: {e}")
        return False

async def register_deposit(txid: str, address: str, user_id: int, amount_ltc: float, confirmations: int, status: str):
    """Регистрация депозита в базе данных"""
    try:
        async with db_connection() as conn:
            ltc_rate = await get_ltc_usd_rate()
            amount_usd = amount_ltc * ltc_rate
            
            await conn.execute('''
                INSERT INTO deposits (txid, address, user_id, amount_ltc, amount_usd, confirmations, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (txid) DO UPDATE SET
                confirmations = EXCLUDED.confirmations,
                status = EXCLUDED.status
            ''', txid, address, user_id, amount_ltc, amount_usd, confirmations, status)
            
            await update_address_balance(address, amount_ltc, 1)
            
            log_transaction_event(
                txid, address, amount_ltc, 
                "DEPOSIT_REGISTERED", 
                f"Deposit registered for user {user_id} with {confirmations} confirmations", 
                "INFO"
            )
    except Exception as e:
        logger.error(f"Error registering deposit: {e}")
        log_transaction_event(
            txid, address, amount_ltc, 
            "DEPOSIT_ERROR", 
            f"Error registering deposit: {str(e)}", 
            "ERROR"
        )

async def process_confirmed_deposit(txid: str, user_id: int, amount_ltc: float):
    """Обработка подтвержденного депозита - зачисление средств на баланс пользователя"""
    try:
        async with db_connection() as conn:
            deposit = await conn.fetchrow("SELECT * FROM deposits WHERE txid = $1", txid)
            if not deposit:
                return
            
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                deposit['amount_usd'], user_id
            )
            
            await conn.execute(
                "UPDATE deposits SET status = 'processed' WHERE txid = $1",
                txid
            )
            
            # Дополнительно: обновляем статус соответствующего инвойса
            invoice = await conn.fetchrow(
                "SELECT * FROM transactions WHERE crypto_address = $1 AND crypto_amount = $2 AND status = 'pending'",
                deposit['address'], deposit['amount_ltc']
            )
            if invoice:
                await conn.execute(
                    "UPDATE transactions SET status = 'completed' WHERE order_id = $1",
                    invoice['order_id']
                )
            
            log_transaction_event(
                txid, deposit['address'], amount_ltc, 
                "DEPOSIT_PROCESSED", 
                f"Deposit processed for user {user_id}, amount: {deposit['amount_usd']} USD", 
                "INFO"
            )
            
            try:
                from bot import bot
                user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
                lang = user['language'] or 'ru'
                
                from db import get_text
                message = get_text(lang, 'deposit_confirmed').format(
                    amount_ltc=amount_ltc,
                    amount_usd=deposit['amount_usd']
                )
                
                await bot.send_message(user_id, message)
            except Exception as e:
                logger.error(f"Error sending deposit notification: {e}")
            
    except Exception as e:
        logger.error(f"Error processing confirmed deposit: {e}")
        log_transaction_event(
            txid, "unknown", amount_ltc, 
            "DEPOSIT_PROCESS_ERROR", 
            f"Error processing confirmed deposit: {str(e)}", 
            "ERROR"
        )

async def monitor_deposits():
    """Мониторинг депозитов на всех адресах"""
    while True:
        try:
            addresses = await get_all_tracked_addresses()
            
            for addr in addresses:
                transactions = await get_address_transactions(addr['address'])
                
                for tx in transactions:
                    if await is_transaction_processed(tx['txid']):
                        continue
                    
                    # Регистрируем любую положительную сумму
                    if tx.get('amount', 0) > 0:
                        amount_ltc = tx['amount'] / 100000000  # Конвертация из сатоши
                        confirmations = tx.get('confirmations', 0)
                        status = 'confirmed' if confirmations >= CONFIRMATIONS_REQUIRED else 'pending'
                        
                        await register_deposit(
                            txid=tx['txid'],
                            address=addr['address'],
                            user_id=addr['user_id'],
                            amount_ltc=amount_ltc,
                            confirmations=confirmations,
                            status=status
                        )
            
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Error in monitor_deposits: {e}")
            await asyncio.sleep(60)

async def confirm_pending_deposits():
    """Подтверждение ожидающих депозитов"""
    while True:
        try:
            async with db_connection() as conn:
                pending_deposits = await conn.fetch(
                    "SELECT * FROM deposits WHERE status = 'pending'"
                )
                
                for deposit in pending_deposits:
                    # Получаем актуальное количество подтверждений
                    transactions = await get_address_transactions(deposit['address'])
                    confirmations = 0
                    
                    for tx in transactions:
                        if tx['txid'] == deposit['txid']:
                            confirmations = tx.get('confirmations', 0)
                            break
                    
                    if confirmations >= CONFIRMATIONS_REQUIRED:
                        await conn.execute(
                            "UPDATE deposits SET status = 'confirmed', confirmations = $1 WHERE txid = $2",
                            confirmations, deposit['txid']
                        )
                        await process_confirmed_deposit(deposit['txid'], deposit['user_id'], deposit['amount_ltc'])
            
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Error in confirm_pending_deposits: {e}")
            await asyncio.sleep(60)

async def get_address_balance(address: str) -> Tuple[float, int]:
    """
    Получение баланса и количества транзакций для адреса через BitAPS
    Возвращает (balance, transaction_count)
    """
    try:
        start_time = time.time()
        
        if not validate_ltc_address(address):
            return 0, 0
            
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{PRIMARY_API_URL}/address/{address}") as response:
                if response.status == 200:
                    data = await response.json()
                    balance = data['data']['balance'] / 100000000  # Конвертация из сатоши
                    tx_count = data['data']['tx_count']
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('bitaps_balance', True, response_time, 
                                  f"Balance: {balance}, TX count: {tx_count}")
                    return balance, tx_count
    except Exception as e:
        logger.error(f"BitAPS balance error: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('bitaps_balance', False, response_time, f"Exception: {str(e)}")
        
        # Fallback to litecoinspace.org
        try:
            start_time_fallback = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{FALLBACK_API_URL}/v1/address/{address}") as response:
                    if response.status == 200:
                        data = await response.json()
                        balance = data['balance'] / 100000000  # Конвертация из сатоши
                        tx_count = data['tx_count']
                        response_time = (time.time() - start_time_fallback) * 1000
                        log_api_request('litecoinspace_balance', True, response_time, 
                                      f"Balance: {balance}, TX count: {tx_count}")
                        return balance, tx_count
                except Exception as fallback_error:
            logger.error(f"Litecoinspace balance error: {fallback_error}")
            response_time = (time.time() - start_time_fallback) * 1000
            log_api_request('litecoinspace_balance', False, response_time, f"Exception: {str(fallback_error)}")
    
    return 0, 0

async def get_key_usage_stats() -> Dict[str, any]:
    """Получение статистики использования API ключей"""
    # BitAPS не требует API ключей для базового использования
    return {
        "bitaps": {
            "total_requests": 0,
            "successful_requests": 0,
            "last_used": datetime.now().isoformat(),
            "daily_limit": float('inf'),
            "remaining_daily_requests": float('inf')
        }
    }

# Кэш для хранения курса LTC
_cached_rate = None
_cached_rate_time = 0
CACHE_DURATION = 300  # 5 минут

async def get_cached_rate() -> Tuple[float, bool]:
    """Получение кэшированного курса LTC"""
    global _cached_rate, _cached_rate_time
    current_time = time.time()
    
    if _cached_rate and (current_time - _cached_rate_time) < CACHE_DURATION:
        return _cached_rate, True
    
    try:
        _cached_rate = await get_ltc_usd_rate()
        _cached_rate_time = current_time
        return _cached_rate, False
    except Exception as e:
        logger.error(f"Error getting cached rate: {e}")
        return 65.0, False  # Fallback value

# Запуск фоновых задач мониторинга
def start_deposit_monitoring():
    """Запуск задач мониторинга депозитов"""
    asyncio.create_task(monitor_deposits())
    asyncio.create_task(confirm_pending_deposits())
