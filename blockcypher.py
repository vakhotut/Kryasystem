import os
import aiohttp
import asyncio
import logging

# Настройка логирования
logger = logging.getLogger(__name__)

BLOCKCYPHER_TOKEN = os.getenv('BLOCKCYPHER_TOKEN')
BLOCKCYPHER_API_URL = os.getenv('BLOCKCYPHER_API_URL', 'https://api.blockcypher.com/v1/ltc/main')

async def generate_ltc_address():
    """Генерация нового LTC адреса через BlockCypher"""
    url = f'{BLOCKCYPHER_API_URL}/addrs?token={BLOCKCYPHER_TOKEN}'
    
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
                    logger.error(f"BlockCypher error: {error}")
                    return None
    except Exception as e:
        logger.error(f"Error generating LTC address: {e}")
        return None

async def get_ltc_usd_rate():
    """Получение текущего курса LTC к USD"""
    url = 'https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd'
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['litecoin']['usd']
                else:
                    logger.error("Error getting LTC rate")
                    return None
    except Exception as e:
        logger.error(f"Error getting LTC rate: {e}")
        return None

async def check_address_balance(address):
    """Проверка баланса LTC адреса"""
    url = f'{BLOCKCYPHER_API_URL}/addrs/{address}/balance?token={BLOCKCYPHER_TOKEN}'
    
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
                    logger.error(f"BlockCypher balance error: {error}")
                    return None
    except Exception as e:
        logger.error(f"Error checking address balance: {e}")
        return None
