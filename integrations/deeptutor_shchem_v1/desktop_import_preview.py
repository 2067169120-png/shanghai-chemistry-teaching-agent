"""Read-only, in-memory file previews before a teacher commits a whole-file import."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
import zipfile
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .desktop_preparation_sources import PreparationSourcesService
from .desktop_word_question_index import index_word_questions

MAX_PREVIEW_SESSIONS = 3
MAX_CACHED_WORD_SOURCES = 3
MAX_FILES = 100
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_WORD_BYTES = 40 * 1024 * 1024
MAX_BATCH_BYTES = 512 * 1024 * 1024
ROLES = ("question", "answer", "handout")
MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
TEACHING_PACK = Path(".intake/2026-07-30-user-teaching-pack")


class ImportPreviewError(ValueError):
    def __init__(self, code, message):
        self.code = code
        self.message_zh = message
        super().__init__(message)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _fingerprint(path):
    """Stream the initial inventory, without retaining or parsing every document."""
    suffix = path.suffix.casefold()
    if suffix not in MIME_TYPES or not path.is_file():
        raise ImportPreviewError(
            "import_preview_file_invalid",
            "请选择可读取的Word、PDF、PNG、JPEG或WebP文件。",
        )
    limit = MAX_WORD_BYTES if suffix == ".docx" else MAX_FILE_BYTES
    if not 0 < path.stat().st_size <= limit:
        raise ImportPreviewError(
            "import_preview_file_size",
            "所选文件为空或过大；Word不超过40MB，其他文件不超过256MB。",
        )
    digest, size, header = hashlib.sha256(), 0, b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if not header:
                header = chunk[:1024]
            size += len(chunk)
            if size > limit:
                raise ImportPreviewError(
                    "import_preview_file_size",
                    "文件读取时大小发生变化或超过上限，请重新选择。",
                )
            digest.update(chunk)
    if not size:
        raise ImportPreviewError("import_preview_file_size", "所选文件为空。")
    valid_header = {
        ".docx": header.startswith(b"PK"),
        ".pdf": b"%PDF-" in header,
        ".png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": header.startswith(b"\xff\xd8\xff"),
        ".jpeg": header.startswith(b"\xff\xd8\xff"),
        ".webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
    }[suffix]
    if not valid_header:
        raise ImportPreviewError(
            "import_preview_file_format", "文件内容与扩展名不一致，请重新选择有效文件。"
        )
    if suffix == ".docx":
        # Read the ZIP directory only; paragraph/formula extraction is lazy.
        try:
            with zipfile.ZipFile(path) as package:
                package.getinfo("word/document.xml")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ImportPreviewError(
                "import_preview_file_format", "Word文件结构不完整，请重新选择有效DOCX。"
            ) from exc
    return digest.hexdigest(), size


def validate_loaded_inputs(sources, expected_inputs):
    """Check the actual bytes the saver loaded, not a prior unbound path check."""
    actual = [
        {
            "role": source.role,
            "order_index": source.order_index,
            "filename": source.filename,
            "mime_type": source.mime_type,
            "source_sha256": source.source_sha256,
            "size_bytes": len(source.content),
            "group_id": source.group_id,
        }
        for source in sources
    ]
    if not isinstance(expected_inputs, list) or actual != expected_inputs:
        raise ImportPreviewError(
            "import_preview_changed",
            "确认前文件内容、角色或顺序发生变化，尚未导入，请重新预览。",
        )


class ImportPreviewService:
    def __init__(self, facade):
        self.facade = facade
        self.reader = PreparationSourcesService(facade.paths.workspace_root)
        self._sessions = OrderedDict()
        self._lock = threading.RLock()

    def preview(
        self, *, question_files=(), answer_files=(), handout_files=(), source_type
    ):
        if (
            not isinstance(source_type, str)
            or len(source_type) > 1000
            or any(ord(c) < 32 for c in source_type)
        ):
            raise ImportPreviewError("import_preview_source_type", "资料类型不正确。")
        files = {
            "question": question_files,
            "answer": answer_files,
            "handout": handout_files,
        }
        if any(not isinstance(paths, (list, tuple)) for paths in files.values()):
            raise ImportPreviewError(
                "import_preview_file_list", "请选择需要预览的文件。"
            )
        if not 1 <= sum(len(paths) for paths in files.values()) <= MAX_FILES:
            raise ImportPreviewError(
                "import_preview_file_count", "每批请选择1至100份文件。"
            )
        sources, private, total = [], {}, 0
        for role in ROLES:
            for order_index, raw_path in enumerate(files[role], 1):
                try:
                    path = Path(raw_path).resolve()
                    digest, size = _fingerprint(path)
                except (OSError, TypeError, ValueError) as exc:
                    if isinstance(exc, ImportPreviewError):
                        raise
                    raise ImportPreviewError(
                        "import_preview_file_unreadable",
                        "所选文件无法读取，请检查文件是否仍然可用。",
                    ) from exc
                total += size
                if total > MAX_BATCH_BYTES:
                    raise ImportPreviewError(
                        "import_preview_batch_size",
                        "本批文件超过512MB，请分批预览导入。",
                    )
                source_id = f"PREVIEW-{role.upper()}-{order_index:03d}-{digest[:16]}"
                kind = (
                    "docx"
                    if path.suffix.casefold() == ".docx"
                    else "pdf"
                    if path.suffix.casefold() == ".pdf"
                    else "image"
                )
                row = {
                    "source_id": source_id,
                    "role": role,
                    "kind": kind,
                    "source_name": path.name,
                    "source_sha256": digest,
                    "order_index": order_index,
                }
                sources.append(row)
                private[source_id] = {
                    "path": path,
                    "size_bytes": size,
                    "mime_type": MIME_TYPES[path.suffix.casefold()],
                }
        revision = _digest(
            {
                "sources": sources,
                "sizes": [private[row["source_id"]]["size_bytes"] for row in sources],
                "source_type": source_type,
            }
        )
        result = {
            "preview_id": "IMPORT-PREVIEW-" + uuid4().hex,
            "revision": revision,
            "sources": sources,
            "warnings": [
                "此处按文件选择；确认后会导入勾选文件的完整内容及其全部候选题，文件内逐题选择用于后续备课或练习。"
            ],
        }
        with self._lock:
            while len(self._sessions) >= MAX_PREVIEW_SESSIONS:
                expired = next(
                    (key for key, value in self._sessions.items() if not value["busy"]),
                    None,
                )
                if expired is None:
                    raise ImportPreviewError(
                        "import_preview_busy",
                        "已有文件正在确认导入，请完成后再打开新的预览。",
                    )
                self._sessions.pop(expired)
            self._sessions[result["preview_id"]] = {
                "result": deepcopy(result),
                "private": private,
                "source_type": source_type,
                "cache": OrderedDict(),
                "busy": False,
            }
        return result

    def _session(self, preview_id, expected_revision):
        if not isinstance(preview_id, str) or not isinstance(expected_revision, str):
            raise ImportPreviewError(
                "import_preview_expired", "预览已失效，请重新选择文件。"
            )
        with self._lock:
            session = self._sessions.get(preview_id)
            if session is None or session["result"]["revision"] != expected_revision:
                raise ImportPreviewError(
                    "import_preview_expired", "预览已失效或已更新，请重新选择文件。"
                )
            self._sessions.move_to_end(preview_id)
            return session

    @staticmethod
    def _source(session, source_id):
        source = next(
            (
                row
                for row in session["result"]["sources"]
                if row["source_id"] == source_id
            ),
            None,
        )
        if source is None:
            raise ImportPreviewError(
                "import_preview_source_missing", "这份资料不属于当前预览，请重新选择。"
            )
        return source, session["private"][source_id]

    @staticmethod
    def _bytes(source, private):
        try:
            with private["path"].open("rb") as stream:
                raw = stream.read(private["size_bytes"] + 1)
        except OSError as exc:
            raise ImportPreviewError(
                "import_preview_file_unreadable", "预览原文件无法读取，请重新选择。"
            ) from exc
        if (
            len(raw) != private["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != source["source_sha256"]
        ):
            raise ImportPreviewError(
                "import_preview_changed", "文件内容已变化，请重新选择并预览。"
            )
        return raw

    def source(self, preview_id, expected_revision, source_id):
        session = self._session(preview_id, expected_revision)
        source, private = self._source(session, source_id)
        raw = self._bytes(source, private)
        common = {key: source[key] for key in ("kind", "source_name", "source_sha256")}
        if source["kind"] != "docx":
            return {
                **common,
                "bytes": raw,
                "mime_type": private["mime_type"],
                "warnings": [],
            }
        with self._lock:
            cached = session["cache"].get(source_id)
        if cached is None:
            preview = self.reader.word_preview_bytes(raw, source["source_name"])
            cached = {
                **common,
                "preview": preview,
                "questions": index_word_questions(preview),
                "warnings": list(preview.get("warnings", [])),
            }
            with self._lock:
                session["cache"][source_id] = cached
                while len(session["cache"]) > MAX_CACHED_WORD_SOURCES:
                    session["cache"].popitem(last=False)
        else:
            with self._lock:
                session["cache"].move_to_end(source_id)
        return deepcopy(cached)

    def asset(self, preview_id, expected_revision, source_id, asset_id):
        session = self._session(preview_id, expected_revision)
        source, private = self._source(session, source_id)
        if source["kind"] != "docx":
            raise ImportPreviewError(
                "import_preview_asset_invalid", "请在当前Word预览中选择原图。"
            )
        preview = self.source(preview_id, expected_revision, source_id)["preview"]
        asset = next(
            (row for row in preview["assets"] if row["asset_id"] == asset_id), None
        )
        if asset is None:
            raise ImportPreviewError(
                "import_preview_asset_invalid", "这幅原图不属于当前Word预览。"
            )
        result = self.reader.word_asset_bytes(self._bytes(source, private), asset_id)
        if hashlib.sha256(result["bytes"]).hexdigest() != asset["sha256"]:
            raise ImportPreviewError(
                "import_preview_changed", "原图内容发生变化，请重新预览。"
            )
        return result

    def discard(self, preview_id):
        with self._lock:
            session = (
                self._sessions.get(preview_id) if isinstance(preview_id, str) else None
            )
            if session is not None and not session["busy"]:
                self._sessions.pop(preview_id)

    def commit(
        self,
        preview_id,
        expected_revision,
        selected_source_ids,
        *,
        progress_callback=None,
        should_cancel=None,
    ):
        session = self._session(preview_id, expected_revision)
        if (
            not isinstance(selected_source_ids, list)
            or not selected_source_ids
            or any(not isinstance(value, str) for value in selected_source_ids)
            or len(set(selected_source_ids)) != len(selected_source_ids)
        ):
            raise ImportPreviewError(
                "import_preview_selection_invalid",
                "请勾选至少一份文件，同一文件不能重复选择。",
            )
        selected_set = set(selected_source_ids)
        if not selected_set.issubset(session["private"]):
            raise ImportPreviewError(
                "import_preview_source_missing", "所选文件不属于当前预览，请重新选择。"
            )
        selected = [
            row
            for row in session["result"]["sources"]
            if row["source_id"] in selected_set
        ]
        if all(row["role"] == "answer" for row in selected):
            raise ImportPreviewError(
                "question_source_required", "不能只导入答案，请同时勾选题目或讲义文件。"
            )
        paths = {role: [] for role in ROLES}
        expected_inputs = []
        for row in selected:
            private = session["private"][row["source_id"]]
            paths[row["role"]].append(private["path"])
            expected_inputs.append(
                {
                    "role": row["role"],
                    "order_index": len(paths[row["role"]]),
                    "filename": row["source_name"],
                    "mime_type": private["mime_type"],
                    "source_sha256": row["source_sha256"],
                    "size_bytes": private["size_bytes"],
                    "group_id": "default",
                }
            )
        with self._lock:
            if session["busy"]:
                raise ImportPreviewError(
                    "import_preview_busy", "这批文件正在导入，请等待完成。"
                )
            session["busy"] = True
        try:
            result = self.facade.save_visual_import_batch(
                question_files=tuple(paths["question"]),
                answer_files=tuple(paths["answer"]),
                handout_files=tuple(paths["handout"]),
                source_type=session["source_type"],
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                _expected_inputs=expected_inputs,
            )
        finally:
            with self._lock:
                session["busy"] = False
        self.discard(preview_id)
        return result


def teaching_pack_analysis_files(shchem_root):
    """Read only the named old pack inventory; never discover unrelated folders."""
    library_root = Path(shchem_root).resolve()
    root = (library_root / TEACHING_PACK).resolve()
    expanded = (root / "expanded").resolve()
    inventory = root / "archive_inventory.csv"
    try:
        if (
            not root.is_relative_to(library_root)
            or not expanded.is_relative_to(root)
            or not inventory.resolve().is_relative_to(root)
        ):
            raise ValueError("pack outside library")
        with inventory.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        paths, seen = [], set()
        for row in rows:
            if row.get("document_role") != "解析版":
                continue
            relative = row.get("output_relative_path", "")
            if not relative or Path(relative).is_absolute():
                raise ValueError("invalid source path")
            path = (root / relative).resolve()
            if (
                not path.is_relative_to(expanded)
                or path.suffix.casefold() != ".docx"
                or path in seen
            ):
                raise ValueError("source outside pack or duplicate")
            recorded = row.get("sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", recorded):
                raise ValueError("invalid digest")
            digest, size = _fingerprint(path)
            if digest != recorded or size != int(row.get("bytes", "")):
                raise ValueError("source changed")
            seen.add(path)
            paths.append(str(path))
        if not paths or len(paths) > MAX_FILES:
            raise ValueError("empty or oversized inventory")
        return tuple(paths)
    except (OSError, UnicodeError, csv.Error, ValueError, TypeError) as exc:
        raise ImportPreviewError(
            "teaching_pack_inventory_invalid",
            "已有讲义清单缺失、内容变化或路径不正确，请核对旧资料包后重试；未导入任何文件。",
        ) from exc
