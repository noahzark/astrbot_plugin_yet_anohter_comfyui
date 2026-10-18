import json

import pytest

from comfyui_core.exceptions import ReflexJsonNotFoundError, WorkflowAdminLimitation, WorkflowGroupLimitation
from comfyui_core.workflow import WorkflowStore, validate_permissions


def test_lists_only_complete_pairs(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "a_reflex.json").write_text("{}")
    (tmp_path / "b.json").write_text("{}")  # no reflex
    (tmp_path / "c_reflex.json").write_text("{}")  # no workflow
    store = WorkflowStore(tmp_path)
    assert store.list_workflows() == ["a"]
    assert store.list_workflows(index=1) == ["a"]
    assert store.list_workflows(index=2) == []
    assert store.list_workflows(search="zzz") == []
    with pytest.raises(ReflexJsonNotFoundError):
        store.load("b")


def test_built_ins_all_load(built_in_wf_dir):
    store = WorkflowStore(built_in_wf_dir)
    names = store.list_workflows()
    assert {"txt2img", "img2img", "超分", "打标"} <= set(names)
    for name in names:
        api, reflex = store.load(name)
        assert isinstance(api, dict) and isinstance(reflex, dict)
        assert "output" in reflex


def test_missing_dir_lists_nothing(tmp_path):
    assert WorkflowStore(tmp_path / "nope").list_workflows() == []


def test_validate_permissions():
    validate_permissions({}, "123", is_admin=False)
    validate_permissions({"admin": True}, "123", is_admin=True)
    with pytest.raises(WorkflowAdminLimitation):
        validate_permissions({"admin": True}, "123", is_admin=False)
    validate_permissions({"group": [123]}, "123", is_admin=False)
    validate_permissions({"group": [123]}, "", is_admin=False)  # private chat passes
    validate_permissions({"group": [123]}, "not-a-number", is_admin=False)
    with pytest.raises(WorkflowGroupLimitation):
        validate_permissions({"group": [123]}, "456", is_admin=False)
