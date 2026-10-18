"""Generate golden fixtures from the original nonebot_plugin_comfyui engine.

Run once (and again only if the nonebot engine changes on purpose):

    cd <repo root> && python3 tests/golden/generate_golden.py

It imports the nonebot engine modules directly, bypassing the package
``__init__`` (which needs NoneBot2 and Alconna), with a stub ``nonebot``
module on sys.path. The output, ``tests/golden/cases.json``, is what
``tests/core/test_golden.py`` checks the ported ``comfyui_core`` against.

Every case embeds its own workflow and reflex JSON, so later edits to the
built-in workflow files cannot silently change what the port is measured
against. ``random`` is seeded before each call, so ``randint`` actions and
the implicit seed are reproducible as long as the port draws random numbers
in the same order.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PKG_DIR = ROOT / "nonebot_plugin_comfyui"
WF_DIR = PKG_DIR / "build_in_wf"
SEED = 20261018

sys.path.insert(0, str(HERE / "stubs"))
sys.path.insert(0, str(ROOT))

# Register the package without executing its __init__ (which imports NoneBot).
pkg = types.ModuleType("nonebot_plugin_comfyui")
pkg.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
sys.modules["nonebot_plugin_comfyui"] = pkg

from nonebot_plugin_comfyui.services.workflow_engine import workflow_engine  # noqa: E402
from nonebot_plugin_comfyui.services.lora_engine import lora_engine  # noqa: E402
from nonebot_plugin_comfyui.parser import rebuild_parser  # noqa: E402
from nonebot_plugin_comfyui.exceptions import ArgsError  # noqa: E402


def load_pair(name: str) -> tuple[dict, dict]:
    api = json.loads((WF_DIR / f"{name}.json").read_text(encoding="utf-8"))
    reflex = json.loads((WF_DIR / f"{name}_reflex.json").read_text(encoding="utf-8"))
    return api, reflex


BASE_PARAMS = {
    "prompt": "1girl, solo, masterpiece",
    "negative_prompt": "lowres, bad anatomy",
    "width": 832,
    "height": 1216,
    "seed": 123456,
    "steps": 28,
    "cfg_scale": 7.0,
    "denoise_strength": 1.0,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "batch_size": 1,
    "model": "",
    "override": False,
    "override_ng": False,
    "loras": [],
}

IMAGES = [{"name": "upload_a.png", "subfolder": "", "type": "input"},
          {"name": "upload_b.png", "subfolder": "", "type": "input"}]


def reflex_case(name: str, api: dict, reflex: dict, params: dict, *,
                args: dict | None = None, init_images: list | None = None,
                backend_index: int = 0) -> dict:
    run_params = dict(params)
    run_params["args"] = argparse.Namespace(**args) if args is not None else None
    random.seed(SEED)
    out = workflow_engine.apply_reflex(
        raw_api_json=copy.deepcopy(api), reflex_json=reflex, params=run_params,
        init_images=init_images, backend_index=backend_index)
    return {
        "kind": "apply_reflex",
        "name": name,
        "api_json": api,
        "reflex_json": reflex,
        "params": params,
        "args": args,
        "init_images": init_images,
        "backend_index": backend_index,
        "expected": out,
    }


def parser_case(name: str, wf: str, reg_args: dict | None, tokens: list[str]) -> dict:
    ns = rebuild_parser(wf, reg_args).parse_args(tokens)
    return {"kind": "parser", "name": name, "wf": wf, "reg_args": reg_args,
            "tokens": tokens, "expected": vars(ns)}


def dims_case(name: str, **kw) -> dict:
    case = {"kind": "dimensions", "name": name, "inputs": kw}
    try:
        w, h = workflow_engine.calculate_dimensions(**kw)
        case["expected"] = [w, h]
    except ArgsError as e:
        case["expected_error"] = "ArgsError"
    return case


def lora_extract_case(name: str, prompt: str) -> dict:
    clean, loras = lora_engine.extract_loras_from_prompt(prompt)
    return {"kind": "extract_loras", "name": name, "prompt": prompt,
            "expected": {"prompt": clean, "loras": [list(x) for x in loras]}}


def lora_apply_case(name: str, api: dict, lora_config, loras: list) -> dict:
    out = lora_engine.apply_loras_to_api_json(copy.deepcopy(api), lora_config,
                                              [tuple(x) for x in loras])
    return {"kind": "apply_loras", "name": name, "api_json": api,
            "lora_config": lora_config, "loras": loras, "expected": out}


def main() -> None:
    cases: list[dict] = []
    txt2img, txt2img_rf = load_pair("txt2img")
    img2img, img2img_rf = load_pair("img2img")
    cn, cn_rf = load_pair("controlnet以图绘图")
    upscale, upscale_rf = load_pair("超分")
    tagger, tagger_rf = load_pair("打标")
    music, music_rf = load_pair("music_gen")
    audio, audio_rf = load_pair("stale_audio")

    # --- built-in pairs, as shipped --------------------------------------
    cases.append(reflex_case("txt2img/base", txt2img, txt2img_rf, BASE_PARAMS))
    cases.append(reflex_case("txt2img/batch_and_model", txt2img, txt2img_rf,
                             {**BASE_PARAMS, "batch_size": 2, "model": "x.safetensors",
                              "steps": 30, "cfg_scale": 5.5, "sampler": "euler",
                              "scheduler": "normal", "denoise_strength": 0.7}))
    cases.append(reflex_case("txt2img/no_seed_in_params", txt2img, txt2img_rf,
                             {k: v for k, v in BASE_PARAMS.items() if k != "seed"}))
    cases.append(reflex_case("img2img/with_image", img2img, img2img_rf,
                             {**BASE_PARAMS, "denoise_strength": 0.5}, init_images=IMAGES[:1]))
    cases.append(reflex_case("img2img/no_image", img2img, img2img_rf, BASE_PARAMS))
    cases.append(reflex_case("controlnet/reg_args_default", cn, cn_rf, BASE_PARAMS,
                             args={"strength": 0.8}, init_images=IMAGES[:1]))
    cases.append(reflex_case("controlnet/reg_args_custom", cn, cn_rf,
                             {**BASE_PARAMS, "prompt": "cat"}, args={"strength": 0.35},
                             init_images=IMAGES))
    cases.append(reflex_case("controlnet/override_flags", cn, cn_rf,
                             {**BASE_PARAMS, "override": True, "override_ng": True},
                             args={"strength": 0.8}, init_images=IMAGES[:1]))
    cases.append(reflex_case("controlnet/args_missing_dest", cn, cn_rf, BASE_PARAMS,
                             args={"other": 1}, init_images=IMAGES[:1]))
    cases.append(reflex_case("upscale/default", upscale, upscale_rf, BASE_PARAMS,
                             args={"text": "2"}, init_images=IMAGES[:1]))
    cases.append(reflex_case("upscale/custom", upscale, upscale_rf, BASE_PARAMS,
                             args={"text": "4"}, init_images=IMAGES[:1]))
    cases.append(reflex_case("tagger/text_output", tagger, tagger_rf, BASE_PARAMS,
                             init_images=IMAGES[:1]))
    cases.append(reflex_case("music_gen/seconds", music, music_rf,
                             {**BASE_PARAMS, "prompt": "lofi beat"}, args={"seconds": 45}))
    cases.append(reflex_case("stale_audio/append_negative_Text", audio, audio_rf,
                             {**BASE_PARAMS, "prompt": "rain", "negative_prompt": "noise"},
                             args={"audio_length": 12}))
    cases.append(reflex_case("stale_audio/override_ng", audio, audio_rf,
                             {**BASE_PARAMS, "override_ng": True}, args={"audio_length": 12}))

    # --- synthetic reflex variants for every documented key -----------------
    rf = {**txt2img_rf, "checkpoint": 4, "seed": [3], "image_size": "5"}
    cases.append(reflex_case("synthetic/roles_list_str_checkpoint", txt2img, rf,
                             {**BASE_PARAMS, "model": "anima.safetensors"}))
    cases.append(reflex_case("synthetic/checkpoint_without_model", txt2img, rf, BASE_PARAMS))

    rf = {**txt2img_rf, "prompt": {"6": {"override": {"text": "append_prompt"}}},
          "negative_prompt": {"7": {"override": {"text": "append_negative_prompt"}}}}
    cases.append(reflex_case("synthetic/append_actions", txt2img, rf, BASE_PARAMS))
    cases.append(reflex_case("synthetic/append_actions_override", txt2img, rf,
                             {**BASE_PARAMS, "override": True, "override_ng": True}))

    rf = {**txt2img_rf, "prompt": {"6": {"override": {"text": "replace_prompt"}}}}
    api = copy.deepcopy(txt2img)
    api["6"]["inputs"]["text"] = "score_9, {prompt}, hires"
    cases.append(reflex_case("synthetic/replace_prompt", api, rf, BASE_PARAMS))

    rf = {**txt2img_rf, "misc": {"3": {"override": {"seed": "randint"}},
                                 "5": {"override": {"width": "upscale", "height": "upscale_2"}},
                                 "4": {"override": {"ckpt_name": "value_other.ckpt_str"}}},
          "misc2": {"3": {"override": {"steps": "value_12_int", "cfg": "value_3.5_float"}}}}
    cases.append(reflex_case("synthetic/randint_upscale_value", txt2img, rf, BASE_PARAMS))

    rf = {**img2img_rf, "second": {"10": {"override": {"image": "image_1"}}}}
    cases.append(reflex_case("synthetic/image_n_action", img2img, rf, BASE_PARAMS,
                             init_images=IMAGES))
    cases.append(reflex_case("synthetic/image_n_action_out_of_range", img2img, rf,
                             BASE_PARAMS, init_images=IMAGES[:1]))

    rf = {**txt2img_rf, "reflex": {"0": {"4": {"ckpt_name": "be0.safetensors"}},
                                   "1": {"4": {"ckpt_name": "be1.safetensors"},
                                         "3": {"steps": 99}, "77": {"x": 1}}}}
    cases.append(reflex_case("synthetic/backend_reflex_0", txt2img, rf, BASE_PARAMS, backend_index=0))
    cases.append(reflex_case("synthetic/backend_reflex_1", txt2img, rf, BASE_PARAMS, backend_index=1))
    cases.append(reflex_case("synthetic/backend_reflex_none", txt2img, rf, BASE_PARAMS, backend_index=5))

    reg = {"3": {"args": [
        {"name_or_flags": ["-st"], "type": "int", "dest": "my_steps", "default": 10,
         "dest_to_value": {"steps": "my_steps"}},
        {"name_or_flags": ["-q"], "type": "str", "dest": "quality",
         "preset": {"hi": "dpmpp_2m_sde", "lo": "euler"}, "dest_to_value": {"sampler_name": "quality"}},
        {"name_or_flags": ["-dn"], "type": "float", "dest": "denoise"},
    ]}, "99": {"args": [{"name_or_flags": ["-x"], "type": "int", "dest": "x", "default": 1}]}}
    rf = {**txt2img_rf, "reg_args": reg}
    cases.append(reflex_case("synthetic/reg_args_preset_rename", txt2img, rf, BASE_PARAMS,
                             args={"my_steps": 15, "quality": "hi", "denoise": 0.3, "x": 2}))
    cases.append(reflex_case("synthetic/reg_args_preset_miss", txt2img, rf, BASE_PARAMS,
                             args={"my_steps": 15, "quality": "ddim", "denoise": "0.6"}))
    cases.append(reflex_case("synthetic/reg_args_no_args", txt2img, rf, BASE_PARAMS))

    rf = {**txt2img_rf, "lora": 4}
    cases.append(reflex_case("synthetic/lora_int_in_reflex", txt2img, rf,
                             {**BASE_PARAMS, "loras": [["styleA", 0.8], ["styleB", 0.5]]}))
    cases.append(reflex_case("synthetic/lora_params_but_no_key", txt2img, txt2img_rf,
                             {**BASE_PARAMS, "loras": [["styleA", 0.8]]}))

    # --- lora engine -------------------------------------------------------
    lora_wf = copy.deepcopy(txt2img)
    lora_wf["10"] = {"inputs": {"lora_name": "base.safetensors", "strength_model": 1.0,
                                "strength_clip": 1.0, "model": ["4", 0], "clip": ["4", 1]},
                     "class_type": "LoraLoader", "_meta": {"title": "Load LoRA"}}
    lora_wf["3"]["inputs"]["model"] = ["10", 0]
    lora_wf["6"]["inputs"]["clip"] = ["10", 1]
    lora_wf["7"]["inputs"]["clip"] = ["10", 1]
    cases.append(lora_apply_case("lora/int_single", lora_wf, 10, [["a", 0.7]]))
    cases.append(lora_apply_case("lora/int_chain", lora_wf, 10, [["a", 0.7], ["b", 1.0], ["c", 0.2]]))
    cases.append(lora_apply_case("lora/int_as_str", lora_wf, "10", [["a", 0.7], ["b", 1.0]]))
    list_cfg = [{"20": {"from": {"model": 4, "clip": 4}, "to": {"model": [3], "clip": [6, 7]}}}]
    cases.append(lora_apply_case("lora/list_single", txt2img, list_cfg, [["a", 0.9]]))
    cases.append(lora_apply_case("lora/list_chain", txt2img, list_cfg, [["a", 0.9], ["b", 0.4]]))
    cases.append(lora_apply_case("lora/empty", txt2img, 4, []))

    for name, prompt in [
        ("plain", "1girl, solo"),
        ("one", "1girl, <lora:styleA:0.8>, solo"),
        ("two_and_default_weight", "<lora:a>, 1girl,<lora:b:1.2> , solo,"),
        ("bad_weight", "x, <lora:c:abc>"),
        ("no_commas", "1girl <lora:a:1> solo"),
    ]:
        cases.append(lora_extract_case(f"extract/{name}", prompt))

    # --- dimensions --------------------------------------------------------
    cases.append(dims_case("dims/defaults"))
    cases.append(dims_case("dims/explicit", width=512, height=768))
    cases.append(dims_case("dims/width_only", width=640))
    for s in ["p", "l", "s", "lp", "ll", "ls", "up", "ul", "960x1920", "bogus", "axb"]:
        cases.append(dims_case(f"dims/shape_{s}", shape=s, width=100, height=200))
    for ar in ["16:9", "9:16", "1:1", "4:3", "21:9", "3:2"]:
        cases.append(dims_case(f"dims/ar_{ar.replace(':', '_')}", accept_ratio=ar))
    cases.append(dims_case("dims/ar_bad", accept_ratio="a:b"))
    cases.append(dims_case("dims/ar_no_colon", accept_ratio="169", width=300, height=400))
    cases.append(dims_case("dims/shape_beats_ar", shape="p", accept_ratio="16:9"))

    # --- parser ------------------------------------------------------------
    cases.append(parser_case("parser/base_defaults", "txt2img", None, ["1girl", "solo"]))
    cases.append(parser_case("parser/base_many", "txt2img", None,
                             ["1girl", "solo", "-u", "lowres", "bad", "-ar", "16:9", "-s", "42",
                              "-t", "30", "-cfg", "6.5", "-n", "0.6", "-高", "1216", "-宽", "832",
                              "-o", "-on", "-wf", "flux", "-sp", "euler", "-sch", "normal",
                              "-b", "2", "-bc", "3", "-m", "x.ckpt", "-be", "1", "-f", "-gif",
                              "-con", "-r", "p", "-sil", "-notice", "-nt", "-llm", "2"]))
    cases.append(parser_case("parser/chinese_aliases", "txt2img", None,
                             ["猫", "-负面", "差", "-画面比例", "4:3", "-种子", "7", "-步数", "20",
                              "-去噪", "0.5", "-工作流", "img2img", "-采样器", "DPM++ 2M",
                              "-调度器", "Karras", "-数量", "1", "-批数", "2", "-模型", "m",
                              "-后端", "http://x:1", "-转发", "-并发", "-分辨率", "960x1920",
                              "-静默", "-通知", "-不翻译", "-llm预设", "3"]))
    cases.append(parser_case("parser/attached_values", "txt2img", None,
                             ["1girl", "-高1216", "-宽832", "-s200", "-t28"]))
    cases.append(parser_case("parser/dynamic_default_wf", "controlnet以图绘图", cn_rf["reg_args"],
                             ["a", "b", "-cn", "0.5"]))
    cases.append(parser_case("parser/dynamic_reg_default", "超分", upscale_rf["reg_args"], []))
    cases.append(parser_case("parser/dynamic_conflict_replaces_base", "x",
                             {"3": {"args": [{"name_or_flags": ["-t"], "type": "int",
                                              "dest": "temperature", "default": 5}]}},
                             ["hello", "-t", "9"]))
    cases.append(parser_case("parser/dynamic_preset_forces_str", "x",
                             {"3": {"args": [{"name_or_flags": ["-q"], "type": "int", "dest": "q",
                                              "preset": {"hi": 1}, "default": "lo"}]}},
                             ["-q", "hi"]))
    cases.append(parser_case("parser/dynamic_bool_flag", "x",
                             {"3": {"args": [{"name_or_flags": ["-flag"], "dest": "flag",
                                              "action": "store_true"}]}},
                             ["-flag"]))

    out = HERE / "cases.json"
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {out}")


if __name__ == "__main__":
    main()
