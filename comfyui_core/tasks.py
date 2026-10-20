"""Per-user task history for ``get-task``, kept small enough for a KV store."""
from __future__ import annotations

import time
from typing import Any

MAX_PER_USER = 50


class TaskRecords:
    def __init__(self, max_per_user: int = MAX_PER_USER) -> None:
        self.max_per_user = max_per_user
        self.by_user: dict[str, list[dict[str, Any]]] = {}

    def record(self, user_id: str, task_id: str, workflow: str, backend_index: int, status: str = "完成") -> None:
        tasks = self.by_user.setdefault(str(user_id), [])
        tasks.append({"id": task_id, "workflow": workflow, "backend": backend_index, "status": status, "time": time.time()})
        del tasks[:-self.max_per_user]

    def recent(self, user_id: str, start: int = 0, end: int = 10) -> list[dict[str, Any]]:
        """Newest first, sliced like the nonebot ``get-task 0-10``."""
        return list(reversed(self.by_user.get(str(user_id), [])))[start:end]

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return {u: list(t) for u, t in self.by_user.items()}

    def restore(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self.by_user = {
            str(u): [t for t in tasks if isinstance(t, dict) and "id" in t][-self.max_per_user:]
            for u, tasks in data.items()
            if isinstance(tasks, list)
        }
