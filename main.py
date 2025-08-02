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

@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    app.requests_client = httpx.AsyncClient()
    yield
    await app.requests_client.aclose()

app = fastapi.FastAPI(lifespan=lifespan)

@get_time
@cached(ttl=600, cache=Cache.MEMORY, key_builder=lambda f, file_path, host_url, ua, client: file_path + host_url + ua)
async def get_or_cache_alist_raw_url(file_path, host_url, ua, client: httpx.AsyncClient) -> str:
    """创建或获取Alist Raw Url缓存，缓存时间为10分钟"""    
    raw_url = await get_alist_raw_url(file_path, host_url=host_url, ua=ua, client=client)
    logger.info("Alist Raw Url: " + raw_url)
    return raw_url

async def request_handler(expected_status_code: int,
                          cache: AsyncGenerator[bytes, None]=None,
                          request_info: RequestInfo=None,
                          resp_header: dict=None,
                          background_tasks: fastapi.BackgroundTasks=None,
                          client: httpx.AsyncClient=None
                          ) -> fastapi.Response:
    if request_info.cache_status != CacheStatus.UNKNOWN and background_tasks is not None and enable_cache_next_episode is True:
        background_tasks.add_task(cache_next_episode, request_info=request_info, api_key=request_info.api_key, client=client)
        logger.info("Started background task to cache next episode.")
    
    alist_raw_url_task = request_info.raw_url_task
    if expected_status_code == 302:
        raw_url = await alist_raw_url_task
        return fastapi.responses.RedirectResponse(url=raw_url, status_code=302)
    
    # 修复流式传输重定向冲突：缓存播放完毕后自然结束流
    if expected_status_code == 206 and cache is not None:
        async def cached_stream_wrapper():
            try:
                async for chunk in cache:
                    yield chunk
                # 缓存播放完毕，不抛异常，让流自然结束
                logger.info("缓存流已正常结束，后续请求将自动重定向")
            except Exception as e:
                logger.error(f"Error in cached stream: {e}")
                raise
        
        return fastapi.responses.StreamingResponse(
            cached_stream_wrapper(),
            headers=resp_header,
            status_code=206
        )
    
    if expected_status_code == 416:
        return fastapi.responses.Response(status_code=416, headers=resp_header)
    
    raise fastapi.HTTPException(status_code=500, detail=f"Unexpected argument: {expected_status_code}")

# for infuse, emby
@app.get('/Videos/{item_id}/{filename}')
@app.get('/videos/{item_id}/{filename}')
@app.get('/emby/Videos/{item_id}/{filename}')
@app.get('/emby/videos/{item_id}/{filename}')
async def redirect(item_id, filename, request: fastapi.Request, background_tasks: fastapi.BackgroundTasks):
    api_key = extract_api_key(request)
    media_source_id = request.query_params.get('MediaSourceId') or request.query_params.get('mediaSourceId')
    if not media_source_id:
        raise fastapi.HTTPException(status_code=400, detail="MediaSourceId is required")
    file_info_list = await get_file_info(item_id, api_key, media_source_id, client=app.requests_client)
    if not file_info_list:
        raise fastapi.HTTPException(status_code=404, detail="MediaSource not found or no files available.")
    file_info = file_info_list[0]

    item_info: ItemInfo = await get_item_info(item_id, api_key, client=app.requests_client)
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

    # 处理黑名单路径
    if not should_redirect_to_alist(file_info.path):
        final_host_url = host_url
        if 'FORCE_HTTPS_REDIRECT' in globals() and FORCE_HTTPS_REDIRECT and host_url.startswith('http://'):
            final_host_url = host_url.replace('http://', 'https://', 1)
            logger.info(f"Forcing redirect protocol to HTTPS based on config. New host base: {final_host_url}")
        redirected_url = f"{final_host_url}preventRedirect{request.url.path}{'?' + request.url.query if request.url.query else ''}"
        logger.info("Redirected Url: " + redirected_url)
        return fastapi.responses.RedirectResponse(url=redirected_url, status_code=302)

    # 处理缓存黑名单
    if cache_blacklist and any(match_with_regex(pattern, file_info.path) for pattern in cache_blacklist):
        logger.info("File is in cache blacklist. Redirecting directly.")
        request_info.raw_url_task = asyncio.create_task(get_or_cache_alist_raw_url(file_path=file_info.path,host_url=host_url,ua=ua,client=app.requests_client))
        return await request_handler(expected_status_code=302,request_info=request_info,client=app.requests_client)

    request_info.raw_url_task = asyncio.create_task(get_or_cache_alist_raw_url(file_path=file_info.path,host_url=host_url,ua=ua,client=app.requests_client))

    # 处理关闭缓存的情况
    if not enable_cache:
        return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)

    range_header = request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        logger.warning("Range header not found or invalid. Redirecting.")
        return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)

    start_byte = int(range_header.split('=')[1].split('-')[0])
    request_info.start_byte = start_byte

    if start_byte >= file_info.size:
        return await request_handler(expected_status_code=416, request_info=request_info, resp_header={'Content-Range': f'bytes */{file_info.size}'})

    # 核心的缓存/重定向逻辑
    cache_file_size = file_info.cache_file_size
    if start_byte < cache_file_size:
        request_info.cache_status = CacheStatus.PARTIAL
        if get_cache_status(request_info):
            logger.info("Cache hit. Serving partial content from cache.")
            resp_end_byte = cache_file_size - 1
            resp_headers = {'Content-Type': get_content_type(file_info.container),'Accept-Ranges': 'bytes','Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info.size}",'Content-Length': str(resp_end_byte - start_byte + 1),'Cache-Control': 'private, no-transform, no-cache','X-EmbyToAList-Cache': 'Hit'}
            return await request_handler(expected_status_code=206, cache=read_cache_file(request_info), request_info=request_info, resp_header=resp_headers, background_tasks=background_tasks, client=app.requests_client)
        else:
            logger.info("Cache miss. Starting background cache write and redirecting user.")
            background_tasks.add_task(write_cache_file, item_id, request_info, request.headers, client=app.requests_client)
            return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)
    elif file_info.size - start_byte < 2 * 1024 * 1024:
        request_info.cache_status = CacheStatus.HIT_TAIL
        if get_cache_status(request_info):
            end_byte = file_info.size - 1
            resp_headers = {'Content-Type': get_content_type(file_info.container),'Accept-Ranges': 'bytes','Content-Range': f"bytes {start_byte}-{end_byte}/{file_info.size}",'Content-Length': str(end_byte - start_byte + 1),'Cache-Control': 'private, no-transform, no-cache','X-EmbyToAList-Cache': 'Hit'}
            return await request_handler(expected_status_code=206, cache=read_cache_file(request_info), request_info=request_info, resp_header=resp_headers, background_tasks=background_tasks, client=app.requests_client)
        else:
            background_tasks.add_task(write_cache_file, item_id, request_info, request.headers, client=app.requests_client)
            return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)
    else:
        logger.info("Request is outside cache range. Redirecting to source.")
        return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)

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
            deleted_file_info = FileInfo(path=data.get('Item').get('Path'),bitrate=0,size=data.get('Item').get('Size'),container="",cache_file_size=0)
            deleted_item_info = ItemInfo(item_id=data.get('Item').get('Id'),item_type=data.get('Item').get('Type'),season_id=data.get('Item').get('SeasonId', None))
            if await clean_cache(deleted_item_info, deleted_file_info):  # 修复此处调用方式（加await）
                print(f"Cache for Item ID {deleted_item_info.item_id} has been cleaned.")
                return fastapi.responses.Response(status_code=200)
            else:
                logger.error(f"Failed to clean cache for Item ID {deleted_item_info.item_id}.")
        case _:
            raise fastapi.HTTPException(status_code=400, detail="Event not supported")

if __name__ == "__main__":
    log_level = log_level.lower()
    uvicorn.run(app, port=60001, host='0.0.0.0', log_config="logger_config.json", log_level=log_level)
