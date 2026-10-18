"""Input images for image workflows, collected from the incoming message.

The three sources match the nonebot plugin: the QQ avatar of a member the
message @-mentions (aiocqhttp only), image segments in the message itself,
and image segments of the message it quotes.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from PIL import Image as PILImage

AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"


def first_frame(data: bytes) -> bytes:
    """A GIF's first frame as PNG; anything else is returned unchanged."""
    try:
        with PILImage.open(io.BytesIO(data)) as img:
            if img.format != "GIF":
                return data
            img.seek(0)
            buf = io.BytesIO()
            img.convert("RGBA").save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return data


def image_sources(event: AstrMessageEvent) -> list[Comp.Image]:
    """Every image the message offers as input, in the nonebot order."""
    messages = list(event.get_messages() or [])
    sources: list[Comp.Image] = []

    if event.get_platform_name() == "aiocqhttp":
        self_id = str(event.get_self_id() or "")
        for seg in messages:
            if isinstance(seg, Comp.At) and str(seg.qq) not in ("all", self_id, ""):
                sources.append(Comp.Image.fromURL(AVATAR_URL.format(qq=seg.qq)))

    sources.extend(seg for seg in messages if isinstance(seg, Comp.Image))

    for seg in messages:
        if isinstance(seg, Comp.Reply) and seg.chain:
            sources.extend(c for c in seg.chain if isinstance(c, Comp.Image))
    return sources


async def collect_input_images(event: AstrMessageEvent, *, keep_gif: bool = False) -> list[bytes]:
    """Download the input images; ones that fail are logged and skipped."""
    images: list[bytes] = []
    for img in image_sources(event):
        try:
            path = await img.convert_to_file_path()
            data = Path(path).read_bytes()
        except Exception as e:
            logger.warning("Could not fetch input image %s: %s", _describe(img), e)
            continue
        images.append(data if keep_gif else first_frame(data))
    return images


def _describe(img: Any) -> str:
    ref = getattr(img, "url", "") or getattr(img, "file", "") or ""
    return ref if len(ref) < 120 else ref[:117] + "..."
