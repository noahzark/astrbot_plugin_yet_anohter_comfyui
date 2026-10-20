"""Compare the running version with the latest GitHub release."""
from __future__ import annotations

import logging
import re

from .client import ComfyClient

logger = logging.getLogger(__name__)

REPO = "noahzark/astrbot_plugin_yet_anohter_comfyui"


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text.split("-")[0])[:4])


async def check_update(client: ComfyClient, current: str, repo: str = REPO) -> str:
    """A message naming the newer release, or ``""`` when up to date or unknown."""
    try:
        data = await client.request(
            "GET",
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": repo},
            timeout=10,
            proxy=True,
        )
    except Exception as e:
        logger.debug("Update check failed: %s", e)
        return ""
    tag = str((data or {}).get("tag_name") or "") if isinstance(data, dict) else ""
    if not tag or version_tuple(tag) <= version_tuple(current):
        return ""
    return f"Yet Another ComfyUI 有新版本 {tag}（当前 {current}）: {data.get('html_url') or f'https://github.com/{repo}/releases'}"
