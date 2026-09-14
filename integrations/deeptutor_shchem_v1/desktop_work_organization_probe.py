"""Opt-in source/packaged acceptance helper, only called with isolated CI state."""
from __future__ import annotations
from copy import deepcopy
import hashlib


def exercise(window, settle, capture):
    facade, page, prep = window.facade, window.my_work_page, window.preparation_page
    prep.apply_studio_template("concept", "合成原课题：证据与解释", "软件验收")
    # The template correctly preserves an existing topic; explicitly edit the
    # synthetic form here instead of expecting template application to replace it.
    prep.topic.setText("合成原课题：证据与解释")
    prep.audience.setText("软件验收")
    prep.materials.setPlainText("软件验收合成材料；公共材料和原课题不因整理作品而变化。")
    facade.create_preparation_draft(prep._payload())
    identity = facade.preparation_draft_options()[0]["draft_id"]
    source = deepcopy(facade.state_store.snapshot()["drafts"])
    prep.materials.setPlainText("尚未正式保存的另一份编辑；整理作品不应清空这里。")
    unsaved = deepcopy(prep._payload())
    prep.recovery.flush()
    recovery_before = prep.recovery.store.path.read_bytes()
    window.navigate("mywork")

    def select(key):
        settle(lambda: not page._loading and not page._mutating)
        index = next(i for i, record in enumerate(page.records) if record["id"] == key)
        page.results.setCurrentRow(index)

    def scope(index):
        page.shelves.setCurrentIndex(index)
        settle(lambda: not page._loading and not page._mutating)

    def finish():
        settle(lambda: not page._loading and not page._mutating)

    original_ask, original_confirm = page._ask_title, page._confirm_trash
    try:
        select(identity)
        page._ask_title = lambda _: "下周高二 · 证据推理课（合成演示）"
        page.rename_button.click(); finish()
        assert page.records[0]["original_title"] == "合成原课题：证据与解释"
        select(identity)
        capture(window, "work-current.png")
        page.archive_button.click(); finish()
        scope(1); select(identity)
        capture(window, "work-archived.png")
        page._confirm_trash = lambda _: True
        page.trash_button.click(); finish()
        scope(2); select(identity)
        assert not page.open_button.isEnabled()
        capture(window, "work-trash.png")
        page.restore_button.click(); finish()
        scope(1); select(identity)
        page.archive_button.click(); finish()
        scope(0); select(identity)
        page.query.setText("原课题"); finish()
        assert len(page.records) == 1
        capture(window, "work-original-search.png")
        window.resize(800, 700); settle()
        capture(window, "work-compact.png")
        window.resize(1360, 900); settle()
        assert facade.state_store.snapshot()["drafts"] == source
        assert prep._payload() == unsaved
        assert prep.recovery.store.path.read_bytes() == recovery_before
        page.query.clear(); finish()
        # A real saved task is cancelled locally before being organized; no provider call.
        manager = facade._preparation_manager_instance()
        task = manager.prepare(prep._payload(), "LOCAL-TEST", "REV-TEST")
        manager.cancel(task["task_id"])
        files_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in manager.tasks_root.glob("*.json")}
        page.refresh(); finish(); select(task["task_id"])
        page._ask_title = lambda _: "合成任务 · 保留候选"
        page.rename_button.click(); finish(); select(task["task_id"])
        page.trash_button.click(); finish()
        scope(2); select(task["task_id"])
        page.restore_button.click(); finish()
        scope(0); select(task["task_id"])
        assert files_before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in manager.tasks_root.glob("*.json")}
        return {"draft_lifecycle": True, "task_lifecycle": True, "original_drafts_unchanged": True,
                "task_files_unchanged": True, "unsaved_editor_preserved": True,
                "recovery_file_unchanged": True, "original_topic_search": True, "model_calls": 0}
    finally:
        page._ask_title, page._confirm_trash = original_ask, original_confirm
