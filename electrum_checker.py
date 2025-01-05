import asyncio
import aiohttp
import json
from typing import List, Dict, Optional

class ElectrumChecker:
    def __init__(self, servers: List[str] = None):
        if servers is None:
            self.servers = [
                "electrum.blockstream.info:50002",
                "electrum.bitcoinunlimited.info:50002",
                "fortress.qtornado.com:50002",
                "E-X.not.fyi:50002",
                "electrum.hsmiths.com:50002"
            ]
        else:
            self.servers = servers
            
        self.current_server = 0
        self.session = None
        
    async def connect(self):
        """Устанавливает соединение с сервером"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
            
    async def close(self):
        """Закрывает соединение"""
        if self.session:
            await self.session.close()
            self.session = None
            
    async def check_address(self, address: str) -> Optional[Dict]:
        """Проверяет адрес через Electrum-сервер"""
        try:
            await self.connect()
            
            server = self.servers[self.current_server]
            url = f"https://{server}/api/address/{address}"
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Проверяем наличие транзакций
                    if data.get('chain_stats', {}).get('tx_count', 0) > 0:
                        return {
                            'n_tx': data['chain_stats']['tx_count'],
                            'total_received': data['chain_stats']['funded_txo_sum'],
                            'balance': data['chain_stats']['funded_txo_sum'] - data['chain_stats']['spent_txo_sum']
                        }
                        
                elif response.status == 429:  # Rate limit
                    # Переключаемся на следующий сервер
                    self.current_server = (self.current_server + 1) % len(self.servers)
                    await asyncio.sleep(1)
                    return await self.check_address(address)
                    
            return None
            
        except Exception as e:
            print(f"Ошибка при проверке {address}: {e}")
            # Переключаемся на следующий сервер при ошибке
            self.current_server = (self.current_server + 1) % len(self.servers)
            return None 