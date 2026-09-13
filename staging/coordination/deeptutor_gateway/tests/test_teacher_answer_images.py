"""Teacher answer images stay source-bound and separate from student images."""

from __future__ import annotations

import hashlib
import os
import threading
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import reference_answer_images as policy
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_library import (
    LibraryPartDetail,
    LibraryThemeDetail,
    answer_image_descriptors,
    image_descriptors,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanError,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    MasterWave1WorkbenchReader,
)
from integrations.deeptutor_shchem_v1.shanghai_high_east2025_theme45_direct_visual_scan import (
    EXPECTED_ATOMIC_IDS,
    ShanghaiHighEast2025Theme45DirectVisualScanReader,
)

ROOT = Path(__file__).resolve().parents[4]
DB = ROOT / "sh-chem-db"
Q3 = "SHEAST2025-M05-B-T5-Q3-P1"
Q5 = "SHEAST2025-M05-B-T5-Q5-P1"


def _descriptor():
    return {
        **{k: policy.INLINE_ANSWERS[Q3][k] for k in ("crop_id", "sha256")},
        "evidence_role": "answer",
        "source_page": 2,
        "bytes": 38220,
        "width": 1080,
        "height": 115,
    }


def test_only_two_explicitly_inspected_answers_require_inline_images():
    assert set(policy.INLINE_ANSWERS) == {Q3, Q5}
    assert (
        policy.project_answer_image(Q3, _descriptor(), policy.ANSWER_PAGE_SHA256)[
            "display_mode"
        ]
        == "inline_required"
    )
    assert (
        policy.project_answer_image(
            "SHEAST2025-M05-B-T5-Q2-P1", _descriptor(), policy.ANSWER_PAGE_SHA256
        )["display_mode"]
        == "preview_only"
    )


@pytest.mark.parametrize(
    "field,value",
    [("crop_id", "wrong-crop"), ("sha256", "0" * 64), ("evidence_role", "question")],
)
def test_policy_drift_is_not_silently_downgraded(field, value):
    descriptor = _descriptor()
    descriptor[field] = value
    with pytest.raises(ValueError, match="mismatch"):
        policy.project_answer_image(Q3, descriptor, policy.ANSWER_PAGE_SHA256)


@pytest.mark.parametrize(
    "node,digest", [(Q3, "0" * 64), ("UNKNOWN", policy.ANSWER_PAGE_SHA256)]
)
def test_unrecognized_source_or_node_has_no_policy(node, digest):
    with pytest.raises(ValueError, match="source_mismatch"):
        policy.project_answer_image(node, _descriptor(), digest)


def test_saved_snapshot_tracks_only_affected_answer_policy(monkeypatch):
    assert policy.answer_presentation_fingerprint({"atomic_part_id": "UNKNOWN"}) is None
    catalog = {"nested": [{"atomic_part_id": Q3}]}
    first = policy.answer_presentation_fingerprint(catalog)
    assert first == policy.answer_presentation_fingerprint(deepcopy(catalog))
    before = DesktopWorkbenchFacade._with_presentation_snapshot(catalog)[
        "data_snapshot_id"
    ]
    monkeypatch.setattr(policy, "ANSWER_IMAGE_REVISION_ID", "changed-test-policy")
    assert first != policy.answer_presentation_fingerprint(catalog)
    assert (
        before
        != DesktopWorkbenchFacade._with_presentation_snapshot(catalog)[
            "data_snapshot_id"
        ]
    )


@pytest.fixture(scope="module")
def reader():
    if os.environ.get("SHCHEM_RUN_ARCHIVED_WECHAT_TESTS") != "1":
        pytest.skip("set SHCHEM_RUN_ARCHIVED_WECHAT_TESTS=1 for pinned local sources")
    master = MasterWave1WorkbenchReader(DB)._snapshot()
    value = ShanghaiHighEast2025Theme45DirectVisualScanReader(
        DB, SimpleNamespace(_snapshot=lambda: master)
    )
    snapshot = value._snapshot()
    files = [DB / path for path in snapshot.output_bytes]
    before = {
        path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in files
    }
    yield value
    assert before == {
        path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in files
    }


def test_all_eleven_original_answer_images_are_exact_and_question_route_stays_closed(
    reader,
):
    inline = []
    snapshot = reader._snapshot()
    for node in EXPECTED_ATOMIC_IDS:
        detail = reader.detail(node)
        (answer,) = detail["reference_answer_images"]
        payload = reader.teacher_answer_crop(node, answer["crop_id"])
        assert (
            payload.sha256
            == answer["sha256"]
            == hashlib.sha256(payload.data).hexdigest()
        )
        crop = snapshot.crop_by_id[answer["crop_id"]]
        assert payload.data == (DB / crop["output_path"]).read_bytes()
        assert len(payload.data) == answer["bytes"]
        if answer["display_mode"] == "inline_required":
            inline.append(node)
        with pytest.raises(MasterDirectVisualScanError):
            reader.question_crop(node, answer["crop_id"])
        assert all(
            image.role != "answer"
            for image in image_descriptors("master", node, detail)
        )
        assert detail["reference_answer"]["source_authority"] == "nonofficial_reference"
        assert detail["authority"]["publication_allowed"] is False
    assert inline == [Q3, Q5]


def test_teacher_route_rejects_other_question_or_question_image(reader):
    first = reader.detail(Q3)
    other_answer = reader.detail(Q5)["reference_answer_images"][0]
    question = first["evidence_descriptors"][0]
    for crop_id in (other_answer["crop_id"], question["crop_id"], "UNKNOWN"):
        with pytest.raises(MasterDirectVisualScanError):
            reader.teacher_answer_crop(Q3, crop_id)


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("answer", "availability", "present_unaligned"),
        ("answer", "source_authority", "official"),
        ("image", "evidence_role", "question"),
        ("image", "access", "teacher_loopback_read_only"),
        ("image", "display_mode", "unknown"),
        ("image", "sha256", "broken"),
        ("image", "source_sha256", "broken"),
    ],
)
def test_native_projection_requires_answer_contract(target, field, value):
    scan = {
        "reference_answer": {
            "availability": "present_part_aligned",
            "source_authority": "nonofficial_reference",
        },
        "reference_answer_images": [
            policy.project_answer_image(Q3, _descriptor(), policy.ANSWER_PAGE_SHA256)
        ],
    }
    assert len(answer_image_descriptors("master", Q3, scan)) == 1
    item = (
        scan["reference_answer"]
        if target == "answer"
        else scan["reference_answer_images"][0]
    )
    item[field] = value
    assert answer_image_descriptors("master", Q3, scan) == ()


def test_facade_answer_image_session_binding_and_wrong_routes(reader):
    facade = object.__new__(DesktopWorkbenchFacade)
    facade._library_view_lock = threading.RLock()
    facade._library_views = {"test-view": (None, None, None, None, None, reader)}
    facade._library_scan = lambda scope, node, readers: (
        scope,
        node,
        reader.detail(node),
    )
    (image,) = answer_image_descriptors(
        "master", Q3, reader.detail(Q3), view_id="test-view"
    )
    assert (
        hashlib.sha256(facade.library_answer_image(image)).hexdigest() == image.sha256
    )
    for changed in (
        replace(image, role="question"),
        replace(image, view_id="expired"),
        replace(image, sha256="0" * 64),
        replace(image, node_id=Q5),
    ):
        with pytest.raises(DesktopFacadeError):
            facade.library_answer_image(changed)
    with pytest.raises(DesktopFacadeError, match="题面与共同材料"):
        facade.library_image(image)


@pytest.mark.parametrize("width", [420, 960])
def test_answer_preview_is_lazy_separate_and_can_collapse(width):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    from integrations.deeptutor_shchem_v1.desktop_workbench.library_detail import (
        FitWidthImage,
        LibraryDetailDialog,
    )

    app = QApplication.instance() or QApplication([])
    buffer = QByteArray()
    writer = QBuffer(buffer)
    writer.open(QIODevice.OpenModeFlag.WriteOnly)
    synthetic = QImage(400, 80, QImage.Format.Format_RGB32)
    synthetic.fill(0xFFFFFFFF)
    assert synthetic.save(writer, "PNG")
    raw = bytes(buffer)
    scan = {
        "reference_answer": {
            "availability": "present_part_aligned",
            "source_authority": "nonofficial_reference",
        },
        "reference_answer_images": [
            policy.project_answer_image(Q3, _descriptor(), policy.ANSWER_PAGE_SHA256)
        ],
    }
    images = answer_image_descriptors("master", Q3, scan)
    detail = LibraryThemeDetail(
        key="synthetic",
        scope="master",
        title_zh="演示主题",
        paper_title_zh="合成资料",
        source_zh="合成",
        page_zh="第1页",
        context_zh="演示",
        parts=(
            LibraryPartDetail(
                key=Q3,
                label_zh="原卷第3题",
                summary_zh="演示",
                requirement_zh="演示",
                dependency_zh="演示",
                answer_images=images,
            ),
        ),
    )
    pending, calls = [], []

    def submit(label, operation, *, on_success, on_failure):
        pending.append((operation, on_success))
        return "test-task"

    def answer_loader(image):
        calls.append(image)
        return raw

    dialog = LibraryDetailDialog(
        detail,
        SimpleNamespace(submit=submit, cancel=lambda key: None),
        lambda image: pytest.fail("question loader received answer"),
        answer_image_loader=answer_loader,
    )
    dialog.resize(width, 750)
    dialog.show()
    dialog.tabs.setCurrentIndex(1)
    app.processEvents()
    (button,) = dialog.findChildren(QPushButton, "LibraryShowAnswerImage")
    assert not calls and not pending
    button.click()
    operation, callback = pending.pop()
    callback(operation())
    app.processEvents()
    (widget,) = dialog.findChildren(FitWidthImage, "LibraryReferenceAnswerImage")
    assert widget.has_image and widget.loaded_bytes == raw and calls == list(images)
    assert all(
        "用于备课" not in item.text() for item in dialog.findChildren(QPushButton)
    )
    assert dialog.width() == width
    button.click()
    (container,) = dialog.findChildren(QWidget, "LibraryAnswerImages")
    assert container.isHidden()
    button.click()
    assert not pending and len(calls) == 1
    dialog.close()
    app.processEvents()
