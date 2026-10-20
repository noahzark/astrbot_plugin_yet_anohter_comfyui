"""Read-only views for the side commands: help, workflow list, backend status, queue.

Each view returns plain data (dicts and lists) that a card template or a
text formatter turns into a message; nothing here knows about chat platforms.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
from argparse import Namespace
from typing import Any

from .cli import build_parser
from .client import ComfyClient
from .settings import CoreSettings
from .workflow import WorkflowStore

logger = logging.getLogger(__name__)

QUEUE_PARAMS = [
    {"flag": "-be", "description": "后端索引（默认 0）", "example": "queue -t -be 0"},
    {"flag": "-t", "description": "查看后端当前的任务", "example": "queue -t"},
    {"flag": "-i", "description": "查询指定任务的状态", "example": "queue -i <id>"},
    {"flag": "-g", "description": "获取指定任务的输出", "example": "queue -g <id>"},
    {"flag": "-v", "description": "查看历史任务，配合 -index 使用", "example": "queue -v -index 0-20"},
    {"flag": "-d", "description": "从队列删除任务（管理员）", "example": "queue -d <id>"},
    {"flag": "-c", "description": "清空队列（管理员）", "example": "queue -c"},
    {"flag": "-stop", "description": "停止当前生成（管理员）", "example": "queue -stop"},
]

CAPI_PARAMS = [
    {"flag": "-be", "description": "后端索引（默认 0）", "example": "capi -be 0"},
    {"flag": "-g", "description": "节点名称，或 all 列出全部节点", "example": "capi -g KSampler"},
]

OTHER_COMMANDS = [
    {"command": "查看工作流", "description": "查看可用工作流，可加名称或序号筛选", "example": "查看工作流 / 查看工作流 flux"},
    {"command": "comfyui后端", "description": "查看所有后端的状态", "example": "comfyui后端"},
    {"command": "queue", "description": "查看和管理后端队列", "example": "queue -t"},
    {"command": "capi", "description": "查询后端节点信息（管理员）", "example": "capi -g KSampler"},
    {"command": "二次元的我", "description": "随机拼凑提示词生成二次元形象", "example": "二次元的我"},
    {"command": "dan", "description": "从 Danbooru 查询 tag", "example": "dan 原神 5"},
    {"command": "llm-tag", "description": "用 LLM 扩写提示词再生成", "example": "llm-tag 海边的少女 -llm 1"},
    {"command": "get-ckpt", "description": "查看后端的模型列表", "example": "get-ckpt 0"},
    {"command": "get-loras", "description": "查看后端的 LoRA 列表", "example": "get-loras 0"},
    {"command": "get-task", "description": "查看自己提交过的任务", "example": "get-task 0-10"},
]

# Optional packages each local audit mode needs, for the help card's warning.
AUDIT_DEPS = {
    "wd14": ["onnxruntime", "pandas", "huggingface_hub"],
    "nudenet": ["nudenet"],
    "dual": ["onnxruntime", "pandas", "huggingface_hub", "nudenet"],
}


def missing_audit_deps(settings: CoreSettings) -> list[str]:
    if not settings.audit.enabled:
        return []
    return [m for m in AUDIT_DEPS.get(settings.audit.mode, []) if importlib.util.find_spec(m) is None]


def mask_url(url: str) -> str:
    """Hide the middle of a backend URL, as the nonebot status card did."""
    if len(url) <= 16:
        return url
    return url[:8] + "*" * (len(url) - 16) + url[-8:]


# ------------------------------------------------------------------ help


def generation_params(default_workflow: str) -> list[dict[str, str]]:
    params = []
    for action in build_parser(default_workflow)._actions:
        if action.dest == "help":
            continue
        flag = action.option_strings[0] if action.option_strings else action.dest
        text = action.help or ""
        desc, _, example = text.partition("example:")
        params.append({"flag": flag, "description": desc.strip(), "example": example.strip()})
    return params


def help_data(
    settings: CoreSettings,
    reg_commands: list[tuple[str, str]],
    version: str,
    notices: list[str] | None = None,
) -> dict[str, Any]:
    notices = list(notices or [])
    missing = missing_audit_deps(settings)
    if missing:
        notices.append(f"图片审核方式为 {settings.audit.mode}，但未安装: {', '.join(missing)}。请管理员 pip install 这些依赖。")
    return {
        "reg_commands": [{"command": c, "note": n} for c, n in reg_commands],
        "parameters": generation_params(settings.default_workflow),
        "shape_presets": [{"name": k, "width": v[0], "height": v[1]} for k, v in settings.shape_preset.items()],
        "queue_params": QUEUE_PARAMS,
        "capi_params": CAPI_PARAMS,
        "other_commands": OTHER_COMMANDS,
        "notices": notices,
        "version": version,
    }


def help_text(data: dict[str, Any]) -> str:
    lines = [f"Yet Another ComfyUI {data['version']}", "", "基础命令: prompt <提示词> [参数]"]
    if data["reg_commands"]:
        lines.append("工作流命令: " + ", ".join(c["command"] for c in data["reg_commands"]))
    lines.append("")
    lines.append("参数:")
    lines += [f"  {p['flag']}  {p['description']}" for p in data["parameters"]]
    lines.append("分辨率预设: " + ", ".join(f"{p['name']}={p['width']}x{p['height']}" for p in data["shape_presets"]))
    lines.append("")
    lines.append("其他命令:")
    lines += [f"  {c['command']}  {c['description']}" for c in data["other_commands"]]
    lines.append("queue 参数: " + " ".join(p["flag"] for p in data["queue_params"]))
    lines += data["notices"]
    return "\n".join(lines)


# ------------------------------------------------------------- workflows


def _reg_args_rows(reg_args: Any) -> list[dict[str, Any]]:
    rows = []
    if not isinstance(reg_args, dict):
        return rows
    for spec in reg_args.values():
        for arg in (spec or {}).get("args", []) if isinstance(spec, dict) else []:
            flags = arg.get("name_or_flags") or [""]
            rows.append({
                "flag": flags[0],
                "type": arg.get("type", ""),
                "default": arg.get("default", ""),
                "help": arg.get("help", ""),
            })
    return rows


def workflow_rows(store: WorkflowStore, search: str | None = None) -> list[dict[str, Any]]:
    """One row per listed workflow; ``search`` is a 1-based index or a substring.

    Rows keep their position in the full list, so ``index`` is what ``-wf``
    accepts. Workflows whose reflex says ``"visible": false`` are hidden.
    """
    names = store.list_workflows()
    rows = []
    for index, name in enumerate(names, 1):
        if search:
            if search.isdigit():
                if int(search) != index:
                    continue
            elif search not in name:
                continue
        try:
            reflex = store.load_reflex(name)
        except Exception as e:
            logger.warning("Skipping workflow %s in the list: %s", name, e)
            continue
        if reflex.get("visible") is False:
            continue
        load_image = reflex.get("load_image")
        command = reflex.get("command") or ""
        override = reflex.get("override") if isinstance(reflex.get("override"), dict) else {}
        available = reflex.get("available") or []
        rows.append({
            "index": index,
            "name": name,
            "command": " / ".join(command) if isinstance(command, list) else str(command),
            "note": str(reflex.get("note") or "").strip(),
            "day_limit": reflex.get("daylimit") or "不限",
            "needs_image": bool(load_image),
            "image_count": len(load_image) if isinstance(load_image, dict) else (1 if load_image else 0),
            "media": reflex.get("media") or (", ".join(reflex["output"]) if isinstance(reflex.get("output"), dict) else "image"),
            "override": [f"{k}: {v}" for k, v in override.items()],
            "available": ", ".join(str(b) for b in available) + " 号后端" if available else "全部后端",
            "admin": bool(reflex.get("admin")),
            "reg_args": _reg_args_rows(reflex.get("reg_args")),
        })
    return rows


def workflows_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "没有找到工作流"
    lines = []
    for r in rows:
        line = f"{r['index']}. {r['name']}"
        if r["command"]:
            line += f"  命令: {r['command']}"
        if r["needs_image"]:
            line += f"  需要 {r['image_count']} 张图"
        if r["note"]:
            line += f"\n   {r['note']}"
        if r["reg_args"]:
            line += "\n   参数: " + ", ".join(f"{a['flag']} {a['help']}".strip() for a in r["reg_args"])
        lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------- backends


async def backend_status(client: ComfyClient, timeout: int) -> list[dict[str, Any]]:
    async def one(index: int, url: str) -> dict[str, Any]:
        node: dict[str, Any] = {"index": index, "url": mask_url(url), "system": {}, "queue": {}, "error": None}
        stats, queue = await asyncio.gather(
            client.request("GET", f"{url}/system_stats", timeout=timeout),
            client.request("GET", f"{url}/queue", timeout=timeout),
            return_exceptions=True,
        )
        if isinstance(stats, Exception) or not isinstance(stats, dict):
            node["error"] = f"获取系统信息失败: {stats}"
        else:
            system = stats.get("system", {})
            node["system"] = {
                "comfyui_version": system.get("comfyui_version") or "N/A",
                "python_version": str(system.get("python_version") or "N/A").split("|")[0].strip(),
                "pytorch_version": system.get("pytorch_version") or "N/A",
            }
            devices = stats.get("devices") or []
            if devices:
                dev = devices[0]
                node["system"]["device_name"] = str(dev.get("name") or "N/A")
                node["system"]["vram_total_gb"] = round((dev.get("vram_total") or 0) / 1024 ** 3, 2)
                node["system"]["vram_free_gb"] = round((dev.get("vram_free") or 0) / 1024 ** 3, 2)
        if isinstance(queue, Exception) or not isinstance(queue, dict):
            node["error"] = node["error"] or f"获取队列信息失败: {queue}"
        else:
            running = queue.get("queue_running", [])
            pending = queue.get("queue_pending", [])
            node["queue"] = {
                "running_count": len(running),
                "pending_count": len(pending),
                "running_ids": [t[1] for t in running if len(t) > 1],
            }
        if node["error"]:
            # Error texts quote the request URL; cards may go through a public render service.
            node["error"] = node["error"].replace(url, node["url"])
        return node

    return list(await asyncio.gather(*(one(i, u) for i, u in enumerate(client.backend_urls))))


def backends_text(nodes: list[dict[str, Any]]) -> str:
    lines = []
    for n in nodes:
        head = f"[{n['index']}] {n['url']}"
        if n["error"]:
            lines.append(f"{head}  ❌ {n['error']}")
            continue
        s, q = n["system"], n["queue"]
        lines.append(
            f"{head}  ✅ {s.get('device_name', 'N/A')}\n"
            f"   ComfyUI {s.get('comfyui_version')}, 显存 {s.get('vram_free_gb', 'N/A')}/{s.get('vram_total_gb', 'N/A')} GB, "
            f"运行 {q.get('running_count', 0)} 排队 {q.get('pending_count', 0)}"
        )
    return "\n".join(lines) or "没有配置后端"


# ----------------------------------------------------------------- queue


def parse_range(text: str | None, default: tuple[int, int] = (0, 10)) -> tuple[int, int]:
    try:
        start, end = (int(x) for x in str(text).split("-", 1))
        return (start, end) if 0 <= start <= end else default
    except (TypeError, ValueError):
        return default


async def queue_report(client: ComfyClient, url: str, args: Namespace) -> tuple[str, list[dict[str, Any]]]:
    """Run the ``queue`` flags against one backend.

    Returns the text to send and the output files of a ``-g`` task, as
    ``/history`` metadata for the caller to download (and audit).
    """
    lines: list[str] = []
    files: list[dict[str, Any]] = []

    if args.stop:
        try:
            await client.interrupt(url)
            lines.append("已请求停止当前任务")
        except Exception as e:
            lines.append(f"停止任务失败: {e}")

    if args.track:
        try:
            data = await client.request("GET", f"{url}/queue")
            running = [t[1] for t in data.get("queue_running", []) if len(t) > 1]
            pending = [t[1] for t in data.get("queue_pending", []) if len(t) > 1]
            lines.append(f"后端当前共 {len(running) + len(pending)} 个任务")
            if running:
                lines.append("正在执行:\n" + "\n".join(running))
            if pending:
                lines.append("排队中:\n" + "\n".join(pending))
        except Exception as e:
            lines.append(f"获取队列失败: {e}")

    if args.delete:
        ids = [i.strip() for i in args.delete.split(",") if i.strip()]
        try:
            await client.delete_queue(url, ids)
            lines.append(f"已从队列删除: {', '.join(ids)}")
        except Exception as e:
            lines.append(f"删除任务失败: {e}")

    if args.clear:
        try:
            await client.clear_queue(url)
            lines.append("已清空队列")
        except Exception as e:
            lines.append(f"清空队列失败: {e}")

    if args.task_id:
        try:
            hist = await client.get_history(url, args.task_id)
            if args.task_id in hist:
                status = hist[args.task_id].get("status", {})
                done = "是" if status.get("completed") else "否"
                lines.append(f"任务 {args.task_id}\n状态: {status.get('status_str', '未知')}\n是否完成: {done}")
            else:
                lines.append(f"任务 {args.task_id}: 生成中或未找到")
        except Exception as e:
            lines.append(f"查询任务失败: {e}")

    if args.get_task:
        try:
            hist = await client.get_history(url, args.get_task)
            if args.get_task in hist:
                for node in hist[args.get_task].get("outputs", {}).values():
                    for key in ("images", "gifs", "videos", "audio"):
                        files += [m for m in node.get(key, []) or [] if isinstance(m, dict) and m.get("filename")]
                lines.append(f"任务 {args.get_task} 共 {len(files)} 个输出")
            else:
                lines.append(f"未找到任务 {args.get_task} 的历史记录")
        except Exception as e:
            lines.append(f"获取任务输出失败: {e}")

    if args.view:
        try:
            keys = list((await client.request("GET", f"{url}/history")).keys())
            start, end = parse_range(args.index)
            lines.append(f"后端共有 {len(keys)} 个历史任务 [{start}-{end}]:\n" + "\n".join(keys[start:end]))
        except Exception as e:
            lines.append(f"查看历史失败: {e}")

    return "\n".join(lines) or "请指定参数，发送 queue -h 查看用法", files


async def node_info(client: ComfyClient, url: str, node: str) -> str:
    if node == "all":
        names = list((await client.request("GET", f"{url}/object_info")).keys())
        text = f"后端共加载 {len(names)} 个节点:\n" + "\n".join(names[:100])
        if len(names) > 100:
            text += f"\n...（其余 {len(names) - 100} 个省略）"
        return text
    resp = await client.request("GET", f"{url}/object_info/{node}")
    if node not in resp:
        return f"后端没有节点 {node}"
    lines = [f"{k}: {v}" for k, v in resp[node].items()]
    return f"节点 {node}:\n" + "\n".join(lines[:30])
