import asyncio

import httpx
from loguru import logger

from .CacheSystem import CacheSystem
from ..utils.common import ClientManager
    
class FileRequest():
    """文件请求
    """
    def __init__(self, url: str, headers: dict):
        self.url = url
        self.headers = headers
        self.client: httpx.AsyncClient = ClientManager.get_client()
        self.lock = asyncio.Lock()
        self.response: httpx.Response = self.start_connection()
        
    async def get_or_start_conn(self) -> httpx.Response:
        async with self.lock:
            if self.response is None:
                self.response: httpx.Response = await self.client.stream("GET", self.url, headers=self.headers).__aenter__()
                if self.response.status_code != 206:
                    raise ValueError(f"Expected 206 response, got {self.response.status_code}")
                
                logger.debug(f"Connection started for {self.url}")
                return self.response
            else:
                return self.response
                
    async def close_conn(self):
        async with self.lock:
            if self.response is not None:
                await self.response.__aclose__()
                logger.debug(f"Connection closed for {self.url}")
                
        
class FileRequestManager():
    """管理文件请求任务
    """
    def __init__(self):
        self.requests = {}
        self.lock = asyncio.Lock()
        
    async def get_or_create_request(self, file_id: str, headers: dict, url: str) -> FileRequest:
        async with self.lock:
            file_range = headers.get("Range").split('=')[1]
            if file_id not in self.requests:
                self.requests[file_id] = {}
            
            if file_range in self.requests[file_id]:
                return self.requests[file_id][file_range]
            
            request = FileRequest(url, headers)
            self.requests[file_id][file_range] = request
            return request
    
    async def request_exists(self, file_id: str, headers: dict) -> bool:
        async with self.lock:
            file_range = headers.get("Range").split('=')[1]
            return file_id in self.requests and file_range in self.requests[file_id]
    
    async def close_request(self, file_id: str):
        """默认关闭该文件的所有请求

        Args:
            file_id (str): 文件ID
        """
        async with self.lock:
            if file_id in self.requests:
                for request in self.requests[file_id].values():
                    await request.close_conn()
                del self.requests[file_id]
        
class CacheManager():
    _cache_system: CacheSystem = None
    _request_manager: FileRequest = None
    
    @classmethod
    def init(cls, root_dir: str):
        cls.init_cache(root_dir)
        cls.init_request_manager()
    
    @classmethod
    def init_cache(cls, root_dir: str):
        cls._cache_system = CacheSystem(root_dir)
        
    @classmethod
    def get_cache_system(cls) -> CacheSystem:
        return cls._cache_system
    
    @classmethod
    def init_request_manager(cls):
        cls._request_manager = FileRequestManager()
    
    @classmethod
    def get_request_manager(cls) -> FileRequestManager:
        return cls._request_manager