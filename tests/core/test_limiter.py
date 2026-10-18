from comfyui_core.limiter import RateLimiter


def test_cooldown():
    rl = RateLimiter(cd_seconds=20, day_limit=50)
    assert rl.check_cd("u1") == (False, 0)
    rl.update_cd("u1")
    in_cd, left = rl.check_cd("u1")
    assert in_cd and 0 <= left <= 20
    assert rl.check_cd("u1", is_superuser=True) == (False, 0)
    assert RateLimiter(cd_seconds=0, day_limit=50).check_cd("u1") == (False, 0)


def test_daily_limit_allows_exactly_the_limit_and_does_not_charge_rejections():
    rl = RateLimiter(cd_seconds=0, day_limit=3)
    key = rl.get_today_key("u1")
    assert rl.check_daily_limit(key, 1) == ("今天已使用 1 次，还能使用 2 次", False)
    assert rl.check_daily_limit(key, 2) == ("今天已使用 3 次，还能使用 0 次", False)
    msg, reached = rl.check_daily_limit(key, 1)
    assert reached and "上限" in msg
    assert rl.daily_records[key] == 3  # the rejected request cost nothing
    rl.revert_daily(key, 1)
    assert rl.daily_records[key] == 2
    assert rl.check_daily_limit(key, 100, is_superuser=True) == ("", False)


def test_batch_larger_than_what_is_left_is_rejected():
    rl = RateLimiter(cd_seconds=0, day_limit=4)
    key = rl.get_today_key("u1")
    rl.check_daily_limit(key, 3)
    assert rl.check_daily_limit(key, 2)[1] is True
    assert rl.check_daily_limit(key, 1)[1] is False


def test_per_workflow_limit_counts_calls():
    rl = RateLimiter(cd_seconds=0, day_limit=100)
    key = rl.get_today_key("u1")
    assert rl.check_daily_limit(key, 4, wf_name="wf", wf_limit=2)[1] is False
    assert rl.check_daily_limit(key, 4, wf_name="wf", wf_limit=2)[1] is False
    msg, reached = rl.check_daily_limit(key, 1, wf_name="wf", wf_limit=2)
    assert reached and "工作流" in msg
    assert rl.get_workflow_daily_usage(key, "wf") == 2
    assert rl.daily_records[key] == 8
    rl.revert_daily(key, 4, wf_name="wf")
    assert rl.get_workflow_daily_usage(key, "wf") == 1


def test_snapshot_round_trip_keeps_only_today():
    rl = RateLimiter(cd_seconds=0, day_limit=10)
    key = rl.get_today_key("u1")
    rl.check_daily_limit(key, 3, wf_name="wf")
    rl.daily_records["u2:2000-01-01"] = 9
    data = rl.snapshot()
    assert data == {"daily": {key: 3}, "workflows": {key: {"wf": 1}}}

    fresh = RateLimiter(cd_seconds=0, day_limit=10)
    fresh.restore({**data, "daily": {**data["daily"], "u3:2000-01-01": 5, "bad": "x"}})
    assert fresh.daily_records == {key: 3}
    assert fresh.get_workflow_daily_usage(key, "wf") == 1
    fresh.restore(None)  # malformed stored data is ignored
    assert fresh.check_daily_limit(key, 8)[1] is True
