import re
import asyncio
import json
import hashlib

import fastapi
from loguru import logger
from aiocache import Cache

from ..api.alist import get_alist_raw_url
from ..config import ENABLE_UA_PASSTHROUGH
from ..utils.common import ClientManager

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

def extract_api_key(request: fastapi.Request):
    """从请求中提取API密钥"""
    api_key = request.query_params.get('api_key') or request.query_params.get('X-Emby-Token')
    if not api_key:
        # For Infuse
        auth_header = request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                api_key = match_token.group(1)
        else:
            # Sometimes Fileball uses x-emby-token header
            auth_header = request.headers.get('x-emby-token')
            if auth_header:
                api_key = auth_header
    return api_key
            
class RawLinkManager():
    """管理alist直链获取任务和缓存
    
    支持普通文件和strm文件
    """
    cache = Cache(Cache.MEMORY)
    
    def __init__(self, 
                 path: str,
                 is_strm: bool,
                 ua: str = None,
                 enable_ua_passthrough: bool = ENABLE_UA_PASSTHROUGH
                 ):
        if enable_ua_passthrough and ua is None:
            raise fastapi.HTTPException(status_code=500, detail="User-Agent passthrough is enabled, but User-Agent is None")
        
        if not enable_ua_passthrough:
            # use default media player user-agent
            self.ua = "mpv/0.33.1"
        else:
            self.ua = ua
            
        self.path = path
        self.is_strm = is_strm
        self.client = ClientManager.get_client()
        self.raw_url = None
        self.task = None
        
        ua_hash = hashlib.md5(self.ua.encode()).hexdigest()
        # 使用md5哈希值作为缓存key的一部分，避免过长的key
        self.key = f"raw_url:{self.path}:{ua_hash}"
        
    async def create_task(self) -> None:
        # 如果任务已存在:
        if self.task and not self.task.done():
            return
        
        self.raw_url = await self.cache.get(self.key, None)
        
        # 如果已经获取到直链:
        if self.raw_url is not None:
            logger.debug(f"Cache hit for {self.path}")
            return

        self.task = asyncio.create_task(self.cache_raw_url())
            
        self.task.add_done_callback(self.on_task_done)
        return
    
    async def cache_raw_url(self) -> str:
        if self.is_strm:
            raw_url = await self.precheck_strm()
        else:
            raw_url = await get_alist_raw_url(
                self.path,
                self.ua
                )
        await self.cache.set(self.key, raw_url, ttl=600)
        return raw_url
    
    async def precheck_strm(self) -> str:
        """预先请求strm文件地址，以便在请求时直接返回直链

        Returns:
            str: strm文件中的直链
        """
        # 流式请求可以避免获取响应体
        async with self.client.stream("GET", self.path, headers={
            "user-agent": self.ua
            }) as response:
            if response.status_code in {302, 301}:
                location = response.headers.get("Location")
                if location: 
                    logger.debug(f"Strm file redirected to {location}")
                    return location
                raise fastapi.HTTPException(status_code=500, detail="No Location header in response")
            elif response.status_code == 200:
                # 避免响应错误信息
                if "application/json" in response.headers.get("Content-Type", "").lower():
                    logger.warning("Strm file returned JSON response")
                    body = await response.aread()
                    body = json.loads(body.decode())
                    logger.debug(f"Response Text: {body}")
                    raise fastapi.HTTPException(status_code=500, detail="Strm file returned JSON response")
                
                # path中存储的是直链
                return self.path
            else:
                response.raise_for_status()
            
            raise fastapi.HTTPException(status_code=500, detail="Failed to request strm file")
    
    async def get_raw_url(self) -> str:
        if self.raw_url is not None:
            return self.raw_url
          
        if await self.cache.exists(self.key):
            self.raw_url = await self.cache.get(self.key)
            logger.debug(f"Cache hit for {self.path}")
            return self.raw_url
          
        if self.task is None:
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task not created")
        try:
            return await self.task
        except asyncio.CancelledError:
            logger.warning("RawLinkManager task was cancelled")
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task was cancelled")
        except Exception as e:
            logger.error(f"Error: RawLinkManager task failed for path {self.path}, error: {e}")
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task")
        
    def on_task_done(self, task) -> None:
        self.raw_url = task.result()
