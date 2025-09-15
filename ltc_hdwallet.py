import os
import json
import logging
import time
import io
import re
from typing import Dict, Any, Optional
from functools import wraps

from bip_utils import (
    Bip39MnemonicGenerator, 
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip39WordsNum,
    Bip39Languages,
    Bip84,  # Изменено с Bip44 на Bip84
    Bip84Coins,  # Изменено с Bip44Coins на Bip84Coins
    Bip84Changes,  # Изменено с Bip44Changes на Bip84Changes
    Bip44Coins,  # Оставляем для обратной совместимости
    Bip44Changes  # Оставляем для обратной совместимости
)

# Добавим валидацию адресов Litecoin
from bip_utils import (
    BchAddrConverter,
    LtcAddrDecoder,
    LtcAddrEncoder,
    P2WPKH
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
        
        # Создание BIP84 кошелька для Litecoin (изменено с BIP44)
        coin_type = self.config.get('coin_type', Bip84Coins.LITECOIN)
        self.bip84_mst = Bip84.FromSeed(self.seed_bytes, coin_type)  # Изменено с Bip44 на Bip84
        
        logger.info("LTC Wallet initialized with enhanced security")

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Загрузка конфигурации из файла"""
        default_config = {
            'coin_type': Bip84Coins.LITECOIN,  # Изменено с Bip44Coins.LITECOIN
            'index_storage_path': 'wallet_state.json',
            'max_addresses_per_second': 5,
            'mnemonic_length': 12  # 12, 15, 18, 21 или 24 слова
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
        # 1. Проверка переменной окружения
        mnemonic = os.getenv("LTC_MNEMONIC")
        if mnemonic:
            logger.info("Using mnemonic from environment variable")
            return mnemonic
        
        # 2. Проверка зашифрованного файла
        mnemonic_path = self.config.get('mnemonic_backup_path', 'mnemonic_backup.enc')
        if os.path.exists(mnemonic_path):
            try:
                if self.encryption_key and CRYPTO_AVAILABLE:
                    with open(mnemonic_path, 'rb') as f:
                        encrypted_mnemonic = f.read()
                    cipher = Fernet(self.encryption_key)
                    decrypted_mnemonic = cipher.decrypt(encrypted_mnemonic).decode()
                    logger.info("Using mnemonic from encrypted backup file")
                    return decrypted_mnemonic
                else:
                    with open(mnemonic_path, 'r') as f:
                        stored_mnemonic = f.read().strip()
                    logger.info("Using mnemonic from backup file")
                    return stored_mnemonic
            except Exception as e:
                logger.error(f"Error reading mnemonic backup: {e}")
        
        # 3. Генерация новой мнемонической фразы
        try:
            # Определение длины мнемоники из конфига
            words_num = self.config.get('mnemonic_length', 12)
            
            # Поддержка различных длин мнемонических фраз
            words_num_map = {
                12: Bip39WordsNum.WORDS_NUM_12,
                15: Bip39WordsNum.WORDS_NUM_15,
                18: Bip39WordsNum.WORDS_NUM_18,
                21: Bip39WordsNum.WORDS_NUM_21,
                24: Bip39WordsNum.WORDS_NUM_24
            }
            
            if words_num not in words_num_map:
                logger.warning(f"Invalid mnemonic length {words_num}, using 12 words")
                words_num = 12
                
            # Генерация мнемонической фразы
            new_mnemonic = Bip39MnemonicGenerator().FromWordsNumber(words_num_map[words_num])
            
            logger.warning("Generated new mnemonic. Please securely store it in a safe place")
            logger.warning(f"Mnemonic: {new_mnemonic}")
            
            # Попытка безопасного сохранения
            try:
                if self.encryption_key and CRYPTO_AVAILABLE:
                    cipher = Fernet(self.encryption_key)
                    encrypted_mnemonic = cipher.encrypt(str(new_mnemonic).encode())
                    with open(mnemonic_path, 'wb') as f:
                        f.write(encrypted_mnemonic)
                else:
                    with open(mnemonic_path, 'w') as f:
                        f.write(str(new_mnemonic))
                logger.info(f"Mnemonic backup saved to {mnemonic_path}")
            except Exception as e:
                logger.error(f"Error saving mnemonic: {e}")
            
            return str(new_mnemonic)
            
        except Exception as e:
            logger.error(f"Error generating mnemonic: {e}")
            # Резервный метод генерации
            import secrets
            import hashlib
            
            # Генерация случайных байтов для энтропии
            entropy_bytes = secrets.token_bytes(16)  # 16 байт = 128 бит для 12 слов
            
            # Использование Bip39MnemonicGenerator с энтропией
            try:
                new_mnemonic = Bip39MnemonicGenerator().FromEntropy(entropy_bytes)
                logger.warning("Generated new mnemonic using fallback method")
                return str(new_mnemonic)
            except Exception as fallback_error:
                logger.error(f"Fallback mnemonic generation also failed: {fallback_error}")
                raise

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
            
            # Генерация адреса по индексу: m/84'/2'/0'/0/address_index (BIP84)
            bip84_acc = self.bip84_mst.Purpose().Coin().Account(0).Change(Bip84Changes.CHAIN_EXT).AddressIndex(address_index)
            
            # Получение адреса и ключей
            address = bip84_acc.PublicKey().ToAddress()
            private_key = bip84_acc.PrivateKey().Raw().ToHex()
            public_key = bip84_acc.PublicKey().RawCompressed().ToHex()
            
            # Валидация сгенерированного адреса
            if not self.validate_address(address):
                raise ValueError(f"Generated invalid Litecoin address: {address}")
            
            result = {
                "address": address,
                "private_key": SecureData(private_key),  # Безопасное хранение приватного ключа
                "public_key": public_key,
                "index": address_index,
                "path": f"m/84'/2'/0'/0/{address_index}"  # Обновлен путь для BIP84
            }
            
            # Увеличиваем индекс только если не был указан конкретный индекс
            if index is None:
                self.index_manager.increment_and_save()
                
            logger.info(f"Generated LTC address: {address}, index: {result['index']}")
            return result
            
        except Exception as e:
            logger.error(f"Error generating LTC address: {e}")
            raise

    def validate_address(self, address: str) -> bool:
        """
        Валидация адреса Litecoin.
        Поддерживает адреса форматов:
        - P2PKH (начинаются с 'L')
        - P2SH (начинаются с 'M')
        - Bech32 (начинаются с 'ltc1')
        """
        try:
            # Проверка Bech32 адресов (начинаются с ltc1)
            if address.startswith('ltc1'):
                # Декодируем и снова кодируем для проверки
                decoded = LtcAddrDecoder.Decode(address)
                encoded = LtcAddrEncoder.Encode(decoded)
                return encoded == address
            
            # Проверка Legacy адресов (начинаются с L или M)
            elif address.startswith('L') or address.startswith('M'):
                # Конвертируем в cash адрес для проверки
                cash_addr = BchAddrConverter.ToCashAddress(address)
                # Конвертируем обратно для проверки
                legacy_addr = BchAddrConverter.ToLegacyAddress(cash_addr)
                return legacy_addr == address
            
            return False
        except:
            return False

    def get_qr_code(self, address: str, amount: float = None) -> str:
        """Генерация QR-кода для адреса"""
        try:
            # Валидация адреса перед генерацией QR-кода
            if not self.validate_address(address):
                raise ValueError(f"Invalid Litecoin address: {address}")
                
            ltc_amount = f"?amount={amount}" if amount else ""
            qr_data = f"litecoin:{address}{ltc_amount}"
            
            # Всегда используем внешний сервис для генерации QR-кодов
            # чтобы избежать проблем с портами и валидностью URL
            return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={qr_data}"
            
        except Exception as e:
            logger.error(f"Error generating QR code: {e}")
            # Возвращаем просто адрес как fallback
            return address

    def health_check(self) -> Dict[str, Any]:
        """Проверка состояния кошелька"""
        try:
            # Проверка базовой функциональности
            test_account = self.bip84_mst.Purpose().Coin().Account(0)
            test_address = test_account.Change(Bip84Changes.CHAIN_EXT).AddressIndex(0)
            
            # Валидация тестового адреса
            address_valid = self.validate_address(test_address.PublicKey().ToAddress())
            
            return {
                "status": "healthy",
                "index": self.index_manager.index,
                "can_generate_addresses": True,
                "mnemonic_available": bool(self.mnemonic),
                "seed_available": bool(self.seed_bytes),
                "address_validation_working": address_valid
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "can_generate_addresses": False,
                "mnemonic_available": bool(self.mnemonic),
                "seed_available": bool(self.seed_bytes),
                "address_validation_working": False
            }

    def backup_wallet(self, backup_path: str) -> bool:
        """Создание резервной копии кошелька"""
        try:
            backup_data = {
                "mnemonic": self.mnemonic,
                "index": self.index_manager.index,
                "backup_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "address_type": "BIP84"  # Добавляем информацию о типе адресов
            }
            
            if self.encryption_key and CRYPTO_AVAILABLE:
                cipher = Fernet(self.encryption_key)
                encrypted_data = cipher.encrypt(json.dumps(backup_data).encode())
                with open(backup_path, 'wb') as f:
                    f.write(encrypted_data)
            else:
                with open(backup_path, 'w') as f:
                    json.dump(backup_data, f)
            
            logger.info(f"Wallet backup created at {backup_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating wallet backup: {e}")
            return False

    def restore_wallet(self, backup_path: str) -> bool:
        """Восстановление кошелька из резервной копии"""
        try:
            if not os.path.exists(backup_path):
                logger.error(f"Backup file {backup_path} not found")
                return False
            
            if self.encryption_key and CRYPTO_AVAILABLE:
                with open(backup_path, 'rb') as f:
                    encrypted_data = f.read()
                cipher = Fernet(self.encryption_key)
                decrypted_data = cipher.decrypt(encrypted_data)
                backup_data = json.loads(decrypted_data.decode())
            else:
                with open(backup_path, 'r') as f:
                    backup_data = json.load(f)
            
            # Восстановление мнемоники и индекса
            self.mnemonic = backup_data['mnemonic']
            self.index_manager.index = backup_data['index']
            
            # Реинициализация кошелька
            self.seed_bytes = Bip39SeedGenerator(self.mnemonic).Generate()
            coin_type = self.config.get('coin_type', Bip84Coins.LITECOIN)
            self.bip84_mst = Bip84.FromSeed(self.seed_bytes, coin_type)
            
            logger.info("Wallet restored from backup")
            return True
            
        except Exception as e:
            logger.error(f"Error restoring wallet: {e}")
            return False

    def get_balance_info(self, address: str) -> Dict[str, Any]:
        """Получение информации о балансе адреса"""
        # Валидация адреса перед запросом баланса
        if not self.validate_address(address):
            return {
                "address": address,
                "error": "Invalid Litecoin address",
                "confirmed": 0.0,
                "unconfirmed": 0.0,
                "total": 0.0
            }
        
        # Заглушка для реализации проверки баланса через внешний API
        # В реальной реализации здесь должен быть вызов API блокчейна
        return {
            "address": address,
            "confirmed": 0.0,
            "unconfirmed": 0.0,
            "total": 0.0
        }

# Глобальный экземпляр кошелька
try:
    ltc_wallet = LTCWallet()
except Exception as e:
    logger.error(f"Failed to initialize LTC wallet: {e}")
    # Создаем заглушку для избежания ошибок импорта
    class FallbackWallet:
        def generate_address(self, index=None):
            return {"address": "ERROR", "error": str(e)}
        def get_qr_code(self, address, amount=None):
            return "ERROR"
        def validate_address(self, address):
            return False
    ltc_wallet = FallbackWallet()
