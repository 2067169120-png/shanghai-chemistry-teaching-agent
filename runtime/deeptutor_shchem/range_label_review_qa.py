"""Capture explicit range review with synthetic labels and no personal store."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
        apply_teacher_edits, suggest_attributes,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_attributes_dialog import (
        WordQuestionAttributesDialog,
    )

    catalog = {"knowledge_points": [{"id": "K01", "name": "物质分类（合成目录）"}], "nodes": []}
    item = {
        "key": "synthetic-range-review", "revision": "synthetic-old-range",
        "source_sha256": "a" * 64, "source_name": "界面验收合成资料.docx",
        "title": "合成题 · 按当前题目范围核对教学标签",
        "question_blocks": [{"index": 2, "text": "合成材料：阅读条件，解释分类依据。", "assets": []}],
        "answer_blocks": [{"index": 3, "text": "合成答案，仅用于界面验收。", "assets": []}],
        "context_blocks": [], "warnings": [], "block_start": 2, "question_end": 2,
        "answer_start": 3, "block_end": 3, "context_start": None, "context_end": None,
    }
    old = apply_teacher_edits(suggest_attributes(item, {}, catalog),
                             {"teacher_note": "旧范围用于课堂讨论；新范围需重新核对，不能自动沿用。"})
    item["revision"] = "synthetic-current-range"
    current = suggest_attributes(item, {}, catalog)
    options = {"attributes": current, "catalog": catalog, "history": [old],
               "stored_revision": old["revision"], "previous_attributes": old,
               "range_review_required": True,
               "warning": "题目范围已变化。原标签仅供对照，核对后可保留现有值或修改。"}
    app = create_application(["range-review-qa"])
    captures = []
    for width, height in ((900, 850), (420, 700), (360, 600)):
        dialog = WordQuestionAttributesDialog(item, options)
        dialog.resize(width, height)
        dialog.show()
        for page in ("edit", "compare"):
            if page == "compare":
                dialog.range_review_check.setChecked(True)
                dialog._preview()
                assert dialog._proposal == {} and dialog.pages.currentIndex() == 1
            for _ in range(8):
                app.processEvents()
            assert dialog.width() == width and dialog.height() == height
            assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
            active = dialog.preview_button if page == "edit" else dialog.save_button
            assert active.isVisible() and dialog.rect().contains(active.geometry().center())
            path = output / f"range-review-{width}x{height}-{page}.png"
            assert dialog.grab().save(str(path))
            captures.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        dialog.reject()
        assert dialog.updates is None and not dialog.reconfirm_range
        dialog.deleteLater()
        app.processEvents()
    (output / "manifest.json").write_text(json.dumps({
        "data": "Synthetic only; no personal store or original source opened",
        "captures": captures, "widths": [900, 420, 360],
        "empty_proposal_requires_explicit_review": True, "cancel_writes": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "captures": len(captures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
