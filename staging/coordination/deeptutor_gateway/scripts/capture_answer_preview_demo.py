"""Capture the native answer-image control using only synthetic local content."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main() -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QFont, QFontDatabase, QImage, QPainter
    from PySide6.QtWidgets import QPushButton

    from integrations.deeptutor_shchem_v1.desktop_library import (
        LibraryImage,
        LibraryPartDetail,
        LibraryThemeDetail,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_detail import (
        FitWidthImage,
        LibraryDetailDialog,
    )

    output = ROOT / "runtime/deeptutor_shchem/qa_0.1.57_answer_ui_20260910_r1"
    output.mkdir(exist_ok=True, parents=True)
    run = 1
    while (output / f"capture-{run:02d}").exists():
        run += 1
    output = output / f"capture-{run:02d}"
    output.mkdir()
    app = create_application([])
    for font_name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"):
        if QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{font_name}") < 0:
            raise RuntimeError(f"Required Chinese demo font unavailable: {font_name}")
    app.setFont(QFont("Microsoft YaHei", 10))
    picture = QImage(760, 150, QImage.Format.Format_RGB32)
    picture.fill(Qt.GlobalColor.white)
    painter = QPainter(picture)
    painter.setFont(QFont("Microsoft YaHei", 18))
    painter.drawText(
        picture.rect(),
        Qt.AlignmentFlag.AlignCenter,
        "甲  ──反应条件──→  乙\n界面演示图片，不是真实试题或答案",
    )
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not picture.save(buffer, "PNG"):
        raise RuntimeError("Synthetic image encoding failed")
    raw = bytes(data)
    descriptor = LibraryImage(
        scope="master",
        node_id="DEMO",
        crop_id="DEMO-ANSWER",
        sha256=hashlib.sha256(raw).hexdigest(),
        role="answer",
        caption_zh="非官方参考答案图 · 合成界面演示",
        width=760,
        height=150,
    )
    first = LibraryPartDetail(
        key="DEMO",
        label_zh="原卷第 3 题",
        summary_zh="合成界面演示",
        requirement_zh="说明图中甲到乙的过程",
        dependency_zh="使用主题共同材料",
        reference_answer_zh="答案文字与结构图对应保存。原图中的结构和条件可放大核对。",
        answer_boundary_zh="来源参考答案（非官方）；演示内容不代表真实题目。",
        answer_images=(descriptor,),
    )
    second = LibraryPartDetail(
        key="DEMO-2",
        label_zh="原卷第 4 题",
        summary_zh="合成界面演示",
        requirement_zh="说明理由",
        dependency_zh="使用主题共同材料",
        reference_answer_zh="纯文字答案直接展示，不额外重复贴图。",
        answer_boundary_zh="来源参考答案（非官方）；演示内容不代表真实题目。",
    )
    detail = LibraryThemeDetail(
        key="DEMO-THEME",
        scope="master",
        title_zh="答案图逐题预览",
        paper_title_zh="合成演示资料",
        source_zh="本地演示",
        page_zh="第 1 页",
        context_zh="界面演示",
        parts=(first, second),
    )
    pending = []
    loads = []

    def submit(label, operation, *, on_success, on_failure):
        pending.append((operation, on_success))
        return "synthetic-task"

    def load(image):
        if image != descriptor:
            raise RuntimeError("Unexpected image binding")
        loads.append(image.crop_id)
        return raw

    def reject_question(image):
        raise RuntimeError("No source question image is loaded by this demo")

    records = []
    for width, label in ((960, "main"), (420, "narrow")):
        dialog = LibraryDetailDialog(
            detail,
            SimpleNamespace(submit=submit, cancel=lambda key: None),
            reject_question,
            answer_image_loader=load,
        )
        dialog.resize(width, 820)
        dialog.show()
        dialog.tabs.setCurrentIndex(1)
        app.processEvents()
        if pending:
            raise RuntimeError("Answer image loaded before explicit click")
        (button,) = dialog.findChildren(QPushButton, "LibraryShowAnswerImage")
        button.click()
        operation, callback = pending.pop()
        callback(operation())
        for _ in range(5):
            app.processEvents()
            time.sleep(0.02)
        (widget,) = dialog.findChildren(FitWidthImage, "LibraryReferenceAnswerImage")
        if (
            not widget.has_image
            or widget.loaded_bytes != raw
            or dialog.width() != width
        ):
            raise RuntimeError("Answer demo UI failed")
        target = output / f"answer-preview-{label}.png"
        if not dialog.grab().save(str(target)):
            raise RuntimeError("Widget capture failed")
        records.append(
            {
                "path": str(target),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "width": width,
            }
        )
        dialog.close()
        app.processEvents()
    report = {
        "synthetic_only": True,
        "original_documents_loaded": 0,
        "model_calls": 0,
        "personal_configuration_accesses": 0,
        "image_loads": loads,
        "screenshots": records,
    }
    (output / "capture-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
