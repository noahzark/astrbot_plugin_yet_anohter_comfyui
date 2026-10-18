"""A tiny ComfyUI stand-in for client tests: HTTP endpoints plus ``/ws``."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from aiohttp import web


class FakeComfy:
    def __init__(self, queue_remaining: int = 0) -> None:
        self.queue_remaining = queue_remaining
        self.prompts: dict[str, dict] = {}
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

    async def history(self, request: web.Request) -> web.Response:
        pid = request.match_info["pid"]
        if pid not in self.prompts:
            return web.json_response({})
        return web.json_response({pid: {"outputs": {"9": {"images": [
            {"filename": "out.png", "subfolder": "", "type": "output"}]}},
            "status": {"status_str": "success", "completed": True}}})

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
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Report on the newest prompt: a little progress, then done.
        await asyncio.sleep(0.05)
        pid = next(reversed(self.prompts), "none")
        for i in (1, 2):
            await ws.send_str(json.dumps({"type": "progress", "data": {"prompt_id": pid, "value": i, "max": 2}}))
        await ws.send_str(json.dumps({"type": "executing", "data": {"prompt_id": pid, "node": None}}))
        await asyncio.sleep(0.5)
        await ws.close()
        return ws
