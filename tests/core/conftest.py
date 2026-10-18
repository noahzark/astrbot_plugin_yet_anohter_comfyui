import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from golden_util import BUILT_IN_WF  # noqa: E402


@pytest.fixture
def built_in_wf_dir() -> Path:
    return BUILT_IN_WF
