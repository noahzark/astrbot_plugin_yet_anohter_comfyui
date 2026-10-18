"""Minimal stand-in for the ``nonebot`` package.

Only used by ``tests/golden/generate_golden.py`` so that the original
nonebot_plugin_comfyui engine modules can be imported without NoneBot2
installed. It is deliberately tiny: a logger and a driver whose config is
empty, so every plugin setting falls back to its default.
"""
import logging
from types import SimpleNamespace

logger = logging.getLogger("nonebot-stub")


class _DriverConfig:
    superusers: set = set()

    def model_dump(self) -> dict:
        return {}


def get_driver():
    return SimpleNamespace(config=_DriverConfig())


def get_bot():  # pragma: no cover - never called by the engine modules
    raise RuntimeError("no bot in the golden generator")
