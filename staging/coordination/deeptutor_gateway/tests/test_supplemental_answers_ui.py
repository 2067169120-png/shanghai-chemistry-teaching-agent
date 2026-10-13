from __future__ import annotations

import os
from copy import deepcopy
from hashlib import sha256
from io import BytesIO

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PIL import Image
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget
from test_desktop_library_ui import _ManualBridge

from integrations.deeptutor_shchem_v1.desktop_library import (
    LibraryPartDetail,
    LibraryThemeDetail,
    LibraryImage,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.library_detail import (
    LibraryDetailDialog,
)
from integrations.deeptutor_shchem_v1.supplemental_answers import (
    all_supplemental_answers,
)


@pytest.mark.parametrize("index", [0, 2, 3])
def test_answer_tab_shows_supplement_and_diagram_without_old_contradictory_analysis(
    index,
):
    app = QApplication.instance() or QApplication([])
    answer = all_supplemental_answers()[index]
    part = LibraryPartDetail(
        key=answer["node_id"],
        label_zh="补充解答测试",
        summary_zh="题面",
        requirement_zh="作答",
        dependency_zh="",
        analysis_zh=("旧候选错误解路不应继续显示",),
        supplemental_answer_zh=answer["text_zh"],
        supplemental_explanation_zh=answer["explanation_zh"],
        supplemental_diagram_key=answer["diagram_key"],
        question_text_zh="合成原题完整文字",
    )
    other = LibraryPartDetail(key="other-part", label_zh="另一作答单元",
        summary_zh="另一题面", requirement_zh="作答", dependency_zh="",
        reference_answer_zh="另一单元的来源参考答案",
        analysis_zh=("另一单元的AI候选分析",), question_text_zh="另一合成原题")
    detail = LibraryThemeDetail(
        key="theme",
        scope="wave1",
        title_zh="氯气",
        paper_title_zh="大同高一期中",
        source_zh="本地归档",
        page_zh="第一主题",
        context_zh="",
        parts=(part, other),
    )
    before = deepcopy(detail)
    tasks = _ManualBridge()
    def unexpected(_descriptor):
        pytest.fail("No original image descriptor is present in this fixture")
    dialog = LibraryDetailDialog(detail, tasks, unexpected,
        answer_image_loader=unexpected, embedded=True)
    widget = dialog.tabs.widget(1)
    try:
        dialog.resize(880, 740)
        dialog.tabs.setCurrentIndex(1)
        dialog.show()
        app.processEvents()
        labels = widget.findChildren(QLabel)
        text = "\n".join(label.text() for label in labels)
        assert "补充解答（AI，非官方）" in text
        assert answer["text_zh"] in text
        assert answer["explanation_zh"] in text
        assert "旧候选错误解路" not in text
        diagrams = widget.findChildren(QLabel, "LibrarySupplementalAnswerDiagram")
        assert len(diagrams) == int(answer["diagram_key"] is not None)
        assert all(not image.pixmap().isNull() for image in diagrams)
        cards = [card for card in widget.findChildren(QWidget)
                 if card.property("semanticRole") == "LibraryAnswerPartCard"]
        assert len(cards) == 2
        first_text = "\n".join(label.text() for label in cards[0].findChildren(QLabel))
        second_text = "\n".join(label.text() for label in cards[1].findChildren(QLabel))
        assert answer["text_zh"] in first_text and "另一单元的来源参考答案" not in first_text
        assert "另一单元的来源参考答案" in second_text and "另一单元的AI候选分析" in second_text
        assert not cards[1].findChildren(QLabel, "LibrarySupplementalAnswer")
        assert not cards[1].findChildren(QLabel, "LibrarySupplementalAnswerDiagram")
        assert detail == before and not tasks.pending
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_reference_answer_image_loads_only_in_its_nonofficial_answer_card():
    """Check actual in-memory widget rendering, not source parsing correctness."""
    app = QApplication.instance() or QApplication([])
    stream = BytesIO()
    Image.new("RGB", (72, 44), "#5a8a62").save(stream, "PNG")
    pixels = stream.getvalue()
    descriptor = LibraryImage(scope="synthetic", node_id="image-part", crop_id="local-answer",
        sha256=sha256(pixels).hexdigest(), role="answer", caption_zh="合成非官方答案原图",
        width=72, height=44)
    part = LibraryPartDetail(key="image-part", label_zh="图像答案单元", summary_zh="题面",
        requirement_zh="作答", dependency_zh="", question_text_zh="合成原题完整文字",
        reference_answer_zh="非官方来源答案文字", answer_images=(descriptor,))
    detail = LibraryThemeDetail(key="synthetic", scope="synthetic", title_zh="答案角色测试",
        paper_title_zh="内存合成资料", source_zh="测试合成，非官方", page_zh="1",
        context_zh="", parts=(part,))
    before = deepcopy(detail)
    tasks = _ManualBridge(); reads = []
    def question_loader(_descriptor):
        pytest.fail("Answer images must not be requested by the question loader")
    def answer_loader(image):
        reads.append(image)
        assert image == descriptor and image.role == "answer"
        return pixels
    dialog = LibraryDetailDialog(detail, tasks, question_loader,
        answer_image_loader=answer_loader, embedded=True)
    try:
        dialog.tabs.setCurrentIndex(1); dialog.show(); app.processEvents()
        answer_tab = dialog.tabs.widget(1)
        assert not tasks.pending and not reads
        button = answer_tab.findChild(QPushButton, "LibraryShowAnswerImage")
        assert button.text() == "查看非官方答案原图"
        button.click(); app.processEvents()
        assert len(tasks.pending) == 1 and not reads
        pending = tasks.pending.pop()
        tasks.succeed(pending, run=True); app.processEvents()
        image = answer_tab.findChild(QLabel, "LibraryReferenceAnswerImage")
        assert image.has_image and image.loaded_bytes == pixels
        assert image.descriptor == descriptor and image.descriptor.role == "answer"
        assert not image.pixmap().isNull()
        assert not dialog.tabs.widget(0).findChildren(QLabel, "LibraryReferenceAnswerImage")
        assert reads == [descriptor] and detail == before
        button.click(); assert button.text() == "查看非官方答案原图"
        button.click(); assert not tasks.pending and reads == [descriptor]
    finally:
        dialog.close(); dialog.deleteLater(); app.processEvents()
