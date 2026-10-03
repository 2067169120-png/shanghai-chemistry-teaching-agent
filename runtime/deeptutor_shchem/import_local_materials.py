"""Local bulk intake using the same services as the teacher workbench.

Defaults to preview. No OCR, model invocation, source mutation or promotion.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _files(folder, recursive, suffixes):
    from integrations.deeptutor_shchem_v1.model_provider_settings import (
        _assert_components_not_reparse,
    )
    folder = Path(folder).expanduser().absolute()
    _assert_components_not_reparse(folder)
    if not folder.is_dir():
        raise ValueError("资料目录不存在或不是文件夹。")
    paths, skipped = [], []
    # Do not follow linked subdirectories or infer the purpose of answer files.
    import os
    for current, directories, files in os.walk(folder, followlinks=False):
        directories[:] = sorted(name for name in directories if not (Path(current) / name).is_symlink())
        for name in sorted(files):
            path = Path(current) / name
            if path.is_symlink() or path.suffix.casefold() not in suffixes:
                skipped.append(name)
            else:
                _assert_components_not_reparse(path)
                paths.append(path)
        if not recursive:
            directories.clear()
    if not paths:
        raise ValueError("所选目录没有支持的资料文件。")
    return paths, skipped


def main(argv=None):
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
    from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import (
        TextbookWorkspaceService,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("question-bank", "textbooks", "knowledge-candidates"), required=True)
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--apply", action="store_true", help="Save the previewed local materials; default only previews.")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    paths = DesktopPaths.from_workspace(args.workspace, state_root=args.state_root)
    report = {"kind": args.kind, "applied": args.apply, "model_calls": 0,
        "original_files_modified": 0, "batches": [], "skipped_files": []}
    try:
        textbooks = TextbookWorkspaceService(paths, DesktopStateStore(paths.state_root))
        if args.kind == "knowledge-candidates":
            result = textbooks.import_candidates() if args.apply else textbooks.candidate_catalog()
            report["candidate_count"] = result["count"]
            report["source_sha256"] = result.get("sha256")
            report["review_status"] = "candidate-only"
        else:
            if args.folder is None:
                raise ValueError("请选择--folder资料目录。")
            suffixes = {".pdf"} if args.kind == "textbooks" else {".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
            files, report["skipped_files"] = _files(args.folder, args.recursive, suffixes)
            report["supported_files"] = len(files)
            facade = None
            if args.kind == "question-bank":
                from integrations.deeptutor_shchem_v1.desktop_facade import (
                    build_default_facade,
                )
                facade = build_default_facade(paths)
            # Bound batches by both file count and source bytes.
            batches, batch, size = [], [], 0
            for path in files:
                file_size = path.stat().st_size
                if batch and (len(batch) == 100 or size + file_size > 512 * 1024 * 1024):
                    batches.append(batch)
                    batch, size = [], 0
                batch.append(path)
                size += file_size
            if batch:
                batches.append(batch)
            try:
                for batch in batches:
                    if args.kind == "textbooks":
                        preview = textbooks.preview_books(batch)
                        summary = {"files": len(batch), "pages": sum(row["page_count"] for row in preview["sources"]),
                            "duplicates": sum(row["duplicate"] for row in preview["sources"])}
                        if args.apply:
                            summary.update(textbooks.commit_books(preview))
                    else:
                        preview = facade.preview_import_files(
                            handout_files=[path for path in batch if path.suffix.casefold() == ".docx"],
                            question_files=[path for path in batch if path.suffix.casefold() != ".docx"],
                            source_type="教师讲义")
                        summary = {"files": len(batch), "sources": preview["sources"],
                            "warnings": preview.get("warnings", [])}
                        try:
                            if args.apply:
                                receipt = facade.commit_import_preview(preview["preview_id"], preview["revision"],
                                    [row["source_id"] for row in preview["sources"]])
                                summary.update(batch_id=receipt.batch_id, state=receipt.status,
                                    native_quick_count=receipt.native_quick_count,
                                    visual_queue_count=receipt.visual_queue_count,
                                    candidate_only=receipt.candidate_only)
                        finally:
                            facade.discard_import_preview(preview["preview_id"])
                    report["batches"].append(summary)
            finally:
                if facade is not None:
                    facade.shutdown()
        report["ok"] = True
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        report["ok"] = False
        report["error"] = getattr(exc, "message_zh", None) or (str(exc) if isinstance(exc, ValueError) else "本地导入未完成，请核对资料目录与保存位置。")
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
