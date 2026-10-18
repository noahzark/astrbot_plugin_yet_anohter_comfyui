"""Workflow-level defaults, backend whitelists, uploads and media outputs."""
import copy

import pytest

from comfyui_core.cli import build_parser, tokenize
from comfyui_core.client import ComfyClient
from comfyui_core.exceptions import ComfyuiBackendConnectionError, NoAvailableBackendForSelectedWorkflow
from comfyui_core.pipeline import GenerationPipeline, build_request
from comfyui_core.settings import CoreSettings
from comfyui_core.workflow import WorkflowStore, apply_reflex

from fake_comfy import PNG_1X1, FakeComfy
from golden_util import BUILT_IN_WF

PRIMITIVE_API = {
    "1": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "baked prompt"}},
    "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "baked negative"}},
    "3": {"class_type": "PrimitiveInt", "inputs": {"value": 7}},
    "4": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 8, "sampler_name": "euler",
                                               "scheduler": "normal", "denoise": 1}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
}


def parse(text: str, wf: str = "txt2img", reg_args=None):
    return build_parser(wf, reg_args).parse_args(tokenize(text))


def test_prompt_reaches_string_primitives_but_not_other_values():
    reflex = {"prompt": 1, "negative_prompt": 2, "seed": 3, "output": 9}
    out = apply_reflex(PRIMITIVE_API, reflex, {"prompt": "a fox", "negative_prompt": "blurry", "seed": 5})
    assert out["1"]["inputs"]["value"] == "a fox"
    assert out["2"]["inputs"]["value"] == "blurry"
    assert out["3"]["inputs"]["value"] == 7  # an int primitive is not a text box

    linked = copy.deepcopy(PRIMITIVE_API)
    linked["1"]["inputs"]["value"] = ["5", 0]
    assert apply_reflex(linked, reflex, {"prompt": "a fox"})["1"]["inputs"]["value"] == ["5", 0]


QWEN_OVERRIDE = {"width": 1024, "height": 1024, "steps": 25, "cfg_scale": 1.0, "sampler": "euler",
                 "scheduler": "simple", "denoise_strength": 1.0}


def test_workflow_override_replaces_config_defaults():
    req = build_request(parse("a fox", "qwen21"), CoreSettings(), workflow_defaults=QWEN_OVERRIDE)
    assert (req.width, req.height, req.steps, req.cfg_scale) == (1024, 1024, 25, 1.0)
    assert (req.sampler, req.scheduler) == ("euler", "simple")


def test_command_line_beats_workflow_override():
    req = build_request(parse("a fox -t 8 -cfg 2.5 -ar 16:9", "qwen21"), CoreSettings(),
                        workflow_defaults=QWEN_OVERRIDE)
    assert (req.steps, req.cfg_scale) == (8, 2.5)
    assert (req.width, req.height) == (1365, 767)


def test_override_wins_over_configured_shape_and_model():
    settings = CoreSettings(model="sdxl.safetensors")
    settings.default_value["shape"] = "640x640"
    req = build_request(parse("x"), settings, workflow_defaults={"width": 1024, "height": 1024, "model": "q.gguf"})
    assert (req.width, req.height, req.model) == (1024, 1024, "q.gguf")
    # Without a size in the override, the configured shape still applies.
    req = build_request(parse("x"), settings, workflow_defaults={"steps": 9})
    assert (req.width, req.height, req.model) == (640, 640, "sdxl.safetensors")


def test_override_prompt_flags_and_seed():
    settings = CoreSettings()
    settings.default_value["preset_prompt"] = "masterpiece"
    req = build_request(parse("edit it"), settings, workflow_defaults={"override": True, "seed": 77})
    assert req.prompt == "edit it" and req.seed == 77
    req = build_request(parse(""), settings, workflow_defaults={"prompt": "default scene"})
    assert req.prompt == "masterpiece, default scene"


def test_builtin_img2img_gets_its_denoise():
    store = WorkflowStore(BUILT_IN_WF)
    reflex = store.load_reflex("img2img")
    req = build_request(parse("cat", "img2img"), CoreSettings(), workflow_defaults=reflex["override"])
    assert req.denoise_strength == 0.5


# ------------------------------------------------------------ live pipeline


@pytest.fixture
async def servers():
    started = [FakeComfy(), FakeComfy()]
    for s in started:
        await s.start()
    yield started
    for s in started:
        await s.stop()


def write_pair(tmp_path, name, api, reflex):
    import json

    (tmp_path / f"{name}.json").write_text(json.dumps(api), encoding="utf-8")
    (tmp_path / f"{name}_reflex.json").write_text(json.dumps(reflex), encoding="utf-8")


def make_pipeline(urls, wf_dir):
    settings = CoreSettings(backend_urls=urls, workflows_dir=wf_dir, timeout=2, task_timeout=10)
    client = ComfyClient(settings.backend_urls, timeout=2)
    return settings, client, GenerationPipeline(client, WorkflowStore(wf_dir), settings)


IMG_API = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
}


async def test_available_limits_backend_choice(servers, tmp_path):
    write_pair(tmp_path, "only1", IMG_API, {"output": 9, "available": [1]})
    settings, client, pipeline = make_pipeline([s.url for s in servers], tmp_path)
    try:
        for chosen in (None, "0"):  # automatic, and a manual pick outside the list
            req = build_request(parse("x -wf only1" + (f" -be {chosen}" if chosen else "")), settings)
            out = await pipeline.run(req)
            assert out.backend_index == 1
        assert not servers[0].prompts and len(servers[1].prompts) == 2

        await servers[1].stop()
        with pytest.raises(NoAvailableBackendForSelectedWorkflow, match="only1"):
            await pipeline.run(build_request(parse("x -wf only1"), settings))
    finally:
        await client.close()


async def test_concurrent_batch_uploads_to_every_backend(servers, tmp_path):
    write_pair(tmp_path, "edit", IMG_API, {"output": 9, "load_image": 1})
    settings, client, pipeline = make_pipeline([s.url for s in servers], tmp_path)
    try:
        req = build_request(parse("x -wf edit -con -bc 2"), settings)
        req.init_images = [PNG_1X1]
        out = await pipeline.run(req)
        assert len(out.results) == 2
        for server in servers:
            assert len(server.uploads) == 1 and len(server.prompts) == 1
            sent = next(iter(server.prompts.values()))["prompt"]
            assert sent["1"]["inputs"]["image"] == server.uploads[0]
    finally:
        await client.close()


async def test_upload_error_page_is_a_clean_error(servers, tmp_path):
    write_pair(tmp_path, "edit", IMG_API, {"output": 9, "load_image": 1})
    servers[0].upload_status = 503
    settings, client, pipeline = make_pipeline([servers[0].url], tmp_path)
    try:
        req = build_request(parse("x -wf edit"), settings)
        req.init_images = [PNG_1X1]
        with pytest.raises(ComfyuiBackendConnectionError, match="503"):
            await pipeline.run(req)
    finally:
        await client.close()


async def test_video_audio_and_text_outputs(servers, tmp_path):
    api = {
        "1": {"class_type": "SaveVideo", "inputs": {}},
        "2": {"class_type": "VHS_VideoCombine", "inputs": {}},
        "3": {"class_type": "SaveAudio", "inputs": {}},
        "4": {"class_type": "ShowText|pysssss", "inputs": {}},
    }
    write_pair(tmp_path, "media", api, {"output": {"video": [1, 2], "audio": [3], "text": [4]}})
    settings, client, pipeline = make_pipeline([servers[0].url], tmp_path)
    try:
        out = await pipeline.run(build_request(parse("x -wf media"), settings))
        result = out.results[0]
        assert len(result.videos) == 2  # SaveVideo reports under "images", VHS under "gifs"
        assert len(result.audios) == 1
        assert result.texts == ["tags from 4"]
        assert any(v.endswith("_1_0.mp4") for v in servers[0].views)
    finally:
        await client.close()
