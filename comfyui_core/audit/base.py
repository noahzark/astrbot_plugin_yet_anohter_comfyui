from __future__ import annotations

import base64
import logging
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

AuditResult = dict[str, Any]  # {"is_nsfw": bool, "message": str, "tags": Any}


def prepare_audit_image(image_input: bytes | str, downscale: bool = False) -> tuple[str, Image.Image]:
    """Decode to RGB, optionally shrink to about 640² pixels, return (b64 JPEG, PIL image)."""
    img_bytes = base64.b64decode(image_input) if isinstance(image_input, str) else image_input
    img = Image.open(BytesIO(img_bytes)).convert("RGB")

    if downscale:
        max_res = 640
        w, h = img.size
        if w * h > max_res ** 2:
            if w <= h:
                ratio = h / w
                new_w = max_res / (ratio ** 0.5)
                new_h = new_w * ratio
            else:
                ratio = w / h
                new_h = max_res / (ratio ** 0.5)
                new_w = new_h * ratio
            img = img.resize((round(new_w), round(new_h)), Image.LANCZOS)
            logger.info("Audit image downscaled to %sx%s", round(new_w), round(new_h))

    buf = BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8"), img


def resolve_audit_level(default_level: int, group_config: dict[str, dict], group_id: str) -> int:
    """Group override of the audit level, if the group has one."""
    group_cfg = group_config.get("audit_level_group", {})
    if group_id and group_id in group_cfg:
        try:
            return int(group_cfg[group_id])
        except ValueError:
            pass
    return default_level


def rating_is_nsfw(top_category: str, audit_level: int) -> bool:
    """Map a WD14-style top rating to a verdict for the given level.

    0 off, 1 explicit only, 2 adds questionable, 3 adds sensitive,
    100 or more flags everything.
    """
    if audit_level == 1:
        return top_category == "explicit"
    if audit_level == 2:
        return top_category in ("questionable", "explicit")
    if audit_level == 3:
        return top_category in ("questionable", "explicit", "sensitive")
    if audit_level >= 100:
        return True
    return False


class BaseAuditor(ABC):
    @abstractmethod
    async def audit(self, image_bytes: bytes, group_id: str = "") -> AuditResult:
        """Return ``{"is_nsfw": bool, "message": str, "tags": Any}``."""
