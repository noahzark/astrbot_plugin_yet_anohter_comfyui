from __future__ import annotations

import logging

from ..client import ComfyClient
from ..settings import CoreSettings
from .base import AuditResult, BaseAuditor, prepare_audit_image, rating_is_nsfw, resolve_audit_level

logger = logging.getLogger(__name__)


class RemoteTaggerAuditor(BaseAuditor):
    """Rates an image through a remote sd-webui tagger API."""

    def __init__(self, settings: CoreSettings, client: ComfyClient) -> None:
        self.settings = settings
        self.client = client

    async def audit(self, image_bytes: bytes, group_id: str = "") -> AuditResult:
        audit_info: AuditResult = {"is_nsfw": False, "message": "", "tags": {}}

        try:
            b64, _ = prepare_audit_image(image_bytes, self.settings.audit.downscale)
        except Exception as e:
            logger.error("Audit image preprocessing failed: %s", e)
            audit_info["is_nsfw"] = True
            audit_info["message"] = "图片预处理失败"
            return audit_info

        payload = {"image": b64, "model": "wd14-vit-v2-git", "threshold": 0.35}
        try:
            resp_dict = await self.client.request(
                "POST", f"{self.settings.audit.site}/tagger/v1/interrogate",
                json_data=payload, timeout=15)
        except Exception as e:
            logger.error("Remote audit request failed: %s", e)
            audit_info["message"] = f"远程审核请求失败: {e}"
            return audit_info

        if not isinstance(resp_dict, dict) or "caption" not in resp_dict:
            audit_info["message"] = "远程审核返回异常格式"
            return audit_info

        tags = resp_dict["caption"]
        replace_list = ["general", "sensitive", "questionable", "explicit"]
        to_user_list = ["这张图很安全!", "较为安全", "色情", "泰色辣!"]
        possibilities: dict[str, float] = {}
        to_user_dict: dict[str, float] = {}
        message = "审核结果:\n"

        for key, user_label in zip(replace_list, to_user_list):
            score = tags.get(key, 0.0)
            possibilities[key] = score
            message += f"[{user_label}{f':{score * 100:.2f}'.rjust(6)}%]\n"
            to_user_dict[user_label] = score

        max_user_label, _ = max(to_user_dict.items(), key=lambda item: item[1])
        reverse_dict = {v: k for k, v in possibilities.items()}
        top_category = reverse_dict.get(max(possibilities.values()), "general")

        level = resolve_audit_level(self.settings.audit.level, self.settings.group_config, group_id)
        audit_info["tags"] = possibilities
        audit_info["message"] = f"{message}最终判定: {max_user_label}"
        audit_info["is_nsfw"] = rating_is_nsfw(top_category, level)
        return audit_info
