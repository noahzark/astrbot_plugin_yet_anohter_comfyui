"""Workflow files and the reflex mechanism.

A workflow is ``<name>.json`` (ComfyUI API format) next to
``<name>_reflex.json``. :func:`apply_reflex` writes the generation
parameters into the graph as the reflex file directs; see
``docs/md/node_control.md`` for the file format. The graph rewriting
itself is the ``comfyui_reflex`` library (split out of the nonebot
plugin); this module adds what the plugin needs on top of it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from comfyui_reflex.dimensions import calculate_dimensions as _reflex_dimensions
from comfyui_reflex.engine import ReflexEngine
from comfyui_reflex.exceptions import ReflexArgumentError

from .exceptions import (
    ReflexJsonNotFoundError,
    WorkflowAdminLimitation,
    WorkflowGroupLimitation,
    ArgsError,
)

logger = logging.getLogger(__name__)


class WorkflowStore:
    """Lists and loads workflow pairs from one directory."""

    def __init__(self, workflows_dir: str | Path) -> None:
        self.dir = Path(workflows_dir).resolve()

    def list_workflows(self, search: str | None = None, index: int | None = None) -> list[str]:
        """Names of every ``.json`` that has a ``_reflex.json`` companion."""
        if not self.dir.exists():
            return []

        names: list[str] = []
        for path in sorted(self.dir.iterdir()):
            file = path.name
            if file.endswith(".json") and not file.endswith("_reflex.json"):
                name = file[:-5]
                if (self.dir / f"{name}_reflex.json").exists():
                    if search and search in name:
                        names.append(name)
                    elif not search:
                        names.append(name)

        if index is not None:
            if 1 <= index <= len(names):
                return [names[index - 1]]
            return []
        return names

    def resolve(self, name: str) -> str:
        """Turn what a user typed after ``-wf`` into a workflow name.

        Exact name first, then a 1-based index, then a unique substring.
        """
        names = self.list_workflows()
        if name in names:
            return name
        if name.isdigit():
            hit = self.list_workflows(index=int(name))
            if hit:
                return hit[0]
        partial = [n for n in names if name in n]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise ArgsError(f"工作流名称 {name} 不唯一，匹配到: {', '.join(partial)}")
        raise ReflexJsonNotFoundError(f"未找到工作流: {name}")

    def load(self, wf_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        api_path = self.dir / f"{wf_name}.json"
        reflex_path = self.dir / f"{wf_name}_reflex.json"
        if not api_path.exists() or not reflex_path.exists():
            raise ReflexJsonNotFoundError(f"未找到工作流 {wf_name} 的完整定义文件")
        api_json = json.loads(api_path.read_text(encoding="utf-8"))
        reflex_json = json.loads(reflex_path.read_text(encoding="utf-8"))
        return api_json, reflex_json

    def load_reflex(self, wf_name: str) -> dict[str, Any]:
        reflex_path = self.dir / f"{wf_name}_reflex.json"
        if not reflex_path.exists():
            raise ReflexJsonNotFoundError(f"未找到工作流 {wf_name} 的 reflex 文件")
        return json.loads(reflex_path.read_text(encoding="utf-8"))


def calculate_dimensions(
    shape: str | None = None,
    accept_ratio: str | None = None,
    width: int | None = None,
    height: int | None = None,
    *,
    shape_preset: dict[str, tuple[int, int]],
    base_res: int,
    default_width: int,
    default_height: int,
) -> tuple[int, int]:
    """Shape preset or ``WxH`` first, then ``W:H`` ratio, then explicit/defaults."""
    try:
        return _reflex_dimensions(
            shape=shape,
            accept_ratio=accept_ratio,
            width=width,
            height=height,
            shape_presets=shape_preset,
            base_res=base_res,
            default_width=default_width,
            default_height=default_height,
        )
    except ReflexArgumentError as e:
        raise ArgsError(str(e)) from e


def validate_permissions(reflex_json: dict[str, Any], group_id: str, is_admin: bool) -> None:
    """``admin: true`` workflows need an admin; ``group: [...]`` limits groups."""
    if reflex_json.get("admin", False) and not is_admin:
        raise WorkflowAdminLimitation("此工作流仅限管理员使用")

    allowed_groups = reflex_json.get("group")
    if allowed_groups and group_id:
        try:
            gid_int = int(group_id)
            if gid_int not in allowed_groups:
                raise WorkflowGroupLimitation("此工作流不允许在当前群使用")
        except ValueError:
            pass


def apply_reflex(
    raw_api_json: dict[str, Any],
    reflex_json: dict[str, Any],
    params: dict[str, Any],
    init_images: list[dict[str, Any]] | None = None,
    backend_index: int = 0,
) -> dict[str, Any]:
    """Return a copy of the graph with the parameters written in.

    ``params`` keys: prompt, negative_prompt, width, height, seed, steps,
    cfg_scale, sampler, scheduler, denoise_strength, batch_size, model,
    override, override_ng, args (an argparse Namespace for ``reg_args``),
    loras. ``init_images`` are the ``/upload/image`` responses.
    """
    api_json = ReflexEngine.apply_reflex(
        raw_api_json=raw_api_json,
        reflex_json=reflex_json,
        params=params,
        init_images=init_images,
        backend_index=backend_index,
    )
    _fill_string_primitives(raw_api_json, api_json, reflex_json, params)
    return api_json


def _node_ids(node_ref: Any) -> list[str]:
    if isinstance(node_ref, (list, dict)):
        return [str(x) for x in node_ref]
    if isinstance(node_ref, (int, str)):
        return [str(node_ref)]
    return []


def _fill_string_primitives(
    raw_api_json: dict[str, Any],
    api_json: dict[str, Any],
    reflex_json: dict[str, Any],
    params: dict[str, Any],
) -> None:
    """Write the prompts into ``value`` text boxes (PrimitiveStringMultiline).

    comfyui_reflex only fills ``text``/``Text``/``prompt``. A ``value`` that
    still holds the workflow's baked-in string gets the prompt; linked or
    numeric values, and ones another reflex rule already rewrote, stay.
    """
    for item in ("prompt", "negative_prompt"):
        node_ref = reflex_json.get(item)
        if not node_ref:
            continue
        text = params.get(item, "")
        for nid in _node_ids(node_ref):
            raw = raw_api_json.get(nid, {}).get("inputs", {}).get("value")
            inputs = api_json.get(nid, {}).get("inputs")
            if isinstance(raw, str) and inputs is not None and inputs.get("value") == raw:
                inputs["value"] = text
