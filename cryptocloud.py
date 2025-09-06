import requests
import os
import time
from datetime import datetime, timedelta

CRYPTOCLOUD_API_KEY = os.getenv("CRYPTOCLOUD_API_KEY")
CRYPTOCLOUD_SHOP_ID = os.getenv("CRYPTOCLOUD_SHOP_ID")

def create_cryptocloud_invoice(amount, crypto_currency, order_id, email=None):
    """
    Создание инвойса в CryptoCloud
    
    :param amount: Сумма в USD
    :param crypto_currency: Криптовалюта (BTC, ETH, USDT, LTC и др.)
    :param order_id: Уникальный идентификатор заказа
    :param email: Email плательщика (опционально)
    :return: Ответ API CryptoCloud
    """
    url = "https://api.cryptocloud.plus/v2/invoice/create"
    
    headers = {
        "Authorization": f"Token {CRYPTOCLOUD_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "shop_id": CRYPTOCLOUD_SHOP_ID,
        "amount": amount,
        "currency": "USD",  # Фиатная валюта всегда USD
        "order_id": order_id,
        "add_fields": {
            "cryptocurrency": crypto_currency  # Криптовалюта для оплаты
        }
    }
    
    if email:
        data["email"] = email
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        
        # Проверяем статус ответа
        if result.get('status') == 'success':
            return result
        else:
            print(f"CryptoCloud API error: {result}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"Error creating CryptoCloud invoice: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response content: {e.response.text}")
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
        print(f"Error getting CryptoCloud invoice status: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response content: {e.response.text}")
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
        print(f"Error canceling CryptoCloud invoice: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response content: {e.response.text}")
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
