from bit import Key
from typing import Optional, Dict
import time
import requests

class BitcoinChecker:
    def __init__(self):
        self.api_endpoints = [
            "https://blockstream.info/api",
            "https://blockchain.info",
            "https://api.blockcypher.com/v1/btc/main"
        ]
        self.current_endpoint = 0
        self.last_request_time = 0
        
    def check_address(self, address: str) -> Optional[Dict]:
        """
        Проверяет Bitcoin адрес на наличие транзакций
        """
        try:
            # Добавляем задержку между запросами
            current_time = time.time()
            if current_time - self.last_request_time < 1:
                time.sleep(1)
            
            # Пробуем разные API
            if self.current_endpoint == 0:  # Blockstream
                url = f"{self.api_endpoints[0]}/address/{address}"
                response = requests.get(url)
                if response.status_code == 200:
                    data = response.json()
                    return {
                        'n_tx': data.get('chain_stats', {}).get('tx_count', 0),
                        'total_received': data.get('chain_stats', {}).get('funded_txo_sum', 0),
                        'balance': data.get('chain_stats', {}).get('funded_txo_sum', 0) - 
                                 data.get('chain_stats', {}).get('spent_txo_sum', 0)
                    }
                    
            elif self.current_endpoint == 1:  # Blockchain.info
                url = f"{self.api_endpoints[1]}/rawaddr/{address}"
                response = requests.get(url)
                if response.status_code == 200:
                    data = response.json()
                    return {
                        'n_tx': data.get('n_tx', 0),
                        'total_received': data.get('total_received', 0),
                        'balance': data.get('final_balance', 0)
                    }
                    
            else:  # BlockCypher
                url = f"{self.api_endpoints[2]}/addrs/{address}/balance"
                response = requests.get(url)
                if response.status_code == 200:
                    data = response.json()
                    return {
                        'n_tx': data.get('n_tx', 0),
                        'total_received': data.get('total_received', 0),
                        'balance': data.get('balance', 0)
                    }
                    
            # Если текущий API не ответил, переключаемся на следующий
            self.current_endpoint = (self.current_endpoint + 1) % len(self.api_endpoints)
            return self.check_address(address)
            
        except Exception as e:
            print(f"Ошибка при проверке адреса {address}: {e}")
           
            self.current_endpoint = (self.current_endpoint + 1) % len(self.api_endpoints)
            return None
            
    def get_transaction_history(self, address: str) -> list:
        """
        Получает историю транзакций для адреса
        """
        try:
            key = Key(address)
            return key.get_transactions()
            
        except Exception as e:
            print(f"Ошибка при получении истории транзакций: {e}")
            return []
            
    def get_balance(self, address: str) -> int:
        """
        Получает баланс адреса в сатоши
        """
        try:
            key = Key(address)
            return key.get_balance()
            
        except Exception as e:
            print(f"Ошибка при получении баланса: {e}")
            return 0