"""QQ-only tricks (aiocqhttp / OneBot v11): recall, image links, forwards.

Every helper here checks :func:`is_qq` first; callers fall back to plain
AstrBot messaging on other platforms.
"""
from __future__ import annotations

import asyncio
import base64
import io
import re
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..comfyui_core.exceptions import SendImageToBotException

URL_RE = re.compile(r"https?://[^\s\"'\]\)]+")

# How long the nonebot plugin kept its transient messages before recalling them.
RECALL_NOTICE = 15
RECALL_ERROR = 20
RECALL_COOLDOWN = 8
RECALL_LIMIT = 15

_background: set[asyncio.Task] = set()


def is_qq(event: AstrMessageEvent) -> bool:
    return event.get_platform_name() == "aiocqhttp" and hasattr(event, "bot")


async def call(event: AstrMessageEvent, action: str, **params: Any) -> Any:
    return await event.bot.api.call_action(action, **params)


def _later(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def cancel_pending() -> None:
    """Drop scheduled recalls (plugin unload)."""
    for task in list(_background):
        task.cancel()


async def _recall_after(event: AstrMessageEvent, message_id: Any, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await call(event, "delete_msg", message_id=message_id)
    except Exception as e:
        logger.debug("Recall of %s failed: %s", message_id, e)


async def send_text_and_recall(event: AstrMessageEvent, text: str, delay: float, *, reply: bool = True) -> bool:
    """Send ``text`` (quoting the user's message) and recall it after ``delay``.

    Returns ``False`` when this is not QQ or the send failed, so the caller
    sends the text the ordinary way.
    """
    if not is_qq(event):
        return False
    segments: list[dict[str, Any]] = []
    msg_id = getattr(event.message_obj, "message_id", "")
    if reply and msg_id:
        segments.append({"type": "reply", "data": {"id": str(msg_id)}})
    segments.append({"type": "text", "data": {"text": text}})
    try:
        group_id = event.get_group_id()
        if group_id:
            res = await call(event, "send_group_msg", group_id=int(group_id), message=segments)
        else:
            res = await call(event, "send_private_msg", user_id=int(event.get_sender_id()), message=segments)
    except Exception as e:
        logger.warning("QQ send failed, falling back: %s", e)
        return False
    message_id = res.get("message_id") if isinstance(res, dict) else None
    if message_id is not None and delay > 0:
        _later(_recall_after(event, message_id, delay))
    return True


async def image_link(event: AstrMessageEvent, image: bytes, retries: int = 4) -> str:
    """Upload ``image`` by sending it to the bot itself and read back its CDN link."""
    payload = [{"type": "image", "data": {"file": "base64://" + base64.b64encode(image).decode()}}]
    sent = None
    for attempt in range(retries):
        try:
            sent = await call(event, "send_private_msg", user_id=int(event.get_self_id()), message=payload)
            break
        except Exception as e:
            logger.warning("Sending the image to the bot itself failed (try %s): %s", attempt + 1, e)
            await asyncio.sleep(1)
    if not isinstance(sent, dict) or "message_id" not in sent:
        raise SendImageToBotException("机器人给自身发送图片获取图片url时失败")

    msg = await call(event, "get_msg", message_id=sent["message_id"])
    urls = URL_RE.findall(str((msg or {}).get("message", "")))
    if not urls:
        raise SendImageToBotException("未能在私聊返回中匹配到图片链接")
    return urls[0].replace("&amp;", "&").replace("multimedia.nt.qq.com.cn", "gchat.qpic.cn")


def qr_png(text: str) -> bytes:
    import qrcode

    buf = io.BytesIO()
    qrcode.make(text).save(buf, format="PNG")
    return buf.getvalue()


def forward_chains(event: AstrMessageEvent, chains: list[list[Any]]) -> list[list[Any]]:
    """Fold text and image results into one forward message; videos and audio stay separate."""
    nodes: list[Any] = []
    rest: list[list[Any]] = []
    for chain in chains:
        if any(isinstance(c, (Comp.Video, Comp.Record)) for c in chain):
            rest.append(chain)
            continue
        content = [c for c in chain if not isinstance(c, Comp.Reply)]
        if content:
            nodes.append(Comp.Node(content=content, uin=str(event.get_self_id()), name="ComfyUI"))
    return ([[Comp.Nodes(nodes=nodes)]] if nodes else []) + rest
