"""Typed settings for the engine.

An adapter builds one ``CoreSettings`` from its own config system (the
AstrBot ``_conf_schema.json``, a yaml file, ...) and hands it to the engine
objects. Defaults mirror the nonebot plugin's ``Config`` so behaviour is
identical when nothing is configured.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import DEFAULT_LLM_SYS_PROMPT, DEFAULT_LLM_CONVERSATIONS


def _default_shape_preset() -> dict[str, tuple[int, int]]:
    return {
        "p": (832, 1216),
        "l": (1216, 832),
        "s": (1024, 1024),
        "lp": (1152, 1536),
        "ll": (1536, 1152),
        "ls": (1240, 1240),
        "up": (960, 1920),
        "ul": (1920, 960),
    }


def _default_value() -> dict[str, Any]:
    return {
        "width": 832,
        "height": 1216,
        "accept_ratio": None,
        "shape": None,
        "steps": 28,
        "cfg_scale": 7.0,
        "denoise_strength": 1.0,
        "sampler": "dpmpp_2m",
        "scheduler": "karras",
        "batch_size": 1,
        "batch_count": 1,
        "model": "",
        "override": False,
        "override_ng": False,
        "forward": False,
        "concurrency": False,
        "pure": False,
        "notice": False,
        "preset_prompt": "",
        "preset_negative_prompt": "",
        "llm_preset": 0,
    }


def _default_max_values() -> dict[str, int]:
    return {"batch_size": 2, "batch_count": 2, "width": 2048, "height": 2048, "steps": 100}


def _default_group_config() -> dict[str, dict]:
    return {
        "audit_level_group": {},
        "reject_nsfw_prompts": {},
        "img_send": {},
        "enable_in_group": {},
        "pure": {},
    }


def _default_wd_model() -> dict[str, Any]:
    return {
        "name": "WaifuDiffusion",
        "repo_id": "wd-vit-tagger-v3",
        "revision": "v2.0",
        "model_path": "model.onnx",
        "tags_path": "selected_tags.csv",
    }


def _default_openai() -> dict[str, Any]:
    return {
        "endpoint": "https://api.openai.com/v1",
        "token": "sk-xxxxxx",
        "params": {
            "model": "gpt-3.5-turbo",
            "temperature": 1,
            "top_p": 1,
            "frequency_penalty": 2,
            "presence_penalty": 2,
        },
        "repeat_sys_prompt": False,
        "prompt": DEFAULT_LLM_SYS_PROMPT,
        "conversations": DEFAULT_LLM_CONVERSATIONS,
    }


@dataclass
class AuditSettings:
    """Image audit. ``mode``: ``remote`` | ``wd14`` | ``nudenet`` | ``dual``."""

    enabled: bool = True
    mode: str = "remote"
    level: int = 2
    site: str = "http://server.20020026.xyz:7865"
    downscale: bool = False
    gpu: bool = False
    wd_model: dict[str, Any] = field(default_factory=_default_wd_model)
    nude_model_path: str = ""


@dataclass
class TextAuditSettings:
    """Prompt text audit through an OpenAI-compatible endpoint."""

    enabled: bool = False
    ban_words: list[str] = field(default_factory=list)
    openai: dict[str, Any] = field(default_factory=_default_openai)


@dataclass
class CoreSettings:
    backend_urls: list[str] = field(default_factory=lambda: ["http://127.0.0.1:8188"])
    timeout: int = 5
    http_proxy: str = ""
    model: str = ""
    task_timeout: int = 1800

    workflows_dir: Path = Path("./data/comfyui")
    default_workflow: str = "txt2img"
    trigger_words: list[str] = field(default_factory=lambda: ["comfyui执行"])
    auto_lora: bool = False
    base_res: int = 1024
    shape_preset: dict[str, tuple[int, int]] = field(default_factory=_default_shape_preset)
    default_value: dict[str, Any] = field(default_factory=_default_value)
    max_values: dict[str, int] = field(default_factory=_default_max_values)

    cd: int = 20
    day_limit: int = 50
    superusers: list[str] = field(default_factory=list)

    save_image: bool = True
    silent: bool = False
    quiet: bool = False

    audit: AuditSettings = field(default_factory=AuditSettings)
    text_audit: TextAuditSettings = field(default_factory=TextAuditSettings)
    group_config: dict[str, dict] = field(default_factory=_default_group_config)

    def __post_init__(self) -> None:
        self.workflows_dir = Path(self.workflows_dir)
        # JSON configs give lists; the engine wants tuples for presets.
        self.shape_preset = {k: (int(v[0]), int(v[1])) for k, v in self.shape_preset.items()}
        self.superusers = [str(u) for u in self.superusers]
