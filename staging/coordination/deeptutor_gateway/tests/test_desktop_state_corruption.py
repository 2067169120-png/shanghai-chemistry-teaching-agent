"""Corrupt durable fields must not be silently erased by an unrelated update."""
import json
from copy import deepcopy
import pytest
from integrations.deeptutor_shchem_v1.desktop_state import (
    DesktopStateError, DesktopStateStore, STATE_SCHEMA,
)


@pytest.mark.parametrize("field,value", [
    ("basket", None), ("basket", {}), ("basket", "recoverable text"),
    ("drafts", None), ("drafts", []), ("drafts", "recoverable text"),
])
@pytest.mark.parametrize("action", ["read", "window", "add", "draft"])
def test_malformed_durable_field_blocks_all_updates_without_altering_bytes(tmp_path, field, value, action):
    store = DesktopStateStore(tmp_path)
    document = {"schema_version": STATE_SCHEMA, "window": {},
                "basket": [{"key": "original", "title_zh": "教师原选题"}],
                "drafts": {"original": {"topic": "教师原稿"}}}
    document[field] = value
    original = json.dumps(document, ensure_ascii=False).encode("utf-8")
    store.path.write_bytes(original)
    operations = {
        "read": store.snapshot,
        "window": lambda: store.save_window_state(geometry="new", layout="new"),
        "add": lambda: store.add_to_basket({"key": "new"}),
        "draft": lambda: store.save_draft("new", {"topic": "不应写入"}),
    }
    with pytest.raises(DesktopStateError, match="格式异常"):
        operations[action]()
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".desktop-state-*.tmp"))


def test_missing_optional_legacy_fields_still_load_and_can_be_saved(tmp_path):
    store = DesktopStateStore(tmp_path)
    store.path.write_text(json.dumps({"schema_version": STATE_SCHEMA}), encoding="utf-8")
    assert store.basket() == []
    assert store.snapshot()["drafts"] == {}
    store.add_to_basket({"key": "new"})
    assert [row["key"] for row in DesktopStateStore(tmp_path).basket()] == ["new"]


def test_valid_basket_and_drafts_survive_unrelated_window_write(tmp_path):
    store = DesktopStateStore(tmp_path)
    store.add_to_basket({"key": "original", "title_zh": "教师原选题"})
    store.save_draft("original", {"topic": "教师原稿"})
    before = deepcopy(store.snapshot())
    store.save_window_state(geometry="new", layout="new")
    after = DesktopStateStore(tmp_path).snapshot()
    assert after["basket"] == before["basket"]
    assert after["drafts"] == before["drafts"]
