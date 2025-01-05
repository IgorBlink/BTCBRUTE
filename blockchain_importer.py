import hashlib
import requests
import json
import sqlite3
from typing import List, Generator
import time
from pathlib import Path
import gzip
import csv
from bitcoin_checker import BitcoinChecker
import asyncio

class BlockchainImporter:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.checker = BitcoinChecker()
        
    async def check_address_transactions(self, address: str) -> dict:
        """
        Проверяет транзакции через Bitcoin API
        """
        try:
            if not hasattr(self, 'stats'):
                self.stats = {'checked': 0, 'with_tx': 0, 'without_tx': 0}
            self.stats['checked'] += 1

            result = self.checker.check_address(address)
            
            if result and result['n_tx'] > 0:
                self.stats['with_tx'] += 1
                print("\n" + "="*50)
                print("🎉 НАЙДЕН АКТИВНЫЙ КОШЕЛЕК! 🎉")
                print("="*50)
                print(f"📍 Адрес: {address}")
                print(f"📊 Транзакций: {result['n_tx']}")
                print(f"💰 Получено: {result['total_received']} satoshi")
                print(f"💎 Баланс: {result['balance']} satoshi")
                print("="*50)
                
                # Получаем дополнительную информацию
                balance = self.checker.get_balance(address)
                if balance > 0:
                    print(f"\nТекущий баланс: {balance} satoshi")
                
                return result
                
            self.stats['without_tx'] += 1
            if self.stats['checked'] % 1000 == 0:
                print(f"\n📊 Проверено: {self.stats['checked']:,}")
                print(f"   💰 С транзакциями: {self.stats['with_tx']}")
                
            return None
            
        except Exception as e:
            print(f"\n❌ Ошибка: {e}")
            return None
            
    async def __aenter__(self):
        await self.checker.connect()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.checker.close()

    def setup_database(self):
        """Создает оптимизированную структуру базы данных"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Удаляем старые таблицы если они существуют
        c.execute('DROP TABLE IF EXISTS success_addresses')
        c.execute('DROP TABLE IF EXISTS checked_addresses')
        
        # Таблица для адресов с транзакциями (успешные находки)
        c.execute('''CREATE TABLE IF NOT EXISTS success_addresses
                    (address TEXT PRIMARY KEY,
                     private_key_hex TEXT,
                     wif TEXT,
                     pattern TEXT,
                     n_tx INTEGER,
                     total_received INTEGER,
                     timestamp TEXT)''')
        
        # Таблица для проверенных адресов без транзакций
        c.execute('''CREATE TABLE IF NOT EXISTS checked_addresses
                    (address TEXT PRIMARY KEY,
                     private_key_hex TEXT,
                     wif TEXT,
                     pattern TEXT,
                     timestamp TEXT)''')
        
        # Индексы для оптимизации
        c.execute('CREATE INDEX IF NOT EXISTS idx_n_tx ON success_addresses(n_tx)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pattern ON checked_addresses(pattern)')
        
        conn.commit()
        conn.close()

    def _parse_blockchain_info(self, data: dict) -> dict:
        """Парсит ответ от blockchain.info API"""
        n_tx = data.get('n_tx', 0)
        if n_tx > 0:
            print(f"\n✅ Найдены транзакции!")
            print(f"   Количество транзакций: {n_tx}")
            print(f"   Получено всего: {data.get('total_received', 0)} satoshi")
            return {
                'n_tx': n_tx,
                'total_received': data.get('total_received', 0),
                'first_seen': data.get('first_seen', ''),
            }
        return None

    def _parse_blockchair(self, data: dict) -> dict:
        """Парсит ответ от blockchair API"""
        if 'data' in data and len(data['data']) > 0:
            address_data = next(iter(data['data'].values()))['address']
            tx_count = address_data.get('transaction_count', 0)
            
            if tx_count > 0:
                print(f"\n✅ Найдены транзакции!")
                print(f"   Количество транзакций: {tx_count}")
                print(f"   Получено всего: {address_data.get('received', 0)} satoshi")
                return {
                    'n_tx': tx_count,
                    'total_received': address_data.get('received', 0),
                    'first_seen': address_data.get('first_seen', ''),
                }
        return None

    def save_success_address(self, address: str, private_key: bytes, pattern: str, tx_info: dict):
        """Сохраняет адрес с транзакциями"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''INSERT OR REPLACE INTO success_addresses 
                    (address, private_key_hex, wif, pattern, n_tx, 
                     total_received, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'))''',
                 (address, private_key.hex(), self.private_key_to_wif(private_key),
                  pattern, tx_info['n_tx'], tx_info['total_received']))
        
        conn.commit()
        conn.close()

    def save_checked_address(self, address: str, private_key: bytes, pattern: str):
        """Сохраняет проверенный адрес без транзакций"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            wif = self.private_key_to_wif(private_key)
            c.execute('''INSERT OR IGNORE INTO checked_addresses 
                        (address, private_key_hex, wif, pattern, timestamp)
                        VALUES (?, ?, ?, ?, datetime('now'))''',
                     (address, private_key.hex(), wif, pattern))
            
            conn.commit()
            conn.close()
            
        except sqlite3.OperationalError as e:
            # Если возникла ошибка с структурой таблицы, пересоздаем базу
            print("\nПересоздание структуры базы данных...")
            self.setup_database()
            # Повторяем попытку сохранения
            self.save_checked_address(address, private_key, pattern)
        except Exception as e:
            print(f"\n❌ Ошибка при сохранении адреса: {e}")

    def is_address_checked(self, address: str) -> bool:
        """Проверяет, был ли адрес уже проверен"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("""SELECT 1 FROM checked_addresses WHERE address = ?
                    UNION ALL
                    SELECT 1 FROM success_addresses WHERE address = ?""",
                 (address, address))
        
        result = c.fetchone() is not None
        conn.close()
        return result

    def import_from_csv(self, file_path: str, batch_size: int = 1000):
        """Импортирует адреса из CSV файла"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        total_imported = 0
        batch = []
        
        print(f"Импорт адресов из {file_path}")
        
        with open(file_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Если есть приватный ключ, генерируем WIF
                private_key_hex = row.get('private_key_hex', '')
                wif = ''
                if private_key_hex:
                    try:
                        private_key = bytes.fromhex(private_key_hex)
                        wif = self.private_key_to_wif(private_key)
                    except:
                        pass
                
                batch.append((
                    row['address'],
                    private_key_hex,
                    wif,
                    row.get('first_seen', ''),
                    row.get('last_seen', ''),
                    int(row.get('total_received', 0)),
                    int(row.get('total_sent', 0)),
                    int(row.get('balance', 0)),
                    int(row.get('n_tx', 0))
                ))
                
                if len(batch) >= batch_size:
                    c.executemany('''INSERT OR REPLACE INTO known_addresses 
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', batch)
                    conn.commit()
                    total_imported += len(batch)
                    print(f"\rИмпортировано адресов: {total_imported}", end='')
                    batch = []
        
        if batch:
            c.executemany('INSERT OR REPLACE INTO known_addresses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', batch)
            conn.commit()
            total_imported += len(batch)
            
        print(f"\nВсего импортировано: {total_imported} адресов")
        conn.close()

    def import_from_blockchain_info(self, start_block: int, end_block: int):
        """Импортирует адреса напрямую из блокчейна через API"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        for block in range(start_block, end_block + 1):
            try:
                url = f"https://blockchain.info/block-height/{block}?format=json"
                response = requests.get(url)
                data = response.json()
                
                for tx in data['blocks'][0]['tx']:
                    # Собираем адреса из выходов
                    for output in tx['out']:
                        if 'addr' in output:
                            c.execute('''INSERT OR IGNORE INTO known_addresses 
                                       (address, first_seen, balance) 
                                       VALUES (?, datetime('now'), ?)''',
                                    (output['addr'], output['value']))
                
                conn.commit()
                print(f"\rОбработан блок: {block}", end='')
                
                # Небольшая задержка чтобы не перегружать API
                time.sleep(1)
                
            except Exception as e:
                print(f"\nОшибка при обработке блока {block}: {e}")
                continue
        
        conn.close()

    def get_address_info(self, address: str) -> dict:
        """Получает информацию об адресе из базы данных"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("SELECT * FROM known_addresses WHERE address=?", (address,))
        result = c.fetchone()
        
        conn.close()
        
        if result:
            return {
                'address': result[0],
                'first_seen': result[1],
                'last_seen': result[2],
                'total_received': result[3],
                'total_sent': result[4],
                'balance': result[5],
                'n_tx': result[6]
            }
        return None

    def get_rich_addresses(self, min_balance: int = 1000000) -> List[str]:
        """Получает список адресов с балансом выше указанного"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("SELECT address, balance FROM known_addresses WHERE balance >= ?", 
                 (min_balance,))
        results = c.fetchall()
        
        conn.close()
        return [(row[0], row[1]) for row in results] 

    def private_key_to_wif(self, private_key: bytes) -> str:
        """
        Конвертирует приватный ключ в формат WIF
        """
        # Добавляем версию сети (0x80 для mainnet)
        version_key = b'\x80' + private_key
        
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

    def check_address_local(self, address: str) -> dict:
        """
        Проверяет адрес локально через базу данных или файл
        """
        try:
            # Проверяем в базе данных
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            c.execute("""
                SELECT n_tx, total_received, first_seen 
                FROM success_addresses 
                WHERE address = ?
            """, (address,))
            
            result = c.fetchone()
            conn.close()
            
            if result:
                return {
                    'n_tx': result[0],
                    'total_received': result[1],
                    'first_seen': result[2]
                }
                
            # Если адреса нет в базе, проверяем через API
            return self.check_address_transactions(address)
            
        except Exception as e:
            print(f"Ошибка при локальной проверке адреса: {e}")
            return None 