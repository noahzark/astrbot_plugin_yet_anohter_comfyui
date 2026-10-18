"""Per-user cooldown and daily quota (in memory)."""
from __future__ import annotations

import datetime
import time


class RateLimiter:
    def __init__(self, cd_seconds: int, day_limit: int) -> None:
        self.cd_seconds = cd_seconds
        self.day_limit = day_limit
        self.cd_records: dict[str, float] = {}
        self.daily_records: dict[str, int] = {}
        self.workflow_daily_records: dict[str, dict[str, int]] = {}

    @staticmethod
    def get_today_key(user_id: str) -> str:
        today_date = datetime.datetime.now().strftime("%Y-%m-%d")
        return f"{user_id}:{today_date}"

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
        """Add ``amount`` to today's count. Returns ``(message, limit_reached)``."""
        if is_superuser:
            return "", False

        new_total = self.daily_records.get(daily_key, 0) + amount
        self.daily_records[daily_key] = new_total

        if new_total >= self.day_limit:
            msg = f"今天你的使用量已达上限，最多可以使用 {self.day_limit} 次。"
            reached = True
        else:
            msg = f"今天已使用 {new_total} 次，还能使用 {self.day_limit - new_total} 次"
            reached = False

        if wf_name:
            per_wf = self.workflow_daily_records.setdefault(daily_key, {})
            wf_current = per_wf.get(wf_name, 0) + amount
            per_wf[wf_name] = wf_current
            if wf_limit and wf_current > wf_limit:
                msg = f"已超过该工作流每日调用限制（上限 {wf_limit} 次）"
                reached = True

        return msg, reached

    def revert_daily(self, daily_key: str, amount: int) -> None:
        if daily_key in self.daily_records:
            self.daily_records[daily_key] = max(0, self.daily_records[daily_key] - amount)

    def get_workflow_daily_usage(self, daily_key: str, wf_name: str) -> int:
        return self.workflow_daily_records.get(daily_key, {}).get(wf_name, 0)
