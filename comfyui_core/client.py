"""HTTP and WebSocket client for one or more ComfyUI backends.

One ``ComfyClient`` owns one ``aiohttp.ClientSession``; the adapter creates
it when the plugin starts and awaits :meth:`close` when it stops.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiohttp import TCPConnector

from .exceptions import (
    ComfyuiBackendConnectionError,
    NoAvailableBackendError,
    APIJsonError,
)

logger = logging.getLogger(__name__)


class ComfyClient:
    def __init__(self, backend_urls: list[str], timeout: int = 5, http_proxy: str = "") -> None:
        self.backend_urls = list(backend_urls)
        self.timeout = timeout
        self.http_proxy = http_proxy
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.set_ciphers("DEFAULT")
                ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                ssl_ctx.options |= ssl.OP_NO_COMPRESSION
                self._session = aiohttp.ClientSession(
                    connector=TCPConnector(ssl=ssl_ctx),
                    proxy=self.http_proxy or None,
                )
            return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def request(
        self,
        method: str,
        target_url: str,
        headers: dict | None = None,
        params: dict | None = None,
        data: Any = None,
        json_data: Any = None,
        timeout: int = 5000,
        as_json: bool = True,
        as_text: bool = False,
        proxy: bool = False,
    ) -> Any:
        session = await self.get_session()
        custom_proxy = self.http_proxy if (proxy and self.http_proxy) else None
        try:
            async with session.request(
                method,
                target_url,
                headers=headers,
                params=params,
                data=data,
                json=json_data,
                timeout=aiohttp.ClientTimeout(total=timeout),
                proxy=custom_proxy,
            ) as response:
                if as_text:
                    return await response.text()
                if as_json:
                    return await response.json()
                return await response.read()
        except Exception as e:
            raise ComfyuiBackendConnectionError(f"请求后端失败 [{target_url}]: {e}")

    async def is_port_open(self, host: str, port: int, timeout: int | None = None) -> bool:
        t = timeout or self.timeout
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=t)
            writer.close()
            await writer.wait_closed()
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return False

    async def get_backend_work_status(self, url: str) -> dict[str, Any]:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        if not await self.is_port_open(host, port):
            raise ComfyuiBackendConnectionError(f"后端 {url} 端口无法连接")
        return await self.request("GET", f"{url}/prompt", timeout=self.timeout)

    async def get_available_backends(self) -> tuple[set[int], dict[str, Any]]:
        backend_dict: dict[str, Any] = {}
        available_indices: set[int] = set()

        results = await asyncio.gather(
            *(self.get_backend_work_status(url) for url in self.backend_urls),
            return_exceptions=True,
        )
        for idx, (res, url) in enumerate(zip(results, self.backend_urls)):
            if isinstance(res, Exception):
                logger.warning("Backend [%s] %s is offline: %s", idx, url, res)
            else:
                backend_dict[url] = res
                available_indices.add(idx)
        return available_indices, backend_dict

    async def select_backend(self, selected_backend: str | None = None) -> tuple[str, int, dict[str, Any]]:
        """Pick the backend with the shortest queue, or the one asked for.

        Returns ``(url, index, status)``; index is -1 for a URL that is not
        in the configured list.
        """
        if selected_backend:
            if selected_backend.isdigit():
                idx = int(selected_backend)
                if 0 <= idx < len(self.backend_urls):
                    return self.backend_urls[idx], idx, {}
            elif selected_backend in self.backend_urls:
                return selected_backend, self.backend_urls.index(selected_backend), {}
            else:
                return selected_backend, -1, {}

        available_indices, backend_dict = await self.get_available_backends()
        if not available_indices:
            raise NoAvailableBackendError("没有可用的 ComfyUI 后端")

        best_url = None
        min_queue = float("inf")
        best_info: dict[str, Any] = {}
        for url, info in backend_dict.items():
            queue_remaining = info.get("exec_info", {}).get("queue_remaining", 0)
            if queue_remaining < min_queue:
                min_queue = queue_remaining
                best_url = url
                best_info = info

        if best_url is None:
            best_url = self.backend_urls[random.choice(list(available_indices))]

        return best_url, self.backend_urls.index(best_url), best_info

    async def post_prompt(self, backend_url: str, prompt_json: dict, client_id: str) -> str:
        res = await self.request("POST", f"{backend_url}/prompt",
                                 json_data={"client_id": client_id, "prompt": prompt_json})
        if not isinstance(res, dict) or res.get("error"):
            err_msg = res.get("error") if isinstance(res, dict) else str(res)
            node_errors = res.get("node_errors", "") if isinstance(res, dict) else ""
            raise APIJsonError(f"ComfyUI API 错误: {err_msg}\n节点错误: {node_errors}")
        return res["prompt_id"]

    async def upload_image(self, backend_url: str, image_bytes: bytes, name: str,
                           image_type: str = "input", overwrite: bool = False) -> dict:
        session = await self.get_session()
        form = aiohttp.FormData()
        form.add_field("image", image_bytes, filename=f"{name}.png", content_type="image/png")
        form.add_field("type", image_type)
        form.add_field("overwrite", str(overwrite).lower())
        async with session.post(f"{backend_url}/upload/image", data=form) as resp:
            return json.loads(await resp.read())

    async def track_task(self, backend_url: str, task_id: str, client_id: str, timeout: int = 1800) -> None:
        """Follow a prompt over the ``/ws`` socket until it finishes."""
        session = await self.get_session()
        ws_url = f"{backend_url}/ws?clientId={client_id}"
        start_time = time.time()

        ws_timeout = getattr(aiohttp, "ClientWSTimeout", None)
        ws_kwargs = {"timeout": ws_timeout(ws_close=30.0)} if ws_timeout else {"timeout": 30.0}
        async with session.ws_connect(ws_url, **ws_kwargs) as ws:
            logger.info("Tracking task %s on %s", task_id, backend_url)
            async for msg in ws:
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"任务 {task_id} 等待超时")

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")
                    if msg_type == "progress" and data.get("data", {}).get("prompt_id") == task_id:
                        logger.debug("task %s progress %s/%s", task_id[:8],
                                     data["data"].get("value"), data["data"].get("max"))
                    elif msg_type == "executing":
                        exec_data = data.get("data", {})
                        if exec_data.get("node") is None and exec_data.get("prompt_id") == task_id:
                            logger.info("Task %s finished", task_id)
                            break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("WebSocket error while tracking %s: %s", task_id, msg.data)
                    break

    async def get_history(self, backend_url: str, task_id: str) -> dict[str, Any]:
        return await self.request("GET", f"{backend_url}/history/{task_id}")

    async def interrupt(self, backend_url: str) -> str:
        return await self.request("POST", f"{backend_url}/interrupt", as_text=True)

    async def delete_queue(self, backend_url: str, task_ids: list[str]) -> str:
        return await self.request("POST", f"{backend_url}/queue", json_data={"delete": task_ids}, as_text=True)

    async def clear_queue(self, backend_url: str) -> str:
        return await self.request("POST", f"{backend_url}/queue", json_data={"clear": True}, as_text=True)

    async def get_loras(self, backend_url: str) -> list[str]:
        resp = await self.request("GET", f"{backend_url}/object_info/LoraLoader")
        try:
            return resp["LoraLoader"]["input"]["required"]["lora_name"][0]
        except Exception:
            return []

    async def get_checkpoints(self, backend_url: str) -> list[str]:
        resp = await self.request("GET", f"{backend_url}/object_info/CheckpointLoaderSimple")
        try:
            return resp["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        except Exception:
            return []
