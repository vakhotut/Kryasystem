import os
import json
import logging
import time
import io
from typing import Dict, Any, Optional
from functools import wraps

from bip_utils import (
    Bip39MnemonicGenerator, 
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip44,
    Bip44Coins,
    Bip44Changes
)

# Попытаемся импортировать дополнительные библиотеки безопасности
try:
    import qrcode
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False
    logging.warning("QRCode library not available. Using external service for QR generation.")

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logging.warning("Cryptography library not available. Using less secure storage methods.")

logger = logging.getLogger(__name__)

class SecureData:
    """Класс для безопасного хранения чувствительных данных"""
    def __init__(self, data: str):
        self._data = data.encode()
        
    def __str__(self):
        return "**REDACTED**"
    
    def get_data(self) -> bytes:
        return self._data

class IndexManager:
    """Менеджер для безопасного хранения и управления индексами адресов"""
    def __init__(self, storage_path: str = "wallet_state.json", encryption_key: Optional[bytes] = None):
        self.storage_path = storage_path
        self.encryption_key = encryption_key
        self.index = self._load_index()
    
    def _load_index(self) -> int:
        try:
            if os.path.exists(self.storage_path):
                if self.encryption_key and CRYPTO_AVAILABLE:
                    with open(self.storage_path, 'rb') as f:
                        encrypted_data = f.read()
                    cipher = Fernet(self.encryption_key)
                    data = cipher.decrypt(encrypted_data)
                    return json.loads(data.decode()).get('last_index', 0)
                else:
                    with open(self.storage_path, 'r') as f:
                        return json.load(f).get('last_index', 0)
            return 0
        except Exception as e:
            logger.error(f"Error loading index: {e}")
            return 0
            
    def increment_and_save(self):
        """Увеличивает индекс и сохраняет его безопасным способом"""
        self.index += 1
        try:
            data = json.dumps({'last_index': self.index})
            
            if self.encryption_key and CRYPTO_AVAILABLE:
                cipher = Fernet(self.encryption_key)
                encrypted_data = cipher.encrypt(data.encode())
                with open(self.storage_path, 'wb') as f:
                    f.write(encrypted_data)
            else:
                with open(self.storage_path, 'w') as f:
                    f.write(data)
        except Exception as e:
            logger.error(f"Error saving index: {e}")

def rate_limited(max_per_second):
    """Декоратор для ограничения частоты вызовов функции"""
    min_interval = 1.0 / max_per_second
    def decorator(func):
        last_time_called = 0.0
        @wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal last_time_called
            elapsed = time.time() - last_time_called
            left_to_wait = min_interval - elapsed
            if left_to_wait > 0:
                time.sleep(left_to_wait)
            ret = func(*args, **kwargs)
            last_time_called = time.time()
            return ret
        return wrapper
    return decorator

class LTCWallet:
    def __init__(self, config_path: str = "config.yaml"):
        # Загрузка конфигурации
        self.config = self._load_config(config_path)
        
        # Генерация или загрузка ключа шифрования
        self.encryption_key = self._get_encryption_key()
        
        # Инициализация менеджера индексов
        self.index_manager = IndexManager(
            storage_path=self.config.get('index_storage_path', 'wallet_state.json'),
            encryption_key=self.encryption_key
        )
        
        # Загрузка или генерация мнемонической фразы
        self.mnemonic = self._get_mnemonic()
        
        # Валидация мнемонической фразы
        if not Bip39MnemonicValidator().IsValid(self.mnemonic):
            raise ValueError("Invalid mnemonic phrase")
        
        # Генерация seed из мнемоники
        self.seed_bytes = Bip39SeedGenerator(self.mnemonic).Generate()
        
        # Создание BIP44 кошелька для Litecoin
        coin_type = self.config.get('coin_type', Bip44Coins.LITECOIN)
        self.bip44_mst = Bip44.FromSeed(self.seed_bytes, coin_type)
        
        logger.info("LTC Wallet initialized with enhanced security")

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Загрузка конфигурации из файла"""
        default_config = {
            'coin_type': Bip44Coins.LITECOIN,
            'index_storage_path': 'wallet_state.json',
            'max_addresses_per_second': 5
        }
        
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    import yaml
                    config = yaml.safe_load(f)
                    return {**default_config, **config}
        except Exception as e:
            logger.error(f"Error loading config: {e}")
        
        return default_config

    def _get_encryption_key(self) -> Optional[bytes]:
        """Получение или генерация ключа шифрования"""
        key_path = self.config.get('encryption_key_path', 'encryption.key')
        
        if os.path.exists(key_path):
            try:
                with open(key_path, 'rb') as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Error reading encryption key: {e}")
        
        # Генерация нового ключа
        if CRYPTO_AVAILABLE:
            key = Fernet.generate_key()
            try:
                with open(key_path, 'wb') as f:
                    f.write(key)
                return key
            except Exception as e:
                logger.error(f"Error saving encryption key: {e}")
        
        return None

    def _get_mnemonic(self) -> str:
        """Безопасное получение мнемонической фразы"""
        mnemonic = os.getenv("LTC_MNEMONIC")
        if mnemonic:
            return mnemonic
        
        # Генерация новой мнемонической фразы
        new_mnemonic = Bip39MnemonicGenerator().Generate()
        logger.warning("Generated new mnemonic. Please securely store it in a safe place")
        
        # Попытка безопасного сохранения
        try:
            mnemonic_path = self.config.get('mnemonic_backup_path', 'mnemonic_backup.enc')
            if self.encryption_key and CRYPTO_AVAILABLE:
                cipher = Fernet(self.encryption_key)
                encrypted_mnemonic = cipher.encrypt(new_mnemonic.encode())
                with open(mnemonic_path, 'wb') as f:
                    f.write(encrypted_mnemonic)
            else:
                with open(mnemonic_path, 'w') as f:
                    f.write(new_mnemonic)
        except Exception as e:
            logger.error(f"Error saving mnemonic: {e}")
        
        return new_mnemonic

    @rate_limited(5)  # Ограничение: 5 вызовов в секунду
    def generate_address(self, index: Optional[int] = None) -> Dict[str, Any]:
        """Генерация нового адреса с валидацией параметров"""
        try:
            # Валидация индекса
            if index is not None:
                if index < 0:
                    raise ValueError("Index must be non-negative")
                address_index = index
            else:
                address_index = self.index_manager.index
            
            # Генерация адреса по индексу: m/44'/2'/0'/0/address_index
            bip44_acc = self.bip44_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(address_index)
            
            # Получение адреса и ключей
            address = bip44_acc.PublicKey().ToAddress()
            private_key = bip44_acc.PrivateKey().Raw().ToHex()
            public_key = bip44_acc.PublicKey().RawCompressed().ToHex()
            
            result = {
                "address": address,
                "private_key": SecureData(private_key),  # Безопасное хранение приватного ключа
                "public_key": public_key,
                "index": address_index
            }
            
            # Увеличиваем индекс только если не был указан конкретный индекс
            if index is None:
                self.index_manager.increment_and_save()
                
            logger.info(f"Generated LTC address: {address}, index: {result['index']}")
            return result
        except Exception as e:
            logger.error(f"Error generating LTC address: {e}")
            raise

    def get_qr_code(self, address: str, amount: float = None) -> str:
        """Генерация QR-кода для адреса"""
        ltc_amount = f"?amount={amount}" if amount else ""
        
        if QRCODE_AVAILABLE:
            # Локальная генерация QR-кода
            uri = f"litecoin:{address}{ltc_amount}"
            img = qrcode.make(uri)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            # В реальном приложении нужно вернуть изображение подходящим способом
            return f"data:image/png;base64,{buf.getvalue()}"
        else:
            # Использование внешнего сервиса как запасной вариант
            return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=litecoin:{address}{ltc_amount}"

    def health_check(self) -> Dict[str, Any]:
        """Проверка состояния кошелька"""
        try:
            # Проверка базовой функциональности
            test_account = self.bip44_mst.Purpose().Coin().Account(0)
            test_address = test_account.Change(Bip44Changes.CHAIN_EXT).AddressIndex(0)
            
            return {
                "status": "healthy",
                "index": self.index_manager.index,
                "can_generate_addresses": True
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "can_generate_addresses": False
            }

    def backup_wallet(self, backup_path: str) -> bool:
        """Создание резервной копии кошелька"""
        try:
            backup_data = {
                "mnemonic": self.mnemonic,
                "index": self.index_manager.index
            }
            
            if self.encryption_key and CRYPTO_AVAILABLE:
                cipher = Fernet(self.encryption_key)
                encrypted_data = cipher.encrypt(json.dumps(backup_data).encode())
                with open(backup_path, 'wb') as f:
                    f.write(encrypted_data)
            else:
                with open(backup_path, 'w') as f:
                    json.dump(backup_data, f)
            
            return True
        except Exception as e:
            logger.error(f"Error creating wallet backup: {e}")
            return False

# Глобальный экземпляр кошелька
ltc_wallet = LTCWallet()
