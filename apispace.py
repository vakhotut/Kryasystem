# apispace.py
import aiohttp
import asyncio
import logging
import time
from typing import Dict, List, Any, Optional, Tuple
from decimal import Decimal
import json
from datetime import datetime

logger = logging.getLogger(__name__)

# Базовые URL для LitecoinSpace API
LITECOINSPACE_MAINNET_API = "https://litecoinspace.org/api"
LITECOINSPACE_TESTNET_API = "https://litecoinspace.org/testnet/api"

# Глобальные переменные для кэширования
_address_cache = {}
_tx_cache = {}
_utxo_cache = {}
_last_cache_cleanup = time.time()
_last_rate_update = 0
_cached_ltc_rate = 50.0  # Fallback value

class LitecoinSpaceAPI:
    def __init__(self, network='mainnet'):
        self.network = network
        self.base_url = LITECOINSPACE_MAINNET_API if network == 'mainnet' else LITECOINSPACE_TESTNET_API
        self.session = None
        
    async def init_session(self):
        """Инициализация aiohttp сессии"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
            
    async def close_session(self):
        """Закрытие aiohttp сессии"""
        if self.session:
            await self.session.close()
            self.session = None
            
    async def _make_request(self, endpoint):
        """Базовый метод для выполнения запросов к API"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            await self.init_session()
            async with self.session.get(url, timeout=30) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    logger.warning(f"API endpoint not found: {url}")
                    return None
                else:
                    logger.error(f"API request failed: {url}, status: {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.error(f"API request timeout: {url}")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"API client error: {url}, error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in API request: {url}, error: {e}")
            return None
            
    async def get_address_info(self, address: str) -> Optional[Dict]:
        """Получение информации об адресе"""
        cache_key = f"address_{address}"
        if cache_key in _address_cache:
            return _address_cache[cache_key]
            
        endpoint = f"/address/{address}"
        data = await self._make_request(endpoint)
        
        if data:
            _address_cache[cache_key] = data
            return data
        return None
        
    async def get_address_utxo(self, address: str) -> Optional[List]:
        """Получение UTXO для адреса"""
        cache_key = f"utxo_{address}"
        if cache_key in _utxo_cache:
            return _utxo_cache[cache_key]
            
        endpoint = f"/address/{address}/utxo"
        data = await self._make_request(endpoint)
        
        if data:
            _utxo_cache[cache_key] = data
            return data
        return None
        
    async def get_transaction(self, txid: str) -> Optional[Dict]:
        """Получение информации о транзакции"""
        cache_key = f"tx_{txid}"
        if cache_key in _tx_cache:
            return _tx_cache[cache_key]
            
        endpoint = f"/tx/{txid}"
        data = await self._make_request(endpoint)
        
        if data:
            _tx_cache[cache_key] = data
            return data
        return None
        
    async def get_transaction_status(self, txid: str) -> Optional[Dict]:
        """Получение статуса транзакции"""
        endpoint = f"/tx/{txid}/status"
        return await self._make_request(endpoint)
        
    async def get_address_transactions(self, address: str, limit=50) -> Optional[List]:
        """Получение истории транзакций адреса"""
        endpoint = f"/address/{address}/txs"
        if limit:
            endpoint += f"?limit={limit}"
        return await self._make_request(endpoint)
        
    async def check_payment(self, address: str, expected_amount: float) -> Dict[str, Any]:
        """
        Проверка поступления платежа на адрес
        
        Returns:
            Dict с ключами:
            - found: bool (найден ли платеж)
            - confirmed: bool (подтвержден ли платеж)
            - confirmations: int (количество подтверждений)
            - amount: float (фактическая полученная сумма)
            - txid: str (ID транзакции)
        """
        try:
            # Получаем UTXO для адреса
            utxos = await self.get_address_utxo(address)
            if not utxos:
                return {
                    'found': False,
                    'confirmed': False,
                    'confirmations': 0,
                    'amount': 0,
                    'txid': None
                }
                
            # Конвертируем ожидаемую сумму в литоши
            expected_litoshi = int(expected_amount * 10**8)
            
            # Ищем UTXO с нужной суммой
            for utxo in utxos:
                if utxo.get('value') == expected_litoshi:
                    txid = utxo.get('txid')
                    if not txid:
                        continue
                        
                    # Проверяем статус транзакции
                    status = await self.get_transaction_status(txid)
                    if status:
                        confirmations = status.get('confirmations', 0)
                        return {
                            'found': True,
                            'confirmed': confirmations >= 3,
                            'confirmations': confirmations,
                            'amount': expected_amount,
                            'txid': txid
                        }
                    else:
                        return {
                            'found': True,
                            'confirmed': False,
                            'confirmations': 0,
                            'amount': expected_amount,
                            'txid': txid
                        }
            
            # Если точная сумма не найдена, проверяем общий баланс
            address_info = await self.get_address_info(address)
            if address_info:
                chain_stats = address_info.get('chain_stats', {})
                mempool_stats = address_info.get('mempool_stats', {})
                
                funded = chain_stats.get('funded_txo_sum', 0) + mempool_stats.get('funded_txo_sum', 0)
                spent = chain_stats.get('spent_txo_sum', 0) + mempool_stats.get('spent_txo_sum', 0)
                balance = funded - spent
                
                if balance >= expected_litoshi:
                    # Ищем транзакции, которые принесли средства
                    txs = await self.get_address_transactions(address, limit=10)
                    if txs:
                        for tx in txs:
                            if tx.get('vin') and tx.get('vout'):
                                for vout in tx['vout']:
                                    if (vout.get('scriptpubkey_address') == address and 
                                        vout.get('value') >= expected_litoshi):
                                        status = await self.get_transaction_status(tx['txid'])
                                        confirmations = status.get('confirmations', 0) if status else 0
                                        return {
                                            'found': True,
                                            'confirmed': confirmations >= 3,
                                            'confirmations': confirmations,
                                            'amount': vout['value'] / 10**8,
                                            'txid': tx['txid']
                                        }
            
            return {
                'found': False,
                'confirmed': False,
                'confirmations': 0,
                'amount': 0,
                'txid': None
            }
                
        except Exception as e:
            logger.error(f"Error checking payment for address {address}: {e}")
            return {
                'found': False,
                'confirmed': False,
                'confirmations': 0,
                'amount': 0,
                'txid': None
            }
            
    async def validate_address(self, address: str) -> bool:
        """Проверка валидности Litecoin адреса"""
        try:
            info = await self.get_address_info(address)
            return info is not None
        except Exception:
            return False
            
    async def get_balance(self, address: str) -> float:
        """Получение баланса адреса в LTC"""
        try:
            info = await self.get_address_info(address)
            if info:
                chain_stats = info.get('chain_stats', {})
                mempool_stats = info.get('mempool_stats', {})
                
                funded = chain_stats.get('funded_txo_sum', 0) + mempool_stats.get('funded_txo_sum', 0)
                spent = chain_stats.get('spent_txo_sum', 0) + mempool_stats.get('spent_txo_sum', 0)
                balance = funded - spent
                
                return balance / 10**8
            return 0.0
        except Exception as e:
            logger.error(f"Error getting balance for address {address}: {e}")
            return 0.0

# Глобальный экземпляр API
litecoinspace_api = LitecoinSpaceAPI()

# Функции для интеграции с существующим кодом
async def get_ltc_usd_rate():
    """Получение курса LTC/USD через CoinGecko"""
    global _last_rate_update, _cached_ltc_rate
    
    # Проверяем, нужно ли обновлять курс (кешируем на 1 час)
    current_time = time.time()
    if current_time - _last_rate_update < 3600:
        return _cached_ltc_rate
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd', timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    rate = data['litecoin']['usd']
                    _cached_ltc_rate = rate
                    _last_rate_update = current_time
                    return rate
                else:
                    logger.error(f"CoinGecko API error: {response.status}")
                    return _cached_ltc_rate
    except Exception as e:
        logger.error(f"Error getting LTC/USD rate: {e}")
        return _cached_ltc_rate

async def check_ltc_transaction(address: str, amount: float) -> bool:
    """Основная функция проверки транзакций через LitecoinSpace"""
    payment_info = await litecoinspace_api.check_payment(address, amount)
    return payment_info['found'] and payment_info['confirmed']

async def check_ltc_transaction_enhanced(address: str, amount: float) -> Dict[str, Any]:
    """Расширенная функция проверки транзакций через LitecoinSpace"""
    return await litecoinspace_api.check_payment(address, amount)

async def validate_ltc_address(address: str) -> bool:
    """Проверка валидности адреса через LitecoinSpace"""
    return await litecoinspace_api.validate_address(address)

async def get_ltc_balance(address: str) -> float:
    """Получение баланса Litecoin адреса через LitecoinSpace"""
    return await litecoinspace_api.get_balance(address)

async def get_address_transactions(address: str, limit: int = 50) -> Optional[List]:
    """Получение истории транзакций адреса через LitecoinSpace"""
    return await litecoinspace_api.get_address_transactions(address, limit)

async def log_transaction_event(order_id: str, address: str, amount: float, status: str, message: str, level: str = "INFO"):
    """Логирование событий транзакций"""
    log_data = {
        'timestamp': datetime.now().isoformat(),
        'order_id': order_id,
        'address': address,
        'amount': amount,
        'status': status,
        'message': message,
        'level': level
    }
    
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        f"Transaction event: {json.dumps(log_data)}"
    )

async def get_key_usage_stats():
    """Получение статистики использования API ключей (заглушка)"""
    # Для LitecoinSpace API не требуется ключ, поэтому возвращаем заглушку
    return []

async def cleanup_cache():
    """Очистка кэша каждые 10 минут"""
    global _address_cache, _tx_cache, _utxo_cache, _last_cache_cleanup
    
    current_time = time.time()
    if current_time - _last_cache_cleanup > 600:  # 10 минут
        _address_cache = {}
        _tx_cache = {}
        _utxo_cache = {}
        _last_cache_cleanup = current_time
        logger.info("LitecoinSpace API cache cleaned up")

# Функция для мониторинга депозитов через LitecoinSpace
async def monitor_deposits():
    """Мониторинг депозитов через LitecoinSpace API"""
    from db import get_pending_deposits, update_deposit_confirmations, process_confirmed_deposit
    
    while True:
        try:
            # Очищаем кэш
            await cleanup_cache()
            
            # Получаем pending депозиты из базы
            pending_deposits = await get_pending_deposits()
            
            for deposit in pending_deposits:
                txid = deposit['txid']
                address = deposit['address']
                user_id = deposit['user_id']
                amount_ltc = deposit['amount_ltc']
                
                # Проверяем статус транзакции
                status = await litecoinspace_api.get_transaction_status(txid)
                if status:
                    confirmations = status.get('confirmations', 0)
                    
                    # Обновляем количество подтверждений в базе
                    await update_deposit_confirmations(txid, confirmations)
                    
                    # Если есть достаточно подтверждений, обрабатываем депозит
                    if confirmations >= 3 and deposit['status'] != 'confirmed':
                        await process_confirmed_deposit(txid, user_id, deposit['amount_usd'])
                        await log_transaction_event(
                            f"deposit_{txid}", address, amount_ltc,
                            "CONFIRMED", f"Deposit confirmed with {confirmations} confirmations"
                        )
                        
            # Пауза между проверками
            await asyncio.sleep(60)
            
        except Exception as e:
            logger.error(f"Error in deposit monitoring: {e}")
            await asyncio.sleep(60)

# Инициализация при импорте
async def init_litecoinspace_api():
    """Инициализация LitecoinSpace API"""
    await litecoinspace_api.init_session()
    logger.info("LitecoinSpace API initialized")

# Завершение работы при выходе
async def close_litecoinspace_api():
    """Завершение работы LitecoinSpace API"""
    await litecoinspace_api.close_session()
    logger.info("LitecoinSpace API closed")

# Функция для получения кэшированного курса (для обратной совместимости)
async def get_cached_rate():
    """Получение кэшированного курса LTC/USD"""
    return _cached_ltc_rate, True  # True указывает, что значение из кэша

# Функция для запуска мониторинга депозитов (для обратной совместимости)
def start_deposit_monitoring():
    """Запуск мониторинга депозитов"""
    asyncio.create_task(monitor_deposits())
