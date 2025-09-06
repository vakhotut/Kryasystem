import requests
import os
import time
from datetime import datetime, timedelta

CRYPTOCLOUD_API_KEY = os.getenv("CRYPTOCLOUD_API_KEY")
CRYPTOCLOUD_SHOP_ID = os.getenv("CRYPTOCLOUD_SHOP_ID")

def create_cryptocloud_invoice(amount, currency, order_id, email=None):
    """
    Создание инвойса в CryptoCloud
    
    :param amount: Сумма в USD
    :param currency: Криптовалюта (BTC, ETH, USDT, LTC и др.)
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
        "currency": currency,
        "order_id": order_id
    }
    
    if email:
        data["email"] = email
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error creating CryptoCloud invoice: {e}")
        return None

def get_cryptocloud_invoice_status(invoice_uuid):
    """
    Получение статуса инвойса в CryptoCloud
    
    :param invoice_uuid: UUID инвойса
    :return: Ответ API CryptoCloud
    """
    url = f"https://api.cryptocloud.plus/v2/invoice/info?uuid={invoice_uuid}"
    
    headers = {
        "Authorization": f"Token {CRYPTOCLOUD_API_KEY}"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting CryptoCloud invoice status: {e}")
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
        
        if status_info and status_info.get('status') == 'success':
            invoice_status = status_info['result']['status']
            if invoice_status == 'paid':
                return 'paid'
            elif invoice_status in ['expired', 'canceled']:
                return invoice_status
        
        time.sleep(interval)
    
    return 'timeout'
