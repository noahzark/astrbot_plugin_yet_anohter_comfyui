"""Yet Another ComfyUI: the AstrBot port of nonebot-plugin-comfyui.

Workflows are the same ``<name>.json`` + ``<name>_reflex.json`` pairs the
nonebot plugin used; the engine that applies them lives in ``comfyui_core``
and this module only speaks AstrBot.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import shlex
from pathlib import Path
from typing import Any, Awaitable, Callable

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .adapter.cards import TEMPLATE_DIR, card
from .adapter.commands import COMMANDS, EXTRA_KEY, GenerateCommandFilter
from .adapter.config import build_settings, cards_enabled, llm_settings, text_audit_provider, update_check_enabled
from .adapter.media import collect_input_images
from .adapter import qq
from .adapter.render import Delivery, write_media
from .adapter.workflows import install_builtin_workflows
from .comfyui_core import __version__ as CORE_VERSION
from .comfyui_core import info
from .comfyui_core.amusement import (
    danbooru_image,
    danbooru_samples,
    danbooru_tags,
    llm_tag_system_prompt,
    pick_llm_preset,
    today_girl,
)
from .comfyui_core.cli import build_api_parser, build_parser, build_queue_parser, tokenize
from .comfyui_core.client import ComfyClient
from .comfyui_core.exceptions import ArgsError, ComfyuiExceptions
from .comfyui_core.audit import ImageAuditor, TextAuditor, clean_llm_response
from .comfyui_core.importer import import_nonebot_config, merge_into
from .comfyui_core.limiter import RateLimiter
from .comfyui_core.pipeline import GenerationPipeline, build_request
from .comfyui_core.screenshot import workflow_screenshot
from .comfyui_core.tasks import TaskRecords
from .comfyui_core.updates import check_update
from .comfyui_core.workflow import WorkflowStore, validate_permissions

PLUGIN_NAME = "astrbot_plugin_yet_anohter_comfyui"
USAGE_KEY = "daily_usage"
TASKS_KEY = "user_tasks"


def _read_version() -> str:
    try:
        text = (Path(__file__).parent / "metadata.yaml").read_text(encoding="utf-8")
        match = re.search(r"^version:\s*v?([\w.\-]+)", text, re.M)
        return match.group(1) if match else CORE_VERSION
    except OSError:
        return CORE_VERSION


PLUGIN_VERSION = _read_version()
LLM_FLAGS = ("-llm", "-llm预设")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".mkv", ".gif")

# The session a generation runs for, so the text-audit LLM can use its model.
CURRENT_UMO: contextvars.ContextVar[str] = contextvars.ContextVar("yac_umo", default="")

# Route the engine's stdlib logging into AstrBot's logger.
logging.getLogger("comfyui_core").setLevel(logging.INFO)


class YetAnotherComfyUI(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        self.data_dir = self.resolve_data_dir()
        self.tasks = TaskRecords()
        self.update_notice = ""
        self._background: set[asyncio.Task] = set()
        self.configure()

    def configure(self) -> None:
        """(Re)build everything that depends on the config."""
        self.settings = build_settings(self.config, self.data_dir)
        self.store = WorkflowStore(self.settings.workflows_dir)
        self.client = ComfyClient(self.settings.backend_urls, self.settings.timeout, self.settings.http_proxy)
        self.text_provider = text_audit_provider(self.config)
        self.llm = llm_settings(self.config)
        self.cards = cards_enabled(self.config)
        self.image_auditor = ImageAuditor(self.settings, self.client)
        self.pipeline = GenerationPipeline(
            self.client,
            self.store,
            self.settings,
            image_auditor=self.image_auditor,
            text_auditor=TextAuditor(self.settings, self.client, llm=self.text_audit_llm),
        )
        old = getattr(self, "limiter", None)
        self.limiter = RateLimiter(self.settings.cd, self.settings.day_limit)
        if old is not None:
            self.limiter.restore(old.snapshot())

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
        await self.load_tasks()
        if update_check_enabled(self.config):
            self.spawn(self.check_updates())
        logger.info(
            "Yet Another ComfyUI %s ready, backends: %s", PLUGIN_VERSION, ", ".join(self.settings.backend_urls)
        )

    async def terminate(self) -> None:
        qq.cancel_pending()
        for task in list(self._background):
            task.cancel()
        await self.client.close()

    def spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def check_updates(self) -> None:
        notice = await check_update(self.client, PLUGIN_VERSION)
        if notice:
            self.update_notice = notice
            logger.info(notice)

    # --------------------------------------------------------------- commands

    @filter.custom_filter(GenerateCommandFilter)
    async def generate(self, event: AstrMessageEvent):
        """prompt / 工作流命令: 用 ComfyUI 生成图片。加 -h 查看全部参数。"""
        word = event.get_extra(EXTRA_KEY) or ""
        try:
            async for result in self.run_generation(event, COMMANDS.strip(word, event.message_str), COMMANDS.workflow_for(word)):
                yield result
        finally:
            # Only after the last yield: AstrBot drops results of an already stopped event.
            event.stop_event()

    async def run_generation(
        self,
        event: AstrMessageEvent,
        body: str,
        workflow: str | None,
        adjust: Callable[[Any], Awaitable[None]] | None = None,
        temp_files: list[Path] | None = None,
    ):
        """Parse ``body`` for ``workflow`` and generate.

        ``adjust`` may rewrite the parsed arguments (二次元的我, llm-tag); it
        runs after the cooldown check, so a user in cooldown costs nothing.
        Throwaway output files are tracked on the event, or collected in
        ``temp_files`` when the caller outlives it (the LLM tool).
        """
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        is_admin = event.is_admin()
        is_superuser = is_admin or user_id in self.settings.superusers

        if self.settings.group_value("enable_in_group", group_id, 1) in (0, False):
            yield event.plain_result("本群已禁用 ComfyUI 生图插件")
            return

        try:
            workflow, args, reflex = self.parse(body, workflow)
        except ComfyuiExceptions as e:
            yield event.plain_result(str(e))
            return

        in_cd, remain = self.limiter.check_cd(user_id, is_superuser)
        if in_cd:
            text = f"您冲得太快啦，请休息一下，剩余冷却时间为: {remain}s"
            if not await qq.send_text_and_recall(event, text, qq.RECALL_COOLDOWN):
                yield event.plain_result(text)
            return

        try:
            if adjust is not None:
                await adjust(args)
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
            await self.say(event, text, qq.RECALL_NOTICE)

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

        for result in outcome.results:
            self.tasks.record(user_id, result.task_id, workflow, result.backend_index)
        await self.save_tasks()

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
                if temp_files is not None:
                    temp_files.append(path)
                else:
                    event.track_temporary_local_file(str(path))

        if req.forward and qq.is_qq(event):
            chains = qq.forward_chains(event, chains)
        for chain in chains:
            yield event.chain_result(chain)

    async def say(self, event: AstrMessageEvent, text: str, recall_after: float) -> None:
        """A transient notice: recalled later on QQ, a plain message elsewhere."""
        if not await qq.send_text_and_recall(event, text, recall_after):
            await event.send(MessageChain().message(text))

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

    async def load_tasks(self) -> None:
        try:
            self.tasks.restore(await self.get_kv_data(TASKS_KEY, None))
        except Exception as e:
            logger.debug("Task history not restored: %s", e)

    async def save_tasks(self) -> None:
        try:
            await self.put_kv_data(TASKS_KEY, self.tasks.snapshot())
        except Exception as e:
            logger.debug("Task history not saved: %s", e)

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

    def backend_for(self, value: str | None, event: AstrMessageEvent) -> tuple[int | None, str]:
        """Resolve ``-be``: an index for anyone, a URL outside the list for admins only."""
        value = str(value if value is not None else "0").strip() or "0"
        urls = self.settings.backend_urls
        if value.isdigit():
            idx = int(value)
            if 0 <= idx < len(urls):
                return idx, urls[idx]
            raise ArgsError(f"没有后端 {idx}，可用的后端索引为 0-{len(urls) - 1}")
        url = value.rstrip("/")
        if url in urls:
            return urls.index(url), url
        if url.startswith(("http://", "https://")) and self.can_manage(event):
            return None, url
        raise ArgsError("只有管理员可以指定后端地址，请使用后端索引")

    def render(self):
        return (lambda tmpl, data: self.html_render(tmpl, data)) if self.cards else None

    # ----------------------------------------------------------- info cards

    @filter.command("comfyui帮助", alias={"comfy帮助", "comfyhelp"})
    async def help(self, event: AstrMessageEvent):
        """查看 ComfyUI 插件的帮助"""
        reg = []
        for words, wf in COMMANDS.workflow_commands():
            try:
                reg.append((words, str(self.store.load_reflex(wf).get("note") or "")))
            except ComfyuiExceptions:
                reg.append((words, ""))
        data = info.help_data(self.settings, reg, PLUGIN_VERSION, [self.update_notice] if self.update_notice else [])
        yield event.chain_result(await card(self.render(), "help", data, info.help_text(data)))
        if self.cards:
            try:
                guide = await self.text_to_image((TEMPLATE_DIR / "guide.md").read_text(encoding="utf-8"))
                yield event.chain_result([Comp.Plain("基础使用教程\n"), Comp.Image.fromURL(guide)])
            except Exception as e:
                logger.debug("Usage guide not rendered: %s", e)

    @filter.command("查看工作流")
    async def list_workflows(self, event: AstrMessageEvent):
        """查看可用的工作流，可加名称或序号筛选"""
        search = command_body(event) or None
        rows = info.workflow_rows(self.store, search)
        if not rows:
            yield event.plain_result(f"没有找到工作流{': ' + search if search else ''}")
            return
        yield event.chain_result(await card(self.render(), "workflows", {"rows": rows}, info.workflows_text(rows)))
        if len(rows) == 1 and self.settings.backend_urls:
            available = self.store.load_reflex(rows[0]["name"]).get("available") or [0]
            idx = available[0] if isinstance(available[0], int) and available[0] < len(self.settings.backend_urls) else 0
            shot = await workflow_screenshot(self.settings.backend_urls[idx], self.store.dir / f"{rows[0]['name']}.json")
            if shot:
                path = write_media(shot, "image", data_dir=self.data_dir, user_id="", persistent=False)
                event.track_temporary_local_file(str(path))
                yield event.chain_result([Comp.Plain("工作流预览:\n"), Comp.Image.fromFileSystem(str(path))])

    @filter.command("comfyui后端", alias={"后端"})
    async def backends(self, event: AstrMessageEvent):
        """查看 ComfyUI 后端状态"""
        nodes = await info.backend_status(self.client, self.settings.timeout)
        yield event.chain_result(await card(self.render(), "backends", {"nodes": nodes}, info.backends_text(nodes)))

    # -------------------------------------------------------- backend tools

    @filter.command("queue")
    async def queue(self, event: AstrMessageEvent):
        """查看和管理后端队列，queue -h 查看参数"""
        try:
            args = build_queue_parser().parse_args(tokenize(command_body(event)))
            if (args.stop or args.clear or args.delete) and not self.can_manage(event):
                raise ArgsError("只有管理员可以停止、删除或清空任务")
            _, url = self.backend_for(args.backend, event)
        except ComfyuiExceptions as e:
            yield event.plain_result(str(e))
            return
        text, files = await info.queue_report(self.client, url, args)
        chain: list[Any] = [Comp.Plain(text)]
        extra: list[list[Any]] = []
        flagged = 0
        for i, meta in enumerate(files[:10]):
            try:
                data = await self.client.download(url, meta)
            except ComfyuiExceptions as e:
                chain.append(Comp.Plain(f"\n{meta.get('filename')}: {e}"))
                continue
            name = str(meta.get("filename", "")).lower()
            if name.endswith(IMAGE_SUFFIXES):
                if (await self.image_auditor.audit(data, event.get_group_id()))["is_nsfw"]:
                    flagged += 1
                    continue
                path = write_media(data, "image", data_dir=self.data_dir, user_id="", persistent=False, index=i)
                chain.append(Comp.Image.fromFileSystem(str(path)))
            elif name.endswith(VIDEO_SUFFIXES):
                path = write_media(data, "video", data_dir=self.data_dir, user_id="", persistent=False, index=i)
                extra.append([Comp.Video.fromFileSystem(str(path))])
            else:
                path = write_media(data, "audio", data_dir=self.data_dir, user_id="", persistent=False, index=i)
                extra.append([Comp.Record.fromFileSystem(str(path))])
            event.track_temporary_local_file(str(path))
        if flagged:
            chain.append(Comp.Plain(f"\n⚠️ {flagged} 张图片太涩了，已被过滤"))
        yield event.chain_result(chain)
        for c in extra:
            yield event.chain_result(c)

    @filter.command("capi")
    async def capi(self, event: AstrMessageEvent):
        """查询后端节点信息（管理员），capi -g 节点名"""
        if not self.can_manage(event):
            yield event.plain_result("只有管理员可以查询后端节点信息")
            return
        try:
            args = build_api_parser().parse_args(tokenize(command_body(event)))
            _, url = self.backend_for(args.backend, event)
            text = await info.node_info(self.client, url, args.get)
        except ComfyuiExceptions as e:
            text = f"获取节点信息失败: {e}"
        yield event.plain_result(text)

    @filter.command("get-ckpt")
    async def get_ckpt(self, event: AstrMessageEvent):
        """查看后端的模型列表，get-ckpt 后端索引"""
        async for r in self.model_list(event, "模型", self.client.get_checkpoints):
            yield r

    @filter.command("get-loras", alias={"get-lora"})
    async def get_loras(self, event: AstrMessageEvent):
        """查看后端的 LoRA 列表，get-loras 后端索引"""
        async for r in self.model_list(event, "LoRA", self.client.get_loras):
            yield r

    async def model_list(self, event: AstrMessageEvent, label: str, fetch: Callable[[str], Awaitable[list[str]]]):
        try:
            idx, url = self.backend_for((command_body(event).split() or ["0"])[0], event)
            names = await fetch(url)
        except ComfyuiExceptions as e:
            yield event.plain_result(f"获取{label}列表失败: {e}")
            return
        more = f"\n...（共 {len(names)} 个）" if len(names) > 40 else ""
        yield event.plain_result(f"后端 [{idx}] {label}列表:\n" + "\n".join(names[:40]) + more)

    @filter.command("get-task")
    async def get_task(self, event: AstrMessageEvent):
        """查看自己提交过的任务，get-task 0-10"""
        start, end = info.parse_range(command_body(event) or None)
        tasks = self.tasks.recent(event.get_sender_id(), start, end)
        if not tasks:
            yield event.plain_result("暂无历史任务记录")
            return
        lines = [f"您的历史任务 [{start}-{end}]:"]
        lines += [f"- {t['id']}\n  工作流: {t['workflow']}, 后端: [{t['backend']}], 状态: {t['status']}" for t in tasks]
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------ amusement

    @filter.command("二次元的", alias={"二次元的我"})
    async def today_girl_cmd(self, event: AstrMessageEvent):
        """随机拼凑提示词，生成二次元的我"""

        async def adjust(args: Any) -> None:
            prompt, description = today_girl()
            args.prompt = [prompt]
            args.silent = True
            await self.say(event, f"锵锵~~~{description}\n正在为你生成二次元图像捏", qq.RECALL_NOTICE)

        try:
            async for r in self.run_generation(event, command_body(event), None, adjust):
                yield r
        finally:
            event.stop_event()

    @filter.command("llm-tag")
    async def llm_tag(self, event: AstrMessageEvent):
        """用 LLM 扩写提示词再生成，llm-tag 描述 [-llm 预设]"""
        body = command_body(event)
        tokens = tokenize(body)
        explicit = any(t in LLM_FLAGS or t.startswith(tuple(f + "=" for f in LLM_FLAGS)) for t in tokens)

        async def adjust(args: Any) -> None:
            text = " ".join(args.prompt or []).strip()
            if not text:
                raise ArgsError("请在 llm-tag 后面写上想画的内容")
            index = args.llm_preset if explicit else int(self.settings.default_value.get("llm_preset") or 0)
            preset = pick_llm_preset(self.llm["presets"], int(index or 0))
            try:
                provider = (preset.get("provider") or self.llm["provider"]
                            or await self.context.get_current_chat_provider_id(event.unified_msg_origin))
                system = llm_tag_system_prompt(str(preset.get("system_prompt") or self.llm["system_prompt"]))
                resp = await self.context.llm_generate(chat_provider_id=provider, prompt=text, system_prompt=system)
                result = clean_llm_response(resp.completion_text or "")
            except Exception as e:
                logger.warning("llm-tag failed: %s", e)
                raise ComfyuiExceptions(f"LLM 扩写失败: {e}") from e
            if not result:
                raise ComfyuiExceptions("LLM 没有返回提示词")
            args.prompt = [result]
            args.silent = True
            await self.say(event, f"LLM 扩写结果:\n{result}", qq.RECALL_LIMIT)

        try:
            async for r in self.run_generation(event, body, None, adjust):
                yield r
        finally:
            event.stop_event()

    @filter.command("dan", alias={"查tag"})
    async def danbooru_cmd(self, event: AstrMessageEvent):
        """从 Danbooru 查询 tag，dan 标签 [数量]"""
        parts = command_body(event).split()
        if not parts:
            yield event.plain_result("用法: dan 标签 [数量]")
            return
        limit = min(int(parts[1]), 10) if len(parts) > 1 and parts[1].isdigit() else 3
        group_id = event.get_group_id()
        try:
            tags = await danbooru_tags(self.client, parts[0], limit)
        except ComfyuiExceptions as e:
            yield event.plain_result(f"查询 Danbooru 失败: {e}")
            return
        if not tags:
            yield event.plain_result(f"Danbooru 上没有找到 {parts[0]}")
            return
        auditor = self.pipeline.text_auditor
        if auditor is not None and auditor.mode_for(group_id) and await auditor.is_unsafe(", ".join(tags)):
            yield event.plain_result("查询到的标签含有违规内容，已拦截")
            return
        chain: list[Any] = []
        for i, tag in enumerate(tags):
            chain.append(Comp.Plain(("\n" if i else "") + f"({tag}:1)\n"))
            try:
                urls = await danbooru_samples(self.client, tag)
            except ComfyuiExceptions as e:
                logger.debug("Danbooru samples for %s failed: %s", tag, e)
                continue
            for j, url in enumerate(urls):
                try:
                    data = await danbooru_image(self.client, url)
                except ComfyuiExceptions as e:
                    logger.debug("Danbooru sample skipped: %s", e)
                    continue
                if (await self.image_auditor.audit(data, group_id))["is_nsfw"]:
                    chain.append(Comp.Plain("太涩了\n"))
                    continue
                path = write_media(data, "image", data_dir=self.data_dir, user_id="", persistent=False, index=j)
                event.track_temporary_local_file(str(path))
                chain.append(Comp.Image.fromFileSystem(str(path)))
        yield event.chain_result(chain)

    # ------------------------------------------------------------ LLM tools

    @filter.llm_tool(name="comfyui_draw")
    async def comfyui_draw(self, event: AstrMessageEvent, prompt: str, workflow: str = ""):
        """Draw a picture with ComfyUI and send it to the user when it is ready. Use when the user asks for an image, illustration or drawing. Images attached to the user's message are used as input by workflows that need one.

        Args:
            prompt(string): The image description as comma-separated English tags, for example "1girl, solo, cherry blossoms, masterpiece". Translate the request into English.
            workflow(string): Optional workflow name from comfyui_list_workflows. Leave empty for the default text-to-image workflow.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return "prompt is empty; describe the picture in English tags"
        if self.settings.group_value("enable_in_group", event.get_group_id(), 1) in (0, False):
            return "Drawing is disabled in this group; tell the user."
        name: str | None = None
        if workflow and workflow.strip():
            try:
                name = self.store.resolve(workflow.strip())
                validate_permissions(self.store.load_reflex(name), event.get_group_id(), self.can_manage(event))
            except ComfyuiExceptions as e:
                return f"workflow error: {e}. Call comfyui_list_workflows to see the available workflows."
        user_id = event.get_sender_id()
        in_cd, remain = self.limiter.check_cd(user_id, self.can_manage(event))
        if in_cd:
            return f"The user is in cooldown for {remain} more seconds; tell them to wait and do not retry now."
        self.spawn(self.draw_in_background(event, shlex.quote(prompt), name))
        return (
            f"Generation started with workflow {name or self.settings.default_workflow}. "
            "The picture will be sent to the user automatically when it is ready; tell them it is on the way "
            "and do not describe or invent the result."
        )

    async def draw_in_background(self, event: AstrMessageEvent, body: str, workflow: str | None) -> None:
        """Run a tool-started generation and send its messages to the conversation."""
        temp_files: list[Path] = []
        umo = event.unified_msg_origin
        try:
            async for result in self.run_generation(event, body, workflow, temp_files=temp_files):
                if result is not None and result.chain:
                    await self.context.send_message(umo, MessageChain(chain=list(result.chain)))
        except Exception:
            logger.exception("ComfyUI generation started by the LLM failed")
        finally:
            for path in temp_files:
                path.unlink(missing_ok=True)

    @filter.llm_tool(name="comfyui_list_workflows")
    async def comfyui_list_workflows(self, event: AstrMessageEvent):
        """List the ComfyUI workflows that comfyui_draw can use, with what each one does and whether it needs an input image."""
        rows = info.workflow_rows(self.store)
        return info.workflows_text(rows) + f"\n默认工作流: {self.settings.default_workflow}"

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

    @comfy.command("import-config")
    async def comfy_import_config(self, event: AstrMessageEvent):
        """导入 nonebot 版的 comfyui.yaml 配置（管理员）"""
        if not self.can_manage(event):
            yield event.plain_result("只有管理员可以导入配置")
            return
        raw = command_body(event)
        path = Path(raw.split(maxsplit=1)[1].strip().strip("\"'")) if len(raw.split(maxsplit=1)) > 1 else None
        if path is None:
            yield event.plain_result("用法: /comfy import-config /path/to/config/comfyui.yaml")
            return
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("文件内容不是 YAML 字典")
        except Exception as e:
            yield event.plain_result(f"读取 {path} 失败: {e}")
            return
        result = import_nonebot_config(data, path)
        merge_into(self.config, result)
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        await self.client.close()
        self.configure()
        self.refresh_commands()
        lines = [f"已导入 {sum(len(v) for v in result.config.values())} 项设置: {', '.join(result.config)}"]
        if result.unmapped:
            lines.append("无法识别、没有导入: " + ", ".join(result.unmapped))
        lines += result.notes
        yield event.plain_result("\n".join(lines))


def command_body(event: AstrMessageEvent) -> str:
    """The message text after the command word."""
    parts = event.message_str.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


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
