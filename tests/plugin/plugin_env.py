"""Helpers for the plugin-level tests: import main.py as AstrBot would and
build events that record what the plugin sends."""
import importlib
import json
import sys
from pathlib import Path

import pytest

astrbot = pytest.importorskip("astrbot")

ROOT = Path(__file__).resolve().parents[2]
CORE_TESTS = ROOT / "tests" / "core"
for p in (str(ROOT.parent), str(CORE_TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

assert ROOT.name.isidentifier(), f"checkout dir {ROOT.name!r} must be importable as a package"
plugin_main = importlib.import_module(f"{ROOT.name}.main")
plugin_commands = importlib.import_module(f"{ROOT.name}.adapter.commands")

from astrbot.core.config.astrbot_config import AstrBotConfig  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402

from fake_comfy import FakeComfy  # noqa: E402


class RecordingEvent(AstrMessageEvent):
    """A minimal concrete event that records what the plugin sends."""

    def __init__(self, text: str, *, user_id: str = "10001", group_id: str = "", admin: bool = False):
        msg = AstrBotMessage()
        msg.type = MessageType.GROUP_MESSAGE if group_id else MessageType.FRIEND_MESSAGE
        msg.self_id = "bot"
        msg.session_id = group_id or user_id
        msg.message_id = "msg-1"
        msg.group_id = group_id
        msg.sender = MessageMember(user_id=user_id, nickname="tester")
        msg.message = [Plain(text)]
        msg.message_str = text
        msg.raw_message = {}
        meta = PlatformMetadata(name="aiocqhttp", description="test", id="qq")
        super().__init__(text, msg, meta, msg.session_id)
        self.is_at_or_wake_command = True  # the wake stage already ran
        if admin:
            self.role = "admin"
        self.sent: list[MessageChain] = []

    async def send(self, message: MessageChain):
        self.sent.append(message)


async def drive(plugin, event) -> list:
    """Run the generate handler like AstrBot would and return every result chain."""
    assert plugin_commands.GenerateCommandFilter().filter(event, {}), "command word not recognised"
    results = []
    async for res in plugin.generate(event):
        if res is not None:
            results.append(list(res.chain))
    return results


