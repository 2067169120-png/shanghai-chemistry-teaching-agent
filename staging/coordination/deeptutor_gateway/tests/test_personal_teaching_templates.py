"""Personal structures use ordinary state/ZIP and never clone source content."""
from copy import deepcopy
import json

import pytest

from integrations.deeptutor_shchem_v1.desktop_backup import (plan_backup, create_backup,
    inspect_backup, restore_backup, manifest_revision)
from integrations.deeptutor_shchem_v1.desktop_lesson_design import DesignHistory, digest, new_node
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore, DesktopStateError
from integrations.deeptutor_shchem_v1.desktop_teaching_framework import FIELDS, SCHEMA as FRAMEWORK_SCHEMA
from integrations.deeptutor_shchem_v1.desktop_teaching_templates import (TeachingTemplateService,
    capture_structure, apply_structure, can_replace_empty_structure, validate_library, validate_structure)
from test_lesson_composition_contract import payload
from test_desktop_preparation_drafts import _record


def lesson():
    result = payload()
    plan = result["lesson_design"]
    plan["teaching_framework"] = {"schema_version": FRAMEWORK_SCHEMA,
        **{key: "" for key, _ in FIELDS}, "focus": "教师原有重点", "methods": "观察与讨论"}
    node = plan["nodes"][0]
    node.update(teacher_action="从弱酸微粒组成引导解释", student_task="比较溶液中分子与离子的变化",
        expected_output="画出微粒变化图", criteria="区分浓度和电离程度", notes="不复用私人备注",
        material_text="不复用原题与完整条件", teacher_answer="不复用原答案", image_ids=["IMG-" + "b" * 64],
        source_digest="a" * 64, locked=True, student_material=True, confirmed=True)
    result["materials"] = "原教材及完整题目"
    return result


def test_capture_selected_complete_teacher_structure_excludes_source_domains_and_ids():
    source = lesson(); before = deepcopy(source)
    captured = capture_structure(source["lesson_design"])
    assert source == before
    assert captured["nodes"][0]["teacher_action"] == "从弱酸微粒组成引导解释"
    assert captured["framework"]["focus"] == "教师原有重点"
    encoded = json.dumps(captured, ensure_ascii=False)
    for forbidden in ("不复用", "complete_question", "composition", "materials", "teacher_answer",
                      "image_ids", "source_digest", "exports", "IMG-", source["lesson_design"]["id"]):
        assert forbidden not in encoded
    assert capture_structure(source["lesson_design"], node_ids=[], include_framework=False,
                             include_objectives=False)["nodes"] == []


def test_apply_keeps_complete_original_lesson_and_is_one_history_edit():
    source = lesson(); before = deepcopy(source); plan = source["lesson_design"]
    structure = capture_structure(plan)
    structure["framework"]["focus"] = "模板重点不覆盖已编辑字段"
    structure["framework"]["homework"] = "比较稀释前后微粒组成"
    history = DesignHistory(plan)
    history.put(apply_structure(plan, structure, expected_fingerprint=digest(plan)))
    applied = history.value
    assert source == before
    assert applied["nodes"][:len(plan["nodes"])] == plan["nodes"]
    for key in ("id", "composition", "exports"):
        assert applied[key] == plan[key]
    assert applied["teaching_framework"]["focus"] == "教师原有重点"
    assert applied["teaching_framework"]["homework"] == "比较稀释前后微粒组成"
    new = applied["nodes"][-1]
    assert not any(new[key] for key in ("material_text", "teacher_answer", "image_ids", "source_digest",
                                       "confirmed", "locked", "student_material", "notes"))
    assert new["id"] not in {node["id"] for node in plan["nodes"]}
    assert set(new["objective_ids"]).isdisjoint({goal["id"] for goal in plan["objectives"]})
    history.travel(); assert history.value == plan
    history.travel(True); assert history.value == applied


def test_stale_apply_and_nonempty_replace_reject_without_modifying_inputs():
    plan = lesson()["lesson_design"]; structure = capture_structure(plan); before = deepcopy(plan)
    with pytest.raises(ValueError, match="变化"):
        apply_structure(plan, structure, expected_fingerprint="0" * 64)
    with pytest.raises(ValueError, match="已有编辑"):
        apply_structure(plan, structure, expected_fingerprint=digest(plan), mode="replace_empty")
    assert plan == before and not can_replace_empty_structure(plan)
    empty = deepcopy(plan); empty["nodes"] = [new_node()]
    assert can_replace_empty_structure(empty)
    replaced = apply_structure(empty, structure, expected_fingerprint=digest(empty), mode="replace_empty")
    assert len(replaced["nodes"]) == len(structure["nodes"])
    assert replaced["composition"] == empty["composition"]


def test_blank_node_already_used_by_classroom_page_cannot_be_replaced():
    from integrations.deeptutor_shchem_v1.desktop_composition_activities import import_activities
    from integrations.deeptutor_shchem_v1.desktop_lesson_composition import add_resource
    plan = lesson()["lesson_design"]
    plan["nodes"] = [new_node()]
    assert can_replace_empty_structure(plan)
    selected = import_activities(plan, [plan["nodes"][0]["id"]], [])
    linked = deepcopy(plan)
    linked["composition"] = add_resource(linked["composition"], selected["resources"][0],
        {"page_id": linked["composition"]["pages"][0], "x": 14, "y": 140, "width": 180, "height": 50})
    assert not can_replace_empty_structure(linked)
    before = deepcopy(linked)
    with pytest.raises(ValueError, match="已有编辑"):
        apply_structure(linked, capture_structure(plan), expected_fingerprint=digest(linked), mode="replace_empty")
    assert linked == before


def test_state_roundtrip_search_many_rename_recover_delete_stale_and_legacy(tmp_path):
    store = DesktopStateStore(tmp_path)
    assert "teaching_templates" not in store.snapshot()
    store.save_draft("original", {"private_student": "existing private domain"})
    store.add_to_basket({"key": "original-basket"})
    before = store.snapshot(); service = TeachingTemplateService(store)
    assert service.search() == []
    structure = capture_structure(lesson()["lesson_design"])
    first = service.save("弱酸新授", structure)
    service.save("弱酸复习", structure)
    assert len(service.search("弱酸")) == 2
    assert len(service.search("微粒")) == 2
    reopened = TeachingTemplateService(DesktopStateStore(tmp_path))
    assert reopened.load(first["id"]) == first
    renamed = reopened.rename(first, "两课时探究")
    with pytest.raises(DesktopStateError, match="另一窗口"):
        service.delete(first)
    deleted = service.delete(renamed)
    assert len(service.search()) == 1 and len(service.search(include_deleted=True)) == 2
    with pytest.raises(ValueError, match="已删除"):
        service.load(first["id"])
    assert service.restore(deleted)["structure"] == structure
    after = store.snapshot()
    assert after["drafts"] == before["drafts"] and after["basket"] == before["basket"]
    assert after["draft_revisions"] == before["draft_revisions"]


def test_templates_ordinary_backup_exact_restore_includes_deleted_and_no_student_records(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    store = DesktopStateStore(source); service = TeachingTemplateService(store)
    store.save_draft("prep-original", _record(lesson()))
    store.save_draft("private", {"kind": "student-analysis", "student_id": "excluded-person"})
    active = service.save("完整教学结构", capture_structure(lesson()["lesson_design"]))
    service.delete(service.save("可恢复结构", active["structure"]))
    before = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    plan = plan_backup(source)
    assert plan.summary["teaching_templates"] == 1 and plan.summary["deleted_teaching_templates"] == 1
    archive = tmp_path / "ordinary.zip"; create_backup(plan, archive)
    report = inspect_backup(archive); destination = tmp_path / "restored"
    restore_backup(archive, destination, expected_manifest=manifest_revision(report))
    restored = DesktopStateStore(destination).snapshot()
    assert restored["teaching_templates"] == store.snapshot()["teaching_templates"]
    assert restored["drafts"]["prep-original"] == store.snapshot()["drafts"]["prep-original"]
    assert "private" not in restored["drafts"]
    assert TeachingTemplateService(DesktopStateStore(destination)).load(active["id"]) == active
    assert before == {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}


def test_rename_history_is_durable_reversible_and_old_optional_history_is_readable(tmp_path):
    service = TeachingTemplateService(DesktopStateStore(tmp_path))
    original = service.save("最初的名称", capture_structure(lesson()["lesson_design"]))
    changed = service.rename(original, "新的名称")
    reopened = TeachingTemplateService(DesktopStateStore(tmp_path))
    restored_name = reopened.undo_rename(reopened.load(changed["id"]))
    assert restored_name["name"] == original["name"] and restored_name["structure"] == original["structure"]
    with pytest.raises(ValueError, match="没有可撤销"):
        reopened.undo_rename(restored_name)
    # An early optional-history v1 record remains readable without a save.
    library = reopened.store.snapshot()["teaching_templates"]
    del library["records"][original["id"]]["name_history"]
    assert validate_library(library)["records"][original["id"]]["name_history"] == []


def test_malformed_template_field_does_not_allow_unrelated_state_write(tmp_path):
    service = TeachingTemplateService(DesktopStateStore(tmp_path))
    service.save("结构", capture_structure(lesson()["lesson_design"]))
    raw = json.loads(service.store.path.read_text(encoding="utf-8"))
    raw["teaching_templates"]["schema_version"] = "future"
    service.store.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    before = service.store.path.read_bytes()
    with pytest.raises(DesktopStateError, match="已保留"):
        service.store.save_draft("unrelated", {"kind": "preparation"})
    assert service.store.path.read_bytes() == before


@pytest.mark.parametrize("corrupt", ["identity", "version", "foreign_field", "length", "source_field"])
def test_templates_reject_invalid_versions_lengths_identities_and_source_fields(tmp_path, corrupt):
    service = TeachingTemplateService(DesktopStateStore(tmp_path))
    record = service.save("教学结构", capture_structure(lesson()["lesson_design"]))
    library = service.store.snapshot()["teaching_templates"]
    row = library["records"][record["id"]]
    if corrupt == "identity": row["id"] = "TT-" + "f" * 32
    elif corrupt == "version": row["schema_version"] = "future"
    elif corrupt == "foreign_field": row["student_records"] = []
    elif corrupt == "length": row["name"] = "名" * 81
    else: row["structure"]["nodes"][0]["teacher_answer"] = "原答案"
    with pytest.raises(ValueError): validate_library(library)
    with pytest.raises(ValueError): validate_structure({"schema_version": "future"})
