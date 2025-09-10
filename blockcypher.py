import os
import aiohttp
import asyncio
import logging
import time
import random

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
    """Получение текущего курса LTC к USD через различные источники"""
    global ltc_rate_cache, ltc_rate_time
    
    # Проверяем кэш
    current_time = time.time()
    if ltc_rate_cache and (current_time - ltc_rate_time) < CACHE_DURATION:
        return ltc_rate_cache
    
    # Список API для получения курса LTC (в порядке приоритета)
    apis = [
        # CoinGecko
        'https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd',
        # CryptoCompare
        'https://min-api.cryptocompare.com/data/price?fsym=LTC&tsyms=USD',
        # Coinbase
        'https://api.coinbase.com/v2/prices/LTC-USD/spot',
        # Kraken
        'https://api.kraken.com/0/public/Ticker?pair=LTCUSD',
    ]
    
    # Перемешиваем порядок API для распределения нагрузки
    random.shuffle(apis)
    
    for api_url in apis:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Обработка разных форматов ответов
                        if 'coingecko' in api_url:
                            rate = data['litecoin']['usd']
                        elif 'cryptocompare' in api_url:
                            rate = data['USD']
                        elif 'coinbase' in api_url:
                            rate = float(data['data']['amount'])
                        elif 'kraken' in api_url:
                            rate = float(data['result']['XLTCZUSD']['c'][0])
                        else:
                            continue
                            
                        ltc_rate_cache = rate
                        ltc_rate_time = current_time
                        logger.info(f"Successfully got LTC rate from {api_url}: {rate}")
                        return rate
        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting LTC rate from {api_url}")
            continue
        except Exception as e:
            logger.warning(f"Error getting LTC rate from {api_url}: {e}")
            continue
    
    # Если все API не сработали, используем фиксированный курс как запасной вариант
    logger.error("All LTC rate APIs failed, using fallback rate")
    ltc_rate_cache = 70.0  # Примерный курс как запасной вариант
    ltc_rate_time = current_time
    return ltc_rate_cache

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
