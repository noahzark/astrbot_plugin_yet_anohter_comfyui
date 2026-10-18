"""Per-user cooldown and daily quota.

Cooldowns live in memory only. Daily counts can be saved and restored by
the adapter (``snapshot`` / ``restore``) so a restart keeps everyone's
quota; only today's counts are kept.
"""
from __future__ import annotations

import datetime
import time
from typing import Any


def today() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


class RateLimiter:
    def __init__(self, cd_seconds: int, day_limit: int) -> None:
        self.cd_seconds = cd_seconds
        self.day_limit = day_limit
        self.cd_records: dict[str, float] = {}
        self.daily_records: dict[str, int] = {}
        self.workflow_daily_records: dict[str, dict[str, int]] = {}

    @staticmethod
    def get_today_key(user_id: str) -> str:
        return f"{user_id}:{today()}"

    def check_cd(self, user_id: str, is_superuser: bool = False) -> tuple[bool, int]:
        """Returns ``(in_cooldown, seconds_left)``."""
        if is_superuser or self.cd_seconds <= 0:
            return False, 0

        delta = time.time() - self.cd_records.get(user_id, 0.0)
        if delta < self.cd_seconds:
            return True, int(self.cd_seconds - delta)
        return False, 0

    def update_cd(self, user_id: str) -> None:
        self.cd_records[user_id] = time.time()

    def check_daily_limit(
        self,
        daily_key: str,
        amount: int,
        is_superuser: bool = False,
        wf_name: str | None = None,
        wf_limit: int | None = None,
    ) -> tuple[str, bool]:
        """Charge ``amount`` images (and one call of ``wf_name``) to today.

        Returns ``(message, limit_reached)``. A rejected request is not
        charged, and a request that lands exactly on the limit is allowed.
        """
        if is_superuser:
            return "", False

        used = self.daily_records.get(daily_key, 0)
        if used + amount > self.day_limit:
            left = max(self.day_limit - used, 0)
            return f"今天你的使用量已达上限，最多可以使用 {self.day_limit} 次，剩余 {left} 次。", True

        if wf_name and wf_limit:
            wf_used = self.workflow_daily_records.get(daily_key, {}).get(wf_name, 0)
            if wf_used + 1 > int(wf_limit):
                return f"已超过该工作流每日调用限制（上限 {wf_limit} 次）", True

        self.daily_records[daily_key] = used + amount
        if wf_name:
            per_wf = self.workflow_daily_records.setdefault(daily_key, {})
            per_wf[wf_name] = per_wf.get(wf_name, 0) + 1
        return f"今天已使用 {used + amount} 次，还能使用 {self.day_limit - used - amount} 次", False

    def revert_daily(self, daily_key: str, amount: int, wf_name: str | None = None) -> None:
        """Refund a charge made by :meth:`check_daily_limit`."""
        if daily_key in self.daily_records:
            self.daily_records[daily_key] = max(0, self.daily_records[daily_key] - amount)
        per_wf = self.workflow_daily_records.get(daily_key, {})
        if wf_name and wf_name in per_wf:
            per_wf[wf_name] = max(0, per_wf[wf_name] - 1)

    def get_workflow_daily_usage(self, daily_key: str, wf_name: str) -> int:
        return self.workflow_daily_records.get(daily_key, {}).get(wf_name, 0)

    # ------------------------------------------------------------ persistence

    def snapshot(self) -> dict[str, Any]:
        """Today's counts as plain JSON data; older days are dropped."""
        suffix = f":{today()}"
        self.daily_records = {k: v for k, v in self.daily_records.items() if k.endswith(suffix)}
        self.workflow_daily_records = {k: v for k, v in self.workflow_daily_records.items() if k.endswith(suffix)}
        return {"daily": dict(self.daily_records), "workflows": {k: dict(v) for k, v in self.workflow_daily_records.items()}}

    def restore(self, data: Any) -> None:
        """Load a :meth:`snapshot`; anything malformed or from another day is ignored."""
        if not isinstance(data, dict):
            return
        suffix = f":{today()}"
        for key, value in (data.get("daily") or {}).items():
            if str(key).endswith(suffix) and isinstance(value, int):
                self.daily_records[str(key)] = value
        for key, per_wf in (data.get("workflows") or {}).items():
            if str(key).endswith(suffix) and isinstance(per_wf, dict):
                self.workflow_daily_records[str(key)] = {
                    str(k): v for k, v in per_wf.items() if isinstance(v, int)
                }
