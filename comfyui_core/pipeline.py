"""The generation pipeline: argparse namespace in, media bytes out.

This is the platform-free half of the nonebot ``GenerationContext``. An
adapter parses the chat message with :mod:`comfyui_core.cli`, calls
:func:`build_request` to turn the namespace plus settings into a
:class:`GenerationRequest`, then :meth:`GenerationPipeline.run` to get
:class:`TaskResult` objects whose media the adapter sends.
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from argparse import Namespace
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .client import ComfyClient
from .constants import MAX_SEED, REFLEX_DICT
from .exceptions import ArgsError, InputFileNotFoundError
from .lora import extract_loras_from_prompt, match_loras
from .settings import CoreSettings
from .workflow import WorkflowStore, apply_reflex, calculate_dimensions, validate_permissions

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class GenerationRequest:
    """Everything one generation needs, already defaulted and clamped."""

    workflow: str
    prompt: str = ""
    negative_prompt: str = ""
    width: int = 832
    height: int = 1216
    seed: int = 0
    steps: int = 28
    cfg_scale: float = 7.0
    denoise_strength: float = 1.0
    sampler: str = "dpmpp_2m"
    scheduler: str = "karras"
    batch_size: int = 1
    batch_count: int = 1
    model: str = ""
    override: bool = False
    override_ng: bool = False
    concurrency: bool = False
    selected_backend: str | None = None
    loras: list[tuple[str, float]] = field(default_factory=list)
    init_images: list[bytes] = field(default_factory=list)
    args: Namespace | None = None

    user_id: str = ""
    group_id: str = ""
    is_admin: bool = False

    # Adapter-facing flags the pipeline itself does not act on.
    forward: bool = False
    silent: bool = False
    notice: bool = False
    pure: bool = False

    @property
    def total_images(self) -> int:
        return self.batch_size * self.batch_count


@dataclass
class TaskResult:
    task_id: str
    backend_url: str
    backend_index: int
    images: list[bytes] = field(default_factory=list)
    videos: list[bytes] = field(default_factory=list)
    audios: list[bytes] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)


@dataclass
class GenerationOutcome:
    workflow: str
    reflex_json: dict[str, Any]
    backend_index: int
    results: list[TaskResult]
    timings: dict[str, float] = field(default_factory=dict)


def _first(args: Namespace | None, name: str, default: Any = None) -> Any:
    value = getattr(args, name, None) if args is not None else None
    return default if value is None else value


def _join(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)


def build_request(
    args: Namespace,
    settings: CoreSettings,
    *,
    user_id: str = "",
    group_id: str = "",
    is_admin: bool = False,
    is_superuser: bool = False,
) -> GenerationRequest:
    """Port of the nonebot ``GenerationContext.__init__`` + limitations."""
    defaults = settings.default_value
    workflow = _first(args, "work_flows") or settings.default_workflow

    override = bool(_first(args, "override", False) or defaults.get("override", False))
    override_ng = bool(_first(args, "override_ng", False) or defaults.get("override_ng", False))

    raw_prompt = _join(_first(args, "prompt", []))
    raw_neg = _join(_first(args, "negative_prompt", []))
    preset_p = defaults.get("preset_prompt", "") or ""
    preset_np = defaults.get("preset_negative_prompt", "") or ""
    prompt = (f"{preset_p}, {raw_prompt}" if (preset_p and not override) else raw_prompt).strip(", ")
    negative = (f"{preset_np}, {raw_neg}" if (preset_np and not override_ng) else raw_neg).strip(", ")

    width, height = calculate_dimensions(
        shape=_first(args, "shape") or defaults.get("shape"),
        accept_ratio=_first(args, "accept_ratio") or defaults.get("accept_ratio"),
        width=_first(args, "width"),
        height=_first(args, "height"),
        shape_preset=settings.shape_preset,
        base_res=settings.base_res,
        default_width=int(defaults.get("width", 832)),
        default_height=int(defaults.get("height", 1216)),
    )

    sampler = _first(args, "sampler") or defaults.get("sampler", "dpmpp_2m")
    scheduler = _first(args, "scheduler") or defaults.get("scheduler", "karras")

    selected_backend = _first(args, "backend")
    if selected_backend and not str(selected_backend).isdigit() and not is_admin:
        raise ArgsError("只有管理员可以通过 -be 指定后端地址，普通用户请使用后端索引")

    prompt, loras = extract_loras_from_prompt(prompt)

    req = GenerationRequest(
        workflow=workflow,
        prompt=prompt,
        negative_prompt=negative,
        width=width,
        height=height,
        seed=_first(args, "seed") or random.randint(0, MAX_SEED),
        steps=int(_first(args, "steps") or defaults.get("steps", 28)),
        cfg_scale=float(_first(args, "cfg_scale") or defaults.get("cfg_scale", 7.0)),
        denoise_strength=float(_first(args, "denoise_strength") or defaults.get("denoise_strength", 1.0)),
        sampler=REFLEX_DICT["sampler"].get(sampler, sampler),
        scheduler=REFLEX_DICT["scheduler"].get(scheduler, scheduler),
        batch_size=int(_first(args, "batch_size") or defaults.get("batch_size", 1)),
        batch_count=int(_first(args, "batch_count") or defaults.get("batch_count", 1)),
        model=_first(args, "model") or settings.model or defaults.get("model", "") or "",
        override=override,
        override_ng=override_ng,
        concurrency=bool(_first(args, "concurrency", False) or defaults.get("concurrency", False)),
        selected_backend=str(selected_backend) if selected_backend else None,
        loras=loras,
        args=args,
        user_id=str(user_id),
        group_id=str(group_id or ""),
        is_admin=is_admin,
        forward=bool(_first(args, "forward", False) or defaults.get("forward", False)),
        silent=bool(_first(args, "silent", False) or settings.silent),
        notice=bool(_first(args, "notice", False) or defaults.get("notice", False)),
        pure=bool(_first(args, "pure", False) or defaults.get("pure", False)),
    )

    if not is_superuser:
        limits = settings.max_values
        for name in ("batch_size", "batch_count", "width", "height", "steps"):
            if name in limits:
                setattr(req, name, min(getattr(req, name), int(limits[name])))
    return req


def _output_map(reflex_json: dict[str, Any]) -> dict[str, list[str]]:
    output_node = reflex_json.get("output")
    media_type = reflex_json.get("media", "image")
    if isinstance(output_node, (int, str)):
        return {media_type: [str(output_node)]}
    if isinstance(output_node, dict):
        return {k: [str(n) for n in (v if isinstance(v, list) else [v])] for k, v in output_node.items()}
    return {"image": []}


class GenerationPipeline:
    def __init__(self, client: ComfyClient, store: WorkflowStore, settings: CoreSettings) -> None:
        self.client = client
        self.store = store
        self.settings = settings

    async def run(self, req: GenerationRequest, notify: ProgressCallback | None = None) -> GenerationOutcome:
        timings: dict[str, float] = {}
        t0 = time.monotonic()

        api_json, reflex_json = self.store.load(req.workflow)
        validate_permissions(reflex_json, req.group_id, req.is_admin)
        if reflex_json.get("load_image") and not req.init_images:
            raise InputFileNotFoundError("当前工作流需要提供输入图片！")
        timings["pre_process"] = time.monotonic() - t0

        backend_url, backend_idx, _ = await self.client.select_backend(req.selected_backend)
        if notify is not None:
            await notify(f"已选择工作流: {req.workflow} (后端 [{backend_idx}])\n正在生成中，请稍候...")

        if self.settings.auto_lora and req.loras:
            req.loras = match_loras(req.loras, await self.client.get_loras(backend_url))

        uploaded: list[dict[str, Any]] = []
        for img in req.init_images:
            uploaded.append(await self.client.upload_image(backend_url, img, uuid.uuid4().hex))

        # Spread a concurrent batch over every reachable backend.
        targets: list[tuple[str, int]] = [(backend_url, backend_idx)] * req.batch_count
        if req.concurrency and req.batch_count > 1 and not req.selected_backend:
            available, _ = await self.client.get_available_backends()
            pool = [(self.client.backend_urls[i], i) for i in sorted(available)] or [(backend_url, backend_idx)]
            targets = [pool[i % len(pool)] for i in range(req.batch_count)]

        async def run_one(seed: int, url: str, idx: int) -> TaskResult:
            client_id = uuid.uuid4().hex
            params = {
                "prompt": req.prompt,
                "negative_prompt": req.negative_prompt,
                "width": req.width,
                "height": req.height,
                "seed": seed,
                "steps": req.steps,
                "cfg_scale": req.cfg_scale,
                "denoise_strength": req.denoise_strength,
                "sampler": req.sampler,
                "scheduler": req.scheduler,
                "batch_size": req.batch_size,
                "model": req.model,
                "override": req.override,
                "override_ng": req.override_ng,
                "args": req.args,
                "loras": req.loras,
            }
            final_json = apply_reflex(api_json, reflex_json, params, init_images=uploaded, backend_index=idx)
            task_id, entry = await self.client.submit_and_wait(
                url, final_json, client_id, timeout=self.settings.task_timeout
            )
            result = TaskResult(task_id=task_id, backend_url=url, backend_index=idx)
            await self._collect(result, entry.get("outputs", {}) or {}, reflex_json)
            return result

        t1 = time.monotonic()
        coros = [run_one(req.seed + i, url, idx) for i, (url, idx) in enumerate(targets)]
        if req.concurrency:
            results = list(await asyncio.gather(*coros))
        else:
            results = [await c for c in coros]
        timings["request"] = time.monotonic() - t1

        return GenerationOutcome(
            workflow=req.workflow,
            reflex_json=reflex_json,
            backend_index=backend_idx,
            results=results,
            timings=timings,
        )

    async def _collect(self, result: TaskResult, outputs: dict[str, Any], reflex_json: dict[str, Any]) -> None:
        for media, nodes in _output_map(reflex_json).items():
            for node_id in nodes:
                node_out = outputs.get(str(node_id), {}) or {}
                if media == "image":
                    for meta in node_out.get("images", []):
                        result.images.append(await self.client.download(result.backend_url, meta))
                elif media == "video":
                    for meta in node_out.get("gifs", []) + node_out.get("videos", []):
                        result.videos.append(await self.client.download(result.backend_url, meta))
                elif media == "audio":
                    for meta in node_out.get("audio", []):
                        result.audios.append(await self.client.download(result.backend_url, meta))
                elif media == "text":
                    for txt in node_out.get("text", []):
                        result.texts.append(str(txt))
