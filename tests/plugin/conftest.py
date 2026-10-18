"""Plugin-level tests: run main.py inside a real ``astrbot`` install.

These need ``pip install astrbot`` (see the ``plugin`` CI job) and are
skipped when it is missing, so the core suite stays light.
"""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("astrbot")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from plugin_env import ROOT, AstrBotConfig, plugin_main  # noqa: E402
from fake_comfy import FakeComfy  # noqa: E402


@pytest.fixture
async def server():
    s = FakeComfy()
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def plugin_factory(tmp_path, monkeypatch):
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    async def make(server, **overrides):
        cfg = AstrBotConfig(config_path=str(tmp_path / "cfg.json"), schema=schema)
        cfg["backend"]["urls"] = [server.url]
        cfg["backend"]["timeout"] = 2
        cfg["backend"]["task_timeout"] = 10
        cfg["audit"]["site"] = server.url  # never reach the real public tagger from tests
        for section, values in overrides.items():
            cfg[section].update(values)
        monkeypatch.setattr(plugin_main.YetAnotherComfyUI, "resolve_data_dir", lambda self: tmp_path / "data")
        plugin = plugin_main.YetAnotherComfyUI(context=None, config=cfg)
        await plugin.initialize()
        return plugin

    return make
