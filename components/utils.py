import hashlib
import os
import re
import urllib.parse

import fastapi
import httpx
from uvicorn.server import logger
from aiolimiter import AsyncLimiter

from config import *
from components.models import *
from typing import AsyncGenerator, Tuple

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
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'ogg': 'video/ogg',
        'avi': 'video/x-msvideo',
        'mpeg': 'video/mpeg',
        'mov': 'video/quicktime',
        'mkv': 'video/x-matroska',
        'ts': 'video/mp2t',
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
    return True

def transform_file_path(file_path, mount_path_prefix_remove=mount_path_prefix_remove, mount_path_prefix_add=mount_path_prefix_add) -> str:
    try:
        # 解析URL，提取纯路径（自动去除?及查询参数）
        parsed = urllib.parse.urlparse(file_path)
        file_path = parsed.path

        # 按用户配置处理前缀移除（convert_mount_path=True时生效）
        if convert_mount_path and mount_path_prefix_remove and file_path.startswith(mount_path_prefix_remove):
            file_path = file_path[len(mount_path_prefix_remove):]
            # 修正可能的//开头问题
            if file_path.startswith('//'):
                file_path = file_path[1:]

        # 按用户配置处理前缀添加（convert_mount_path=True时生效）
        if convert_mount_path and mount_path_prefix_add:
            file_path = f"{mount_path_prefix_add}{file_path}"

    except Exception as e:
        logger.error(f"路径处理错误: {e}")

    # 按用户配置处理特殊字符
    if convert_special_chars:
        for char in special_chars_list:
            if char in file_path:
                file_path = file_path.replace(char, '‛'+char)

    logger.debug(f"处理后传给Alist的路径: {file_path}")
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

async def get_alist_raw_url(file_path, host_url, ua, client: httpx.AsyncClient) -> str:
    alist_api_url = f"{alist_server}/api/fs/get"

    # 传给Alist的路径由transform_file_path处理后生成（依赖用户配置）
    body = {
        "path": file_path,
        "password": ""
    }
    header = {
        "Authorization": alist_key,
        "Content-Type": "application/json;charset=UTF-8"
    }

    if ua is not None:
        header['User-Agent'] = ua

    try:
        req = await client.post(alist_api_url, json=body, headers=header)
        req.raise_for_status()
        resp_data = req.json()
    except httpx.ReadTimeout:
        logger.error("Alist服务器超时")
        raise fastapi.HTTPException(status_code=500, detail="Alist server timeout")
    except Exception as e:
        logger.error(f"Alist请求失败: {e}")
        raise fastapi.HTTPException(status_code=500, detail="Failed to request Alist server")

    code = resp_data['code']
    if code == 200:
        raw_url = resp_data['data']['raw_url']
        logger.debug(f"Alist返回原始URL: {raw_url}")

        # 移除可能的多余斜杠前缀（如/https:// → https://）
        if raw_url.startswith(("/http://", "/https://")):
            raw_url = raw_url[1:]

        # 严格按用户配置的替换规则执行
        if alist_download_url_replacement_map:
            for old_prefix, new_prefix in alist_download_url_replacement_map.items():
                # 统一处理结尾斜杠，避免因是否带/导致匹配失败
                old_prefix = old_prefix.rstrip('/')
                new_prefix = new_prefix.rstrip('/')
                # 仅替换前缀，不影响后续路径
                if raw_url.startswith(old_prefix):
                    raw_url = raw_url.replace(old_prefix, new_prefix, 1)  # 只替换一次，防止多路径错误
                    break

        logger.debug(f"按配置替换后URL: {raw_url}")
        return raw_url
    elif code == 403:
        logger.error("Alist API密钥错误（403）")
        raise fastapi.HTTPException(status_code=500, detail="Alist 403 Forbidden")
    else:
        logger.error(f"Alist错误: {resp_data['message']}")
        raise fastapi.HTTPException(status_code=500, detail=f"Alist错误: {resp_data['message']}")

async def get_file_info(item_id, api_key, media_source_id, client: httpx.AsyncClient) -> FileInfo:
    media_info_api = f"{emby_server}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={media_source_id}&api_key={api_key}"
    logger.info(f"请求Emby播放信息: {media_info_api}")
    try:
        media_info = await client.get(media_info_api)
        media_info.raise_for_status()
        media_info = media_info.json()
    except Exception as e:
        logger.error(f"Emby请求失败: {e}")
        raise fastapi.HTTPException(status_code=500, detail=f"Emby request failed: {e}")

    if media_source_id is None:
        all_source = []
        for i in media_info['MediaSources']:
            all_source.append(FileInfo(
                path=transform_file_path(i.get('Path')),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15)
            ))
        return all_source

    for i in media_info['MediaSources']:
        if i['Id'] == media_source_id:
            return FileInfo(
                path=transform_file_path(i.get('Path')),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15)
            )
    raise fastapi.HTTPException(status_code=500, detail="未找到MediaSourceId")

async def get_item_info(item_id, api_key, client) -> ItemInfo:
    item_info_api = f"{emby_server}/emby/Items?api_key={api_key}&Ids={item_id}"
    logger.debug(f"请求Emby条目信息: {item_info_api}")
    try:
        req = await client.get(item_info_api)
        req.raise_for_status()
        req = req.json()
    except Exception as e:
        logger.error(f"Emby条目请求失败: {e}")
        raise fastapi.HTTPException(status_code=500, detail="Emby request failed")

    if not req['Items']: 
        logger.debug(f"条目不存在: {item_id}")
        return None

    item_type = req['Items'][0]['Type'].lower()
    if item_type != 'movie':
        item_type = 'episode'
    season_id = int(req['Items'][0]['SeasonId']) if item_type == 'episode' else None

    return ItemInfo(
        item_id=int(item_id),
        item_type=item_type,
        season_id=season_id
    )

async def reverse_proxy(cache: AsyncGenerator[bytes, None],
                        url_task: str,
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
                logger.info("缓存播放完毕，切换至源播放")
            raw_url = await url_task

            request_header['host'] = raw_url.split('/')[2]
            async with client.stream("GET", raw_url, headers=request_header) as response:
                response.raise_for_status()
                if status_code == 206 and response.status_code != 206:
                    raise ValueError(f"预期206响应，实际收到{response.status_code}")
                async for chunk in response.aiter_bytes():
                    await limiter.acquire(len(chunk))
                    yield chunk
        except Exception as e:
            logger.error(f"反向代理失败: {e}")
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
        return re.search(pattern, target_string) is not None
    raise ValueError("无效的正则表达式")
