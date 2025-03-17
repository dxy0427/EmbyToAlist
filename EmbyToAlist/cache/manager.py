import asyncio

from loguru import logger

from .CacheSystem import CacheSystem
from ..models import CacheRangeStatus
from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from .CacheSystem import ChunksWriter

class TaskManager():
    """任务管理器
    用于管理文件缓存写入任务，避免创建多个写入task
    每个文件包含头尾两个任务, head和tail
    {file_id: [ChunksWriter, ChunksWriter]}
    """
    def __init__(self):
        self.tasks = {}
        self.lock = asyncio.Lock()

    async def get_task(self, file_id: str, cache_range_status: CacheRangeStatus) -> Optional[asyncio.Task]:
        """获取任务
        """
        async with self.lock:
            if file_id in self.tasks:
                if cache_range_status is CacheRangeStatus.FULLY_CACHED_TAIL:
                    return self.tasks[file_id][1]
                else:
                    return self.tasks[file_id][0]
            else:
                return None
            
    async def create_task(self, file_id: str, writer: 'ChunksWriter', cache_range_status: CacheRangeStatus):
        """添加任务
        """
        async with self.lock:
            if file_id not in self.tasks:
                self.tasks[file_id] = {None, None}
            if cache_range_status is CacheRangeStatus.FULLY_CACHED_TAIL:
                self.tasks[file_id][1] = writer
            else:
                self.tasks[file_id][0] = writer
            logger.debug(f"Task added for {file_id} with status {cache_range_status}")
            logger.debug(f"Task list: {self.tasks}")

    async def remove_task(self, file_id: str, cache_range_status: CacheRangeStatus):
        """移除任务
        """
        async with self.lock:
            if file_id in self.tasks:
                if cache_range_status is CacheRangeStatus.FULLY_CACHED_TAIL:
                    self.tasks[file_id][1] = None
                else:
                    self.tasks[file_id][0] = None
                    
                # 如果两个任务都完成，则删除任务
                if self.tasks[file_id][0] is None and self.tasks[file_id][1] is None:
                    del self.tasks[file_id]
                    logger.debug(f"Task removed for {file_id} with status {cache_range_status}")

                logger.debug(f"Task removed for {file_id} with status {cache_range_status}")
                logger.debug(f"Task list: {self.tasks}")
            else:
                logger.debug(f"Task not found for {file_id} with status {cache_range_status}")
                logger.debug(f"Task list: {self.tasks}")
            
        
class CacheManager():
    _cache_system: CacheSystem = None
    
    @classmethod
    def init(cls, root_dir: str):
        cls.init_cache(root_dir)
        cls.init_request_manager()
    
    @classmethod
    def init_cache(cls, root_dir: str):
        cls._cache_system = CacheSystem(root_dir)
        
    @classmethod
    def get_cache_system(cls) -> CacheSystem:
        return cls._cache_system