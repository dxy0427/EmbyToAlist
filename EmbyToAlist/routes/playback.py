import fastapi
from loguru import logger

from ..utils.helpers import extract_api_key, RawLinkManager, ClientManager
from ..api.emby import get_file_info, parse_playback_info
from ..utils.path import transform_file_path, should_redirect_to_alist
from ..models import FileInfo
from ..config import EMBY_SERVER, CACHE_ENABLE

router = fastapi.APIRouter()

# Example Path: /emby/Users/xxxx/Items/xxxx
# @router.get('/emby/Users/{user_id}/Items/{item_id}')
# Example Path: /emby/Items/xxx/PlaybackInfo
@router.get('/emby/Items/{item_id}/PlaybackInfo')
@router.post('/emby/Items/{item_id}/PlaybackInfo')
async def playback_info(item_id: str, request: fastapi.Request):
    logger.debug(f"Received request for PlaybackInfo with item_id: {item_id}")
    client = ClientManager.get_client()
    
    params = request.query_params
    remote_url = f"{EMBY_SERVER}/emby/Items/{item_id}/PlaybackInfo"+f"?{params}"
    
    try:
        # Forward the request to the remote server
        response = await client.request(
            method=request.method,
            url=remote_url,
            headers=request.headers,
            content=await request.body(),
        )
        response.raise_for_status()
        
        data = response.json()
    except Exception as e:
        logger.error(f"Error during proxying request: {e}")
        return fastapi.Response(
            content="Internal Server Error",
            status_code=500,
        )
    
    files_info: list[FileInfo] = parse_playback_info(data)
    for index, each in enumerate(files_info):
        
        # 如果需要alist处理，如云盘路径，或strm流，提前通过异步缓存alist直链
        if should_redirect_to_alist(each.path) or each.is_strm:
            path = transform_file_path(each.path) if not each.is_strm else each.path
            
            raw_link_manager = RawLinkManager(path, each.is_strm, request.headers.get("User-Agent"))
            await raw_link_manager.create_task()
            
            redirected_url = None
        else:
            original_stream_url = data['MediaSources'][index]['DirectStreamUrl'] 
            redirected_url = f"{request.base_url}preventRedirect/emby{original_stream_url}"

        if redirected_url:
            data['MediaSources'][index]['DirectStreamUrl'] = redirected_url
            logger.debug(f"Play Url modified to: {redirected_url}")
    
    logger.debug(data)
    headers = dict(response.headers)
    headers.pop('content-length', None)
    
    # Prepare the response to forward back to the client
    return fastapi.responses.JSONResponse(
        content=data,
        status_code=response.status_code,
        headers=headers,
    )        
        
    
    # 如果满足alist直链条件，提前通过异步缓存alist直链
    