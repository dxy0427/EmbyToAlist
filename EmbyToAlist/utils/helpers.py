import re
import asyncio
import json
import hashlib

import fastapi
from loguru import logger
from aiocache import Cache

from ..api.alist import get_alist_raw_url
from ..config import ENABLE_UA_PASSTHROUGH, EMBY_SERVER
from ..models import ItemInfo, TVShowsInfo, FileInfo
from ..utils.common import ClientManager
from ..cache.manager import AppContext

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

        self.task_manager = AppContext.get_task_manager()
        
        ua_hash = hashlib.md5(self.ua.encode()).hexdigest()
        # 使用md5哈希值作为缓存key的一部分，避免过长的key
        self.key = f"raw_url:{self.path}:{ua_hash}"
        self.task_sub_key = self.ua  # 以UA区分任务
        
    async def create_task(self) -> None:
        # 如果缓存中没有，则使用 TaskManager 创建唯一任务
        if await self.cache.exists(self.key):
            self.raw_url = await self.cache.get(self.key)
            logger.debug(f"Raw Url Cache hit for {self.path}")
            return
        
        existing_task = await self.task_manager.get_task(RawLinkManager, self.path, sub_key=self.task_sub_key)
        if existing_task:
            logger.debug(f"Task already exists for {self.path} - reuse")
            return 
        
        task = asyncio.create_task(self._wrapped_download())
        await self.task_manager.create_task(RawLinkManager, self.path, task, sub_key=self.task_sub_key, ttl=600)
    
    async def _wrapped_download(self):
        try:
            raw_url = await self.cache_raw_url()
            await self.cache.set(self.key, raw_url, ttl=3600)
            self.raw_url = raw_url
            return raw_url
        finally:
            await self.task_manager.remove_task(RawLinkManager, self.path, sub_key=self.task_sub_key)

    
    async def cache_raw_url(self) -> str:
        """获取alist直链并缓存
        1. 如果是strm文件，请求strm，缓存真正的文件链接
        2. 如果是普通文件，使用alist的get_alist_raw_url方法获取直链
        """
        if self.is_strm:
            return await self.precheck_strm()
        else:
            return await get_alist_raw_url(
                self.path,
                self.ua
            )
            
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
                    body = await response.aread()
                    body = json.loads(body.decode())
                    logger.warning(f"Strm file returned JSON: {body}")
                    raise fastapi.HTTPException(status_code=500, detail="Strm file returned JSON response")
                
                # path中存储的是直链
                return self.path
            else:
                response.raise_for_status()
            
            raise fastapi.HTTPException(status_code=500, detail="Failed to request strm file")
    
    async def get_raw_url(self) -> str:
        """用于外部获取直链（自动触发任务/复用任务）"""
        if self.raw_url is not None:
            return self.raw_url
          
        if await self.cache.exists(self.key):
            self.raw_url = await self.cache.get(self.key)
            logger.debug(f"Cache hit for {self.path}")
            return self.raw_url
          
        task = await self.task_manager.get_task(RawLinkManager, self.path, sub_key=self.task_sub_key)
        if not task:
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task not created")
        
        try:
            return await task
        except asyncio.CancelledError:
            logger.warning("RawLinkManager task was cancelled")
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task was cancelled")
        except Exception as e:
            logger.error(f"Error: RawLinkManager task failed for path {self.path}, error: {e}")
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task")
        finally:
            await self.task_manager.remove_task(RawLinkManager, self.path, sub_key=self.task_sub_key)

# --- Emby Helpers ---

async def request_emby_json(api_url: str) -> dict:
    """
    请求Emby API并返回JSON数据
    
    Args:
        api_url (str): Emby API的URL
    Returns:
        dict: 返回的JSON数据
    """
    client = ClientManager.get_client()
    try:
        logger.debug(f"Requesting URL: {api_url}")
        response = await client.get(api_url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to request Emby API: {e}")
        raise fastapi.HTTPException(status_code=500, detail=f"Failed to request Emby server, {e}")


def emby_api(path: str, **params) -> str:
    """构建Emby API请求URL
    
    Args:
        path (str): Emby API路径
        **params: 其他查询参数
    Returns:
        str: 完整的Emby API请求URL
    """
    query = '&'.join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{EMBY_SERVER}{path}?{query}"
    logger.debug(f"Emby API query: {url}")
    return url

def build_item_info(item: dict) -> ItemInfo:
    """构建ItemInfo对象
    Args:
        item (dict): Emby API返回的items信息
    Returns:
        ItemInfo: 构建的ItemInfo对象
    """
    
    item_type = item.get('Type', '').lower()
    if item_type != 'movie':
        item_type = 'episode'

    tvshows_info = TVShowsInfo(
        series_id=int(item['SeriesId']),
        season_id=int(item['SeasonId']),
        index_number=int(item['IndexNumber'])
    ) if item_type == 'episode' else None

    return ItemInfo(
        item_id=int(item['Id']),
        item_type=item_type,
        in_progress=item.get('UserData', {}).get('PlaybackPositionTicks', 0) > 0,
        tvshows_info=tvshows_info
    )
    
def build_playback_info(data: dict) -> FileInfo:
    """解析 PlaybackInfo 返回的播放信息
    
    Args:
        data (dict): Emby PlaybackInfo 返回的json数据
    Returns:
        FileInfo: 包含文件信息的dataclass
    """
    
    return FileInfo(
        id=data.get('Id'),
        path=data.get('Path'),
        bitrate=data.get('Bitrate', 27962026),
        size=data.get('Size', 0),
        container=data.get('Container', None),
        # 获取15秒的缓存文件大小， 并取整
        cache_file_size=int(data.get('Bitrate', 27962026) / 8 * 20),
        name=data.get('Name'),
        # 是否为远程流
        is_strm=data.get('IsRemote', False)
    )