"""End-to-end pipeline tests against the fake ComfyUI server."""
from argparse import Namespace

import pytest

from comfyui_core.cli import build_parser, tokenize
from comfyui_core.client import ComfyClient
from comfyui_core.exceptions import ArgsError, InterruptError, TaskError
from comfyui_core.pipeline import GenerationPipeline, build_request
from comfyui_core.settings import CoreSettings
from comfyui_core.workflow import WorkflowStore

from fake_comfy import PNG_1X1, FakeComfy

from golden_util import BUILT_IN_WF as BUILT_IN


def parse(text: str, default_wf: str = "txt2img") -> Namespace:
    return build_parser(default_wf).parse_args(tokenize(text))


async def make(ws_mode: str = "ok", **settings_kwargs):
    server = FakeComfy(ws_mode=ws_mode)
    await server.start()
    settings = CoreSettings(backend_urls=[server.url], workflows_dir=BUILT_IN, timeout=2, task_timeout=10,
                            **settings_kwargs)
    client = ComfyClient(settings.backend_urls, timeout=2)
    pipeline = GenerationPipeline(client, WorkflowStore(BUILT_IN), settings)
    return server, settings, client, pipeline


def test_build_request_defaults_and_ratio():
    settings = CoreSettings()
    req = build_request(parse("1girl, smile -ar 16:9 -s 1"), settings, user_id="u1")
    assert req.workflow == "txt2img"
    assert req.prompt == "1girl, smile"
    assert req.seed == 1
    assert (req.width, req.height) == (1365, 767)  # golden-tested ratio math
    assert req.sampler == "dpmpp_2m" and req.scheduler == "karras"
    assert req.steps == 28 and req.batch_count == 1


def test_build_request_presets_and_limits():
    settings = CoreSettings()
    settings.default_value["preset_prompt"] = "masterpiece"
    settings.default_value["preset_negative_prompt"] = "lowres"
    req = build_request(parse("cat -u blurry -t 500 -b 9 -bc 9"), settings)
    assert req.prompt == "masterpiece, cat"
    assert req.negative_prompt == "lowres, blurry"
    assert (req.steps, req.batch_size, req.batch_count) == (100, 2, 2)  # clamped by max_values

    su = build_request(parse("cat -o -t 500"), settings, is_superuser=True)
    assert su.prompt == "cat" and su.steps == 500


def test_build_request_backend_url_is_admin_only():
    settings = CoreSettings()
    with pytest.raises(ArgsError):
        build_request(parse("cat -be http://evil:1"), settings)
    assert build_request(parse("cat -be http://x:1"), settings, is_admin=True).selected_backend == "http://x:1"
    assert build_request(parse("cat -be 0"), settings).selected_backend == "0"


def test_build_request_extracts_loras():
    req = build_request(parse("cat, <lora:foo:0.8>, dog"), CoreSettings())
    assert req.loras == [("foo", 0.8)]
    assert req.prompt == "cat, dog"


async def test_txt2img_end_to_end():
    server, settings, client, pipeline = await make()
    try:
        notices = []

        async def notify(text):
            notices.append(text)

        req = build_request(parse("1girl -ar 16:9 -s 1 -b 2"), settings, user_id="u1")
        outcome = await pipeline.run(req, notify=notify)

        assert len(outcome.results) == 1
        result = outcome.results[0]
        assert result.images == [PNG_1X1, PNG_1X1]
        assert result.backend_index == 0 and result.task_id in server.prompts
        assert notices and "txt2img" in notices[0]

        sent = server.prompts[result.task_id]["prompt"]
        assert sent["6"]["inputs"]["text"] == "1girl"
        assert sent["3"]["inputs"]["seed"] == 1
        assert (sent["5"]["inputs"]["width"], sent["5"]["inputs"]["height"]) == (1365, 767)
        assert sent["5"]["inputs"]["batch_size"] == 2
        assert len(server.views) == 2
    finally:
        await client.close()
        await server.stop()


async def test_batch_count_increments_seed():
    server, settings, client, pipeline = await make()
    try:
        outcome = await pipeline.run(build_request(parse("cat -s 10 -bc 2"), settings))
        seeds = sorted(server.prompts[r.task_id]["prompt"]["3"]["inputs"]["seed"] for r in outcome.results)
        assert seeds == [10, 11]
        assert sum(len(r.images) for r in outcome.results) == 2
    finally:
        await client.close()
        await server.stop()


async def test_concurrency_spreads_over_backends():
    a, b = FakeComfy(), FakeComfy()
    await a.start()
    await b.start()
    settings = CoreSettings(backend_urls=[a.url, b.url], workflows_dir=BUILT_IN, timeout=2, task_timeout=10)
    client = ComfyClient(settings.backend_urls, timeout=2)
    pipeline = GenerationPipeline(client, WorkflowStore(BUILT_IN), settings)
    try:
        outcome = await pipeline.run(build_request(parse("cat -con -bc 2"), settings))
        assert sorted(r.backend_index for r in outcome.results) == [0, 1]
        assert len(a.prompts) == 1 and len(b.prompts) == 1
    finally:
        await client.close()
        await a.stop()
        await b.stop()


async def test_execution_error_raises_task_error():
    server, settings, client, pipeline = await make(ws_mode="error")
    try:
        with pytest.raises(TaskError, match="CUDA out of memory"):
            await pipeline.run(build_request(parse("cat"), settings))
    finally:
        await client.close()
        await server.stop()


async def test_interrupt_raises():
    server, settings, client, pipeline = await make(ws_mode="interrupt")
    try:
        with pytest.raises(InterruptError):
            await pipeline.run(build_request(parse("cat"), settings))
    finally:
        await client.close()
        await server.stop()


@pytest.mark.parametrize("ws_mode", ["silent", "refuse"])
async def test_falls_back_to_history_polling(ws_mode):
    """A dead or missing socket must not hang the task."""
    server, settings, client, pipeline = await make(ws_mode=ws_mode)
    try:
        task_id, entry = await client.submit_and_wait(server.url, {"5": {"class_type": "EmptyLatentImage",
                                                                     "inputs": {"batch_size": 1}}}, "cid", timeout=10)
        assert entry["status"]["completed"]
    finally:
        await client.close()
        await server.stop()


async def test_wait_times_out():
    server, settings, client, pipeline = await make(ws_mode="silent")
    try:
        with pytest.raises(TimeoutError):
            await client.wait_for_task(server.url, "missing", None, timeout=0.3, poll_interval=0.05)
    finally:
        await client.close()
        await server.stop()
