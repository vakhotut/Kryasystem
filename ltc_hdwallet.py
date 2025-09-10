import os
from hdwallet import HDWallet
from hdwallet.cryptocurrencies import Litecoin
from hdwallet.utils import generate_mnemonic  # Используем встроенный генератор
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class LTCWallet:
    def __init__(self):
        self.mnemonic = os.getenv("LTC_MNEMONIC")
        if not self.mnemonic:
            # Генерация новой мнемонической фразы с помощью hdwallet
            self.mnemonic = generate_mnemonic(language="english", strength=128)
            logger.warning(f"Generated new mnemonic: {self.mnemonic}")
            # В продакшене нужно сохранить этот мнемоник в безопасное место
        
        self.hdwallet = HDWallet(cryptocurrency=Litecoin)
        # Передаем мнемонику как строку - библиотека сама обработает ее
        self.hdwallet.from_mnemonic(mnemonic=self.mnemonic)
        self.address_index = 0
        logger.info("LTC Wallet initialized")

    def generate_address(self) -> Dict[str, Any]:
        try:
            # Используем BIP44 путь для Litecoin: m/44'/2'/0'/0/address_index
            self.hdwallet.from_path(f"m/44'/2'/0'/0/{self.address_index}")
            address = self.hdwallet.address()
            private_key = self.hdwallet.private_key()
            public_key = self.hdwallet.public_key()
            
            result = {
                "address": address,
                "private_key": private_key,  # Внимание: храните безопасно!
                "public_key": public_key,
                "index": self.address_index
            }
            
            self.address_index += 1
            logger.info(f"Generated LTC address: {address}, index: {result['index']}")
            return result
        except Exception as e:
            logger.error(f"Error generating LTC address: {e}")
            raise

    def get_qr_code(self, address: str, amount: float = None) -> str:
        ltc_amount = f"?amount={amount}" if amount else ""
        return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=litecoin:{address}{ltc_amount}"

# Глобальный экземпляр кошелька
ltc_wallet = LTCWallet()
