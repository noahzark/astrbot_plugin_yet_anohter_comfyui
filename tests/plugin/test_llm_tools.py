"""The LLM function tools: comfyui_draw queues a run and delivers it later."""
import asyncio

import pytest
from astrbot.core.message.components import Image
from astrbot.core.provider.register import llm_tools

from plugin_env import RecordingEvent


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, list(chain.chain)))
        return True


async def settle(plugin):
    while plugin._background:
        await asyncio.gather(*list(plugin._background))


@pytest.fixture
async def make(server, plugin_factory):
    made = []

    async def factory(**overrides):
        overrides.setdefault("limits", {"cd": 0})
        p = await plugin_factory(server, **overrides)
        p.context = FakeContext()
        made.append(p)
        return p

    yield factory
    for p in made:
        await p.terminate()


def test_tools_are_registered_with_parameters():
    draw = llm_tools.get_func("comfyui_draw")
    assert draw is not None
    props = draw.parameters["properties"]
    assert set(props) == {"prompt", "workflow"} and props["prompt"]["type"] == "string"
    assert "English" in props["prompt"]["description"]
    assert llm_tools.get_func("comfyui_list_workflows") is not None


async def test_draw_returns_at_once_and_delivers_later(make, server):
    plugin = await make()
    event = RecordingEvent("画一个海边的女孩", user_id="u1")
    answer = await plugin.comfyui_draw(event, prompt='1girl, "beach", -wf x')
    assert "started" in answer and "txt2img" in answer

    await settle(plugin)
    sent = next(iter(server.prompts.values()))["prompt"]["6"]["inputs"]["text"]
    assert '1girl, "beach", -wf x' in sent  # the prompt is one argument, never parsed as flags
    umo, chain = plugin.context.sent[-1]
    assert umo == event.unified_msg_origin and any(isinstance(c, Image) for c in chain)
    assert not event.is_stopped()  # the LLM's own reply must still go out


async def test_draw_with_a_named_workflow(make, server):
    plugin = await make()
    answer = await plugin.comfyui_draw(RecordingEvent("x"), prompt="cat", workflow="txt2img")
    assert "txt2img" in answer
    await settle(plugin)
    assert server.prompts


async def test_draw_rejections_go_back_to_the_llm(make, server):
    plugin = await make()
    assert "empty" in await plugin.comfyui_draw(RecordingEvent("x"), prompt="  ")
    answer = await plugin.comfyui_draw(RecordingEvent("x"), prompt="cat", workflow="nope")
    assert "comfyui_list_workflows" in answer
    await settle(plugin)
    assert not server.prompts and not plugin.context.sent


async def test_draw_respects_group_switch(make, server):
    groups = {"overrides": [
        {"__template_key": "group", "group_id": "1", "enabled": False, "audit_level": -1,
         "reject_nsfw_prompts": -1, "img_send": -1, "pure": False},
    ]}
    plugin = await make(groups=groups)
    answer = await plugin.comfyui_draw(RecordingEvent("x", group_id="1"), prompt="cat")
    assert "disabled" in answer
    await settle(plugin)
    assert not server.prompts


async def test_draw_reports_cooldown(make, server):
    plugin = await make(limits={"cd": 60})
    await plugin.comfyui_draw(RecordingEvent("x", user_id="u9"), prompt="cat")
    await settle(plugin)
    answer = await plugin.comfyui_draw(RecordingEvent("x", user_id="u9"), prompt="cat")
    assert "cooldown" in answer and len(server.prompts) == 1


async def test_failed_background_run_is_logged_not_raised(make, server, monkeypatch):
    plugin = await make()

    async def boom(*args, **kwargs):
        raise RuntimeError("backend exploded")
        yield  # pragma: no cover

    monkeypatch.setattr(plugin, "run_generation", boom)
    await plugin.comfyui_draw(RecordingEvent("x"), prompt="cat")
    await settle(plugin)
    assert not plugin.context.sent


async def test_list_workflows(make):
    plugin = await make()
    text = await plugin.comfyui_list_workflows(RecordingEvent("x"))
    assert "txt2img" in text and "默认工作流: txt2img" in text
