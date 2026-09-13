"""Capture a pure synthetic picture-question label comparison, without a store."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def capture(output: Path, *, width=900, height=760):
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing screenshot: {output}")
    if output.suffix.casefold() != ".png" or width < 420 or height < 520:
        raise ValueError("Choose a fresh PNG path and at least 420 by 520 pixels")

    from PySide6.QtCore import QBuffer, QIODevice, Qt

    from integrations.deeptutor_shchem_v1.desktop_personal_visual_attributes import (
        attribute_catalog,
        initial_attributes,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        install_font_fallbacks,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_attributes_dialog import (
        PersonalVisualAttributesDialog,
    )

    app = create_application([])
    font = install_font_fallbacks()
    catalog = attribute_catalog(
        {
            "knowledge_points": [
                {"id": "K11", "name": "氧化还原反应"},
                {"id": "K10", "name": "电离与离子反应"},
            ],
            "nodes": [],
        }
    )
    row = {
        "batch_id": "DESKTOPBATCH-" + "a" * 32,
        "key": "synthetic-picture-question",
        "revision": "synthetic-content-revision",
        "title": "合成图片题 · 标签修改前后对照",
        "source_name": "纯合成界面示例（非真实题目）",
        "candidate_revision": "synthetic-candidate",
        "candidate_sha256": "b" * 64,
        "curriculum_paths": [],
        "images": [],
        "shared_text": "",
        "warnings": [],
    }
    # Only the pure initializer is used. These synthetic locators are never
    # presented as a real import, looked up on disk, or saved to any CAS/store.
    pages = {
        "synthetic": {
            "source_file_id": "synthetic-only-page",
            "source_role": "question",
            "source_sha256": "c" * 64,
            "page_number": 1,
            "page_sha256": "d" * 64,
        }
    }
    printed = {
        "atomic_parts": [
            {
                "atomic_part_id": "synthetic-only-atomic",
                "classification": {
                    "primary_knowledge_K": ["K11"],
                    "supporting_knowledge_K": [],
                },
                "curriculum": {},
            }
        ]
    }
    attributes = initial_attributes(
        row, {"candidate": {"paper": {}}}, pages, printed, catalog
    )
    dialog = PersonalVisualAttributesDialog(
        row,
        {
            "attributes": attributes,
            "catalog": catalog,
            "history": [],
            "warning": "本窗口仅演示个人标签编辑；全部内容为合成数据，不调用模型、不保存修改。",
        },
    )
    try:
        dialog.resize(width, height)
        dialog.primary_combo.setCurrentIndex(dialog.primary_combo.findData("K10"))
        dialog.grade_checks["grade_11"].setChecked(True)
        dialog.exam_combo.setCurrentIndex(dialog.exam_combo.findData("second_mock"))
        for index in range(dialog.use_list.count()):
            if dialog.use_list.item(index).data(Qt.ItemDataRole.UserRole) == "practice":
                dialog.use_list.item(index).setCheckState(Qt.CheckState.Checked)
        dialog._preview()
        dialog.show()
        app.processEvents()
        assert dialog.pages.currentIndex() == 1
        assert "修改前：" in dialog.comparison.toPlainText()
        assert "修改后：" in dialog.comparison.toPlainText()
        assert not dialog.confirm_tags.isChecked() and not dialog.teacher_confirmed
        assert dialog.updates is None
        for button in (dialog.cancel_button, dialog.back_button, dialog.save_button):
            assert button.isVisible()
            assert dialog.rect().contains(
                button.mapTo(dialog, button.rect().bottomRight())
            )
        pixmap = dialog.grab()
        assert not pixmap.isNull() and (pixmap.width(), pixmap.height()) == (
            width,
            height,
        )
        buffer = QBuffer()
        assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert pixmap.save(buffer, "PNG")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target:
            target.write(bytes(buffer.data()))
    finally:
        dialog.reject()
        app.processEvents()
    print(f"Synthetic personal visual label comparison: {width}x{height}; font={font}")
    print(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=760)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    capture(output, width=args.width, height=args.height)


if __name__ == "__main__":
    main()
