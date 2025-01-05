import numpy as np
import hashlib
import time
from typing import List, Tuple
import ecdsa
from concurrent.futures import ThreadPoolExecutor
import queue
import secrets
import multiprocessing

class GPUBitcoinGenerator:
    def __init__(self, batch_size: int = 1000):
        self.batch_size = batch_size
        self.secp256k1 = ecdsa.SECP256k1
        self.address_queue = queue.Queue()
        self.num_threads = multiprocessing.cpu_count()  # Используем все доступные ядра CPU
        print(f"\n💻 CPU Threads available: {self.num_threads}")
        
    def generate_batch(self) -> List[Tuple[str, bytes]]:
        """Генерирует batch адресов используя многопоточность на CPU"""
        try:
            chunk_size = max(1, self.batch_size // self.num_threads)
            remaining = self.batch_size
            results = []
            
            print(f"   Генерация {self.batch_size} адресов в {self.num_threads} потоках")
            print(f"   Размер чанка: {chunk_size} адресов")
            
            with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
                futures = []
                while remaining > 0:
                    current_chunk = min(chunk_size, remaining)
                    futures.append(executor.submit(self._generate_chunk, current_chunk))
                    remaining -= current_chunk
                
                for i, future in enumerate(futures, 1):
                    chunk_results = future.result()
                    results.extend(chunk_results)
                    print(f"   ✓ Чанк {i}/{len(futures)} готов: {len(chunk_results)} адресов")
                    
            print(f"   ✓ Всего сгенерировано: {len(results)} адресов")
            return results[:self.batch_size]
            
        except Exception as e:
            print(f"❌ Ошибка при генерации: {e}")
            print("   ⚠️ Переключение на простой метод генерации")
            return self._generate_batch_simple()
    
    def _generate_chunk(self, chunk_size: int) -> List[Tuple[str, bytes]]:
        """Генерирует часть адресов в одном потоке"""
        chunk_results = []
        for _ in range(chunk_size):
            try:
                private_key = secrets.token_bytes(32)
                address, _ = self._private_to_address(private_key)
                chunk_results.append((address, private_key))
            except Exception as e:
                print(f"❌ Ошибка в чанке: {e}")
                continue
        return chunk_results
    
    def _generate_batch_simple(self) -> List[Tuple[str, bytes]]:
        """Простой резервный метод генерации"""
        results = []
        print(f"   Генерация {self.batch_size} адресов простым методом")
        for i in range(self.batch_size):
            try:
                private_key = secrets.token_bytes(32)
                address, _ = self._private_to_address(private_key)
                results.append((address, private_key))
                if (i + 1) % 10 == 0:
                    print(f"   ✓ Прогресс: {i + 1}/{self.batch_size}")
            except Exception as e:
                print(f"❌ Ошибка: {e}")
                continue
        print(f"   ✓ Сгенерировано: {len(results)} адресов")
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