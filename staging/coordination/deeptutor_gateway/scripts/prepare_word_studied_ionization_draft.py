"""Build a source-studied lesson brief and optionally save one native draft.

No model/provider access. Existing state records and source files are retained.
Saving to the daily app uses its actual single-instance lock, never bypasses it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    PreparationImageStore,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
    _acquire_desktop_instance_lock,
    create_application,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog import (
    PreparationDraftDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)

OUTPUT = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义精读生成材料"
BASE = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义笔记完整版-r5"
QA = ROOT / "runtime/deeptutor_shchem/qa/word-studied-draft-20260909"
SOURCE_HASHES = {
    "sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-032/第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）.docx": "d60317b8e533b957943e98b481305b85557d030d3056bf2eb0e9273f2811162d",
    "课本/沪科技化学必修第一册【高清教材】.pdf": "a565f0a15ffd10c704f4be42bfe7200c125b68959d11ef582acdc45dde2ccf22",
}
IMAGE_PATHS = (
    "runtime/deeptutor_shchem/qa/word-led-source-assets-20260909-r2/book-figure-2-12.png",
    "runtime/deeptutor_shchem/qa/word-led-source-assets-20260909-r2/book-figure-2-13.png",
    "runtime/deeptutor_shchem/qa/word-led-source-assets-20260909-r2/book-definition.png",
    "runtime/deeptutor_shchem/qa/word-led-source-assets-20260909-r2/book-figure-2-15.png",
    "runtime/deeptutor_shchem/qa/word-led-source-assets-20260909-r2/word-three-states.png",
    "outputs/备课/2026-09-09-电解质与电离方程式-修订版/textbook-figure-2-14.png",
    "runtime/deeptutor_shchem/qa/notebook-definition-assets-20260909/ionization-definition.png",
    "runtime/deeptutor_shchem/qa/notebook-definition-assets-20260909/strong-weak-definition.png",
)


class NoProvider:
    def __getattr__(self, name):
        raise AssertionError("Preparing this draft must not access provider settings")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def build_brief(materials, assets):
    return {
        "output_kind": "joint",
        "topic": "电解质的电离｜讲义精读两课时",
        "audience": "高二化学复习；具体班级基础未知，需教师核对",
        "lesson_route": "复习",
        "lesson_timing": "2课时×40分钟",
        "objective": "按材料中的O1—O4完成分类、微观解释、强弱辨析和电离式书写；六块完整笔记、Q1—Q8课堂讲练与反馈、Q9课后选做。教材原句与讲义整理均须在学生可见正文落实。",
        "materials": materials.strip(),
        "image_assets": assets,
        "advanced": {
            "learning_and_experiment": "本稿为助手据用户原页整理的待核对输入，不代表教师确认。2×40分钟，只读图和纸笔；不开展熔盐实验，不编造实测数据或学情。",
            "template_and_delivery": "首页主标题用教材课题“电解质的电离”，另列第2章及2.2节名。按K1—K6知识、例题、练习、讲评组织，不固定页数。三个定义节点有原句局部图及可编辑文字；表格保留完整例式。题目和答案分开，PPT/教案/学习单对应同一编号。",
            "homework_and_strategy": "Q1—Q8错题订正并写依据；Q9仅课后选做。各课时40分钟已含作答和记笔记时间。原题、据讲义整理题、教材原练习与例式复写分别标注，不使用未经核验的官方题源或采分点声明。",
        },
    }


def validate_brief(brief):
    normalized = normalize_preparation_payload(brief)
    text = brief["materials"]
    assert len(text) <= 20_000
    assert re.findall(r"^## (K\d+) ", text, re.MULTILINE) == [
        f"K{n}" for n in range(1, 7)
    ]
    assert re.findall(r"^### (Q\d+) ", text, re.MULTILINE) == [
        f"Q{n}" for n in range(1, 10)
    ]
    assert normalized["timing"]["total_minutes"] == 80
    assert normalized["teacher_review_required"] is True
    assert normalized["publication_allowed"] is False
    assert len(brief["image_assets"]) == 8
    for expected in (
        "NaHSO₄＝Na⁺＋H⁺＋SO₄²⁻",
        "NaHSO₄＝Na⁺＋HSO₄⁻",
        "NaHCO₃＝Na⁺＋HCO₃⁻",
        "HCO₃⁻ ⇌ H⁺＋CO₃²⁻",
        "H₂S ⇌ H⁺＋HS⁻",
        "HS⁻ ⇌ H⁺＋S²⁻",
        "氯化铵",
        "冰醋酸",
        "全部是弱电解质",
        "6．H₂O ⇌ H⁺＋OH⁻。",
        "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。",
    ):
        assert expected in text
    prompt = _prompt(normalized)
    embedded, _ = json.JSONDecoder().raw_decode(
        prompt.split("教师备课简报 JSON：\n", 1)[1]
    )
    assert embedded["materials"] == text
    assert embedded["image_assets"] == brief["image_assets"]
    return normalized


def inspect_native_load(facade, draft_id, brief, app):
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()  # This check must not read model settings.
    dialogs, errors = [], []

    def choose_saved():
        try:
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, PreparationDraftDialog)
            selected = next(
                i
                for i in range(dialog.source.count())
                if dialog.source.itemData(i)["draft_id"] == draft_id
            )
            dialog.source.setCurrentIndex(selected)
            assert dialog.selected["payload"] == brief
            assert brief["materials"] in dialog.preview.toPlainText()
            for width in (900, 420):
                dialog.resize(width, 800)
                app.processEvents()
                target = QA / f"saved-draft-{width}.png"
                assert dialog.grab().save(str(target))
                dialogs.append(str(target))
            dialog._confirm()
        except Exception as exc:  # noqa: BLE001 - report callback errors and close modal QA
            errors.append(str(exc) or type(exc).__name__)
            current = QApplication.activeModalWidget()
            if current:
                current.reject()

    QTimer.singleShot(0, choose_saved)
    page._open_draft()
    page._availability_timer.stop()
    assert not errors, errors
    assert page._payload() == brief
    page.show()
    page.resize(1060, 850)
    app.processEvents()
    assert page.grab().save(str(QA / "loaded-form.png"))
    page.close()
    return dialogs + [str(QA / "loaded-form.png")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-default-draft", action="store_true")
    args = parser.parse_args()
    if (OUTPUT / "teacher-brief.json").exists() or QA.exists():
        raise RuntimeError(
            "Retain the existing bundle and receipt; use an explicitly revised workflow"
        )
    for name, expected in SOURCE_HASHES.items():
        assert digest(ROOT / name) == expected
    assets = json.loads((BASE / "teacher-brief.json").read_text("utf-8"))[
        "image_assets"
    ]
    local_store = PreparationImageStore(OUTPUT / "assets")
    mapping = []
    for asset, source in zip(assets, IMAGE_PATHS, strict=True):
        assert digest(ROOT / source) == asset["sha256"]
        imported = local_store.import_image(
            ROOT / source, asset["caption"], asset["source"], asset["purpose"]
        )
        assert imported == asset
        mapping.append({"asset_id": asset["asset_id"], "source_path": source})
    materials = (OUTPUT / "备课材料.md").read_text("utf-8")
    brief = build_brief(materials, assets)
    validate_brief(brief)
    write_json(OUTPUT / "teacher-brief.json", brief)
    write_json(
        OUTPUT / "source-bindings.json",
        {
            "source_hashes": SOURCE_HASHES,
            "images": mapping,
            "transcription_by": "assistant_from_user_provided_pages",
            "teacher_confirmed": False,
            "official_claim_allowed": False,
        },
    )
    QA.mkdir(parents=True, exist_ok=False)
    report = {
        "materials_characters": len(brief["materials"]),
        "knowledge_units": 6,
        "classroom_question_groups": 8,
        "optional_question_groups": 1,
        "image_assets": 8,
        "timing_minutes": [40, 40],
        "prompt_materials_exact": True,
        "network_calls": 0,
        "teacher_review_required": True,
        "new_teaching_ppt_exported": False,
    }
    if args.save_default_draft:
        app = create_application([])
        install_font_fallbacks()
        paths = DesktopPaths.from_workspace(ROOT)
        lock = _acquire_desktop_instance_lock(paths.state_root)
        try:
            state = DesktopStateStore(paths.state_root)
            before = state.snapshot()
            facade = DesktopWorkbenchFacade(
                paths, state_store=state, provider_store=NoProvider()
            )
            for asset, source in zip(assets, IMAGE_PATHS, strict=True):
                assert (
                    facade.import_preparation_image(
                        str(ROOT / source),
                        asset["caption"],
                        asset["source"],
                        asset["purpose"],
                    )
                    == asset
                )
            receipt = facade.create_preparation_draft(brief)
            after = state.snapshot()
            assert set(after["drafts"]) - set(before["drafts"]) == {receipt.draft_id}
            assert all(
                after["drafts"][key] == value for key, value in before["drafts"].items()
            )
            assert all(
                after[key] == value
                for key, value in before.items()
                if key not in {"drafts", "updated_at"}
            )
            option = next(
                row
                for row in facade.preparation_draft_options()
                if row["draft_id"] == receipt.draft_id
            )
            assert (
                facade.load_preparation_draft(receipt.draft_id, option["revision"])[
                    "payload"
                ]
                == brief
            )
            report.update(
                {
                    "draft_id": receipt.draft_id,
                    "title": brief["topic"],
                    "saved_at": receipt.saved_at,
                    "revision": option["revision"],
                    "existing_drafts_unchanged": len(before["drafts"]),
                    "other_state_fields_unchanged": True,
                }
            )
            # Save the successful mutation receipt before optional UI QA.
            write_json(OUTPUT / "saved-draft-receipt.json", report)
            report["screenshots"] = inspect_native_load(
                facade, receipt.draft_id, brief, app
            )
            assert state.snapshot() == after
            report["native_form_payload_exact"] = True
            report["visual_review"] = "pending"
        finally:
            lock.unlock()
    for name, expected in SOURCE_HASHES.items():
        assert digest(ROOT / name) == expected
    report["source_files_unchanged"] = True
    write_json(QA / "verification.json", report)
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
