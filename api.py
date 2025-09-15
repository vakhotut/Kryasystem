# api.py
import aiohttp
import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import time
from db import db_pool, update_user, add_generated_address, update_address_balance

logger = logging.getLogger(__name__)

# Конфигурация API
MEMPOOL_API_URL = "https://mempool.space/api"
MEMPOOL_TESTNET_URL = "https://mempool.space/testnet/api"
LTC_NETWORK = "mainnet"  # или "testnet"
CONFIRMATIONS_REQUIRED = 3  # Требуемое количество подтверждений

# Кэш для хранения данных о транзакциях
transaction_cache = {}
address_cache = {}

async def get_ltc_usd_rate() -> float:
    """Получение текущего курса LTC к USD"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MEMPOOL_API_URL}/v1/historical?currency=USD") as response:
                if response.status == 200:
                    data = await response.json()
                    # Получаем последнюю доступную цену
                    return float(data.get('prices', [])[-1][1] if data.get('prices') else 0)
    except Exception as e:
        logger.error(f"Error getting LTC rate: {e}")
    
    # Возвращаем значение по умолчанию в случае ошибки
    return 65.0  # Примерное значение

async def get_address_transactions(address: str) -> List[Dict]:
    """Получение транзакций для адреса из mempool.space"""
    try:
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/address/{address}/txs") as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Error getting transactions for address {address}: {response.status}")
                    return []
    except Exception as e:
        logger.error(f"Error in get_address_transactions for {address}: {e}")
        return []

async def get_transaction(txid: str) -> Optional[Dict]:
    """Получение информации о конкретной транзакции"""
    try:
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/tx/{txid}") as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Error getting transaction {txid}: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error in get_transaction for {txid}: {e}")
        return None

async def check_ltc_transaction(address: str, expected_amount: float) -> bool:
    """
    Проверка наличия транзакции на указанный адрес с ожидаемой суммой
    Возвращает True если транзакция найдена и имеет достаточное количество подтверждений
    """
    try:
        transactions = await get_address_transactions(address)
        
        for tx in transactions:
            # Проверяем выходы транзакции на наш адрес
            for output in tx.get('vout', []):
                if 'scriptpubkey_address' in output and output['scriptpubkey_address'] == address:
                    amount_ltc = output['value'] / 100000000  # Конвертация из сатоши
                    
                    # Проверяем совпадение суммы и достаточность подтверждений
                    if abs(amount_ltc - expected_amount) < 0.00000001 and tx['status']['confirmed']:
                        confirmations = await get_confirmations_count(tx['txid'])
                        if confirmations >= CONFIRMATIONS_REQUIRED:
                            return True
        
        return False
    except Exception as e:
        logger.error(f"Error checking LTC transaction for address {address}: {e}")
        return False

async def get_confirmations_count(txid: str) -> int:
    """Получение количества подтверждений для транзакции"""
    try:
        tx = await get_transaction(txid)
        if tx and tx['status']['confirmed']:
            # Для подтвержденных транзакций получаем количество подтверждений
            best_block_height = await get_best_block_height()
            if best_block_height:
                return best_block_height - tx['status']['block_height'] + 1
        return 0
    except Exception as e:
        logger.error(f"Error getting confirmations for tx {txid}: {e}")
        return 0

async def get_best_block_height() -> Optional[int]:
    """Получение высоты последнего блока"""
    try:
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/blocks/tip/height") as response:
                if response.status == 200:
                    return int(await response.text())
                else:
                    logger.error(f"Error getting best block height: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error in get_best_block_height: {e}")
        return None

async def monitor_deposits():
    """Фоновая задача для мониторинга депозитов на всех отслеживаемых адресах"""
    while True:
        try:
            # Получаем все адреса из базы данных
            addresses = await get_all_tracked_addresses()
            
            for address_data in addresses:
                address = address_data['address']
                user_id = address_data['user_id']
                expected_amount = address_data.get('expected_amount', 0)
                
                # Проверяем транзакции для этого адреса
                transactions = await get_address_transactions(address)
                
                for tx in transactions:
                    txid = tx['txid']
                    
                    # Проверяем, не обрабатывали ли мы уже эту транзакцию
                    if await is_transaction_processed(txid):
                        continue
                    
                    # Ищем выходы на наш адрес
                    for output in tx.get('vout', []):
                        if 'scriptpubkey_address' in output and output['scriptpubkey_address'] == address:
                            amount_ltc = output['value'] / 100000000  # Конвертация из сатоши
                            
                            # Если сумма соответствует ожидаемой или мы отслеживаем все поступления
                            if expected_amount == 0 or abs(amount_ltc - expected_amount) < 0.00000001:
                                # Регистрируем депозит
                                confirmations = await get_confirmations_count(txid)
                                status = 'confirmed' if confirmations >= CONFIRMATIONS_REQUIRED else 'pending'
                                
                                await register_deposit(
                                    txid=txid,
                                    address=address,
                                    user_id=user_id,
                                    amount_ltc=amount_ltc,
                                    confirmations=confirmations,
                                    status=status
                                )
                                
                                # Если транзакция подтверждена, зачисляем средства
                                if status == 'confirmed':
                                    await process_confirmed_deposit(txid, user_id, amount_ltc)
            
            # Ждем перед следующей проверкой
            await asyncio.sleep(300)  # 5 минут
        except Exception as e:
            logger.error(f"Error in monitor_deposits: {e}")
            await asyncio.sleep(60)  # Ждем 1 минуту при ошибке

async def get_all_tracked_addresses():
    """Получение всех отслеживаемых адресов из базы данных"""
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM generated_addresses WHERE balance = 0 OR balance IS NULL")
    except Exception as e:
        logger.error(f"Error getting tracked addresses: {e}")
        return []

async def is_transaction_processed(txid: str) -> bool:
    """Проверка, была ли уже обработана транзакция"""
    try:
        async with db_pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM deposits WHERE txid = $1", txid)
            return count > 0
    except Exception as e:
        logger.error(f"Error checking if transaction processed: {e}")
        return False

async def register_deposit(txid: str, address: str, user_id: int, amount_ltc: float, confirmations: int, status: str):
    """Регистрация депозита в базе данных"""
    try:
        async with db_pool.acquire() as conn:
            # Получаем курс LTC для конвертации в USD
            ltc_rate = await get_ltc_usd_rate()
            amount_usd = amount_ltc * ltc_rate
            
            await conn.execute('''
                INSERT INTO deposits (txid, address, user_id, amount_ltc, amount_usd, confirmations, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (txid) DO UPDATE SET
                confirmations = EXCLUDED.confirmations,
                status = EXCLUDED.status
            ''', txid, address, user_id, amount_ltc, amount_usd, confirmations, status)
            
            # Обновляем баланс адреса
            await update_address_balance(address, amount_ltc, 1)  # 1 транзакция
            
            logger.info(f"Registered deposit: {txid} for user {user_id}, amount: {amount_ltc} LTC")
    except Exception as e:
        logger.error(f"Error registering deposit: {e}")

async def process_confirmed_deposit(txid: str, user_id: int, amount_ltc: float):
    """Обработка подтвержденного депозита - зачисление средств на баланс пользователя"""
    try:
        async with db_pool.acquire() as conn:
            # Получаем информацию о депозите
            deposit = await conn.fetchrow("SELECT * FROM deposits WHERE txid = $1", txid)
            if not deposit:
                return
            
            # Зачисляем средства на баланс пользователя
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                deposit['amount_usd'], user_id
            )
            
            # Обновляем статус депозита
            await conn.execute(
                "UPDATE deposits SET status = 'processed' WHERE txid = $1",
                txid
            )
            
            # Отправляем уведомление пользователю
            try:
                from bot import bot
                user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
                lang = user['language'] or 'ru'
                
                from db import get_text
                message = get_text(lang, 'deposit_confirmed').format(
                    amount_ltc=amount_ltc,
                    amount_usd=deposit['amount_usd']
                )
                
                await bot.send_message(user_id, message)
            except Exception as e:
                logger.error(f"Error sending deposit notification: {e}")
            
            logger.info(f"Processed confirmed deposit: {txid} for user {user_id}")
    except Exception as e:
        logger.error(f"Error processing confirmed deposit: {e}")

async def get_address_balance(address: str) -> Tuple[float, int]:
    """
    Получение баланса и количества транзакций для адреса
    Возвращает (balance, transaction_count)
    """
    try:
        base_url = MEMPOOL_API_URL if LTC_NETWORK == "mainnet" else MEMPOOL_TESTNET_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/address/{address}") as response:
                if response.status == 200:
                    data = await response.json()
                    balance = data['chain_stats']['funded_txo_sum'] - data['chain_stats']['spent_txo_sum']
                    balance_ltc = balance / 100000000  # Конвертация из сатоши
                    tx_count = data['chain_stats']['tx_count'] + data['mempool_stats']['tx_count']
                    return balance_ltc, tx_count
                else:
                    logger.error(f"Error getting address balance for {address}: {response.status}")
                    return 0, 0
    except Exception as e:
        logger.error(f"Error in get_address_balance for {address}: {e}")
        return 0, 0

async def get_key_usage_stats() -> Dict[str, any]:
    """Получение статистики использования API ключей"""
    # В данной реализации мы не используем API ключи для mempool.space,
    # но функция оставлена для совместимости
    return {
        "mempool": {
            "total_requests": 0,
            "successful_requests": 0,
            "last_used": datetime.now().isoformat(),
            "daily_limit": float('inf'),
            "remaining_daily_requests": float('inf')
        }
    }

# Запуск фоновой задачи мониторинга
def start_deposit_monitoring():
    """Запуск задачи мониторинга депозитов"""
    asyncio.create_task(monitor_deposits())
