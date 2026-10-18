"""comfyui_core must stay free of chat-framework imports."""
import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "comfyui_core"
FORBIDDEN = ("nonebot", "astrbot", "tqdm", "nonebot_plugin_alconna", "nonebot_plugin_htmlrender")


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


@pytest.mark.parametrize("path", sorted(CORE.rglob("*.py")), ids=lambda p: str(p.relative_to(CORE)))
def test_no_framework_imports(path):
    bad = [m for m in _imports(path) if m.split(".")[0] in FORBIDDEN]
    assert not bad, f"{path.name} imports {bad}"


def test_package_imports_cleanly():
    import comfyui_core
    import comfyui_core.audit
    import comfyui_core.cli
    import comfyui_core.client
    import comfyui_core.limiter
    import comfyui_core.lora
    import comfyui_core.workflow

    assert comfyui_core.__version__
