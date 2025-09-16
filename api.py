# api.py
import aiohttp
import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import time
from db import db_connection, update_user, add_generated_address, update_address_balance
import base58
import hashlib
import re

logger = logging.getLogger(__name__)

# Конфигурация API
MEMPOOL_API_URL = "https://mempool.space/api"
MEMPOOL_TESTNET_URL = "https://mempool.space/testnet/api"
LTC_NETWORK = "mainnet"  # или "testnet"
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
        'transaction_id': transaction_id,
        'address': address,
        'amount': amount,
        'status': status
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
    payment_logger.info(
        f"Address validation: {status} - {address} {context}",
        extra={'address': address, 'validation_status': status}
    )

def log_api_request(api_name: str, success: bool, response_time: float, 
                   details: str = ''):
    """Логирование запросов к API"""
    status = "SUCCESS" if success else "FAILED"
    payment_logger.info(
        f"API {api_name} request {status} - {response_time:.2f}ms {details}",
        extra={'api': api_name, 'status': status, 'response_time': response_time}
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
        # Bech32 адреса обычно имеют длину около 42 символов
        return 40 <= len(address) <= 62
    
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
        # Проверяем префикс
        if not address.startswith(expected_prefix):
            return False
        
        # Декодируем Base58
        decoded = base58.b58decode(address)
        
        # Проверяем длину
        if len(decoded) != 25:
            return False
        
        # Разделяем на payload и checksum
        payload = decoded[:-4]
        checksum = decoded[-4:]
        
        # Вычисляем проверочную сумму
        first_sha = hashlib.sha256(payload).digest()
        second_sha = hashlib.sha256(first_sha).digest()
        calculated_checksum = second_sha[:4]
        
        # Сравниваем checksum
        return checksum == calculated_checksum
        
    except Exception:
        return False

async def get_ltc_usd_rate() -> float:
    """Получение текущего курса LTC к USD"""
    try:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MEMPOOL_API_URL}/v1/historical?currency=USD") as response:
                if response.status == 200:
                    data = await response.json()
                    # Получаем последнюю доступную цену
                    rate = float(data.get('prices', [])[-1][1] if data.get('prices') else 0)
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_rate', True, response_time, f"Rate: {rate}")
                    return rate
        response_time = (time.time() - start_time) * 1000
        log_api_request('mempool_rate', False, response_time, f"Status: {response.status}")
    except Exception as e:
        logger.error(f"Error getting LTC rate: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('mempool_rate', False, response_time, f"Exception: {str(e)}")
    
    # Возвращаем значение по умолчанию в случае ошибки
    return 65.0  # Примерное значение

async def get_address_transactions(address: str) -> List[Dict]:
    """Получение транзакций для адреса из mempool.space"""
    try:
        start_time = time.time()
        
        # Валидация адреса перед запросом
        if not validate_ltc_address(address):
            log_address_validation(address, False, "API request blocked")
            return []
            
        log_address_validation(address, True, "API request")
        
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/address/{address}/txs") as response:
                if response.status == 200:
                    transactions = await response.json()
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_address_txs', True, response_time, 
                                  f"Found {len(transactions)} transactions")
                    return transactions
                else:
                    logger.error(f"Error getting transactions for address {address}: {response.status}")
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_address_txs', False, response_time, 
                                  f"Status: {response.status}")
                    return []
    except Exception as e:
        logger.error(f"Error in get_address_transactions for {address}: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('mempool_address_txs', False, response_time, f"Exception: {str(e)}")
        return []

async def get_transaction(txid: str) -> Optional[Dict]:
    """Получение информации о конкретной транзакции"""
    try:
        start_time = time.time()
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/tx/{txid}") as response:
                if response.status == 200:
                    transaction = await response.json()
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_tx', True, response_time, f"TX: {txid}")
                    return transaction
                else:
                    logger.error(f"Error getting transaction {txid}: {response.status}")
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_tx', False, response_time, f"Status: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error in get_transaction for {txid}: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('mempool_tx', False, response_time, f"Exception: {str(e)}")
        return None

async def check_unconfirmed_transactions(address: str) -> List[Dict]:
    """Проверка неподтвержденных транзакций через Mempool.space API"""
    try:
        start_time = time.time()
        
        # Валидация адреса перед запросом
        if not validate_ltc_address(address):
            return []
            
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/address/{address}/txs/mempool") as response:
                if response.status == 200:
                    transactions = await response.json()
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_unconfirmed', True, response_time, 
                                  f"Found {len(transactions)} unconfirmed transactions")
                    return transactions
                return []
    except Exception as e:
        logger.error(f"Error checking unconfirmed transactions for {address}: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('mempool_unconfirmed', False, response_time, f"Exception: {str(e)}")
        return []

async def check_ltc_transaction_enhanced(address: str, expected_amount: float) -> Dict[str, Any]:
    """
    Улучшенная проверка транзакций с учетом неподтвержденных
    Возвращает детальную информацию о статусе транзакции
    """
    try:
        start_time = time.time()
        
        # Проверяем подтвержденные транзакции
        confirmed_txs = await get_address_transactions(address)
        # Проверяем неподтвержденные транзакции
        unconfirmed_txs = await check_unconfirmed_transactions(address)
        
        result = {
            'confirmed': False,
            'unconfirmed': False,
            'confirmations': 0,
            'amount': 0.0,
            'txids': []
        }
        
        # Проверяем подтвержденные транзакции
        for tx in confirmed_txs:
            for output in tx.get('vout', []):
                if 'scriptpubkey_address' in output and output['scriptpubkey_address'] == address:
                    amount_ltc = output['value'] / 100000000
                    if abs(amount_ltc - expected_amount) < 0.00000001:
                        result['confirmed'] = True
                        result['confirmations'] = await get_confirmations_count(tx['txid'])
                        result['amount'] = amount_ltc
                        result['txids'].append(tx['txid'])
        
        # Проверяем неподтвержденные транзакции
        for tx in unconfirmed_txs:
            for output in tx.get('vout', []):
                if 'scriptpubkey_address' in output and output['scriptpubkey_address'] == address:
                    amount_ltc = output['value'] / 100000000
                    if abs(amount_ltc - expected_amount) < 0.00000001:
                        result['unconfirmed'] = True
                        result['amount'] = amount_ltc
                        result['txids'].append(tx['txid'])
        
        response_time = (time.time() - start_time) * 1000
        log_api_request('enhanced_check', True, response_time, 
                       f"Confirmed: {result['confirmed']}, Unconfirmed: {result['unconfirmed']}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error in enhanced LTC transaction check for {address}: {e}")
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

async def get_confirmations_count(txid: str) -> int:
    """Получение количества подтверждений для транзакции"""
    try:
        tx = await get_transaction(txid)
        if tx and tx['status']['confirmed']:
            # Для подтвержденных транзакций получаем количество подтверждений
            best_block_height = await get_best_block_height()
            if best_block_height:
                return best_block_height - tx['status']['block_height'] + 1
        return 0
    except Exception as e:
        logger.error(f"Error getting confirmations for tx {txid}: {e}")
        return 0

async def get_best_block_height() -> Optional[int]:
    """Получение высоты последнего блока"""
    try:
        start_time = time.time()
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/blocks/tip/height") as response:
                if response.status == 200:
                    height = int(await response.text())
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_height', True, response_time, f"Height: {height}")
                    return height
                else:
                    logger.error(f"Error getting best block height: {response.status}")
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_height', False, response_time, f"Status: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error in get_best_block_height: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('mempool_height', False, response_time, f"Exception: {str(e)}")
        return None

async def monitor_deposits():
    """Фоновая задача для мониторинга депозитов на всех отслеживаемых адресах"""
    while True:
        try:
            # Получаем все адреса из базы данных
            addresses = await get_all_tracked_addresses()
            
            for address_data in addresses:
                address = address_data['address']
                user_id = address_data['user_id']
                expected_amount = address_data.get('expected_amount', 0)
                
                # Проверяем транзакции для этого адреса
                transactions = await get_address_transactions(address)
                
                for tx in transactions:
                    txid = tx['txid']
                    
                    # Проверяем, не обрабатывали ли мы уже эту транзакцию
                    if await is_transaction_processed(txid):
                        continue
                    
                    # Ищем выходы на наш адрес
                    for output in tx.get('vout', []):
                        if 'scriptpubkey_address' in output and output['scriptpubkey_address'] == address:
                            amount_ltc = output['value'] / 100000000  # Конвертация из сатоши
                            
                            # Если сумма соответствует ожидаемой или мы отслеживаем все поступления
                            if expected_amount == 0 or abs(amount_ltc - expected_amount) < 0.00000001:
                                # Регистрируем депозит
                                confirmations = await get_confirmations_count(txid)
                                status = 'confirmed' if confirmations >= CONFIRMATIONS_REQUIRED else 'pending'
                                
                                await register_deposit(
                                    txid=txid,
                                    address=address,
                                    user_id=user_id,
                                    amount_ltc=amount_ltc,
                                    confirmations=confirmations,
                                    status=status
                                )
                                
                                # Если транзакция подтверждена, зачисляем средства
                                if status == 'confirmed':
                                    await process_confirmed_deposit(txid, user_id, amount_ltc)
            
            # Ждем перед следующей проверкой
            await asyncio.sleep(300)  # 5 минут
        except Exception as e:
            logger.error(f"Error in monitor_deposits: {e}")
            await asyncio.sleep(60)  # Ждем 1 минуту при ошибке

async def get_all_tracked_addresses():
    """Получение всех отслеживаемых адресов из базы данных"""
    try:
        async with db_connection() as conn:
            return await conn.fetch("SELECT * FROM generated_addresses WHERE balance = 0 OR balance IS NULL")
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
            # Получаем курс LTC для конвертации в USD
            ltc_rate = await get_ltc_usd_rate()
            amount_usd = amount_ltc * ltc_rate
            
            await conn.execute('''
                INSERT INTO deposits (txid, address, user_id, amount_ltc, amount_usd, confirmations, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (txid) DO UPDATE SET
                confirmations = EXCLUDED.confirmations,
                status = EXCLUDED.status
            ''', txid, address, user_id, amount_ltc, amount_usd, confirmations, status)
            
            # Обновляем баланс адреса
            await update_address_balance(address, amount_ltc, 1)  # 1 транзакция
            
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
            # Получаем информацию о депозите
            deposit = await conn.fetchrow("SELECT * FROM deposits WHERE txid = $1", txid)
            if not deposit:
                return
            
            # Зачисляем средства на баланс пользователя
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                deposit['amount_usd'], user_id
            )
            
            # Обновляем статус депозита
            await conn.execute(
                "UPDATE deposits SET status = 'processed' WHERE txid = $1",
                txid
            )
            
            log_transaction_event(
                txid, deposit['address'], amount_ltc, 
                "DEPOSIT_PROCESSED", 
                f"Deposit processed for user {user_id}, amount: {deposit['amount_usd']} USD", 
                "INFO"
            )
            
            # Отправляем уведомление пользователю
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

async def get_address_balance(address: str) -> Tuple[float, int]:
    """
    Получение баланса и количества транзакций для адреса
    Возвращает (balance, transaction_count)
    """
    try:
        start_time = time.time()
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/address/{address}") as response:
                if response.status == 200:
                    data = await response.json()
                    balance = data['chain_stats']['funded_txo_sum'] - data['chain_stats']['spent_txo_sum']
                    balance_ltc = balance / 100000000  # Конвертация из сатоши
                    tx_count = data['chain_stats']['tx_count'] + data['mempool_stats']['tx_count']
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_balance', True, response_time, 
                                  f"Balance: {balance_ltc}, TX count: {tx_count}")
                    return balance_ltc, tx_count
                else:
                    logger.error(f"Error getting address balance for {address}: {response.status}")
                    response_time = (time.time() - start_time) * 1000
                    log_api_request('mempool_balance', False, response_time, f"Status: {response.status}")
                    return 0, 0
    except Exception as e:
        logger.error(f"Error in get_address_balance for {address}: {e}")
        response_time = (time.time() - start_time) * 1000
        log_api_request('mempool_balance', False, response_time, f"Exception: {str(e)}")
        return 0, 0

async def get_key_usage_stats() -> Dict[str, any]:
    """Получение статистики использования API ключей"""
    # В данной реализации мы не используем API ключи для mempool.space,
    # но функция оставлена для совместимости
    return {
        "mempool": {
            "total_requests": 0,
            "successful_requests": 0,
            "last_used": datetime.now().isoformat(),
            "daily_limit": float('inf'),
            "remaining_daily_requests": float('inf')
        }
    }

async def monitor_unconfirmed_transactions():
    """Фоновая задача для мониторинга неподтвержденных транзакций"""
    while True:
        try:
            # Получаем все ожидающие транзакции
            async with db_connection() as conn:
                pending_txs = await conn.fetch(
                    "SELECT * FROM transactions WHERE status = 'pending'"
                )
            
            for tx in pending_txs:
                # Проверяем статус каждой транзакции
                tx_check = await check_ltc_transaction_enhanced(
                    tx['crypto_address'], 
                    float(tx['crypto_amount'])
                )
                
                if tx_check['confirmed'] and tx_check['confirmations'] >= CONFIRMATIONS_REQUIRED:
                    # Транзакция подтверждена
                    await update_transaction_status(tx['order_id'], 'completed')
                    await process_successful_payment(tx)
                    
                    log_transaction_event(
                        tx['order_id'], tx['crypto_address'],
                        float(tx['crypto_amount']), 
                        "CONFIRMED", 
                        f"Transaction confirmed via monitoring with {tx_check['confirmations']} confirmations", 
                        "INFO"
                    )
                    
                elif tx_check['unconfirmed']:
                    # Транзакция все еще в mempool
                    log_transaction_event(
                        tx['order_id'], tx['crypto_address'],
                        float(tx['crypto_amount']), 
                        "MONITORED", 
                        f"Transaction still in mempool with {tx_check.get('confirmations', 0)} confirmations", 
                        "DEBUG"
                    )
                
                # Если транзакция не найдена ни в блокчейне, ни в mempool,
                # и прошло больше времени чем ожидалось, помечаем как проблемную
                elif not tx_check['unconfirmed'] and not tx_check['confirmed']:
                    created_at = tx['created_at']
                    if (datetime.now() - created_at).total_seconds() > 3600:  # 1 час
                        log_transaction_event(
                            tx['order_id'], tx['crypto_address'],
                            float(tx['crypto_amount']), 
                            "STALE", 
                            "Transaction not found in blockchain or mempool for over 1 hour", 
                            "WARNING"
                        )
            
            await asyncio.sleep(300)  # Проверяем каждые 5 минут
            
        except Exception as e:
            logger.exception("Error in unconfirmed transactions monitoring")
            await asyncio.sleep(60)

# Запуск фоновой задачи мониторинга
def start_deposit_monitoring():
    """Запуск задачи мониторинга депозитов"""
    asyncio.create_task(monitor_deposits())
    asyncio.create_task(monitor_unconfirmed_transactions())
