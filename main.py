# main.py

from contextlib import asynccontextmanager

import fastapi
import httpx
import uvicorn
from uvicorn.server import logger
from aiocache import cached, Cache

from config import *
from components.utils import *
from components.cache import *
from components.models import *

# 使用上下文管理器，创建异步请求客户端
@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    app.requests_client = httpx.AsyncClient()
    yield
    await app.requests_client.aclose()

app = fastapi.FastAPI(lifespan=lifespan)

# return Alist Raw Url
@get_time
@cached(ttl=600, cache=Cache.MEMORY, key_builder=lambda f, file_path, host_url, ua, client: file_path + host_url + ua)
async def get_or_cache_alist_raw_url(file_path, host_url, ua, client: httpx.AsyncClient) -> str:
    """创建或获取Alist Raw Url缓存，缓存时间为5分钟"""    
    raw_url = await get_alist_raw_url(file_path, host_url=host_url, ua=ua, client=client)
    logger.info("Alist Raw Url: " + raw_url)
    return raw_url

# request_handler 保持原样，用于处理重定向和反代逻辑
# 我们将在主路由函数中决定何时调用它
async def request_handler(expected_status_code: int,
                          cache: AsyncGenerator[bytes, None]=None,
                          request_info: RequestInfo=None,
                          resp_header: dict=None,
                          background_tasks: fastapi.BackgroundTasks=None,
                          client: httpx.AsyncClient=None
                          ) -> fastapi.Response:
    """决定反代还是重定向，创建alist缓存
    
    :param expected_status_code: 期望返回的状态码，302或206
    :param cache: 内部缓存数据
    :param request_info: 请求信息
    :param resp_header: 需要返回的响应头
    :param client: httpx异步请求客户端
    
    :return fastapi.Response: 返回重定向或反代的响应
    """

    if request_info.cache_status != CacheStatus.UNKNOWN and background_tasks is not None and enable_cache_next_episode is True:
        background_tasks.add_task(cache_next_episode, request_info=request_info, api_key=request_info.api_key, client=client)
        logger.info("Started background task to cache next episode.")

    alist_raw_url_task = request_info.raw_url_task

    if expected_status_code == 302:
        raw_url = await alist_raw_url_task
        return fastapi.responses.RedirectResponse(url=raw_url, status_code=302)

    if expected_status_code == 206:
        start_byte = request_info.start_byte
        end_byte = request_info.end_byte
        local_cache_size = request_info.file_info.cache_file_size
        cache_status = request_info.cache_status

        if cache_status == CacheStatus.MISS:
            # Case 1: Requested range is entirely beyond the cache
            # Prepare Range header
            if end_byte is not None:
                source_range_header = f"bytes={start_byte}-{end_byte - 1}"
            else:
                source_range_header = f"bytes={start_byte}-"

            headers = dict(request_info.headers)
            headers["Range"] = source_range_header
            return await reverse_proxy(
                cache=None, 
                url_task=alist_raw_url_task, 
                request_header=headers,
                response_headers=resp_header,
                client=client
                )
        elif cache_status in {CacheStatus.HIT, CacheStatus.HIT_TAIL}:
            # Case 2: Requested range is entirely within the cache
            return fastapi.responses.StreamingResponse(cache, headers=resp_header, status_code=206)
        else:
            # Case 3: Requested range overlaps cache and extends beyond it
            source_start = local_cache_size

            if end_byte is not None:
                source_range_header = f"bytes={source_start}-{end_byte}"
            else:
                source_range_header = f"bytes={source_start}-"

            headers = dict(request_info.headers)
            headers["Range"] = source_range_header
            return await reverse_proxy(
                cache=cache, 
                url_task=alist_raw_url_task, 
                request_header=headers,
                response_headers=resp_header,
                client=client
                )

    if expected_status_code == 200:
        headers = dict(request_info.headers)
        headers["Range"] = f"bytes={request_info.file_info.cache_file_size}-"
        return await reverse_proxy(
            cache=cache,
            url_task=alist_raw_url_task,
            request_header=headers,
            response_headers=resp_header,
            client=client,
            status_code=200
            )

    if expected_status_code == 416:
        return fastapi.responses.Response(status_code=416, headers=resp_header)

    raise fastapi.HTTPException(status_code=500, detail=f"Unexpected argument: {expected_status_code}")

# for infuse
@app.get('/Videos/{item_id}/{filename}')
# for emby
@app.get('/videos/{item_id}/{filename}')
@app.get('/emby/Videos/{item_id}/{filename}')
@app.get('/emby/videos/{item_id}/{filename}')
async def redirect(item_id, filename, request: fastapi.Request, background_tasks: fastapi.BackgroundTasks):
    # Example: https://emby.example.com/emby/Videos/xxxxx/original.mp4?MediaSourceId=xxxxx&api_key=xxxxx

    api_key = extract_api_key(request)
    media_source_id = request.query_params.get('MediaSourceId') if 'MediaSourceId' in request.query_params else request.query_params.get('mediaSourceId')

    if not media_source_id:
        raise fastapi.HTTPException(status_code=400, detail="MediaSourceId is required")

    file_info: FileInfo = await get_file_info(item_id, api_key, media_source_id, client=app.requests_client)
    item_info: ItemInfo = await get_item_info(item_id, api_key, client=app.requests_client)
    # host_url example: https://emby.example.com:8096/
    host_url = str(request.base_url)
    ua = request.headers.get('User-Agent')
    request_info = RequestInfo(
        file_info=file_info, 
        item_info=item_info, 
        host_url=host_url,
        api_key=api_key,
        headers=request.headers,
        )

    logger.info(f"Requested Item ID: {item_id}")
    logger.info("MediaFile Mount Path: " + file_info.path)

    # 如果在not_redirect_paths中，直接返回Emby原始链接，不进行后续处理
    if not should_redirect_to_alist(file_info.path):
        redirected_url = f"{host_url}preventRedirect{request.url.path}{'?' + request.url.query if request.url.query else ''}"
        logger.info("Redirected Url: " + redirected_url)
        return fastapi.responses.RedirectResponse(url=redirected_url, status_code=302)

    # 关键修改1：修正cache_blacklist的匹配参数顺序
    # 原代码的参数顺序颠倒了，导致黑名单不生效
    if cache_blacklist and any(match_with_regex(pattern, file_info.path) for pattern in cache_blacklist):
        logger.info("File is in cache blacklist.")
        # 为黑名单文件创建Alist直链任务并直接重定向
        request_info.raw_url_task = asyncio.create_task(
            get_or_cache_alist_raw_url(file_path=file_info.path, host_url=host_url, ua=ua, client=app.requests_client)
        )
        return await request_handler(
            expected_status_code=302,
            request_info=request_info,
            client=app.requests_client
        )

    # 如果满足alist直链条件，提前通过异步缓存alist直链
    request_info.raw_url_task = asyncio.create_task(
        get_or_cache_alist_raw_url(
            file_path=file_info.path,
            host_url=host_url,
            ua=ua,
            client=app.requests_client
        )
    )

    # 如果没有启用缓存，直接返回Alist Raw Url
    if not enable_cache:
        return await request_handler(
            expected_status_code=302,
            request_info=request_info,
            client=app.requests_client
        )

    range_header = request.headers.get('Range', '')
    # 如果没有Range请求头（如VLC），直接重定向
    if not range_header.startswith('bytes='):
        logger.warning("Range header not found or not correctly formatted. Redirecting.")
        return await request_handler(
            expected_status_code=302,
            request_info=request_info,
            client=app.requests_client
        )

    # 解析Range头，获取请求的起始字节
    bytes_range = range_header.split('=')[1]
    if bytes_range.endswith('-'):
        start_byte = int(bytes_range[:-1])
        end_byte = None
    else:
        start_byte, end_byte = map(int, bytes_range.split('-'))

    logger.debug("Request Range Header: " + range_header)
    request_info.start_byte = start_byte
    request_info.end_byte = end_byte

    if start_byte >= file_info.size:
        logger.warning("Requested Range is out of file size.")
        return await request_handler(
            expected_status_code=416,
            request_info=request_info,
            resp_header={'Content-Range': f'bytes */{file_info.size}'}
        )

    cache_file_size = file_info.cache_file_size

    # 关键修改2：重构缓存处理逻辑
    # 场景一：请求落在缓存区域内 (start_byte < cache_file_size)
    if start_byte < cache_file_size:
        if end_byte is None or end_byte >= cache_file_size:
            request_info.cache_status = CacheStatus.PARTIAL
        else:
            request_info.cache_status = CacheStatus.HIT

        # 检查缓存是否存在且有效
        if get_cache_status(request_info):
            logger.info("Cached file exists and is valid. Serving from cache.")
            
            # 核心逻辑：只返回缓存部分。为此，必须精确计算响应头。
            # 响应的结束点就是缓存的末尾
            resp_end_byte = cache_file_size - 1
            
            resp_headers = {
                'Content-Type': get_content_type(file_info.container),
                'Accept-Ranges': 'bytes',
                'Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info.size}",
                'Content-Length': f'{resp_end_byte - start_byte + 1}',
                'Cache-Control': 'private, no-transform, no-cache',
                'X-EmbyToAList-Cache': 'Hit',
            }
            # 触发下一集缓存
            if enable_cache_next_episode:
                background_tasks.add_task(cache_next_episode, request_info=request_info, api_key=api_key, client=app.requests_client)
            
            # 直接返回 StreamingResponse，只包含缓存文件的内容
            return fastapi.responses.StreamingResponse(
                read_cache_file(request_info),
                headers=resp_headers,
                status_code=206
            )
        else:
            # 缓存不存在，则后台创建缓存，同时对当前用户重定向
            logger.info("Cache file not found. Starting background task to write cache and redirecting user.")
            background_tasks.add_task(
                write_cache_file, item_id, request_info, request.headers, client=app.requests_client
            )
            return await request_handler(
                expected_status_code=302,
                request_info=request_info,
                client=app.requests_client
            )

    # 场景二：请求落在文件末尾的缓存区
    elif file_info.size - start_byte < 2 * 1024 * 1024:
        request_info.cache_status = CacheStatus.HIT_TAIL
        # 这部分逻辑与原版类似，检查并返回末尾缓存，或后台创建并重定向
        if get_cache_status(request_info):
            resp_end_byte = file_info.size - 1 if end_byte is None else end_byte
            resp_file_size = resp_end_byte - start_byte + 1
            resp_headers = {
                'Content-Type': get_content_type(file_info.container),
                'Accept-Ranges': 'bytes',
                'Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info.size}",
                'Content-Length': f'{resp_file_size}',
                'Cache-Control': 'private, no-transform, no-cache',
                'X-EmbyToAList-Cache': 'Hit',
            }
            return fastapi.responses.StreamingResponse(read_cache_file(request_info), headers=resp_headers, status_code=206)
        else:
            background_tasks.add_task(write_cache_file, item_id, request_info, request.headers, client=app.requests_client)
            return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)
            
    # 场景三：请求完全落在缓存区之外 (这是缓存播放完后，客户端发出的新请求)
    else:
        logger.info("Request is outside cache range. Redirecting to source.")
        request_info.cache_status = CacheStatus.MISS
        # 直接重定向到Alist直链
        return await request_handler(
            expected_status_code=302, 
            request_info=request_info, 
            client=app.requests_client
        )

@app.post('/webhook')
async def webhook(request: fastapi.Request):
    if not clean_cache_after_remove_media:
        raise fastapi.HTTPException(status_code=400, detail="Webhook is not enabled")

    if 'application/json' not in request.headers.get('Content-Type', ''):
        raise fastapi.HTTPException(status_code=400, detail="Content-Type is not application/json")

    data = await request.json()

    match data.get('Event'):
        case "system.notificationtest":
            print("Webhook test successful.")
            return fastapi.responses.Response(status_code=200)
        case "library.deleted":
            if data.get('IsFolder') is True:
                raise fastapi.HTTPException(status_code=400, detail="Folder deletion is not supported.")

            deleted_file_info = FileInfo(
                path=data.get('Item').get('Path'),
                bitrate=0,
                size=data.get('Item').get('Size'),
                container="",
                cache_file_size=0
                )
            deleted_item_info = ItemInfo(
                item_id=data.get('Item').get('Id'),
                item_type=data.get('Item').get('Type'),
                # 电影：如果不存在SeasonId则为None
                season_id=data.get('Item').get('SeasonId', None)
                )

            if clean_cache(deleted_item_info, deleted_file_info):
                print(f"Cache for Item ID {deleted_item_info.item_id} has been cleaned.")
                return fastapi.responses.Response(status_code=200)
            else:
                logger.error(f"Failed to clean cache for Item ID {deleted_item_info.item_id}.")

        case _:
            raise fastapi.HTTPException(status_code=400, detail="Event not supported")


if __name__ == "__main__":
    log_level = log_level.lower()
    uvicorn.run(app, port=60001, host='0.0.0.0', log_config="logger_config.json", log_level=log_level)