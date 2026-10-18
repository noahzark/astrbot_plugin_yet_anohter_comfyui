"""Yet Another ComfyUI: the AstrBot port of nonebot-plugin-comfyui.

Workflows are the same ``<name>.json`` + ``<name>_reflex.json`` pairs the
nonebot plugin used; the engine that applies them lives in ``comfyui_core``
and this module only speaks AstrBot.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .adapter.commands import COMMANDS, EXTRA_KEY, GenerateCommandFilter
from .adapter.config import build_settings
from .adapter.render import build_chains
from .adapter.workflows import install_builtin_workflows
from .comfyui_core import __version__ as CORE_VERSION
from .comfyui_core.cli import build_parser, tokenize
from .comfyui_core.client import ComfyClient
from .comfyui_core.exceptions import ComfyuiExceptions
from .comfyui_core.limiter import RateLimiter
from .comfyui_core.pipeline import GenerationPipeline, build_request
from .comfyui_core.workflow import WorkflowStore

PLUGIN_NAME = "astrbot_plugin_yet_anohter_comfyui"

# Route the engine's stdlib logging into AstrBot's logger.
logging.getLogger("comfyui_core").setLevel(logging.INFO)


class YetAnotherComfyUI(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        self.data_dir = self.resolve_data_dir()
        self.settings = build_settings(self.config, self.data_dir)
        self.store = WorkflowStore(self.settings.workflows_dir)
        self.client = ComfyClient(self.settings.backend_urls, self.settings.timeout, self.settings.http_proxy)
        self.pipeline = GenerationPipeline(self.client, self.store, self.settings)
        self.limiter = RateLimiter(self.settings.cd, self.settings.day_limit)

    # ------------------------------------------------------------------ setup

    def resolve_data_dir(self) -> Path:
        try:
            return Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:  # StarTools not initialised (tests, odd loaders)
            path = Path("data") / "plugin_data" / PLUGIN_NAME
            path.mkdir(parents=True, exist_ok=True)
            return path

    def refresh_commands(self) -> None:
        reflexes: dict[str, dict[str, Any]] = {}
        for name in self.store.list_workflows():
            try:
                reflexes[name] = self.store.load_reflex(name)
            except Exception as e:  # one bad file must not take the plugin down
                logger.warning("Skipping workflow %s: %s", name, e)
        COMMANDS.refresh(self.settings.trigger_words, reflexes)
        logger.info("ComfyUI commands: %s", ", ".join(COMMANDS.words()))

    async def initialize(self) -> None:
        copied = install_builtin_workflows(Path(self.settings.workflows_dir))
        if copied:
            logger.info("Installed built-in workflows into %s: %s", self.settings.workflows_dir, ", ".join(copied))
        self.refresh_commands()
        logger.info(
            "Yet Another ComfyUI ready (core %s), backends: %s", CORE_VERSION, ", ".join(self.settings.backend_urls)
        )

    async def terminate(self) -> None:
        await self.client.close()

    # --------------------------------------------------------------- commands

    @filter.custom_filter(GenerateCommandFilter)
    async def generate(self, event: AstrMessageEvent):
        """prompt / 工作流命令: 用 ComfyUI 生成图片。加 -h 查看全部参数。"""
        word = event.get_extra(EXTRA_KEY) or ""
        body = COMMANDS.strip(word, event.message_str)
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        is_admin = event.is_admin()
        is_superuser = is_admin or user_id in self.settings.superusers

        try:
            workflow, args, reflex = self.parse(body, COMMANDS.workflow_for(word))
        except ComfyuiExceptions as e:
            yield event.plain_result(str(e))
            event.stop_event()
            return

        in_cd, remain = self.limiter.check_cd(user_id, is_superuser)
        if in_cd:
            yield event.plain_result(f"您冲得太快啦，请休息一下，剩余冷却时间为: {remain}s")
            event.stop_event()
            return

        daily_key = self.limiter.get_today_key(user_id)
        total = (getattr(args, "batch_size", 1) or 1) * (getattr(args, "batch_count", 1) or 1)
        limit_msg, reached = self.limiter.check_daily_limit(
            daily_key, total, is_superuser, wf_name=workflow, wf_limit=reflex.get("daylimit")
        )
        if reached:
            yield event.plain_result(f"调用受限: {limit_msg}")
            event.stop_event()
            return
        self.limiter.update_cd(user_id)

        try:
            req = build_request(
                args, self.settings, user_id=user_id, group_id=group_id, is_admin=is_admin, is_superuser=is_superuser
            )
            req.workflow = workflow

            async def notify(text: str) -> None:
                await event.send(MessageChain().message(text))

            quiet = req.silent or self.settings.quiet
            outcome = await self.pipeline.run(req, notify=None if quiet else notify)
        except ComfyuiExceptions as e:
            self.limiter.revert_daily(daily_key, total)
            logger.error("ComfyUI generation failed: %s", e)
            yield event.plain_result(f"生图失败: {e}")
            event.stop_event()
            return
        except Exception as e:
            self.limiter.revert_daily(daily_key, total)
            logger.exception("ComfyUI generation crashed")
            yield event.plain_result(f"生图发生未知错误: {e}")
            event.stop_event()
            return

        reply_to = str(getattr(event.message_obj, "message_id", "") or "") or None
        chains, written = build_chains(
            req, outcome, data_dir=self.data_dir, reply_to=reply_to, persistent=self.settings.save_image
        )
        if not self.settings.save_image:
            for path in written:
                event.track_temporary_local_file(str(path))
        for chain in chains:
            if chain:
                yield event.chain_result(chain)
        event.stop_event()

    # ---------------------------------------------------------------- helpers

    def parse(self, body: str, workflow: str | None):
        """Parse the message body for ``workflow`` (``None`` = default).

        A ``-wf`` flag in the body wins over the command's own workflow, and
        the final parse uses that workflow's ``reg_args``.
        """
        default_wf = workflow or self.settings.default_workflow
        tokens = tokenize(body)
        pre, _ = build_parser(default_wf).parse_known_args(tokens)
        resolved = self.store.resolve(pre.work_flows or default_wf)
        reflex = self.store.load_reflex(resolved)
        args = build_parser(resolved, reflex.get("reg_args")).parse_args(tokens)
        args.work_flows = resolved
        return resolved, args, reflex
