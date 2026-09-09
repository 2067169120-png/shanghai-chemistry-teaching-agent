"""Verify the live data correction, stale-review handling and real Qt page list."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from prepare_pkg043_boundary_patch import (
    BATCH,
    PACKET,
    Q2,
    read_json,
    read_rows,
    verify_after,
)
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    REVIEW_KIND,
    HandoutCandidateError,
    HandoutCandidateService,
)
from integrations.deeptutor_shchem_v1.desktop_paths import (
    DesktopPaths,
    default_state_root,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.handout_candidate_dialog import (
    HandoutCandidateDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)

QA = ROOT / "runtime/deeptutor_shchem/qa/pkg043-q2-boundary-integration-20260908"


def tree(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class LocalTasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        on_success(operation())
        return "local-boundary-qa"


def main():
    report_path = QA / "integration-verification.json"
    assert not report_path.exists(), "Do not overwrite previous QA"
    result = verify_after()
    before = {
        r["candidate_id"]: r
        for r in read_rows(QA / "batch-before/question_candidates.jsonl")
    }
    after = {
        r["candidate_id"]: r for r in read_rows(BATCH / "question_candidates.jsonl")
    }
    assert before.keys() == after.keys()
    assert [key for key in before if before[key] != after[key]] == [Q2]
    old_without_binding = dict(before[Q2])
    new_without_binding = dict(after[Q2])
    old_without_binding.pop("page_binding")
    new_without_binding.pop("page_binding")
    assert old_without_binding == new_without_binding
    old_files, new_files = tree(QA / "batch-before"), tree(BATCH)
    assert old_files.keys() == new_files.keys()
    changed_files = sorted(
        name for name in old_files if old_files[name] != new_files[name]
    )
    assert changed_files == [
        "hybrid_visual_queue.jsonl",
        "output_manifest.json",
        "question_candidates.jsonl",
        "validation_report.json",
    ]
    # Unchanged source documents, renders, retired/staged batches and reader.
    validation = read_json(PACKET / "validation_report.json")
    protected = {}
    for name, recorded in validation["protected_inputs_after"].items():
        if name in {"active_a02", "version_manifest", "product_manifest"}:
            continue
        path = Path(recorded["path"])
        if recorded["kind"] == "file":
            assert hashlib.sha256(path.read_bytes()).hexdigest() == recorded["sha256"]
        else:
            entries = [
                {
                    "path": p.relative_to(path).as_posix(),
                    "bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                }
                for p in sorted(p for p in path.rglob("*") if p.is_file())
            ]
            payload = json.dumps(
                entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            assert hashlib.sha256(payload).hexdigest() == recorded["tree_sha256"]
        protected[name] = True
    user_before = tree(default_state_root())
    paths = DesktopPaths.from_workspace(
        ROOT, state_root=tempfile.mkdtemp(prefix="shchem-boundary-qa-")
    )
    facade = DesktopWorkbenchFacade(paths)
    service = HandoutCandidateService(ROOT, facade._state)
    q2diff = next(
        r
        for r in read_json(PACKET / "field_differences.json")["records"]
        if r["candidate_id"] == Q2
    )
    key = q2diff["reader_key_before"]
    # A previous local review must not silently transfer to changed page evidence.
    facade._state.save_draft(
        "BOUNDARY-QA-OLD-REVIEW",
        {
            "kind": REVIEW_KIND,
            "event_id": "boundary-qa-old",
            "candidate_key": key,
            "revision": q2diff["reader_revision_before"],
            "decision": "needs_correction",
            "created_at": "2026-09-08T00:00:00Z",
            "candidate_only": True,
            "note": "自动化测试旧版本记录，不代表教师审核。",
            "bank_ingest_allowed": False,
            "answer_correctness_verified": False,
        },
    )
    assert service.detail(key)["review_state"] == "stale"
    try:
        service.record_review(
            key,
            q2diff["reader_revision_before"],
            decision="checked",
            question_checked=True,
            answer_checked=True,
        )
    except HandoutCandidateError as exc:
        assert exc.code == "handout_review_stale"
    else:
        raise AssertionError("Old revision accepted")
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    dialog = HandoutCandidateDialog(facade, LocalTasks())
    captures = []
    try:
        dialog.package.setCurrentIndex(dialog.package.findData("PKG-043"))
        dialog.state_filter.setCurrentIndex(dialog.state_filter.findData("incomplete"))
        dialog.results.setCurrentRow(
            next(i for i, r in enumerate(dialog._shown) if r["key"] == key)
        )
        assert dialog._detail["key"] == key
        assert dialog._detail["review_state"] == "stale"
        labels = [
            dialog.page_combo.itemText(i) for i in range(dialog.page_combo.count())
        ]
        assert labels == ["题目原页 · 第 1 页", "答案原页 · 第 2 页"]
        for role in ("question", "answer"):
            assert service.page_bytes(key, dialog._detail["revision"], role, 0)
        dialog.show()
        dialog.tabs.setCurrentIndex(2)
        for width in (1000, 420):
            dialog.resize(width, 900)
            for _ in range(5):
                app.processEvents()
            assert dialog.width() == width
            capture = QA / f"q2-page-selector-{width}.png"
            assert dialog.grab().save(str(capture))
            captures.append(str(capture))
        dialog.page_combo.setCurrentIndex(0)
        dialog.page_button.click()
        assert dialog._zoom is not None
        dialog._zoom.zoom.setValue(50)
        for _ in range(5):
            app.processEvents()
        capture = QA / "q2-actual-source-page.png"
        assert dialog._zoom.grab().save(str(capture))
        captures.append(str(capture))
    finally:
        dialog._dirty = False
        dialog.close()
        facade.shutdown()
    assert tree(default_state_root()) == user_before
    result.update(
        {
            "changed_batch_files": changed_files,
            "protected_inputs": protected,
            "other_67_candidates_unchanged": True,
            "user_state_unchanged": True,
            "old_review_marked_stale": True,
            "old_revision_save_rejected": True,
            "qt_page_labels": labels,
            "qt_source_page_opened": True,
            "captures": captures,
            "isolated_state": str(paths.state_root),
            "model_calls": 0,
            "formal_bank_writes": 0,
        }
    )
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
