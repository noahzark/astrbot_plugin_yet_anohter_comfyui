from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

from ..constants import NUDENET_UNSAFE_LABELS
from ..settings import CoreSettings
from .base import AuditResult, BaseAuditor

logger = logging.getLogger(__name__)


class LocalNudeNetAuditor(BaseAuditor):
    """NudeNet detector run locally (loaded on first use). Needs ``nudenet``."""

    def __init__(self, settings: CoreSettings) -> None:
        self.settings = settings
        self._detector: Any = None
        self._load_lock = asyncio.Lock()

    def _ensure_loaded(self) -> Any:
        if self._detector is not None:
            return self._detector
        try:
            from nudenet import NudeDetector
        except ImportError as e:
            logger.error("NudeNet dependency missing: %s. pip install nudenet", e)
            raise

        model_path = self.settings.audit.nude_model_path or None
        logger.info("Loading NudeNet detector")
        self._detector = NudeDetector(model_path=model_path, inference_resolution=640)
        return self._detector

    async def audit(self, image_bytes: bytes, group_id: str = "") -> AuditResult:
        audit_info: AuditResult = {"is_nsfw": False, "message": "", "tags": []}
        try:
            async with self._load_lock:
                detector = await asyncio.to_thread(self._ensure_loaded)
        except Exception as e:
            audit_info["message"] = f"NudeNet 加载失败: {e}"
            return audit_info

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
                tmp_file.write(image_bytes)
                tmp_path = tmp_file.name
            detections = await asyncio.to_thread(detector.detect, tmp_path)
        except Exception as e:
            logger.error("NudeNet detection failed: %s", e)
            audit_info["is_nsfw"] = True
            audit_info["message"] = f"NudeNet 检测错误: {e}"
            return audit_info
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

        unsafe_found: list[str] = []
        msg_lines: list[str] = []
        for item in detections:
            label = item.get("class")
            score = item.get("score", 0.0)
            if score < 0.5:
                continue
            pct = f"{int(score * 100)}%"
            if label in NUDENET_UNSAFE_LABELS:
                unsafe_found.append(f"{label} ({pct})")
                msg_lines.append(f"⚠️ [{label}]: {pct}")
            else:
                msg_lines.append(f"ℹ️ [{label}]: {pct}")

        is_nsfw = bool(unsafe_found)
        status_text = "包含敏感内容" if is_nsfw else "图片安全"
        audit_info["tags"] = detections
        audit_info["message"] = f"NudeNet 结果: {status_text}\n" + "\n".join(msg_lines)
        audit_info["is_nsfw"] = is_nsfw
        return audit_info
