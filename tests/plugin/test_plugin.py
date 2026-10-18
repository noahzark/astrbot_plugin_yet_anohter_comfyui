"""The milestone-2 deliverable: ``/绘画 1girl -ar 16:9 -s 1`` returns an image."""
from pathlib import Path

from astrbot.core.message.components import Image, Plain, Reply

from plugin_env import RecordingEvent, drive, plugin_commands
from fake_comfy import PNG_1X1


async def test_builtin_workflows_installed_and_commands_registered(server, plugin_factory):
    plugin = await plugin_factory(server)
    wf_dir = Path(plugin.settings.workflows_dir)
    assert (wf_dir / "txt2img.json").exists() and (wf_dir / "txt2img_reflex.json").exists()
    words = plugin_commands.COMMANDS.words()
    assert {"prompt", "comfyui执行", "绘画"} <= set(words)
    assert plugin_commands.COMMANDS.workflow_for("绘画") == "txt2img"
    assert plugin_commands.COMMANDS.workflow_for("prompt") is None
    await plugin.terminate()


async def test_draw_command_returns_image(server, plugin_factory):
    plugin = await plugin_factory(server)
    event = RecordingEvent("绘画 1girl -ar 16:9 -s 1", group_id="777")
    results = await drive(plugin, event)

    # "generating" notice went out through event.send
    assert len(event.sent) == 1 and "txt2img" in event.sent[0].chain[0].text

    assert len(results) == 1
    chain = results[0]
    assert isinstance(chain[0], Reply) and chain[0].id == "msg-1"
    assert isinstance(chain[1], Plain) and "任务ID" in chain[1].text
    images = [c for c in chain if isinstance(c, Image)]
    assert len(images) == 1
    saved = Path(images[0].file.replace("file:///", "/"))
    assert saved.read_bytes() == PNG_1X1
    assert "output/image" in saved.as_posix()

    sent = next(iter(server.prompts.values()))["prompt"]
    assert sent["6"]["inputs"]["text"] == "1girl"
    assert sent["3"]["inputs"]["seed"] == 1
    assert (sent["5"]["inputs"]["width"], sent["5"]["inputs"]["height"]) == (1365, 767)
    assert event.is_stopped()
    await plugin.terminate()


async def test_prompt_with_wf_flag_and_quotes(server, plugin_factory):
    plugin = await plugin_factory(server, delivery={"silent": True})
    event = RecordingEvent('prompt "1girl, red   eyes" -u "bad hands" -wf txt2img -s 7')
    results = await drive(plugin, event)
    assert event.sent == []  # silent: no notice
    sent = next(iter(server.prompts.values()))["prompt"]
    assert sent["6"]["inputs"]["text"] == "1girl, red   eyes"
    assert sent["7"]["inputs"]["text"] == "bad hands"
    assert any(isinstance(c, Image) for c in results[0])
    await plugin.terminate()


async def test_help_and_bad_args_are_reported(server, plugin_factory):
    plugin = await plugin_factory(server)
    event = RecordingEvent("绘画 -h")
    results = await drive(plugin, event)
    assert "usage" in results[0][0].text and "-ar" in results[0][0].text

    event = RecordingEvent("绘画 cat -t notanumber")
    results = await drive(plugin, event)
    assert "参数错误" in results[0][0].text
    assert not server.prompts
    await plugin.terminate()


async def test_unknown_workflow_and_backend_url_rules(server, plugin_factory):
    plugin = await plugin_factory(server)
    results = await drive(plugin, RecordingEvent("绘画 cat -wf nope"))
    assert "未找到工作流" in results[0][0].text

    results = await drive(plugin, RecordingEvent("绘画 cat -be http://10.0.0.1:1"))
    assert "管理员" in results[0][0].text
    assert not server.prompts
    await plugin.terminate()


async def test_cooldown_and_daily_limit(server, plugin_factory):
    plugin = await plugin_factory(server, limits={"cd": 1000, "day_limit": 50})
    await drive(plugin, RecordingEvent("绘画 cat", user_id="u1"))
    results = await drive(plugin, RecordingEvent("绘画 cat", user_id="u1"))
    assert "冷却" in results[0][0].text

    # admins bypass the cooldown
    results = await drive(plugin, RecordingEvent("绘画 cat", user_id="u1", admin=True))
    assert any(isinstance(c, Image) for c in results[0])
    await plugin.terminate()

    plugin = await plugin_factory(server, limits={"cd": 0, "day_limit": 2})
    await drive(plugin, RecordingEvent("绘画 cat", user_id="u2"))
    results = await drive(plugin, RecordingEvent("绘画 cat", user_id="u2"))
    assert "调用受限" in results[0][0].text
    results = await drive(plugin, RecordingEvent("绘画 cat", user_id="u2", admin=True))
    assert any(isinstance(c, Image) for c in results[0])
    await plugin.terminate()


async def test_backend_error_is_reported_and_quota_refunded(plugin_factory):
    from fake_comfy import FakeComfy

    server = FakeComfy(ws_mode="error")
    await server.start()
    try:
        plugin = await plugin_factory(server, limits={"day_limit": 2})
        results = await drive(plugin, RecordingEvent("绘画 cat", user_id="u1"))
        assert "生图失败" in results[-1][0].text and "CUDA" in results[-1][0].text
        assert plugin.limiter.daily_records[plugin.limiter.get_today_key("u1")] == 0
        await plugin.terminate()
    finally:
        await server.stop()


def test_filter_ignores_unwoken_and_unknown_messages(server):
    f = plugin_commands.GenerateCommandFilter()
    ev = RecordingEvent("绘画 cat")
    ev.is_at_or_wake_command = False
    assert not f.filter(ev, {})
    assert not f.filter(RecordingEvent("hello 绘画"), {})
    assert not f.filter(RecordingEvent("绘画师傅 cat"), {})
