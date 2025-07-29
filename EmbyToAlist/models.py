import copy
from dataclasses import dataclass
from enum import StrEnum

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .service.alist.manager import RawLinkManager

class CacheRangeStatus(StrEnum):
    """ 请求的范围与本地缓存的匹配状态 """
        
    FULLY_CACHED = "Fully_Cached"
    """ 请求范围 完全 在缓存内部 """
    
    NOT_CACHED = "Not_Cached"
    """ 请求范围 完全 不在缓存内部 """
    
    PARTIALLY_CACHED = "Partially_Cached"
    """ 请求范围 部分 在缓存内部，部分超出 """
    
    FULLY_CACHED_TAIL = "Fully_Cached_Tail"
    """ 请求范围 完全 在缓存内部，且位于文件末尾 2MB 内 """
    
    UNKNOWN = "Unknown"
    """ 未知状态 """

@dataclass
class ItemInfo:
    """ 视频信息，如果type是movie，则tvshows_info为None """
    
    item_id: int
    item_type: str
    """episode or movie"""
    in_progress: bool = False
    """ 是否之前播放过 """
    tvshows_info: Optional['TVShowsInfo'] = None
    """ 剧集信息, 如果type是movie，则为None """
    
@dataclass
class TVShowsInfo:
    """ 剧集信息 """
    
    series_id: int
    """ 剧集id"""
    season_id: int
    """ 季id """
    index_number: int
    """ 剧集在季中的顺序 """
    

@dataclass
class FileInfo:
    id: str
    path: str
    bitrate: int
    size: int
    container: str
    cache_file_size: int
    name: str
    is_strm: bool = False
    
@dataclass
class RangeInfo:
    """ 范围信息 """
    
    request_range: tuple[int, int]
    """ 请求的范围 """
    
    cache_range: Optional[tuple[int, int]] = None
    """ 缓存的范围 """
    
    response_range: Optional[tuple[int, int]] = None
    """ 响应的范围 """
    
    # file_source_request_range: Optional[tuple[int, int]] = None
    # """ 文件源的请求范围(根据缓存状态变化) """
    
@dataclass
class RequestInfo:
    file_info: FileInfo
    item_info: ItemInfo
    range_info: RangeInfo
    cache_range_status: CacheRangeStatus = CacheRangeStatus.UNKNOWN
    api_key: Optional[str] = None
    raw_link_manager: Optional['RawLinkManager'] = None
    user_agent: Optional[str] = None
    is_HIGH_COMPAT_MEDIA_CLIENTS: bool = False
    """ 是否为对 Range 请求支持较好的播放器 """
    is_LOW_COMPAT_MEDIA_CLIENTS: bool = False
    """ 是否为对 Range 请求支持较差的播放器 """

    def __deepcopy__(self, memo):
        copied = RequestInfo(
            file_info=self.file_info,
            item_info=self.item_info,
            range_info=copy.deepcopy(self.range_info, memo),
            cache_range_status=self.cache_range_status,
            api_key=self.api_key,
            raw_link_manager=self.raw_link_manager,  # 👈 关键跳过点
            is_HIGH_COMPAT_MEDIA_CLIENTS=self.is_HIGH_COMPAT_MEDIA_CLIENTS,
            is_LOW_COMPAT_MEDIA_CLIENTS=self.is_LOW_COMPAT_MEDIA_CLIENTS
        )
        memo[id(self)] = copied
        return copied


response_headers_template = {
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'private, no-transform, no-cache'
}