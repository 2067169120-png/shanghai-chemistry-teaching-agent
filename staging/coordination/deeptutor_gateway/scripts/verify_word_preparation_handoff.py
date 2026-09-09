"""Exercise native Word -> local preparation with isolated state and no provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Use a fresh isolated QA folder")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
        PreparationImageStore,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    class NoProviders:
        calls = 0

        def list_metadata(self):
            return ()

        def borrow_invocation_context(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("QA must never borrow a model credential")

        def __getattr__(self, name):
            raise RuntimeError("QA must not read or call a provider: " + name)

    source_digest = hashlib.sha256(args.source.read_bytes()).hexdigest()
    started = time.monotonic()
    args.output.mkdir(parents=True)
    providers = NoProviders()
    paths = DesktopPaths.from_workspace(ROOT, state_root=args.output / "isolated-state")
    facade = DesktopWorkbenchFacade(paths, provider_store=providers)
    facade.save_visual_import_batch(handout_files=(args.source,), source_type="教师讲义")
    print(json.dumps({"phase": "source_saved", "seconds": round(time.monotonic() - started, 2)}), flush=True)
    catalog = facade.word_question_catalog()
    print(json.dumps({"phase": "catalog", "questions": len(catalog["items"]), "seconds": round(time.monotonic() - started, 2)}), flush=True)
    selections, sampled_issues = [], []
    for item in catalog["items"]:
        if not any(block.get("assets") for group in ("question_blocks", "answer_blocks") for block in item[group]):
            continue
        choice = {"key": item["key"], "revision": item["revision"], "points": 3}
        reference = facade.word_question_reference(selections + [choice])
        if reference["image_issues"] or len(reference["image_assets"]) > 12:
            sampled_issues.append({"key": item["key"], "issues": reference["image_issues"], "images": len(reference["image_assets"])})
            continue
        selections.append(choice)
        if len(selections) == 2:
            break
    if len(selections) != 2:
        raise RuntimeError("Need two supported actual-source questions for this QA")
    reference = facade.word_question_reference(selections)
    print(json.dumps({"phase": "reference", "images": len(reference["image_assets"]), "seconds": round(time.monotonic() - started, 2)}), flush=True)
    assert reference["image_assets"] and not reference["image_issues"]
    app = create_application([])
    for font in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc", "arial.ttf", "segoeui.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    app.setFont(QFont("Microsoft YaHei", 10))
    tasks = DesktopTaskBridge()
    page = PreparationPage(facade, tasks)
    page.topic.setText("原有课题保留")
    page.audience.setText("高三复习班")
    page.objective.setPlainText("保留原有备课要求并核对所选题目的图文关系。")
    page.materials.setPlainText("教师已填写的原有资料。")
    before = page._payload()
    page.resize(1100, 1000)
    page.show()
    assert page.import_word_reference(reference)
    assert page._payload() == before and not page.isEnabled()
    deadline = time.monotonic() + 60
    while page._library_image_task_id and time.monotonic() < deadline:
        app.processEvents()
        # Release Python's GIL so real background readers can run. A loop made
        # entirely of Qt processEvents/qWait can starve Python QRunnables.
        time.sleep(0.015)
    assert not page._library_image_task_id and page.isEnabled(), page.status.text()
    after = page._payload()
    assert after["topic"] == before["topic"]
    assert after["materials"].startswith(before["materials"])
    assert reference["materials"] in after["materials"]
    assert after["image_assets"] == reference["image_assets"]
    assert len({asset["caption"] for asset in after["image_assets"]}) == len(after["image_assets"])
    assert all(args.source.name in asset["source"] for asset in after["image_assets"])
    store = PreparationImageStore(paths.task_root / "preparation-v1/images")
    for asset in after["image_assets"]:
        assert hashlib.sha256(store.load(asset)).hexdigest() == asset["sha256"]
    saved = facade.create_preparation_draft(after)
    restored = DesktopWorkbenchFacade(paths, provider_store=providers)
    option = next(row for row in restored.preparation_draft_options() if row["draft_id"] == saved.draft_id)
    assert restored.load_preparation_draft(saved.draft_id, option["revision"])["payload"] == after
    # Own-widget captures only, no desktop/screen capture and no public artifacts.
    QTest.qWait(100)
    page.image_assets_widget.grab().save(str(args.output / "local-image-list.png"))
    assert hashlib.sha256(args.source.read_bytes()).hexdigest() == source_digest
    assert providers.calls == 0
    report = {
        "source_sha256": source_digest,
        "candidate_questions": len(catalog["items"]),
        "selected": selections,
        "image_count": len(after["image_assets"]),
        "image_sha256": [a["sha256"] for a in after["image_assets"]],
        "sampled_image_issues": sampled_issues,
        "old_form_preserved": True,
        "source_unchanged": True,
        "draft_reopened": True,
        "provider_calls": providers.calls,
        "isolated_qa_not_user_import": True,
    }
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    page.close()
    tasks.shutdown()
    print(json.dumps({k: v for k, v in report.items() if k not in ("selected", "image_sha256", "sampled_image_issues")}))


if __name__ == "__main__":
    main()
