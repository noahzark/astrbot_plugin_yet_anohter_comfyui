"""Shared paths and golden-case loader for the core tests."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "cases.json"
BUILT_IN_WF = ROOT / "build_in_wf"


def golden_cases(kind: str) -> list[dict]:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [c for c in cases if c["kind"] == kind]


def golden_ids(cases: list[dict]) -> list[str]:
    return [c["name"] for c in cases]
