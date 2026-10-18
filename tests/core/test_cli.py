import pytest

from comfyui_core.cli import build_api_parser, build_parser, build_queue_parser, tokenize
from comfyui_core.exceptions import ArgsError


def test_tokenize_keeps_quoted_phrases():
    assert tokenize("1girl -u '低质量 文字' -ar 16:9") == ["1girl", "-u", "低质量 文字", "-ar", "16:9"]


def test_tokenize_unbalanced_quote_falls_back_to_split():
    assert tokenize("it's fine") == ["it's", "fine"]


def test_tokenize_empty():
    assert tokenize("   ") == []


def test_bad_value_raises_args_error_not_exit():
    parser = build_parser("txt2img")
    with pytest.raises(ArgsError) as e:
        parser.parse_args(["-s", "notanumber"])
    assert "参数错误" in str(e.value)


def test_help_flag_raises_args_error_with_usage():
    parser = build_parser("txt2img")
    with pytest.raises(ArgsError) as e:
        parser.parse_args(["-h"])
    assert "usage" in str(e.value)
    assert "-wf" in str(e.value)


def test_queue_and_api_parsers():
    q = build_queue_parser().parse_args(["-stop", "-be", "1", "-index", "2-5"])
    assert q.stop and q.backend == "1" and q.index == "2-5"
    a = build_api_parser().parse_args([])
    assert a.get == "all" and a.backend == "0"
