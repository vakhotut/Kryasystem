import aiohttp
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple
import os
import time
import json

logger = logging.getLogger(__name__)

API_REQUEST_TIMEOUT = 10.0
CACHE_TTL = 60  # Время жизни кеша в секундах

# API ключи
COINGECKO_API_KEY = os.getenv('COINGECKO_API_KEY', '')
TESTNET = os.getenv('TESTNET', 'False').lower() == 'true'

# Кеши
_rate_cache = {}
_cache_lock = asyncio.Lock()
RATE_CACHE_TTL = 30  # Время жизни кеша курсов в секундах

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
    """Очистка устаревших записей в кеше курсов"""
    async with _cache_lock:
        current_time = time.time()
        rate_keys_to_delete = []
        for key, (rate, timestamp) in _rate_cache.items():
            if current_time - timestamp > RATE_CACHE_TTL:
                rate_keys_to_delete.append(key)
        
        for key in rate_keys_to_delete:
            del _rate_cache[key]
            logger.debug(f"Removed expired rate cache entry for key: {key}")

def get_key_usage_stats() -> Dict[str, Any]:
    """Статистика использования API ключей и кешей"""
    return {
        "rate_cache_size": len(_rate_cache)
                        }
