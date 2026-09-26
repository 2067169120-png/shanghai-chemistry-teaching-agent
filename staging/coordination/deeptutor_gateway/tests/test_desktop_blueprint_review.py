import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_desktop_blueprint_generation import (  # noqa: F401
    candidate,
    configured,
    desktop_paths,
    preview,
)
from test_desktop_preparation_provider import _context, _Transport

from integrations.deeptutor_shchem_v1.desktop_blueprint_generation import (
    BlueprintGenerationError,
)
from integrations.deeptutor_shchem_v1.desktop_blueprint_review import (
    REVIEW_KIND,
    candidate_revision,
    format_review,
    review_blueprint,
    review_evidence,
    review_prompt,
    validate_review,
)
from integrations.deeptutor_shchem_v1.desktop_chemistry_prompt_rules import (
    CHEMISTRY_CONSISTENCY_RULES,
    TEACHING_SOURCE_RULES,
)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError


def report():
    revised = candidate()
    revised["question_chain"][0]["answer_outline"] = (
        "分取新样，先排除试剂引入待测离子的风险。"
    )
    return {
        "summary": "已修订检验方案，仍需教师核验。",
        "issues": [
            {
                "issue_id": "R1",
                "atomic_part_ids": ["Q1-A1"],
                "material_ids": ["M1"],
                "severity": "error",
                "category": "reagent_interference",
                "diagnosis": "试剂引入待测离子，不能证明原样含该离子。",
                "correction": "分取新样，换用不引入待测离子的试剂。",
                "evidence_refs": ["E1"],
            }
        ],
        "revised_blueprint": revised,
    }


class ReviewTransport(_Transport):
    def __init__(self):
        super().__init__({})

    def send(self, request, **kwargs):
        name = json.loads(request.body)["text"]["format"]["name"]
        value = report()
        self.candidate = (
            {key: value[key] for key in ("summary", "issues")}
            if name == "shchem_blueprint_diagnosis_v1"
            else value["revised_blueprint"]
        )
        return super().send(request, **kwargs)


def test_review_receives_original_evidence_and_never_implies_approval():
    original, local = candidate(), preview()
    before = deepcopy(original)
    transport = ReviewTransport()
    result = review_blueprint(
        _context(), local, original, focus="检查试剂引入", transport=transport
    )
    assert len(transport.requests) == 2
    for request in transport.requests:
        text = json.dumps(json.loads(request.body), ensure_ascii=False).replace(
            "\\n", "\n"
        )
        assert CHEMISTRY_CONSISTENCY_RULES in text
        assert TEACHING_SOURCE_RULES in text
    assert json.loads(transport.requests[0].body)["max_output_tokens"] == 24000
    assert json.loads(transport.requests[1].body)["max_output_tokens"] == 32000
    assert (
        json.loads(transport.requests[1].body)["text"]["format"]["name"]
        == "shchem_blueprint_revision_v1"
    )
    assert "saved_diagnosis" in transport.requests[1].body.decode()
    assert result["usage"]["total_tokens"] == 60
    body = json.loads(transport.requests[0].body)
    text = json.dumps(body, ensure_ascii=False)
    assert "试剂引入" in text and "共同语境" in text and "电子转移" in text
    assert (
        "private" not in text
        and "source_path" not in text
        and "input_image" not in text
    )
    assert result["chemistry_correctness_verified"] is False
    assert result["teacher_review_required"] is True
    assert result["publication_allowed"] is result["bank_ingest_allowed"] is False
    assert result["source_candidate_revision"] == candidate_revision(original)
    assert original == before
    assert "AI审校" in format_review(result)


@pytest.mark.parametrize(
    "mutation",
    [
        "bad_part",
        "bad_material",
        "bad_source",
        "blank_reason",
        "duplicate",
        "drop_part",
        "bad_dependency",
        "bad_field",
    ],
)
def test_bad_review_not_saved_as_valid(mutation):
    value = report()
    issue = value["issues"][0]
    if mutation == "bad_part":
        issue["atomic_part_ids"] = ["Q999"]
    elif mutation == "bad_material":
        issue["material_ids"] = ["M999"]
    elif mutation == "bad_source":
        issue["evidence_refs"] = ["E999"]
    elif mutation == "blank_reason":
        issue["diagnosis"] = " "
    elif mutation == "duplicate":
        value["issues"] *= 2
    elif mutation == "drop_part":
        value["revised_blueprint"]["question_chain"][0]["atomic_part_id"] = "renumbered"
    elif mutation == "bad_dependency":
        value["revised_blueprint"]["question_chain"][0]["depends_on"] = ["Q999"]
    else:
        value["approved"] = True
    with pytest.raises(BlueprintGenerationError):
        validate_review(value, candidate(), 1)


def test_empty_issues_are_not_a_correctness_certificate():
    value = report()
    value["issues"] = []
    validate_review(value, candidate(), 1)
    assert "不证明" in format_review({"report": value})


def test_review_compacts_contracts_not_teacher_sources_or_ids():
    local, original = preview(), candidate()
    local["task_prompt"] = "REPEATED_GENERATION_RUBRIC" * 1000
    local["evidence"].extend(
        [
            {"source_type": "local_contract", "supports": ["OMIT_UNUSED"]},
            {"source_type": "local_contract", "supports": ["KEEP_REFERENCED"]},
            {
                "source_type": "user_handout_question_reference",
                "supports": ["FULL_QUESTION_AND_ANSWER"],
            },
            {"source_type": "textbook", "supports": ["FULL_TEXTBOOK"]},
        ]
    )
    original["question_chain"][0]["knowledge_evidence"] += " E3"
    before = deepcopy(local)
    request = {
        "learning_goal": "TEACHER_GOAL",
        "context": "TEACHER_CONTEXT",
        "handout_reference": {"private_path": "SECRET_LOCAL_BINDING"},
    }
    text = review_prompt(local, original, "", teacher_request=request)
    for required in (
        "TEACHER_GOAL",
        "TEACHER_CONTEXT",
        "KEEP_REFERENCED",
        "FULL_QUESTION_AND_ANSWER",
        "FULL_TEXTBOOK",
    ):
        assert required in text
    for omitted in (
        "REPEATED_GENERATION_RUBRIC",
        "OMIT_UNUSED",
        "SECRET_LOCAL_BINDING",
    ):
        assert omitted not in text
    assert [e["evidence_id"] for e in review_evidence(local, original)] == [
        "E1",
        "E3",
        "E4",
        "E5",
    ]
    assert local == before


@pytest.mark.parametrize("target", ["issue", "material"])
def test_review_rejects_reference_to_omitted_contract(target):
    value = report()
    if target == "issue":
        value["issues"][0]["evidence_refs"] = ["E2"]
    else:
        value["revised_blueprint"]["shared_material_plan"][0]["evidence_refs"] = ["E2"]
    with pytest.raises(BlueprintGenerationError, match="资料"):
        validate_review(value, candidate(), 3, evidence_ids={"E1", "E3"})


@pytest.fixture
def ready(configured):  # noqa: F811 - imported pytest fixture
    facade, provider, local = configured
    generated = facade.generate_prompt_blueprint(
        local["preview_id"], "teacher-text", provider.revision, teacher_confirmed=True
    )
    facade._blueprint_review_transport = ReviewTransport()
    return facade, provider, local, generated


def invoke(ready, **changes):
    facade, provider, local, generated = ready
    values = {
        "preview_id": local["preview_id"],
        "source_revision": candidate_revision(generated["candidate"]),
        "profile_id": "teacher-text",
        "expected_profile_revision": provider.revision,
        "teacher_confirmed": True,
        "focus": "检查检验流程",
    }
    values.update(changes)
    return facade.review_prompt_blueprint(**values)


@pytest.mark.parametrize(
    "change",
    [
        {"teacher_confirmed": False},
        {"source_revision": "stale"},
        {"preview_id": "missing"},
        {"expected_profile_revision": "stale"},
        {"focus": "字" * 2001},
        {"should_cancel": lambda: True},
    ],
)
def test_preconditions_precede_credential_and_state_write(ready, change):
    facade, provider, _, _ = ready
    before = facade.state_store.snapshot()
    borrowed = len(provider.borrow_calls)
    with pytest.raises(DesktopFacadeError):
        invoke(ready, **change)
    assert len(provider.borrow_calls) == borrowed
    assert facade._blueprint_review_transport.requests == []
    assert facade.state_store.snapshot() == before


def test_review_append_and_reopen_without_overwriting_original_or_new_model_call(ready):
    facade, _provider, local, original = ready
    before = deepcopy(facade.state_store.snapshot()["drafts"][local["preview_id"]])
    result = invoke(ready)
    assert facade.state_store.snapshot()["drafts"][local["preview_id"]] == before
    assert result["candidate"] != original["candidate"]
    history = facade.prompt_blueprint_reviews(
        local["preview_id"], candidate_revision(original["candidate"])
    )
    assert len(history) == 1 and history[0]["result"] == result
    assert len(facade._blueprint_review_transport.requests) == 2
    assert (
        facade.prompt_blueprint_reviews(local["preview_id"], "different-source") == []
    )
    assert facade.prompt_blueprint_history()[0]["result"] == original


def test_bad_review_failure_keeps_original_and_allows_explicit_next_attempt(ready):
    facade, _, local, original = ready
    facade._blueprint_review_transport = _Transport({"private": "fixture diagnostic"})
    with pytest.raises(DesktopFacadeError):
        invoke(ready)
    reviews = [
        r
        for r in facade.state_store.snapshot()["drafts"].values()
        if r.get("kind") == REVIEW_KIND
    ]
    assert (
        len(reviews) == 1
        and reviews[0]["status"] == "failed"
        and "result" not in reviews[0]
    )
    assert "fixture diagnostic" not in facade.state_store.path.read_text(
        encoding="utf-8"
    )
    assert (
        facade.state_store.snapshot()["drafts"][local["preview_id"]]["result"]
        == original
    )
    facade._blueprint_review_transport = ReviewTransport()
    invoke(ready)
    assert (
        len(
            facade.prompt_blueprint_reviews(
                local["preview_id"], candidate_revision(original["candidate"])
            )
        )
        == 1
    )


def test_inflight_cancel_and_busy_do_not_persist_success(ready):
    facade, _, _, _ = ready
    facade._blueprint_generation_lock.acquire()
    try:
        with pytest.raises(DesktopFacadeError) as failed:
            invoke(ready)
        assert failed.value.code == "blueprint_busy"
    finally:
        facade._blueprint_generation_lock.release()
    stopped = [False]

    class StopTransport(_Transport):
        def send(self, *args, **kwargs):
            response = super().send(*args, **kwargs)
            stopped[0] = True
            return response

    facade._blueprint_review_transport = StopTransport(report())
    with pytest.raises(DesktopFacadeError):
        invoke(ready, should_cancel=lambda: stopped[0])
    reviews = [
        r
        for r in facade.state_store.snapshot()["drafts"].values()
        if r.get("kind") == REVIEW_KIND
    ]
    assert reviews[0]["status"] == "cancelled" and "result" not in reviews[0]


def fail_revision(ready):
    facade, _, local, generated = ready

    class FailsSecond(ReviewTransport):
        def send(self, request, **kwargs):
            name = json.loads(request.body)["text"]["format"]["name"]
            if name == "shchem_blueprint_revision_v1":
                # The checkpoint must already exist on disk before request two.
                records = facade.prompt_blueprint_reviews(
                    local["preview_id"], candidate_revision(generated["candidate"])
                )
                assert (
                    len(records) == 1
                    and records[0]["diagnosis_result"]["candidate"]["issues"]
                )
                self.candidate = {"invalid_revision": True}
                return _Transport.send(self, request, **kwargs)
            return super().send(request, **kwargs)

    facade._blueprint_review_transport = FailsSecond()
    with pytest.raises(DesktopFacadeError):
        invoke(ready)
    return facade.prompt_blueprint_reviews(
        local["preview_id"], candidate_revision(generated["candidate"])
    )[0]


def test_revision_retry_reuses_persisted_diagnosis_and_completed_retry_is_free(ready):
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore

    facade, provider, _, _ = ready
    record = fail_revision(ready)
    assert record["status"] == "failed" and record["stage"] == "revision"
    assert record["attempts"][0]["failed_stage"] == "revision"
    checkpoint = deepcopy(record["diagnosis_result"])
    facade._state = DesktopStateStore(facade.state_store.root)
    facade._blueprint_review_transport = ReviewTransport()
    result = invoke(ready, resume_review_id=record["review_id"])
    assert len(facade._blueprint_review_transport.requests) == 1
    assert (
        "saved_diagnosis"
        in facade._blueprint_review_transport.requests[0].body.decode()
    )
    saved = facade.state_store.snapshot()["drafts"][record["review_id"]]
    assert saved["diagnosis_result"] == checkpoint
    assert saved["status"] == "completed" and len(saved["attempts"]) == 2
    borrowed = len(provider.borrow_calls)
    assert invoke(ready, resume_review_id=record["review_id"]) == result
    assert len(provider.borrow_calls) == borrowed
    assert len(facade._blueprint_review_transport.requests) == 1


@pytest.mark.parametrize("mismatch", ["focus", "missing", "bad_diagnosis"])
def test_resume_rejects_wrong_checkpoint_before_new_call_or_state_write(
    ready, mismatch
):
    facade, provider, _, _ = ready
    record = fail_revision(ready)
    changes = {"resume_review_id": record["review_id"]}
    if mismatch == "focus":
        changes["focus"] = "changed"
    elif mismatch == "missing":
        changes["resume_review_id"] = "missing"
    else:
        record["diagnosis_result"]["candidate"]["issues"][0]["atomic_part_ids"] = [
            "missing"
        ]
        facade.state_store.save_draft(record["review_id"], record)
    before = facade.state_store.snapshot()
    count = len(provider.borrow_calls)
    requests = len(facade._blueprint_review_transport.requests)
    with pytest.raises(DesktopFacadeError):
        invoke(ready, **changes)
    assert facade.state_store.snapshot() == before
    assert len(provider.borrow_calls) == count
    assert len(facade._blueprint_review_transport.requests) == requests


def test_cancel_between_stages_retains_diagnosis_and_sends_no_revision(ready):
    facade, _, local, generated = ready
    stopped = [False]

    def progress(value):
        if value["stage"] == "revision":
            stopped[0] = True

    with pytest.raises(DesktopFacadeError):
        invoke(ready, on_progress=progress, should_cancel=lambda: stopped[0])
    assert len(facade._blueprint_review_transport.requests) == 1
    record = facade.prompt_blueprint_reviews(
        local["preview_id"], candidate_revision(generated["candidate"])
    )[0]
    assert record["status"] == "cancelled" and record["diagnosis_result"]
    # A previous process dying with stage=revision is recoverable in the same way.
    record["status"] = "running"
    facade.state_store.save_draft(record["review_id"], record)
    result = invoke(ready, resume_review_id=record["review_id"])
    assert result["report"]["revised_blueprint"]
    assert len(facade._blueprint_review_transport.requests) == 2


def test_pending_diagnosis_ui_reopens_offline_and_only_resumes_revision(
    ready, monkeypatch
):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_review_dialog import (
        BlueprintReviewDialog,
    )

    app = QApplication.instance() or QApplication([])
    facade, provider, local, original = ready
    record = fail_revision(ready)

    class Tasks:
        def submit(self, label, operation, *, on_success, on_failure):
            on_success(operation())

        def submit_progress(
            self, label, operation, *, on_success, on_failure, on_progress=None
        ):
            on_success(operation(on_progress, lambda: False))

    profile = SimpleNamespace(
        provider_name="test",
        model_id="test",
        profile_id="teacher-text",
        revision=provider.revision,
    )
    offline = BlueprintReviewDialog(
        facade, Tasks(), local["preview_id"], original, None
    )
    offline.show()
    offline.history.setCurrentIndex(1)
    assert "试剂引入" in offline.findings.toPlainText()
    assert not offline.copy.isEnabled() and not offline.start.isEnabled()
    offline.close()
    facade._blueprint_review_transport = ReviewTransport()
    dialog = BlueprintReviewDialog(
        facade, Tasks(), local["preview_id"], original, profile
    )
    dialog.show()
    dialog.history.setCurrentIndex(1)
    assert dialog._resume_review_id == record["review_id"]
    assert dialog.start.text() == "继续修订" and not dialog.focus.isEnabled()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes
    )
    dialog.start.click()
    assert len(facade._blueprint_review_transport.requests) == 1
    assert dialog.copy.isEnabled() and "分取新样" in dialog.revised.toPlainText()
    dialog.history.setCurrentIndex(0)
    assert dialog._resume_review_id is None and dialog.focus.isEnabled()
    assert not dialog.copy.isEnabled() and not dialog.revised.toPlainText()
    dialog.close()
    app.processEvents()


def test_ui_confirmation_generation_history_and_offline_copy(ready, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_review_dialog import (
        BlueprintReviewDialog,
    )

    app = QApplication.instance() or QApplication([])
    facade, provider, local, original = ready
    profile = SimpleNamespace(
        provider_name="测试",
        model_id="test",
        profile_id="teacher-text",
        revision=provider.revision,
    )

    class Tasks:
        def submit(self, label, operation, *, on_success, on_failure):
            on_success(operation())

        def submit_progress(
            self, label, operation, *, on_success, on_failure, on_progress=None
        ):
            on_success(operation(on_progress or (lambda _: None), lambda: False))

    dialog = BlueprintReviewDialog(
        facade, Tasks(), local["preview_id"], original, profile
    )
    dialog.resize(420, 760)
    dialog.show()
    app.processEvents()
    assert dialog.width() == 420 and dialog.height() == 760
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No
    )
    dialog.start.click()
    assert not facade._blueprint_review_transport.requests
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes
    )
    dialog.start.click()
    assert "试剂引入" in dialog.findings.toPlainText()
    assert "分取新样" in dialog.revised.toPlainText()
    assert "分取新样" not in dialog.original.toPlainText()
    assert dialog.history.count() == 2
    dialog.close()
    reopened = BlueprintReviewDialog(
        facade, Tasks(), local["preview_id"], original, None
    )
    reopened.show()
    app.processEvents()
    assert not reopened.start.isEnabled()
    reopened.history.setCurrentIndex(1)
    reopened.copy.click()
    assert "尚未通过教师化学核验" in app.clipboard().text()
    assert len(facade._blueprint_review_transport.requests) == 2
    reopened.close()
    reopened._completed({})


def test_ui_close_requests_stop_and_prevents_duplicate_submission(ready, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_review_dialog import (
        BlueprintReviewDialog,
    )

    _app = QApplication.instance() or QApplication([])  # retain the Qt application
    facade, provider, local, original = ready
    profile = SimpleNamespace(
        provider_name="测试",
        model_id="test",
        profile_id="teacher-text",
        revision=provider.revision,
    )

    class Tasks:
        count = 0

        def submit(self, label, operation, *, on_success, on_failure):
            on_success(operation())

        def submit_progress(
            self, label, operation, *, on_success, on_failure, on_progress=None
        ):
            self.count += 1
            self.failed = on_failure

    tasks = Tasks()
    dialog = BlueprintReviewDialog(
        facade, tasks, local["preview_id"], original, profile
    )
    dialog.show()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes
    )
    dialog.start.click()
    dialog._start()
    assert tasks.count == 1
    dialog._history_failed("旧的历史刷新失败")
    assert dialog._running and not dialog.start.isEnabled()
    assert not dialog.close() and dialog._stop.is_set() and not dialog._closed
    tasks.failed("已取消")
    assert dialog.close()


def test_parent_blueprint_dialog_opens_review_and_waits_for_child_cancel(
    ready, monkeypatch
):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
        PromptBlueprintDialog,
    )

    app = QApplication.instance() or QApplication([])
    facade, _, local, original = ready

    class Tasks:
        def submit(self, label, operation, *, on_success, on_failure):
            on_success(operation())

        def submit_progress(
            self, label, operation, *, on_success, on_failure, on_progress=None
        ):
            self.failed = on_failure

    tasks = Tasks()
    parent = PromptBlueprintDialog(facade, tasks)
    parent._compiled(local)
    parent._generated(original)
    parent.show()
    parent.review_button.click()
    child = parent._review_dialog
    assert child is not None and child.original.toPlainText()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes
    )
    child.start.click()
    assert not parent.close()
    assert child._stop.is_set() and not parent._closed
    tasks.failed("已停止")
    assert parent.close()
    app.processEvents()
