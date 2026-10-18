"""Workflow files and the reflex mechanism.

A workflow is ``<name>.json`` (ComfyUI API format) next to
``<name>_reflex.json``. :func:`apply_reflex` writes the generation
parameters into the graph as the reflex file directs; see
``docs/md/node_control.md`` for the file format.
"""
from __future__ import annotations

import copy
import json
import logging
import random
from pathlib import Path
from typing import Any

from .constants import MAX_SEED, OTHER_ACTION, MODIFY_ACTION
from .exceptions import (
    ReflexJsonNotFoundError,
    WorkflowAdminLimitation,
    WorkflowGroupLimitation,
    ArgsError,
)
from .lora import apply_loras_to_api_json

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
    if shape:
        preset = shape_preset.get(shape)
        if preset:
            return preset[0], preset[1]
        if "x" in shape:
            try:
                w_s, h_s = shape.split("x")
                return int(w_s), int(h_s)
            except ValueError:
                pass

    if accept_ratio and ":" in accept_ratio:
        try:
            w_ratio, h_ratio = map(int, accept_ratio.split(":"))
            total_pixels = base_res ** 2
            aspect = w_ratio / h_ratio
            if aspect >= 1:
                w = int((total_pixels * aspect) ** 0.5)
                h = int(w / aspect)
            else:
                h = int((total_pixels / aspect) ** 0.5)
                w = int(h * aspect)
            return w, h
        except Exception:
            raise ArgsError(f"画幅比例参数无效: {accept_ratio}")

    return width or default_width, height or default_height


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
    api_json = copy.deepcopy(raw_api_json)
    init_images = init_images or []

    prompt = params.get("prompt", "")
    negative_prompt = params.get("negative_prompt", "")
    width = params.get("width", 832)
    height = params.get("height", 1216)
    seed = params.get("seed", random.randint(0, MAX_SEED))
    steps = params.get("steps", 28)
    cfg_scale = params.get("cfg_scale", 7.0)
    sampler = params.get("sampler", "dpmpp_2m")
    scheduler = params.get("scheduler", "karras")
    denoise_strength = params.get("denoise_strength", 1.0)
    batch_size = params.get("batch_size", 1)
    model = params.get("model", "")
    override = params.get("override", False)
    override_ng = params.get("override_ng", False)
    cli_args = params.get("args")

    update_mapping: dict[str, dict[str, Any]] = {
        "sampler": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg_scale,
            "sampler_name": sampler,
            "scheduler": scheduler,
            "denoise": denoise_strength,
        },
        "seed": {"seed": seed, "noise_seed": seed},
        "image_size": {"width": width, "height": height, "batch_size": batch_size},
        "prompt": {"text": prompt, "Text": prompt, "prompt": prompt},
        "negative_prompt": {"text": negative_prompt, "Text": negative_prompt, "prompt": negative_prompt},
        "checkpoint": {
            "ckpt_name": model if model else None,
            "unet_name": model if model else None,
            "model": model if model else None,
        },
        "load_image": {"image": init_images[0]["name"] if init_images else None},
        "tipo": {"width": width, "height": height, "seed": seed, "tags": prompt},
    }

    for item, node_id in reflex_json.items():
        if node_id and item not in OTHER_ACTION:
            org_node_id = node_id
            target_ids: list[str] = []
            if isinstance(node_id, list):
                target_ids = [str(x) for x in node_id]
            elif isinstance(node_id, (int, str)):
                target_ids = [str(node_id)]
            elif isinstance(node_id, dict):
                target_ids = [str(x) for x in node_id.keys()]

            for nid in target_ids:
                node_data = api_json.get(nid)
                if node_data and item in update_mapping:
                    inputs = node_data.get("inputs", {})
                    for k, v in update_mapping[item].items():
                        if k in inputs and v is not None:
                            inputs[k] = v

            # Per-node override actions.
            if isinstance(org_node_id, dict) and item not in MODIFY_ACTION:
                for node_key, override_dict in org_node_id.items():
                    single_override = override_dict.get("override", {})
                    node_str = str(node_key)
                    if single_override and node_str in api_json:
                        for inp_key, action in single_override.items():
                            if action == "randint":
                                api_json[node_str]["inputs"][inp_key] = random.randint(0, MAX_SEED)
                            elif action == "append_prompt" and not override:
                                orig = raw_api_json[node_str]["inputs"].get(inp_key, "")
                                api_json[node_str]["inputs"][inp_key] = prompt + orig
                            elif action == "append_negative_prompt" and not override_ng:
                                orig = raw_api_json[node_str]["inputs"].get(inp_key, "")
                                api_json[node_str]["inputs"][inp_key] = negative_prompt + orig
                            elif action == "replace_prompt" and not override:
                                orig = raw_api_json[node_str]["inputs"].get(inp_key, "")
                                api_json[node_str]["inputs"][inp_key] = orig.replace("{prompt}", prompt)
                            elif action == "replace_negative_prompt" and not override_ng:
                                orig = raw_api_json[node_str]["inputs"].get(inp_key, "")
                                api_json[node_str]["inputs"][inp_key] = orig.replace("{prompt}", negative_prompt)
                            elif "upscale" in action:
                                scale = 1.5
                                if "_" in action:
                                    scale = float(action.split("_")[1])
                                res = width if inp_key == "width" else height
                                api_json[node_str]["inputs"][inp_key] = int(res * scale)
                            elif "value" in action:
                                if "_" in action:
                                    val_str = action.split("_")[1]
                                    t_str = action.split("_")[2]
                                    if t_str == "int":
                                        val: Any = int(val_str)
                                    elif t_str == "float":
                                        val = float(val_str)
                                    else:
                                        val = str(val_str)
                                    api_json[node_str]["inputs"][inp_key] = val
                            elif "image" in action and init_images:
                                img_idx = int(action.split("_")[1])
                                if img_idx < len(init_images):
                                    api_json[node_str]["inputs"][inp_key] = init_images[img_idx]["name"]

        # Registered CLI args (reg_args).
        elif item == "reg_args" and cli_args:
            args_dict = vars(cli_args)
            for node_k, item_data in node_id.items():
                node_str = str(node_k)
                if node_str not in api_json:
                    continue
                for arg in item_data.get("args", []):
                    dest_key = arg.get("dest")
                    type_str = arg.get("type")
                    json_key = list(arg["dest_to_value"].keys())[0] if "dest_to_value" in arg else dest_key
                    preset_dict = arg.get("preset", {})

                    if "default" in arg:
                        api_json[node_str]["inputs"][json_key] = arg["default"]

                    if hasattr(cli_args, dest_key):
                        val = args_dict[dest_key]
                        if preset_dict and val in preset_dict:
                            val = preset_dict[val]
                        if type_str == "int":
                            val = int(val)
                        elif type_str == "float":
                            val = float(val)
                        elif type_str == "bool":
                            val = bool(val)
                        api_json[node_str]["inputs"][json_key] = val

        # Per-backend patches (reflex).
        elif item == "reflex":
            for be_idx_str, node_reflex in node_id.items():
                if backend_index == int(be_idx_str):
                    for n_id, inps in node_reflex.items():
                        if str(n_id) in api_json:
                            api_json[str(n_id)]["inputs"].update(inps)

    loras = params.get("loras", [])
    if loras and "lora" in reflex_json:
        api_json = apply_loras_to_api_json(api_json, reflex_json["lora"], loras)

    return api_json
