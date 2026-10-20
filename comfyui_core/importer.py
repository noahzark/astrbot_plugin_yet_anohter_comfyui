"""Turn a nonebot ``config/comfyui.yaml`` into this plugin's config sections.

One-shot migration for ``/comfy import-config``: every key that has a home
in the new schema is mapped, everything else is reported back unmapped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Keys the new schema dropped on purpose (they did nothing, or are implied).
DROPPED = {
    "comfyui_ai_prompt", "comfyui_translate", "trans_api", "comfyui_qr_mode", "comfyui_random_params",
    "comfyui_random_params_enable", "comfyui_tips", "comfyui_limit_as_seconds", "comfyui_multi_backend",
    "comfyui_random_wf", "comfyui_random_wf_list",
}

DEFAULT_KEYS = (
    "width", "height", "shape", "accept_ratio", "steps", "cfg_scale", "denoise_strength", "sampler",
    "scheduler", "batch_size", "batch_count", "preset_prompt", "preset_negative_prompt", "override", "override_ng",
)
MAX_KEYS = ("batch_size", "batch_count", "width", "height", "steps")
PLACEHOLDER_TOKEN = "sk-xxxxxx"


@dataclass
class ImportResult:
    config: dict[str, dict[str, Any]] = field(default_factory=dict)
    unmapped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def put(self, section: str, key: str, value: Any) -> None:
        self.config.setdefault(section, {})[key] = value


def _resolve_dir(value: str, yaml_path: Path | None) -> str:
    """nonebot resolved relative paths from the bot directory (the yaml's grandparent)."""
    path = Path(value)
    if path.is_absolute() or yaml_path is None:
        return value
    return str((yaml_path.parent.parent / path).resolve())


def _group_overrides(group_config: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}

    def entry(gid: Any) -> dict[str, Any]:
        return groups.setdefault(str(gid), {
            "__template_key": "group", "group_id": str(gid), "enabled": True,
            "audit_level": -1, "reject_nsfw_prompts": -1, "img_send": -1, "pure": False,
        })

    for src, dst in (("audit_level_group", "audit_level"), ("reject_nsfw_prompts", "reject_nsfw_prompts"),
                     ("img_send", "img_send")):
        for gid, value in (group_config.get(src) or {}).items():
            entry(gid)[dst] = int(value)
    for gid, value in (group_config.get("enable_in_group") or {}).items():
        entry(gid)["enabled"] = bool(int(value))
    for gid, value in (group_config.get("pure") or {}).items():
        entry(gid)["pure"] = bool(value)
    return list(groups.values())


def import_nonebot_config(data: dict[str, Any], yaml_path: Path | None = None) -> ImportResult:
    res = ImportResult()
    seen: set[str] = set()

    def take(key: str) -> Any:
        seen.add(key)
        return data[key]

    def has(key: str) -> bool:
        return key in data and data[key] is not None

    # backend
    if has("comfyui_url_list") and data.get("comfyui_multi_backend"):
        res.put("backend", "urls", [str(u) for u in take("comfyui_url_list")])
    elif has("comfyui_url"):
        res.put("backend", "urls", [str(take("comfyui_url"))])
    seen.update({"comfyui_url", "comfyui_url_list"})
    for src, dst in (("comfyui_timeout", "timeout"), ("comfyui_http_proxy", "http_proxy"), ("comfyui_model", "model")):
        if has(src):
            res.put("backend", dst, take(src))

    # workflow
    if has("comfyui_workflows_dir"):
        res.put("workflow", "workflows_dir", _resolve_dir(str(take("comfyui_workflows_dir")), yaml_path))
    if has("comfyui_default_workflows"):
        res.put("workflow", "default_workflow", str(take("comfyui_default_workflows")))
    if has("comfyui_trigger_word"):
        res.put("workflow", "trigger_words", [str(w) for w in take("comfyui_trigger_word") or [] if w])
    if has("comfyui_auto_lora"):
        res.put("workflow", "auto_lora", bool(take("comfyui_auto_lora")))

    # generation
    if has("comfyui_base_res"):
        res.put("generation", "base_res", int(take("comfyui_base_res")))
    if has("comfyui_shape_preset"):
        res.put("generation", "shape_preset",
                [f"{k}={int(v[0])}x{int(v[1])}" for k, v in take("comfyui_shape_preset").items()])
    if has("comfyui_default_value"):
        defaults = take("comfyui_default_value")
        res.put("generation", "defaults", {
            k: ("" if defaults[k] is None else defaults[k]) for k in DEFAULT_KEYS if k in defaults
        })
        if defaults.get("model") and "model" not in res.config.get("backend", {}):
            res.put("backend", "model", defaults["model"])
        rest = sorted(set(defaults) - set(DEFAULT_KEYS) - {"model"})
        if rest:
            res.notes.append("comfyui_default_value 中这些项没有对应设置，请改用命令参数: " + ", ".join(rest))
    if has("comfyui_max_dict"):
        res.put("generation", "max_values", {k: int(v) for k, v in take("comfyui_max_dict").items() if k in MAX_KEYS})

    # limits
    for src, dst in (("comfyui_cd", "cd"), ("comfyui_day_limit", "day_limit")):
        if has(src):
            res.put("limits", dst, int(take(src)))
    if has("comfyui_superusers"):
        res.put("limits", "superusers", [str(u) for u in take("comfyui_superusers") or [] if u is not None])

    # delivery
    for src, dst in (("comfyui_save_image", "save_image"), ("comfyui_silent", "silent"), ("comfyui_quiet", "quiet")):
        if has(src):
            res.put("delivery", dst, bool(take(src)))
    for src, dst in (("comfyui_img_send", "img_send"), ("comfyui_r18_action", "r18_action")):
        if has(src):
            res.put("delivery", dst, int(take(src)))

    # audit
    if has("comfyui_audit"):
        res.put("audit", "enabled", bool(take("comfyui_audit")))
    if has("comfyui_audit_level"):
        res.put("audit", "level", int(take("comfyui_audit_level")))
    if data.get("comfyui_dual_audit"):
        mode = "dual"
    elif data.get("comfyui_audit_local"):
        mode = "nudenet" if int(data.get("comfyui_audit_model") or 1) == 2 else "wd14"
    else:
        mode = "remote"
    if {"comfyui_dual_audit", "comfyui_audit_local", "comfyui_audit_model"} & set(data):
        res.put("audit", "mode", mode)
    seen.update({"comfyui_dual_audit", "comfyui_audit_local", "comfyui_audit_model"})
    for src, dst in (("comfyui_audit_site", "site"), ("comfyui_nude_model_path", "nude_model_path")):
        if has(src):
            res.put("audit", dst, str(take(src)))
    for src, dst in (("comfyui_audit_comp", "downscale"), ("comfyui_audit_gpu", "gpu")):
        if has(src):
            res.put("audit", dst, bool(take(src)))
    if has("comfyui_wd_model"):
        repo = (take("comfyui_wd_model") or {}).get("repo_id")
        if repo:
            res.put("audit", "wd_repo_id", str(repo))

    # text audit and LLM
    if has("comfyui_text_audit"):
        res.put("text_audit", "enabled", bool(take("comfyui_text_audit")))
    if has("comfyui_ban_words"):
        res.put("text_audit", "ban_words", [str(w) for w in take("comfyui_ban_words") or [] if w])
    if has("comfyui_openai"):
        openai = take("comfyui_openai") or {}
        token = str(openai.get("token") or "")
        if token and token != PLACEHOLDER_TOKEN:
            res.put("text_audit", "use_endpoint", True)
            res.put("text_audit", "token", token)
            if openai.get("endpoint"):
                res.put("text_audit", "endpoint", str(openai["endpoint"]))
            model = (openai.get("params") or {}).get("model")
            if model:
                res.put("text_audit", "model", str(model))
            res.notes.append("提示词审核沿用原来的 OpenAI 接口；llm-tag 改用 AstrBot 的模型，可在「LLM 提示词」里选择")
        if openai.get("prompt"):
            res.put("llm", "system_prompt", str(openai["prompt"]))
    if has("comfyui_llm_prompt_preset"):
        presets = [
            {"__template_key": "preset", "name": str(p.get("name") or f"预设 {i}"), "provider": "",
             "system_prompt": str(p.get("prompt") or "")}
            for i, p in enumerate(take("comfyui_llm_prompt_preset") or [], 1)
            if isinstance(p, dict)
        ]
        if presets:
            res.put("llm", "presets", presets)
            res.notes.append("LLM 预设只导入了系统提示词，使用的模型请在 AstrBot 里选择")

    # groups
    if has("comfyui_group_config"):
        overrides = _group_overrides(take("comfyui_group_config") or {})
        if overrides:
            res.put("groups", "overrides", overrides)

    res.unmapped = sorted(k for k in data if k not in seen and k not in DROPPED)
    dropped = sorted(k for k in data if k in DROPPED)
    if dropped:
        res.notes.append("这些旧设置已不再使用，没有导入: " + ", ".join(dropped))
    return res


def merge_into(config: dict[str, Any], result: ImportResult) -> None:
    """Write the imported sections over ``config`` (an AstrBotConfig or a dict)."""
    for section, values in result.config.items():
        current = config.get(section)
        if not isinstance(current, dict):
            config[section] = dict(values)
            continue
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key].update(value)
            else:
                current[key] = value
