import asyncio
import os
from weakref import WeakValueDictionary

import aiofiles
import aiofiles.os
import httpx
from uvicorn.server import logger

from components.utils import *
from main import get_or_cache_alist_raw_url, FileInfo, RequestInfo, CacheStatus
from typing import AsyncGenerator, Optional, Tuple

global_cache_lock = asyncio.Lock()

cache_locks = WeakValueDictionary()

def get_cache_lock(subdirname, dirname):
    key = os.path.join(subdirname, dirname)  
    if key not in cache_locks:
        lock = asyncio.Lock()
        cache_locks[key] = lock
    return cache_locks[key]

async def read_file(
    file_path: str, 
    start_point: int = 0, 
    end_point: Optional[int] = None, 
    chunk_size: int = 1024*1024, 
) -> AsyncGenerator[bytes, None]:
    try:
        async with aiofiles.open(file_path, 'rb') as f:
            await f.seek(start_point)
            while True:
                if end_point is not None:
                    remaining = (end_point+1) - await f.tell()
                    if remaining <= 0:
                        break
                    chunk_size = min(chunk_size, remaining)
                data = await f.read(chunk_size)
                if not data:
                    break
                yield data
        logger.info("缓存内容已播放完毕，准备重定向到直连链接")
        raise StopIteration("Cache completed, redirect to direct link")
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
    except Exception as e:
        logger.error(f"Unexpected error occurred while reading file: {e}")

async def write_cache_file(item_id, request_info: RequestInfo, req_header=None, client: httpx.AsyncClient=None) -> bool:
    path = request_info.file_info.path
    file_size = request_info.file_info.size
    cache_size = request_info.file_info.cache_file_size
    subdirname, dirname = get_hash_subdirectory_from_path(path, request_info.item_info.item_type)
    if request_info.cache_status in {CacheStatus.PARTIAL, CacheStatus.HIT}:
        start_point = 0
        end_point = cache_size - 1
    elif request_info.cache_status == CacheStatus.HIT_TAIL:
        start_point = request_info.start_byte
        end_point = file_size - 1
    else:
        logger.error(f"Cache Error {request_info.start_byte}, File Size is None")
        return False
    if request_info.raw_url is None:
        raw_url = await request_info.raw_url_task
    else:
        raw_url = request_info.raw_url
    final_url = raw_url
    logger.info(f"Forcing final URL resolution for caching process, starting with: {raw_url}")
    try:
        final_url = await get_redirected_final_url(raw_url, client)
        logger.info(f"Final URL for caching has been resolved to: {final_url}")
    except Exception as e:
        logger.error(f"Failed to resolve final URL for caching: {e}")
        return False
    cache_file_name = f'cache_file_{start_point}_{end_point}'
    cache_file_path = os.path.join(cache_path, subdirname, dirname, cache_file_name)
    logger.debug(f"Start to cache file {start_point}-{end_point}: {item_id}, file path: {cache_file_path}")
    os.makedirs(os.path.dirname(cache_file_path), exist_ok=True)
    cache_write_tag_path = os.path.join(cache_path, subdirname, dirname, f'{cache_file_name}.tag')
    
    lock = get_cache_lock(subdirname, dirname)
    async with lock:
        for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
            if file.startswith('cache_file_') and not file.endswith('.tag'):
                file_range_start, file_range_end = map(int, file.split('_')[2:4])
                if start_point >= file_range_start and end_point <= file_range_end:
                    logger.warning(f"Cache Range Already Exists. Abort.")
                    if await aiofiles.os.path.exists(cache_write_tag_path):
                        await aiofiles.os.remove(cache_write_tag_path)
                    return False

    async with global_cache_lock:
        logger.info(f"获取全局缓存锁，开始处理视频 {item_id} 缓存")
        async with lock:  
            async with aiofiles.open(cache_write_tag_path, 'w') as f:
                pass
            try:
                for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
                    if file.startswith('cache_file_') and not file.endswith('.tag'):
                        file_range_start, file_range_end = map(int, file.split('_')[2:4])
                        if start_point >= file_range_start and end_point <= file_range_end:
                            logger.warning(f"等待全局锁期间，缓存已存在。Abort.")
                            await aiofiles.os.remove(cache_write_tag_path)
                            return False
                        elif start_point <= file_range_start and end_point >= file_range_end:
                            logger.warning(f"等待全局锁期间，发现旧缓存范围更小，删除旧缓存。")
                            await aiofiles.os.remove(os.path.join(cache_path, subdirname, dirname, file))
                
                if req_header is None:
                    req_header = {}
                else:
                    req_header = dict(req_header)
                req_header['host'] = final_url.split('/')[2]
                req_header['range'] = f"bytes={start_point}-{end_point}"
                
                resp = await client.get(final_url, headers=req_header, timeout=30)
                if resp.status_code != 206:
                    logger.error(f"Write Cache Error {start_point}-{end_point}: Upstream return code: {resp.status_code}")
                    logger.error(f"Upstream response headers: {resp.headers}")
                    raise ValueError("Upstream response code not 206")
                async with aiofiles.open(cache_file_path, 'wb') as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024*1024):
                        await f.write(chunk)
                logger.info(f"视频 {item_id} 缓存完成，释放全局缓存锁，文件路径: {cache_file_path}")
                await aiofiles.os.remove(cache_write_tag_path)
                return True
            except Exception as e:
                logger.error(f"Write Cache Error {start_point}-{end_point}: {e}")
                if await aiofiles.os.path.exists(cache_file_path):
                    await aiofiles.os.remove(cache_file_path)
                if await aiofiles.os.path.exists(cache_write_tag_path):
                    await aiofiles.os.remove(cache_write_tag_path)
                return False
            finally:
                logger.info(f"视频 {item_id} 缓存任务结束（无论成功与否），释放全局锁")

def read_cache_file(request_info: RequestInfo) -> AsyncGenerator[bytes, None]:
    subdirname, dirname = get_hash_subdirectory_from_path(request_info.file_info.path, request_info.item_info.item_type)
    file_dir = os.path.join(cache_path, subdirname, dirname)
    if not os.path.exists(file_dir):
        return None
    for file in os.listdir(file_dir):
        if file.startswith('cache_file_') and not file.endswith('.tag'):
            range_start, range_end = map(int, file.split('_')[2:4])
            if range_start <= request_info.start_byte <= range_end:
                adjusted_end_point = None if request_info.cache_status in {CacheStatus.PARTIAL, CacheStatus.HIT_TAIL} else request_info.end_byte - request_info.start_byte
                logger.info(f"Read Cache: {os.path.join(file_dir, file)}")
                return read_file(os.path.join(file_dir, file), request_info.start_byte - range_start, adjusted_end_point)
    return None

def get_cache_status(request_info: RequestInfo) -> bool:
    subdirname, dirname = get_hash_subdirectory_from_path(request_info.file_info.path, request_info.item_info.item_type)
    cache_dir = os.path.join(cache_path, subdirname, dirname)
    if not os.path.exists(cache_dir):
        return False
    for file in os.listdir(cache_dir):
        if file.endswith('.tag'):
            return False
    for file in os.listdir(cache_dir):
        if file.startswith('cache_file_'):
            range_start, range_end = map(int, file.split('_')[2:4])
            if verify_cache_file(request_info.file_info, (range_start, range_end)):
                if range_start <= request_info.start_byte <= range_end:
                    return True
            else:
                os.remove(os.path.join(cache_dir, file))
    return False

async def cache_next_episode(request_info: RequestInfo, api_key: str, client: httpx.AsyncClient) -> bool:
    if request_info.item_info.item_type != 'episode': 
        logger.debug(f"Skip caching next episode for non-episode item: {request_info.item_info.item_id}")
        return False
    next_episode_id = request_info.item_info.item_id + 1
    next_item_info = await get_item_info(next_episode_id, api_key, client)
    if next_item_info and next_item_info.season_id == request_info.item_info.season_id:
        next_file_info_list = await get_file_info(next_item_info.item_id, api_key, media_source_id=None, client=client)
        if not next_file_info_list:
            logger.warning(f"Could not find any media files for the next episode: {next_episode_id}")
            return False
        for file_info in next_file_info_list:
            next_request_info = RequestInfo(
                file_info=file_info,
                item_info=next_item_info,
                host_url=request_info.host_url,
                start_byte=0,
                end_byte=None,
                cache_status=CacheStatus.PARTIAL,
                raw_url_task=asyncio.create_task(
                    get_or_cache_alist_raw_url(
                        file_path=file_info.path, 
                        host_url=request_info.host_url, 
                        ua=request_info.headers.get("User-Agent"), 
                        client=client
                    )
                ),
            )
            if get_cache_status(next_request_info):
                logger.debug(f"Skip caching next episode version, cache already exists: {file_info.path}")
                continue
            else:
                logger.info(f"Starting background task to cache next episode version: {file_info.path}")
                await write_cache_file(next_episode_id, next_request_info, req_header=request_info.headers, client=client)
        return True
    return False

def verify_cache_file(file_info: FileInfo, cache_file_range: Tuple[int, int]) -> bool:
    start, end = cache_file_range
    if start == 0 and end == file_info.cache_file_size - 1:
        return True
    elif end == file_info.size - 1:
        return True
    else:
        return False

async def clean_cache(file_info: FileInfo, item_info: ItemInfo) -> bool:
    path = file_info.path
    subdirname, dirname = get_hash_subdirectory_from_path(path, item_info.item_type)
    cache_dir = os.path.join(cache_path, subdirname, dirname)
    lock = get_cache_lock(subdirname, dirname)
    async with lock:
        try:
            if os.path.exists(cache_dir):
                for file in os.listdir(cache_dir):
                    if file.startswith('cache_file_'):
                        await aiofiles.os.remove(os.path.join(cache_dir, file))
                if not os.listdir(cache_dir):
                    await aiofiles.os.rmdir(cache_dir)
                else:
                    logger.error(f"Clean Cache Error: Cache directory is not empty: {cache_dir}")
                    raise Exception("Cache directory is not empty")
            logger.info(f"Clean Cache: {cache_dir}")
            return True
        except Exception as e:
            logger.error(f"Clean Cache Error: {e}")
            return False
