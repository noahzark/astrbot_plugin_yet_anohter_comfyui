"""Errors AstrBot must see, the cooldown under concurrent requests, and reloads mid-generation."""
import asyncio
import json
import shutil
from pathlib import Path

import pytest
from astrbot.core.message.components import Image, Plain

from plugin_env import RecordingEvent, drive, plugin_main


def texts(results):
    return " ".join(c.text for chain in results for c in chain if isinstance(c, Plain))


@pytest.fixture
def kv(monkeypatch):
    store = {}

    async def put(self, key, value):
        store[key] = json.loads(json.dumps(value))

    async def get(self, key, default):
        return store.get(key, default)

    monkeypatch.setattr(plugin_main.YetAnotherComfyUI, "put_kv_data", put, raising=False)
    monkeypatch.setattr(plugin_main.YetAnotherComfyUI, "get_kv_data", get, raising=False)
    return store


async def test_unexpected_errors_reach_astrbot(server, plugin_factory):
    plugin = await plugin_factory(server, limits={"cd": 0})
    wf_dir = Path(plugin.settings.workflows_dir)
    shutil.copy(wf_dir / "txt2img.json", wf_dir / "broken.json")
    (wf_dir / "broken_reflex.json").write_text("{not json", encoding="utf-8")

    event = RecordingEvent("prompt cat -wf broken")
    with pytest.raises(json.JSONDecodeError):
        await drive(plugin, event)
    # AstrBot only tells the user about a handler's exception while the event is not stopped.
    assert not event.is_stopped()
    await plugin.terminate()


async def test_crash_after_generation_refunds_quota(server, plugin_factory, monkeypatch):
    plugin = await plugin_factory(server, limits={"cd": 0})

    async def broken_build(self, req, outcome):
        raise OSError("disk full")

    monkeypatch.setattr(plugin_main.Delivery, "build", broken_build)
    results = await drive(plugin, RecordingEvent("绘画 cat", user_id="u1"))
    assert "disk full" in texts(results)
    assert plugin.limiter.daily_records[plugin.limiter.get_today_key("u1")] == 0
    await plugin.terminate()


async def test_requests_sent_together_share_one_cooldown(server, plugin_factory):
    plugin = await plugin_factory(server, limits={"cd": 60})

    async def slow(args):  # like llm-tag waiting for the model
        await asyncio.sleep(0.05)

    async def request():
        out = []
        async for r in plugin.run_generation(RecordingEvent("prompt cat"), "cat", None, slow):
            if r is not None:
                out.append(list(r.chain))
        return out

    first, second = await asyncio.gather(request(), request())
    assert texts(first + second).count("冷却") == 1
    assert len(server.prompts) == 1
    await plugin.terminate()


async def test_rejected_request_gives_the_cooldown_back(server, plugin_factory):
    plugin = await plugin_factory(server, limits={"cd": 60, "day_limit": 1})
    assert "调用受限" in texts(await drive(plugin, RecordingEvent("绘画 cat -b 2")))
    results = await drive(plugin, RecordingEvent("绘画 cat"))
    assert any(isinstance(c, Image) for chain in results for c in chain), texts(results)
    await plugin.terminate()


async def test_terminate_refunds_a_cancelled_llm_generation(server, plugin_factory, monkeypatch, kv):
    plugin = await plugin_factory(server, limits={"cd": 0})
    started = asyncio.Event()

    async def hang(req, notify=None):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(plugin.pipeline, "run", hang)
    await plugin.comfyui_draw(RecordingEvent("画一张图", user_id="u1"), prompt="1girl")
    await asyncio.wait_for(started.wait(), 2)
    key = plugin.limiter.get_today_key("u1")
    assert kv["daily_usage"]["daily"][key] == 1

    await plugin.terminate()
    assert kv["daily_usage"]["daily"][key] == 0

    # Work that outlives terminate() must not overwrite what the next instance saves.
    kv.clear()
    await plugin.save_usage()
    assert kv == {}
