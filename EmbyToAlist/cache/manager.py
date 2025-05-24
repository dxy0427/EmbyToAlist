import asyncio

from loguru import logger

from typing import Optional, TYPE_CHECKING, Any, Union, Type
if TYPE_CHECKING:
    from .CacheSystem import CacheSystem

class TaskManager():
    """任务管理器
    用于管理ChunksWriter实例，避免创建多个写入task
    每个文件包含头尾两个任务, head和tail
    {file_id: [ChunksWriter, ChunksWriter]}
    """
    def __init__(self):
        self.tasks: dict[str, dict[str, list]] = {}
        self.lock = asyncio.Lock()
        
    def _get_type_key(self, task_type: Union[str, Type]) -> str:
        if isinstance(task_type, str):
            return task_type
        else:
            return task_type.__name__

    async def get_task(self, task_type: Union[str, Type], file_id: str, sub_key: Any = None) -> Optional[Any]:
        """获取任务，没有任务则返回None
        
        Args:
            task_type (str): 任务类型
            file_id (str): 文件ID
            sub_key (Any): 子键，默认为None
        Returns:
            Optional[Any]: 任务实例或None
        """
        type_key = self._get_type_key(task_type)
        async with self.lock:
            return self.tasks.get(type_key, {}).get(file_id, {}).get(sub_key, None)
            
    async def create_task(self, task_type: Union[str, Type], file_id: str, task_instance: Any, sub_key: Any = None):
        """添加任务
        
        Args:
            task_type (str): 任务类型
            file_id (str): 文件ID
            task_instance (Any): 任务实例
            sub_key (Any): 子键，默认为None   
        """
        type_key = self._get_type_key(task_type)
        async with self.lock:
            self.tasks.setdefault(type_key, {}).setdefault(file_id, {})[sub_key] = task_instance
            
    async def remove_task(self, task_type: Union[str, Type], file_id: str, sub_key: Any = None):
        """移除任务
        
        Args:
            task_type (str): 任务类型
            file_id (str): 文件ID
            sub_key (Any): 子键，默认为None
        """
        type_key = self._get_type_key(task_type)
        async with self.lock:
            task_group = self.tasks.get(type_key, {}).get(file_id, {})
            if sub_key in task_group:
                del task_group[sub_key]
                logger.debug(f"Task removed for {file_id} with sub key '{sub_key}'")

            else:
                logger.debug(f"Task not found for {file_id} with sub key '{sub_key}'")
            
            # 清理空结构
            if not task_group:
                file_map = self.tasks.get(type_key)
                if file_map:
                    file_map.pop(file_id, None)
                    if not file_map:
                        self.tasks.pop(type_key, None) 
            logger.debug(f"Task list: {self.tasks}")
            
        
class AppContext():
    _cache_system: 'CacheSystem' = None
    _task_manager: TaskManager = None
    
    @classmethod
    def init(cls, root_dir: str):
        cls.init_cache_system(root_dir)
    
    @classmethod
    def init_cache_system(cls, root_dir: str):
        from .CacheSystem import CacheSystem
        cls._cache_system = CacheSystem(root_dir)
        
    @classmethod
    def get_cache_system(cls) -> 'CacheSystem':
        return cls._cache_system
    
    @classmethod
    def get_task_manager(cls) -> TaskManager:
        if cls._task_manager is None:
            cls._task_manager = TaskManager()
        return cls._task_manager