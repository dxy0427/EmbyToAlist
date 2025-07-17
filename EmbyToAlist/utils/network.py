import asyncio

import fastapi
import httpx
from loguru import logger

from ..config import FORCE_CLIENT_RECONNECT
from ..models import RequestInfo, CacheRangeStatus
from ..cache.manager import AppContext
from ..utils.common import ClientManager
from ..cache.system import CacheSystem
from ..cache.writer import ChunksWriter
from typing import AsyncGenerator, TYPE_CHECKING
if TYPE_CHECKING:
    from ..utils.helpers import RawLinkManager

async def reverse_proxy(cache: AsyncGenerator[bytes, None],
                        request_header: dict,
                        response_headers: dict,
                        request_info: RequestInfo,
                        status_code: int = 206
                        ):
    """
    读取缓存数据和URL，返回合并后的流

    :param cache: 缓存数据
    :param request_header: 请求头，用于请求直链，包含host和range
    :param response_headers: 返回的响应头，包含调整过的range以及content-type
    :param request_info: 请求信息
    :param status_code: HTTP响应状态码，默认为206
    
    :return: fastapi.responses.StreamingResponse
    """
    cache_system: CacheSystem = AppContext.get_cache_system()
    
    async def merged_stream() -> AsyncGenerator[bytes, None]:
        
        try:
            # 如果缓存存在，先 yield 出缓存数据
            if cache is not None:
                async for chunk in cache:
                    yield chunk
            else:
                # 当缓存不存在且允许写缓存时，获取缓存写入器
                if request_info.cache_range_status != CacheRangeStatus.NOT_CACHED:
                    raw_link_manager = request_info.raw_link_manager
                    raw_url = await raw_link_manager.get_raw_url()
    
                    request_header['host'] = raw_url.split('/')[2]
                    
                    writer: ChunksWriter = await cache_system.get_writer(request_info, request_header)
                    await writer.write(raw_url)

                    start, end = request_info.range_info.response_range
                   
                    data_read = 0
                    if request_info.cache_range_status == CacheRangeStatus.PARTIALLY_CACHED:
                        if not request_info.is_HIGH_COMPAT_MEDIA_CLIENTS and not request_info.is_LOW_COMPAT_MEDIA_CLIENTS:
                            async for chunk in writer.read(start, None):
                                data_read += len(chunk)
                                yield chunk
                    
                    async for chunk in writer.read(start, end):
                        data_read += len(chunk)
                        yield chunk
                    
                    logger.debug(f"Expected data read: {end - start + 1}, Actual data read: {data_read}")
                    logger.debug(f"Read from {start} to {end}")
                    
            if not request_info.is_HIGH_COMPAT_MEDIA_CLIENTS and not request_info.is_LOW_COMPAT_MEDIA_CLIENTS:
                # 不是末尾则打断
                if FORCE_CLIENT_RECONNECT and request_info.cache_range_status == CacheRangeStatus.PARTIALLY_CACHED:
                    logger.info("Cache exhausted, breaking the connection")
                    raise ForcedReconnectError()
                
            if request_info.is_LOW_COMPAT_MEDIA_CLIENTS and request_info.cache_range_status == CacheRangeStatus.PARTIALLY_CACHED:
                # 低兼容播放器，反向代理后端
                logger.info("Streaming from backend...")
                client = ClientManager.get_client()
                request_header['range'] = f"bytes={request_info.range_info.cache_range[1]+1}-"
                async with client.stream(method="GET", url=raw_url, headers=request_header) as response:
                    if response.status_code != 206:
                        logger.error(f"Reverse_proxy failed, {response.status_code}")
                        raise fastapi.HTTPException(status_code=500, detail="Reverse Proxy Failed")
                    
                    async for chunk in response.aiter_bytes():
                        yield chunk
                    
        except ForcedReconnectError as e:
            logger.info(f"Expected ForcedReconnectError: {e}")
            raise fastapi.HTTPException(status_code=500, detail="Force Reconnect")
        except Exception as e:
            logger.error(f"Reverse_proxy failed, {e}")
            raise fastapi.HTTPException(status_code=500, detail="Reverse Proxy Failed")
        
    
    logger.debug(f"Response Headers: {response_headers}")
    return fastapi.responses.StreamingResponse(
        merged_stream(), 
        headers=response_headers, 
        status_code=status_code
        )

async def temporary_redirect(raw_link_manager: 'RawLinkManager') -> fastapi.Response:
    """重定向到alist直链
    
    :param raw_link_manager: RawLinkManager实例
    
    :return fastapi.Response: 重定向到alist直链的响应
    """
    raw_url = await raw_link_manager.get_raw_url()
    return fastapi.responses.RedirectResponse(url=raw_url, status_code=302)

def verify_download_response(resposne: httpx.Response):
    """验证status_code, 验证响应header

    Args:
        resposne (httpx.Response): HTTPX响应对象
    """
    if resposne.status_code == 416:
        logger.warning("Reponse Verification: 416 Range Not Satisfiable")
        logger.debug(f"Valid Range: {resposne.headers.get('Content-Range')}")
        raise ValueError("Reponse Verification Failed: Range Not Satisfiable")
    if resposne.status_code == 400:
        logger.warning("Reponse Verification: 400 Bad Request")
        logger.debug(f"Response Text: {resposne.text}")
        logger.debug(f"Response Headers: {resposne.headers}")
        raise ValueError("Reponse Verification Failed: 400 Bad Request")
    
    resposne.raise_for_status()
    
    content_type = resposne.headers.get('Content-Type')
    if "application/json;" in content_type:
        logger.warning("Reponse Verification: JSON Response")
        logger.debug(f"Response Text: {resposne.text}")
        raise ValueError("Reponse Verification Failed: JSON Response")
    

class ForcedReconnectError(Exception):
    """预期异常，用于强制播放器重新请求"""
    def __init__(self, message="Expected Error, Force Break the Connection"):
        self.message = message
        super().__init__(message)
