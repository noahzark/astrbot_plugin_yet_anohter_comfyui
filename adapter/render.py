"""Turn pipeline results into files on disk and AstrBot message chains."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path

import astrbot.api.message_components as Comp

from ..comfyui_core.pipeline import GenerationOutcome, GenerationRequest, TaskResult

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
    return (
        f"各步骤耗时: 预处理 {t.get('pre_process', 0):.2f}s; 生成 {t.get('request', 0):.2f}s\n"
        f"任务ID: {result.task_id}, 后端索引: {result.backend_index}"
    )


def build_chains(
    req: GenerationRequest,
    outcome: GenerationOutcome,
    *,
    data_dir: Path,
    reply_to: str | None,
    persistent: bool,
) -> tuple[list[list[Comp.BaseMessageComponent]], list[Path]]:
    """One message chain per task result, plus every file written.

    Temporary files (``persistent=False``) are returned so the caller can
    register them for cleanup after sending.
    """
    chains: list[list[Comp.BaseMessageComponent]] = []
    written: list[Path] = []
    for result in outcome.results:
        chain: list[Comp.BaseMessageComponent] = []
        if reply_to:
            chain.append(Comp.Reply(id=reply_to))
        text_parts: list[str] = []
        if not req.pure:
            text_parts.append(header_text(outcome, result))
        text_parts.extend(result.texts)
        if text_parts:
            chain.append(Comp.Plain("\n".join(text_parts)))
        for i, img in enumerate(result.images):
            path = write_media(img, "image", data_dir=data_dir, user_id=req.user_id, persistent=persistent, index=i)
            written.append(path)
            chain.append(Comp.Image.fromFileSystem(str(path)))
        chains.append(chain)

        for i, vid in enumerate(result.videos):
            path = write_media(vid, "video", data_dir=data_dir, user_id=req.user_id, persistent=persistent, index=i)
            written.append(path)
            chains.append([Comp.Video.fromFileSystem(str(path))])
        for i, aud in enumerate(result.audios):
            path = write_media(aud, "audio", data_dir=data_dir, user_id=req.user_id, persistent=persistent, index=i)
            written.append(path)
            chains.append([Comp.Record.fromFileSystem(str(path))])
    return chains, written
