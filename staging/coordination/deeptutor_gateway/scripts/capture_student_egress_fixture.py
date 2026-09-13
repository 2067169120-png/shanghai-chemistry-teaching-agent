"""Capture real student-egress UI with synthetic in-memory pages only.

No facade, provider, private submission, or real question source is read.  The
only file produced is the requested fresh PNG screenshot; overwrites fail.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def _page_png(index: int, font_family: str) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

    styles = (
        ("#edf5ff", "#205391", "合成题目页", "请观察记录，再回答下面的问题。"),
        (
            "#edf9f1",
            "#246440",
            "合成参考答案页",
            "本页只用于检查参考答案图片是否可见。",
        ),
        (
            "#fff4ec",
            "#94502b",
            "匿名学生作答页",
            "匿名学生甲 · 无姓名、学号等直接身份信息",
        ),
    )
    background, accent, title, subtitle = styles[index]
    image = QImage(760, 420, QImage.Format.Format_RGB32)
    image.fill(QColor(background))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(accent), 2))
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(QRectF(12, 12, 736, 396), 12, 12)

    def text(y: int, value: str, *, size=20, bold=False, color=accent, height=44):
        painter.setPen(QColor(color))
        painter.setFont(
            QFont(font_family, size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        )
        painter.drawText(
            QRectF(34, y, 690, height),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            value,
        )

    text(26, title + "｜石蕊试液观察", size=26, bold=True)
    text(76, subtitle, size=17, color="#4f5d69")
    if index == 0:
        text(132, "样品 A：红色    样品 B：紫色    样品 C：蓝色", size=21)
        text(194, "根据记录，呈酸性的样品是：________。", size=22, bold=True)
        text(252, "判断依据：________________________。", size=22)
        text(
            311,
            "请完整查看题面后，再核对参考答案和学生作答。",
            size=17,
            color="#4f5d69",
        )
    elif index == 1:
        text(132, "示例参考答案：样品 A。", size=25, bold=True)
        text(194, "判断依据：酸性溶液使石蕊试液变红。", size=22)
        text(252, "核对提示：答案页与题目页需要分别显示。", size=20)
        text(311, "此页为合成示例，不标注“官方答案”。", size=18, color="#4f5d69")
    else:
        text(132, "我的作答：样品 A。", size=25, bold=True)
        text(194, "我的依据：加入石蕊试液后变成红色。", size=22)
        text(252, "教师批注：尚未分析，尚未评分。", size=21)
        text(311, "请在发送前检查整页是否仍有身份信息。", size=18, color="#4f5d69")
    text(361, "完全合成的界面测试材料 · 非真实试卷或学生资料", size=16, color="#687780")
    painter.end()
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(
        buffer, "PNG"
    ):
        raise RuntimeError("Could not encode synthetic page")
    return bytes(buffer.data())


def _fixture(font_family: str):
    roles = (
        ("question_pages", "题目页面"),
        ("reference_answer_pages", "参考答案页面"),
        ("student_work_pages", "学生作答页面"),
    )
    pages, pixels = [], {}
    for index, (role, label) in enumerate(roles):
        raw = _page_png(index, font_family)
        digest = hashlib.sha256(raw).hexdigest()
        pixels[digest] = (raw, "image/png")
        pages.append(
            SimpleNamespace(
                file_id=f"synthetic-student-page-{index + 1}",
                sha256=digest,
                role=role,
                role_zh=label,
                ordinal=1,
                page_number=1,
                label_zh=label + " · 第 1 页",
                mime_type="image/png",
                width=760,
                height=420,
            )
        )
    confirmation = SimpleNamespace(
        student_id="synthetic-anonymous-student",
        submission_id="synthetic-only-submission",
        expected_revision="synthetic-revision",
        provider_profile_id="synthetic-profile",
        provider_revision="synthetic-profile-revision",
        pages=tuple(pages),
        page_sha256=tuple(page.sha256 for page in pages),
        provider_label_zh="合成视觉模型（界面演示，不连接 API）",
        total_page_count=3,
        page_counts_by_role={role: 1 for role, _label in roles},
        student_label_zh="匿名学生甲",
        retention_days=30,
        message_zh="全部图片由本脚本在内存生成，仅用于检查发送前核对界面。",
    )
    return confirmation, pixels


def capture(output: Path, *, width=980, height=760, page=1) -> None:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing screenshot: {output}")
    if output.suffix.casefold() != ".png":
        raise ValueError("Screenshot output must have a .png extension")
    if width < 320 or height < 420 or page not in (1, 2, 3):
        raise ValueError("Invalid capture size or page; --page must be 1, 2, or 3")

    from PySide6.QtCore import QBuffer, QIODevice

    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        install_font_fallbacks,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.student_egress_dialog import (
        AnalysisConfirmationDialog,
    )

    app = create_application([])
    font_family = install_font_fallbacks()
    confirmation, pixels = _fixture(font_family)
    loaded = []

    def load(digest):
        loaded.append(digest)
        return pixels[digest]

    dialog = AnalysisConfirmationDialog(confirmation, image_loader=load)
    try:
        dialog.resize(width, height)
        dialog.show()
        for _ in range(240):
            app.processEvents()
            if not dialog._timer.isActive():
                break
        if dialog._timer.isActive() or not dialog._ready:
            raise RuntimeError("Synthetic sending pages did not finish loading")
        if set(loaded) != set(confirmation.page_sha256):
            raise AssertionError("Not every frozen page was read")
        dialog.image_list.setCurrentRow(page - 1)
        app.processEvents()
        if not dialog.image_preview.has_image or dialog.image_list.count() != 3:
            raise AssertionError("Actual synthetic pixels are not visible")
        if dialog.identifiers_clear.isChecked() or dialog.egress_confirmed.isChecked():
            raise AssertionError("Consent must remain unchecked in the fixture")
        if dialog.start_button.isEnabled():
            raise AssertionError("Unchecked confirmation must not permit sending")
        for control in (
            dialog.identifiers_clear,
            dialog.egress_confirmed,
            dialog.start_button,
            dialog.cancel_button,
        ):
            if not control.isVisible() or not dialog.rect().contains(
                control.mapTo(dialog, control.rect().bottomRight())
            ):
                raise AssertionError("Bottom consent controls are clipped")
        screenshot = dialog.grab()
        if screenshot.isNull() or (screenshot.width(), screenshot.height()) != (
            width,
            height,
        ):
            raise AssertionError(f"Unexpected capture size: {screenshot.size()}")
        encoded = QBuffer()
        if not encoded.open(QIODevice.OpenModeFlag.WriteOnly) or not screenshot.save(
            encoded, "PNG"
        ):
            raise RuntimeError("Could not encode screenshot")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target:
            target.write(bytes(encoded.data()))
    finally:
        dialog.reject()
        app.processEvents()
    print(
        f"Synthetic student-egress capture: {width}x{height}, page={page}, font={font_family}"
    )
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Fresh PNG path; existing files are rejected",
    )
    parser.add_argument("--width", type=int, default=980)
    parser.add_argument("--height", type=int, default=760)
    parser.add_argument(
        "--page",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="1=question, 2=reference answer, 3=anonymous work",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    capture(output, width=args.width, height=args.height, page=args.page)


if __name__ == "__main__":
    main()
