"""Replay teacher UI structure edits on the preserved live lesson, offline.

Copies only task state, never settings. These are assistant demonstration edits,
not teacher approval. Uses the shipped facade with provider access prohibited.
"""

import argparse
import json
import os
import shutil
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from verify_preparation_revision_workflow import NoProviderAccess, files
from verify_real_source_preparation import ROOT, BundledArtifactRenderer

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_structure import (
    classroom_timeline,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_revision_dialog import (
    PreparationRevisionDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Refresh only owned UI screenshots; never export or revise task state",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--sequence",
        action="store_true",
        help="Replay page ordering on the locally repaired live lesson",
    )
    modes.add_argument("--insert-knowledge", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    if output.exists() and not args.preview_only:
        raise RuntimeError("Use a new output directory")
    source_qa = ROOT / (
        "runtime/deeptutor_shchem/qa/classroom-sequence-20260909-r2"
        if args.insert_knowledge
        else "runtime/deeptutor_shchem/qa/classroom-structure-20260909-r2"
        if args.sequence
        else "runtime/deeptutor_shchem/qa/classroom-live-v21-20260909/live"
    )
    receipt = json.loads((source_qa / "verification.json").read_text("utf-8"))
    source_root = Path(receipt["isolated_state"]) / "tasks/preparation-v1"
    before = files(source_root)
    state = (
        Path(receipt["isolated_state"])
        if args.preview_only
        else Path(tempfile.mkdtemp(prefix="shchem-classroom-structure-"))
    )
    if not args.preview_only:
        shutil.copytree(source_root, state / "tasks/preparation-v1")
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=state),
        provider_store=NoProviderAccess(),
        preparation_renderer=BundledArtifactRenderer(args.artifact_python),
    )
    source_task = (
        receipt["summary"]["task_id"]
        if args.sequence or args.insert_knowledge
        else "PREP-1ba3f4b87bcf4f25c473a44803da70ca"
    )
    source = facade.preparation_revision_source(source_task)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    bridge = DesktopTaskBridge()
    dialog = PreparationRevisionDialog(facade, bridge, source)
    structure = dialog.structure
    dialog.tabs.setCurrentWidget(structure)
    dialog.show()
    output.mkdir(parents=True, exist_ok=args.preview_only)
    edits = []
    if args.insert_knowledge:
        edits = queue_knowledge_demo(dialog, source["candidate"], output, app)
    elif args.sequence:
        sequence = dialog.sequence
        dialog.tabs.setCurrentWidget(sequence)
        sequence.pages_list.clearSelection()
        for index in range(sequence.pages_list.count()):
            item = sequence.pages_list.item(index)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) == "S30")
        sequence.destination.setCurrentIndex(sequence.destination.findData("S27"))
        sequence.move_button.click()
        assert sequence.pages[27]["id"] == "S30"
        sequence.undo_button.click()
        assert sequence.pages[30]["id"] == "S30"
        sequence.destination.setCurrentIndex(sequence.destination.findData("S27"))
        sequence.move_button.click()
        assert len(structure.operations) == 1
        # Explicitly reorder existing teaching instructions too. Page movement
        # does not silently rewrite a lesson or guess semantic correspondence.
        for section, identity, key, order in (
            ("activities", "A08", "teacher_action", [0, 3, 1, 2]),
            ("lesson_stages", "L07", "teacher_action", [0, 3, 1, 2]),
            ("lesson_stages", "L07", "student_action", [0, 4, 1, 2, 3]),
        ):
            index = next(
                i
                for i, row in enumerate(source["candidate"][section])
                if row["id"] == identity
            )
            original_text = source["candidate"][section][index][key]
            clauses = original_text.rstrip("。").split("；")
            assert len(clauses) == len(order)
            edits.append(
                {
                    "path": [section, index, key],
                    "text": "；".join(clauses[i] for i in order) + "。",
                }
            )
        for edit in edits:
            field = next(f for f in dialog.fields if f["path"] == edit["path"])
            dialog.group.setCurrentText(field["group"])
            dialog.editors[tuple(edit["path"])].setPlainText(edit["text"])
        dialog.note.setPlainText(
            "助手本地页序修订演示：将水的电离说明移至Q8书写练习之前，仍在A08活动内。"
            "同时明确重排教案和活动中3处原有教学指令；不改课件化学正文、图片、学习单或分钟数，未作教师或课堂审核。"
        )
    else:
        edits = queue_structure_demo(dialog)
    mode = (
        "insert"
        if args.insert_knowledge
        else "sequence"
        if args.sequence
        else "structure"
    )
    for width in (920, 460):
        dialog.resize(width, 800)
        app.processEvents()
        dialog.grab().save(str(output / f"{mode}-ui-{width}.png"))
    if args.preview_only:
        assert files(source_root) == before
        dialog.pending.clear()
        dialog.structure.clear()
        dialog.close()
        bridge.shutdown(1000)
        print(
            "UI previews refreshed; original task state unchanged; no export or model call."
        )
        return
    operations = list(structure.operations)
    results = []
    dialog.revision_saved.connect(results.append)
    dialog._save()
    deadline = time.monotonic() + 150
    while dialog.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    if not results or results[-1].status != "completed":
        raise RuntimeError("Local export did not complete: " + dialog.status.text())
    summary = results[-1]
    for artifact in summary.artifact_ids:
        path = facade.preparation_artifact_path(summary.task_id, artifact)
        if path.suffix in {".docx", ".pptx", ".json"}:
            shutil.copy2(path, output / path.name)
    revised = facade.preparation_revision_source(summary.task_id)["candidate"]
    assert files(source_root) == before
    if args.insert_knowledge:
        expected = deepcopy(source["candidate"])
        for edit in edits:
            target = expected
            for key in edit["path"][:-1]:
                target = target[key]
            target[edit["path"][-1]] = edit["text"]
        for key in ("activities", "lesson_stages", "image_assets", "source_basis"):
            assert revised[key] == expected[key]
        old_slides = {row["id"]: row for row in expected["slides"]}
        for row in revised["slides"]:
            if row["id"] == "SLOCAL002":
                continue
            original = deepcopy(old_slides[row["id"]])
            if row["id"] == "S31":
                original["minutes"] -= 1
            assert {k: v for k, v in row.items() if k != "order"} == {
                k: v for k, v in original.items() if k != "order"
            }
        assert revised["slides"][31]["id"] == "SLOCAL002"
        assert revised["slides"][32]["id"] == "S31"
        assert revised["slides"][31]["minutes"] == 1
        assert len(revised["slides"]) == 36
    elif args.sequence:
        old_slides = {row["id"]: row for row in source["candidate"]["slides"]}
        for row in revised["slides"]:
            assert {k: v for k, v in row.items() if k != "order"} == {
                k: v for k, v in old_slides[row["id"]].items() if k != "order"
            }
        assert revised["slides"][27]["id"] == "S30"
        assert revised["slides"][28]["id"] == "S27"
        expected = deepcopy(source["candidate"])
        for edit in edits:
            expected[edit["path"][0]][edit["path"][1]][edit["path"][2]] = edit["text"]
        for key in ("activities", "lesson_stages", "image_assets", "source_basis"):
            assert revised[key] == expected[key]
    else:
        assert (
            revised["slides"][14]["content"]
            == source["candidate"]["slides"][13]["content"]
        )
        assert (
            revised["slides"][13]["image"] == source["candidate"]["slides"][13]["image"]
        )
    assert sum(row["minutes"] for row in revised["slides"][:17]) == 40
    assert sum(row["minutes"] for row in revised["slides"][17:]) == 40
    assert (
        revised["activities"][7]["worksheet"]
        == source["candidate"]["activities"][7]["worksheet"]
    )
    report = {
        "method": "real_qt_controls_and_local_facade_export",
        "mode": mode,
        "model_invoked": False,
        "provider_access_forbidden": True,
        "author": "assistant_demonstration",
        "original_task_tree_unchanged": True,
        "source_task": source["task_id"],
        "source_revision": source["source_revision"],
        "operations": operations,
        "text_edits": edits,
        "isolated_state": str(state),
        "summary": asdict(summary),
        "timeline": classroom_timeline(revised),
        "teacher_review_required": True,
        "visual_review": "pending",
    }
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        json.dumps(
            {
                "slides": len(revised["slides"]),
                "timeline": report["timeline"],
                "output": str(output),
                "model_invoked": False,
            },
            ensure_ascii=False,
        )
    )
    bridge.shutdown(1000)


def queue_knowledge_demo(dialog, candidate, output, app):
    """Explicit source-based demo; no chapter-specific rules in application code."""
    sequence = dialog.sequence
    dialog.tabs.setCurrentWidget(sequence)
    for index in range(sequence.pages_list.count()):
        item = sequence.pages_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == "S31":
            sequence.pages_list.setCurrentRow(index)
            break
    errors = []

    def fill_modal():
        modal = QApplication.activeModalWidget()
        try:
            modal.title.setText("多元弱酸分步电离")
            modal.purpose.setText(
                "在Q5纠错之前复习分步书写规则，提供学生可直接记录的完整例式。"
            )
            modal.content.setPlainText(
                "多元弱酸分步电离，且电离程度逐步减弱，以第一步电离为主。\n"
                "以硫化氢H₂S在水溶液中的电离为例："
            )
            modal.source_reference.setPlainText(
                "用户提供《第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）》"
                "阅读版第5页，知识点3第2条第(1)项；原Word SHA-256 "
                "d60317b8e533b957943e98b481305b85557d030d3056bf2eb0e9273f2811162d。"
                "本页按讲义转录并整理为可编辑表，换行与上下标已规范化，不冒称教材原句。"
            )
            modal.teacher_notes.setPlainText(
                "助手本地修订演示，尚未经教师确认。1分钟用于已学知识的复习提示和记录；"
                "Q5保留2分钟独立判断、3分钟讲评。若学生尚未学过分步电离，应另调活动预算。"
                "两步均用可逆符号；第二步从硫氢根离子出发，不能写成一次完全电离。"
                "不在本页提前展示Q5四项判断答案。"
            )
            modal.kind.setCurrentIndex(modal.kind.findData("comparison"))
            for row, values in enumerate(
                [
                    ["电离步骤", "电离方程式", "书写说明"],
                    ["第一步", "H₂S ⇌ H⁺＋HS⁻", "硫化氢电离；以第一步为主"],
                ["第二步", "HS⁻ ⇌ H⁺＋S²⁻", "硫氢根离子电离；程度更弱"],
                ]
            ):
                for column, value in enumerate(values):
                    modal.table.setItem(row, column, QTableWidgetItem(value))
            modal.tabs.setCurrentIndex(1)
            for width in (820, 460):
                modal.resize(width, 800)
                app.processEvents()
                modal.grab().save(str(output / f"insert-form-{width}.png"))
            modal.add_button.click()
            assert modal.operation is not None, modal.status.text()
        except (AssertionError, AttributeError, RuntimeError, TypeError, ValueError) as error:
            errors.append(str(error))
            if modal is not None:
                modal.reject()

    QTimer.singleShot(0, fill_modal)
    sequence.insert_button.click()
    if errors:
        raise RuntimeError("Insert dialog failed: " + "; ".join(errors))
    assert len(dialog.structure.operations) == 1
    edits = []
    for section, identity, suffix, text in (
        (
            "activities",
            "A09",
            ["teacher_action"],
            "先用1分钟复习H₂S分步电离规则和两步例式；出示Q5，留2分钟独立判断改正；用3分钟逐条讲评完整改正式。",
        ),
        (
            "activities",
            "A09",
            ["student_action"],
            "对照两步例式记录分步规则；独立判断Q5四条，写完整改正式；核对并记录错误原因。",
        ),
        (
            "activities",
            "A09",
            ["materials", 0],
            "W5知识点3第2条第(1)项；W7考向2例2，据讲义考查点整理",
        ),
        (
            "lesson_stages",
            "L08",
            ["teacher_action"],
            "先复习H₂S分步电离规则和两步例式；出示Q5纠错题，组织独立判断；逐条讲评完整改正式；最后组织独立复述四条主线，再展示完整汇总框架查漏。",
        ),
        (
            "lesson_stages",
            "L08",
            ["student_action"],
            "记录H₂S两步电离例式与分步规则；独立判断Q5四条，写完整改正式；核对并记录错误原因；不看笔记复述分类、电离、强弱、书写；对照汇总补缺。",
        ),
        (
            "lesson_stages",
            "L08",
            ["materials", 0],
            "W5知识点3第2条第(1)项；W7考向2例2（据讲义整理）；本课笔记框架",
        ),
        (
            "slides",
            "S29",
            ["teacher_notes"],
            "对比强电解质用＝，弱电解质用⇌；H₂O按教材简单离子符号书写，联系练习前已讲过的图2.15解释H₃O⁺。",
        ),
    ):
        index = next(
            i for i, row in enumerate(candidate[section]) if row["id"] == identity
        )
        edit = {"path": [section, index, *suffix], "text": text}
        edits.append(edit)
        field = next(f for f in dialog.fields if f["path"] == edit["path"])
        dialog.group.setCurrentText(field["group"])
        dialog.editors[tuple(edit["path"])].setPlainText(text)
    dialog.note.setPlainText(
        "助手依据讲义W5补入多元弱酸分步电离知识表，置于Q5之前；从Q5题面页划出1分钟，"
        "不改变两课时总时间。明确修订对应活动与教案指令；订正水电离说明的旧页序提示。"
        "原讲义、教材、题面、学习单和审核限制均保留。实际授课节奏待教师确认。"
    )
    sequence.pages_list.clearSelection()
    sequence.pages_list.setCurrentRow(31)
    return edits


def queue_structure_demo(dialog):
    structure = dialog.structure
    # A teacher must explicitly assign the originally unlinked cover.
    structure.activity.setCurrentIndex(structure.activity.findData("A01"))
    structure.link_button.click()
    structure.slide.setCurrentIndex(structure.slide.findData("S14"))
    structure.split_button.click()
    for slide_id, activity_id in (
        ("S06", "A05"),
        ("S09", "A05"),
        ("S19", "A10"),
        ("S20", "A10"),
    ):
        structure.slide.setCurrentIndex(structure.slide.findData(slide_id))
        structure.activity.setCurrentIndex(structure.activity.findData(activity_id))
        structure.table_notes.click()
    structure.heading.setText("电离方程式书写检查")
    structure.prompt.setPlainText(
        "结合本课例题，记录离子符号、原子守恒、电荷守恒和强弱电解质所用符号的检查方法，并写下自己的一处订正。"
    )
    structure.add_notes.click()
    structure.align_button.click()
    if len(structure.operations) != 8:
        raise RuntimeError("Unexpected operation count: " + structure.status.text())
    edits = [
        {"path": ["slides", 31, "title"], "text": "逐项判断并说明理由"},
        {"path": ["slides", 32, "title"], "text": "回看本课三个问题"},
    ]
    for edit in edits:
        field = next(f for f in dialog.fields if f["path"] == edit["path"])
        dialog.group.setCurrentText(field["group"])
        dialog.editors[tuple(edit["path"])].setPlainText(edit["text"])
    dialog.note.setPlainText(
        "助手本地修订演示：确认首页活动归属；Q7完整图文拆页；按活动预算校时；为两课时补笔记表和书写检查。未作教师或课堂审核。"
    )
    return edits


if __name__ == "__main__":
    main()
