"""Offline actual-handout integration and Qt screenshot checks."""

from __future__ import annotations

import hashlib
import json

from verify_preparation_sources_ui import (
    SOURCE,
    WORKBENCH_STYLE,
    WORKSPACE,
    PreparationSourcesDialog,
    QApplication,
    _ReadOnlySourceFacade,
    install_font_fallbacks,
)

from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt


def main():
    output = WORKSPACE / "runtime/deeptutor_shchem/qa/word-section-picker-20260909"
    output.mkdir(parents=True, exist_ok=True)
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    facade = _ReadOnlySourceFacade(WORKSPACE)
    dialog = PreparationSourcesDialog(facade)
    dialog._load_word(str(SOURCE))
    options = [
        dialog.section_picker.itemText(i)
        for i in range(1, dialog.section_picker.count())
    ]
    matches = [
        i
        for i in range(1, dialog.section_picker.count())
        if "考点一" in dialog.section_picker.itemText(i)
    ]
    assert len(matches) == 1, options
    dialog.section_picker.setCurrentIndex(matches[0])
    dialog.section_picker.activated.emit(matches[0])
    assert (dialog.block_start.value(), dialog.block_end.value()) == (39, 123)
    dialog._compile_preview()
    assert dialog.import_button.isEnabled(), dialog.status.text()
    materials = dialog.preview.toPlainText()
    for expected in (
        "[Word区块39]",
        "[Word区块123]",
        "得分速记",
        "例1",
        "例2",
        "【答案】",
        "【解析】",
        "待查看原文",
    ):
        assert expected in materials, expected
    assert "[Word区块124]" not in materials
    request_text = _prompt({"topic": "电解质的电离", "materials": materials})
    assert json.dumps(materials, ensure_ascii=False) in request_text
    dialog.block_list.setCurrentRow(78)
    assert (dialog.block_start.value(), dialog.block_end.value()) == (39, 123)
    captures = []
    for width, height in [(1000, 700), (420, 700)]:
        dialog.resize(width, height)
        dialog.show()
        for _ in range(5):
            app.processEvents()
        destination = output / f"section-picker-{width}.png"
        assert dialog.grab().save(str(destination))
        captures.append(
            {
                "requested": [width, height],
                "actual": [dialog.width(), dialog.height()],
                "path": str(destination),
            }
        )
        assert dialog.width() == width
    dialog._confirm()
    assert dialog.reference and dialog.reference["materials"] == materials
    assert before == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    report = {
        "source_sha256": before,
        "blocks": [39, 123],
        "material_characters": len(materials),
        "chapter_options": options,
        "source_unchanged": True,
        "provider_calls": 0,
        "full_materials_preserved_in_provider_prompt": True,
        "screenshots": captures,
    }
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    dialog.close()


if __name__ == "__main__":
    main()
