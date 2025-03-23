from environs import Env

env = Env()
env.read_env()

EMBY_SERVER = env.str("EMBY_SERVER")

ALIST_SERVER = env.str("ALIST_SERVER")
ALIST_API_KEY = env.str("ALIST_API_KEY")

IGNORE_PATH = env.list("IGNORE_PATH", subcast=str, default=[])
IGNORE_PATH = [path.strip() for path in IGNORE_PATH]

MOUNT_PATH_PREFIX_REMOVE = env.str("MOUNT_PATH_PREFIX_REMOVE", default="").strip()
MOUNT_PATH_PREFIX_ADD = env.str("MOUNT_PATH_PREFIX_ADD", default="").strip()

CACHE_ENABLE = env.bool("CACHE_ENABLE", default=False)
CACHE_NEXT_EPISODE = env.bool("CACHE_NEXT_EPISODE", default=False)
CACHE_PATH = env.str("CACHE_PATH", default="./cache")
FORCE_CLIENT_RECONNECT = env.bool("FORCE_CLIENT_RECONNECT", default=False)

LOG_LEVEL = env.str("LOG_LEVEL", default="info").lower()

# ADVANCED CONFIGURATION 高级配置
INITIAL_CACHE_SIZE_OF_TAIL = 1*1024*1024
"""初始末尾缓存大小，会经过裁切，需要保证请求Range在文件末尾的Cache Size内"""

CHUNK_SIZE_OF_CHUNKSWITER = 2*1024*1024
"""ChunksWriter实例中的缓存块大小，默认为2MB"""

# EXPERIMENTAL CONFIGURATION 实验性配置
MEMORY_CACHE_ONLY = env.bool("MEMORY_CACHE_ONLY", default=False)
"""是否只使用内存缓存，默认False"""