import aiohttp
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple, List
import os
import time
import json
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

API_REQUEST_TIMEOUT = 10.0
CACHE_TTL = 60  # Время жизни кеша в секундах

# API ключи
COINGECKO_API_KEY = os.getenv('COINGECKO_API_KEY', '')
TESTNET = os.getenv('TESTNET', 'False').lower() == 'true'

# Кеши
_rate_cache = {}
_address_cache = {}
_cache_lock = asyncio.Lock()
RATE_CACHE_TTL = 30  # Время жизни кеша курсов в секундах

# WebSocket соединение
_websocket = None
_websocket_lock = asyncio.Lock()
_tracked_addresses = set()

# DB helpers (assumed to exist in your project)
from db import increment_api_request, get_api_limits

async def check_api_limit(api_name: str) -> bool:
    """Проверяет лимиты API и возвращает True если можно сделать запрос"""
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
        logger.warning(f"No API limit found for {api_name}, allowing request")
        return True
    except Exception as e:
        logger.error(f"Error checking API limit for {api_name}: {e}")
        return True

async def _get_cached_address_data(address: str, testnet: bool) -> Tuple[Optional[Dict[str, Any]], bool]:
    async with _cache_lock:
        cache_key = f"{address}_{testnet}"
        if cache_key in _address_cache:
            cached_data, timestamp = _address_cache[cache_key]
            if time.time() - timestamp < CACHE_TTL:
                logger.debug(f"Using cached data for address {address}")
                return cached_data, True
            else:
                del _address_cache[cache_key]
    return None, False

async def _set_cached_address_data(address: str, testnet: bool, data: Dict[str, Any]):
    async with _cache_lock:
        cache_key = f"{address}_{testnet}"
        _address_cache[cache_key] = (data, time.time())
        logger.debug(f"Cached data for address {address}")

async def check_transaction_litecoinspace_api(address: str, expected_amount: float, testnet: bool = False) -> Optional[Dict[str, Any]]:
    """
    Получает данные по адресу через официальный API litecoinspace.org
    Возвращает словарь с полями: balance, received, transaction_count
    """
    if not await check_api_limit('litecoinspace'):
        return None
        
    if testnet:
        logger.warning("Litecoinspace.org does not support testnet")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            # Получаем историю транзакций
            url = f"https://litecoinspace.org/api/address/{address}/txs"
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    transactions = await response.json()
                    
                    # Вычисляем общую полученную сумму
                    total_received = 0
                    confirmed_txs = 0
                    
                    for tx in transactions:
                        if tx.get('status', {}).get('confirmed', False):
                            confirmed_txs += 1
                            for vout in tx.get('vout', []):
                                if 'scriptPubKey' in vout and 'addresses' in vout['scriptPubKey']:
                                    if address in vout['scriptPubKey']['addresses']:
                                        total_received += vout.get('value', 0)
                    
                    # Получаем информацию о difficulty adjustment для дополнительных данных
                    difficulty_url = "https://litecoinspace.org/api/v1/difficulty-adjustment"
                    async with session.get(difficulty_url, timeout=API_REQUEST_TIMEOUT) as diff_response:
                        difficulty_data = await diff_response.json() if diff_response.status == 200 else {}
                    
                    return {
                        'received': total_received,
                        'transaction_count': confirmed_txs,
                        'difficulty_data': difficulty_data,
                        'source': 'litecoinspace_api'
                    }
                else:
                    logger.warning(f"Litecoinspace API returned status {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Litecoinspace API error: {e}")
        return None

async def check_transaction_litecoinspace_html(address: str, expected_amount: float, testnet: bool = False) -> Optional[Dict[str, Any]]:
    """
    Получает данные по адресу с litecoinspace.org (парсит публичную страницу)
    Используется как fallback, если API не работает
    """
    if testnet:
        logger.warning("Litecoinspace.org does not support testnet")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://litecoinspace.org/address/{address}"
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    html = await response.text()
                    
                    # Используем BeautifulSoup для парсинга HTML
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Ищем элементы с данными
                    balance_element = soup.find('span', {'class': 'final-balance'})
                    received_element = soup.find('span', {'class': 'total-received'})
                    txs_element = soup.find('span', {'class': 'transaction-count'})
                    
                    # Альтернативный поиск, если классы изменились
                    if not balance_element:
                        balance_td = soup.find('td', text='Final Balance')
                        balance_element = balance_td.find_next_sibling('td') if balance_td else None
                    if not received_element:
                        received_td = soup.find('td', text='Total Received')
                        received_element = received_td.find_next_sibling('td') if received_td else None
                    if not txs_element:
                        txs_td = soup.find('td', text='No. Transactions')
                        txs_element = txs_td.find_next_sibling('td') if txs_td else None
                    
                    balance = 0.0
                    received = 0.0
                    txs_count = 0
                    
                    if balance_element:
                        balance_text = balance_element.get_text().strip().replace('LTC', '').strip()
                        try:
                            balance = float(balance_text) if balance_text else 0.0
                        except ValueError:
                            balance = 0.0
                    
                    if received_element:
                        received_text = received_element.get_text().strip().replace('LTC', '').strip()
                        try:
                            received = float(received_text) if received_text else 0.0
                        except ValueError:
                            received = 0.0
                    
                    if txs_element:
                        txs_text = txs_element.get_text().strip()
                        try:
                            txs_count = int(txs_text) if txs_text.isdigit() else 0
                        except ValueError:
                            txs_count = 0
                    
                    return {
                        'balance': balance,
                        'received': received,
                        'transaction_count': txs_count,
                        'source': 'litecoinspace_html'
                    }
                else:
                    logger.error(f"Litecoinspace.org returned status {response.status}")
    except Exception as e:
        logger.error(f"Litecoinspace.org HTML parsing error: {e}")
    return None

async def check_ltc_transaction(address: str, expected_amount: float, testnet: bool = False) -> bool:
    """
    Основная функция проверки транзакции
    Сначала пытается использовать API, затем fallback на парсинг HTML
    """
    try:
        cached_data, from_cache = await _get_cached_address_data(address, testnet)
        if from_cache and cached_data and cached_data.get('received', 0) >= expected_amount:
            logger.info(f"Transaction found in cache for address {address}")
            return True

        # Сначала пробуем API
        api_data = await check_transaction_litecoinspace_api(address, expected_amount, testnet)
        if api_data and api_data.get('received', 0) >= expected_amount:
            logger.info(f"Transaction found via Litecoinspace API: {api_data}")
            await _set_cached_address_data(address, testnet, api_data)
            return True
        
        # Если API не сработал или сумма недостаточна, пробуем парсинг HTML
        html_data = await check_transaction_litecoinspace_html(address, expected_amount, testnet)
        if html_data and html_data.get('received', 0) >= expected_amount:
            logger.info(f"Transaction found via Litecoinspace HTML: {html_data}")
            await _set_cached_address_data(address, testnet, html_data)
            return True
            
        logger.info("No transaction found with expected amount")
        return False
    except Exception as e:
        logger.error(f"Error checking LTC transaction: {e}")
    
    logger.info("No transaction found with expected amount")
    return False

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
                elif 'block-transactions' in data:
                    # Обработка подтвержденных транзакций
                    for tx in data['block-transactions']:
                        logger.info(f"Block confirmed transaction: {tx['txid']}")
                    
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("WebSocket error occurred")
                break
            elif msg.type == aiohttp.WSMsgType.CLOSED:
                logger.info("WebSocket connection closed")
                break
                
    except Exception as e:
        logger.error(f"WebSocket message handler error: {e}")
    finally:
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
        keys_to_delete = []
        for key, (data, timestamp) in _address_cache.items():
            if current_time - timestamp > CACHE_TTL:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del _address_cache[key]
            logger.debug(f"Removed expired cache entry for key: {key}")
        
        # Также очищаем кеш курсов
        rate_keys_to_delete = []
        for key, (rate, timestamp) in _rate_cache.items():
            if current_time - timestamp > RATE_CACHE_TTL:
                rate_keys_to_delete.append(key)
        
        for key in rate_keys_to_delete:
            del _rate_cache[key]
            logger.debug(f"Removed expired rate cache entry for key: {key}")

def get_key_usage_stats() -> Dict[str, Any]:
    """Минимальная статистика"""
    return {
        "cache_size": len(_address_cache),
        "rate_cache_size": len(_rate_cache),
        "websocket_connected": _websocket is not None and not _websocket.closed,
        "tracked_addresses_count": len(_tracked_addresses)
    }

# Инициализация WebSocket при импорте модуля
asyncio.create_task(init_websocket())
