from copy import deepcopy
import json

import pytest

from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_mapping_profiles import ExamMappingProfiles
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore


@pytest.fixture
def mapped(tmp_path):
    book, config, _ = example(tmp_path / "scores.xlsx")
    store = ExamMappingProfiles(DesktopStateStore(tmp_path / "state"))
    return store, book, config


def test_profile_reopens_without_student_rows_or_exam_title(mapped):
    store, book, config = mapped
    saved = store.save("合成列映射", book, config)
    assert store.preview(saved["id"], saved["revision"], book)["exact"]
    raw = json.dumps(store.profiles(), ensure_ascii=False)
    assert book["sheets"][0]["rows"][1][0] not in raw
    assert "title" not in saved["params"] and "students" not in saved


@pytest.mark.parametrize("change", ["reorder", "rename", "sheet", "width"])
def test_changed_layout_never_provides_applicable_config(mapped, change):
    store, book, config = mapped
    saved = store.save("合成列映射", book, config)
    changed = deepcopy(book)
    sheet = next(row for row in changed["sheets"] if row["name"] == config["sheet"])
    if change == "reorder":
        sheet["rows"][0][0], sheet["rows"][0][1] = sheet["rows"][0][1], sheet["rows"][0][0]
    elif change == "rename":
        sheet["rows"][0][0] = "不同字段"
    elif change == "width":
        sheet["rows"][0].append("新列")
    else:
        sheet["name"] = "另一张表"
    check = store.preview(saved["id"], saved["revision"], changed)
    assert not check["exact"] and check["config"] is None and check["differences"]


def test_profile_conflict_preserves_latest_mapping(mapped):
    store, book, config = mapped
    saved = store.save("first", book, config)
    config["pass_score"] = 55
    latest = store.save("new", book, config, profile_id=saved["id"], expected_revision=saved["revision"])
    with pytest.raises(ExamError):
        store.save("stale", book, config, profile_id=saved["id"], expected_revision=saved["revision"])
    with pytest.raises(ExamError):
        store.preview(saved["id"], saved["revision"], book)
    assert store.profiles() == [latest]


def test_scores_can_change_with_same_layout_and_are_reread(mapped):
    store, book, config = mapped
    saved = store.save("合成列映射", book, config)
    changed = deepcopy(book)
    changed["sheets"][0]["rows"][1][3] = 0
    changed["source_sha256"] = "f" * 64
    assert store.preview(saved["id"], saved["revision"], changed)["exact"]
