"""Milestone 4: limits that survive restarts, audit outcomes, group overrides, delivery modes."""
import asyncio
import importlib

import pytest
from astrbot.core.message.components import Image, Node, Nodes, Plain

from plugin_env import ROOT, RecordingEvent, drive, plugin_main

qq = importlib.import_module(f"{ROOT.name}.adapter.qq")


class FakeQQBot:
    """Records OneBot v11 calls; ``get_msg`` returns a CDN link for anything sent."""

    def __init__(self):
        self.calls = []
        self.api = self

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if action in ("send_group_msg", "send_private_msg"):
            return {"message_id": 100 + len(self.calls)}
        if action == "get_msg":
            return {"message": [{"type": "image", "data": {"url": "https://multimedia.nt.qq.com.cn/x.png"}}]}
        return {}

    def actions(self, name):
        return [p for a, p in self.calls if a == name]


class FakeContext:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain.chain))
        return self.ok


def qq_event(text, **kw):
    event = RecordingEvent(text, **kw)
    event.message_obj.self_id = "42"  # QQ ids are numbers
    event.bot = FakeQQBot()
    return event


def flat(results):
    return [c for chain in results for c in chain]


def texts(results):
    return " ".join(c.text for c in flat(results) if isinstance(c, Plain))


@pytest.fixture
def ctx():
    return FakeContext()


async def make(plugin_factory, server, ctx=None, **overrides):
    overrides.setdefault("limits", {"cd": 0})
    plugin = await plugin_factory(server, **overrides)
    plugin.context = ctx
    return plugin


async def test_flagged_image_is_sent_privately(server, plugin_factory, ctx):
    server.rating = "explicit"
    plugin = await make(plugin_factory, server, ctx)
    results = await drive(plugin, RecordingEvent("绘画 cat", group_id="777"))
    assert "私聊发送" in texts(results)
    assert not any(isinstance(c, Image) for c in flat(results))
    umo, chain = ctx.sent[0]
    assert umo.endswith(":FriendMessage:10001") and isinstance(chain[0], Image)
    await plugin.terminate()


@pytest.mark.parametrize("action", [0, 2, 3])
async def test_flagged_image_is_filtered_without_qq(server, plugin_factory, ctx, action):
    server.rating = "explicit"
    plugin = await make(plugin_factory, server, ctx, delivery={"r18_action": action})
    results = await drive(plugin, RecordingEvent("绘画 cat"))
    assert "过滤拦截" in texts(results)
    assert not ctx.sent and not any(isinstance(c, Image) for c in flat(results))
    await plugin.terminate()


async def test_failed_dm_falls_back_to_filtering(server, plugin_factory):
    server.rating = "explicit"
    plugin = await make(plugin_factory, server, FakeContext(ok=False))
    results = await drive(plugin, RecordingEvent("绘画 cat"))
    assert "过滤拦截" in texts(results)
    await plugin.terminate()


async def test_audit_failure_follows_fail_closed(server, plugin_factory, ctx):
    server.rating = "error"
    plugin = await make(plugin_factory, server, ctx, delivery={"r18_action": 0})
    assert "过滤拦截" in texts(await drive(plugin, RecordingEvent("绘画 cat")))
    await plugin.terminate()

    plugin = await make(plugin_factory, server, ctx, audit={"fail_closed": False})
    assert any(isinstance(c, Image) for c in flat(await drive(plugin, RecordingEvent("绘画 cat"))))
    await plugin.terminate()


async def test_link_modes_on_qq(server, plugin_factory, ctx):
    plugin = await make(plugin_factory, server, ctx, delivery={"img_send": 3})
    event = qq_event("绘画 cat", group_id="777")
    results = await drive(plugin, event)
    assert "图片链接: https://gchat.qpic.cn/x.png" in texts(results)
    assert event.bot.actions("send_private_msg")[0]["user_id"] == 42  # uploaded by messaging the bot itself
    await plugin.terminate()

    server.rating = "explicit"
    plugin = await make(plugin_factory, server, ctx, delivery={"r18_action": 2})
    results = await drive(plugin, qq_event("绘画 cat", group_id="777"))
    images = [c for c in flat(results) if isinstance(c, Image)]
    assert len(images) == 1 and "qpic" not in texts(results)  # a QR code image, not the picture
    await plugin.terminate()


async def test_link_modes_fall_back_to_images_elsewhere(server, plugin_factory, ctx):
    plugin = await make(plugin_factory, server, ctx, delivery={"img_send": 2})
    assert any(isinstance(c, Image) for c in flat(await drive(plugin, RecordingEvent("绘画 cat"))))
    await plugin.terminate()


async def test_group_overrides(server, plugin_factory, ctx):
    groups = {"overrides": [
        {"__template_key": "group", "group_id": "1", "enabled": False, "audit_level": -1,
         "reject_nsfw_prompts": -1, "img_send": -1, "pure": False},
        {"__template_key": "group", "group_id": "2", "enabled": True, "audit_level": 0,
         "reject_nsfw_prompts": -1, "img_send": -1, "pure": True},
    ]}
    server.rating = "explicit"
    plugin = await make(plugin_factory, server, ctx, groups=groups)
    results = await drive(plugin, RecordingEvent("绘画 cat", group_id="1"))
    assert "禁用" in texts(results) and not server.prompts

    results = await drive(plugin, RecordingEvent("绘画 cat", group_id="2"))
    assert "任务ID" not in texts(results)  # pure
    assert any(isinstance(c, Image) for c in flat(results))  # audit off for this group
    assert plugin.settings.group_value("audit_level_group", "1") is None  # -1 follows global
    await plugin.terminate()


async def test_notice_and_forward(server, plugin_factory, ctx):
    plugin = await make(plugin_factory, server, ctx)
    event = qq_event("绘画 cat -notice -f -b 2", group_id="777")
    results = await drive(plugin, event)
    assert "生图任务已完成" in ctx.sent[0][1][0].text
    assert len(results) == 1 and isinstance(results[0][0], Nodes)
    node = results[0][0].nodes[0]
    assert isinstance(node, Node) and sum(isinstance(c, Image) for c in node.content) == 2
    await plugin.terminate()


async def test_qq_notices_are_recalled(server, plugin_factory, ctx, monkeypatch):
    monkeypatch.setattr(qq, "RECALL_NOTICE", 0.01)
    monkeypatch.setattr(qq, "RECALL_COOLDOWN", 0.01)
    plugin = await make(plugin_factory, server, ctx, limits={"cd": 1000})
    event = qq_event("绘画 cat", group_id="777")
    await drive(plugin, event)
    sent = event.bot.actions("send_group_msg")
    assert "正在生成" in sent[0]["message"][-1]["data"]["text"] and not event.sent

    event = qq_event("绘画 cat", group_id="777")
    assert await drive(plugin, event) == []  # the cooldown reply went out through QQ, not as a result
    await asyncio.sleep(0.05)
    assert len(event.bot.actions("delete_msg")) == 1
    await plugin.terminate()


async def test_daily_usage_survives_restart(server, plugin_factory, monkeypatch):
    store = {}

    async def put(self, key, value):
        store[key] = value

    async def get(self, key, default):
        return store.get(key, default)

    monkeypatch.setattr(plugin_main.YetAnotherComfyUI, "put_kv_data", put, raising=False)
    monkeypatch.setattr(plugin_main.YetAnotherComfyUI, "get_kv_data", get, raising=False)

    plugin = await make(plugin_factory, server, limits={"cd": 0, "day_limit": 2})
    await drive(plugin, RecordingEvent("绘画 cat -b 2", user_id="u9"))
    await plugin.terminate()

    plugin = await make(plugin_factory, server, limits={"cd": 0, "day_limit": 2})
    assert "调用受限" in texts(await drive(plugin, RecordingEvent("绘画 cat", user_id="u9")))
    await plugin.terminate()


async def test_text_audit_blocks_prompt(server, plugin_factory, ctx):
    plugin = await make(plugin_factory, server, ctx, text_audit={"enabled": True})

    async def llm(system, msg):
        return "yes" if "bad" in msg else "no"

    plugin.pipeline.text_auditor.llm = llm
    assert "已拦截" in texts(await drive(plugin, RecordingEvent("绘画 bad thing")))
    assert not server.prompts
    assert any(isinstance(c, Image) for c in flat(await drive(plugin, RecordingEvent("绘画 cat"))))
    await plugin.terminate()
