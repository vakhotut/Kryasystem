import os
from bip_utils import (
    Bip39MnemonicGenerator, 
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip44,
    Bip44Coins,
    Bip44Changes,
    LitecoinConf,
    Litecoin
)
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class LTCWallet:
    def __init__(self):
        self.mnemonic = os.getenv("LTC_MNEMONIC")
        if not self.mnemonic:
            # Генерация новой мнемонической фразы с помощью bip_utils
            self.mnemonic = Bip39MnemonicGenerator().Generate()
            logger.warning(f"Generated new mnemonic: {self.mnemonic}")
            # В продакшене нужно сохранить этот мнемоник в безопасное место
        
        # Валидация мнемонической фразы
        if not Bip39MnemonicValidator().IsValid(self.mnemonic):
            raise ValueError("Invalid mnemonic phrase")
        
        # Генерация seed из мнемоники
        self.seed_bytes = Bip39SeedGenerator(self.mnemonic).Generate()
        
        # Создание BIP44 кошелька для Litecoin
        self.bip44_mst = Bip44.FromSeed(self.seed_bytes, Bip44Coins.LITECOIN)
        self.address_index = 0
        logger.info("LTC Wallet initialized with bip_utils")

    def generate_address(self) -> Dict[str, Any]:
        try:
            # Генерация адреса по индексу: m/44'/2'/0'/0/address_index
            bip44_acc = self.bip44_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(self.address_index)
            
            # Получение адреса и ключей
            address = bip44_acc.PublicKey().ToAddress()
            private_key = bip44_acc.PrivateKey().Raw().ToHex()
            public_key = bip44_acc.PublicKey().RawCompressed().ToHex()
            
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
