from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QLabel

from integrations.deeptutor_shchem_v1.desktop_library import (
    LibraryPartDetail,
    LibraryThemeDetail,
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
    )
    detail = LibraryThemeDetail(
        key="theme",
        scope="wave1",
        title_zh="氯气",
        paper_title_zh="大同高一期中",
        source_zh="本地归档",
        page_zh="第一主题",
        context_zh="",
        parts=(part,),
    )
    widget = LibraryDetailDialog._answer_tab(detail)
    try:
        widget.resize(880, 740)
        widget.show()
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
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()
