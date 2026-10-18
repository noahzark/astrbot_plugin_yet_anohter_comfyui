"""Turn pipeline results into files on disk and AstrBot message chains."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

import astrbot.api.message_components as Comp
from astrbot.api import logger

from ..comfyui_core.pipeline import GenerationOutcome, GenerationRequest, TaskResult
from .qq import qr_png

SUFFIX = {"image": ".png", "video": ".mp4", "audio": ".flac"}


def write_media(data: bytes, kind: str, *, data_dir: Path, user_id: str, persistent: bool, index: int = 0) -> Path:
    """Write one output to disk and return its path.

    Persistent files go to ``output/<kind>/<date>/<user>/<md5>_<i>``,
    mirroring the nonebot layout; throwaway files go to ``tmp/``.
    """
    suffix = SUFFIX.get(kind, ".bin")
    if persistent:
        folder = data_dir / "output" / kind / datetime.now().strftime("%Y-%m-%d") / (user_id or "unknown")
        name = f"{hashlib.md5(data).hexdigest()}_{index}{suffix}"
    else:
        folder = data_dir / "tmp"
        name = f"{uuid.uuid4().hex}{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def header_text(outcome: GenerationOutcome, result: TaskResult) -> str:
    t = outcome.timings
    timing = f"各步骤耗时: 预处理 {t.get('pre_process', 0):.2f}s; 生成 {t.get('request', 0):.2f}s"
    if "audit" in t:
        timing += f"; 审核 {t['audit']:.2f}s"
    lines = [timing, f"任务ID: {result.task_id}, 后端索引: {result.backend_index}"]
    if result.audit_message:
        lines.append(f"审核信息: {result.audit_message}")
    lines.extend(outcome.notes)
    return "\n".join(lines)


LinkFn = Callable[[bytes], Awaitable[str]]


@dataclass
class Delivery:
    """How one request's results are sent.

    ``img_send``/``r18_action`` follow the nonebot numbering (see
    ``CoreSettings``). ``link_for`` uploads an image and returns a link
    (QQ only); without it the link modes fall back as the plan says: safe
    images are sent as images and flagged ones are dropped. ``dm`` sends a
    chain to the requesting user privately and says whether it worked.
    """

    data_dir: Path
    persistent: bool
    img_send: int = 1
    r18_action: int = 1
    pure: bool = False
    reply_to: str | None = None
    link_for: LinkFn | None = None
    dm: Callable[[list[Comp.BaseMessageComponent]], Awaitable[bool]] | None = None
    written: list[Path] = field(default_factory=list)

    def save(self, data: bytes, kind: str, user_id: str, index: int) -> Path:
        path = write_media(data, kind, data_dir=self.data_dir, user_id=user_id, persistent=self.persistent, index=index)
        self.written.append(path)
        return path

    def _start(self) -> list[Comp.BaseMessageComponent]:
        return [Comp.Reply(id=self.reply_to)] if self.reply_to else []

    async def _image_parts(self, img: bytes, mode: int, user_id: str, index: int) -> list[Comp.BaseMessageComponent]:
        if mode in (2, 3) and self.link_for is not None:
            try:
                url = await self.link_for(img)
                if mode == 3:
                    return [Comp.Plain(f"\n图片链接: {url}")]
                return [Comp.Image.fromFileSystem(str(self.save(qr_png(url), "image", user_id, index)))]
            except Exception as e:
                logger.warning("Image link mode %s failed, sending the image instead: %s", mode, e)
        elif mode in (2, 3):
            logger.info("Image send mode %s needs QQ (aiocqhttp); sending the image instead", mode)
        return [Comp.Image.fromFileSystem(str(self.save(img, "image", user_id, index)))]

    async def _flagged(self, img: bytes, user_id: str, index: int) -> list[Comp.BaseMessageComponent]:
        """What the chat sees for one flagged image (the DM, if any, is sent here)."""
        action = self.r18_action
        if action == 1 and self.dm is not None:
            path = self.save(img, "image", user_id, index)
            if await self.dm([Comp.Image.fromFileSystem(str(path))]):
                return [Comp.Plain("⚠️ 生成的图片太涩了，已为您私聊发送！")]
        elif action in (2, 3) and self.link_for is not None:
            try:
                url = await self.link_for(img)
                if action == 3:
                    return [Comp.Plain(f"⚠️ 图片太涩了，访问链接查看: {url}")]
                return [Comp.Image.fromFileSystem(str(self.save(qr_png(url), "image", user_id, index)))]
            except Exception as e:
                logger.warning("NSFW link mode %s failed, dropping the image: %s", action, e)
        elif action in (2, 3):
            logger.info("NSFW mode %s needs QQ (aiocqhttp); the image is filtered instead", action)
        return [Comp.Plain("⚠️ 图片太涩了，已被过滤拦截！")]

    async def build(self, req: GenerationRequest, outcome: GenerationOutcome) -> list[list[Comp.BaseMessageComponent]]:
        """Message chains in sending order: flagged-image notes, the result, then videos and audio."""
        chains: list[list[Comp.BaseMessageComponent]] = []
        uid = req.user_id
        for result in outcome.results:
            for i, img in enumerate(result.nsfw_images):
                chains.append(self._start() + await self._flagged(img, uid, i))

            chain = self._start()
            text_parts: list[str] = []
            if not (req.pure or self.pure):
                text_parts.append(header_text(outcome, result))
            text_parts.extend(result.texts)
            if text_parts:
                chain.append(Comp.Plain("\n".join(text_parts)))
            for i, img in enumerate(result.images):
                chain.extend(await self._image_parts(img, self.img_send, uid, i))
            if len(chain) > len(self._start()):
                chains.append(chain)

            for i, vid in enumerate(result.videos):
                chains.append([Comp.Video.fromFileSystem(str(self.save(vid, "video", uid, i)))])
            for i, aud in enumerate(result.audios):
                chains.append([Comp.Record.fromFileSystem(str(self.save(aud, "audio", uid, i)))])
        return chains
