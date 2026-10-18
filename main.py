"""Yet Another ComfyUI: the AstrBot port of nonebot-plugin-comfyui.

Workflows are the same ``<name>.json`` + ``<name>_reflex.json`` pairs the
nonebot plugin used; the engine that applies them lives in ``comfyui_core``
and this module only speaks AstrBot.
"""
from __future__ import annotations

import contextvars
import logging
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .adapter.commands import COMMANDS, EXTRA_KEY, GenerateCommandFilter
from .adapter.config import build_settings, text_audit_provider
from .adapter.media import collect_input_images
from .adapter import qq
from .adapter.render import Delivery
from .adapter.workflows import install_builtin_workflows
from .comfyui_core import __version__ as CORE_VERSION
from .comfyui_core.cli import build_parser, tokenize
from .comfyui_core.client import ComfyClient
from .comfyui_core.exceptions import ComfyuiExceptions
from .comfyui_core.audit import ImageAuditor, TextAuditor
from .comfyui_core.limiter import RateLimiter
from .comfyui_core.pipeline import GenerationPipeline, build_request
from .comfyui_core.workflow import WorkflowStore

PLUGIN_NAME = "astrbot_plugin_yet_anohter_comfyui"
USAGE_KEY = "daily_usage"

# The session a generation runs for, so the text-audit LLM can use its model.
CURRENT_UMO: contextvars.ContextVar[str] = contextvars.ContextVar("yac_umo", default="")

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
        self.text_provider = text_audit_provider(self.config)
        self.pipeline = GenerationPipeline(
            self.client,
            self.store,
            self.settings,
            image_auditor=ImageAuditor(self.settings, self.client),
            text_auditor=TextAuditor(self.settings, self.client, llm=self.text_audit_llm),
        )
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
        await self.load_usage()
        logger.info(
            "Yet Another ComfyUI ready (core %s), backends: %s", CORE_VERSION, ", ".join(self.settings.backend_urls)
        )

    async def terminate(self) -> None:
        qq.cancel_pending()
        await self.client.close()

    # --------------------------------------------------------------- commands

    @filter.custom_filter(GenerateCommandFilter)
    async def generate(self, event: AstrMessageEvent):
        """prompt / 工作流命令: 用 ComfyUI 生成图片。加 -h 查看全部参数。"""
        try:
            async for result in self.run_generation(event):
                yield result
        finally:
            # Only after the last yield: AstrBot drops results of an already stopped event.
            event.stop_event()

    async def run_generation(self, event: AstrMessageEvent):
        word = event.get_extra(EXTRA_KEY) or ""
        body = COMMANDS.strip(word, event.message_str)
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        is_admin = event.is_admin()
        is_superuser = is_admin or user_id in self.settings.superusers

        if self.settings.group_value("enable_in_group", group_id, 1) in (0, False):
            yield event.plain_result("本群已禁用 ComfyUI 生图插件")
            return

        try:
            workflow, args, reflex = self.parse(body, COMMANDS.workflow_for(word))
            req = build_request(
                args,
                self.settings,
                user_id=user_id,
                group_id=group_id,
                is_admin=is_admin,
                is_superuser=is_superuser,
                workflow_defaults=reflex.get("override") if isinstance(reflex.get("override"), dict) else None,
            )
            req.workflow = workflow
        except ComfyuiExceptions as e:
            yield event.plain_result(str(e))
            return

        in_cd, remain = self.limiter.check_cd(user_id, is_superuser)
        if in_cd:
            text = f"您冲得太快啦，请休息一下，剩余冷却时间为: {remain}s"
            if not await qq.send_text_and_recall(event, text, qq.RECALL_COOLDOWN):
                yield event.plain_result(text)
            return

        daily_key = self.limiter.get_today_key(user_id)
        total = req.total_images
        limit_msg, reached = self.limiter.check_daily_limit(
            daily_key, total, is_superuser, wf_name=workflow, wf_limit=reflex.get("daylimit")
        )
        if reached:
            text = f"调用受限: {limit_msg}"
            if not await qq.send_text_and_recall(event, text, qq.RECALL_LIMIT):
                yield event.plain_result(text)
            return
        self.limiter.update_cd(user_id)
        await self.save_usage()

        async def notify(text: str) -> None:
            if not await qq.send_text_and_recall(event, text, qq.RECALL_NOTICE):
                await event.send(MessageChain().message(text))

        token = CURRENT_UMO.set(event.unified_msg_origin)
        try:
            if reflex.get("load_image"):
                req.init_images = await collect_input_images(event, keep_gif=bool(getattr(args, "gif", False)))
            quiet = req.silent or self.settings.quiet
            outcome = await self.pipeline.run(req, notify=None if quiet else notify)
        except Exception as e:
            self.limiter.revert_daily(daily_key, total, wf_name=workflow)
            await self.save_usage()
            if isinstance(e, ComfyuiExceptions):
                logger.error("ComfyUI generation failed: %s", e)
                text = f"生图失败: {e}"
            else:
                logger.exception("ComfyUI generation crashed")
                text = f"生图发生未知错误: {e}"
            if not await qq.send_text_and_recall(event, text, qq.RECALL_ERROR):
                yield event.plain_result(text)
            return
        finally:
            CURRENT_UMO.reset(token)

        if req.notice:
            ids = ", ".join(r.task_id for r in outcome.results)
            await self.dm(event, [Comp.Plain(f"您的生图任务已完成 (ID: {ids})")])

        delivery = Delivery(
            data_dir=self.data_dir,
            persistent=self.settings.save_image,
            img_send=int(self.settings.group_value("img_send", group_id, self.settings.img_send)),
            r18_action=self.settings.r18_action,
            pure=bool(self.settings.group_value("pure", group_id, False)),
            reply_to=str(getattr(event.message_obj, "message_id", "") or "") or None,
            link_for=(lambda img: qq.image_link(event, img)) if qq.is_qq(event) else None,
            dm=lambda chain: self.dm(event, chain),
        )
        chains = await delivery.build(req, outcome)
        if not self.settings.save_image:
            for path in delivery.written:
                event.track_temporary_local_file(str(path))

        if req.forward and qq.is_qq(event):
            chains = qq.forward_chains(event, chains)
        for chain in chains:
            yield event.chain_result(chain)

    async def dm(self, event: AstrMessageEvent, chain: list) -> bool:
        """Send ``chain`` to the requesting user privately."""
        umo = f"{event.get_platform_id()}:FriendMessage:{event.get_sender_id()}"
        try:
            return bool(await self.context.send_message(umo, MessageChain(chain=chain)))
        except Exception as e:
            logger.warning("Private message to %s failed: %s", event.get_sender_id(), e)
            return False

    async def text_audit_llm(self, system_prompt: str, text: str) -> str:
        """The AstrBot model used for prompt audit: the configured one, else the session's."""
        provider_id = self.text_provider or await self.context.get_current_chat_provider_id(CURRENT_UMO.get())
        resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=text, system_prompt=system_prompt)
        return resp.completion_text or ""

    async def load_usage(self) -> None:
        try:
            self.limiter.restore(await self.get_kv_data(USAGE_KEY, None))
        except Exception as e:  # no KV store (tests, very old AstrBot)
            logger.debug("Daily usage not restored: %s", e)

    async def save_usage(self) -> None:
        try:
            await self.put_kv_data(USAGE_KEY, self.limiter.snapshot())
        except Exception as e:
            logger.debug("Daily usage not saved: %s", e)

    # ---------------------------------------------------------------- helpers

    def parse(self, body: str, workflow: str | None):
        """Parse the message body for ``workflow`` (``None`` = default).

        A ``-wf`` flag in the body wins over the command's own workflow, and
        the parse uses that workflow's ``reg_args``.
        """
        default_wf = workflow or self.settings.default_workflow
        tokens = tokenize(body)
        resolved = self.store.resolve(workflow_flag(tokens) or default_wf)
        reflex = self.store.load_reflex(resolved)
        args = build_parser(resolved, reflex.get("reg_args")).parse_args(tokens)
        args.work_flows = resolved
        return resolved, args, reflex

    def can_manage(self, event: AstrMessageEvent) -> bool:
        return event.is_admin() or event.get_sender_id() in self.settings.superusers

    # ------------------------------------------------------- admin commands

    @filter.command_group("comfy")
    def comfy(self):
        """Yet Another ComfyUI 管理命令"""

    @comfy.command("reload")
    async def comfy_reload(self, event: AstrMessageEvent):
        """重新扫描工作流目录并刷新工作流命令（管理员）"""
        if not self.can_manage(event):
            yield event.plain_result("只有管理员可以重新加载工作流")
            return
        self.refresh_commands()
        names = self.store.list_workflows()
        yield event.plain_result(
            f"已重新加载 {len(names)} 个工作流: {', '.join(names) or '无'}\n命令: {', '.join(COMMANDS.words())}"
        )


WORKFLOW_FLAGS = ("-wf", "-工作流")


def workflow_flag(tokens: list[str]) -> str | None:
    """The value of the last ``-wf``/``-工作流`` flag, read without argparse.

    Read before the parser exists, because the parser depends on which
    workflow runs (its ``reg_args``) and a full parse with the wrong one
    misreads that workflow's own flags.
    """
    found = None
    for i, tok in enumerate(tokens):
        if tok in WORKFLOW_FLAGS and i + 1 < len(tokens):
            found = tokens[i + 1]
        else:
            for flag in WORKFLOW_FLAGS:
                if tok.startswith(flag + "="):
                    found = tok[len(flag) + 1:]
    return found
