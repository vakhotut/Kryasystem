# api.py
import aiohttp
import logging
from typing import Optional, Dict, Any
import os

logger = logging.getLogger(__name__)

# API ключи (можно вынести в конфигурацию)
BLOCKCHAIR_API_KEY = os.getenv('BLOCKCHAIR_API_KEY', '')
NOWNODES_API_KEY = os.getenv('NOWNODES_API_KEY', '')

async def get_ltc_usd_rate() -> float:
    """Получение курса LTC к USD с fallback значением"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'https://api.binance.com/api/v3/ticker/price?symbol=LTCUSDT',
                timeout=10
            ) as response:
                data = await response.json()
                if 'price' in data:
                    return float(data['price'])
                else:
                    logger.warning("Binance API response missing 'price' field")
                    return 117.0
    except Exception as e:
        logger.error(f"Error getting LTC rate: {e}")
        return 117.0

async def check_transaction_blockchair(address: str, amount: float) -> Optional[Dict[str, Any]]:
    """Проверка транзакции через Blockchair API"""
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

async def check_transaction_sochain(address: str, amount: float) -> Optional[Dict[str, Any]]:
    """Проверка транзакции через Sochain API"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://sochain.com/api/v2/get_address_balance/LTC/{address}"
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

async def check_transaction_nownodes(address: str, amount: float) -> Optional[Dict[str, Any]]:
    """Проверка транзакции через Nownodes API"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://ltc.nownodes.io/api/v2/address/{address}"
            headers = {'api-key': NOWNODES_API_KEY} if NOWNODES_API_KEY else {}
            
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'balance': float(data['balance']),
                        'transaction_count': data['txsCount'],
                        'received': float(data['totalReceived'])
                    }
    except Exception as e:
        logger.error(f"Nownodes API error: {e}")
    return None

async def check_ltc_transaction(address: str, expected_amount: float) -> bool:
    """
    Проверка LTC транзакции через несколько эксплореров
    Возвращает True если найдена транзакция с ожидаемой суммой
    """
    try:
        # Проверяем через Blockchair
        blockchair_data = await check_transaction_blockchair(address, expected_amount)
        if blockchair_data and blockchair_data['received'] >= expected_amount:
            return True

        # Проверяем через Sochain
        sochain_data = await check_transaction_sochain(address, expected_amount)
        if sochain_data and sochain_data['balance'] >= expected_amount:
            return True

        # Проверяем через Nownodes
        nownodes_data = await check_transaction_nownodes(address, expected_amount)
        if nownodes_data and nownodes_data['received'] >= expected_amount:
            return True

    except Exception as e:
        logger.error(f"Error checking LTC transaction: {e}")
    
    return False
