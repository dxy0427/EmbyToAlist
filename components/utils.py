# utils.py

import hashlib
import os
import re
import urllib.parse
import httpx
import fastapi
from uvicorn.server import logger
from aiolimiter import AsyncLimiter
import asyncio

from config import *
from components.models import *
from typing import AsyncGenerator, Tuple, Optional

def get_time(func):
    def wrapper(*args, **kwargs):
        import time
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger.info(f"Function {func.__name__} takes: {end - start} seconds")
        return result
    return wrapper

def get_content_type(container) -> str:
    content_types = {
        'mp4': 'video/mp4', 'webm': 'video/webm', 'ogg': 'video/ogg', 'avi': 'video/x-msvideo',
        'mpeg': 'video/mpeg', 'mov': 'video/quicktime', 'mkv': 'video/x-matroska', 'ts': 'video/mp2t',
    }
    return content_types.get(container.lower(), 'application/octet-stream')

def get_hash_subdirectory_from_path(file_path, media_type) -> Tuple[str, str]:
    parts = file_path.split('/')
    if media_type != 'movie':        
        file_path: str = os.path.join("series", os.path.join(parts[-3], parts[-2], parts[-1]))
    else:
        file_path: str = os.path.join("movie", os.path.join(parts[-2], parts[-1]))
    hasher = hashlib.md5()
    hasher.update(file_path.encode('utf-8'))
    hash_digest = hasher.hexdigest()
    return hash_digest[:2], hash_digest

def should_redirect_to_alist(file_path: str) -> bool:
    if any(file_path.startswith(path) for path in not_redirect_paths):
        logger.debug(f"File Path is in notRedirectPaths, return Emby Original Url")
        return False
    else:
        return True

def transform_file_path(file_path, mount_path_prefix_remove=mount_path_prefix_remove, mount_path_prefix_add=mount_path_prefix_add) -> str:
    if file_path.startswith('http://') or file_path.startswith('https://'):
        logger.debug(f"检测到输入路径为 URL，将直接使用: {file_path}")
        return file_path
    
    if convert_mount_path:
        try:
            mount_path_prefix_remove = mount_path_prefix_remove.removesuffix("/")
            mount_path_prefix_add = mount_path_prefix_add.removesuffix("/")
            if file_path.startswith(mount_path_prefix_remove):
                file_path = file_path[len(mount_path_prefix_remove):]
            if mount_path_prefix_add:
                file_path = mount_path_prefix_add + file_path
        except Exception as e:
            print(f"Error: convert_mount_path failed, {e}")

    if convert_special_chars:
        for char in special_chars_list:
            if char in file_path:
                file_path = file_path.replace(char, '‛'+char)

    if convert_mount_path or convert_special_chars: logger.debug(f"Processed File Path: {file_path}")
    return file_path

def extract_api_key(request: fastapi.Request):
    api_key = request.query_params.get('api_key') or request.query_params.get('X-Emby-Token')
    if not api_key:
        auth_header = request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                api_key = match_token.group(1)
    return api_key or emby_key

async def get_redirected_final_url(url: str, client: httpx.AsyncClient) -> str:
    try:
        current_url = url
        for i in range(5):
            logger.debug(f"正在追踪重定向 (第 {i+1} 次): {current_url}")
            head_resp = await client.head(current_url, follow_redirects=False, timeout=10)
            
            if head_resp.is_redirect:
                current_url = head_resp.headers['location']
                if not current_url.startswith('http'):
                    parsed_original_url = urllib.parse.urlparse(str(head_resp.url))
                    current_url = urllib.parse.urljoin(f"{parsed_original_url.scheme}://{parsed_original_url.netloc}", current_url)
                logger.info(f"发现重定向，新 URL: {current_url}")
                continue
            
            logger.info(f"找到最终地址 (状态码 {head_resp.status_code}): {current_url}")
            return current_url
        
        logger.warning("达到最大重定向次数，使用当前 URL。")
        return current_url
    except Exception as e:
        logger.error(f"追踪重定向时发生错误: {e}，将使用原始替换后的 URL。")
        return url

async def get_alist_raw_url(file_path, host_url, ua, client: httpx.AsyncClient) -> Optional[str]:
    if direct_url_handler["enable"]:
        for pattern in direct_url_handler["match_patterns"]:
            if re.match(pattern, file_path):
                logger.info(f"路径 '{file_path}' 匹配直接URL处理规则，将跳过 Alist API 调用。")
                direct_url = file_path
                for old_base, new_base in direct_url_handler["replacement_map"].items():
                    if direct_url.startswith(old_base):
                        direct_url = direct_url.replace(old_base, new_base, 1)
                        logger.info(f"域名已替换，新 URL: {direct_url}")
                        break
                
                if direct_url_handler["resolve_final_url"]:
                    return await get_redirected_final_url(direct_url, client)
                else:
                    return direct_url

    logger.debug(f"路径未匹配直接URL规则，将通过 Alist API 获取直链: {file_path}")
    alist_api_url = f"{alist_server}/api/fs/get"
    body = {"path": file_path, "password": ""}
    header = {"Authorization": alist_key, "Content-Type": "application/json;charset=UTF-8"}
    if ua is not None:
        header['User-Agent'] = ua

    try:
        req = await client.post(alist_api_url, json=body, headers=header)
        req.raise_for_status()
        req = req.json()
    except httpx.ReadTimeout:
        logger.error("Alist server response timeout, please check your network connectivity to Alist server")
        raise fastapi.HTTPException(status_code=500, detail="Alist server response timeout")
    except Exception as e:
        logger.error(f"Error: get_alist_raw_url failed, {e}")
        logger.error(f"Alist Server Return a {req.status_code} Error. Info: {req.text}")
        raise fastapi.HTTPException(status_code=500, detail=f"Failed to request Alist server: {e}")

    code = req['code']
    if code == 200:
        return req['data']['raw_url']
    elif code == 403:
        logger.error("Alist server response 403 Forbidden, Please check your Alist Key")
        raise fastapi.HTTPException(status_code=500, detail="Alist return 403 Forbidden, Please check your Alist Key")
    else:
        logger.error(f"Error: {req['message']}")
        raise fastapi.HTTPException(status_code=500, detail=f"Alist Server Error: {req['message']}")

async def get_file_info(item_id, api_key, media_source_id, client: httpx.AsyncClient) -> FileInfo:
    media_info_api = f"{emby_server}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={media_source_id}&api_key={api_key}"
    logger.info(f"Requested Info URL: {media_info_api}")
    try:
        media_info = await client.get(media_info_api)
        media_info.raise_for_status()
        media_info = media_info.json()
    except Exception as e:
        logger.error(f"Error: failed to request Emby server, {e}")
        raise fastapi.HTTPException(status_code=500, detail=f"Failed to request Emby server, {e}")

    for i in media_info['MediaSources']:
        if i['Id'] == media_source_id:
            return FileInfo(
                path=transform_file_path(i.get('Path')),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15)
            )
    raise fastapi.HTTPException(status_code=500, detail="Can't match MediaSourceId")

async def get_item_info(item_id, api_key, client) -> ItemInfo:
    item_info_api = f"{emby_server}/emby/Items?api_key={api_key}&Ids={item_id}"
    logger.debug(f"Requesting Item Info: {item_info_api}")
    try:
        req = await client.get(item_info_api)
        req.raise_for_status()
        req = req.json()
    except Exception as e:
        logger.error(f"Error: get_item_info failed, {e}")
        raise fastapi.HTTPException(status_code=500, detail="Failed to request Emby server, {e}")
    if not req['Items']: 
        logger.debug(f"Item not found: {item_id};")
        return None
    item_type = req['Items'][0]['Type'].lower()
    if item_type != 'movie': item_type = 'episode'
    season_id = int(req['Items'][0]['SeasonId']) if item_type == 'episode' else None
    return ItemInfo(
        item_id=int(item_id),
        item_type=item_type,
        season_id=season_id
    )

async def reverse_proxy(cache: AsyncGenerator[bytes, None],
                        url_task: asyncio.Task[str],
                        request_header: dict,
                        response_headers: dict,
                        client: httpx.AsyncClient,
                        status_code: int = 206
                        ):
    limiter = AsyncLimiter(10*1024*1024, 1)
    async def merged_stream():
        try:
            if cache is not None:
                async for chunk in cache:
                    await limiter.acquire(len(chunk))
                    yield chunk
                logger.info("Cache exhausted, streaming from source")
            raw_url = await url_task
            request_header['host'] = raw_url.split('/')[2]
            async with client.stream("GET", raw_url, headers=request_header) as response:
                response.raise_for_status()
                if status_code == 206 and response.status_code != 206:
                    raise ValueError(f"Expected 206 response, got {response.status_code}")
                async for chunk in response.aiter_bytes():
                    await limiter.acquire(len(chunk))
                    yield chunk
        except Exception as e:
            logger.error(f"Reverse_proxy failed, {e}")
            raise fastapi.HTTPException(status_code=500, detail="Reverse Proxy Failed")
    return fastapi.responses.StreamingResponse(
        merged_stream(), 
        headers=response_headers, 
        status_code=status_code
        )

def validate_regex(word: str) -> bool:
    try:
        re.compile(word)
        return True
    except re.error:
        return False

def match_with_regex(pattern, target_string):
    if validate_regex(pattern):
        return re.search(pattern, target_string)
    else:
        raise ValueError("Invalid regex pattern")