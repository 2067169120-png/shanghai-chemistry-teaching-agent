from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_desktop_library import RAW, Reader
from test_student_recommendation_workbench import TARGET_SECTION, _catalog
from test_student_visual_analysis import (
    CapturingTransport,
    FakeProviderStore,
    complete_and_score,
)

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
    _canonical_digest,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled
from integrations.deeptutor_shchem_v1.student_visual_analysis import (
    StudentVisualAnalysisManager,
)


class CurriculumReader:
    def __init__(self):
        self.value = _catalog()
        self.layer = "master_direct_active"
        self.atomic_id = "A1"

    def catalog(self):
        return deepcopy(self.value)

    def search(self, **query):
        return {
            "items": [{"atomic_id": self.atomic_id, "source_layer": self.layer}],
            "atomic_ids": [self.atomic_id],
            "query": dict(query),
        }


def _diagnose(env, scoring, *, action="accept"):
    current = env.manager.get_submission(env.student_id, env.submission_id)
    accepted = action in {"accept", "edit"}
    return env.manager.append_diagnostic_decision(
        env.student_id,
        env.submission_id,
        {
            "expected_revision": current["revision"],
            "match_id": env.match_id,
            "scoring_decision_id": scoring["decision"]["decision_id"],
            "decision": action,
            "result": "incorrect" if accepted else "not_scored",
            "primary_error_type": "concept" if accepted else None,
            "secondary_error_types": [],
            "curriculum_section_keys": [TARGET_SECTION] if accepted else [],
            "teacher_note": "合成夹具的教师诊断，不是真实学生或化学判断。",
        },
    )


def _rescore(env, score):
    current = env.manager.get_submission(env.student_id, env.submission_id)
    return env.manager.append_scoring_decision(
        env.student_id,
        env.submission_id,
        {
            "expected_revision": current["revision"],
            "match_id": env.match_id,
            "teacher_score": score,
            "reason": "修改合成夹具分数。",
        },
    )


@pytest.fixture
def env(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    private = tmp_path / "private"
    curriculum = CurriculumReader()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        private,
        project_root=None,
        provider_store=FakeProviderStore(),
        transport=transport,
        curriculum_catalog=curriculum.catalog(),
    )
    facade = None
    try:
        student, completed, scoring = complete_and_score(manager, transport)
        reader, wave = Reader(), Reader()
        paths = DesktopPaths.from_workspace(workspace, state_root=tmp_path / "desktop")
        facade = DesktopWorkbenchFacade(
            paths,
            theme_reader=reader,
            supplemental_reader=reader,
            curriculum_reader=curriculum,
            wave_visual_reader=wave,
            wave_crop_reader=wave,
            master_workbench_reader=reader,
            master_direct_reader=reader,
            provider_store=SimpleNamespace(list_metadata=list),
            student_analysis_manager=manager,
        )
        value = SimpleNamespace(
            facade=facade,
            manager=manager,
            transport=transport,
            reader=reader,
            curriculum=curriculum,
            private=private,
            root=tmp_path,
            student_id=student["student_id"],
            submission_id=completed["submission_id"],
            match_id=completed["matching"]["matches"][0]["match_id"],
            scoring=scoring,
        )
        _diagnose(value, scoring)
        yield value
    finally:
        if facade is not None:
            facade.shutdown()
        else:
            manager.shutdown()


def _preview(env):
    return env.facade.student_practice_preview(
        student_id=env.student_id, submission_id=env.submission_id
    )


def _files(root: Path):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "scope, layer",
    [
        ("master", "master_direct_active"),
        ("supplemental", "supplemental_wechat_active"),
    ],
)
def test_real_manager_to_complete_theme_preview_then_opaque_basket(env, scope, layer):
    env.curriculum.layer = layer
    before, calls = _files(env.root), env.transport.calls
    preview = _preview(env)
    assert len(preview.recommendations) == 1
    candidate = preview.recommendations[0]
    assert candidate.atomic_total == 2
    assert candidate.matched_question_count == 1
    assert preview.diagnoses[0].status_zh == "本次失分线索"
    assert _files(env.root) == before
    assert env.facade.basket() == ()

    detail = env.facade.student_practice_theme_detail(preview, candidate.key)
    assert len(detail.parts) == 2
    assert detail.shared_images
    assert "原卷第 7 题 · 作答单元 1" in detail.parts[1].dependency_zh
    assert env.facade.library_image(detail.shared_images[0]) == RAW
    assert all(part.reference_answer_zh for part in detail.parts)
    assert _files(env.root) == before
    assert env.facade.add_student_practice_to_basket(preview, candidate.key) == 1
    basket = env.facade.basket()
    assert len(basket) == 1
    assert basket[0]["atomic_total"] == 2
    assert basket[0]["scope"] == scope
    assert basket[0]["source_identity_sha256"] == _canonical_digest(
        {"scope": scope, "paper": "P1", "theme": "T1"}
    )
    assert basket[0]["data_snapshot_id"] == env.facade._paper_catalog_snapshot_id(
        env.facade._load_theme_scope(scope)
    )
    assert "basket_selection" not in basket[0]
    assert "atomic_ids" not in basket[0]
    assert env.facade.add_student_practice_to_basket(preview, candidate.key) == 1
    after = _files(env.root)
    changed = {
        name
        for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    }
    assert changed == {env.facade._state.path.relative_to(env.root).as_posix()}
    assert env.transport.calls == calls
    assert env.student_id not in repr(preview)
    assert env.submission_id not in repr(preview)


def test_add_requires_opening_complete_theme_not_only_summary(env):
    preview = _preview(env)
    with pytest.raises(DesktopFacadeError) as caught:
        env.facade.add_student_practice_to_basket(
            preview, preview.recommendations[0].key
        )
    assert caught.value.code == "student_practice_preview_required"
    assert env.facade.basket() == ()


def test_rescore_invalidates_old_preview_and_old_diagnosis_then_fresh_diagnosis_restores(
    env,
):
    old = _preview(env)
    key = old.recommendations[0].key
    env.facade.student_practice_theme_detail(old, key)
    _rescore(env, env.scoring["decision"]["maximum_score"])
    for action in (
        env.facade.student_practice_theme_detail,
        env.facade.add_student_practice_to_basket,
    ):
        with pytest.raises(DesktopFacadeError) as caught:
            action(old, key)
        assert caught.value.code == "student_practice_stale"
    review = env.facade.student_analysis_review(
        student_id=env.student_id, submission_id=env.submission_id
    )
    assert review.review_complete is False
    assert review.items[0].diagnostic_requires_reconfirmation is True
    assert review.items[0].latest_diagnostic_decision is None
    assert review.diagnostic_confirmed_count == 0
    current = _preview(env)
    assert current.recommendations == ()
    assert current.diagnoses == ()
    assert any("改过分" in notice for notice in current.notices_zh)
    scoring = _rescore(env, 0.0)
    _diagnose(env, scoring, action="edit")
    refreshed = _preview(env)
    assert len(refreshed.recommendations) == 1
    assert env.facade.basket() == ()
    assert env.facade.student_analysis_review(
        student_id=env.student_id, submission_id=env.submission_id
    ).review_complete


@pytest.mark.parametrize("action", ["reject", "pending"])
def test_rejected_or_pending_teacher_diagnosis_produces_no_practice(env, action):
    _diagnose(env, env.scoring, action=action)
    before = _files(env.root)
    result = _preview(env)
    assert result.recommendations == ()
    assert result.diagnoses == ()
    assert env.facade.basket() == ()
    assert _files(env.root) == before


@pytest.mark.parametrize("kind", ["source", "catalog", "mapping"])
def test_changed_source_catalog_or_mapping_blocks_old_add(env, kind):
    preview = _preview(env)
    key = preview.recommendations[0].key
    env.facade.student_practice_theme_detail(preview, key)
    if kind == "source":
        env.reader.value["papers"][0]["theme_groups"][0]["shared_context"][
            "context_summary_zh"
        ] = "资料已修改"
        code = "student_practice_library_stale"
    elif kind == "catalog":
        env.curriculum.value["data_snapshot_id"] = "changed-curriculum"
        code = "student_practice_curriculum_stale"
    else:
        env.curriculum.atomic_id = "A2"
        code = "student_practice_curriculum_stale"
    with pytest.raises(DesktopFacadeError) as caught:
        env.facade.add_student_practice_to_basket(preview, key)
    assert caught.value.code == code
    assert env.facade.basket() == ()


def test_same_current_preview_refresh_requires_viewing_the_refreshed_detail(env):
    old = _preview(env)
    key = old.recommendations[0].key
    env.facade.student_practice_theme_detail(old, key)
    new = _preview(env)
    assert new == old
    with pytest.raises(DesktopFacadeError) as caught:
        env.facade.add_student_practice_to_basket(new, key)
    assert caught.value.code == "student_practice_preview_required"


@pytest.mark.parametrize("mutation", ["student", "key", "hash"])
def test_forged_handle_does_not_select_another_student_or_theme(env, mutation):
    preview = _preview(env)
    key = preview.recommendations[0].key
    if mutation == "student":
        preview = replace(preview, student_id="11111111-1111-4111-8111-111111111111")
    elif mutation == "key":
        key = "unregistered-theme"
    else:
        preview = replace(preview, preview_hash="0" * 64)
    with pytest.raises(DesktopFacadeError) as caught:
        env.facade.student_practice_theme_detail(preview, key)
    assert caught.value.code == "student_practice_preview_missing"


def test_review_changes_during_search_cannot_register_a_preview(env, monkeypatch):
    original = env.facade._search.search
    changed = False

    def search(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            _rescore(env, 1.0)
        return result

    monkeypatch.setattr(env.facade._search, "search", search)
    with pytest.raises(DesktopFacadeError) as caught:
        _preview(env)
    assert caught.value.code == "student_practice_stale"
    assert env.facade._student_practice._records == {}
    assert env.facade.basket() == ()


def test_shutdown_clears_previews_and_cancels_all_practice_entry_points(env):
    preview = _preview(env)
    key = preview.recommendations[0].key
    env.facade.shutdown()
    assert env.facade._student_practice._records == {}
    for action in (
        lambda: _preview(env),
        lambda: env.facade.student_practice_theme_detail(preview, key),
        lambda: env.facade.add_student_practice_to_basket(preview, key),
    ):
        with pytest.raises(ReadCancelled):
            action()


def test_private_provider_or_reader_exception_is_not_exposed(env, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("SENTINEL-PRIVATE-PATH-DO-NOT-DISPLAY")

    monkeypatch.setattr(env.manager, "get_submission", fail)
    with pytest.raises(DesktopFacadeError) as caught:
        _preview(env)
    assert "SENTINEL" not in str(caught.value)
    assert "暂时无法读取" in str(caught.value)


def test_window_student_basket_signal_updates_the_real_composer(env):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        TeacherWorkbenchWindow,
    )

    app = QApplication.instance() or QApplication([])
    window = TeacherWorkbenchWindow(env.facade)
    try:
        window.show()
        app.processEvents()
        assert window.paper_page.model.themes == []
        original_summary = window.paper_page.summary_label.text()
        preview = _preview(env)
        key = preview.recommendations[0].key
        env.facade.student_practice_theme_detail(preview, key)
        count = env.facade.add_student_practice_to_basket(preview, key)
        window.student_page.basket_changed.emit(count)
        app.processEvents()
        assert len(window.paper_page.model.themes) == 1
        assert window.paper_page.summary_label.text() != original_summary
        assert "1" in window.paper_page.summary_label.text()
    finally:
        window.close()
        app.processEvents()
