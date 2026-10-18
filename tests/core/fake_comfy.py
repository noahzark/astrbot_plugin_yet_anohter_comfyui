"""A tiny ComfyUI stand-in for client tests: HTTP endpoints plus ``/ws``."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from aiohttp import web


# A valid 1x1 PNG so adapters can hand the bytes to real image code.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


class FakeComfy:
    """``ws_mode``: ``ok`` reports progress then success, ``error`` reports an
    ``execution_error``, ``interrupt`` an ``execution_interrupted``, ``silent``
    never sends anything (history must be polled), ``refuse`` rejects the
    socket upgrade."""

    def __init__(self, queue_remaining: int = 0, ws_mode: str = "ok") -> None:
        self.queue_remaining = queue_remaining
        self.ws_mode = ws_mode
        self.prompts: dict[str, dict] = {}
        self.views: list[str] = []
        self.uploads: list[str] = []
        self.interrupted = 0
        self.deleted: list[str] = []
        self.cleared = 0
        self.app = web.Application()
        self.app.router.add_get("/prompt", self.get_prompt)
        self.app.router.add_post("/prompt", self.post_prompt)
        self.app.router.add_get("/history/{pid}", self.history)
        self.app.router.add_post("/upload/image", self.upload)
        self.app.router.add_post("/interrupt", self.interrupt)
        self.app.router.add_post("/queue", self.queue)
        self.app.router.add_get("/object_info/{node}", self.object_info)
        self.app.router.add_get("/ws", self.ws)
        self.app.router.add_get("/view", self.view)
        self._runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> str:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def get_prompt(self, request: web.Request) -> web.Response:
        return web.json_response({"exec_info": {"queue_remaining": self.queue_remaining}})

    async def post_prompt(self, request: web.Request) -> web.Response:
        body = await request.json()
        if "bad" in body["prompt"]:
            return web.json_response({"error": "invalid prompt", "node_errors": {"bad": {}}}, status=400)
        pid = uuid.uuid4().hex
        self.prompts[pid] = body
        return web.json_response({"prompt_id": pid, "number": len(self.prompts), "node_errors": {}})

    def _history_entry(self, pid: str) -> dict:
        if self.ws_mode == "error":
            return {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
                ["execution_error", {"prompt_id": pid, "node_id": "3", "node_type": "KSampler",
                                     "exception_message": "CUDA out of memory"}]]}}
        body = self.prompts[pid]["prompt"]
        n_images = 1
        for node in body.values():
            if isinstance(node, dict) and node.get("class_type") == "EmptyLatentImage":
                n_images = int(node["inputs"].get("batch_size", 1))
        images = [{"filename": f"{pid[:8]}_{i}.png", "subfolder": "", "type": "output"} for i in range(n_images)]
        return {"outputs": {"9": {"images": images}}, "status": {"status_str": "success", "completed": True}}

    async def history(self, request: web.Request) -> web.Response:
        pid = request.match_info["pid"]
        if pid not in self.prompts:
            return web.json_response({})
        return web.json_response({pid: self._history_entry(pid)})

    async def view(self, request: web.Request) -> web.Response:
        self.views.append(request.query.get("filename", ""))
        return web.Response(body=PNG_1X1, content_type="image/png")

    async def upload(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        name = ""
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "image":
                name = part.filename or ""
                await part.read()
            else:
                await part.text()
        self.uploads.append(name)
        return web.json_response({"name": name, "subfolder": "", "type": "input"})

    async def interrupt(self, request: web.Request) -> web.Response:
        self.interrupted += 1
        return web.Response(text="")

    async def queue(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("clear"):
            self.cleared += 1
        self.deleted += body.get("delete", [])
        return web.Response(text="")

    async def object_info(self, request: web.Request) -> web.Response:
        node = request.match_info["node"]
        key = "lora_name" if node == "LoraLoader" else "ckpt_name"
        return web.json_response({node: {"input": {"required": {key: [["a.safetensors", "b.safetensors"]]}}}})

    async def ws(self, request: web.Request) -> web.WebSocketResponse:
        if self.ws_mode == "refuse":
            raise web.HTTPNotFound()
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        client_id = request.query.get("clientId", "")

        if self.ws_mode != "silent":
            # Wait for a prompt from this client (the client connects first).
            pid = None
            for _ in range(200):
                pid = next((p for p, body in reversed(self.prompts.items()) if body.get("client_id") == client_id), None)
                if pid:
                    break
                await asyncio.sleep(0.01)
            pid = pid or "none"
            if self.ws_mode == "error":
                await ws.send_str(json.dumps({"type": "execution_error", "data": {
                    "prompt_id": pid, "node_id": "3", "node_type": "KSampler",
                    "exception_message": "CUDA out of memory"}}))
            elif self.ws_mode == "interrupt":
                await ws.send_str(json.dumps({"type": "execution_interrupted", "data": {"prompt_id": pid}}))
            else:
                for i in (1, 2):
                    await ws.send_str(json.dumps({"type": "progress", "data": {"prompt_id": pid, "value": i, "max": 2}}))
                await ws.send_str(json.dumps({"type": "executing", "data": {"prompt_id": pid, "node": None}}))

        # Stay open until the client goes away, like the real server.
        try:
            async for _ in ws:
                pass
        except Exception:
            pass
        return ws
