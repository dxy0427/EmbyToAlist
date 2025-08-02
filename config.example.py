# config.py

emby_server = "http://127.0.0.1:8096"
emby_key = ""
alist_server = "http://127.0.0.1:5244"
alist_key = ""

# Alist api 提供的 Raw Url 的 Host 替换
# 如：Alist 路径下 /movie 为 OneDrive 存储，提供的下载 Raw Url 为 xxx.sharepoint.com, 
# 经过配置后将替换为 https://download.example.com/onedrive/
# 不清楚请留空
# Example: https://download.example.com/onedrive/

# 可以用{host_url}代指请求头的host
# 如果url为列表，则自动选择二级域名一致的url。和host_url互斥
alist_download_url_replacement_map = {
    "/path/in/Alist": "https://url",
    "/movie": "https://download.example.com/onedrive/",
    "/anime": "{host_url}/anime/",
    "/tv": ["https://download.example.com/tv/", "https://download.example2.net/tv/"],
}

#strm文件的
direct_url_handler = {
    # 总开关
    "enable": True,
    
    # 是否追踪重定向，获取最终的真实视频地址。
    # True:  后台缓存和前台播放都会使用最终的真实链接。兼容性最好，推荐！
    # False: 后台缓存会强制获取真实链接，但前台播放超过15秒后会重定向到“跳板链接”，
    #        这需要您的播放器能很好地处理302跳转。
    "resolve_final_url": False,
    
    # URL 路径匹配规则 (使用正则表达式)
    # 此规则会匹配 Emby 媒体库中的“路径”。
    # 如果您的 Emby 路径本身就是一个 Alist 的 http 链接，就可以用此功能。
    # 示例: 匹配所有由本地 Alist (127.0.0.1:5244) 生成的链接。
    "match_patterns": [
        r"^http:\/\/127\.0\.0\.1:5244.*"
    ],

    # URL 域名替换规则
    # 将匹配到的路径中的某一部分替换为新的内容。
    # 示例: 将本地 Alist 地址替换为公网可访问的域名。
    "replacement_map": {
        "http://127.0.0.1:5244": "https://xxx.xxx.com"
    }
}

not_redirect_paths = ['/mnt/localpath/']

# If you store your media files on OneDrive and use rclone for processing them (uploading and mounting on the server),
# it's recommended to set convertSpecialChars to True.
# This is because rclone converts certain special characters during the upload process, but displays the original characters when the files are mounted.
convert_special_chars = False
special_chars_list = ['？','：']

# 下面两项配置是为了处理挂载路径和Alist路径不一致的情况
# 例如，在emby服务器中，视频路径是 /mnt/movie/xxx，但在alist中，视频路径是 /movie/xxx
# 则需要配置 mount_path_prefix_remove 为 /mnt，该程序会移除文件路径中的 /mnt 部分
# 同理，如果视频路径是 /movie/xxx，但在alist中，视频路径是 /mnt/movie/xxx，则需要配置 mount_path_prefix_add 为 /mnt
# 顺序是先移除，再添加
# 如果你100%确定挂载路径和Alist路径一致，可以忽略这两项配置
convert_mount_path = False
mount_path_prefix_remove = "/"
mount_path_prefix_add = ""

# 是否缓存视频前15秒用于起播加速
enable_cache = False
enable_cache_next_episode = False
cache_path = "/app/cache"
# 缓存文件名称黑名单，文件名称包含以下字符串的文件不会被缓存，支持正则表达式
cache_blacklist = []

# 是否强制将 http:// 的重定向地址改为 https://
# 适用于源站无证书，但通过CDN实现HTTPS访问的场景
FORCE_HTTPS_REDIRECT = True
clean_cache_after_remove_media = True  # 媒体删除后清理缓存

log_level = "INFO"