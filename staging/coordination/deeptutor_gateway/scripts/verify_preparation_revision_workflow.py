"""Exercise real Qt text edits and the local export facade on a copied lesson.

Only the previous preparation task subtree is copied, never model settings.
The changes are assistant-authored QA demonstration edits, not human approval.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from verify_real_source_preparation import (
    EXPECTED,
    ROOT,
    BundledArtifactRenderer,
    digest,
)

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_revision import (
    preparation_text_fields,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_revision_dialog import (
    PreparationRevisionDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
    DesktopTaskBridge,
)

SOURCE = ROOT / "runtime/deeptutor_shchem/qa/real-source-preparation-v14-20260909-r2"


class NoProviderAccess:
    def __getattr__(self, _name):
        raise AssertionError("Offline revision must not access provider settings")


def files(root):
    return {str(p.relative_to(root)): digest(p) for p in root.rglob("*") if p.is_file()}


def lesson_edits(candidate):
    """Explicit instructional corrections after reading textbook pp.56-58."""
    edits = {}
    for field in preparation_text_fields(candidate):
        old = field["text"]
        text = old.replace("一页笔记", "本课笔记").replace("补全本页", "补全学习单")
        text = text.replace("“典型物质”一列", "“典型物质”一行")
        if old != text:
            edits[tuple(field["path"])] = text

    def put(path, text):
        edits[tuple(path)] = text

    put(
        ["slides", 3, "content", 1],
        "本页讨论电解质的水溶液或熔融物：电离形成自由移动离子；外接电源形成闭合通路时，离子定向移动产生电流。",
    )
    put(
        ["slides", 3, "visual", "comparison", "rows", 2, "values", 0],
        "对本页讨论的体系，电离提供自由移动的离子",
    )
    put(
        ["slides", 3, "visual", "comparison", "rows", 2, "values", 1],
        "这些离子在外加电场下定向移动形成电流",
    )
    put(
        ["slides", 5, "content", 0],
        "依据教材第58页整理：按在水溶液中的电离程度，区分强电解质与弱电解质。",
    )
    put(["slides", 5, "visual", "comparison", "rows", 1, "label"], "电离方程式符号")
    put(
        ["slides", 5, "visual", "comparison", "rows", 1, "values", 0],
        "本课用“=”表示完全电离",
    )
    put(
        ["slides", 5, "visual", "comparison", "rows", 1, "values", 1],
        "用“⇌”表示可逆的部分电离",
    )
    put(["slides", 6, "visual", "steps", 1, "label"], "写出离子")
    put(
        ["slides", 6, "visual", "steps", 1, "detail"],
        "左侧写电解质的化学式，右侧写电离生成的阴、阳离子，并配平系数。",
    )
    put(
        ["slides", 6, "visual", "steps", 2, "detail"],
        "两侧各元素的原子数相等；两侧电荷代数和相等。",
    )
    put(
        ["slides", 6, "teacher_notes"],
        "依据教材第57—58页。先示范NaCl=Na⁺+Cl⁻和CH₃COOH⇌H⁺+CH₃COO⁻，再检查守恒。这里书写的是电离方程式，不能套用离子反应方程式中强电解质拆写、弱电解质不拆写的规则。板书保留左侧物质、右侧离子及系数。",
    )
    relationships = "电解质可发生电离；按水中电离程度分强、弱；电离过程用方程式表示；其溶液或熔融物中的离子定向移动形成电流。"
    put(["slides", 9, "content", 2], "笔记关系：" + relationships)
    put(
        ["slides", 9, "teacher_notes"],
        "先让学生独立解释固体NaCl不导电而溶液通电导电，再核对本课笔记。请分别用“发生过程”“分类依据”“符号表达”说明概念关系，不把所有概念串成同一种因果链。离堂辨析：固体NaCl不导电但它是电解质；CH₃COOH是弱电解质，其溶液仍能导电。",
    )
    put(
        ["lesson_stages", 2, "assessment"],
        "请学生独立说出电离形成自由移动离子，不以通电为条件；再说明在本课体系中，外接电源形成闭合通路后离子定向移动产生电流。两点均清楚才达成本环节目标。",
    )
    put(
        ["lesson_stages", 5, "student_action"],
        "补全学习单，按分类依据、发生过程、符号表达整理本课笔记；独立解释开场的NaCl导电现象。",
    )
    put(
        ["lesson_stages", 5, "assessment"],
        "检查学生能分别说清电解质与电离的关系、强弱分类依据、电离方程式的作用，以及电离与导电的区别。",
    )
    put(
        ["activities", 6, "teacher_action"],
        "指导学生补全学习单中的定义、比较表与电离方程式；用分类依据、发生过程和符号表达说明概念关系；组织离堂前口头辨析。",
    )
    put(
        ["activities", 6, "worksheet", "instructions", 2],
        "最后用短句区分：电解质的分类依据、电离过程、电离方程式的作用，以及电离与导电的关系。",
    )
    put(
        ["activities", 6, "worksheet", "sections", 2, "row_labels", 1], "电离方程式符号"
    )
    put(
        ["activities", 6, "worksheet", "sections", 4, "prompt"],
        "分别说明电解质如何分类、电离是怎样的过程、方程式表示什么，以及电离与导电有何区别；写下一个易错点。",
    )
    put(
        ["assessments", 2, "success_criteria", 3],
        "能分别说明电解质的分类依据、电离过程、方程式作用，以及电离与导电的区别。",
    )
    put(
        ["homework", "tasks", 0, "instruction"],
        "补全本课笔记：按分类依据、发生过程和符号表达整理概念，分别写出电解质与电离、强弱电解质与电离程度、电离与电离方程式的联系，再说明电离与导电的区别。",
    )
    return [{"path": list(path), "text": text} for path, text in edits.items()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-python", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT)
    if output.exists() or any(digest(p) != h for p, h in EXPECTED.items()):
        raise RuntimeError("Output exists or source changed")
    receipt = json.loads((SOURCE / "verification.json").read_text("utf-8"))
    source_root = Path(receipt["isolated_state"]) / "tasks/preparation-v1"
    before = files(source_root)
    state = Path(tempfile.mkdtemp(prefix="shchem-local-lesson-revision-"))
    shutil.copytree(source_root, state / "tasks/preparation-v1")
    output.mkdir(parents=True)
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=state),
        provider_store=NoProviderAccess(),
        preparation_renderer=BundledArtifactRenderer(args.artifact_python),
    )
    task_id = "PREP-7ed269131cc7c9de71b21ae82dcfe000"
    source = facade.preparation_revision_source(task_id)
    edits = lesson_edits(source["candidate"])
    app = QApplication.instance() or QApplication([])
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    if font_id < 0:
        raise RuntimeError("Chinese preview font unavailable")
    app.setStyleSheet(WORKBENCH_STYLE)
    family = QFontDatabase.applicationFontFamilies(font_id)[0]
    app.setFont(QFont(family, 10))
    bridge = DesktopTaskBridge()
    dialog = PreparationRevisionDialog(facade, bridge, source)
    dialog.show()
    for edit in edits:
        field = next(f for f in dialog.fields if f["path"] == edit["path"])
        dialog.group.setCurrentText(field["group"])
        dialog.editors[tuple(edit["path"])].setPlainText(edit["text"])
    dialog.note.setPlainText(
        "助手离线修订演示：据教材56—58页补足条件、区分概念关系，修正学习单措辞。未作真人教学审核。"
    )
    dialog.group.setCurrentIndex(3)
    app.processEvents()
    dialog.grab().save(str(output / "editor-920.png"))
    dialog.resize(460, 740)
    app.processEvents()
    dialog.grab().save(str(output / "editor-460.png"))
    dialog.tabs.setCurrentIndex(1)
    app.processEvents()
    dialog.grab().save(str(output / "changes-460.png"))
    results = []
    dialog.revision_saved.connect(results.append)
    dialog._save()
    deadline = time.monotonic() + 150
    while dialog.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    if not results or results[-1].status != "completed":
        raise RuntimeError("Local UI export did not complete: " + dialog.status.text())
    summary = results[-1]
    new_source = facade.preparation_revision_source(summary.task_id)
    for edit in edits:
        value = new_source["candidate"]
        for part in edit["path"]:
            value = value[part]
        assert value == edit["text"]
    for artifact in summary.artifact_ids:
        path = facade.preparation_artifact_path(summary.task_id, artifact)
        if path.suffix in {".docx", ".pptx", ".json"}:
            shutil.copy2(path, output / path.name)
    assert files(source_root) == before
    assert all(digest(p) == h for p, h in EXPECTED.items())
    result = {
        "method": "real_qt_form_and_local_facade_export",
        "author": "assistant_demonstration",
        "new_model_calls": 0,
        "provider_store_access_forbidden": True,
        "source_task": task_id,
        "source_revision": source["source_revision"],
        "isolated_state": str(state),
        "changed_fields": len(edits),
        "edits": edits,
        "original_task_tree_unchanged": True,
        "source_materials_unchanged": True,
        "summary": asdict(summary),
        "teacher_approval": False,
        "office_visual_review": "pending",
    }
    (output / "revision-workflow-verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
    )
    bridge.shutdown(1000)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"edits", "summary"}},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
