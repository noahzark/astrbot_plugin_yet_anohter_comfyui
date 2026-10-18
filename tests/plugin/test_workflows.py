"""Milestone 3: every built-in workflow by its own command, with input images."""
import importlib
import io
import json
from pathlib import Path

import pytest
from astrbot.core.message.components import At, Image, Plain, Record, Reply
from PIL import Image as PILImage

from plugin_env import ROOT, RecordingEvent, drive, plugin_commands
from fake_comfy import PNG_1X1

media = importlib.import_module(f"{ROOT.name}.adapter.media")


@pytest.fixture
def png_file(tmp_path):
    path = tmp_path / "in.png"
    path.write_bytes(PNG_1X1)
    return path


def with_images(event, *images):
    event.message_obj.message.extend(images)
    return event


def sent_prompt(server):
    return next(reversed(server.prompts.values()))["prompt"]


@pytest.mark.parametrize(
    "command, needs_image, kind",
    [
        ("绘画 1girl", False, Image),
        ("图生图 1girl", True, Image),
        ("以图绘图 1girl -cn 0.5", True, Image),
        ("超分 -upscale 4", True, Image),
        ("打标", True, Plain),
        ("音乐生成 piano -sec 8", False, Record),
        ("音频生成 rain -sec 8", False, Record),
    ],
)
async def test_builtin_workflows_by_command(server, plugin_factory, png_file, command, needs_image, kind):
    plugin = await plugin_factory(server)
    event = RecordingEvent(command)
    if needs_image:
        with_images(event, Image.fromFileSystem(str(png_file)))
    results = await drive(plugin, event)
    comps = [c for chain in results for c in chain]
    if kind is Plain:  # a text output, not just the header
        assert any(isinstance(c, Plain) and "tags from" in c.text for c in comps), comps
    else:
        assert any(isinstance(c, kind) for c in comps), comps
    assert len(server.uploads) == (1 if needs_image else 0)
    await plugin.terminate()


async def test_reg_args_flags_work_on_their_own_command(server, plugin_factory, png_file):
    plugin = await plugin_factory(server, limits={"cd": 0})
    await drive(plugin, RecordingEvent("音乐生成 piano -sec 8 -s 3"))
    assert sent_prompt(server)["5"]["inputs"]["seconds"] == 8
    await drive(plugin, with_images(RecordingEvent("超分 -upscale 4"), Image.fromFileSystem(str(png_file))))
    assert sent_prompt(server)["12"]["inputs"]["text"] == "4"
    await plugin.terminate()


async def test_reg_args_with_wf_flag_from_prompt(server, plugin_factory):
    plugin = await plugin_factory(server)
    await drive(plugin, RecordingEvent("prompt piano -wf music_gen -sec 12"))
    assert sent_prompt(server)["5"]["inputs"]["seconds"] == 12
    await plugin.terminate()


async def test_img2img_uses_its_override_denoise(server, plugin_factory, png_file):
    plugin = await plugin_factory(server)
    await drive(plugin, with_images(RecordingEvent("图生图 cat"), Image.fromFileSystem(str(png_file))))
    sent = sent_prompt(server)
    sampler = next(n for n in sent.values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["denoise"] == 0.5
    load = next(n for n in sent.values() if n["class_type"] == "LoadImage")
    assert load["inputs"]["image"] == server.uploads[0]
    await plugin.terminate()


async def test_image_from_quoted_message(server, plugin_factory, png_file):
    plugin = await plugin_factory(server)
    event = with_images(RecordingEvent("超分"), Reply(id="q1", chain=[Image.fromFileSystem(str(png_file))]))
    results = await drive(plugin, event)
    assert any(isinstance(c, Image) for c in results[0])
    assert len(server.uploads) == 1
    await plugin.terminate()


async def test_missing_image_is_reported(server, plugin_factory):
    plugin = await plugin_factory(server)
    results = await drive(plugin, RecordingEvent("超分"))
    assert "输入图片" in results[0][0].text
    assert not server.prompts
    await plugin.terminate()


def test_at_mentions_become_avatars_on_qq_only(png_file):
    event = with_images(RecordingEvent("超分"), At(qq="bot"), At(qq="12345"), Image.fromFileSystem(str(png_file)))
    sources = media.image_sources(event)
    assert sources[0].file == media.AVATAR_URL.format(qq="12345")  # avatar first, the bot's own @ skipped
    assert len(sources) == 2

    event.platform_meta.name = "telegram"
    assert len(media.image_sources(event)) == 1


def test_gif_input_keeps_first_frame():
    frames = [PILImage.new("RGB", (4, 4), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    out = media.first_frame(buf.getvalue())
    with PILImage.open(io.BytesIO(out)) as img:
        assert img.format == "PNG" and img.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
    assert media.first_frame(PNG_1X1) == PNG_1X1


async def test_comfy_reload_registers_new_workflows(server, plugin_factory):
    plugin = await plugin_factory(server)
    wf_dir = Path(plugin.settings.workflows_dir)
    (wf_dir / "mine.json").write_text((wf_dir / "txt2img.json").read_text(encoding="utf-8"), encoding="utf-8")
    reflex = json.loads((wf_dir / "txt2img_reflex.json").read_text(encoding="utf-8"))
    reflex["command"] = "我的画"
    (wf_dir / "mine_reflex.json").write_text(json.dumps(reflex, ensure_ascii=False), encoding="utf-8")

    out = [r async for r in plugin.comfy_reload(RecordingEvent("comfy reload"))]
    assert "管理员" in out[0].chain[0].text
    assert plugin_commands.COMMANDS.workflow_for("我的画") is None

    out = [r async for r in plugin.comfy_reload(RecordingEvent("comfy reload", admin=True))]
    assert "mine" in out[0].chain[0].text
    assert plugin_commands.COMMANDS.workflow_for("我的画") == "mine"
    await plugin.terminate()
