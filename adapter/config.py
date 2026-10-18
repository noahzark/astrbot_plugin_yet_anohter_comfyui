"""Map the plugin's ``_conf_schema.json`` values onto :class:`CoreSettings`."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..comfyui_core.settings import CoreSettings, _default_shape_preset


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
    )
