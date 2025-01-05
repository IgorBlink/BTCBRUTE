import os
import hashlib
from typing import List, Tuple, Optional
import sqlite3
import secrets
import ecdsa
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import random

class PatternGenerator:
    def __init__(self, db_path: str = "address_db.db"):
        self.secp256k1 = ecdsa.SECP256k1
        self.db_path = db_path
        self.setup_database()
        self.thread_pool = ThreadPoolExecutor(
            max_workers=multiprocessing.cpu_count() * 4
        )
        
    def setup_database(self):
        """Создаем БД для хранения известных адресов"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS known_addresses (
                address TEXT PRIMARY KEY,
                has_transactions INTEGER,
                last_seen TEXT,
                total_received INTEGER
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS addr_idx ON known_addresses(address)')
        conn.commit()
        conn.close()
        
    def generate_with_pattern(self, pattern: List[int], mask: List[bool], batch_size: int = 1000) -> List[Tuple[str, bytes]]:
        """
        Генерирует адреса на основе паттерна
        pattern: список битов (0 или 1)
        mask: список булевых значений (True = фиксированный бит, False = случайный)
        """
        results = []
        for _ in range(batch_size):
            # Генерируем 256-битное число с учетом паттерна
            private_key_bits = []
            for i in range(256):
                if i < len(mask) and mask[i]:
                    # Используем бит из паттерна
                    private_key_bits.append(pattern[i] if i < len(pattern) else 0)
                else:
                    # Генерируем случайный бит
                    private_key_bits.append(secrets.randbelow(2))
            
            # Конвертируем биты в байты
            private_key_bytes = bytes(int(''.join(map(str, private_key_bits[i:i+8])), 2) 
                                    for i in range(0, 256, 8))
            
            # Создаем адрес
            address, priv_key = self._private_to_address(private_key_bytes)
            results.append((address, priv_key))
            
        return results
        
    def generate_with_shift_pattern(self, base_pattern: List[int], max_shift: int = 8, batch_size: int = 1000) -> List[Tuple[str, bytes]]:
        """
        Генерирует адреса со сдвигом паттерна
        base_pattern: базовый паттерн для сдвига
        max_shift: максимальное количество позиций для сдвига
        """
        results = []
        for _ in range(batch_size):
            # Выбираем случайный сдвиг
            shift = secrets.randbelow(max_shift)
            
            # Создаем сдвинутый паттерн
            shifted_pattern = [0] * shift + base_pattern + [0] * (256 - len(base_pattern) - shift)
            
            # Генерируем приватный ключ
            private_key_bytes = bytes(int(''.join(map(str, shifted_pattern[i:i+8])), 2)
                                    for i in range(0, 256, 8))
            
            # Создаем адрес
            address, priv_key = self._private_to_address(private_key_bytes)
            results.append((address, priv_key))
            
        return results
        
    def generate_with_repeating_pattern(self, base_pattern: List[int], batch_size: int = 1000) -> List[Tuple[str, bytes]]:
        """
        Генерирует адреса с повторяющимся паттерном
        base_pattern: паттерн для повторения
        """
        results = []
        pattern_len = len(base_pattern)
        
        for _ in range(batch_size):
            # Повторяем паттерн до 256 бит
            full_pattern = (base_pattern * (256 // pattern_len + 1))[:256]
            
            # Генерируем приватный ключ
            private_key_bytes = bytes(int(''.join(map(str, full_pattern[i:i+8])), 2)
                                    for i in range(0, 256, 8))
            
            # Создаем адрес
            address, priv_key = self._private_to_address(private_key_bytes)
            results.append((address, priv_key))
            
        return results
        
    def _private_to_address(self, private_key: bytes) -> Tuple[str, bytes]:
        """Конвертирует приватный ключ в адрес"""
        signing_key = ecdsa.SigningKey.from_string(private_key, curve=self.secp256k1)
        verifying_key = signing_key.get_verifying_key()
        public_key = verifying_key.to_string()
        
        # SHA256
        sha256_hash = hashlib.sha256(public_key).digest()
        
        # RIPEMD160
        ripemd160 = hashlib.new('ripemd160')
        ripemd160.update(sha256_hash)
        hash160 = ripemd160.digest()
        
        # Добавляем версию сети
        version_hash160 = b'\x00' + hash160
        
        # Двойной SHA256 для контрольной суммы
        double_sha256 = hashlib.sha256(
            hashlib.sha256(version_hash160).digest()
        ).digest()
        
        # Берем первые 4 байта как контрольную сумму
        checksum = double_sha256[:4]
        
        # Окончательный двоичный адрес
        binary_address = version_hash160 + checksum
        
        return self.base58_encode(binary_address), private_key
        
    def base58_encode(self, data: bytes) -> str:
        """Кодирует байты в формат Base58"""
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
        
    def check_address_exists(self, address: str) -> Optional[Tuple[bool, int]]:
        """Проверяет существование адреса в локальной БД"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT has_transactions, total_received FROM known_addresses WHERE address = ?', (address,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return bool(result[0]), result[1]
        return None
        
    def add_known_address(self, address: str, has_transactions: bool = False, total_received: int = 0):
        """Добавляет адрес в локальную БД"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO known_addresses (address, has_transactions, last_seen, total_received)
            VALUES (?, ?, datetime('now'), ?)
        ''', (address, int(has_transactions), total_received))
        conn.commit()
        conn.close()

    def import_addresses_from_file(self, file_path: str):
        """Импортирует адреса из текстового файла в БД"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        with open(file_path, 'r') as f:
            for line in f:
                address = line.strip()
                if address.startswith('1'):  # Проверяем что это Bitcoin адрес
                    cursor.execute('''
                        INSERT OR IGNORE INTO known_addresses (address, has_transactions, last_seen)
                        VALUES (?, 1, datetime('now'))
                    ''', (address,))
                    
        conn.commit()
        conn.close()

# Примеры паттернов
COMMON_PATTERNS = {
    # Паттерны из старых библиотек
    'old_random_1': ([0] * 200 + [1] * 8, [True] * 208 + [False] * 48),  # 200 нулей, потом единицы
    'old_random_2': ([0] * 240 + [1] * 16, [True] * 256),  # Почти все нули, кроме последних 16 бит
    'old_random_3': ([1] * 8 + [0] * 248, [True] * 256),  # 8 единиц в начале, остальное нули
    
    # Паттерны со сдвигом
    'shift_pattern_1': ([1,1,0,0,1,0,1,0] * 32, [True] * 256),  # Базовый паттерн для сдвига
    'shift_pattern_2': ([1,1,1,1,0,0,0,0] * 32, [True] * 256),  # Другой паттерн для сдвига
    
    # Паттерны из старого Python Random
    'python_random_1': ([0] * 128 + [1] * 32 + [0] * 96, [True] * 256),  # Старый баг Python Random
    'python_random_2': ([1] * 64 + [0] * 192, [True] * 256),  # Еще один баг
    
    # Специальные паттерны
    'special_1': ([0] * 200 + [1,0,1,0,1,0,1,0] * 7, [True] * 256),  # 200 нулей + повторяющийся паттерн
    'special_2': ([1,1,1,1] + [0] * 248 + [1,1,1,1], [True] * 256),  # Единицы по краям
}

def generate_with_old_python_pattern(self, batch_size: int = 1000) -> List[Tuple[str, bytes]]:
    """
    Генерирует адреса, имитируя баги старого Python Random
    """
    results = []
    patterns = [
        # Имитация бага с повторяющимися числами
        lambda: [1] * 32 + [0] * 192 + [1] * 32,
        # Имитация бага со сдвигом
        lambda: [int(i > 128) for i in range(256)],
        # Имитация бага с неполной рандомизацией
        lambda: [0] * 200 + [secrets.randbelow(2) for _ in range(56)]
    ]
    
    for _ in range(batch_size):
        pattern_func = random.choice(patterns)
        pattern = pattern_func()
        
        # Генерируем приватный ключ
        private_key_bytes = bytes(int(''.join(map(str, pattern[i:i+8])), 2)
                                for i in range(0, 256, 8))
        
        # Создаем адрес
        address, priv_key = self._private_to_address(private_key_bytes)
        results.append((address, priv_key))
        
    return results

def generate_with_shift_sequence(self, base_pattern: List[int], batch_size: int = 1000) -> List[Tuple[str, bytes]]:
    """
    Генерирует последовательность адресов, сдвигая паттерн на 1 бит каждый раз
    """
    results = []
    pattern_len = len(base_pattern)
    
    for i in range(batch_size):
        # Сдвигаем паттерн на i позиций
        shift = i % (256 - pattern_len)
        shifted_pattern = [0] * shift + base_pattern + [0] * (256 - pattern_len - shift)
        
        # Генерируем приватный ключ
        private_key_bytes = bytes(int(''.join(map(str, shifted_pattern[i:i+8])), 2)
                                for i in range(0, 256, 8))
        
        # Создаем адрес
        address, priv_key = self._private_to_address(private_key_bytes)
        results.append((address, priv_key))
        
    return results 