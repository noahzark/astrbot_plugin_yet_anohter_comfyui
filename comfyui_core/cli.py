"""The generation command line: argparse parsers shared by every adapter.

Chat platforms hand us one string. ``tokenize`` splits it shell-style and
``build_parser`` gives an argparse parser that never exits the process:
any usage error (or ``-h``) surfaces as :class:`ArgsError` whose message is
what the user should read.
"""
from __future__ import annotations

import argparse
import io
import logging
import shlex
from typing import Any

from .exceptions import ArgsError

logger = logging.getLogger(__name__)

TYPE_MAPPING: dict[str, type] = {
    "int": int,
    "str": str,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
}


class ArgumentParser(argparse.ArgumentParser):
    """argparse parser that raises :class:`ArgsError` instead of exiting."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._messages = io.StringIO()

    def _print_message(self, message: str, file: Any = None) -> None:  # type: ignore[override]
        if message:
            self._messages.write(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        if message:
            self._messages.write(message)
        text = self._messages.getvalue().strip()
        self._messages = io.StringIO()
        raise ArgsError(text or "参数错误")

    def error(self, message: str) -> None:  # type: ignore[override]
        self._messages = io.StringIO()
        raise ArgsError(f"参数错误: {message}")


def tokenize(text: str) -> list[str]:
    """Split a chat message into argv, keeping quoted phrases together."""
    text = text.strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        # Unbalanced quotes: fall back to plain whitespace splitting.
        return text.split()


def add_base_arguments(parser: argparse.ArgumentParser, default_wf: str) -> None:
    """Register the generation flags. ``default_wf`` is the ``-wf`` default."""
    parser.add_argument("prompt", nargs="*", help="正面提示词 example:prompt 1girl", type=str)
    parser.add_argument("-u", "-负面", nargs="*", dest="negative_prompt", type=str, help="负面提示词 example:prompt -u '低质量'")
    parser.add_argument("-ar", "-画面比例", dest="accept_ratio", type=str, help="画幅比例 example:prompt -ar 16:9")
    parser.add_argument("-s", "-种子", dest="seed", type=int, help="种子 example:prompt -s 200224")
    parser.add_argument("-t", "-步数", dest="steps", type=int, help="迭代步数 example:prompt -t 28")
    parser.add_argument("-cfg", dest="cfg_scale", type=float, help="CFG scale example:prompt -cfg 7.5")
    parser.add_argument("-n", "-去噪", dest="denoise_strength", type=float, help="降噪强度 example:prompt -n 1.0")
    parser.add_argument("-高", "--height", dest="height", type=int, help="图片的高度 example:prompt -高1216")
    parser.add_argument("-宽", "--width", dest="width", type=int, help="图片的宽度 example:prompt -宽832")
    parser.add_argument("-o", dest="override", action="store_true", help="不使用预设的正面提示词 example:prompt -o")
    parser.add_argument("-on", dest="override_ng", action="store_true", help="不使用预设的负面提示词 example:prompt -on")
    parser.add_argument("-wf", "-工作流", dest="work_flows", type=str, help="选择工作流 example:prompt -wf 1 / prompt -wf flux", default=default_wf)
    parser.add_argument("-sp", "-采样器", dest="sampler", type=str, help="采样器 example:prompt -sp euler")
    parser.add_argument("-sch", "-调度器", dest="scheduler", type=str, help="调度器 example:prompt -sch normal")
    parser.add_argument("-b", "-数量", dest="batch_size", type=int, help="每批数量 example:prompt -b 1", default=1)
    parser.add_argument("-bc", "-批数", dest="batch_count", type=int, help="批数 example:prompt -bc 1", default=1)
    parser.add_argument("-m", "-模型", dest="model", type=str, help="模型 example:prompt -m sdbase.ckpt")
    parser.add_argument("-be", "-后端", dest="backend", type=str, help="后端索引或者url example:prompt -be 0 / prompt -be 'http://127.0.0.1:8388'")
    parser.add_argument("-f", "-转发", dest="forward", action="store_true", help="使用转发消息 example:prompt -f")
    parser.add_argument("-gif", dest="gif", action="store_true", help="使用gif图片进行图片输入 example:prompt -gif")
    parser.add_argument("-con", "-并发", dest="concurrency", action="store_true", help="并发使用多后端生图,和-bc一起使用 example:prompt -con -bc 3")
    parser.add_argument("-r", "-shape", "-分辨率", dest="shape", type=str, help="自定义分辨率的比例字符串 example:prompt -r p / prompt -r 960x1920")
    parser.add_argument("-sil", "-静默", dest="silent", action="store_true", help="不返回各种提示消息 example:prompt -sil")
    parser.add_argument("-notice", "-通知", dest="notice", action="store_true", help="工作流执行完成的时候私聊通知, 适用于长工作流 example:prompt -notice")
    parser.add_argument("-nt", "-不翻译", dest="no_trans", action="store_true", help="不翻译中文输入 example:prompt -nt")
    parser.add_argument("-llm预设", "-llm", dest="llm_preset", type=int, help="通过llm处理prompt时使用的提示词预设 example:prompt -llm 1", default=1)


def build_queue_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="queue")
    parser.add_argument("--track", "-t", "-追踪", "--track_task", dest="track", action="store_true", help="后端当前的任务")
    parser.add_argument("-d", "--delete", dest="delete", type=str, help="从队列中清除指定的任务")
    parser.add_argument("-c", "--clear", "-clear", dest="clear", action="store_true", help="清除后端上的所有任务")
    parser.add_argument("-stop", "--stop", dest="stop", action="store_true", help="停止当前生成")
    parser.add_argument("-be", "--后端", dest="backend", type=str, help="后端索引或者url", default="0")
    parser.add_argument("-i", "--id", dest="task_id", type=str, help="需要查询的任务id")
    parser.add_argument("-v", "--view", dest="view", action="store_true", help="查看历史任务")
    parser.add_argument("-g", "--get", "-get", dest="get_task", type=str, help="需要获取具体信息的任务")
    parser.add_argument("-index", "--index", dest="index", type=str, help="需要获取的任务id范围", default="0-10")
    return parser


def build_api_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="capi")
    parser.add_argument("-g", "--get", "-get", dest="get", type=str, help="获取所有节点", default="all")
    parser.add_argument("-be", "--backend", dest="backend", type=str, help="后端索引或者url", default="0")
    return parser


def build_parser(default_wf: str, reg_args: dict[str, Any] | None = None) -> ArgumentParser:
    """Base generation parser plus the ``reg_args`` of one workflow.

    A registered flag that collides with a base flag replaces it, exactly
    as the nonebot plugin did.
    """
    parser = ArgumentParser(prog=default_wf)
    add_base_arguments(parser, default_wf)

    if not reg_args:
        return parser

    for node_arg in reg_args.values():
        for arg in node_arg.get("args", []):
            arg_copy = dict(arg)
            raw_type = arg_copy.get("type")
            if raw_type in TYPE_MAPPING:
                arg_copy["type"] = TYPE_MAPPING[raw_type]

            flags = arg_copy.pop("name_or_flags", [])
            arg_copy.pop("dest_to_value", None)

            if "preset" in arg_copy:
                arg_copy["type"] = str
                arg_copy.pop("preset", None)

            try:
                parser.add_argument(*flags, **arg_copy)
            except argparse.ArgumentError as e:
                logger.warning("reg_args flag conflicts with a base flag, replacing it: %s", e)
                for flag in flags:
                    if flag.startswith("-") and flag in parser._option_string_actions:
                        parser._remove_action(parser._option_string_actions.pop(flag))
                parser.add_argument(*flags, **arg_copy)

    return parser
