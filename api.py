import aiohttp
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple
import os
import time
import re
import json

logger = logging.getLogger(__name__)

CACHE_TTL = 60  # seconds
_rate_cache = {}
_address_cache = {}
_cache_lock = asyncio.Lock()
API_REQUEST_TIMEOUT = 10.0

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


async def check_transaction_litecoinspace(address: str, expected_amount: float, testnet: bool = False) -> Optional[Dict[str, Any]]:
    """
    Получает данные по адресу с litecoinspace.org (парсит публичную страницу)
    Возвращает словарь с полями: balance, received, transaction_count (если найдено)
    """
    if not await check_api_limit('litecoinspace'):
        return None

    base = 'https://litecoinspace.org/testnet' if testnet else 'https://litecoinspace.org'
    url = f"{base}/address/{address}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=API_REQUEST_TIMEOUT) as resp:
                if resp.status != 200:
                    logger.warning(f"LitecoinSpace returned status {resp.status} for {address}")
                    return None
                text = await resp.text()

        # Попытка вытащить значения: Total received и Balance
        # Примеры строк на странице: "Total received, ‎6.00980776 LTC." или "Balance, ‎6.00980776 LTC."
        # Используем нечувствительные к пробельным/неразрывным пробелам регулярки.
        nbsp = "\u00A0"
        # Общая регулярка для числа перед "LTC"
        num_pattern = r"([0-9]+(?:[.,][0-9]+)?)\s*LTC"

        received = None
        balance = None
        tx_count = None

        # Ищем "Total received"
        m_rec = re.search(r"Total\s*received\s*[,;:\-]*\s*[^0-9\n\r]*([0-9.,]+)\s*LTC", text, flags=re.IGNORECASE)
        if m_rec:
            received = float(m_rec.group(1).replace(',', ''))

        # Ищем "Balance"
        m_bal = re.search(r"Balance\s*[,;:\-]*\s*[^0-9\n\r]*([0-9.,]+)\s*LTC", text, flags=re.IGNORECASE)
        if m_bal:
            balance = float(m_bal.group(1).replace(',', ''))

        # Если не нашлось через ключевые слова, пробуем просто найти первое вхождение числа + LTC
        if received is None or balance is None:
            all_nums = re.findall(num_pattern, text, flags=re.IGNORECASE)
            # Если найдено хотя бы одно число перед LTC — используем первое как баланс/received fallback
            if all_nums:
                try:
                    val = float(all_nums[0].replace(',', ''))
                    if balance is None:
                        balance = val
                    if received is None:
                        received = val
                except Exception:
                    pass

        # Пытаемся найти паттерн "X of Y" — количество транзакций (индикация на странице)
        m_tx = re.search(r"(\d+)\s+of\s+(\d+)", text)
        if m_tx:
            try:
                tx_count = int(m_tx.group(2))
            except Exception:
                tx_count = None

        result = {
            'balance': balance if balance is not None else 0.0,
            'received': received if received is not None else 0.0,
            'transaction_count': tx_count if tx_count is not None else 0
        }

        return result

    except Exception as e:
        logger.error(f"LitecoinSpace API/parsing error for {address}: {e}")
        return None


async def check_ltc_transaction(address: str, expected_amount: float, testnet: bool = False) -> bool:
    """
    Основная функция проверки транзакции — теперь использует только litecoinspace.org
    Кеширует результаты в памяти на CACHE_TTL секунд.
    """
    try:
        cached_data, from_cache = await _get_cached_address_data(address, testnet)
        if from_cache and cached_data and cached_data.get('received', 0) >= expected_amount:
            logger.info(f"Transaction found in cache for address {address}")
            return True

        # Запрос к litecoinspace
        provider_data = await check_transaction_litecoinspace(address, expected_amount, testnet)
        if provider_data:
            received_amount = provider_data.get('received', 0)
            balance = provider_data.get('balance', 0)

            # Сохраняем в кеш
            await _set_cached_address_data(address, testnet, provider_data)

            if received_amount >= expected_amount or balance >= expected_amount:
                logger.info(f"Transaction found via litecoinspace: {provider_data}")
                return True

        logger.info("No transaction found with expected amount on litecoinspace")
        return False

    except Exception as e:
        logger.error(f"Error checking LTC transaction: {e}")
        return False


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


def get_key_usage_stats() -> Dict[str, Any]:
    """Минимальная статистика (удалены все внешние explorer-ключи)"""
    return {
        "cache_size": len(_address_cache),
        "rate_cache_size": len(_rate_cache)
    }
