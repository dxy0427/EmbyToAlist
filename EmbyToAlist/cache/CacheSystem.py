import asyncio
import hashlib
from pathlib import Path
from weakref import WeakValueDictionary

import aiofiles
from loguru import logger

from ..models import FileInfo, RequestInfo, CacheRangeStatus
from .manager import TaskManager
from ..utils.common import ClientManager
from typing import AsyncGenerator, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    pass
class ChunksWriter():
    def __init__(self):
        self.queue = asyncio.Queue()
        self.task = None
        self.client = ClientManager.get_client()
        self.cache_data = []
        self.number_of_chunks = 0

    async def _write(self, request_info: RequestInfo, raw_url: str, request_header: dict):
        
        # 每个chunk 5MB
        chunk_size = 5 * 1024 * 1024
        cache_end = request_info.file_info.cache_file_size
        self.number_of_chunks = ((cache_end + chunk_size) // chunk_size) + 1
        
        if request_info.range_info.cache_range[0] == 0:
            # 读取头部
            request_header['Range'] = f"bytes=0-{cache_end+chunk_size}"
        else:
            # 读取尾部
            request_header['Range'] = f"bytes={request_info.range_info.cache_range[0]}-"

        async with self.client.stream("GET", raw_url, headers=request_header) as response:
            if response.status_code != 206:
                raise ValueError(f"Expected 206 response, got {response.status_code}")
            
            async for chunk in response.iter_bytes(chunk_size):
                # 写入缓存文件
                await self.queue.put(chunk)
            
    async def write(self, request_info: RequestInfo, raw_url: str, request_header: dict):
        """写入缓存文件
        """
        if self.task is None:
            self.task = asyncio.create_task(self._write(request_info, raw_url, request_header))
        else:
            logger.debug("Write task already exists, skipping")
            
    async def read(self) -> AsyncGenerator[bytes, None]:
        """读取缓存文件
        如果cache data不为空，则直接返回
        否则从队列中读取数据，并将数据写入缓存文件
        """
        if self.cache_data:
            for data in self.cache_data:
                yield data
        else:
            while not self.queue.empty():
                data = await self.queue.get()
                self.cache_data.append(data)
                yield data
                self.queue.task_done()

class CacheSystem():
    VERSION: str = "1.0.0"
    def __init__(self, root_dir: str):
        self.root_dir: Path = Path(root_dir)
        self.cache_locks = WeakValueDictionary()
        self.condition = asyncio.Condition()
        self.cache_file_name = "cache_file_{start}_{end}"
        self.client = ClientManager.get_client()
        self.task_manager = TaskManager()
        self._initialize()
        
    def _write_version_file(self):
        """Write the version file to the cache directory.
        """
        version_file = self.root_dir / ".version"
        with version_file.open("w") as f:
            f.write(self.VERSION)
            
    def _read_version_file(self):
        """Read the version file from the cache directory.
        """
        version_file = self.root_dir / ".version"
        if not version_file.exists():
            return None
        return version_file.read_text().strip()
    
    def _get_cache_lock(self, subdirname: Path, dirname: Path):
        # 为每个子目录创建一个锁, 防止不同文件名称的缓存同时写入，导致重复范围的文件
        key = f"{subdirname}/{dirname}" 
        if key not in self.cache_locks:
            # 防止被weakref立即回收
            lock = asyncio.Lock()
            self.cache_locks[key] = lock
        return self.cache_locks[key]
    
    def _get_hash_subdirectory_from_path(self, file_info: FileInfo) -> tuple[str, str]:
        """
        计算给定文件路径的MD5哈希，并返回哈希值的前两位作为子目录名称 (Cache Key)。
        缓存键为文件名称+文件大小+文件类型

        :param file_info: 文件信息
        
        :return: 哈希值的前两个字符，作为子目录名称
        """
        cache_key = f"{file_info.name}:{file_info.size}:{file_info.container}"
        hash_digest = hashlib.md5(cache_key.encode('utf-8')).hexdigest()
        return hash_digest[:2], hash_digest # 返回子目录名称和哈希值
            
    def _initialize(self):
        """初始化缓存系统
        """
        if not self.root_dir.exists():
            self.root_dir.mkdir(parents=True, exist_ok=True)
            self._write_version_file()
        else:
            version = self._read_version_file()
            if version != self.VERSION:
                logger.warning(f"Cache version mismatch, current version: {self.VERSION}, cache version: {version}")
                logger.warning("Please remove the cache directory")
                exit(1)
                
    async def get_writer(self, request_info: RequestInfo) -> ChunksWriter:
        file_name = request_info.file_info.name
        cache_range_status = request_info.cache_range_status
        
        if self.task_manager.get_task(file_name, cache_range_status) is not None:
            return self.task_manager.get_task(file_name, cache_range_status)
        else:
            writer = ChunksWriter()
            self.task_manager.create_task(file_name, writer, cache_range_status)
            asyncio.create_task(self.write_cache_file(request_info))
            return writer
    
    async def write_cache_file(self, request_info: RequestInfo):
        # 后台缓存文件，sleep防止占用异步线程
        asyncio.sleep(20)
        subdirname, dirname = self._get_hash_subdirectory_from_path(request_info.file_info)
        cache_dir = self.root_dir / subdirname / dirname
        if not cache_dir.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
    
        cache_file_name = self.cache_file_name.format(start=request_info.range_info.cache_range[0], end=request_info.range_info.cache_range[1])
        
        chunk_writer = await self.get_writer(request_info)
        async with self._get_cache_lock(subdirname, dirname):
            async with aiofiles.open(cache_dir / cache_file_name, 'wb') as f:
                async for chunk in chunk_writer.read():
                    await f.write(chunk)
                    
        await self.task_manager.remove_task(request_info.file_info.name, request_info.cache_range_status)
        logger.debug(f"Cache file written: {cache_file_name}")
    
    def verify_cache_file(self, file_info: FileInfo, start: int, end: int) -> bool:
        """
        验证缓存文件是否符合 Emby 文件大小，筛选出错误缓存文件
        
        实现方式仅为验证文件大小，不验证文件内容
        
        :param file_info: 文件信息
        :param cache_file_range: 缓存文件的起始点和结束点
        
        :return: 缓存文件是否符合视频文件大小
        """
        # 开头缓存文件
        if start == 0 and end == file_info.cache_file_size - 1:
            return True
        # 末尾缓存文件
        elif end == file_info.size - 1:
            return True
        else:
            return False
    
    def get_cache_status(self, request_info: RequestInfo) -> bool:
        """检查缓存状态
        """
        file_info = request_info.file_info
        range_info = request_info.range_info
        
        subdirname, dirname = self._get_hash_subdirectory_from_path(file_info)
        cache_dir = self.root_dir / subdirname / dirname
        
        if not cache_dir.exists():
            return False
        
        for cache_file in cache_dir.iterdir():
            if cache_file.is_file():
                if cache_file.name.startswith("cache_file"):
                    start, end = map(int, cache_file.stem.split("_")[2:4])
                    if self.verify_cache_file(file_info, start, end):
                        if start <= range_info.request_range[0] <= end:
                            return True
                    else:
                        logger.warning(f"Invalid cache file: {cache_file}")
                        cache_file.unlink()
                        return False
        
        logger.debug(f"No valid cache file found for {file_info.path}")
        return False
    
    def read_cache_file(self, request_info: RequestInfo) -> AsyncGenerator[bytes, None]:
        """
        读取缓存文件，该函数不是异步的，将直接返回一个异步生成器
        
        :param request_info: 请求信息
        
        :return: function read_file
        """    
        subdirname, dirname = self._get_hash_subdirectory_from_path(request_info.file_info)
        file_dir = self.root_dir / subdirname / dirname
        range_info = request_info.range_info
        
        # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
        for cache_file in file_dir.iterdir():
            if cache_file.is_file():
                if cache_file.name.startswith("cache_file"):
                    start, end = map(int, cache_file.stem.split("_")[2:4])
                    if start <= range_info.request_range[0] <= end:
                        # 调整 end_point 的值
                        adjusted_end = None if request_info.cache_range_status in {CacheRangeStatus.PARTIALLY_CACHED, CacheRangeStatus.FULLY_CACHED_TAIL} else range_info.request_range[1] - range_info.request_range[0]
                        logger.debug(f"Read Cache: {cache_file}")
                        
                        return self.read_file(cache_file, range_info.request_range[0] - start, adjusted_end)
                
        logger.error(f"Read Cache Error: There is no matched cache in the cache directory for this file: {request_info.file_info.path}.")
        return 
    
    async def read_file(
        self,
        file_path: str, 
        start_point: int = 0, 
        end_point: Optional[int] = None, 
        chunk_size: int = 1024*1024, 
        ) -> AsyncGenerator[bytes, None]:
        """
        读取文件的指定范围，并返回异步生成器。
    
        :param file_path: 缓存文件路径
        :param start_point: 文件读取起始点，HTTP Range 的字节范围
        :param end_point: 文件读取结束点，None 表示文件末尾，HTTP Range 的字节范围
        :param chunk_size: 每次读取的字节数，默认为 1MB
        
        :return: 生成器，每次返回 chunk_size 大小的数据
        """
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                await f.seek(start_point)
                while True:
                    if end_point is not None:
                        # 传入的range为http请求头的range，直接传入默认会少读取1个字节，所以需要+1
                        remaining = (end_point+1) - await f.tell()
                        if remaining <= 0:
                            break
                        chunk_size = min(chunk_size, remaining)
                    
                    data = await f.read(chunk_size)
                    if not data:
                        break
                    yield data
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
        except Exception as e:
            logger.error(f"Unexpected error occurred while reading file: {e}")
    
    async def write_from_remote(self):
        pass
    
