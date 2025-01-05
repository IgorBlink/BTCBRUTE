import os
import hashlib
from bit import Key
from blockchain_importer import BlockchainImporter
import asyncio
import random
import string
from bitcoin_checker import BitcoinChecker
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import time
from datetime import datetime
import msvcrt
from pattern_generator import PatternGenerator, COMMON_PATTERNS
import aiohttp
from typing import List, Tuple, Dict
import json
import psutil
import threading
import sqlite3

# ASCII арт и информация о программе
PROGRAM_INFO = """
██████╗ ████████╗ ██████╗    ██████╗ ██████╗ ██╗   ██╗████████╗███████╗
██╔══██╗╚══██╔══╝██╔════╝    ██╔══██╗██╔══██╗██║   ██║╚══██╔══╝██╔════╝
██████╔╝   ██║   ██║         ██████╔╝██████╔╝██║   ██║   ██║   █████╗  
██╔══██╗   ██║   ██║         ██╔══██╗██╔══██╗██║   ██║   ██║   ██╔══╝  
██████╔╝   ██║   ╚██████╗    ██████╔╝██║  ██║╚██████╔╝   ██║   ███████╗
╚═════╝    ╚═╝    ╚═════╝    ╚═════╝ ╚═╝  ╚═╝ ╚═════╝    ╚═╝   ╚══════╝
                                                                       
        🔍 Bitcoin Address Checker and Generator 🔍
        Version: 2.0.0 (GPU Accelerated)
        Created by: IgorBlink
        GitHub: https://github.com/IgorBlink
"""

class ResourceMonitor:
    def __init__(self):
        self.cpu_percent = 0
        self.ram_percent = 0
        self.is_monitoring = False
        
    def start_monitoring(self):
        self.is_monitoring = True
        threading.Thread(target=self._monitor_resources, daemon=True).start()
        
    def stop_monitoring(self):
        self.is_monitoring = False
        
    def _monitor_resources(self):
        while self.is_monitoring:
            self.cpu_percent = psutil.cpu_percent(interval=1)
            self.ram_percent = psutil.virtual_memory().percent
            time.sleep(1)
            
    def get_stats(self):
        return {
            'cpu': self.cpu_percent,
            'ram': self.ram_percent
        }

class OptimizedAddressChecker:
    def __init__(self, batch_size: int = 1000, api_concurrency: int = 15,
                 cpu_usage: int = 80, ram_usage: int = 80, save_checked_addresses=True):
        self.batch_size = batch_size
        self.api_concurrency = api_concurrency
        self.cpu_target = cpu_usage
        self.ram_target = ram_usage
        self.session = None
        self.api_semaphore = asyncio.Semaphore(api_concurrency)
        self.resource_monitor = ResourceMonitor()
        
        # Увеличиваем количество воркеров
        cpu_count = psutil.cpu_count()
        self.process_pool = ProcessPoolExecutor(
            max_workers=cpu_count * 4  # В 4 раза больше процессов чем ядер
        )
        
        self.thread_pool = ThreadPoolExecutor(
            max_workers=cpu_count * 8  # В 8 раз больше потоков чем ядер
        )
        
        # Оптимизируем работу с API
        self.api_endpoints = [
            "https://blockstream.info/api",
            "https://blockchain.info",
            "https://api.blockcypher.com/v1/btc/main"
        ]
        self.cache = {}
        self.current_endpoint = 0
        self.last_adjustment_time = time.time()
        self.speed_history = []  # История скорости для анализа
        self.pattern_generator = PatternGenerator()
        self.save_checked_addresses = save_checked_addresses
        self.db_connection = sqlite3.connect('bitcoin_addresses.db')
        self.cursor = self.db_connection.cursor()
        self._create_tables()
        
    def _create_tables(self):
        """Создание необходимых таблиц в базе данных"""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS checked_addresses (
                address TEXT PRIMARY KEY,
                n_tx INTEGER DEFAULT 0,
                total_received INTEGER DEFAULT 0,
                balance INTEGER DEFAULT 0,
                last_checked TEXT
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS addresses_with_transactions (
                address TEXT PRIMARY KEY,
                n_tx INTEGER,
                total_received INTEGER,
                balance INTEGER,
                first_found TEXT
            )
        ''')
        
        self.db_connection.commit()

    def adjust_resources(self, current_speed: float):
        current_time = time.time()
        stats = self.resource_monitor.get_stats()
        
        # Сохраняем историю скорости
        self.speed_history.append(current_speed)
        if len(self.speed_history) > 10:
            self.speed_history.pop(0)
        
        # Анализируем тренд скорости
        speed_trend = 0
        if len(self.speed_history) >= 2:
            speed_trend = self.speed_history[-1] - self.speed_history[0]
        
        # Регулируем каждые 10 секунд
        if current_time - self.last_adjustment_time >= 10:
            self.last_adjustment_time = current_time
            
            # Если CPU загрузка низкая или скорость падает
            if stats['cpu'] < self.cpu_target - 5 or speed_trend < 0:
                # Агрессивно увеличиваем параллельность
                new_concurrency = min(500, self.api_concurrency * 2)
                if new_concurrency != self.api_concurrency:
                    print(f"\n⚡ Увеличиваем параллельность: {self.api_concurrency} -> {new_concurrency}")
                    self.api_concurrency = new_concurrency
                    self.api_semaphore = asyncio.Semaphore(new_concurrency)
            
            # Если CPU загрузка выше целевой
            elif stats['cpu'] > self.cpu_target + 5:
                # Плавно уменьшаем
                new_concurrency = max(50, self.api_concurrency - 20)
                if new_concurrency != self.api_concurrency:
                    print(f"\n⚡ Уменьшаем параллельность: {self.api_concurrency} -> {new_concurrency}")
                    self.api_concurrency = new_concurrency
                    self.api_semaphore = asyncio.Semaphore(new_concurrency)

    async def init_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        self.resource_monitor.start_monitoring()
            
    async def close(self):
        if self.session:
            await self.session.close()
        self.resource_monitor.stop_monitoring()
        self.process_pool.shutdown()

    async def check_address(self, address: str) -> Dict:
        # Сначала проверяем в локальной БД
        local_result = self.pattern_generator.check_address_exists(address)
        if local_result:
            has_tx, total_received = local_result
            if has_tx:
                return {
                    'n_tx': 1,  # Минимум 1 транзакция
                    'total_received': total_received,
                    'balance': 0  # Баланс неизвестен
                }
        
        # Если нет в локальной БД, проверяем через API
        if address in self.cache:
            return self.cache[address]
            
        async with self.api_semaphore:
            endpoint = self.api_endpoints[self.current_endpoint]
            self.current_endpoint = (self.current_endpoint + 1) % len(self.api_endpoints)
            
            try:
                url = f"{endpoint}/address/{address}"
                async with self.session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        result = self._parse_api_response(endpoint, data)
                        if result:
                            # Сохраняем в локальную БД
                            self.pattern_generator.add_known_address(
                                address,
                                has_transactions=result['n_tx'] > 0,
                                total_received=result['total_received']
                            )
                            self.cache[address] = result
                            return result
            except Exception as e:
                pass
                
        return None
        
    def _parse_api_response(self, endpoint: str, data: Dict) -> Dict:
        try:
            if "blockstream" in endpoint:
                return {
                    'n_tx': data.get('chain_stats', {}).get('tx_count', 0),
                    'total_received': data.get('chain_stats', {}).get('funded_txo_sum', 0),
                    'balance': data.get('chain_stats', {}).get('funded_txo_sum', 0) - 
                             data.get('chain_stats', {}).get('spent_txo_sum', 0)
                }
            elif "blockchain.info" in endpoint:
                return {
                    'n_tx': data.get('n_tx', 0),
                    'total_received': data.get('total_received', 0),
                    'balance': data.get('final_balance', 0)
                }
            elif "blockcypher" in endpoint:
                return {
                    'n_tx': data.get('n_tx', 0),
                    'total_received': data.get('total_received', 0),
                    'balance': data.get('balance', 0)
                }
            elif "btc.com" in endpoint:
                data = data.get('data', {})
                return {
                    'n_tx': data.get('tx_count', 0),
                    'total_received': data.get('received', 0),
                    'balance': data.get('balance', 0)
                }
            elif "blockchair" in endpoint:
                data = data.get('data', {}).get(address, {})
                return {
                    'n_tx': data.get('transaction_count', 0),
                    'total_received': data.get('received', 0),
                    'balance': data.get('balance', 0)
                }
            else:
                return {
                    'n_tx': data.get('transactions', 0),
                    'total_received': data.get('totalReceived', 0),
                    'balance': data.get('balance', 0)
                }
        except Exception:
            return None

async def process_batch(addresses: List[Tuple[str, bytes]], 
                       checker: OptimizedAddressChecker,
                       importer: BlockchainImporter,
                       save_checked: bool = True) -> Dict:
    """Асинхронная обработка пакета адресов"""
    results = {'checked': 0, 'with_tx': 0, 'errors': 0}
    
    async def check_single_address(address: str, private_key: bytes):
        try:
            result = await checker.check_address(address)
            results['checked'] += 1
            
            if result and result['n_tx'] > 0:
                results['with_tx'] += 1
                importer.save_success_address(address, private_key, "gpu_generated", result)
                print(f"\n💎 НАЙДЕН АКТИВНЫЙ КОШЕЛЕК!")
                print(f"   Адрес: {address}")
                print(f"   Транзакций: {result['n_tx']}")
                print(f"   Баланс: {result['balance']} satoshi")
            elif save_checked:  # Сохраняем пустые кошельки только если включена опция
                importer.save_checked_address(address, private_key, "gpu_generated")
                
        except Exception as e:
            results['errors'] += 1
            print(f"\n❌ Ошибка при проверке {address}: {e}")
    
    tasks = []
    for address, private_key in addresses:
        tasks.append(check_single_address(address, private_key))
    
    await asyncio.gather(*tasks)
    return results

def load_config():
    """Загрузка настроек из конфиг файла"""
    config_path = "btc_config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Ошибка при чтении конфига: {e}")
    return None

def save_config(config):
    """Сохранение настроек в конфиг файл"""
    config_path = "btc_config.json"
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        print("\n✅ Настройки сохранены в конфиг файл")
    except Exception as e:
        print(f"❌ Ошибка при сохранении конфига: {e}")

def get_settings():
    """Получение настроек от пользователя или из конфига"""
    config = load_config()
    if config:
        print("\n📝 Найден файл конфигурации. Использовать сохраненные настройки?")
        print("   1 - Да")
        print("   2 - Нет, ввести новые")
        while True:
            choice = msvcrt.getch().decode()
            if choice == '1':
                return config
            elif choice == '2':
                break
    
    # Запрашиваем новые настройки
    settings = {}
    
    print("\n💻 НАСТРОЙКА ПАРАМЕТРОВ:")
    
    # CPU Usage
    print("\nВведите желаемое использование CPU (10-100%):")
    while True:
        try:
            cpu = int(input(">>> "))
            if 10 <= cpu <= 100:
                settings['cpu_usage'] = cpu
                break
            print("❌ Введите число от 10 до 100")
        except ValueError:
            print("❌ Введите корректное число")
    
    # RAM Usage
    print("\nВведите желаемое использование RAM (10-100%):")
    while True:
        try:
            ram = int(input(">>> "))
            if 10 <= ram <= 100:
                settings['ram_usage'] = ram
                break
            print("❌ Введите число от 10 до 100")
        except ValueError:
            print("❌ Введите корректное число")
    
    # Batch Size
    print("\nВведите размер пакета (10-10000):")
    while True:
        try:
            batch = int(input(">>> "))
            if 10 <= batch <= 10000:
                settings['batch_size'] = batch
                break
            print("❌ Введите число от 10 до 10000")
        except ValueError:
            print("❌ Введите корректное число")
    
    # API Concurrency
    print("\nВведите количество одновременных API запросов (5-100):")
    while True:
        try:
            api = int(input(">>> "))
            if 5 <= api <= 100:
                settings['api_concurrency'] = api
                break
            print("❌ Введите число от 5 до 100")
        except ValueError:
            print("❌ Введите корректное число")
    
    # Pattern Mode
    print("\nВыберите режим генерации:")
    print("1 - Случайный")
    print("2 - По паттерну")
    while True:
        choice = msvcrt.getch().decode()
        if choice == '1':
            settings['pattern_mode'] = 'random'
            break
        elif choice == '2':
            settings['pattern_mode'] = 'pattern'
            break
    
    # Save Empty Addresses
    print("\nСохранять пустые (отчеканные) адреса?")
    print("1 - Да")
    print("2 - Нет")
    while True:
        choice = msvcrt.getch().decode()
        if choice == '1':
            settings['save_checked'] = True
            break
        elif choice == '2':
            settings['save_checked'] = False
            break
    
    # Сохраняем настройки
    save_config(settings)
    
    return settings

async def generate_and_check_addresses():
    os.system('cls' if os.name == 'nt' else 'clear')
    print(PROGRAM_INFO)
    
    # Получаем настройки
    settings = get_settings()
    
    # Выводим текущие настройки
    print("\n💻 ТЕКУЩИЕ НАСТРОЙКИ:")
    print(f"   CPU использование (цель): {settings['cpu_usage']}%")
    print(f"   RAM использование (цель): {settings['ram_usage']}%")
    print(f"   Доступно ядер CPU: {psutil.cpu_count()}")
    print(f"   Доступно RAM: {psutil.virtual_memory().total / (1024**3):.1f} GB")
    print(f"   Размер пакета: {settings['batch_size']}")
    print(f"   Режим генерации: {settings['pattern_mode']}")
    print(f"   Сохранение отчеканных адресов: {'Включено' if settings['save_checked'] else 'Отключено'}")
    print(f"   Сохранение адресов с балансом: Включено")
    
    print("\nНажмите любую клавишу для запуска...")
    msvcrt.getch()
    os.system('cls' if os.name == 'nt' else 'clear')
    
    # Дальше используем полученные настройки
    db_path = "test.db"
    importer = BlockchainImporter(db_path)
    checker = OptimizedAddressChecker(
        batch_size=settings['batch_size'],
        api_concurrency=settings['api_concurrency'],
        cpu_usage=settings['cpu_usage'],
        ram_usage=settings['ram_usage'],
        save_checked_addresses=settings['save_checked']
    )
    
    await checker.init_session()
    importer.setup_database()
    
    print(f"\n{'='*50}")
    print(f"🚀 Запуск программы: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")
    
    total_stats = {'checked': 0, 'with_tx': 0, 'errors': 0, 'start_time': time.time()}
    
    try:
        batch_num = 1
        while True:
            print(f"\n📝 Генерация пакета {batch_num}...")
            
            # Генерируем адреса в зависимости от режима
            if settings['pattern_mode'] == 'random':
                addresses = checker.pattern_generator.generate_with_pattern(
                    [], [], settings['batch_size']
                )
            else:
                addresses = checker.pattern_generator.generate_with_pattern(
                    COMMON_PATTERNS[settings['pattern_mode']], [], settings['batch_size']
                )
            
            print(f"\n🔍 Проверка пакета из {len(addresses)} адресов...")
            batch_stats = await process_batch(addresses, checker, importer, settings['save_checked'])  # Передаем параметр save_checked
            
            # Обновляем общую статистику
            for key in batch_stats:
                total_stats[key] += batch_stats[key]
            
            resource_stats = checker.resource_monitor.get_stats()
            elapsed_time = time.time() - total_stats['start_time']
            current_speed = total_stats['checked'] / elapsed_time if elapsed_time > 0 else 0
            
            checker.adjust_resources(current_speed)
            
            print(f"\n{'='*50}")
            print(f"📊 СТАТИСТИКА:")
            print(f"   ✓ Проверено всего: {total_stats['checked']:,}")
            print(f"   💎 С транзакциями: {total_stats['with_tx']}")
            print(f"   ❌ Ошибки: {total_stats['errors']}")
            print(f"   ⏱️ Прошло времени: {elapsed_time:.1f} сек")
            print(f"   🚀 Скорость: {current_speed:.1f} адресов/сек")
            print(f"   💻 CPU: {resource_stats['cpu']:.1f}%")
            print(f"   🧮 RAM: {resource_stats['ram']:.1f}%")
            print(f"   📦 Текущий размер пакета: {settings['batch_size']}")
            print(f"   🔄 Текущее кол-во параллельных запросов: {checker.api_concurrency}")
            if len(checker.speed_history) > 1:
                print(f"   📈 Тренд скорости: {'↑' if checker.speed_history[-1] > checker.speed_history[0] else '↓'}")
            print(f"{'='*50}")
            
            batch_num += 1
            
    except KeyboardInterrupt:
        print("\n\n⚠️ Программа остановлена пользователем")
    except Exception as e:
        print(f"\n\n❌ Критическая ошибка: {e}")
    finally:
        await checker.close()

if __name__ == "__main__":
    try:
        asyncio.run(generate_and_check_addresses())
    except KeyboardInterrupt:
        print("\n\n⚠️ Программа остановлена пользователем")
    except Exception as e:
        print(f"\n\n❌ Критическая ошибка: {e}") 