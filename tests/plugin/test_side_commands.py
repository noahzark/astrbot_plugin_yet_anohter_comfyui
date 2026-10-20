"""Milestone 5: help and info cards, queue tools, 二次元的我, llm-tag, dan, get-task and config import."""
import json
from pathlib import Path

import jinja2
import pytest
from astrbot.core.message.components import Image, Plain, Video

from plugin_env import ROOT, AstrBotConfig, RecordingEvent, plugin_commands, plugin_main
from fake_comfy import PNG_1X1

cards = __import__(f"{ROOT.name}.adapter.cards", fromlist=["template"])
info = __import__(f"{ROOT.name}.comfyui_core.info", fromlist=["help_data"])

NONEBOT_YAML = ROOT / "tests" / "fixtures" / "nonebot_comfyui.yaml"


class LLMResponse:
    def __init__(self, text):
        self.completion_text = text


class FakeContext:
    def __init__(self, reply="1girl, beach"):
        self.reply = reply
        self.calls = []
        self.sent = []

    async def get_current_chat_provider_id(self, umo):
        return "session-provider"

    async def llm_generate(self, *, chat_provider_id, prompt, system_prompt):
        self.calls.append((chat_provider_id, prompt, system_prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(self.reply)

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain.chain))
        return True


async def run(handler, event):
    out = []
    async for res in handler(event):
        if res is not None:
            out.append(list(res.chain))
    return out


def text_of(results):
    return "\n".join(c.text for chain in results for c in chain if isinstance(c, Plain))


def images(results):
    return [c for chain in results for c in chain if isinstance(c, Image)]


@pytest.fixture
async def plugin(server, plugin_factory):
    p = await plugin_factory(server, limits={"cd": 0}, delivery={"cards": False})
    p.context = FakeContext()
    yield p
    await p.terminate()


# ------------------------------------------------------------------ cards


async def test_help_as_text_and_as_card(plugin, monkeypatch):
    out = await run(plugin.help, RecordingEvent("comfyui帮助"))
    text = text_of(out)
    assert "-ar" in text and "超分" in text and "queue" in text

    rendered = []

    async def html_render(tmpl, data):
        rendered.append((tmpl, data))
        return "https://render.example/card.jpg"

    async def text_to_image(text):
        assert "基础生图命令" in text
        return "https://render.example/guide.jpg"

    plugin.cards = True
    plugin.update_notice = "有新版本 v9"
    monkeypatch.setattr(plugin, "html_render", html_render, raising=False)
    monkeypatch.setattr(plugin, "text_to_image", text_to_image, raising=False)
    out = await run(plugin.help, RecordingEvent("comfyui帮助"))
    assert [i.file for i in images(out)] == ["https://render.example/card.jpg", "https://render.example/guide.jpg"]
    tmpl, data = rendered[0]
    assert "<html" in tmpl and data["notices"] == ["有新版本 v9"]
    assert any(c["command"].startswith("超分") for c in data["reg_commands"])


async def test_card_render_failure_falls_back_to_text(plugin, monkeypatch):
    async def broken(tmpl, data):
        raise RuntimeError("render service down")

    plugin.cards = True
    monkeypatch.setattr(plugin, "html_render", broken, raising=False)
    out = await run(plugin.backends, RecordingEvent("comfyui后端"))
    assert "4090" in text_of(out) and not images(out)


async def test_workflow_list(plugin):
    out = await run(plugin.list_workflows, RecordingEvent("查看工作流"))
    assert "txt2img" in text_of(out) and "超分" in text_of(out)
    out = await run(plugin.list_workflows, RecordingEvent("查看工作流 txt2"))
    assert "txt2img" in text_of(out) and "超分" not in text_of(out)
    out = await run(plugin.list_workflows, RecordingEvent("查看工作流 nothing-like-this"))
    assert "没有找到" in text_of(out)


@pytest.mark.parametrize("name", ["help", "workflows", "backends"])
async def test_templates_render(plugin, name):
    env = jinja2.Environment(autoescape=True)
    if name == "help":
        data = info.help_data(plugin.settings, [("超分", "放大")], "0.5.0", ["notice <b>"])
        expect = ["超分", "-ar", "832×1216", "notice &lt;b&gt;", "0.5.0"]
    elif name == "workflows":
        data = {"rows": info.workflow_rows(plugin.store)}
        expect = ["txt2img", "超分", "需要 1 张"]
    else:
        nodes = await info.backend_status(plugin.client, 2)
        nodes.append({"index": 1, "url": "http://x", "system": {}, "queue": {}, "error": "获取系统信息失败: boom"})
        data = {"nodes": nodes}
        expect = ["RTX 4090", "运行 1", "获取系统信息失败: boom", "后端 1"]
    html = env.from_string(cards.template(name)).render(**data)
    for snippet in expect:
        assert snippet in html


# ----------------------------------------------------------- queue tools


async def test_queue_permissions_and_backends(plugin, server):
    out = await run(plugin.queue, RecordingEvent("queue -t"))
    assert "running-0" in text_of(out)

    out = await run(plugin.queue, RecordingEvent("queue -stop"))
    assert "管理员" in text_of(out) and server.interrupted == 0
    out = await run(plugin.queue, RecordingEvent("queue -stop -c", admin=True))
    assert server.interrupted == 1 and server.cleared == 1

    assert "没有后端 5" in text_of(await run(plugin.queue, RecordingEvent("queue -t -be 5")))
    out = await run(plugin.queue, RecordingEvent("queue -t -be http://10.0.0.1:8188"))
    assert "只有管理员" in text_of(out)
    out = await run(plugin.queue, RecordingEvent(f"queue -t -be {server.url}"))
    assert "running-0" in text_of(out)  # a configured URL is fine for anyone


async def test_queue_get_outputs_are_audited(plugin, server):
    server.prompts["p1"] = {"prompt": {"9": {"class_type": "SaveImage"}, "5": {"class_type": "SaveVideo"}}}
    out = await run(plugin.queue, RecordingEvent("queue -g p1"))
    assert len(images(out)) == 1 and any(isinstance(c, Video) for chain in out for c in chain)

    server.rating = "explicit"
    out = await run(plugin.queue, RecordingEvent("queue -g p1"))
    assert not images(out) and "太涩了" in text_of(out)


async def test_capi_and_model_lists(plugin):
    assert "管理员" in text_of(await run(plugin.capi, RecordingEvent("capi")))
    assert "共加载" in text_of(await run(plugin.capi, RecordingEvent("capi -g all", admin=True)))
    assert "a.safetensors" in text_of(await run(plugin.get_ckpt, RecordingEvent("get-ckpt 0")))
    assert "a.safetensors" in text_of(await run(plugin.get_loras, RecordingEvent("get-loras")))
    assert "没有后端" in text_of(await run(plugin.get_loras, RecordingEvent("get-loras 3")))


async def test_get_task_lists_own_history(plugin, server):
    from plugin_env import drive

    assert "暂无" in text_of(await run(plugin.get_task, RecordingEvent("get-task")))
    await drive(plugin, RecordingEvent("绘画 cat", user_id="u7"))
    task_id = next(iter(server.prompts))
    out = text_of(await run(plugin.get_task, RecordingEvent("get-task 0-5", user_id="u7")))
    assert task_id in out and "txt2img" in out
    assert "暂无" in text_of(await run(plugin.get_task, RecordingEvent("get-task", user_id="other")))


# ------------------------------------------------------------- amusement


async def test_today_girl_generates_with_random_prompt(plugin, server):
    event = RecordingEvent("二次元的我")
    out = await run(plugin.today_girl_cmd, event)
    sent = next(iter(server.prompts.values()))["prompt"]["6"]["inputs"]["text"]
    assert "(solo:1.1)" in sent
    assert "锵锵" in event.sent[0].chain[0].text and len(event.sent) == 1  # silent: no "generating" notice
    assert images(out) and event.is_stopped()


async def test_llm_tag(plugin, server):
    plugin.llm["presets"] = [{"name": "xml", "provider": "p-xml", "system_prompt": "XML please"}]
    event = RecordingEvent("llm-tag 海边的少女")
    out = await run(plugin.llm_tag, event)
    provider, prompt, system = plugin.context.calls[0]
    assert (provider, prompt) == ("session-provider", "海边的少女") and "must be in English" in system
    assert next(iter(server.prompts.values()))["prompt"]["6"]["inputs"]["text"] == "1girl, beach"
    assert "LLM 扩写结果" in event.sent[0].chain[0].text and images(out)

    await run(plugin.llm_tag, RecordingEvent("llm-tag a cat -llm 1"))
    provider, _, system = plugin.context.calls[1]
    assert provider == "p-xml" and system.startswith("XML please")

    plugin.context.reply = RuntimeError("no key")
    assert "LLM 扩写失败" in text_of(await run(plugin.llm_tag, RecordingEvent("llm-tag a cat")))
    assert "写上想画的内容" in text_of(await run(plugin.llm_tag, RecordingEvent("llm-tag")))
    assert len(server.prompts) == 2


async def test_dan(plugin, server, monkeypatch):
    async def tags(client, query, limit):
        return ["hatsune_miku", "miku"][:limit]

    async def samples(client, tag, count=2):
        return [f"https://danbooru.example/{tag}.png"]

    async def image(client, url):
        assert url.startswith("https://danbooru.example/")
        return PNG_1X1

    monkeypatch.setattr(plugin_main, "danbooru_tags", tags)
    monkeypatch.setattr(plugin_main, "danbooru_samples", samples)
    monkeypatch.setattr(plugin_main, "danbooru_image", image)

    out = await run(plugin.danbooru_cmd, RecordingEvent("dan miku 2"))
    assert "(hatsune_miku:1)" in text_of(out) and "(miku:1)" in text_of(out) and len(images(out)) == 2

    server.rating = "explicit"
    out = await run(plugin.danbooru_cmd, RecordingEvent("dan miku 1"))
    assert not images(out) and "太涩了" in text_of(out)
    assert "用法" in text_of(await run(plugin.danbooru_cmd, RecordingEvent("dan")))


# ------------------------------------------------------- wiring and admin


async def test_side_command_words_are_reserved(server, plugin_factory, tmp_path):
    plugin = await plugin_factory(server)
    wf_dir = Path(plugin.settings.workflows_dir)
    reflex = json.loads((wf_dir / "txt2img_reflex.json").read_text(encoding="utf-8"))
    reflex["command"] = ["queue", "画个图"]
    (wf_dir / "txt2img_reflex.json").write_text(json.dumps(reflex), encoding="utf-8")
    plugin.refresh_commands()
    assert plugin_commands.COMMANDS.workflow_for("画个图") == "txt2img"
    assert "queue" not in plugin_commands.COMMANDS.words()
    await plugin.terminate()


async def test_update_notice_reaches_help(server, plugin_factory, monkeypatch):
    async def fake_check(client, current):
        return f"有新版本 v99（当前 {current}）"

    monkeypatch.setattr(plugin_main, "check_update", fake_check)
    plugin = await plugin_factory(server, delivery={"cards": False}, misc={"update_check": True})
    assert plugin._background
    for task in list(plugin._background):
        await task
    assert "v99" in text_of(await run(plugin.help, RecordingEvent("comfyui帮助")))
    await plugin.terminate()


async def test_import_config_round_trips_the_nonebot_template(server, plugin_factory, tmp_path):
    plugin = await plugin_factory(server)
    cmd = f"comfy import-config {NONEBOT_YAML}"
    assert "管理员" in text_of(await run(plugin.comfy_import_config, RecordingEvent(cmd)))

    out = text_of(await run(plugin.comfy_import_config, RecordingEvent(cmd, admin=True)))
    assert "已导入" in out and "comfyui_tips" in out
    s = plugin.settings
    assert s.backend_urls == ["http://127.0.0.1:8188"] and s.cd == 20 and s.day_limit == 50
    assert s.shape_preset["ul"] == (1920, 960) and s.max_values["steps"] == 100
    assert s.group_config["audit_level_group"] == {"114514": 0, "200224": 1}
    assert s.group_config["reject_nsfw_prompts"] == {"114514": 1}
    assert s.audit.mode == "remote" and s.default_value["steps"] == 28

    # The import was saved: a fresh config from the same file sees it.
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    saved = AstrBotConfig(config_path=str(tmp_path / "cfg.json"), schema=schema)
    assert saved["backend"]["urls"] == ["http://127.0.0.1:8188"]
    assert len(saved["llm"]["presets"]) == 2
    await plugin.terminate()

    missing = text_of(await run(plugin.comfy_import_config, RecordingEvent("comfy import-config /nope.yaml", admin=True)))
    assert "读取" in missing and "失败" in missing
