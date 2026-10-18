"""Image and text audit.

``ImageAuditor`` dispatches on ``settings.audit.mode``: ``remote`` (sd-webui
tagger API), ``wd14`` (local ONNX), ``nudenet`` (local) or ``dual`` (WD14 and
NudeNet, either one flags).
"""
from __future__ import annotations

import asyncio
import logging

from ..client import ComfyClient
from ..exceptions import AuditError
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
        """Rate one image for ``group_id``.

        Level 0 (globally or for the group) skips the audit; 100 or more
        flags everything. When a backend cannot give a verdict the image
        counts as NSFW if ``audit.fail_closed`` is on, safe otherwise.
        """
        cfg = self.settings.audit
        level = resolve_audit_level(cfg.level, self.settings.group_config, group_id)
        if not cfg.enabled or level <= 0:
            return {"is_nsfw": False, "message": "", "tags": {}}

        try:
            result = await self._run(image_bytes, group_id)
        except AuditError as e:
            logger.warning("Image audit failed (%s), treating as %s", e, "NSFW" if cfg.fail_closed else "safe")
            verdict = "按违规处理" if cfg.fail_closed else "按安全处理"
            return {"is_nsfw": cfg.fail_closed, "message": f"审核失败，{verdict}: {e}", "tags": {}}
        if level >= 100:
            result["is_nsfw"] = True
        return result

    async def _run(self, image_bytes: bytes, group_id: str) -> AuditResult:
        mode = self.settings.audit.mode
        if mode == "dual":
            res_wd, res_nude = await asyncio.gather(
                self.wd.audit(image_bytes, group_id),
                self.nudenet.audit(image_bytes, group_id),
                return_exceptions=True,
            )
            for res in (res_wd, res_nude):
                if isinstance(res, BaseException):
                    raise res if isinstance(res, AuditError) else AuditError(str(res))
            return {
                "is_nsfw": res_wd["is_nsfw"] or res_nude["is_nsfw"],
                "message": f"{res_wd['message']}\n{res_nude['message']}",
                "tags": {"wd": res_wd.get("tags"), "nude": res_nude.get("tags")},
            }
        if mode == "wd14":
            return await self.wd.audit(image_bytes, group_id)
        if mode == "nudenet":
            return await self.nudenet.audit(image_bytes, group_id)
        if mode != "remote":
            logger.warning("Unknown audit mode %r, using remote", mode)
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
