import requests
import os
import time
from datetime import datetime, timedelta
import logging

# Настройка логирования
logger = logging.getLogger(__name__)

CRYPTOCLOUD_API_KEY = os.getenv("CRYPTOCLOUD_API_KEY")
CRYPTOCLOUD_SHOP_ID = os.getenv("CRYPTOCLOUD_SHOP_ID")

def create_cryptocloud_invoice(amount, crypto_currency, order_id, email=None, shop_id=None, poll_attempts=30, poll_interval=1):
    """
    Создание инвойса в CryptoCloud с попыткой получить address.
    :param amount: Сумма в USD
    :param crypto_currency: Короткий код валюты, например 'BTC','ETH','USDT','LTC' или уже 'USDT_TRC20'
    :param order_id: Уникальный идентификатор заказа
    :param email: Email (опционально)
    :param shop_id: Можно передать shop_id (по умолчанию берётся CRYPTOCLOUD_SHOP_ID)
    :param poll_attempts: сколько раз перепроверить merchant/info если address пустой
    :param poll_interval: пауза между попытками в секундах
    :return: полный json-ответ от API или None
    """
    url = "https://api.cryptocloud.plus/v2/invoice/create"
    headers = {
        "Authorization": f"Token {CRYPTOCLOUD_API_KEY}",
        "Content-Type": "application/json"
    }

    # mapping — лучше указывать полные варианты сети, если известны
    crypto_mapping = {
        'BTC': 'BTC',
        'ETH': 'ETH',
        'USDT': 'USDT_TRC20',  # по умолчанию TRC20, но лучше использовать точный код
        'LTC': 'LTC'
    }
    crypto_code = crypto_mapping.get(crypto_currency, crypto_currency)

    payload = {
        "shop_id": shop_id or CRYPTOCLOUD_SHOP_ID,
        "amount": amount,
        "currency": "USD",
        "order_id": order_id,
        "add_fields": {
            # указываем валюту и ограничиваем available_currencies чтобы API сразу сгенерировал адрес для нужной сети
            "cryptocurrency": crypto_code,
            "available_currencies": [crypto_code]
        }
    }
    if email:
        payload["email"] = email

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        logger.info(f"CryptoCloud invoice CREATE response: {result}")

        if not result or result.get('status') != 'success':
            logger.error(f"CryptoCloud returned error on create: {result}")
            return result

        # Если address уже есть — возвращаем ответ
        res_obj = result.get('result', {})
        address = res_obj.get('address')
        if address:
            return result

        # если address пустой — пробуем опрашивать merchant/info (иногда генерируется чуть позже)
        uuid = res_obj.get('uuid')
        if not uuid:
            return result

        for attempt in range(poll_attempts):
            time.sleep(poll_interval)
            info = get_cryptocloud_invoice_status(uuid)
            logger.info(f"Polling invoice info (attempt {attempt+1}): {info}")
            if info and info.get('status') == 'success' and len(info.get('result', [])) > 0:
                inv = info['result'][0]
                if inv.get('address'):
                    # возвращаем структуру похожую на create response, с адресом
                    return {
                        "status": "success",
                        "result": inv
                    }
        # по завершении попыток — возвращаем первоначальный результат (без address)
        return result

    except requests.exceptions.RequestException as e:
        logger.error(f"Error creating CryptoCloud invoice: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response content: {e.response.text}")
        return None

def get_cryptocloud_invoice_status(uuids):
    """
    Получение статуса инвойса/инвойсов в CryptoCloud

    :param uuids: UUID инвойса (строка) или список UUID
    :return: Ответ API CryptoCloud
    """
    url = "https://api.cryptocloud.plus/v2/invoice/merchant/info"
    
    headers = {
        "Authorization": f"Token {CRYPTOCLOUD_API_KEY}",
        "Content-Type": "application/json"
    }
    
    if isinstance(uuids, str):
        uuids = [uuids]
    
    data = {
        "uuids": uuids
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting CryptoCloud invoice status: {e}")
        if hasattr(e, 'response') and e.response:
            logger.error(f"Response content: {e.response.text}")
        return None

def cancel_cryptocloud_invoice(invoice_uuid):
    """
    Отмена инвойса в CryptoCloud
    
    :param invoice_uuid: UUID инвойса (формат INV-XXXXXXXX или XXXXXXXX)
    :return: Ответ API CryptoCloud
    """
    url = "https://api.cryptocloud.plus/v2/invoice/merchant/canceled"
    
    headers = {
        "Authorization": f"Token {CRYPTOCLOUD_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "uuid": invoice_uuid
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error canceling CryptoCloud invoice: {e}")
        if hasattr(e, 'response') and e.response:
            logger.error(f"Response content: {e.response.text}")
        return None

def check_payment_status_periodically(invoice_uuid, max_checks=60, interval=60):
    """
    Периодическая проверка статуса платежа
    
    :param invoice_uuid: UUID инвойса
    :param max_checks: Максимальное количество проверок
    :param interval: Интервал между проверками в секундах
    :return: Статус платежа
    """
    for _ in range(max_checks):
        status_info = get_cryptocloud_invoice_status(invoice_uuid)
        
        if status_info and status_info.get('status') == 'success' and len(status_info['result']) > 0:
            invoice = status_info['result'][0]  # Берем первый счет из массива
            invoice_status = invoice['status']
            if invoice_status == 'paid':
                return 'paid'
            elif invoice_status in ['expired', 'canceled']:
                return invoice_status
        
        time.sleep(interval)
    
    return 'timeout'
