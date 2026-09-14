"""Audit A01/A02/A03: exact data preservation, complete search, request-local work."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import random

import pytest

from integrations.deeptutor_shchem_v1.desktop_editor_recovery import PreparationRecoveryError, PreparationRecoveryStore
from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import PreparationDraftError, PreparationDraftService
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_work_search import search_preparation_work
from integrations.deeptutor_shchem_v1.desktop_preparation import DesktopPreparationManager
from integrations.deeptutor_shchem_v1 import desktop_word_question_filters as filters
from integrations.deeptutor_shchem_v1.desktop_question_explorer import personal_results
from test_desktop_preparation_drafts import _payload, _record
from test_word_question_filters import _row, _catalog


def saved_state(tmp_path, count=501):
    state = DesktopStateStore(tmp_path)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    records = {f"prep-{i:04d}": _record(_payload(topic=f"合成课题-{i:04d}"),
        created_at=(start + timedelta(minutes=i)).isoformat()) for i in range(count)}
    state._update(lambda value: value["drafts"].update(records))
    return state


def test_all_drafts_search_before_paging_and_direct_oldest_id(tmp_path):
    state = saved_state(tmp_path)
    before = state.path.read_bytes()
    service = PreparationDraftService(state)
    assert len(service.options()) == 50  # backward-compatible recent list
    found = service.search(query="课题-0000", limit=25)
    assert found["total"] == 1 and found["items"][0]["draft_id"] == "prep-0000"
    all_rows = service.search(limit=None)
    assert all_rows["total"] == 501 and len(all_rows["items"]) == 501
    final = service.search(offset=500, limit=25)
    assert final["items"][0]["draft_id"] == "prep-0000"
    option = service.option("prep-0000")
    assert service.load(option["draft_id"], option["revision"])["payload"]["topic"] == "合成课题-0000"
    assert state.path.read_bytes() == before


def test_work_query_merges_then_sorts_and_pages(tmp_path):
    state = saved_state(tmp_path, 61)
    service = PreparationDraftService(state)
    tasks = [{"task_id": "PREP-synthetic", "title_zh": "合成课题-0000", "created_at": "2026-01-01T00:00:00Z", "status": "prepared"}]
    facade = SimpleNamespace(search_preparation_drafts=service.search,
        search_preparation_tasks=lambda query="": {"items": [t for t in tasks if query.strip().casefold() in t["title_zh"].casefold()], "invalid_count": 0})
    newest = search_preparation_work(facade, limit=25)
    assert newest["total"] == 62 and len(newest["items"]) == 25
    assert newest["items"][0]["id"] == "PREP-synthetic"
    oldest = search_preparation_work(facade, order="oldest", limit=25)
    assert oldest["items"][0]["id"] == "prep-0000"
    assert search_preparation_work(facade, query="课题-0000")["total"] == 2
    assert search_preparation_work(facade, query="课题-0000", kind="draft")["total"] == 1
    page = search_preparation_work(facade, offset=1000)
    assert page["offset"] == 50 and len(page["items"]) == 12 and not page["has_more"]


def test_work_read_failure_stays_visible_and_not_complete_zero():
    def broken(**kwargs):
        raise OSError("private diagnostic")
    facade = SimpleNamespace(search_preparation_drafts=broken,
        search_preparation_tasks=lambda **kw: {"items": [], "invalid_count": 0})
    result = search_preparation_work(facade)
    assert result["warnings"] and "private diagnostic" not in str(result)


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"limit": 0}, {"query": None}, {"kind": "other"}, {"order": "random"}])
def test_invalid_work_query_is_rejected(kwargs):
    with pytest.raises(ValueError):
        search_preparation_work(None, **kwargs)


def test_bad_draft_does_not_hide_valid_records_and_id_is_never_substituted(tmp_path):
    state = saved_state(tmp_path, 3)
    state.save_draft("bad", {"kind": "preparation"})
    service = PreparationDraftService(state)
    assert service.search()["invalid_count"] == 1
    assert service.search()["total"] == 3
    with pytest.raises(PreparationDraftError):
        service.option("missing")


def test_task_search_finds_older_than_200_without_provider(tmp_path):
    manager = DesktopPreparationManager(tmp_path / "tasks", lambda *args, **kw: pytest.fail("Renderer must not run"))
    for i in range(205):
        manager.prepare(_payload(topic=f"合成生成任务-{i:04d}"), "LOCAL-TEST", "REV-1")
    before = {p.name: p.read_bytes() for p in manager.tasks_root.glob("*.json")}
    result = manager.search_tasks(query="任务-0000", limit=None)
    assert result["total"] == 1 and result["items"][0]["topic"] == "合成生成任务-0000"
    assert len(manager.search_tasks(limit=None)["items"]) == 205
    assert len(manager.list_tasks(limit=50)) == 50
    assert before == {p.name: p.read_bytes() for p in manager.tasks_root.glob("*.json")}


def test_recovery_keeps_unfinished_text_images_and_formal_drafts_separate(tmp_path):
    state = saved_state(tmp_path, 1)
    before = state.path.read_bytes()
    payload = _payload(topic="")
    payload["objective"] = ""
    payload["materials"] = "  未完成材料\n\n保留空行、条件和上下标SO₄²⁻  "
    payload["image_assets"] = [{"asset_id": "IMG-" + "a"*64, "sha256": "a"*64,
        "caption": "合成图片", "source": "本机合成", "purpose": "题面", "content_type": "image/png", "width": 40, "height": 20}]
    payload["image_input_mode"] = "vision"
    source = deepcopy(payload)
    store = PreparationRecoveryStore(tmp_path)
    store.save(payload, dirty=True, sequence=1)
    assert PreparationRecoveryStore(tmp_path).load()["payload"] == source
    assert payload == source and state.path.read_bytes() == before
    assert not list(tmp_path.rglob("*.png"))  # only references, no invented recovery image


def test_close_snapshot_wins_over_late_queued_autosave(tmp_path):
    store = PreparationRecoveryStore(tmp_path)
    current = _payload(topic="退出时最新内容")
    store.save(current, dirty=True, sequence=9)
    assert store.save(_payload(topic="旧队列"), dirty=True, sequence=8) is None
    assert store.load()["payload"] == current


def test_failed_atomic_replace_preserves_previous_snapshot(tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_editor_recovery as module
    store = PreparationRecoveryStore(tmp_path)
    store.save(_payload(topic="之前的内容"), dirty=True, sequence=1)
    before = store.path.read_bytes()
    def failure(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(module.os, "replace", failure)
    with pytest.raises(PreparationRecoveryError):
        store.save(_payload(topic="不应破坏旧内容"), dirty=True, sequence=2)
    assert store.path.read_bytes() == before
    assert len(list(store.root.iterdir())) == 1


@pytest.mark.parametrize("mutation", ["schema", "corrupt", "extra_field", "image_field"])
def test_unreadable_recovery_is_not_silently_normalized(tmp_path, mutation):
    import json
    store = PreparationRecoveryStore(tmp_path)
    store.save(_payload(), dirty=True, sequence=1)
    record = json.loads(store.path.read_text(encoding="utf-8"))
    if mutation == "schema": record["schema_version"] = "future-version"
    elif mutation == "extra_field": record["payload"]["api_key"] = "placeholder-never-a-real-key"
    elif mutation == "image_field": record["payload"]["image_assets"] = [{"asset_id": "IMG-invalid"}]
    data = "broken-json" if mutation == "corrupt" else json.dumps(record)
    store.path.write_text(data, encoding="utf-8")
    with pytest.raises(PreparationRecoveryError): store.load()
    assert store.path.read_text(encoding="utf-8") == data


def test_compilation_once_per_search_not_per_question(monkeypatch):
    calls = []
    original = filters._directory
    def count(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(filters, "_directory", count)
    catalog = {"items": [_row(key=f"Q{i}") for i in range(150)], "attribute_catalog": _catalog()}
    assert personal_results(catalog, "word_native", {}, "")["total"] == 150
    assert len(calls) == 2  # one matching directory plus one options directory, independent of N


def test_new_search_observes_changed_catalog_and_compiled_selection_isolated():
    catalog, selection, row = _catalog(), {"book": ["V1"]}, _row()
    matcher = filters.compile_question_matcher(selection, catalog)
    selection["book"].clear()
    catalog["nodes"][0]["volume_id"] = "V2"
    assert matcher(row)
    assert not filters.compile_question_matcher({"book": ["V1"]}, catalog)(row)


def test_compiled_queries_retain_stable_order_unknown_and_answer_isolation():
    catalog = _catalog()
    rows = [_row(key=f"Q{i}", mappings=("S1",) if i % 2 else ("S2",), exam="first_mock" if i % 3 else "second_mock") for i in range(70)]
    randomizer = random.Random(86)
    for _ in range(60):
        selection = {"exam": randomizer.choice([[], ["first_mock"], ["second_mock"], ["unknown"]]),
                     "book": randomizer.choice([[], ["V1"], ["V2"], ["unknown"]]),
                     "query": randomizer.choice(["", "ALPHA", "OMEGA", "CONTEXT"])}
        matcher = filters.compile_question_matcher(selection, catalog)
        got = [r["key"] for r in rows if matcher(r)]
        expected = [r["key"] for r in rows if filters.matches_question(r, selection, catalog)]
        assert got == expected
        if selection["query"] == "OMEGA": assert not got
