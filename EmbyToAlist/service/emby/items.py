from loguru import logger

from ...models import ItemInfo, FileInfo
from .helpers import build_playback_info, build_item_info, emby_api, request_emby_json
from typing import Optional

async def get_item_info(item_id: str, api_key: str, user_id: Optional[str] = None) -> Optional[ItemInfo]:
    """获取某个Emby Item具体的信息

    Args:
        item_id (str): Emby Item ID
        api_key (str): Emby API Key
        user_id (str): Emby User ID
    Returns:
        ItemInfo: 包含Item信息的dataclass
        None: 如果没有找到Item
    """
    
    if user_id is None:
        user_id = ''
    
    item_info_api = emby_api("/emby/Items", api_key=api_key, UserId=user_id, Ids=item_id)
    
    data = await request_emby_json(item_info_api)
    
    assert len(data['Items']) != 0, f"Item not found: {item_id};"
    
    return build_item_info(data['Items'][0])

async def get_series_info(series_id: int, season_id: int, api_key: str) -> list[ItemInfo]:
    """获取剧集某一个季的所有Item信息

    Args:
        series_id (int): Emby Series ID，表示剧集（如一部电视剧）的唯一标识符，用于指定要查询的剧集
        season_id (int): Emby Season ID，表示该剧集下某一季的唯一标识符，用于限定只查询该季的剧集内容
        api_key (str): Emby API Key

    Returns:
        list[ItemInfo]: 包含Item信息的dataclass列表
    """
    # shows_info_api = f"{EMBY_SERVER}/emby/Shows/{series_id}/Episodes?SeasonId={season_id}&api_key={api_key}"
    shows_info_api = emby_api(
        f"/emby/Shows/{series_id}/Episodes",
        api_key=api_key,
        SeasonId=season_id,
    )
    
    data = await request_emby_json(shows_info_api)
    
    return [build_item_info(i) for i in data.get('Items', [])]
        
async def get_next_episode_item_info(
    series_id: int, 
    season_id: int, 
    item_id: int, 
    api_key: str
    ) -> ItemInfo | None:
    """获取剧集当前一季的下一集信息，并不会返回下一季的第一集

    Args:
        series_id (int): Emby Series ID，表示剧集（如一部电视剧）的唯一标识符，用于指定要查询的剧集
        season_id (int): Emby Season ID，表示该剧集下某一季的唯一标识符，用于限定只查询该季的剧集内容
        item_id (int): Emby Item ID，表示剧集中的某一集的唯一标识符，用于指定要查询的剧集内容
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
    # "{EMBY_SERVER}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={media_source_id}&api_key={api_key}"
    media_info_api = emby_api(
        f"/emby/Items/{item_id}/PlaybackInfo",
        api_key=api_key,
        MediaSourceId=media_source_id,
    )
    
    data = await request_emby_json(media_info_api)
    
    if media_source_id is None:
        # 如果没有指定MediaSourceId，返回所有文件信息
        return [build_playback_info(i) for i in data['MediaSources']]   
    
    else:
        # 如果指定了MediaSourceId，返回单个文件信息
        for i in data['MediaSources']:
            if i['Id'] == media_source_id:
                return build_playback_info(i)
        
        # 如果没有找到指定的MediaSourceId，返回None
        return None