import httpx
from loguru import logger

from typing import Optional

# a wrapper function to get the time of the function
def get_time(func):
    def wrapper(*args, **kwargs):
        import time
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger.info(f"Function {func.__name__} takes: {end - start} seconds")
        return result
    return wrapper

class ClientManager():
    _client: Optional[httpx.AsyncClient] = None
    
    @classmethod
    def init_client(cls):
        if cls._client is None:
            cls._client = httpx.AsyncClient()
    
    @classmethod
    def get_client(cls):
        if cls._client is None:
            logger.error("Request Client not initialized")
            raise ValueError("Request Client not initialized")
        return cls._client
    
    @classmethod
    async def close_client(cls):
        if cls._client is not None:
            await cls._client.aclose()

def get_content_type(container) -> str:
    """文件格式对应的Content-Type映射"""
    content_types = {
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'ogg': 'video/ogg',
        'avi': 'video/x-msvideo',
        'mpeg': 'video/mpeg',
        'mov': 'video/quicktime',
        'mkv': 'video/x-matroska',
        'ts': 'video/mp2t',
    }

    # 返回对应的Content-Type，如果未找到，返回一个默认值
    return content_types.get(container.lower(), 'application/octet-stream')