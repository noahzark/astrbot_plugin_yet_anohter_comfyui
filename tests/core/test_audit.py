"""Image audit verdicts, failure policy, text audit and their place in the pipeline."""
import pytest

from comfyui_core.audit import ImageAuditor, TextAuditor
from comfyui_core.client import ComfyClient
from comfyui_core.exceptions import TextContentNotSafeError
from comfyui_core.pipeline import GenerationPipeline, build_request
from comfyui_core.settings import AuditSettings, CoreSettings, TextAuditSettings
from comfyui_core.workflow import WorkflowStore

from fake_comfy import PNG_1X1, FakeComfy
from golden_util import BUILT_IN_WF
from test_workflow_io import parse


@pytest.fixture
async def tagger():
    server = FakeComfy()
    await server.start()
    yield server
    await server.stop()


def auditor_for(server, **audit):
    settings = CoreSettings(audit=AuditSettings(site=server.url, **audit))
    client = ComfyClient([server.url], timeout=2)
    return settings, client, ImageAuditor(settings, client)


@pytest.mark.parametrize(
    "rating, level, nsfw",
    [
        ("general", 2, False),
        ("explicit", 1, True),
        ("questionable", 1, False),
        ("questionable", 2, True),
        ("sensitive", 2, False),
        ("sensitive", 3, True),
        ("general", 100, True),
    ],
)
async def test_remote_levels(tagger, rating, level, nsfw):
    tagger.rating = rating
    _, client, auditor = auditor_for(tagger, level=level)
    try:
        assert (await auditor.audit(PNG_1X1))["is_nsfw"] is nsfw
    finally:
        await client.close()


async def test_level_zero_and_disabled_skip_the_request(tagger):
    tagger.rating = "explicit"
    for kwargs in ({"level": 0}, {"enabled": False}):
        _, client, auditor = auditor_for(tagger, **kwargs)
        assert (await auditor.audit(PNG_1X1))["is_nsfw"] is False
        await client.close()
    assert tagger.audited == 0


async def test_group_level_override(tagger):
    tagger.rating = "questionable"
    settings, client, auditor = auditor_for(tagger, level=2)
    settings.group_config["audit_level_group"] = {"111": 1, "222": 0}
    try:
        assert (await auditor.audit(PNG_1X1, "111"))["is_nsfw"] is False
        assert (await auditor.audit(PNG_1X1, "222"))["is_nsfw"] is False
        assert (await auditor.audit(PNG_1X1, "333"))["is_nsfw"] is True
    finally:
        await client.close()


@pytest.mark.parametrize("fail_closed", [True, False])
async def test_failure_policy_is_one_switch(tagger, fail_closed):
    tagger.rating = "error"
    _, client, auditor = auditor_for(tagger, fail_closed=fail_closed)
    try:
        res = await auditor.audit(PNG_1X1)
        assert res["is_nsfw"] is fail_closed and "审核失败" in res["message"]
        # Local backends that cannot load follow the same switch.
        auditor.settings.audit.mode = "nudenet"
        auditor.nudenet._ensure_loaded = lambda: (_ for _ in ()).throw(ImportError("no nudenet"))
        assert (await auditor.audit(PNG_1X1))["is_nsfw"] is fail_closed
    finally:
        await client.close()


# ----------------------------------------------------------------- text


def text_auditor(reply, **text):
    calls = []

    async def llm(system, msg):
        calls.append((system, msg))
        if isinstance(reply, Exception):
            raise reply
        return reply

    settings = CoreSettings(text_audit=TextAuditSettings(**text))
    return TextAuditor(settings, ComfyClient(["http://x"]), llm=llm), calls


async def test_text_audit_verdicts():
    auditor, calls = text_auditor("<think>hmm</think> Yes.", enabled=True)
    assert await auditor.is_unsafe("bad words") is True
    assert calls[0][1] == "bad words"
    auditor, _ = text_auditor("no", enabled=True)
    assert await auditor.is_unsafe("a cat") is False
    auditor, _ = text_auditor(RuntimeError("provider down"), enabled=True)
    assert await auditor.is_unsafe("a cat") is True  # a failed check blocks


def test_text_audit_mode_per_group():
    auditor, _ = text_auditor("no", enabled=False)
    auditor.settings.group_config["reject_nsfw_prompts"] = {"1": 2, "2": 0}
    assert auditor.mode_for("") == 0
    assert auditor.mode_for("1") == 2
    auditor.settings.text_audit.enabled = True
    assert auditor.mode_for("3") == 1 and auditor.mode_for("2") == 0


async def make_pipeline(server, text_auditor=None, image_auditor=None):
    settings = CoreSettings(backend_urls=[server.url], workflows_dir=BUILT_IN_WF, timeout=2, task_timeout=10)
    client = ComfyClient(settings.backend_urls, timeout=2)
    return client, GenerationPipeline(client, WorkflowStore(BUILT_IN_WF), settings, image_auditor, text_auditor)


async def test_pipeline_blocks_or_rewrites_prompts(tagger):
    auditor, _ = text_auditor("yes", enabled=True)
    client, pipeline = await make_pipeline(tagger, text_auditor=auditor)
    try:
        with pytest.raises(TextContentNotSafeError):
            await pipeline.run(build_request(parse("bad"), pipeline.settings))
        assert not tagger.prompts

        # Group mode 2 rewrites, even with the global switch off.
        auditor, _ = text_auditor("a clean cat")
        auditor.settings.group_config["reject_nsfw_prompts"] = {"9": 2}
        pipeline.text_auditor = auditor
        out = await pipeline.run(build_request(parse("a dirty cat"), pipeline.settings, group_id="9"))
        assert next(iter(tagger.prompts.values()))["prompt"]["6"]["inputs"]["text"] == "a clean cat"
        assert "a clean cat" in out.notes[0]

        # A failed rewrite blocks instead of sending the model's error text as the prompt.
        auditor, _ = text_auditor(RuntimeError("down"))
        auditor.settings.group_config["reject_nsfw_prompts"] = {"9": 2}
        pipeline.text_auditor = auditor
        with pytest.raises(TextContentNotSafeError):
            await pipeline.run(build_request(parse("x"), pipeline.settings, group_id="9"))
    finally:
        await client.close()


async def test_pipeline_splits_flagged_images(tagger):
    tagger.rating = "explicit"
    settings, audit_client, auditor = auditor_for(tagger)
    client, pipeline = await make_pipeline(tagger, image_auditor=auditor)
    try:
        out = await pipeline.run(build_request(parse("x -b 2"), pipeline.settings))
        result = out.results[0]
        assert len(result.nsfw_images) == 2 and result.images == []
        assert "审核" in result.audit_message and "audit" in out.timings
    finally:
        await client.close()
        await audit_client.close()
