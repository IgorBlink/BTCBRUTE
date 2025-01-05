import hashlib
import secrets
from typing import Tuple, List
import ecdsa
from bitcoinlib.keys import HDKey
from bitcoinlib.wallets import Wallet
import threading
import queue
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import time

class BitcoinAddressGenerator:
    def __init__(self, db_path: str = None):
        self.secp256k1 = ecdsa.SECP256k1
        self.db_path = db_path
        self.address_queue = queue.Queue()
        self.running = False
        # Добавляем инициализацию импортера
        if db_path:
            from blockchain_importer import BlockchainImporter
            self.importer = BlockchainImporter(db_path)
            self.importer.setup_database()
        
    def setup_database(self):
        """Создает базу данных для хранения найденных адресов"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS known_addresses
                    (address TEXT PRIMARY KEY, private_key_hex TEXT, wif TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS found_matches
                    (address TEXT PRIMARY KEY, private_key_hex TEXT, 
                     wif TEXT, timestamp TEXT)''')
        conn.commit()
        conn.close()

    def add_known_address(self, address: str):
        """Добавляет известный адрес в базу данных"""
        if self.db_path:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO known_addresses (address) VALUES (?)", 
                     (address,))
            conn.commit()
            conn.close()

    def check_address_exists(self, address: str) -> bool:
        """Проверяет существование адреса в базе данных"""
        if self.db_path:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("SELECT address FROM known_addresses WHERE address=?", 
                     (address,))
            result = c.fetchone() is not None
            conn.close()
            return result
        return False

    def save_match(self, address: str, private_key: bytes):
        """Сохраняет найденное совпадение"""
        if self.db_path:
            wif = self.private_key_to_wif(private_key)
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("""INSERT INTO found_matches 
                        (address, private_key_hex, wif, timestamp) 
                        VALUES (?, ?, ?, datetime('now'))""",
                     (address, private_key.hex(), wif))
            conn.commit()
            conn.close()

    def generate_with_pattern(self, pattern: str, position: int = 0) -> Tuple[str, bytes, dict]:
        """
        Генерирует адреса с заданным паттерном и сохраняет их в базу
        """
        count = 0
        addresses_checked = set()
        
        print(f"\n🔍 Начало генерации для паттерна: {pattern}")
        print(f"   Позиция: {position}")
        
        while True:
            try:
                private_key = secrets.randbits(256)
                pattern_int = int(pattern, 2)
                mask = ((1 << len(pattern)) - 1) << (256 - len(pattern) - position)
                
                private_key = (private_key & ~mask) | (pattern_int << (256 - len(pattern) - position))
                key_bytes = private_key.to_bytes(32, 'big')
                
                public_key = self.private_to_public(key_bytes)
                address = self.public_to_address(public_key)
                
                if address in addresses_checked:
                    continue
                    
                addresses_checked.add(address)
                count += 1
                
                if not self.importer.is_address_checked(address):
                    # Просто сохраняем адрес без проверки
                    self.importer.save_checked_address(address, key_bytes, pattern)
                    
                    if count % 100 == 0:
                        print(f"\r💫 Сгенерировано {count} адресов для паттерна {pattern}", end="")
                
            except Exception as e:
                print(f"\n❌ Ошибка: {e}")
                time.sleep(1)
                continue

    def start_generation(self, patterns: List[str], num_threads: int = 4):
        """Запускает многопоточную генерацию с несколькими паттернами"""
        self.running = True
        
        def worker(pattern):
            while self.running:
                try:
                    address, private_key = self.generate_with_pattern(pattern)
                    self.address_queue.put((address, private_key))
                except Exception as e:
                    print(f"Error in worker: {e}")

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, pattern) for pattern in patterns]
            
        return futures

    def stop_generation(self):
        """Останавливает генерацию"""
        self.running = False

    def generate_private_key(self, fixed_bits: str = None) -> bytes:
        """
        Генерирует приватный ключ с возможностью фиксации определенных бит
        
        Args:
            fixed_bits: Строка из '0' и '1', определяющая фиксированные биты
        Returns:
            32 байта приватного ключа
        """
        if fixed_bits:
           
            fixed_value = int(fixed_bits, 2)
        
            random_bits = secrets.randbits(256 - len(fixed_bits))
           
            private_key = (fixed_value << (256 - len(fixed_bits))) | random_bits
            return private_key.to_bytes(32, 'big')
        else:
            return secrets.token_bytes(32)

    def private_to_public(self, private_key: bytes) -> bytes:
        """
        Преобразует приватный ключ в публичный используя ECDSA
        """
        signing_key = ecdsa.SigningKey.from_string(private_key, curve=self.secp256k1)
        verifying_key = signing_key.get_verifying_key()
        return verifying_key.to_string()

    def public_to_address(self, public_key: bytes) -> str:
        """
        Преобразует публичный ключ в биткоин-адрес
        """
        # SHA256
        sha256_hash = hashlib.sha256(public_key).digest()
        
        # RIPEMD160
        ripemd160 = hashlib.new('ripemd160')
        ripemd160.update(sha256_hash)
        hash160 = ripemd160.digest()
        
        # Добавляем версию сети (0x00 для mainnet)
        version_hash160 = b'\x00' + hash160
        
        # Двойной SHA256 для контрольной суммы
        double_sha256 = hashlib.sha256(
            hashlib.sha256(version_hash160).digest()
        ).digest()
        
        # Берем первые 4 байта как контрольную сумму
        checksum = double_sha256[:4]
        
        # Окончательный двоичный адрес
        binary_address = version_hash160 + checksum
        
        # Конвертируем в base58
        return self.base58_encode(binary_address)

    def base58_encode(self, data: bytes) -> str:
        """
        Кодирует байты в формат Base58
        """
        alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
        n = int.from_bytes(data, byteorder='big')
        result = ''
        
        while n > 0:
            n, r = divmod(n, 58)
            result = alphabet[r] + result
            
        # Добавляем ведущие '1' для каждого нулевого байта
        for b in data:
            if b == 0:
                result = '1' + result
            else:
                break
                
        return result

    def generate_address(self, fixed_bits: str = None) -> Tuple[str, bytes]:
        """
        Генерирует пару приватный ключ - биткоин адрес
        
        Returns:
            Кортеж (адрес, приватный_ключ)
        """
        private_key = self.generate_private_key(fixed_bits)
        public_key = self.private_to_public(private_key)
        address = self.public_to_address(public_key)
        return address, private_key 

    def private_key_to_wif(self, private_key: bytes) -> str:
        """
        Конвертирует приватный ключ в формат WIF (Wallet Import Format)
        
        Args:
            private_key: Приватный ключ в байтах
        Returns:
            WIF строка
        """
        # Добавляем версию сети (0x80 для mainnet)
        version_key = b'\x80' + private_key
        
        # Добавляем байт компрессии если нужно (0x01)
        # version_key += b'\x01'  # раскомментируйте для компрессированных ключей
        
        # Двойной SHA256 для контрольной суммы
        double_sha256 = hashlib.sha256(
            hashlib.sha256(version_key).digest()
        ).digest()
        
        # Берем первые 4 байта как контрольную сумму
        checksum = double_sha256[:4]
        
        # Окончательный двоичный WIF
        binary_wif = version_key + checksum
        
        # Конвертируем в base58
        return self.base58_encode(binary_wif) 

    def generate_hd_wallet(self, private_key: bytes) -> dict:
        """
        Генерирует HD кошелек в формате Electrum
        
        Args:
            private_key: Приватный ключ в байтах
        Returns:
            Словарь с адресами
        """
        # Создаем мастер-ключ из нашего приватного ключа
        master_key = HDKey.from_seed(private_key)
        
        # Получаем первый receiving адрес (m/0/0)
        receiving_key = master_key.child_private(0).child_private(0)
        receiving_address = receiving_key.address()
        
        # Получаем первый change адрес (m/1/0)
        change_key = master_key.child_private(1).child_private(0)
        change_address = change_key.address()
        
        return {
            'master_key': master_key,
            'receiving_address': receiving_address,
            'change_address': change_address
        } 

    def generate_addresses_batch(self, batch_size: int = 1000) -> List[Tuple[str, bytes]]:
        """Генерирует пакет адресов для массовой проверки"""
        results = []
        for _ in range(batch_size):
            address, private_key = self.generate_address()
            results.append((address, private_key))
        return results 