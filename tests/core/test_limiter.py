from comfyui_core.limiter import RateLimiter


def test_cooldown():
    rl = RateLimiter(cd_seconds=20, day_limit=50)
    assert rl.check_cd("u1") == (False, 0)
    rl.update_cd("u1")
    in_cd, left = rl.check_cd("u1")
    assert in_cd and 0 <= left <= 20
    assert rl.check_cd("u1", is_superuser=True) == (False, 0)
    assert RateLimiter(cd_seconds=0, day_limit=50).check_cd("u1") == (False, 0)


def test_daily_limit_counts_and_reverts():
    rl = RateLimiter(cd_seconds=0, day_limit=3)
    key = rl.get_today_key("u1")
    assert rl.check_daily_limit(key, 1) == ("今天已使用 1 次，还能使用 2 次", False)
    assert rl.check_daily_limit(key, 1)[1] is False
    msg, reached = rl.check_daily_limit(key, 1)
    assert reached and "上限" in msg
    rl.revert_daily(key, 1)
    assert rl.daily_records[key] == 2
    assert rl.check_daily_limit(key, 100, is_superuser=True) == ("", False)


def test_per_workflow_limit():
    rl = RateLimiter(cd_seconds=0, day_limit=100)
    key = rl.get_today_key("u1")
    rl.check_daily_limit(key, 1, wf_name="wf", wf_limit=1)
    msg, reached = rl.check_daily_limit(key, 1, wf_name="wf", wf_limit=1)
    assert reached and "工作流" in msg
    assert rl.get_workflow_daily_usage(key, "wf") == 2
