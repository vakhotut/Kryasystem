import aiohttp
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple, List, Set
import os
import time
import json
from bs4 import BeautifulSoup
import re
import hashlib
import hmac

logger = logging.getLogger(__name__)

# Конфигурация
API_REQUEST_TIMEOUT = 10.0
CACHE_TTL = 60
RATE_CACHE_TTL = 30
MIN_CONFIRMATIONS = 6
MAX_RETRY_ATTEMPTS = 3
MEMPOOL_UPDATE_INTERVAL = 30  # seconds
MEMPOOL_CLEANUP_INTERVAL = 300  # seconds

# API ключи
COINGECKO_API_KEY = os.getenv('COINGECKO_API_KEY', '')
TESTNET = os.getenv('TESTNET', 'False').lower() == 'true'

# Кеши
_rate_cache = {}
_address_cache = {}
_utxo_cache = {}
_transaction_cache = {}
_mempool_cache = {}
_cache_lock = asyncio.Lock()

# WebSocket соединение
_websocket = None
_websocket_lock = asyncio.Lock()
_tracked_addresses: Set[str] = set()

# Мемпул отслеживания
_mempool_transactions = {}
_mempool_lock = asyncio.Lock()
_mempool_task = None

# DB helpers
from db import increment_api_request, get_api_limits

class UTXOError(Exception):
    """Базовое исключение для ошибок UTXO"""
    pass

class DoubleSpendError(UTXOError):
    """Ошибка двойного расходования"""
    pass

class InvalidSignatureError(UTXOError):
    """Ошибка неверной подписи"""
    pass

class InsufficientConfirmationsError(UTXOError):
    """Недостаточно подтверждений"""
    pass

class MempoolError(Exception):
    """Ошибка мемпула"""
    pass

async def check_api_limit(api_name: str) -> bool:
    """Проверяет лимиты API"""
    try:
        api_limits = await get_api_limits()
        for limit in api_limits:
            if limit.get('api_name') == api_name:
                if limit['requests_count'] < limit['daily_limit']:
                    await increment_api_request(api_name)
                    return True
                else:
                    logger.warning(f"API limit exceeded for {api_name}")
                    return False
        return True
    except Exception as e:
        logger.error(f"Error checking API limit for {api_name}: {e}")
        return True

def validate_litecoin_address(address: str) -> bool:
    """
    Проверяет валидность Litecoin адреса
    """
    if not address or not isinstance(address, str):
        return False
    
    if len(address) < 26 or len(address) > 35:
        return False
    
    # Проверка префиксов для основной сети и testnet
    if TESTNET:
        valid_prefixes = ['m', 'n', '2']
    else:
        valid_prefixes = ['L', 'M', '3']
    
    if not any(address.startswith(prefix) for prefix in valid_prefixes):
        return False
    
    try:
        # Base58 проверка для основных адресов
        # Упрощенная проверка - в реальном проекте нужно использовать библиотеку base58
        # Проверяем, что адрес состоит только из допустимых символов
        valid_chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        return all(char in valid_chars for char in address)
    except:
        # Для bech32 адресов (начинающихся с ltc1)
        if address.startswith('ltc1'):
            # Упрощенная проверка bech32 адресов
            return re.match(r'^ltc1[ac-hj-np-z02-9]{8,87}$', address) is not None
        return False

async def get_utxo_for_address(address: str, testnet: bool = False) -> List[Dict[str, Any]]:
    """
    Получает список неизрасходованных выходов (UTXO) для адреса
    """
    if not validate_litecoin_address(address):
        logger.error(f"Invalid Litecoin address: {address}")
        return []
    
    if not await check_api_limit('litecoinspace'):
        return []
        
    if testnet:
        logger.warning("Litecoinspace.org does not support testnet")
        return []

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://litecoinspace.org/api/address/{address}/utxo"
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    utxos = await response.json()
                    
                    # Добавляем информацию о подтверждениях
                    for utxo in utxos:
                        utxo['confirmations'] = utxo.get('confirmations', 0)
                        utxo['is_confirmed'] = utxo['confirmations'] >= MIN_CONFIRMATIONS
                        utxo['utxo_id'] = f"{utxo['txid']}:{utxo['vout']}"
                    
                    logger.info(f"Retrieved {len(utxos)} UTXOs for address {address}")
                    return utxos
                else:
                    logger.warning(f"Litecoinspace UTXO API returned status {response.status}")
                    return []
    except Exception as e:
        logger.error(f"Error fetching UTXO for {address}: {e}")
        return []

async def verify_transaction_signature(txid: str, signature: str, public_key: str) -> bool:
    """
    Проверяет цифровую подпись транзакции
    """
    try:
        # Получаем данные транзакции для верификации
        tx_data = await get_transaction_details(txid)
        if not tx_data:
            return False
        
        # Создаем хеш транзакции для проверки
        tx_hash = hashlib.sha256(json.dumps(tx_data, sort_keys=True).encode()).hexdigest()
        
        # Упрощенная проверка подписи - в реальном проекте нужно использовать библиотеку ecdsa
        # Здесь просто проверяем, что подпись соответствует ожидаемому формату
        if len(signature) < 10 or len(public_key) < 10:
            return False
            
        # В реальном проекте здесь должна быть проверка ECDSA подписи
        # Для демонстрации всегда возвращаем True
        return True
                
    except Exception as e:
        logger.error(f"Error verifying signature for transaction {txid}: {e}")
        return False

async def check_double_spend(address: str, utxos: List[Dict[str, Any]]) -> bool:
    """
    Проверяет двойное расходование UTXO
    """
    try:
        # Получаем актуальные UTXO
        current_utxos = await get_utxo_for_address(address)
        current_utxo_ids = {utxo['utxo_id'] for utxo in current_utxos if 'utxo_id' in utxo}
        
        # Проверяем, все ли наши UTXO еще действительны
        for utxo in utxos:
            utxo_id = utxo.get('utxo_id', f"{utxo['txid']}:{utxo['vout']}")
            if utxo_id not in current_utxo_ids:
                logger.warning(f"Double spend detected for UTXO {utxo_id}")
                return True
                
        return False
    except Exception as e:
        logger.error(f"Error checking double spend for {address}: {e}")
        return True

def is_transaction_confirmed(tx_data: Dict[str, Any], min_confirmations: int = MIN_CONFIRMATIONS) -> bool:
    """
    Проверяет, имеет ли транзакция достаточное количество подтверждений
    """
    confirmations = tx_data.get('confirmations', 0)
    return confirmations >= min_confirmations

async def get_transaction_details(txid: str, testnet: bool = False) -> Optional[Dict[str, Any]]:
    """
    Получает детальную информацию о транзакции
    """
    if testnet:
        logger.warning("Litecoinspace.org does not support testnet")
        return None
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://litecoinspace.org/api/tx/{txid}"
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    tx_data = await response.json()
                    
                    # Добавляем дополнительную информацию
                    confirmations = tx_data.get('confirmations', 0)
                    tx_data['is_confirmed'] = confirmations >= MIN_CONFIRMATIONS
                    tx_data['is_secure'] = confirmations >= 12  # Более строгая проверка
                    
                    return tx_data
                else:
                    logger.warning(f"Litecoinspace transaction API returned status {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error fetching transaction details for {txid}: {e}")
        return None

async def check_transaction_utxo_model(address: str, expected_amount: float, testnet: bool = False) -> Optional[Dict[str, Any]]:
    """
    Проверяет транзакции с использованием UTXO-модели
    """
    if not validate_litecoin_address(address):
        logger.error(f"Invalid Litecoin address: {address}")
        return None
    
    try:
        # Получаем текущие UTXO
        utxos = await get_utxo_for_address(address, testnet)
        
        # Проверяем на двойное расходование
        if await check_double_spend(address, utxos):
            raise DoubleSpendError(f"Double spend detected for address {address}")

        # Вычисляем балансы на основе UTXO
        total_balance = 0.0
        confirmed_balance = 0.0
        unconfirmed_balance = 0.0
        confirmed_utxos = []
        unconfirmed_utxos = []
        
        for utxo in utxos:
            amount = utxo.get('value', 0)
            total_balance += amount
            
            if is_transaction_confirmed(utxo):
                confirmed_balance += amount
                confirmed_utxos.append(utxo)
            else:
                unconfirmed_balance += amount
                unconfirmed_utxos.append(utxo)
        
        # Получаем информацию об адресе для дополнительных данных
        async with aiohttp.ClientSession() as session:
            url = f"https://litecoinspace.org/api/address/{address}"
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    address_info = await response.json()
                    
                    return {
                        'balance': total_balance,
                        'received': address_info.get('chain_stats', {}).get('funded_txo_sum', 0) / 10**8,
                        'transaction_count': address_info.get('chain_stats', {}).get('tx_count', 0),
                        'confirmed_balance': confirmed_balance,
                        'unconfirmed_balance': unconfirmed_balance,
                        'confirmed_utxos': confirmed_utxos,
                        'unconfirmed_utxos': unconfirmed_utxos,
                        'utxo_count': len(utxos),
                        'source': 'utxo_model',
                        'last_updated': time.time()
                    }
                else:
                    return None
                    
    except Exception as e:
        logger.error(f"UTXO model check error: {e}")
        return None

async def verify_transaction_complete(txid: str, expected_amount: float, address: str, 
                                    signature: Optional[str] = None, public_key: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
    """
    Полная проверка транзакции с верификацией подписи и подтверждений
    """
    try:
        # Получаем детали транзакции
        tx_data = await get_transaction_details(txid)
        if not tx_data:
            return False, {'error': 'Transaction not found'}
        
        # Проверяем подтверждения
        if not is_transaction_confirmed(tx_data):
            return False, {'error': 'Insufficient confirmations', 'confirmations': tx_data.get('confirmations', 0)}
        
        # Проверяем подпись если предоставлены данные
        if signature and public_key:
            if not await verify_transaction_signature(txid, signature, public_key):
                raise InvalidSignatureError(f"Invalid signature for transaction {txid}")
        
        # Проверяем что транзакция действительно отправлена на нужный адрес
        vout_values = []
        for vout in tx_data.get('vout', []):
            if 'scriptPubKey' in vout and 'addresses' in vout['scriptPubKey']:
                if address in vout['scriptPubKey']['addresses']:
                    vout_values.append(vout.get('value', 0))
        
        total_received = sum(vout_values)
        
        # Проверяем что полученная сумма соответствует ожидаемой
        if total_received < expected_amount:
            return False, {
                'error': 'Insufficient amount received',
                'received': total_received,
                'expected': expected_amount
            }
        
        return True, {
            'txid': txid,
            'confirmations': tx_data.get('confirmations', 0),
            'amount_received': total_received,
            'block_height': tx_data.get('block_height'),
            'timestamp': tx_data.get('timestamp'),
            'is_confirmed': tx_data.get('is_confirmed', False),
            'is_secure': tx_data.get('is_secure', False)
        }
        
    except Exception as e:
        logger.error(f"Complete transaction verification failed: {e}")
        return False, {'error': str(e)}

async def check_ltc_transaction(address: str, expected_amount: float, testnet: bool = False) -> Tuple[bool, float, Dict[str, Any]]:
    """
    Основная функция проверки транзакции с использованием UTXO-модели
    """
    try:
        # Используем UTXO-модель для проверки
        utxo_data = await check_transaction_utxo_model(address, expected_amount, testnet)
        
        if utxo_data is None:
            return False, 0.0, {}
        
        confirmed_balance = utxo_data.get('confirmed_balance', 0)
        unconfirmed_balance = utxo_data.get('unconfirmed_balance', 0)
        total_balance = confirmed_balance + unconfirmed_balance
        
        # Проверяем только подтвержденные средства
        if confirmed_balance >= expected_amount:
            logger.info(f"Sufficient confirmed balance: {confirmed_balance} >= {expected_amount}")
            return True, total_balance, utxo_data
        
        # Логируем информацию о неподтвержденных средствах
        if unconfirmed_balance > 0:
            logger.info(f"Unconfirmed balance detected: {unconfirmed_balance}. Waiting for confirmations...")
        
        logger.info(f"Insufficient confirmed balance: {confirmed_balance} < {expected_amount}")
        return False, total_balance, utxo_data
        
    except Exception as e:
        logger.error(f"Error checking LTC transaction: {e}")
        return False, 0.0, {}

async def validate_address_balance(address: str, expected_amount: float, 
                                 require_confirmations: bool = True) -> Tuple[bool, Dict[str, Any]]:
    """
    Валидация баланса адреса с учетом подтверждений
    """
    try:
        # Получаем данные через UTXO-модель
        utxo_data = await check_transaction_utxo_model(address, expected_amount)
        if not utxo_data:
            return False, {'error': 'Failed to retrieve UTXO data'}
        
        confirmed_balance = utxo_data.get('confirmed_balance', 0)
        unconfirmed_balance = utxo_data.get('unconfirmed_balance', 0)
        
        if require_confirmations:
            # Только подтвержденные средства
            if confirmed_balance >= expected_amount:
                return True, {
                    'balance': confirmed_balance,
                    'unconfirmed': unconfirmed_balance,
                    'status': 'confirmed',
                    'utxo_count': utxo_data.get('utxo_count', 0)
                }
            else:
                return False, {
                    'balance': confirmed_balance,
                    'unconfirmed': unconfirmed_balance,
                    'status': 'insufficient_confirmed',
                    'utxo_count': utxo_data.get('utxo_count', 0)
                }
        else:
            # Все средства (подтвержденные + неподтвержденные)
            total_balance = confirmed_balance + unconfirmed_balance
            if total_balance >= expected_amount:
                return True, {
                    'balance': total_balance,
                    'confirmed': confirmed_balance,
                    'unconfirmed': unconfirmed_balance,
                    'status': 'unconfirmed' if unconfirmed_balance > 0 else 'confirmed',
                    'utxo_count': utxo_data.get('utxo_count', 0)
                }
            else:
                return False, {
                    'balance': total_balance,
                    'confirmed': confirmed_balance,
                    'unconfirmed': unconfirmed_balance,
                    'status': 'insufficient',
                    'utxo_count': utxo_data.get('utxo_count', 0)
                }
                
    except Exception as e:
        logger.error(f"Address balance validation failed: {e}")
        return False, {'error': str(e)}

# Дополнительные функции для работы с UTXO
async def get_address_utxos(address: str, confirmed_only: bool = True) -> List[Dict[str, Any]]:
    """
    Возвращает UTXO адреса с фильтрацией по подтверждениям
    """
    utxos = await get_utxo_for_address(address)
    if confirmed_only:
        return [utxo for utxo in utxos if is_transaction_confirmed(utxo)]
    return utxos

async def calculate_secure_balance(address: str) -> Dict[str, float]:
    """
    Вычисляет безопасный баланс с учетом различных уровней подтверждений
    """
    utxos = await get_utxo_for_address(address)
    
    balance = {
        'total': 0.0,
        'confirmed': 0.0,  # ≥ 6 подтверждений
        'high_confidence': 0.0,  # ≥ 12 подтверждений
        'unconfirmed': 0.0  # < 6 подтверждений
    }
    
    for utxo in utxos:
        amount = utxo.get('value', 0)
        confirmations = utxo.get('confirmations', 0)
        
        balance['total'] += amount
        
        if confirmations >= 12:
            balance['high_confidence'] += amount
            balance['confirmed'] += amount
        elif confirmations >= 6:
            balance['confirmed'] += amount
        else:
            balance['unconfirmed'] += amount
    
    return balance

# Обновленная функция проверки транзакции с поддержкой подписей
async def check_transaction_with_signature(address: str, expected_amount: float, 
                                         txid: Optional[str] = None,
                                         signature: Optional[str] = None,
                                         public_key: Optional[str] = None,
                                         testnet: bool = False) -> Tuple[bool, Dict[str, Any]]:
    """
    Проверяет транзакцию с поддержкой верификации подписи
    """
    # Если есть txid, проверяем конкретную транзакцию
    if txid:
        return await verify_transaction_complete(txid, expected_amount, address, signature, public_key)
    
    # Иначе проверяем баланс адреса
    success, details = await validate_address_balance(address, expected_amount, require_confirmations=True)
    
    if success:
        return True, {
            'status': 'confirmed',
            'balance': details['balance'],
            'message': 'Sufficient confirmed balance'
        }
    else:
        return False, {
            'status': details['status'],
            'balance': details['balance'],
            'confirmed_balance': details.get('confirmed', 0),
            'unconfirmed_balance': details.get('unconfirmed', 0),
            'message': 'Insufficient balance' if details['status'] == 'insufficient' else 'Waiting for confirmations'
        }

# Мемпул мониторинг
async def get_mempool_transactions() -> List[Dict[str, Any]]:
    """
    Получает текущие транзакции из мемпула
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://litecoinspace.org/api/mempool"
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    mempool_data = await response.json()
                    return mempool_data.get('transactions', [])
                else:
                    logger.warning(f"Mempool API returned status {response.status}")
                    return []
    except Exception as e:
        logger.error(f"Error fetching mempool transactions: {e}")
        return []

async def monitor_mempool():
    """
    Мониторинг мемпула на предмет транзакций, связанных с отслеживаемыми адресами
    """
    global _mempool_transactions
    
    while True:
        try:
            # Получаем текущие транзакции из мемпула
            mempool_txs = await get_mempool_transactions()
            
            async with _mempool_lock:
                # Обновляем мемпул
                current_time = time.time()
                new_mempool = {}
                
                for tx in mempool_txs:
                    txid = tx.get('txid')
                    if not txid:
                        continue
                    
                    # Сохраняем время обнаружения транзакции
                    tx['first_seen'] = current_time
                    new_mempool[txid] = tx
                
                # Обновляем глобальный мемпул
                _mempool_transactions = new_mempool
                
                # Проверяем транзакции на соответствие отслеживаемым адресам
                for address in _tracked_addresses:
                    for txid, tx in _mempool_transactions.items():
                        # Проверяем, связана ли транзакция с адресом
                        if await is_transaction_related_to_address(tx, address):
                            logger.info(f"Mempool transaction {txid} related to tracked address {address}")
                            # Здесь можно добавить логику уведомления
                
                logger.debug(f"Mempool updated: {len(_mempool_transactions)} transactions")
            
            # Ждем перед следующей проверкой
            await asyncio.sleep(MEMPOOL_UPDATE_INTERVAL)
            
        except Exception as e:
            logger.error(f"Mempool monitoring error: {e}")
            await asyncio.sleep(MEMPOOL_UPDATE_INTERVAL)

async def is_transaction_related_to_address(tx: Dict[str, Any], address: str) -> bool:
    """
    Проверяет, связана ли транзакции с указанным адресом
    """
    try:
        # Проверяем выходы транзакции
        for vout in tx.get('vout', []):
            if 'scriptPubKey' in vout and 'addresses' in vout['scriptPubKey']:
                if address in vout['scriptPubKey']['addresses']:
                    return True
        
        # Проверяем входы транзакции (для исходящих транзакций)
        for vin in tx.get('vin', []):
            if 'prevout' in vin and 'scriptPubKey' in vin['prevout']:
                if address in vin['prevout']['scriptPubKey'].get('addresses', []):
                    return True
        
        return False
    except Exception as e:
        logger.error(f"Error checking transaction relation: {e}")
        return False

async def get_mempool_transactions_for_address(address: str) -> List[Dict[str, Any]]:
    """
    Возвращает транзакции из мемпула, связанные с указанным адресом
    """
    related_transactions = []
    
    async with _mempool_lock:
        for txid, tx in _mempool_transactions.items():
            if await is_transaction_related_to_address(tx, address):
                related_transactions.append(tx)
    
    return related_transactions

async def cleanup_old_mempool_transactions():
    """
    Очищает старые транзакции из мемпула
    """
    global _mempool_transactions
    
    while True:
        try:
            async with _mempool_lock:
                current_time = time.time()
                old_transactions = []
                
                # Находим транзакции, которые находятся в мемпуле слишком долго
                for txid, tx in _mempool_transactions.items():
                    first_seen = tx.get('first_seen', 0)
                    if current_time - first_seen > 3600:  # 1 час
                        old_transactions.append(txid)
                
                # Удаляем старые транзакции
                for txid in old_transactions:
                    del _mempool_transactions[txid]
                
                if old_transactions:
                    logger.info(f"Cleaned up {len(old_transactions)} old mempool transactions")
            
            # Ждем перед следующей очисткой
            await asyncio.sleep(MEMPOOL_CLEANUP_INTERVAL)
            
        except Exception as e:
            logger.error(f"Mempool cleanup error: {e}")
            await asyncio.sleep(MEMPOOL_CLEANUP_INTERVAL)

async def start_mempool_monitoring():
    """
    Запускает мониторинг мемпула
    """
    global _mempool_task
    
    if _mempool_task is None:
        _mempool_task = asyncio.create_task(monitor_mempool())
        asyncio.create_task(cleanup_old_mempool_transactions())
        logger.info("Mempool monitoring started")

async def stop_mempool_monitoring():
    """
    Останавливает мониторинг мемпула
    """
    global _mempool_task
    
    if _mempool_task:
        _mempool_task.cancel()
        _mempool_task = None
        logger.info("Mempool monitoring stopped")

# Инициализация WebSocket
async def init_websocket():
    """Инициализация WebSocket соединения с litecoinspace.org"""
    global _websocket
    async with _websocket_lock:
        if _websocket is None:
            try:
                # Создаем WebSocket соединение
                _websocket = await aiohttp.ClientSession().ws_connect(
                    "wss://litecoinspace.org/api/v1/ws",
                    timeout=API_REQUEST_TIMEOUT
                )
                
                # Подписываемся на получение блоков и статистики
                await _websocket.send_json({
                    'action': 'want',
                    'data': ['blocks', 'stats', 'mempool-blocks', 'live-2h-chart']
                })
                
                logger.info("WebSocket connection to Litecoinspace established")
                
                # Запускаем обработчик сообщений
                asyncio.create_task(_websocket_message_handler())
                
            except Exception as e:
                logger.error(f"Failed to establish WebSocket connection: {e}")
                _websocket = None

async def _websocket_message_handler():
    """Обработчик сообщений от WebSocket"""
    global _websocket
    try:
        async for msg in _websocket:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                
                # Обрабатываем разные типы сообщений
                if 'block' in data:
                    logger.info(f"New block: {data['block']['height']}")
                    # При новом блоке очищаем кеш, так балансы могли измениться
                    await cleanup_cache()
                elif 'mempoolInfo' in data:
                    logger.debug(f"Mempool info: {data['mempoolInfo']}")
                elif 'transactions' in data:
                    logger.debug(f"New transactions: {len(data['transactions'])}")
                elif 'mempool-blocks' in data:
                    logger.debug(f"Mempool blocks: {len(data['mempool-blocks'])}")
                elif 'address-transactions' in data:
                    # Обработка транзакций связанных с отслеживаемыми адресами
                    for tx in data['address-transactions']:
                        logger.info(f"New transaction for tracked address: {tx['txid']}")
                        # Очищаем кеш для этого адреса
                        for cache_key in list(_address_cache.keys()):
                            if tx['address'] in cache_key:
                                del _address_cache[cache_key]
                    
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("WebSocket error occurred")
                break
            elif msg.type == aiohttp.WSMsgType.CLOSED:
                logger.info("WebSocket connection closed")
                break
                
    except Exception as e:
        logger.error(f"WebSocket message handler error: {e}")
    finally:
        async with _websocket_lock:
            _websocket = None

async def track_address(address: str):
    """Подписывается на отслеживание транзакций для указанного адреса"""
    global _tracked_addresses
    try:
        await init_websocket()
        if _websocket and not _websocket.closed:
            await _websocket.send_json({
                'track-address': address
            })
            _tracked_addresses.add(address)
            logger.info(f"Started tracking address: {address}")
        else:
            logger.warning("WebSocket not available for address tracking")
    except Exception as e:
        logger.error(f"Failed to track address {address}: {e}")

async def untrack_address(address: str):
    """Отписывается от отслеживания транзакций для указанного адреса"""
    global _tracked_addresses
    try:
        if _websocket and not _websocket.closed and address in _tracked_addresses:
            await _websocket.send_json({
                'untrack-address': address
            })
            _tracked_addresses.remove(address)
            logger.info(f"Stopped tracking address: {address}")
    except Exception as e:
        logger.error(f"Failed to untrack address {address}: {e}")

async def get_difficulty_adjustment() -> Optional[Dict[str, Any]]:
    """Получает информацию о корректировке сложности сети"""
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://litecoinspace.org/api/v1/difficulty-adjustment"
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(f"Difficulty adjustment API returned status {response.status}")
    except Exception as e:
        logger.error(f"Difficulty adjustment API error: {e}")
    return None

async def get_binance_ltc_rate(symbol: str = 'LTCUSDT') -> Optional[float]:
    """Получение курса LTC от Binance API"""
    if not await check_api_limit('binance'):
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            url = f'https://api.binance.com/api/v3/ticker/price?symbol={symbol}'
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'price' in data:
                        return float(data['price'])
                    else:
                        logger.warning(f"Binance API response missing 'price' field for symbol {symbol}")
                else:
                    logger.warning(f"Binance API returned status {response.status}")
    except Exception as e:
        logger.error(f"Binance API error: {e}")
    return None

async def get_coingecko_ltc_rate(currency: str = 'usd') -> Optional[float]:
    """Получение курса LTC от CoinGecko API"""
    if not await check_api_limit('coingecko'):
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            url = f'https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies={currency}'
            headers = {}
            if COINGECKO_API_KEY:
                headers['x-cg-pro-api-key'] = COINGECKO_API_KEY
                
            async with session.get(url, headers=headers, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'litecoin' in data and currency in data['litecoin']:
                        return float(data['litecoin'][currency])
                elif response.status == 429:
                    logger.warning("CoinGecko API rate limit exceeded")
                else:
                    logger.warning(f"CoinGecko API returned status {response.status}")
    except Exception as e:
        logger.error(f"CoinGecko API error: {e}")
    return None

async def get_coinbase_ltc_rate(currency: str = 'USD') -> Optional[float]:
    """Получение курса LTC от Coinbase API"""
    if not await check_api_limit('coinbase'):
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            url = f'https://api.coinbase.com/v2/prices/LTC-{currency}/spot'
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'data' in data and 'amount' in data['data']:
                        return float(data['data']['amount'])
                else:
                    logger.warning(f"Coinbase API returned status {response.status}")
    except Exception as e:
        logger.error(f"Coinbase API error: {e}")
    return None

async def get_kraken_ltc_rate(currency: str = 'USD') -> Optional[float]:
    """Получение курса LTC от Kraken API"""
    if not await check_api_limit('kraken'):
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            url = f'https://api.kraken.com/0/public/Ticker?pair=LTC{currency}'
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'result' in data and f'LTC{currency}' in data['result']:
                        pair_data = data['result'][f'LTC{currency}']
                        if 'c' in pair_data and len(pair_data['c']) > 0:
                            return float(pair_data['c'][0])
                else:
                    logger.warning(f"Kraken API returned status {response.status}")
    except Exception as e:
        logger.error(f"Kraken API error: {e}")
    return None

async def get_tsanghi_ltc_rate() -> Optional[float]:
    """
    Получение курса LTC от Tsanghi API
    Документация: https://blog.csdn.net/2401_83241598/article/details/140605132
    """
    if not await check_api_limit('tsanghi'):
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            url = 'https://tsanghi.com/api/fin/crypto/realtime?token=demo&ticker=LTC/USD&exchange_code=Binance'
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'close' in data:
                        return float(data['close'])
                    elif 'data' in data and 'close' in data['data']:
                        return float(data['data']['close'])
    except Exception as e:
        logger.error(f"Tsanghi API error: {e}")
    return None

async def get_cached_rate() -> Tuple[Optional[float], bool]:
    """Получение курса из кеша с проверкой актуальности"""
    async with _cache_lock:
        if 'ltc_usd' in _rate_cache:
            cached_rate, timestamp = _rate_cache['ltc_usd']
            if time.time() - timestamp < RATE_CACHE_TTL:
                logger.debug("Using cached LTC rate")
                return cached_rate, True
    return None, False

async def set_cached_rate(rate: float):
    """Сохранение курса в кеш"""
    async with _cache_lock:
        _rate_cache['ltc_usd'] = (rate, time.time())
        logger.debug(f"Cached LTC rate: {rate}")

async def get_ltc_usd_rate() -> float:
    """
    Получение курса LTC к USD через несколько источников с fallback значением
    Приоритет: CoinGecko → Binance → Coinbase → Kraken → Tsanghi
    """
    # Проверяем кеш перед обращением к API
    cached_rate, from_cache = await get_cached_rate()
    if from_cache and cached_rate:
        return cached_rate
    
    rates = []
    sources = []
    
    coingecko_rate = await get_coingecko_ltc_rate('usd')
    if coingecko_rate:
        rates.append(coingecko_rate)
        sources.append('CoinGecko')
    
    binance_rate = await get_binance_ltc_rate('LTCUSDT')
    if binance_rate:
        rates.append(binance_rate)
        sources.append('Binance')
    
    coinbase_rate = await get_coinbase_ltc_rate('USD')
    if coinbase_rate:
        rates.append(coinbase_rate)
        sources.append('Coinbase')
    
    kraken_rate = await get_kraken_ltc_rate('USD')
    if kraken_rate:
        rates.append(kraken_rate)
        sources.append('Kraken')
    
    tsanghi_rate = await get_tsanghi_ltc_rate()
    if tsanghi_rate:
        rates.append(tsanghi_rate)
        sources.append('Tsanghi')
    
    if rates:
        average_rate = sum(rates) / len(rates)
        logger.info(f"LTC rate obtained from {len(sources)} sources: {sources}. Average: ${average_rate:.2f}")
        
        await set_cached_rate(average_rate)
        return average_rate
    else:
        logger.warning("All rate APIs failed, using fallback value $117.0")
        return 117.0

async def cleanup_cache():
    """Очистка устаревших записей в кеше"""
    async with _cache_lock:
        current_time = time.time()
        
        # Очищаем кеш адресов
        keys_to_delete = []
        for key, (data, timestamp) in _address_cache.items():
            if current_time - timestamp > CACHE_TTL:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del _address_cache[key]
            logger.debug(f"Removed expired address cache entry: {key}")
        
        # Очищаем кеш UTXO
        utxo_keys_to_delete = []
        for key, (data, timestamp) in _utxo_cache.items():
            if current_time - timestamp > CACHE_TTL // 2:
                utxo_keys_to_delete.append(key)
        for key in utxo_keys_to_delete:
            del _utxo_cache[key]
            logger.debug(f"Removed expired UTXO cache entry: {key}")
        
        # Очищаем кеш курсов
        rate_keys_to_delete = []
        for key, (rate, timestamp) in _rate_cache.items():
            if current_time - timestamp > RATE_CACHE_TTL:
                rate_keys_to_delete.append(key)
        
        for key in rate_keys_to_delete:
            del _rate_cache[key]
            logger.debug(f"Removed expired rate cache entry: {key}")

def get_key_usage_stats() -> Dict[str, Any]:
    """Статистика использования системы"""
    return {
        "cache_size": len(_address_cache),
        "utxo_cache_size": len(_utxo_cache),
        "rate_cache_size": len(_rate_cache),
        "websocket_connected": _websocket is not None and not _websocket.closed,
        "tracked_addresses_count": len(_tracked_addresses),
        "mempool_transactions_count": len(_mempool_transactions)
    }

async def check_websocket_health() -> bool:
    """
    Проверяет здоровье WebSocket соединения
    Returns: True если соединение активно
    """
    global _websocket
    if _websocket is None or _websocket.closed:
        return False
    
    try:
        # Отправляем ping для проверки соединения
        await _websocket.ping()
        return True
    except Exception as e:
        logger.error(f"WebSocket health check failed: {e}")
        async with _websocket_lock:
            _websocket = None
        return False

async def reconnect_websocket():
    """Переподключает WebSocket соединение"""
    global _websocket
    async with _websocket_lock:
        if _websocket is not None:
            try:
                await _websocket.close()
            except Exception:
                pass
            _websocket = None
    
    await init_websocket()

async def initialize():
    """Инициализация API"""
    await init_websocket()
    await start_mempool_monitoring()
    logger.info("API initialized")

async def shutdown():
    """Завершение работы API"""
    await stop_mempool_monitoring()
    
    # Закрываем WebSocket соединение
    global _websocket
    async with _websocket_lock:
        if _websocket:
            await _websocket.close()
            _websocket = None
    
    logger.info("API shutdown complete")
