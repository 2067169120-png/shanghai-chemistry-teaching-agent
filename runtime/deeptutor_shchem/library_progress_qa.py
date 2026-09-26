"""Native C08 screenshots from in-memory synthetic data; no personal DB access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SyntheticFacade:
    def __init__(self):
        from integrations.deeptutor_shchem_v1.desktop_personal_visual_attributes import initial_attributes
        from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import _seal
        self.visual_catalog = {"knowledge_points": [{"id": "K01", "name": "合成知识主题"}],
            "nodes": [{"node_key": "synthetic-section", "volume_id": "synthetic-volume",
                       "chapter_id": "synthetic-chapter", "section_title": "合成教材节"}]}
        self.visual_rows = []
        titles = ["观察记录与信息提取", "实验材料与证据比较", "图表关系与条件分析"]
        for index in range(18):
            theme = index // 6 + 1
            number = index % 6 + 1
            row = {"batch_id": "DESKTOPBATCH-" + "a" * 32,
                "key": f"synthetic-visual-{index}", "revision": "synthetic-r1",
                "candidate_revision": "synthetic-cas-r1", "candidate_sha256": "c" * 64,
                "theme_key": f"synthetic-theme-{theme}", "theme_title": titles[theme - 1],
                "theme_sequence": theme, "printed_sequence": number, "sequence_source": "candidate_cas",
                "question_number": str(number), "title": f"{titles[theme - 1]} · 第 {number} 题",
                "source_name": "合成图片资料 · 课堂练习与材料核对",
                "question_text": "合成界面示例：比较材料中的记录，说明所依据的信息。",
                "shared_text": "主题共同材料：这些文字只用于界面验收，不来自试卷或学生资料。",
                "answer_text": "合成隐藏答案，不进入进度报告。",
                "selection_ready": index != 2, "images": [{"role": "question", "image_id": "synthetic-image"}],
                "curriculum_paths": [], "facets": {}, "warnings": []}
            pages = {"synthetic": {"source_file_id": "synthetic-page", "source_role": "question",
                "source_sha256": "a" * 64, "page_number": 1, "page_sha256": "b" * 64}}
            printed = {"atomic_parts": [{"atomic_part_id": "synthetic-atomic",
                "classification": {"primary_knowledge_K": ["K01"] if index % 3 else [],
                                   "supporting_knowledge_K": []}, "curriculum": {}}]}
            attrs = initial_attributes(row, {"candidate": {"paper": {}}}, pages, printed, self.visual_catalog)
            if index == 1:
                attrs["annotation_source"] = "teacher_modified"
                attrs["field_origins"]["teacher_note"] = "teacher"
                attrs["teacher_note"] = "合成教师标签，需人工核对。"
                attrs = _seal(attrs)
            row["attributes"] = attrs
            self.visual_rows.append(row)

    def word_question_catalog(self):
        names = ["合成示例 · 课堂观察与推理.docx", "合成示例 · 实验资料整理.docx"]
        titles = ["根据表格信息描述变化", "比较两组观察记录", "说明实验变量与结论", "整理材料中的关系"]
        items = []
        for index in range(18):
            key = f"synthetic-question-{index}"
            revision = "synthetic-r1"
            attributes = {
                "key": key, "source_sha256": "a" * 64, "question_revision": revision,
                "annotation_source": "teacher_modified" if index == 1 else "auto_suggested",
                "primary_knowledge": {"id": "K01" if index % 3 else "unknown", "status": "auto_suggested"},
                "curriculum_status": "unknown", "curriculum_candidates": [],
                "applicable_grades": {"values": ["grade_11"], "status": "usage_positioning"},
                "original_source": {"exam_type": {"value": "unknown", "status": "unknown"}},
                "material_status": {"missing_context": index == 4},
            }
            items.append({"key": key, "revision": revision, "source_sha256": "a" * 64,
                          "source_id": "source-" + str(index % 2), "source_name": names[index % 2],
                          "title": f"第 {index + 1} 题 · {titles[index % 4]}",
                          "chapter": "界面验收合成资料",
                          "question_blocks": [{"text": "用于检查来源、题目和待办的显示，不引用真实试卷或学生资料。", "assets": []}],
                          "attributes": attributes})
        return {"items": items, "sources": [{"source_id": "source-" + str(i), "source_name": name}
                                             for i, name in enumerate(names)],
                "attribute_catalog": {"nodes": [{"node_key": "synthetic-section", "volume_id": "synthetic-volume", "chapter_id": "synthetic-chapter"}]},
                "warnings": []}

    def personal_visual_questions(self, batch_id=None):
        return {"items": deepcopy([row for row in self.visual_rows if batch_id is None or row["batch_id"] == batch_id]),
                "warnings": [], "attribute_catalog": deepcopy(self.visual_catalog)}


class InlineTasks:
    def submit(self, label, operation, *, on_success, on_failure):
        on_success(operation())
        return "synthetic-task"

    def cancel(self, task_id):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_progress_dialog import LibraryProgressDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import typography_report
    app = create_application(["library-progress-qa"])
    dialog = LibraryProgressDialog(SyntheticFacade(), InlineTasks())
    dialog.bank_tabs.setCurrentIndex(1)
    dialog.setWindowTitle("本地题库进度 · 合成界面验收")
    dialog.show()
    captures = []

    def settle():
        for _ in range(8):
            app.processEvents()

    def capture(name, width, height, target=None):
        dialog.resize(width, height)
        settle()
        dialog.body_scroll.verticalScrollBar().setValue(0)
        if target is not None:
            dialog.body_scroll.ensureWidgetVisible(target, 0, 0)
        settle()
        path = output / (name + ".png")
        image = dialog.grab()
        assert image.save(str(path))
        assert dialog.width() == width and dialog.height() == height
        assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
        captures.append({"path": str(path), "width": image.width(), "height": image.height(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    for width, height in ((1080, 960), (420, 700), (360, 560)):
        capture(f"library-progress-{width}x{height}", width, height)
        if width < 500:
            capture(f"library-progress-{width}x{height}-table", width, height, dialog.table)
            capture(f"library-progress-{width}x{height}-actions", width, height, dialog.close_button)
    manifest = {"data": "in-memory synthetic only; no original questions, personal DB or provider requests",
                "typography": typography_report(), "captures": captures,
                "assertions": {"horizontal_overflow": False, "widths": [1080, 420, 360],
                               "bank": "personal_visual", "theme_order": "synthetic explicit CAS fields",
                               "no_real_personal_state": True}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    dialog.reject()
    print(json.dumps({"output": str(output), "captures": len(captures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
