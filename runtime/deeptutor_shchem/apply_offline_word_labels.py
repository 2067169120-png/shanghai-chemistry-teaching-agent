"""Preview current-session Word label candidates; apply only an exact plan.

Candidates are supplied locally, and this command never calls a model. Both
the report and stdout contain metadata only. Apply requires the desktop app's
exclusive lock and a new report path; an existing lock is never removed.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.offline_word_label_review import (
    OfflineWordLabelReviewError,
    OfflineWordLabelReviewService,
)


def _require(condition, code, message):
    if not condition:
        raise OfflineWordLabelReviewError(code, message)


def _directory(value):
    raw = Path(value).expanduser()
    _require(
        raw.is_dir() and not raw.is_symlink() and not raw.is_junction(),
        "existing_directory_required", "工作区与个人状态目录必须是已有的真实目录。",
    )
    return raw.resolve(strict=True)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "duplicate_json_key", "候选JSON不能包含重复字段。")
        value[key] = item
    return value


def _report_path(value, *, workspace, state, candidate_path):
    """Reject source/state destinations before creating any directory or file."""
    raw = Path(value).expanduser()
    resolved = raw.resolve()
    reserved_roots = [
        state, workspace / ".git", workspace / "sh-chem-db", workspace / "knowledge",
        ROOT / ".git", ROOT / "sh-chem-db", ROOT / "knowledge",
    ]
    _require(
        resolved.suffix.casefold() == ".json"
        and resolved != candidate_path.resolve()
        and resolved.name.casefold() not in {
            "desktop-state.v1.json", ".desktop-workbench.lock",
            "word-question-attributes.sqlite3",
        }
        and not any(resolved.is_relative_to(root.resolve()) for root in reserved_roots),
        "reserved_report_path",
        "报告须使用独立的JSON路径，不能位于个人状态、资料库、知识资料或Git目录中。",
    )
    _require(
        not raw.exists() and not raw.is_symlink()
        and not resolved.exists() and not resolved.is_symlink(),
        "report_exists", "报告文件已存在，请使用新的路径。",
    )
    return resolved


def _operation_metadata(args):
    expected = args.expected_plan_sha256
    if not (
        isinstance(expected, str) and len(expected) == 64
        and all(char in "0123456789abcdef" for char in expected)
    ):
        expected = None
    return {
        "mode": "apply" if args.apply else "preview", "stage": "preflight",
        "attribute_write_attempted": False, "commit_status": "not_attempted",
        "readback_verified": False, "plan_sha256": expected,
        "expected_plan_sha256": expected,
        "provider_invoked": False, "teacher_confirmed": False,
    }


def _failure(exc, operation, *, code=None, stage=None):
    """Keep commit knowledge across service and report-persistence failures."""
    metadata = {**operation, **(getattr(exc, "operation", None) or {})}
    if stage is not None:
        metadata["operation_stage"] = metadata["stage"]
        metadata["stage"] = stage
    if metadata.get("error_code"):
        metadata.setdefault("operation_error_code", metadata["error_code"])
    code = code or getattr(exc, "code", "offline_label_operation_failed")
    attempted = metadata["attribute_write_attempted"]
    committed = metadata["commit_status"] == "committed"
    metadata.update(
        completed=False, error_code=code,
        do_not_retry_automatically=attempted,
        recovery_hint=(
            "属性已经提交；报告或回读未完成，请核对已保存标签和历史，不要自动重试。"
            if committed else
            "已尝试写入，提交状态未知；请先核对已保存标签和历史，不要自动重试。"
            if attempted else
            "未尝试写入属性；修复问题后重新预览。"
        ),
    )
    # Reports are metadata-only, but error output stays compact even after a
    # successful apply generated per-question revision receipts.
    metadata.pop("entries", None)
    metadata.pop("saved_attribute_revisions", None)
    return OfflineWordLabelReviewError(code, metadata["recovery_hint"], operation=metadata)


def _persist_report(stream, report):
    """Close exactly once, and preserve commit facts if writing/closing fails."""
    failure = None
    stage = "report_write"
    try:
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if stream.write(encoded) != len(encoded):
            raise OSError("incomplete report write")
        stage = "report_flush"
        stream.flush()
    except Exception as exc:
        failure = _failure(exc, report, code=stage + "_failed", stage=stage)
    try:
        stream.close()
    except Exception as exc:
        if failure is not None:
            failure.operation["report_close_failed"] = True
        else:
            failure = _failure(exc, report, code="report_close_failed", stage="report_close")
    if failure is not None:
        raise failure


@contextmanager
def desktop_lock(state_root, *, apply):
    if not apply:
        yield
        return
    from PySide6.QtCore import QLockFile

    path = state_root / ".desktop-workbench.lock"
    _require(
        not path.exists() and not path.is_symlink(),
        "desktop_lock_exists", "工作台锁已存在，请先关闭工作台；此命令不会移除锁。",
    )
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)
    _require(lock.tryLock(0), "desktop_lock_unavailable", "无法取得工作台独占锁，未应用候选。")
    try:
        yield
    finally:
        lock.unlock()


def read_side_service(workspace, state_root):
    """Construct only source reading and attributes; no facade initialisation."""
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
    from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import WordPreviewCache
    from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionService

    class ReadOnlyPreviewCache(WordPreviewCache):
        def save(self, source_bytes, source_name, preview):
            return False

    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths = DesktopPaths.from_workspace(workspace, state_root=state_root)
    facade._state = DesktopStateStore(state_root)
    words = WordQuestionService(facade)
    words.preview_cache = ReadOnlyPreviewCache(words.preview_cache.root)
    return words


def run(args, *, service_factory=None):
    operation = _operation_metadata(args)
    stream = None
    try:
        workspace, state = _directory(args.workspace), _directory(args.state)
        _require(
            not args.apply or bool(args.expected_plan_sha256),
            "expected_plan_required", "应用候选须提供 --expected-plan-sha256。",
        )
        _require(
            args.apply or not args.expected_plan_sha256,
            "apply_flag_required", "--expected-plan-sha256 仅与 --apply 同时使用。",
        )
        candidate_path = Path(args.candidates).expanduser()
        _require(
            candidate_path.is_file() and not candidate_path.is_symlink()
            and candidate_path.stat().st_size <= 2_000_000,
            "candidate_file_invalid", "候选须为不超过2MB的本地JSON文件。",
        )
        report_path = _report_path(
            args.report, workspace=workspace, state=state, candidate_path=candidate_path,
        )
        candidates = json.loads(
            candidate_path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object,
        )
        operation["stage"] = "report_reservation"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # Only a validated independent path is reserved before taking the app
        # lock. It can never create the app lock or another state artifact.
        stream = report_path.open("x", encoding="utf-8")
        operation["stage"] = "desktop_lock"
        with desktop_lock(state, apply=args.apply):
            operation["stage"] = "service_initialization"
            words = (service_factory or read_side_service)(workspace, state)
            service = OfflineWordLabelReviewService(words)
            operation["stage"] = "apply" if args.apply else "preview"
            outcome = (
                service.apply(candidates, expected_plan_sha256=args.expected_plan_sha256)
                if args.apply else service.preview(candidates)
            )
            operation = {**operation, **outcome}
        report = {
            **operation, "completed": True,
            "stage": operation["stage"] if args.apply else "preview_complete",
        }
    except Exception as exc:
        failure = _failure(exc, operation)
        if stream is not None:
            # If this also fails, _persist_report forwards the known commit
            # state and the original error code through the CLI's stdout.
            _persist_report(stream, failure.operation)
        raise failure from exc
    _persist_report(stream, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except Exception as exc:
        failure = _failure(exc, _operation_metadata(args))
        print(json.dumps(failure.operation, ensure_ascii=False))
        return 1
    print(json.dumps({
        key: value for key, value in report.items()
        if key not in {"entries", "saved_attribute_revisions"}
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
