"""Side-command views, amusement helpers, task history, update check and the config importer."""
import json
import random
import shutil
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from comfyui_core import amusement, info
from comfyui_core.client import ComfyClient
from comfyui_core.cli import build_queue_parser
from comfyui_core.importer import import_nonebot_config, merge_into
from comfyui_core.settings import AuditSettings, CoreSettings
from comfyui_core.tasks import TaskRecords
from comfyui_core.updates import check_update, version_tuple
from comfyui_core.workflow import WorkflowStore

from fake_comfy import FakeComfy
from golden_util import BUILT_IN_WF, ROOT

NONEBOT_YAML = ROOT / "tests" / "fixtures" / "nonebot_comfyui.yaml"


@pytest.fixture
async def comfy():
    server = FakeComfy(queue_remaining=2)
    await server.start()
    client = ComfyClient([server.url], timeout=2)
    yield server, client
    await client.close()
    await server.stop()


def test_help_data_lists_every_generation_flag():
    settings = CoreSettings(audit=AuditSettings(mode="remote"))
    data = info.help_data(settings, [("超分", "放大图片")], "1.2.3", ["new version"])
    flags = {p["flag"] for p in data["parameters"]}
    assert {"-ar", "-wf", "-be", "-con", "-notice", "-sil"} <= flags
    ar = next(p for p in data["parameters"] if p["flag"] == "-ar")
    assert ar["example"] == "prompt -ar 16:9" and "example" not in ar["description"]
    assert data["reg_commands"] == [{"command": "超分", "note": "放大图片"}]
    assert {"name": "p", "width": 832, "height": 1216} in data["shape_presets"]
    text = info.help_text(data)
    assert "超分" in text and "-ar" in text and "new version" in text


def test_help_warns_about_missing_local_audit_deps(monkeypatch):
    monkeypatch.setattr(info.importlib.util, "find_spec", lambda name: None)
    data = info.help_data(CoreSettings(audit=AuditSettings(mode="nudenet")), [], "1")
    assert any("nudenet" in n for n in data["notices"])
    assert not info.help_data(CoreSettings(audit=AuditSettings(mode="nudenet", enabled=False)), [], "1")["notices"]


def test_workflow_rows_search_and_visibility(tmp_path):
    for f in BUILT_IN_WF.glob("*.json"):
        shutil.copy(f, tmp_path)
    reflex = json.loads((tmp_path / "img2img_reflex.json").read_text(encoding="utf-8"))
    reflex["visible"] = False
    (tmp_path / "img2img_reflex.json").write_text(json.dumps(reflex), encoding="utf-8")

    store = WorkflowStore(tmp_path)
    rows = info.workflow_rows(store)
    names = [r["name"] for r in rows]
    assert "img2img" not in names and "txt2img" in names
    upscale = next(r for r in rows if r["name"] == "超分")
    assert upscale["needs_image"] and upscale["command"]
    # Indices are positions in the full list, so -wf N keeps working.
    assert [r["index"] for r in rows] == [store.list_workflows().index(n) + 1 for n in names]

    third = store.list_workflows()[2]
    assert [r["name"] for r in info.workflow_rows(store, "3")] == ([third] if third != "img2img" else [])
    assert [r["name"] for r in info.workflow_rows(store, "txt2")] == ["txt2img"]
    assert "txt2img" in info.workflows_text(rows)


async def test_backend_status_and_masking(comfy):
    server, client = comfy
    down_url = "http://127.0.0.1:1/private-comfy"
    client.backend_urls.append(down_url)
    nodes = await info.backend_status(client, timeout=2)
    ok, down = nodes
    assert ok["system"]["device_name"].endswith("4090") and ok["system"]["vram_total_gb"] == 24.0
    assert ok["queue"] == {"running_count": 1, "pending_count": 2, "running_ids": ["running-0"]}
    assert server.url not in json.dumps(ok) and ok["url"].startswith("http://")
    assert down["error"] and down_url not in down["error"] and down["url"] in down["error"]
    text = info.backends_text(nodes)
    assert "4090" in text and "❌" in text


async def test_queue_report(comfy):
    server, client = comfy
    parse = build_queue_parser().parse_args
    text, files = await info.queue_report(client, server.url, parse(["-t"]))
    assert "共 3 个任务" in text and "pending-1" in text

    text, _ = await info.queue_report(client, server.url, parse(["-stop", "-c", "-d", "a,b"]))
    assert server.interrupted == 1 and server.cleared == 1 and server.deleted == ["a", "b"]

    server.prompts["p1"] = {"prompt": {"9": {"class_type": "SaveImage"}}}
    text, files = await info.queue_report(client, server.url, parse(["-i", "p1", "-g", "p1", "-v"]))
    assert "success" in text and "是否完成: 是" in text and "p1" in text
    assert files and files[0]["filename"].endswith(".png")
    text, files = await info.queue_report(client, server.url, parse(["-g", "nope"]))
    assert "未找到" in text and not files
    assert "queue -h" in (await info.queue_report(client, server.url, parse([])))[0]


async def test_node_info(comfy):
    server, client = comfy
    assert "共加载 3 个节点" in await info.node_info(client, server.url, "all")
    assert "lora_name" not in await info.node_info(client, server.url, "Missing")
    assert "input" in await info.node_info(client, server.url, "LoraLoader")


def test_parse_range():
    assert info.parse_range("5-20") == (5, 20)
    assert info.parse_range("oops") == (0, 10)
    assert info.parse_range(None) == (0, 10)
    assert info.parse_range("9-3") == (0, 10)


# --------------------------------------------------------------- amusement


def test_today_girl_is_seeded_and_complete():
    prompt, desc = amusement.today_girl(random.Random(7))
    assert (prompt, desc) == amusement.today_girl(random.Random(7))
    assert prompt.startswith("(solo:1.1), ") and prompt.count(",") >= 11
    assert desc.startswith("二次元的我")


async def test_danbooru_parsing():
    class Client:
        def __init__(self):
            self.urls = []

        async def request(self, method, url, **kw):
            self.urls.append(url)
            if "autocomplete" in url:
                return [{"value": "hatsune_miku"}, {"value": "miku_(cosplay)"}, {"label": "no value"}]
            return [{"file_url": "https://x/a.zip"}, {"large_file_url": "https://x/b.jpg"}, {"file_url": "https://x/c.png"}]

    client = Client()
    assert await amusement.danbooru_tags(client, "初音 未来", 5) == ["hatsune_miku", "miku_(cosplay)"]
    assert "%E5%88%9D%E9%9F%B3%20%E6%9C%AA%E6%9D%A5" in client.urls[0]
    assert await amusement.danbooru_samples(client, "hatsune_miku") == ["https://x/b.jpg", "https://x/c.png"]


async def test_danbooru_image_rejects_challenge_pages():
    class Client:
        def __init__(self, body):
            self.body, self.headers = body, None

        async def request(self, method, url, headers=None, **kw):
            self.headers = headers
            return self.body

    ok = Client(b"\xff\xd8\xff jpeg")
    assert await amusement.danbooru_image(ok, "https://x/a.jpg") == b"\xff\xd8\xff jpeg"
    assert ok.headers["User-Agent"]
    with pytest.raises(Exception, match="没有返回图片"):
        await amusement.danbooru_image(Client(b"<!DOCTYPE html><title>Just a moment"), "https://x/a.jpg")


def test_llm_prompt_and_presets():
    system = amusement.llm_tag_system_prompt("")
    assert "must be in English" in system and "Input: 一个女仆" in system
    assert amusement.llm_tag_system_prompt("custom").startswith("custom")
    presets = [{"name": "a"}, {"name": "b"}]
    assert amusement.pick_llm_preset(presets, 2) == {"name": "b"}
    assert amusement.pick_llm_preset(presets, 0) == {} == amusement.pick_llm_preset(presets, 3)


# ------------------------------------------------------------- task records


def test_task_records_cap_and_restore():
    tasks = TaskRecords(max_per_user=3)
    for i in range(5):
        tasks.record("u", f"t{i}", "txt2img", 0)
    assert [t["id"] for t in tasks.recent("u")] == ["t4", "t3", "t2"]
    assert [t["id"] for t in tasks.recent("u", 1, 2)] == ["t3"]
    again = TaskRecords(max_per_user=3)
    again.restore(json.loads(json.dumps(tasks.snapshot())))
    assert again.recent("u") == tasks.recent("u")
    again.restore("garbage")
    assert again.recent("u") == tasks.recent("u")


# ------------------------------------------------------------------ updates


async def test_update_check():
    class Client:
        def __init__(self, data):
            self.data = data

        async def request(self, *a, **kw):
            if isinstance(self.data, Exception):
                raise self.data
            return self.data

    assert version_tuple("v1.10.0") > version_tuple("1.9.3")
    newer = await check_update(Client({"tag_name": "v0.6.0", "html_url": "https://r"}), "0.5.0")
    assert "v0.6.0" in newer and "https://r" in newer
    assert await check_update(Client({"tag_name": "v0.5.0"}), "0.5.0") == ""
    assert await check_update(Client({"message": "Not Found"}), "0.5.0") == ""
    assert await check_update(Client(RuntimeError("offline")), "0.5.0") == ""


# ----------------------------------------------------------------- importer


def test_importer_maps_the_shipped_template():
    data = yaml.safe_load(NONEBOT_YAML.read_text(encoding="utf-8"))
    res = import_nonebot_config(data, NONEBOT_YAML)
    cfg = res.config
    assert cfg["backend"]["urls"] == [data["comfyui_url"]]  # multi_backend is off in the template
    assert cfg["backend"]["timeout"] == data["comfyui_timeout"]
    assert Path(cfg["workflow"]["workflows_dir"]).is_absolute()
    assert cfg["generation"]["shape_preset"][0] == "p=832x1216"
    assert cfg["generation"]["max_values"] == data["comfyui_max_dict"]
    assert cfg["generation"]["defaults"]["shape"] == "" and cfg["generation"]["defaults"]["steps"] == 28
    assert cfg["limits"] == {"cd": 20, "day_limit": 50, "superusers": []}
    assert cfg["audit"]["mode"] == "remote" and cfg["audit"]["site"] == data["comfyui_audit_site"]
    assert "use_endpoint" not in cfg.get("text_audit", {})  # placeholder token: keep the AstrBot provider
    assert len(cfg["llm"]["presets"]) == 2
    groups = {g["group_id"]: g for g in cfg["groups"]["overrides"]}
    assert groups["114514"]["audit_level"] == 0 and groups["114514"]["reject_nsfw_prompts"] == 1
    assert groups["200224"]["audit_level"] == 1 and groups["200224"]["img_send"] == -1
    assert res.unmapped == []
    assert any("comfyui_tips" in n for n in res.notes)


def test_importer_modes_and_endpoint():
    res = import_nonebot_config({
        "comfyui_url": "http://a", "comfyui_url_list": ["http://a", "http://b"], "comfyui_multi_backend": True,
        "comfyui_audit_local": True, "comfyui_audit_model": 2, "comfyui_workflows_dir": "/abs/wf",
        "comfyui_openai": {"endpoint": "https://e/v1", "token": "sk-real", "params": {"model": "m"}},
        "comfyui_group_config": {"enable_in_group": {"1": 0}},
        "something_custom": 1,
    })
    assert res.config["backend"]["urls"] == ["http://a", "http://b"]
    assert res.config["audit"]["mode"] == "nudenet"
    assert res.config["workflow"]["workflows_dir"] == "/abs/wf"
    assert res.config["text_audit"] == {"use_endpoint": True, "token": "sk-real", "endpoint": "https://e/v1", "model": "m"}
    assert res.config["groups"]["overrides"][0]["enabled"] is False
    assert res.unmapped == ["something_custom"]
    assert import_nonebot_config({"comfyui_dual_audit": True}).config["audit"]["mode"] == "dual"

    target = {"audit": {"level": 2, "mode": "remote"}, "generation": {"defaults": {"steps": 20, "cfg_scale": 7}}}
    merge_into(target, import_nonebot_config({"comfyui_audit_local": True, "comfyui_default_value": {"steps": 30}}))
    assert target["audit"] == {"level": 2, "mode": "wd14"}
    assert target["generation"]["defaults"] == {"steps": 30, "cfg_scale": 7}
