"""D09 native Qt replacement gates; synthetic sources and isolated state only."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRect, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QLabel

from test_word_question_dialog import _Tasks, _isolated_qt_app, _question
from test_desktop_library_ui import _detail, _image
from test_desktop_mixed_paper_service import setup  # noqa: F401
from test_independent_paper_ui import workspace, create  # noqa: F401
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_reader import PersonalQuestionReader
from integrations.deeptutor_shchem_v1.desktop_workbench.library_detail import LibraryDetailDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import PaperComposerModel
from integrations.deeptutor_shchem_v1.desktop_workbench.question_replacement_dialog import (
    LANES, QuestionReplacementDialog, QuestionReplacementVersionsDialog,
)


@pytest.fixture
def qt_app():
    with _isolated_qt_app() as app:
        create_application(["question-replacement-ui"])
        yield app


class _Queue(_Tasks):
    task_cancelled = Signal(str, str)

    def submit_progress(self, label, operation, *, on_success=None, on_failure=None, on_progress=None):
        return self.submit(label, lambda: operation(on_progress or (lambda _: None), lambda: False),
                           on_success=on_success, on_failure=on_failure)

    def finish(self, label=None, *, allow_cancelled=False):
        pending = next(job for job in self.pending if label is None or job["label"] == label)
        if pending["task_id"] in self.cancelled and not allow_cancelled:
            self.task_cancelled.emit(pending["task_id"], pending["label"])
        super().finish(label, allow_cancelled=allow_cancelled)


def _png():
    image = QImage(24, 16, QImage.Format.Format_RGB32)
    image.fill(0xFFBCDDEE)
    raw = QByteArray()
    buffer = QBuffer(raw)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(raw)


def _word(key="Q1", *, images=False):
    row = _question(key, "A")
    if not images:
        for group in ("context_blocks", "question_blocks", "answer_blocks"):
            for block in row[group]:
                block["assets"] = []
    return row


def _entry(lane="word_native", key="Q1", *, images=False):
    if lane == "word_native":
        row = _word(key, images=images)
    elif lane == "visual_native":
        row = {"key": key, "revision": "revision-" + key, "batch_id": "synthetic-images",
               "selection_ready": True, "question_text": "合成题面", "shared_text": "合成共同材料",
               "answer_text": "合成来源答案", "images": [
                   {"image_id": "candidate-" + role, "role": role}
                   for role in ("shared_material", "question", "answer")]}
    else:
        row = {"key": key, "scope": lane, "source_identity_sha256": "a" * 64,
               "data_snapshot_id": "b" * 64}
    return {"entry_id": lane + ":" + key, "lane": lane, "key": key,
            "title": "合成完整段", "subtitle": "仅用于界面验证", "payload": row}


def _formal_detail(key, scope):
    detail = _detail(key, "合成完整主题", with_images=False)
    return replace(detail, scope=scope, parts=tuple(replace(part,
        question_text_zh="合成原题完整文字 " + part.label_zh) for part in detail.parts))


def _item(entry):
    row, lane = entry["payload"], entry["lane"]
    if lane == "word_native":
        return {"key": entry["key"], "kind": "word_question", "title_zh": "合成Word完整段",
                "source_ref": {"key": row["key"], "revision": row["revision"]}, "content": deepcopy(row)}
    if lane == "visual_native":
        return {"key": entry["key"], "kind": "personal_visual_theme", "title_zh": "合成图片主题",
                "source_ref": {"batch_id": row["batch_id"]}, "content": {
                    "shared_text": "替换后共同材料", "question_text": "替换后完整题面",
                    "answer_text": "替换后来源答案", "images": [
                        {"asset_id": "plan-" + role, "caption": "合成来源图", "role": role,
                         "teacher_only": role == "answer"}
                        for role in ("shared_material", "question", "answer")]}}
    return {"key": entry["key"], "kind": "core_theme", "title_zh": "合成原主题",
            "source_ref": deepcopy(row), "content": {
                "shared_materials": [{"text_zh": "替换前共同材料"}],
                "printed_questions": [{"atomic_parts": [{"question_text_zh": "替换前完整题面",
                    "dependency_zh": "依赖共同材料", "reference_answer": {"text_zh": "替换前来源答案"}}]}]}}


class _Facade:
    def __init__(self, entry=None):
        self.entry = entry or _entry()
        self.entries = [deepcopy(self.entry)]
        self.calls = []
        self.current = True
        self.read_failed = False
        self.commit_failed = False
        self.failed_asset = None
        self.plan_patch = {}
        self.next_cursor = None
        self.version_revision = "paper-source-revision-7"
        self.versions = [{"version_id": "source-version-1", "title": "合成来源版本",
                          "created_at": "2026-10-13", "goal": {"text": "合成教学目标"}}]

    def search_unified_questions(self, **kwargs):
        self.calls.append(("search", deepcopy(kwargs)))
        return {"entries": deepcopy(self.entries), "version_vector": {"synthetic": "r1"},
                "next_cursor": self.next_cursor}

    def unified_question_search_is_current(self, vector):
        self.calls.append(("current", deepcopy(vector)))
        return self.current

    def word_question_detail(self, key, revision):
        self.calls.append(("word_detail", key, revision))
        if self.read_failed:
            raise RuntimeError("合成原件读取失败")
        row = deepcopy(self.entry["payload"]) if self.entry["lane"] == "word_native" else _word(key)
        row.update(key=key, revision=revision)
        return row

    def personal_visual_question_detail(self, batch, key, revision):
        self.calls.append(("visual_detail", batch, key, revision))
        if self.read_failed:
            raise RuntimeError("合成图片主题读取失败")
        return deepcopy(self.entry["payload"])

    def library_theme_detail(self, row):
        key = row["key"] if isinstance(row, dict) else row.key
        scope = row["scope"] if isinstance(row, dict) else row.scope
        self.calls.append(("formal_detail", key, scope))
        if self.read_failed:
            raise RuntimeError("合成主题读取失败")
        return _formal_detail(key, scope)

    def _image(self, asset):
        if asset == self.failed_asset:
            raise RuntimeError("合成原图读取失败")
        return {"bytes": _png(), "mime_type": "image/png"}

    def word_question_image(self, key, revision, asset):
        self.calls.append(("word_image", key, revision, asset))
        return self._image(asset)

    def personal_visual_question_image(self, batch, key, revision, asset):
        self.calls.append(("visual_image", batch, key, revision, asset))
        return self._image(asset)

    def library_image(self, _descriptor):
        return _png()

    def prepare_question_replacement(self, paper_id, target, *, goal, candidate_entry):
        self.calls.append(("prepare", paper_id, target, deepcopy(goal), deepcopy(candidate_entry)))
        plan = {"plan_id": "replacement-plan-1", "plan_sha256": "c" * 64, "paper_id": paper_id,
                "target_key": target, "new_key": candidate_entry["key"], "paper_revision": "r7",
                "before_projection": {"items": []}, "after_projection": {"items": []},
                "before_item": _item(_entry("master", target)), "after_item": _item(candidate_entry),
                "goal": deepcopy(goal), "match_evidence": {"terms": ["平衡"], "knowledge": ["K10"]},
                "warnings": ["合成来源答案未独立核验"], "settings_notice": "请核对本次配分"}
        plan.update(deepcopy(self.plan_patch))
        return plan

    def question_replacement_image(self, identity, fingerprint, asset, *, side):
        self.calls.append(("plan_image", identity, fingerprint, asset, side))
        return self._image(asset)

    def commit_question_replacement(self, identity, fingerprint):
        self.calls.append(("commit", identity, fingerprint))
        if self.commit_failed:
            raise RuntimeError("合成保存结果无法核对")
        return {"status": "saved", "paper_id": "independent-paper-synthetic", "new_key": self.entry["key"],
                "revision": "r8", "preview_invalidated": True}

    def list_question_replacement_versions(self, paper_id):
        self.calls.append(("versions", paper_id))
        return {"revision": self.version_revision, "versions": deepcopy(self.versions)}

    def restore_question_replacement_version(self, paper_id, version_id, *, expected_revision):
        self.calls.append(("restore", paper_id, version_id, expected_revision))
        if expected_revision != self.version_revision:
            raise RuntimeError("合成当前卷已变化")
        return {"status": "saved", "paper_id": paper_id, "revision": "restored-r8", "preview_invalidated": True}

    def add_theme_to_basket(self, *_args):
        raise AssertionError("替题界面不能改写公共题篮")

    word_question_save_selection = add_theme_to_basket


class _FormalAnswerFacade(_Facade):
    def library_theme_detail(self, row):
        detail = super().library_theme_detail(row)
        return replace(detail, parts=tuple(replace(part, answer_images=(replace(
            _image(f"{detail.key}-answer-{index}", "answer"), scope=detail.scope,
            caption_zh="合成来源答案图"),)) for index, part in enumerate(detail.parts, 1)))

    def library_answer_image(self, descriptor):
        self.calls.append(("formal_answer_image", descriptor.node_id, descriptor.scope))
        return self._image(descriptor.node_id)["bytes"]


def _dialog(facade=None):
    facade, tasks = facade or _Facade(), _Queue()
    dialog = QuestionReplacementDialog(facade, tasks, "independent-paper-synthetic", "old-theme")
    dialog.goal.setPlainText("增加对平衡移动原因的解释练习")
    dialog.query.setText("平衡")
    return dialog, facade, tasks


def _search(dialog, tasks):
    dialog.search_button.click()
    tasks.finish("检索完整段替题候选")


def _read(dialog, tasks):
    _search(dialog, tasks)
    dialog.read_button.click()
    tasks.flush()


def _prepared(dialog, tasks):
    _read(dialog, tasks)
    dialog.prepare_button.click()
    tasks.flush()


def _confirm(dialog):
    for checkbox in (dialog.read_confirm, dialog.goal_confirm, dialog.replace_confirm):
        checkbox.setChecked(True)


def test_search_uses_all_five_sources_explicit_goal_knowledge_and_real_cursor(qt_app):
    dialog, facade, tasks = _dialog()
    dialog.goal.clear()
    dialog.search()
    assert tasks.pending == [] and not facade.calls
    dialog.goal.setPlainText("合成目标")
    dialog.query.clear()
    dialog.search()
    assert not tasks.pending
    dialog.knowledge.setText("K10，K11;K16")
    facade.next_cursor = "source-bound-cursor"
    _search(dialog, tasks)
    call = facade.calls[0][1]
    assert call == {"lanes": list(LANES), "query": "", "filters": {
        "knowledge": ["K10", "K11", "K16"], "knowledge_mode": "all"}, "cursor": None, "limit": 8}
    assert dialog.more_button.isEnabled()
    dialog.more_button.click()
    tasks.flush()
    assert facade.calls[-1][1]["cursor"] == "source-bound-cursor"
    dialog.reject()


def test_complete_original_readers_and_all_three_confirmations_are_required(qt_app):
    dialog, facade, tasks = _dialog()
    saved = []
    dialog.saved.connect(saved.append)
    _search(dialog, tasks)
    assert not dialog.prepare_button.isEnabled() and not dialog.commit_button.isEnabled()
    dialog.prepare()
    assert not any(call[0] == "prepare" for call in facade.calls)
    dialog.read_button.click()
    tasks.flush()
    assert isinstance(dialog._reader, PersonalQuestionReader)
    dialog.prepare_button.click()
    tasks.finish("核对完整段替换计划")
    assert not dialog.read_confirm.isEnabled()
    tasks.flush()
    assert dialog.reading.count() == 3
    assert isinstance(dialog._section_readers[0].native, LibraryDetailDialog)
    assert isinstance(dialog._section_readers[1].native, PersonalQuestionReader)
    labels = "\n".join(widget.text() for widget in dialog.findChildren(QLabel))
    assert "替换前共同材料" in labels and "替换前完整题面" in labels and "替换前来源答案" in labels
    assert "请核对本次配分" in dialog.evidence.text()
    assert "K10" in dialog.evidence.text()
    dialog.read_confirm.setChecked(True)
    dialog.goal_confirm.setChecked(True)
    dialog.commit()
    assert not dialog.commit_button.isEnabled() and not saved
    assert not any(call[0] == "commit" for call in facade.calls)
    dialog.replace_confirm.setChecked(True)
    assert dialog.commit_button.isEnabled()
    dialog.commit_button.click()
    assert not dialog.query.isEnabled() and not dialog.commit_button.isEnabled()
    tasks.flush()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert saved == [{"status": "saved", "paper_id": "independent-paper-synthetic", "new_key": "Q1",
                      "revision": "r8", "preview_invalidated": True}]
    assert ("commit", "replacement-plan-1", "c" * 64) in facade.calls
    prepare = next(call for call in facade.calls if call[0] == "prepare")
    assert prepare[1:3] == ("independent-paper-synthetic", "old-theme")
    assert prepare[3] == {"text": "增加对平衡移动原因的解释练习", "query": "平衡", "knowledge": []}
    assert prepare[4] == facade.entry


@pytest.mark.parametrize("lane", ["master", "wave1", "supplemental"])
def test_formal_candidates_keep_the_actual_full_theme_reader(qt_app, lane):
    dialog, facade, tasks = _dialog(_Facade(_entry(lane, "formal-theme")))
    _prepared(dialog, tasks)
    assert isinstance(dialog._reader, LibraryDetailDialog)
    assert dialog._candidate_ready and all(reader.ready for reader in dialog._section_readers)
    assert ("formal_detail", "formal-theme", lane) in facade.calls
    dialog.reject()


@pytest.mark.parametrize("mode", ["empty", "read_failure", "cancel"])
def test_empty_failed_or_cancelled_selection_never_saves_or_writes_basket(qt_app, mode):
    dialog, facade, tasks = _dialog()
    if mode == "empty":
        facade.entries = []
    _search(dialog, tasks)
    if mode == "empty":
        assert dialog.results.count() == 0
        assert "真实候选" in dialog.status.text() and "当前卷保持原样" in dialog.status.text()
    elif mode == "read_failure":
        facade.read_failed = True
        dialog.read_button.click()
        tasks.flush()
        assert not dialog._candidate_ready and not dialog.prepare_button.isEnabled()
    else:
        dialog.read_button.click()
        dialog.reject()
        tasks.finish("读取替题候选原来源", allow_cancelled=True)
        assert dialog._reader is None and dialog.reading.count() == 0
    _confirm(dialog)
    dialog.commit()
    assert not any(call[0] in {"prepare", "commit", "restore"} for call in facade.calls)
    dialog.reject()


def test_changed_search_and_goal_ignore_late_cancelled_callbacks(qt_app):
    dialog, facade, tasks = _dialog()
    dialog.search()
    dialog.query.setText("新关键词")
    tasks.finish("检索完整段替题候选", allow_cancelled=True)
    assert dialog.results.count() == 0 and dialog._search_result is None
    _read(dialog, tasks)
    dialog.prepare_button.click()
    dialog.goal.setPlainText("修改后的教师目标")
    tasks.finish("核对完整段替换计划", allow_cancelled=True)
    assert dialog._plan is None and dialog.reading.count() == 1 and not dialog.commit_button.isEnabled()
    dialog.prepare_button.click()
    tasks.flush()
    _confirm(dialog)
    assert dialog.commit_button.isEnabled()
    dialog.query.setText("再次变化")
    assert dialog._plan is None and dialog.results.count() == 0
    assert not dialog.commit_button.isEnabled() and not dialog.read_confirm.isChecked()
    dialog.reject()


def test_late_previous_candidate_detail_cannot_attach_to_new_selection(qt_app):
    dialog, facade, tasks = _dialog()
    facade.entries.append(_entry(key="Q2"))
    _search(dialog, tasks)
    dialog.read_button.click()
    dialog.results.setCurrentRow(1)
    tasks.finish("读取替题候选原来源", allow_cancelled=True)
    assert dialog._reader is None and dialog._candidate is None and not dialog.prepare_button.isEnabled()
    dialog.reject()


def test_wrong_candidate_revision_and_incomplete_plan_never_unlock_save(qt_app):
    dialog, facade, tasks = _dialog()
    _search(dialog, tasks)
    read = facade.word_question_detail
    facade.word_question_detail = lambda key, revision: {**read(key, revision), "revision": "changed-revision"}
    dialog.read_button.click()
    tasks.flush()
    assert dialog._reader is None and not dialog.prepare_button.isEnabled()
    assert "候选来源或版本已变化" in dialog.status.text()
    facade.word_question_detail = read
    dialog.read_button.click()
    tasks.flush()
    prepare = facade.prepare_question_replacement
    def incomplete(*args, **kwargs):
        result = prepare(*args, **kwargs)
        result.pop("before_projection")
        return result
    facade.prepare_question_replacement = incomplete
    dialog.prepare_button.click()
    tasks.flush()
    assert dialog._plan is None and not dialog.read_confirm.isEnabled()
    assert "完整替换计划未能核对" in dialog.status.text()
    _confirm(dialog)
    dialog.commit()
    assert not any(call[0] == "commit" for call in facade.calls)
    dialog.reject()


def test_closed_dialog_ignores_late_search_result(qt_app):
    dialog, _, tasks = _dialog()
    dialog.search_button.click()
    dialog.reject()
    tasks.finish("检索完整段替题候选", allow_cancelled=True)
    assert dialog._search_result is None and dialog.results.count() == 0
    assert dialog._reader is None and not dialog.commit_button.isEnabled()


def test_word_question_and_available_answer_images_must_both_finish(qt_app):
    dialog, facade, tasks = _dialog(_Facade(_entry(images=True)))
    _search(dialog, tasks)
    dialog.read_button.click()
    tasks.finish("读取替题候选原来源")
    assert not dialog.prepare_button.isEnabled()
    tasks.finish("读取本题图片")
    assert not dialog.prepare_button.isEnabled()
    tasks.finish("读取本题图片")
    assert dialog.prepare_button.isEnabled()
    assert {call[-1] for call in facade.calls if call[0] == "word_image"} == {"Q1-question", "Q1-answer"}
    dialog.reject()


@pytest.mark.parametrize("outcome", ["success", "failure", "no_loader"])
def test_formal_available_answer_images_are_loaded_before_candidate_is_ready(qt_app, outcome):
    facade = _FormalAnswerFacade(_entry("master", "formal-theme"))
    if outcome == "failure":
        facade.failed_asset = "formal-theme-answer-2"
    elif outcome == "no_loader":
        facade.library_answer_image = None
    dialog, _, tasks = _dialog(facade)
    _search(dialog, tasks)
    dialog.read_button.click()
    tasks.finish("读取替题候选原来源")
    assert isinstance(dialog._reader, LibraryDetailDialog)
    assert dialog._reader.preview_readiness[0]
    assert not dialog.prepare_button.isEnabled() and not dialog._candidate_ready
    if outcome != "no_loader":
        assert len(tasks.pending) == 2
        tasks.finish("读取合成来源答案图")
        assert not dialog.prepare_button.isEnabled()
        tasks.finish("读取合成来源答案图")
        assert {call[1] for call in facade.calls if call[0] == "formal_answer_image"} == {
            "formal-theme-answer-1", "formal-theme-answer-2"}
    else:
        assert not tasks.pending
        assert not [call for call in facade.calls if call[0] == "formal_answer_image"]
    assert dialog.prepare_button.isEnabled() is (outcome == "success")
    if outcome == "success":
        dialog.prepare_button.click()
        tasks.flush()
        assert all(reader.ready for reader in dialog._section_readers)
        _confirm(dialog)
        assert dialog.commit_button.isEnabled()
    else:
        dialog.prepare()
        assert not any(call[0] == "prepare" for call in facade.calls)
    dialog.reject()


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("outcome", ["failure", "no_loader"])
def test_formal_before_and_after_answer_images_each_block_confirmation(qt_app, side, outcome):
    facade = _FormalAnswerFacade(_entry("master", "formal-theme"))
    dialog, _, tasks = _dialog(facade)
    _read(dialog, tasks)
    assert dialog._candidate_ready
    if outcome == "no_loader":
        facade.library_answer_image = None
    else:
        facade.failed_asset = ("old-theme" if side == "before" else "formal-theme") + "-answer-2"
    dialog.prepare_button.click()
    tasks.finish("核对完整段替换计划")
    assert not dialog.read_confirm.isEnabled()
    tasks.flush()
    index = 0 if side == "before" else 1
    assert not dialog._section_readers[index].ready
    assert not dialog.read_confirm.isEnabled() and not dialog.goal_confirm.isEnabled()
    _confirm(dialog)
    assert not dialog.commit_button.isEnabled()
    dialog.commit()
    assert not any(call[0] == "commit" for call in facade.calls)
    dialog.reject()


@pytest.mark.parametrize("phase", ["candidate", "after"])
@pytest.mark.parametrize("issue", ["unsupported_asset", "missing_asset_id", "formula_warning"])
def test_word_unsupported_answer_only_content_blocks_replacement(qt_app, phase, issue):
    facade = _Facade(_entry())
    dialog, _, tasks = _dialog(facade)
    if phase == "after":
        _read(dialog, tasks)
        assert dialog._candidate_ready
    block = facade.entry["payload"]["answer_blocks"][0]
    if issue == "formula_warning":
        block["warnings"] = ["合成公式对象未能提取"]
    else:
        block["assets"] = [{"asset_id": "unsupported-answer" if issue == "unsupported_asset" else "",
                            "mime_type": "image/tiff", "preview_supported": False}]
    facade.entries = [deepcopy(facade.entry)]
    if phase == "candidate":
        _read(dialog, tasks)
        assert not dialog._candidate_ready and not dialog.prepare_button.isEnabled()
    else:
        dialog.prepare_button.click()
        tasks.flush()
        assert not dialog._section_readers[1].ready and not dialog.read_confirm.isEnabled()
    _confirm(dialog)
    assert not dialog.commit_button.isEnabled()
    dialog.commit()
    assert not any(call[0] == "commit" for call in facade.calls)
    dialog.reject()


@pytest.mark.parametrize("failed", [False, True])
def test_visual_replacement_binds_every_shared_question_and_answer_image(qt_app, failed):
    dialog, facade, tasks = _dialog(_Facade(_entry("visual_native", "V1")))
    _read(dialog, tasks)
    assert isinstance(dialog._reader, PersonalQuestionReader) and dialog._candidate_ready
    assert {call[-1] for call in facade.calls if call[0] == "visual_image"} == {
        "candidate-shared_material", "candidate-question", "candidate-answer"}
    if failed:
        facade.failed_asset = "plan-answer"
    dialog.prepare_button.click()
    tasks.finish("核对完整段替换计划")
    assert not dialog.read_confirm.isEnabled()
    tasks.flush()
    images = [call for call in facade.calls if call[0] == "plan_image"]
    assert {(call[3], call[4]) for call in images} == {
        ("plan-shared_material", "after"), ("plan-question", "after"), ("plan-answer", "after")}
    assert all(call[1:3] == ("replacement-plan-1", "c" * 64) for call in images)
    _confirm(dialog)
    assert dialog.commit_button.isEnabled() is (not failed)
    dialog.reject()


def test_plan_displays_literal_knowledge_provenance_and_complete_visual_source_text(qt_app):
    entry = _entry("visual_native", "V1")
    facade = _Facade(entry)
    after = _item(entry)
    after["content"].update(stem="图片主题完整题干文字", content=["来源材料甲", "来源材料乙"],
        response_requirements=["实际作答要求甲", "实际作答要求乙"],
        atomic_part_ids=["INTERNAL_ID_NOT_STUDENT_CONTENT"])
    facade.plan_patch = {"after_item": after, "match_evidence": {
        "text": "真实题面文字命中说明", "terms": ["平衡"], "knowledge": ["K03"],
        "knowledge_evidence": [
            {"unit_id": "source-unit-1", "field": "primary_knowledge.id", "values": ["K03"],
             "status": "auto_suggested", "origin": "local_label_proposal"},
            {"unit_id": "source-unit-2", "field": "supporting_knowledge", "values": ["K03"],
             "status": "unknown", "origin": "unknown"}],
        "knowledge_notice": "来源候选标签不等于教师确认",
        "difficulty": "unknown", "student_diagnosis": "not_claimed"}}
    dialog, _, tasks = _dialog(facade)
    _prepared(dialog, tasks)
    evidence = dialog.evidence.text()
    for literal in ("真实题面文字命中说明", "平衡", "K03", "source-unit-1", "source-unit-2",
                    "primary_knowledge.id", "supporting_knowledge", "auto_suggested",
                    "local_label_proposal", "来源候选标签不等于教师确认"):
        assert literal in evidence
    assert evidence.count("来源状态：") == 1 and evidence.count("来源记录：") == 1
    assert "unknown" not in evidence and "teacher_confirmed" not in evidence
    labels = "\n".join(label.text() for label in dialog._section_readers[1].findChildren(QLabel))
    for literal in ("图片主题完整题干文字", "来源材料甲", "来源材料乙", "实际作答要求甲", "实际作答要求乙"):
        assert literal in labels
    assert "INTERNAL_ID_NOT_STUDENT_CONTENT" not in labels
    assert all(reader.ready for reader in dialog._section_readers)
    dialog.reject()


def test_failed_native_before_or_after_source_blocks_confirmations(qt_app):
    dialog, facade, tasks = _dialog()
    _read(dialog, tasks)
    dialog.prepare_button.click()
    tasks.finish("核对完整段替换计划")
    facade.read_failed = True
    tasks.flush()
    assert not all(reader.ready for reader in dialog._section_readers)
    assert not dialog.read_confirm.isEnabled() and not dialog.commit_button.isEnabled()
    dialog.reject()


def test_source_change_before_commit_clears_plan_without_saving(qt_app):
    dialog, facade, tasks = _dialog()
    _prepared(dialog, tasks)
    _confirm(dialog)
    facade.current = False
    dialog.commit_button.click()
    assert dialog._plan is None and dialog.results.count() == 0
    assert "候选来源已变化" in dialog.status.text()
    assert not any(call[0] == "commit" for call in facade.calls)
    dialog.reject()


def test_uncertain_commit_is_not_automatically_retried(qt_app):
    dialog, facade, tasks = _dialog()
    _prepared(dialog, tasks)
    _confirm(dialog)
    facade.commit_failed = True
    dialog.commit_button.click()
    tasks.flush()
    assert dialog._uncertain and "不会自动重试" in dialog.status.text()
    dialog.commit()
    dialog.search()
    assert not tasks.pending and sum(call[0] == "commit" for call in facade.calls) == 1
    dialog.reject()


def test_cancel_pending_commit_waits_for_cancellation_and_preserves_current_paper(qt_app):
    dialog, facade, tasks = _dialog()
    _prepared(dialog, tasks)
    _confirm(dialog)
    dialog.commit_button.click()
    dialog.reject()
    assert not dialog._closed and dialog._committing
    tasks.flush()
    assert not dialog._committing and dialog._plan is None
    assert not any(call[0] == "commit" for call in facade.calls)
    dialog.reject()


@pytest.mark.parametrize("changed", [False, True])
def test_version_restore_requires_selection_confirmation_and_read_revision(qt_app, changed):
    facade, tasks = _Facade(), _Queue()
    dialog = QuestionReplacementVersionsDialog(facade, tasks, "independent-paper-synthetic")
    saved = []
    dialog.saved.connect(saved.append)
    assert not dialog.restore_button.isEnabled()
    tasks.flush()
    dialog.confirm.setChecked(True)
    assert not dialog.restore_button.isEnabled()
    dialog.versions.setCurrentRow(0)
    assert not dialog.confirm.isChecked()
    dialog.confirm.setChecked(True)
    assert dialog.restore_button.isEnabled()
    if changed:
        facade.version_revision = "concurrent-revision"
    dialog.restore_button.click()
    assert not dialog.restore_button.isEnabled()
    tasks.flush()
    assert ("restore", "independent-paper-synthetic", "source-version-1", "paper-source-revision-7") in facade.calls
    if changed:
        assert not saved and dialog._revision is None and "不会自动重试" in dialog.status.text()
        dialog.restore()
        assert not tasks.pending
        dialog.reject()
    else:
        assert dialog.result() == QDialog.DialogCode.Accepted and saved[0]["preview_invalidated"]


def test_empty_versions_and_closed_list_callback_cannot_enable_restore(qt_app):
    facade, tasks = _Facade(), _Queue()
    facade.versions = []
    dialog = QuestionReplacementVersionsDialog(facade, tasks, "independent-paper-synthetic")
    tasks.flush()
    assert "尚无替题" in dialog.status.text() and not dialog.restore_button.isEnabled()
    dialog.reject()
    assert not any(call[0] == "restore" for call in facade.calls)


def _replacement_methods(facade):
    fixture = _Facade()
    for method in ("search_unified_questions", "prepare_question_replacement", "commit_question_replacement",
                   "list_question_replacement_versions", "restore_question_replacement_version",
                   "unified_question_search_is_current", "word_question_detail", "library_theme_detail",
                   "library_image", "word_question_image", "personal_visual_question_detail",
                   "personal_visual_question_image", "question_replacement_image"):
        setattr(facade, method, getattr(fixture, method))
    return fixture


def test_pending_title_is_saved_before_replacement_and_conflict_blocks_entry(workspace):
    shell, _, service, store, _, tasks = workspace
    _replacement_methods(service.facade)
    panel = create(workspace)
    panel.title.setText("替换前待保存的题名")
    assert panel._pending_title_fields == {"title"}
    panel.replace_button.click()
    assert panel._replacement_dialog is not None and not panel._pending_title_fields
    key = panel.facade.paper_id + ":" + DRAFT_ID
    assert store.draft_snapshot(key).record["payload"]["settings_ui"]["title"] == "替换前待保存的题名"
    panel._replacement_dialog.reject()
    panel.title.setText("并发冲突中的新题名")
    record = store.draft_snapshot(key).record
    record["payload"]["settings_ui"]["subtitle"] = "另一窗口已保存"
    store.save_draft(key, record)
    frozen = store.path.read_bytes()
    panel.replace_button.click()
    assert panel._replacement_dialog is None and panel._restore_failed
    assert not tasks.pending and store.path.read_bytes() == frozen
    assert "尚未保存" in panel.status.text()
    # Avoid fixture teardown attempting to flush the intentionally conflicted title.
    panel._pending_title_fields.clear()


def test_saved_replacement_reopens_real_independent_session_and_invalidates_preview(workspace):
    shell, _, service, store, _, tasks = workspace
    _replacement_methods(service.facade)
    panel = create(workspace)
    identity = panel.facade.paper_id
    basket = deepcopy(store.basket())
    panel._preview = object()
    panel._approved = True
    panel._artifact_paths = {"student": "E:/任务输出/synthetic.docx"}
    key = identity + ":" + DRAFT_ID
    record = store.draft_snapshot(key).record
    record["payload"]["settings_ui"]["title"] = "已保存的新来源版本题名"
    store.save_draft(key, record)
    selected = panel.model.order[-1]
    panel._replacement_committed({"status": "saved", "paper_id": identity,
                                  "new_key": selected, "revision": "saved-r8", "preview_invalidated": True})
    assert panel._preview is None and not panel._approved and panel._artifact_paths == {}
    assert panel._restore_failed and not panel.export_button.isEnabled()
    tasks.flush()
    fresh = shell._mixed_panel
    assert fresh is not panel and fresh.facade.paper_id == identity
    assert not fresh._restore_failed and fresh.title.text() == "已保存的新来源版本题名"
    assert fresh._current_key() == selected and fresh._preview is None and not fresh._approved
    assert "旧预览确认已失效" in fresh.status.text()
    assert store.basket() == basket
    assert shell.saved_papers.currentData() == identity


def test_workspace_versions_entry_preserves_pending_title_and_cancelled_paper(workspace):
    shell, _, service, store, _, tasks = workspace
    _replacement_methods(service.facade)
    panel = create(workspace)
    panel.title.setText("查看版本前题名")
    shell.versions_button.click()
    tasks.flush()
    assert shell._versions_dialog is not None
    assert shell._versions_dialog.paper_id == panel.facade.paper_id
    key = panel.facade.paper_id + ":" + DRAFT_ID
    assert store.draft_snapshot(key).record["payload"]["settings_ui"]["title"] == "查看版本前题名"
    before = store.path.read_bytes()
    shell._versions_dialog.reject()
    assert shell._versions_dialog is None and shell._mixed_panel is panel
    assert store.path.read_bytes() == before


def test_workspace_version_restore_reopens_same_saved_paper_and_clears_old_preview(workspace):
    shell, _, service, store, _, tasks = workspace
    fixture = _replacement_methods(service.facade)
    panel = create(workspace)
    identity, basket = panel.facade.paper_id, deepcopy(store.basket())
    panel._preview, panel._approved = object(), True
    shell.versions_button.click()
    tasks.flush()
    dialog = shell._versions_dialog
    dialog.versions.setCurrentRow(0)
    dialog.confirm.setChecked(True)
    dialog.restore_button.click()
    tasks.flush()
    fresh = shell._mixed_panel
    assert fresh is not panel and fresh.facade.paper_id == identity
    assert not fresh._restore_failed and fresh._preview is None and not fresh._approved
    assert shell._versions_dialog is None and store.basket() == basket
    assert ("restore", identity, "source-version-1", "paper-source-revision-7") in fixture.calls


@pytest.mark.parametrize("operation", ["commit", "restore"])
def test_unconfirmed_save_locks_actual_parent_and_keeps_old_preview_disabled_after_close(workspace, operation):
    shell, _, service, store, _, tasks = workspace
    fixture = _replacement_methods(service.facade)
    panel = create(workspace)
    identity = panel.facade.paper_id
    panel._preview, panel._approved = object(), True
    panel._artifact_paths = {key: f"E:/任务输出/synthetic-{key}" for key in panel.artifact_buttons}
    for button in panel.artifact_buttons.values():
        button.show()
    panel._update_actions()
    assert panel.export_button.isEnabled()
    old_reader = QDialog(panel)
    panel._preview_dialog = old_reader
    old_reader.show()
    before = store.path.read_bytes()
    if operation == "commit":
        fixture.commit_failed = True
        panel.replace_button.click()
        dialog = panel._replacement_dialog
        dialog.goal.setPlainText("合成教师目标")
        dialog.query.setText("平衡")
        dialog.search_button.click()
        tasks.flush()
        dialog.read_button.click()
        tasks.flush()
        dialog.prepare_button.click()
        tasks.flush()
        _confirm(dialog)
        assert dialog.commit_button.isEnabled()
        dialog.commit_button.click()
    else:
        shell.versions_button.click()
        tasks.flush()
        dialog = shell._versions_dialog
        dialog.versions.setCurrentRow(0)
        dialog.confirm.setChecked(True)
        fixture.version_revision = "concurrent-paper-revision"
        dialog.restore_button.click()
    tasks.flush()
    assert panel._restore_failed and panel._draft_session.current is None
    assert panel._preview is None and not panel._approved and panel._artifact_paths == {}
    assert panel._preview_dialog is None and not old_reader.isVisible()
    assert all(button.isHidden() for button in panel.artifact_buttons.values())
    assert not panel.preview_button.isEnabled() and not panel.export_button.isEnabled()
    assert not panel.save_button.isEnabled() and not panel.title.isEnabled()
    assert not shell.versions_button.isEnabled()
    assert "保存结果未能确认" in panel.status.text()
    assert store.path.read_bytes() == before
    dialog.reject()
    assert not panel._busy and panel._restore_failed
    assert not panel.export_button.isEnabled() and not panel.title.isEnabled()
    assert not tasks.pending and store.path.read_bytes() == before
    assert shell._mixed_panel is panel
    shell.refresh(selected=identity)
    shell.open_button.click()
    tasks.flush()
    fresh = shell._mixed_panel
    assert fresh is not panel and fresh.facade.paper_id == identity and not fresh._restore_failed
    assert fresh._preview is None and not fresh._approved and not fresh.export_button.isEnabled()


def test_unrelated_loaded_paper_does_not_receive_another_papers_selection_or_notice(workspace):
    shell, _, service, _, _, _ = workspace
    _replacement_methods(service.facade)
    first = create(workspace)
    first_id = first.facade.paper_id
    second = create(workspace)
    second.sections.setCurrentRow(0)
    selected = second._current_key()
    second.status.setText("另一份卷自己的状态")
    shell._reopen_notice = {"paper_id": first_id, "new_key": second.model.order[-1]}
    shell._loaded(second, True, False)
    assert second._current_key() == selected
    assert second.status.text() == "另一份卷自己的状态"
    assert shell._reopen_notice is None


@pytest.mark.parametrize("entry", ["open", "create"])
def test_ordinary_open_or_create_discards_stale_replacement_notice(workspace, entry):
    shell, _, service, _, _, tasks = workspace
    _replacement_methods(service.facade)
    panel = create(workspace)
    identity = panel.facade.paper_id
    panel.sections.setCurrentRow(0)
    shell._reopen_notice = {"paper_id": identity, "new_key": panel.model.order[-1]}
    if entry == "open":
        shell.refresh(selected=identity)
        shell.open_button.click()
    else:
        shell.new_button.click()
    assert shell._reopen_notice is None
    tasks.flush()
    assert "已重开保存后的完整来源版本" not in shell._mixed_panel.status.text()
    assert shell._mixed_panel._current_key() == shell._mixed_panel.model.order[0]


@pytest.mark.parametrize("field", ["paper_id", "target_key"])
def test_plan_for_another_paper_or_target_cannot_unlock_commit(qt_app, field):
    dialog, facade, tasks = _dialog()
    facade.plan_patch = {field: "different-identity"}
    _prepared(dialog, tasks)
    assert dialog._plan is None and not dialog.read_confirm.isEnabled()
    _confirm(dialog)
    dialog.commit()
    assert not any(call[0] == "commit" for call in facade.calls)
    dialog.reject()


@pytest.mark.parametrize("operation", ["commit", "restore"])
def test_saved_response_for_another_paper_is_unconfirmed_and_not_accepted(qt_app, operation):
    facade, tasks = _Facade(), _Queue()
    if operation == "commit":
        dialog, _, tasks = _dialog(facade)
        _prepared(dialog, tasks)
        _confirm(dialog)
        facade.commit_question_replacement = lambda *_args: {"status": "saved", "paper_id": "another-paper"}
    else:
        dialog = QuestionReplacementVersionsDialog(facade, tasks, "independent-paper-synthetic")
        tasks.flush()
        dialog.versions.setCurrentRow(0)
        dialog.confirm.setChecked(True)
        facade.restore_question_replacement_version = lambda *_args, **_kwargs: {
            "status": "saved", "paper_id": "another-paper"}
    saved, unconfirmed = [], []
    dialog.saved.connect(saved.append)
    dialog.save_unconfirmed.connect(unconfirmed.append)
    (dialog.commit_button if operation == "commit" else dialog.restore_button).click()
    tasks.flush()
    assert not saved and len(unconfirmed) == 1
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "不会自动重试" in dialog.status.text()
    dialog.reject()


@pytest.mark.parametrize("independent,paper_id", [(False, "legacy-paper"), (True, None)])
def test_replacement_entry_is_hidden_without_a_real_independent_paper(qt_app, independent, paper_id):
    facade, tasks = _Facade(), _Queue()
    facade.independent_paper, facade.paper_id = independent, paper_id
    panel = MixedPaperPanel(facade, tasks, PaperComposerModel())
    assert panel.replace_button.isHidden() and not panel.replace_button.isEnabled()
    panel._replace_complete_section()
    assert panel._replacement_dialog is None and not tasks.pending
    panel.close()


@pytest.mark.parametrize("state", ["initial", "prepared", "failed"])
def test_narrow_replacement_footer_is_reachable_and_fixed_while_body_scrolls(qt_app, state):
    dialog, facade, tasks = _dialog()
    if state == "prepared":
        _prepared(dialog, tasks)
        _confirm(dialog)
    elif state == "failed":
        facade.read_failed = True
        _read(dialog, tasks)
    dialog.resize(360, 520)
    dialog.show()
    for _ in range(8):
        qt_app.processEvents()
    assert (dialog.width(), dialog.height()) == (360, 520)
    controls = (dialog.status, dialog.cancel_button, dialog.commit_button)
    geometry = [QRect(widget.mapTo(dialog, QPoint()), widget.size()) for widget in controls]
    assert all(dialog.rect().contains(rect) for rect in geometry)
    assert geometry[0].bottom() < geometry[1].top() < geometry[2].top()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert dialog.scroll.verticalScrollBar().maximum() > 0
    dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
    qt_app.processEvents()
    assert [QRect(widget.mapTo(dialog, QPoint()), widget.size()) for widget in controls] == geometry
    assert dialog.commit_button.isEnabled() is (state == "prepared")
    dialog.reject()
