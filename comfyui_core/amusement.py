"""二次元的我, Danbooru tag lookup and the llm-tag prompt."""
from __future__ import annotations

import json
import logging
import random
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .client import ComfyClient
from .constants import DEFAULT_LLM_CONVERSATIONS, DEFAULT_LLM_SYS_PROMPT
from .exceptions import ComfyuiBackendConnectionError

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "assets"
DANBOORU = "https://danbooru.donmai.us"
DANBOORU_HEADERS = {"User-Agent": "astrbot_plugin_yet_anohter_comfyui"}

# The order the nonebot plugin drew categories in; the description below indexes it.
GIRL_PARTS = ["类型", "发色", "头发", "衣服", "鞋子", "装饰", "胸", "表情", "动作", "天气", "环境", "优秀实践"]


@lru_cache(maxsize=1)
def girl_tags() -> dict[str, dict[str, str]]:
    return json.loads((DATA_DIR / "today_girl.json").read_text(encoding="utf-8"))


def today_girl(rng: random.Random | None = None) -> tuple[str, str]:
    """A random character: the English prompt and its Chinese description."""
    rng = rng or random.Random()
    tags = girl_tags()
    zh: list[str] = []
    en: list[str] = []
    for part in GIRL_PARTS:
        options = tags.get(part) or {}
        if options:
            key = rng.choice(list(options))
            zh.append(key)
            en.append(options[key])
        else:
            zh.append("")
    prompt = "(solo:1.1), " + ", ".join(e for e in en if e)
    description = (
        f"二次元的我:\n{zh[11]},\n是{zh[0]}, {zh[7]},\n{zh[1]}色{zh[2]},\n穿着{zh[3]}和{zh[4]},\n"
        f"有着{zh[5]}和{zh[6]},\n正在{zh[8]},\n画面{zh[9]}, {zh[10]}"
    )
    return prompt, description


async def danbooru_tags(client: ComfyClient, query: str, limit: int = 3) -> list[str]:
    """Danbooru's autocomplete for ``query``: tag names, most used first."""
    url = (
        f"{DANBOORU}/autocomplete.json?search%5Bquery%5D={quote(query)}"
        f"&search%5Btype%5D=tag_query&version=1&limit={int(limit)}"
    )
    resp = await client.request("GET", url, headers=DANBOORU_HEADERS, timeout=20, proxy=True)
    if not isinstance(resp, list):
        return []
    return [str(item["value"]) for item in resp if isinstance(item, dict) and item.get("value")][:limit]


async def danbooru_samples(client: ComfyClient, tag: str, count: int = 2) -> list[str]:
    """URLs of a few sample pictures for ``tag``."""
    url = f"{DANBOORU}/posts.json?tags={quote(tag)}&limit={int(count) * 2}"
    resp = await client.request("GET", url, headers=DANBOORU_HEADERS, timeout=20, proxy=True)
    urls: list[str] = []
    for post in resp if isinstance(resp, list) else []:
        if not isinstance(post, dict):
            continue
        link = post.get("large_file_url") or post.get("file_url")
        if isinstance(link, str) and link.startswith("http") and link.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            urls.append(link)
        if len(urls) >= count:
            break
    return urls


async def danbooru_image(client: ComfyClient, url: str) -> bytes:
    """Download a sample picture; Danbooru's CDN answers a bot challenge page without a User-Agent."""
    data = await client.request("GET", url, headers=DANBOORU_HEADERS, as_json=False, timeout=30, proxy=True)
    if not isinstance(data, (bytes, bytearray)) or data[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ComfyuiBackendConnectionError(f"Danbooru 没有返回图片: {url}")
    return bytes(data)


def llm_tag_system_prompt(base: str = "") -> str:
    """The llm-tag system prompt with the example exchanges folded in."""
    prompt = base.strip() or DEFAULT_LLM_SYS_PROMPT
    pairs = list(zip(DEFAULT_LLM_CONVERSATIONS[::2], DEFAULT_LLM_CONVERSATIONS[1::2]))
    examples = "\n".join(f"Input: {q}\nOutput: {a}" for q, a in pairs)
    return f"{prompt}\n\nExamples:\n{examples}"


def pick_llm_preset(presets: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """``-llm N`` picks the N-th preset (1-based); 0 or a missing one means none."""
    if 1 <= index <= len(presets):
        return presets[index - 1]
    return {}
