import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "tests" / "golden" / "cases.json"
BUILT_IN_WF = ROOT / "nonebot_plugin_comfyui" / "build_in_wf"


def golden_cases(kind: str) -> list[dict]:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [c for c in cases if c["kind"] == kind]


def golden_ids(cases: list[dict]) -> list[str]:
    return [c["name"] for c in cases]


@pytest.fixture
def built_in_wf_dir() -> Path:
    return BUILT_IN_WF
