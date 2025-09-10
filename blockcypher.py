import os
import aiohttp
import asyncio
import logging
import time

# Настройка логирования
logger = logging.getLogger(__name__)

BLOCKCYPHER_TOKEN = os.getenv('BLOCKCYPHER_TOKEN', '')
BLOCKCYPHER_API_URL = os.getenv('BLOCKCYPHER_API_URL', 'https://api.blockcypher.com/v1/ltc/main')

# Кэш для курса LTC
ltc_rate_cache = None
ltc_rate_time = 0
CACHE_DURATION = 300  # 5 минут в секундах

async def generate_ltc_address():
    """Генерация нового LTC адреса через BlockCypher"""
    url = f'{BLOCKCYPHER_API_URL}/addrs'
    if BLOCKCYPHER_TOKEN:
        url += f'?token={BLOCKCYPHER_TOKEN}'
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url) as response:
                if response.status == 201:
                    data = await response.json()
                    return {
                        'address': data.get('address'),
                        'private': data.get('private'),
                        'public': data.get('public')
                    }
                else:
                    error = await response.text()
                    logger.error(f"BlockCypher error: {response.status} - {error}")
                    return None
    except Exception as e:
        logger.error(f"Error generating LTC address: {e}")
        return None

async def get_ltc_usd_rate():
    """Получение текущего курса LTC к USD через Binance"""
    global ltc_rate_cache, ltc_rate_time
    
    # Проверяем кэш
    current_time = time.time()
    if ltc_rate_cache and (current_time - ltc_rate_time) < CACHE_DURATION:
        return ltc_rate_cache
    
    url = 'https://api.binance.com/api/v3/ticker/price?symbol=LTCUSDT'
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    ltc_rate_cache = float(data['price'])
                    ltc_rate_time = current_time
                    return ltc_rate_cache
                else:
                    logger.error(f"Error getting LTC rate from Binance: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error getting LTC rate from Binance: {e}")
        
        # Если Binance не работает, пробуем CoinGecko
        url = 'https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd'
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        ltc_rate_cache = data['litecoin']['usd']
                        ltc_rate_time = current_time
                        return ltc_rate_cache
                    else:
                        logger.error(f"Error getting LTC rate from CoinGecko: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error getting LTC rate from CoinGecko: {e}")
            return None

async def check_address_balance(address):
    """Проверка баланса LTC адреса"""
    url = f'{BLOCKCYPHER_API_URL}/addrs/{address}/balance'
    if BLOCKCYPHER_TOKEN:
        url += f'?token={BLOCKCYPHER_TOKEN}'
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'balance': data.get('balance', 0),  # в сатоши
                        'unconfirmed_balance': data.get('unconfirmed_balance', 0),
                        'final_balance': data.get('final_balance', 0)
                    }
                else:
                    error = await response.text()
                    logger.error(f"BlockCypher balance error: {response.status} - {error}")
                    return None
    except Exception as e:
        logger.error(f"Error checking address balance: {e}")
        return None
