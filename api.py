import aiohttp
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple, List
import os
import time
import random
from datetime import datetime, timedelta
import json
import hashlib
import base58

logger = logging.getLogger(__name__)

# API ключи для ротации
NOWNODES_API_KEYS = os.getenv('NOWNODES_API_KEYS', '').split(',')
BLOCKCYPHER_API_KEYS = os.getenv('BLOCKCYPHER_API_KEYS', '').split(',')
BLOCKCHAIR_API_KEY = os.getenv('BLOCKCHAIR_API_KEY', '')
COINGECKO_API_KEY = os.getenv('COINGECKO_API_KEY', '')
TESTNET = os.getenv('TESTNET', 'False').lower() == 'true'

# Electrum LTC серверы (mainnet и testnet)
ELECTRUM_SERVERS_MAINNET = [
    "electrum-ltc.bysh.me:50002",
    "electrum.ltc.xurious.com:50002",
    "ltc.rentonisk.com:50002",
    "electrum.ltc.rentonisk.com:50002"
]

ELECTRUM_SERVERS_TESTNET = [
    "electrum-ltc.bysh.me:51002",
    "testnet.ltc.rentonisk.com:51002"
]

# Убедимся, что пустые ключи отфильтрованы
NOWNODES_API_KEYS = [key.strip() for key in NOWNODES_API_KEYS if key.strip()]
BLOCKCYPHER_API_KEYS = [key.strip() for key in BLOCKCYPHER_API_KEYS if key.strip()]

# Кеш для хранения результатов проверки адресов и курсов
_address_cache = {}
_rate_cache = {}
_cache_lock = asyncio.Lock()
CACHE_TTL = 60  # Время жизни кеша в секундах (1 минута)
RATE_CACHE_TTL = 30  # Время жизни кеша курсов в секундах

# Счетчики для ротации ключей
_nownodes_key_index = 0
_blockcypher_key_index = 0
_electrum_server_index = 0
_key_rotation_lock = asyncio.Lock()

# Импортируем функции для работы с API лимитами
from db import increment_api_request, get_api_limits

class ElectrumClient:
    """Клиент для работы с Electrum LTC сервером"""
    
    def __init__(self, testnet=False):
        self.testnet = testnet
        self.servers = ELECTRUM_SERVERS_TESTNET if testnet else ELECTRUM_SERVERS_MAINNET
        self.current_server = None
        self.reader = None
        self.writer = None
        self.request_id = 0
        
    async def connect(self):
        """Подключение к Electrum серверу"""
        global _electrum_server_index
        
        if self.servers:
            async with _key_rotation_lock:
                server = self.servers[_electrum_server_index]
                _electrum_server_index = (_electrum_server_index + 1) % len(self.servers)
            
            try:
                host, port = server.split(':')
                self.reader, self.writer = await asyncio.open_connection(host, int(port))
                self.current_server = server
                
                # Отправляем версионный запрос для подтверждения подключения
                version_response = await self._request("server.version", ["electrum-ltc-client", "1.4"])
                logger.debug(f"Connected to Electrum server {server}, version: {version_response}")
                
                return True
            except Exception as e:
                logger.error(f"Failed to connect to Electrum server {server}: {e}")
                return False
        return False
    
    async def _request(self, method, params):
        """Отправка запроса к Electrum серверу"""
        if not self.reader or not self.writer:
            if not await self.connect():
                raise ConnectionError("Failed to connect to Electrum server")
        
        self.request_id += 1
        request = {
            "id": self.request_id,
            "method": method,
            "params": params
        }
        
        try:
            # Отправляем запрос
            self.writer.write((json.dumps(request) + '\n').encode())
            await self.writer.drain()
            
            # Читаем ответ
            response_data = await self.reader.readline()
            if not response_data:
                raise ConnectionError("No data received from Electrum server")
            
            response = json.loads(response_data.decode().strip())
            
            if 'error' in response and response['error']:
                raise Exception(f"Electrum error: {response['error']}")
            
            return response.get('result')
        except Exception as e:
            logger.error(f"Electrum request failed: {e}")
            # Пробуем переподключиться к другому серверу
            if await self.connect():
                return await self._request(method, params)
            raise
    
    async def get_balance(self, address):
        """Получение баланса адреса"""
        try:
            script_hash = await self._get_script_hash(address)
            balance = await self._request("blockchain.scripthash.get_balance", [script_hash])
            return balance
        except Exception as e:
            logger.error(f"Failed to get balance for {address}: {e}")
            return None
    
    async def get_history(self, address):
        """Получение истории транзакций адреса"""
        try:
            script_hash = await self._get_script_hash(address)
            history = await self._request("blockchain.scripthash.get_history", [script_hash])
            return history
        except Exception as e:
            logger.error(f"Failed to get history for {address}: {e}")
            return None
    
    async def get_transaction(self, tx_hash):
        """Получение данных о транзакции"""
        try:
            transaction = await self._request("blockchain.transaction.get", [tx_hash, True])
            return transaction
        except Exception as e:
            logger.error(f"Failed to get transaction {tx_hash}: {e}")
            return None
    
    async def _get_script_hash(self, address):
        """Получение script hash для адреса"""
        try:
            # Для Electrum нам нужно преобразовать адрес в script hash
            # Декодируем LTC адрес
            decoded = base58.b58decode_check(address)
            
            # Определяем версионный байт
            version_byte = decoded[0]
            
            # Берем хэш от публичного ключа (опускаем версионный байт)
            pubkey_hash = decoded[1:]
            
            # Создаем скрипт P2PKH
            script = bytes([0x76, 0xa9, 0x14]) + pubkey_hash + bytes([0x88, 0xac])
            
            # Вычисляем хэш скрипта
            script_hash = hashlib.sha256(script).digest()
            
            # Electrum использует little-endian
            return script_hash[::-1].hex()
        except Exception as e:
            logger.error(f"Failed to get script hash for {address}: {e}")
            return None
    
    async def close(self):
        """Закрытие соединения"""
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

async def check_api_limit(api_name):
    """Проверяет лимиты API и возвращает True если можно сделать запрос"""
    try:
        api_limits = await get_api_limits()
        for limit in api_limits:
            if limit['api_name'] == api_name:
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

async def check_transaction_electrum(address: str, amount: float, testnet: bool = TESTNET) -> Optional[Dict[str, Any]]:
    """Проверка транзакции через Electrum LTC сервер"""
    if not await check_api_limit('electrum'):
        return None
        
    client = ElectrumClient(testnet=testnet)
    try:
        if await client.connect():
            balance = await client.get_balance(address)
            
            if balance:
                confirmed_balance = balance.get('confirmed', 0) / 10**8  # Конвертируем из сатоши
                unconfirmed_balance = balance.get('unconfirmed', 0) / 10**8
                
                # Получаем историю для расчета общей полученной суммы
                history = await client.get_history(address)
                total_received = 0
                
                if history:
                    # Для каждой подтвержденной транзакции получаем детали
                    for tx in history:
                        if tx.get('height', 0) > 0:  # Только подтвержденные транзакции
                            tx_details = await client.get_transaction(tx['tx_hash'])
                            if tx_details and 'vout' in tx_details:
                                for output in tx_details['vout']:
                                    if 'scriptPubKey' in output and 'addresses' in output['scriptPubKey']:
                                        if address in output['scriptPubKey']['addresses']:
                                            total_received += output.get('value', 0)
                
                return {
                    'balance': confirmed_balance,
                    'received': total_received,
                    'transaction_count': len(history) if history else 0,
                    'unconfirmed_balance': unconfirmed_balance
                }
    except Exception as e:
        logger.error(f"Electrum API error: {e}")
    finally:
        await client.close()
    return None

async def get_binance_ltc_rate(symbol: str = 'LTCUSDT') -> Optional[float]:
    """Получение курса LTC от Binance API"""
    if not await check_api_limit('binance'):
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            url = f'https://api.binance.com/api/v3/ticker/price?symbol={symbol}'
            async with session.get(url, timeout=5) as response:
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
                
            async with session.get(url, headers=headers, timeout=5) as response:
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
            async with session.get(url, timeout=5) as response:
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
            async with session.get(url, timeout=5) as response:
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
            async with session.get(url, timeout=5) as response:
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
    
    # Пробуем получить курс из разных источников
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
        # Вычисляем среднее значение из доступных курсов
        average_rate = sum(rates) / len(rates)
        logger.info(f"LTC rate obtained from {len(sources)} sources: {sources}. Average: ${average_rate:.2f}")
        
        # Сохраняем в кеш
        await set_cached_rate(average_rate)
        return average_rate
    else:
        logger.warning("All rate APIs failed, using fallback value $117.0")
        return 117.0

async def check_transaction_blockchair(address: str, amount: float, testnet: bool = TESTNET) -> Optional[Dict[str, Any]]:
    """Проверка транзакции через Blockchair API"""
    if testnet:
        logger.info("Blockchair не поддерживает тестовую сеть LTC")
        return None
        
    if not await check_api_limit('blockchair'):
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.blockchair.com/litecoin/dashboards/address/{address}"
            if BLOCKCHAIR_API_KEY:
                url += f"?key={BLOCKCHAIR_API_KEY}"
                
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('data'):
                        address_data = data['data'][address]
                        return {
                            'balance': address_data['address']['balance'],
                            'transaction_count': address_data['address']['transaction_count'],
                            'received': address_data['address']['received']
                        }
    except Exception as e:
        logger.error(f"Blockchair API error: {e}")
    return None

async def check_transaction_sochain(address: str, amount: float, testnet: bool = TESTNET) -> Optional[Dict[str, Any]]:
    """Проверка транзакции через Sochain API"""
    if not await check_api_limit('sochain'):
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            network = 'LTCTEST' if testnet else 'LTC'
            url = f"https://sochain.com/api/v2/get_address_balance/{network}/{address}"
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['status'] == 'success':
                        return {
                            'balance': float(data['data']['confirmed_balance']),
                            'pending_balance': float(data['data']['unconfirmed_balance'])
                        }
    except Exception as e:
        logger.error(f"Sochain API error: {e}")
    return None

async def get_next_nownodes_key() -> str:
    """Получение следующего ключа Nownodes для ротации"""
    global _nownodes_key_index
    async with _key_rotation_lock:
        if not NOWNODES_API_KEYS:
            return ""
        
        key = NOWNODES_API_KEYS[_nownodes_key_index]
        _nownodes_key_index = (_nownodes_key_index + 1) % len(NOWNODES_API_KEYS)
        return key

async def check_transaction_nownodes(address: str, amount: float, testnet: bool = TESTNET) -> Optional[Dict[str, Any]]:
    """Проверка транзакции через Nownodes API с ротацией ключей"""
    if not await check_api_limit('nownodes'):
        return None
        
    try:
        api_key = await get_next_nownodes_key()
        async with aiohttp.ClientSession() as session:
            # Используем разные хосты для mainnet и testnet
            host = 'tltc.nownodes.io' if testnet else 'ltc.nownodes.io'
            url = f"https://{host}/api/v2/address/{address}"
            headers = {'api-key': api_key} if api_key else {}
            
            logger.debug(f"Using Nownodes key: {api_key[:5]}... for address: {address}")
            
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'balance': float(data['balance']),
                        'transaction_count': data['txsCount'],
                        'received': float(data['totalReceived'])
                    }
                elif response.status == 429:
                    logger.warning(f"Nownodes rate limit exceeded with key: {api_key[:5]}...")
                else:
                    logger.warning(f"Nownodes API returned status {response.status} with key: {api_key[:5]}...")
    except Exception as e:
        logger.error(f"Nownodes API error: {e}")
    return None

async def get_next_blockcypher_key() -> str:
    """Получение следующего ключа BlockCypher для ротации"""
    global _blockcypher_key_index
    async with _key_rotation_lock:
        if not BLOCKCYPHER_API_KEYS:
            return ""
        
        key = BLOCKCYPHER_API_KEYS[_blockcypher_key_index]
        _blockcypher_key_index = (_blockcypher_key_index + 1) % len(BLOCKCYPHER_API_KEYS)
        return key

async def check_transaction_blockcypher(address: str, amount: float, testnet: bool = TESTNET) -> Optional[Dict[str, Any]]:
    """
    Проверка транзакции через BlockCypher API с ротацией ключей
    Документация: https://www.blockcypher.com/dev/ 
    """
    if not await check_api_limit('blockcypher'):
        return None
        
    try:
        api_key = await get_next_blockcypher_key()
        async with aiohttp.ClientSession() as session:
            # Определяем сеть для BlockCypher API
            network = 'testnet' if testnet else 'main'
            url = f"https://api.blockcypher.com/v1/ltc/{network}/addrs/{address}"
            
            # Добавляем API ключ если доступен
            params = {}
            if api_key:
                params['token'] = api_key
            
            logger.debug(f"Using BlockCypher key: {api_key[:5]}... for address: {address}")
            
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'balance': float(data['balance']),
                        'transaction_count': data['n_tx'],
                        'received': float(data['total_received']),
                        'unconfirmed_balance': float(data['unconfirmed_balance'])
                    }
                elif response.status == 429:
                    logger.warning(f"BlockCypher rate limit exceeded with key: {api_key[:5]}...")
                else:
                    logger.warning(f"BlockCypher API returned status {response.status} with key: {api_key[:5]}...")
    except Exception as e:
        logger.error(f"BlockCypher API error: {e}")
    return None

async def _get_cached_address_data(address: str, testnet: bool) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Получение данных адреса из кеша с проверкой актуальности"""
    async with _cache_lock:
        cache_key = f"{address}_{testnet}"
        if cache_key in _address_cache:
            cached_data, timestamp = _address_cache[cache_key]
            # Проверяем, не устарели ли данные
            if time.time() - timestamp < CACHE_TTL:
                logger.debug(f"Using cached data for address {address}")
                return cached_data, True
            else:
                # Удаляем устаревшие данные
                del _address_cache[cache_key]
    
    return None, False

async def _set_cached_address_data(address: str, testnet: bool, data: Dict[str, Any]):
    """Сохранение данных адреса в кеш"""
    async with _cache_lock:
        cache_key = f"{address}_{testnet}"
        _address_cache[cache_key] = (data, time.time())
        logger.debug(f"Cached data for address {address}")

async def check_ltc_transaction(address: str, expected_amount: float, testnet: bool = TESTNET) -> bool:
    """
    Проверка LTC транзакции через несколько эксплореров
    Возвращает True если найдена транзакция с ожидаемой суммой
    """
    try:
        # Проверяем кеш перед обращением к API
        cached_data, from_cache = await _get_cached_address_data(address, testnet)
        if from_cache and cached_data and cached_data.get('received', 0) >= expected_amount:
            logger.info(f"Transaction found in cache for address {address}")
            return True
        
        # Если в кеше нет данных или сумма недостаточна, обращаемся к API
        results = []
        
        # Проверяем через Blockchair (только для mainnet)
        if not testnet:
            blockchair_data = await check_transaction_blockchair(address, expected_amount, testnet)
            results.append(('blockchair', blockchair_data))
            if blockchair_data and blockchair_data['received'] >= expected_amount:
                logger.info(f"Transaction found via Blockchair: {blockchair_data}")
                # Сохраняем в кеш
                await _set_cached_address_data(address, testnet, blockchair_data)
                return True

        # Проверяем через Sochain
        sochain_data = await check_transaction_sochain(address, expected_amount, testnet)
        results.append(('sochain', sochain_data))
        if sochain_data and sochain_data['balance'] >= expected_amount:
            logger.info(f"Transaction found via Sochain: {sochain_data}")
            # Сохраняем в кеш
            await _set_cached_address_data(address, testnet, {
                'received': sochain_data['balance'],
                'balance': sochain_data['balance'],
                'transaction_count': 0  # Sochain не предоставляет это поле
            })
            return True

        # Проверяем через Nownodes (с ротацией ключей)
        nownodes_data = await check_transaction_nownodes(address, expected_amount, testnet)
        results.append(('nownodes', nownodes_data))
        if nownodes_data and nownodes_data['received'] >= expected_amount:
            logger.info(f"Transaction found via Nownodes: {nownodes_data}")
            # Сохраняем в кеш
            await _set_cached_address_data(address, testnet, nownodes_data)
            return True

        # Проверяем через BlockCypher (с ротацией ключей)
        blockcypher_data = await check_transaction_blockcypher(address, expected_amount, testnet)
        results.append(('blockcypher', blockcypher_data))
        if blockcypher_data and blockcypher_data['received'] >= expected_amount:
            logger.info(f"Transaction found via BlockCypher: {blockcypher_data}")
            # Сохраняем в кеш
            await _set_cached_address_data(address, testnet, blockcypher_data)
            return True

        # Проверяем через Electrum LTC сервер
        electrum_data = await check_transaction_electrum(address, expected_amount, testnet)
        results.append(('electrum', electrum_data))
        if electrum_data and electrum_data['received'] >= expected_amount:
            logger.info(f"Transaction found via Electrum: {electrum_data}")
            # Сохраняем в кеш
            await _set_cached_address_data(address, testnet, electrum_data)
            return True

        # Если транзакция не найдена, но мы получили данные от какого-либо провайдера,
        # сохраняем их в кеш для будущих проверок
        for provider_name, data in results:
            if data:
                await _set_cached_address_data(address, testnet, data)
                break  # Сохраняем данные только от одного провайдера

    except Exception as e:
        logger.error(f"Error checking LTC transaction: {e}")
    
    logger.info("No transaction found with expected amount")
    return False

# Функция для очистки устаревших записей в кеше (можно запускать периодически)
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

# Функция для получения статистики использования ключей
def get_key_usage_stats() -> Dict[str, Any]:
    """Получение статистики использования API ключей"""
    return {
        "nownodes_keys_count": len(NOWNODES_API_KEYS),
        "nownodes_current_index": _nownodes_key_index,
        "blockcypher_keys_count": len(BLOCKCYPHER_API_KEYS),
        "blockcypher_current_index": _blockcypher_key_index,
        "electrum_servers_mainnet": len(ELECTRUM_SERVERS_MAINNET),
        "electrum_servers_testnet": len(ELECTRUM_SERVERS_TESTNET),
        "electrum_current_index": _electrum_server_index,
        "cache_size": len(_address_cache),
        "rate_cache_size": len(_rate_cache)
                }
