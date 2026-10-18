"""Map the plugin's ``_conf_schema.json`` values onto :class:`CoreSettings`."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..comfyui_core.settings import (
    AuditSettings,
    CoreSettings,
    TextAuditSettings,
    _default_group_config,
    _default_shape_preset,
    _default_wd_model,
)


def _section(cfg: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = cfg.get(name) if isinstance(cfg, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def parse_shape_presets(entries: Any) -> dict[str, tuple[int, int]]:
    """``["p=832x1216", "l=1216x832"]`` -> ``{"p": (832, 1216), ...}``.

    Malformed entries are skipped; an empty list means the built-in presets.
    """
    presets: dict[str, tuple[int, int]] = {}
    for entry in entries or []:
        if not isinstance(entry, str) or "=" not in entry:
            continue
        name, _, size = entry.partition("=")
        w, _, h = size.lower().partition("x")
        try:
            presets[name.strip()] = (int(w), int(h))
        except ValueError:
            continue
    return presets or _default_shape_preset()


def _clean_default_value(defaults: Mapping[str, Any]) -> dict[str, Any]:
    """The schema stores every default as a concrete value; the engine wants
    ``None`` for "not set" on shape and accept_ratio."""
    out: dict[str, Any] = dict(defaults)
    for key in ("shape", "accept_ratio", "model", "preset_prompt", "preset_negative_prompt"):
        if key in out and out[key] in ("", None):
            out[key] = None if key in ("shape", "accept_ratio") else ""
    return out


def parse_group_overrides(entries: Any) -> dict[str, dict]:
    """The ``groups.overrides`` template list -> the engine's ``group_config``.

    Numbers below 0 mean "follow the global setting" and are left out.
    """
    out = _default_group_config()
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        gid = str(entry.get("group_id") or "").strip()
        if not gid:
            continue
        if not entry.get("enabled", True):
            out["enable_in_group"][gid] = 0
        if entry.get("pure"):
            out["pure"][gid] = True
        for src, dst in (("audit_level", "audit_level_group"), ("reject_nsfw_prompts", "reject_nsfw_prompts"),
                         ("img_send", "img_send")):
            try:
                value = int(entry.get(src, -1))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                out[dst][gid] = value
    return out


def build_audit(audit: Mapping[str, Any]) -> AuditSettings:
    base = AuditSettings()
    wd_model = _default_wd_model()
    if audit.get("wd_repo_id"):
        wd_model["repo_id"] = str(audit["wd_repo_id"])
    return AuditSettings(
        enabled=bool(audit.get("enabled", base.enabled)),
        mode=str(audit.get("mode") or base.mode),
        level=int(audit.get("level", base.level)),
        site=str(audit.get("site") or base.site).rstrip("/"),
        downscale=bool(audit.get("downscale", base.downscale)),
        gpu=bool(audit.get("gpu", base.gpu)),
        wd_model=wd_model,
        nude_model_path=str(audit.get("nude_model_path") or ""),
        fail_closed=bool(audit.get("fail_closed", base.fail_closed)),
    )


def build_text_audit(text: Mapping[str, Any]) -> TextAuditSettings:
    settings = TextAuditSettings(
        enabled=bool(text.get("enabled", False)),
        ban_words=[str(w) for w in (text.get("ban_words") or []) if str(w).strip()],
        use_endpoint=bool(text.get("use_endpoint", False)),
    )
    if text.get("endpoint"):
        settings.openai["endpoint"] = str(text["endpoint"]).rstrip("/")
    if text.get("token"):
        settings.openai["token"] = str(text["token"])
    if text.get("model"):
        settings.openai["params"] = {**settings.openai.get("params", {}), "model": str(text["model"])}
    return settings


def text_audit_provider(cfg: Mapping[str, Any]) -> str:
    """The AstrBot provider id picked for text audit ("" = the session's own)."""
    return str(_section(cfg, "text_audit").get("provider") or "")


def build_settings(cfg: Mapping[str, Any], data_dir: Path) -> CoreSettings:
    backend = _section(cfg, "backend")
    workflow = _section(cfg, "workflow")
    generation = _section(cfg, "generation")
    limits = _section(cfg, "limits")
    delivery = _section(cfg, "delivery")

    base = CoreSettings()
    default_value = dict(base.default_value)
    default_value.update(_clean_default_value(_section(generation, "defaults")))
    max_values = dict(base.max_values)
    max_values.update({k: int(v) for k, v in _section(generation, "max_values").items() if v is not None})

    workflows_dir = str(workflow.get("workflows_dir") or "").strip()
    urls = [str(u).strip().rstrip("/") for u in (backend.get("urls") or []) if str(u).strip()]

    return CoreSettings(
        backend_urls=urls or list(base.backend_urls),
        timeout=int(backend.get("timeout") or base.timeout),
        task_timeout=int(backend.get("task_timeout") or base.task_timeout),
        http_proxy=str(backend.get("http_proxy") or ""),
        model=str(backend.get("model") or ""),
        workflows_dir=Path(workflows_dir) if workflows_dir else data_dir / "workflows",
        default_workflow=str(workflow.get("default_workflow") or base.default_workflow),
        trigger_words=[str(w) for w in (workflow.get("trigger_words") or []) if str(w).strip()],
        auto_lora=bool(workflow.get("auto_lora", False)),
        base_res=int(generation.get("base_res") or base.base_res),
        shape_preset=parse_shape_presets(generation.get("shape_preset")),
        default_value=default_value,
        max_values=max_values,
        cd=int(limits.get("cd", base.cd)),
        day_limit=int(limits.get("day_limit", base.day_limit)),
        superusers=[str(u) for u in (limits.get("superusers") or [])],
        save_image=bool(delivery.get("save_image", True)),
        silent=bool(delivery.get("silent", False)),
        quiet=bool(delivery.get("quiet", False)),
        img_send=int(delivery.get("img_send", base.img_send)),
        r18_action=int(delivery.get("r18_action", base.r18_action)),
        audit=build_audit(_section(cfg, "audit")),
        text_audit=build_text_audit(_section(cfg, "text_audit")),
        group_config=parse_group_overrides(_section(cfg, "groups").get("overrides")),
    )
