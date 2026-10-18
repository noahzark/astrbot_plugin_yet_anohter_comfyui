"""Image and text audit.

``ImageAuditor`` dispatches on ``settings.audit.mode``: ``remote`` (sd-webui
tagger API), ``wd14`` (local ONNX), ``nudenet`` (local) or ``dual`` (WD14 and
NudeNet, either one flags).
"""
from __future__ import annotations

import asyncio
import logging

from ..client import ComfyClient
from ..settings import CoreSettings
from .base import AuditResult, BaseAuditor, prepare_audit_image, rating_is_nsfw, resolve_audit_level
from .local_nudenet import LocalNudeNetAuditor
from .local_wd import LocalWDAuditor
from .remote import RemoteTaggerAuditor
from .text import TextAuditor, clean_llm_response

logger = logging.getLogger(__name__)

MODES = ("remote", "wd14", "nudenet", "dual")


class ImageAuditor:
    def __init__(self, settings: CoreSettings, client: ComfyClient) -> None:
        self.settings = settings
        self.remote = RemoteTaggerAuditor(settings, client)
        self.wd = LocalWDAuditor(settings)
        self.nudenet = LocalNudeNetAuditor(settings)

    async def audit(self, image_bytes: bytes, group_id: str = "") -> AuditResult:
        cfg = self.settings.audit
        if not cfg.enabled:
            return {"is_nsfw": False, "message": "审核已关闭", "tags": {}}

        if cfg.mode == "dual":
            res_wd, res_nude = await asyncio.gather(
                self.wd.audit(image_bytes, group_id),
                self.nudenet.audit(image_bytes, group_id),
            )
            return {
                "is_nsfw": res_wd["is_nsfw"] or res_nude["is_nsfw"],
                "message": f"{res_wd['message']}\n{res_nude['message']}",
                "tags": {"wd": res_wd.get("tags"), "nude": res_nude.get("tags")},
            }
        if cfg.mode == "wd14":
            return await self.wd.audit(image_bytes, group_id)
        if cfg.mode == "nudenet":
            return await self.nudenet.audit(image_bytes, group_id)
        if cfg.mode != "remote":
            logger.warning("Unknown audit mode %r, using remote", cfg.mode)
        return await self.remote.audit(image_bytes, group_id)


__all__ = [
    "AuditResult",
    "BaseAuditor",
    "ImageAuditor",
    "TextAuditor",
    "RemoteTaggerAuditor",
    "LocalWDAuditor",
    "LocalNudeNetAuditor",
    "prepare_audit_image",
    "rating_is_nsfw",
    "resolve_audit_level",
    "clean_llm_response",
    "MODES",
]
