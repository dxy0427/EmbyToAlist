from fastapi import HTTPException
from loguru import logger

from ..config import EMBY_SERVER
from ..models import ItemInfo, FileInfo, TVShowsInfo
from ..utils.common import ClientManager
from typing import Union, Optional

def parse_playback_info(data, media_source_id: Optional[str] = None) -> FileInfo | list[FileInfo]:
    """解析 PlaybackInfo 返回的播放信息
    
    Args:
        data (dict): Emby PlaybackInfo 返回的json数据
        media_source_id (str): Emby MediaSource ID
    Returns:
        FileInfo: 包含文件信息的dataclass
        list[FileInfo]: 没有指定MediaSourceId时，返回所有文件信息的列表
    """
    if media_source_id is None:
        all_source = []
        for i in data['MediaSources']:
            all_source.append(FileInfo(
                id=i.get('Id'),
                path=i.get('Path'),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                # 获取15秒的缓存文件大小， 并取整
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15),
                name=i.get('Name'),
                # 是否为远程流
                is_strm=i.get('IsRemote', False)
            ))
        return all_source

    for i in data['MediaSources']:
        if i['Id'] == media_source_id:
            return FileInfo(
                id = i.get('Id'),
                path=i.get('Path'),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                # 获取15秒的缓存文件大小， 并取整
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15),
                name=i.get('Name'),
                # 是否为远程流
                is_strm=i.get('IsRemote', False)
            )
    # can't find the matched MediaSourceId in MediaSources
    raise HTTPException(status_code=500, detail="Can't match MediaSourceId")

async def get_item_info(item_id: str, api_key: str) -> ItemInfo | None:
    """获取Emby Item信息

    Args:
        item_id (str): Emby Item ID
        api_key (str): Emby API Key
    Returns:
        ItemInfo: 包含Item信息的dataclass
        None: 如果没有找到Item
    """
    client = ClientManager.get_client()
    item_info_api = f"{EMBY_SERVER}/emby/Items?api_key={api_key}&Ids={item_id}"
    logger.debug(f"Requesting Item Info: {item_info_api}")
    try:
        resp = await client.get(item_info_api)
        resp.raise_for_status()
        resp = resp.json()
    except Exception as e:
        logger.error(f"Error: get_item_info failed, {e}")
        raise HTTPException(status_code=500, detail="Failed to request Emby server, {e}")
    
    if not resp['Items']: 
        logger.debug(f"Item not found: {item_id};")
        return None
    
    item_type = resp['Items'][0]['Type'].lower()
    if item_type != 'movie': item_type = 'episode'
    
    if item_type == 'episode':
    
        tvshows_info = TVShowsInfo(
            series_id=int(resp['Items'][0]['SeriesId']),
            season_id=int(resp['Items'][0]['SeasonId']),
            index_number=int(resp['Items'][0]['IndexNumber'])
        )
    else:
        tvshows_info = None

    return ItemInfo(
        item_id=int(item_id),
        item_type=item_type,
        tvshows_info=tvshows_info
    )

async def get_series_info(series_id: int, season_id: int, api_key: str) -> list[ItemInfo]:
    """获取剧集某一个季的所有Item信息

    Args:
        series_id (int): Emby Series ID
        season_id (int): Emby Season ID
        api_key (str): Emby API Key

    Returns:
        list[ItemInfo]: 包含Item信息的dataclass列表
    """
    client = ClientManager.get_client()
    shows_info_api = f"{EMBY_SERVER}/emby/Shows/{series_id}/Episodes?SeasonId={season_id}&api_key={api_key}"
    
    try:
        req = await client.get(shows_info_api)
        req.raise_for_status()
        req = req.json()
    except Exception as e:
        logger.error(f"Error: get_series_info failed, {e}")
        raise HTTPException(status_code=500, detail="Failed to request Emby server, {e}")
    
    items = []
    for i in req['Items']:
        items.append(ItemInfo(
            item_id=int(i['Id']),
            item_type='episode',
            tvshows_info=TVShowsInfo(
                series_id=series_id,
                season_id=season_id,
                index_number=int(i['IndexNumber'])
            )
        ))
    return items
        
async def get_next_episode_item_info(series_id: int, season_id: int, item_id: int, api_key: str) -> ItemInfo | None:
    """获取剧集当前一季的下一集信息，并不会返回下一季的第一集

    Args:
        series_id (int): Emby Series ID
        season_id (int): Emby Season ID
        item_id (int): Emby Item ID
        api_key (str): Emby API Key

    Returns:
        ItemInfo: 包含Item信息的dataclass
        None: 如果没有找到下一集
    """
    items: list[ItemInfo] = await get_series_info(series_id, season_id, api_key)
    for i in items:
        if i.item_id == item_id:
            index = i.tvshows_info.index_number
            if index == len(items):
                return None
            return items[index]
        

# used to get the file info from emby server
async def get_file_info(item_id: str, api_key: str, media_source_id: Optional[str] = None) -> FileInfo | list[FileInfo]:
    """
    从Emby 的 PlaybackInfo 获取文件播放信息
    
    Args:
        item_id (str): Emby Item ID
        api_key (str): Emby API Key
        media_source_id (str): Emby MediaSource ID
    Returns:
        FileInfo: 包含文件信息的dataclass
        list[FileInfo]: 没有指定MediaSourceId时，返回所有文件信息的列表
    """
    client = ClientManager.get_client()
    
    media_info_api = f"{EMBY_SERVER}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={media_source_id}&api_key={api_key}"
    logger.info(f"Requested Info URL: {media_info_api}")
    
    try:
        media_info = await client.get(media_info_api)
        media_info.raise_for_status()
        media_info = media_info.json()
    except Exception as e:
        logger.error(f"Error: failed to request Emby server, {e}")
        raise HTTPException(status_code=500, detail=f"Failed to request Emby server, {e}")
    
    return parse_playback_info(media_info, media_source_id)