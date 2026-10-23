"""The engine must reproduce the nonebot engine byte for byte.

Fixtures in ``tests/golden/cases.json`` were generated from the original
nonebot engine. The generator and the nonebot package were removed in 1.0.0;
both are in git history (``tests/golden/generate_golden.py`` before that).
The engine is now the ``comfyui_reflex`` library, so these cases also guard
against behaviour changes when its pinned version is bumped. Edited by hand
for comfyui-reflex 0.1.0: the auto-inserted LoRA node title, and ``-ar``
without a colon is now an error.
"""
import argparse
import json
import random

import pytest

from comfyui_core import CoreSettings
from comfyui_core.cli import build_parser
from comfyui_core.exceptions import ArgsError
from comfyui_core.lora import apply_loras_to_api_json, extract_loras_from_prompt
from comfyui_core.workflow import apply_reflex, calculate_dimensions

from golden_util import golden_cases, golden_ids

SEED = 20261018


def normalize(obj):
    """JSON round trip so tuples and lists compare equal."""
    return json.loads(json.dumps(obj, ensure_ascii=False))


REFLEX_CASES = golden_cases("apply_reflex")


@pytest.mark.parametrize("case", REFLEX_CASES, ids=golden_ids(REFLEX_CASES))
def test_apply_reflex(case):
    params = dict(case["params"])
    params["args"] = argparse.Namespace(**case["args"]) if case["args"] is not None else None
    random.seed(SEED)
    out = apply_reflex(
        raw_api_json=case["api_json"],
        reflex_json=case["reflex_json"],
        params=params,
        init_images=case["init_images"],
        backend_index=case["backend_index"],
    )
    assert normalize(out) == case["expected"]


@pytest.mark.parametrize("case", REFLEX_CASES, ids=golden_ids(REFLEX_CASES))
def test_apply_reflex_does_not_mutate_input(case):
    before = json.dumps(case["api_json"], sort_keys=True)
    params = dict(case["params"])
    params["args"] = argparse.Namespace(**case["args"]) if case["args"] is not None else None
    apply_reflex(case["api_json"], case["reflex_json"], params,
                 case["init_images"], case["backend_index"])
    assert json.dumps(case["api_json"], sort_keys=True) == before


LORA_CASES = golden_cases("apply_loras")


@pytest.mark.parametrize("case", LORA_CASES, ids=golden_ids(LORA_CASES))
def test_apply_loras(case):
    out = apply_loras_to_api_json(case["api_json"], case["lora_config"],
                                  [tuple(x) for x in case["loras"]])
    assert normalize(out) == case["expected"]


EXTRACT_CASES = golden_cases("extract_loras")


@pytest.mark.parametrize("case", EXTRACT_CASES, ids=golden_ids(EXTRACT_CASES))
def test_extract_loras(case):
    clean, loras = extract_loras_from_prompt(case["prompt"])
    assert clean == case["expected"]["prompt"]
    assert [list(x) for x in loras] == case["expected"]["loras"]


DIM_CASES = golden_cases("dimensions")


@pytest.mark.parametrize("case", DIM_CASES, ids=golden_ids(DIM_CASES))
def test_calculate_dimensions(case):
    s = CoreSettings()
    kwargs = dict(
        shape_preset=s.shape_preset,
        base_res=s.base_res,
        default_width=s.default_value["width"],
        default_height=s.default_value["height"],
    )
    if "expected_error" in case:
        with pytest.raises(ArgsError):
            calculate_dimensions(**case["inputs"], **kwargs)
    else:
        assert list(calculate_dimensions(**case["inputs"], **kwargs)) == case["expected"]


PARSER_CASES = golden_cases("parser")


@pytest.mark.parametrize("case", PARSER_CASES, ids=golden_ids(PARSER_CASES))
def test_parser(case):
    parser = build_parser(case["wf"], case["reg_args"])
    assert vars(parser.parse_args(case["tokens"])) == case["expected"]
