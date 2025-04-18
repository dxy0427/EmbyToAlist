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