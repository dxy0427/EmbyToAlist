from ...models import ItemInfo
from .helpers import build_item_info, emby_api, request_emby_json

async def get_resume_list(user_id: str, api_key: str) -> list[ItemInfo]:
    """获取用户的播放记录列表

    Args:
        user_id (str): Emby User ID
        api_key (str): Emby API Key

    Returns:
        list[ItemInfo]: 包含Item信息的dataclass列表
    """

    resume_list_api = emby_api(f"/emby/Users/{user_id}/Items/Resume", api_key=api_key, Limit=10)

    data = await request_emby_json(resume_list_api)

    return [build_item_info(i) for i in data.get('Items', [])]