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
    APIJsonError,
    ComfyuiBackendConnectionError,
    GetResultError,
    InterruptError,
    NoAvailableBackendError,
    NoAvailableBackendForSelectedWorkflow,
    TaskError,
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

    async def get_available_backends(self, allowed: set[int] | None = None) -> tuple[set[int], dict[str, Any]]:
        """Probe the backends (only the ``allowed`` indices, if given)."""
        backend_dict: dict[str, Any] = {}
        available_indices: set[int] = set()

        candidates = [(i, u) for i, u in enumerate(self.backend_urls) if allowed is None or i in allowed]
        results = await asyncio.gather(
            *(self.get_backend_work_status(url) for _, url in candidates),
            return_exceptions=True,
        )
        for (idx, url), res in zip(candidates, results):
            if isinstance(res, Exception):
                logger.warning("Backend [%s] %s is offline: %s", idx, url, res)
            else:
                backend_dict[url] = res
                available_indices.add(idx)
        return available_indices, backend_dict

    async def select_backend(
        self,
        selected_backend: str | None = None,
        allowed: set[int] | None = None,
        workflow: str = "",
    ) -> tuple[str, int, dict[str, Any]]:
        """Pick the backend with the shortest queue, or the one asked for.

        ``allowed`` is a workflow's ``available`` list: a selected index
        outside it falls back to automatic selection among the allowed ones.
        Returns ``(url, index, status)``; index is -1 for a URL that is not
        in the configured list.
        """
        if selected_backend:
            if selected_backend.isdigit():
                idx = int(selected_backend)
                if 0 <= idx < len(self.backend_urls) and (allowed is None or idx in allowed):
                    return self.backend_urls[idx], idx, {}
                if allowed is not None:
                    logger.info("Backend %s cannot run workflow %s, choosing among %s", idx, workflow, sorted(allowed))
            elif selected_backend in self.backend_urls:
                idx = self.backend_urls.index(selected_backend)
                if allowed is None or idx in allowed:
                    return selected_backend, idx, {}
            else:
                return selected_backend, -1, {}

        available_indices, backend_dict = await self.get_available_backends(allowed)
        if not available_indices:
            if allowed is not None:
                raise NoAvailableBackendForSelectedWorkflow(
                    f"工作流 {workflow} 只能在后端 {sorted(allowed)} 上运行，但这些后端当前都不可用"
                )
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
        try:
            async with session.post(f"{backend_url}/upload/image", data=form) as resp:
                body = await resp.read()
                status = resp.status
        except Exception as e:
            raise ComfyuiBackendConnectionError(f"上传图片到后端失败 [{backend_url}]: {e}")
        try:
            if status >= 400:
                raise ValueError(f"HTTP {status}")
            res = json.loads(body)
            if not isinstance(res, dict) or "name" not in res:
                raise ValueError("unexpected response")
        except ValueError as e:
            snippet = body[:200].decode("utf-8", "replace").strip()
            raise ComfyuiBackendConnectionError(f"上传图片到后端失败 [{backend_url}]: {e} {snippet}".rstrip())
        return res

    def _ws_kwargs(self) -> dict[str, Any]:
        ws_timeout = getattr(aiohttp, "ClientWSTimeout", None)
        if ws_timeout:
            return {"timeout": ws_timeout(ws_close=30.0)}
        return {"timeout": 30.0}

    async def open_ws(self, backend_url: str, client_id: str) -> aiohttp.ClientWebSocketResponse | None:
        """Open the progress socket. Returns ``None`` when the socket is
        unavailable so callers can fall back to polling ``/history``."""
        session = await self.get_session()
        ws_url = f"{backend_url}/ws?clientId={client_id}"
        try:
            return await session.ws_connect(ws_url, **self._ws_kwargs())
        except Exception as e:
            logger.warning("WebSocket to %s unavailable, will poll history: %s", backend_url, e)
            return None

    async def wait_for_task(
        self,
        backend_url: str,
        task_id: str,
        ws: aiohttp.ClientWebSocketResponse | None,
        timeout: float = 1800,
        recv_timeout: float = 30,
        poll_interval: float = 2,
    ) -> dict[str, Any]:
        """Block until ``task_id`` finishes and return its history entry.

        Progress comes over ``ws`` when there is one. Every receive has a
        timeout; when it fires (or the socket is missing) ``/history`` is
        polled so a dropped socket never hangs the task. Errors and
        interrupts reported by ComfyUI raise :class:`TaskError` /
        :class:`InterruptError` instead of waiting forever.
        """
        deadline = time.monotonic() + timeout

        async def poll_once() -> dict[str, Any] | None:
            hist = await self.get_history(backend_url, task_id)
            entry = hist.get(task_id) if isinstance(hist, dict) else None
            if not entry:
                return None
            status = entry.get("status", {}) or {}
            if status.get("status_str") == "error":
                raise TaskError(f"任务 {task_id} 执行出错: {_history_error_text(status)}")
            if status.get("completed") or entry.get("outputs"):
                return entry
            return None

        try:
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"任务 {task_id} 等待超时")

                if ws is None or ws.closed:
                    entry = await poll_once()
                    if entry is not None:
                        return entry
                    await asyncio.sleep(poll_interval)
                    continue

                try:
                    msg = await ws.receive(timeout=min(recv_timeout, max(deadline - time.monotonic(), 0.1)))
                except asyncio.TimeoutError:
                    entry = await poll_once()
                    if entry is not None:
                        return entry
                    continue

                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSE):
                    logger.warning("WebSocket closed while tracking %s, polling history", task_id)
                    ws = None
                    continue
                if msg.type == aiohttp.WSMsgType.ERROR:
                    logger.warning("WebSocket error while tracking %s: %s", task_id, ws.exception())
                    ws = None
                    continue
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue

                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                msg_type = data.get("type")
                payload = data.get("data", {}) or {}
                if payload.get("prompt_id") not in (None, task_id):
                    continue

                if msg_type == "progress":
                    logger.debug("task %s progress %s/%s", task_id[:8], payload.get("value"), payload.get("max"))
                elif msg_type == "execution_error":
                    raise TaskError(f"任务 {task_id} 执行出错: {_ws_error_text(payload)}")
                elif msg_type == "execution_interrupted":
                    raise InterruptError(f"任务 {task_id} 已被中断")
                elif msg_type == "execution_success" or (
                    msg_type == "executing" and payload.get("node") is None and payload.get("prompt_id") == task_id
                ):
                    logger.info("Task %s finished", task_id)
                    entry = await poll_once()
                    if entry is not None:
                        return entry
                    # History can lag the socket by a moment.
                    ws = None
        finally:
            if ws is not None and not ws.closed:
                try:
                    await ws.close()
                except Exception:  # pragma: no cover - best effort
                    pass

    async def submit_and_wait(
        self,
        backend_url: str,
        prompt_json: dict,
        client_id: str,
        timeout: float = 1800,
    ) -> tuple[str, dict[str, Any]]:
        """POST a prompt and wait for it. Returns ``(task_id, history_entry)``.

        The socket is opened *before* the POST so no progress message can be
        missed between the two.
        """
        ws = await self.open_ws(backend_url, client_id)
        try:
            task_id = await self.post_prompt(backend_url, prompt_json, client_id)
        except Exception:
            if ws is not None:
                await ws.close()
            raise
        entry = await self.wait_for_task(backend_url, task_id, ws, timeout=timeout)
        return task_id, entry

    async def track_task(self, backend_url: str, task_id: str, client_id: str, timeout: int = 1800) -> None:
        """Follow an already-posted prompt until it finishes."""
        ws = await self.open_ws(backend_url, client_id)
        await self.wait_for_task(backend_url, task_id, ws, timeout=timeout)

    @staticmethod
    def view_url(backend_url: str, meta: dict[str, Any]) -> str:
        fn = meta["filename"]
        sub = meta.get("subfolder", "")
        kind = meta.get("type", "")
        return f"{backend_url}/view?filename={fn}&subfolder={sub}&type={kind}"

    async def download(self, backend_url: str, meta: dict[str, Any]) -> bytes:
        data = await self.request("GET", self.view_url(backend_url, meta), as_json=False)
        if not isinstance(data, (bytes, bytearray)):
            raise GetResultError(f"下载 {meta.get('filename')} 失败")
        return bytes(data)

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


def _ws_error_text(payload: dict[str, Any]) -> str:
    node = payload.get("node_type") or payload.get("node_id") or "?"
    exc = payload.get("exception_message") or payload.get("exception_type") or "unknown error"
    return f"[{node}] {exc}"


def _history_error_text(status: dict[str, Any]) -> str:
    for name, detail in status.get("messages", []) or []:
        if name == "execution_error" and isinstance(detail, dict):
            return _ws_error_text(detail)
    return "unknown error"
