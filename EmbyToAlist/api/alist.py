import asyncio

from httpx import ReadTimeout, RequestError
from fastapi import HTTPException
from loguru import logger

from ..config import ALIST_SERVER, ALIST_API_KEY
from ..utils.common import ClientManager

# return Alist Raw Url
async def get_alist_raw_url(file_path: str, ua: str, max_retries: int = 3, retry_delay: float = 0.5) -> str:
    """
    创建或获取Alist Raw Url缓存，缓存时间为5分钟

    Args:
        file_path (str): Alist中的文件路径
        ua (str): 请求头中的User-Agent，用于适配115等需要验证UA的网站
        max_retries (int): 最大重试次数
        retry_delay (float): 每次重试之间的等待时间（秒）

    Returns:
        str: Alist Raw Url
    """
    client = ClientManager.get_client()
    alist_api_url = f"{ALIST_SERVER}/api/fs/get"

    body = {
        "path": file_path,
        "password": ""
    }
    header = {
        "Authorization": ALIST_API_KEY,
        "Content-Type": "application/json;charset=UTF-8"
    }

    if ua:
        header['User-Agent'] = ua

    for attempt in range(0, max_retries):
        try:
            req = await client.post(alist_api_url, json=body, headers=header)
            req.raise_for_status()
            req = req.json()

            code = req.get("code", -1)

            if code == 200:
                logger.debug(f"Alist Raw Url: {req['data']['raw_url']}")
                return req['data']['raw_url']
            elif code == 403:
                logger.error("Alist server response 403 Forbidden, Please check your Alist Key")
                raise HTTPException(status_code=500, detail="Alist return 403 Forbidden, Please check your Alist Key")
            else:
                logger.error(f"Alist Error: {req.get('message', 'Unknown Error')}")
                raise HTTPException(status_code=500, detail=f"Alist Server Error: {req.get('message')}")

        except (ReadTimeout, RequestError) as e:
            logger.warning(f"[{attempt}/{max_retries}] Request failed: {e}")
            if attempt == max_retries:
                raise HTTPException(status_code=500, detail="Alist Server Timeout")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            logger.error(f"Unexpected error during Alist request: {e}")
            raise HTTPException(status_code=500, detail="Alist Server Error")