import pytest

from comfyui_core.client import ComfyClient
from comfyui_core.exceptions import APIJsonError, NoAvailableBackendError

from fake_comfy import FakeComfy


@pytest.fixture
async def servers():
    a, b = FakeComfy(queue_remaining=3), FakeComfy(queue_remaining=0)
    await a.start()
    await b.start()
    yield a, b
    await a.stop()
    await b.stop()


@pytest.fixture
async def client(servers):
    a, b = servers
    c = ComfyClient([a.url, b.url], timeout=2)
    yield c
    await c.close()


async def test_select_backend_prefers_shortest_queue(client, servers):
    a, b = servers
    url, idx, info = await client.select_backend()
    assert (url, idx) == (b.url, 1)
    assert info["exec_info"]["queue_remaining"] == 0


async def test_select_backend_explicit(client, servers):
    a, b = servers
    assert await client.select_backend("0") == (a.url, 0, {})
    assert await client.select_backend(b.url) == (b.url, 1, {})
    assert await client.select_backend("http://10.0.0.9:1") == ("http://10.0.0.9:1", -1, {})
    assert (await client.select_backend("7"))[1] in (0, 1)  # out of range index falls back to probing


async def test_no_backend_available():
    c = ComfyClient(["http://127.0.0.1:1"], timeout=1)
    with pytest.raises(NoAvailableBackendError):
        await c.select_backend()
    await c.close()


async def test_prompt_track_history_roundtrip(client, servers):
    a, _ = servers
    pid = await client.post_prompt(a.url, {"3": {"inputs": {}}}, "cid")
    await client.track_task(a.url, pid, "cid", timeout=5)
    hist = await client.get_history(a.url, pid)
    assert hist[pid]["outputs"]["9"]["images"][0]["filename"] == "out.png"


async def test_post_prompt_error_raises(client, servers):
    a, _ = servers
    with pytest.raises(APIJsonError) as e:
        await client.post_prompt(a.url, {"bad": {}}, "cid")
    assert "invalid prompt" in str(e.value)


async def test_upload_queue_and_object_info(client, servers):
    a, _ = servers
    resp = await client.upload_image(a.url, b"\x89PNG", "img1")
    assert resp["name"] == "img1.png" and a.uploads == ["img1.png"]
    await client.interrupt(a.url)
    await client.delete_queue(a.url, ["x"])
    await client.clear_queue(a.url)
    assert (a.interrupted, a.deleted, a.cleared) == (1, ["x"], 1)
    assert await client.get_loras(a.url) == ["a.safetensors", "b.safetensors"]
    assert await client.get_checkpoints(a.url) == ["a.safetensors", "b.safetensors"]


async def test_close_is_idempotent(client):
    await client.get_session()
    await client.close()
    await client.close()
