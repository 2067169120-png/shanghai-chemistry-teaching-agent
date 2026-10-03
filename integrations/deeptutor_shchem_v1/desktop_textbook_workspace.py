"""Whole-book local intake and an explicitly candidate-only knowledge shelf.

The existing section reader remains authoritative for activated directories.
This shelf can archive a complete PDF without treating it as reviewed content
or sending hundreds of pages to the visual importer.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .desktop_import_identity import identity_guard
from .desktop_preparation_sources import PreparationSourceError
from .desktop_state import utc_now
from .reader_cancellation import ReadCancelled, check_read_cancelled

SCHEMA = "shchem.textbook-workspace.v1"
MAX_BOOK_BYTES = 256 * 1024 * 1024
MAX_BATCH_BYTES = 512 * 1024 * 1024
MAX_CANDIDATE_BYTES = 8 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _read_pdf(path):
    from pypdf import PdfReader

    from .model_provider_settings import _assert_components_not_reparse
    try:
        path = Path(path).expanduser().absolute()
        _assert_components_not_reparse(path)
        if path.suffix.casefold() != ".pdf" or path.stat().st_size > MAX_BOOK_BYTES:
            raise ValueError("unsupported PDF")
        with path.open("rb") as handle:
            raw = handle.read(MAX_BOOK_BYTES + 1)
        if len(raw) > MAX_BOOK_BYTES or not raw.startswith(b"%PDF-"):
            raise ValueError("unsupported PDF")
        check_read_cancelled()
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("encrypted PDF")
        pages = len(reader.pages)
        if not 1 <= pages <= 3000:
            raise ValueError("unsupported page count")
        return raw, pages
    except (PreparationSourceError, ReadCancelled):
        raise
    except Exception as exc:
        raise PreparationSourceError("教材PDF无法读取；请选择完整、未加密且不超过256MB的本地PDF。") from exc


def _atomic_bytes(path, raw):
    from .model_provider_settings import _assert_components_not_reparse
    _assert_components_not_reparse(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".textbook-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _candidates(raw):
    if len(raw) > MAX_CANDIDATE_BYTES:
        raise PreparationSourceError("教材知识候选文件过大，请分批核对。")
    try:
        rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
        ids = set()
        for row in rows:
            if (not isinstance(row, dict) or row.get("candidate_only") is not True
                    or row.get("human_reviewed") is not False
                    or any(row.get(key) is not False for key in
                        ("teaching_use_allowed", "generation_allowed", "publication_allowed"))
                    or any(not isinstance(row.get(key), str) or not row[key].strip()
                        for key in ("concept_id", "title", "summary"))
                    or not isinstance(row.get("curriculum"), dict)
                    or not isinstance(row.get("source"), dict)
                    or not re.fullmatch(r"[0-9a-f]{64}", str(row["source"].get("sha256", "")))
                    or row["concept_id"] in ids):
                raise ValueError("invalid candidate")
            ids.add(row["concept_id"])
        return rows
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise PreparationSourceError("教材知识候选格式或权限记录不完整，原文件保留，请核对后重试。") from exc


class TextbookWorkspaceService:
    def __init__(self, paths, state):
        self.paths, self.state = paths, state
        self.root = paths.state_root / "textbook-sources"

    @staticmethod
    def _record(value):
        record = value.get("textbook_workspace", {"schema": SCHEMA, "books": [], "candidate_snapshot": None})
        if (not isinstance(record, dict) or record.get("schema") != SCHEMA
                or not isinstance(record.get("books"), list)):
            raise PreparationSourceError("教材导入记录无法读取，请保留原记录并核对备份。")
        ids = set()
        for book in record["books"]:
            sha = book.get("source_sha256", "") if isinstance(book, dict) else ""
            if (not isinstance(book, dict) or not re.fullmatch(r"[0-9a-f]{64}", sha)
                    or book.get("source_id") != "BOOK-" + sha or book["source_id"] in ids
                    or not isinstance(book.get("source_name"), str)
                    or type(book.get("page_count")) is not int or not 1 <= book["page_count"] <= 3000
                    or book.get("human_reviewed") is not False
                    or book.get("review_status") != "local_archived_pending_review"):
                raise PreparationSourceError("教材来源记录不完整，已停止保存；请核对备份。")
            ids.add(book["source_id"])
        snapshot = record.get("candidate_snapshot")
        if snapshot is not None and (not isinstance(snapshot, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("sha256", "")))
                or type(snapshot.get("count")) is not int or snapshot["count"] < 0):
            raise PreparationSourceError("教材候选快照记录不完整，请核对备份。")
        return record

    def books(self):
        return deepcopy(self._record(self.state.snapshot())["books"])

    def preview_books(self, files):
        if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= 100:
            raise PreparationSourceError("每批请选择1至100份教材PDF。")
        existing = {row["source_sha256"] for row in self.books()}
        sources, seen, total = [], set(), 0
        for path in files:
            check_read_cancelled()
            raw, pages = _read_pdf(path)
            total += len(raw)
            if total > MAX_BATCH_BYTES:
                raise PreparationSourceError("本批教材超过512MB，请分批导入。")
            sha = hashlib.sha256(raw).hexdigest()
            sources.append({"path": str(Path(path).expanduser().absolute()), "source_name": Path(path).name,
                "source_sha256": sha, "page_count": pages, "size_bytes": len(raw),
                "duplicate": sha in existing or sha in seen})
            seen.add(sha)
        return {"sources": sources, "revision": _digest(sources)}

    def commit_books(self, preview, *, progress_callback=None, should_cancel=None):
        if (not isinstance(preview, dict) or not isinstance(preview.get("sources"), list)
                or not 1 <= len(preview["sources"]) <= 100
                or preview.get("revision") != _digest(preview["sources"])):
            raise PreparationSourceError("教材预览已变化，请重新预览后导入。")
        added, reused = [], []
        with identity_guard(self.paths.state_root):
            for index, selected in enumerate(preview["sources"], 1):
                check_read_cancelled()
                if should_cancel and should_cancel():
                    break
                raw, pages = _read_pdf(selected["path"])
                sha = hashlib.sha256(raw).hexdigest()
                if (sha != selected["source_sha256"] or pages != selected["page_count"]
                        or len(raw) != selected["size_bytes"]):
                    raise PreparationSourceError("教材原文件在预览后发生变化，请重新预览。此前已保存的教材保留。")
                book = {"source_id": "BOOK-" + sha, "source_name": selected["source_name"],
                    "source_sha256": sha, "page_count": pages, "size_bytes": len(raw),
                    "imported_at": utc_now(), "review_status": "local_archived_pending_review",
                    "human_reviewed": False, "source_kind": "user_selected_pdf",
                    "source_url": None, "year": None, "region": None, "school": None,
                    "answer_status": "unknown", "scoring_status": "unknown"}
                destination = self.root / (sha + ".pdf")
                if destination.exists():
                    from .model_provider_settings import _assert_components_not_reparse
                    _assert_components_not_reparse(destination)
                    if destination.stat().st_size != len(raw) or hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
                        raise PreparationSourceError("已归档教材的内容发生变化，未覆盖旧文件；请核对备份。")
                else:
                    _atomic_bytes(destination, raw)
                was_added = False
                def edit(value, book=book):
                    nonlocal was_added
                    record = self._record(value)
                    if any(row["source_id"] == book["source_id"] for row in record["books"]):
                        return False
                    record["books"].append(book)
                    value["textbook_workspace"] = record
                    was_added = True
                self.state._update(edit)
                (added if was_added else reused).append(book["source_id"])
                if progress_callback:
                    progress_callback({"completed": index, "total": len(preview["sources"])})
        return {"added": len(added), "reused": len(reused), "source_ids": added + reused}

    def read_book(self, source_id, revision):
        book = next((row for row in self.books() if row["source_id"] == source_id), None)
        if book is None or revision != book["source_sha256"]:
            raise PreparationSourceError("教材来源已变化或缺失，请刷新后重选。")
        raw, pages = _read_pdf(self.root / (book["source_sha256"] + ".pdf"))
        if hashlib.sha256(raw).hexdigest() != revision or pages != book["page_count"]:
            raise PreparationSourceError("教材归档内容与记录不一致，请核对原书或备份。")
        return {**book, "pdf_bytes": raw, "pdf_pages": list(range(1, pages + 1)),
            "title": book["source_name"], "statement": "整本原文件已保留。目录、印刷页码与化学内容尚待核对。",
            "reading_mode": "book"}

    def candidate_catalog(self):
        record = self._record(self.state.snapshot())
        snapshot = record.get("candidate_snapshot")
        if snapshot is not None:
            if not isinstance(snapshot, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("sha256", ""))):
                raise PreparationSourceError("教材候选快照记录无法读取，请核对备份。")
            path = self.root / (snapshot["sha256"] + ".jsonl")
        else:
            path = self.paths.workspace_root / "knowledge" / "textbook" / "knowledge.jsonl"
        try:
            from .model_provider_settings import _assert_components_not_reparse
            _assert_components_not_reparse(path)
            with path.open("rb") as handle:
                raw = handle.read(MAX_CANDIDATE_BYTES + 1)
        except OSError as exc:
            raise PreparationSourceError("教材知识候选尚未提供，请先导入教材或恢复知识候选文件。") from exc
        if snapshot is not None and hashlib.sha256(raw).hexdigest() != snapshot["sha256"]:
            raise PreparationSourceError("教材候选快照内容发生变化，未改写原记录。")
        rows = _candidates(raw)
        books = {book["source_sha256"]: book for book in self.books()}
        return {"rows": rows, "books": books, "imported": snapshot is not None,
            "sha256": hashlib.sha256(raw).hexdigest(), "count": len(rows)}

    def import_candidates(self):
        path = self.paths.workspace_root / "knowledge" / "textbook" / "knowledge.jsonl"
        from .model_provider_settings import _assert_components_not_reparse
        _assert_components_not_reparse(path)
        with path.open("rb") as handle:
            raw = handle.read(MAX_CANDIDATE_BYTES + 1)
        rows = _candidates(raw)
        sha = hashlib.sha256(raw).hexdigest()
        with identity_guard(self.paths.state_root):
            destination = self.root / (sha + ".jsonl")
            if destination.exists():
                _assert_components_not_reparse(destination)
                if destination.read_bytes() != raw:
                    raise PreparationSourceError("候选归档内容发生变化，未覆盖旧文件。")
            else:
                _atomic_bytes(destination, raw)
            def edit(value):
                record = self._record(value)
                if (record.get("candidate_snapshot") or {}).get("sha256") == sha:
                    return False
                record["candidate_snapshot"] = {"sha256": sha, "count": len(rows), "imported_at": utc_now()}
                value["textbook_workspace"] = record
            self.state._update(edit)
        return {"count": len(rows), "sha256": sha, "review_status": "candidate-only"}

    def read_candidate(self, concept_id, revision):
        catalog = self.candidate_catalog()
        row = next((row for row in catalog["rows"] if row["concept_id"] == concept_id), None)
        if row is None or _digest(row) != revision:
            raise PreparationSourceError("教材知识候选已变化，请刷新后重选。")
        book = catalog["books"].get(row["source"]["sha256"])
        if book is None:
            raise PreparationSourceError("该知识候选对应的教材原PDF尚未导入，请先导入原书；候选摘要保留。")
        source = self.read_book(book["source_id"], book["source_sha256"])
        pages = row["source"].get("pdf_pages")
        if (not isinstance(pages, list) or not pages or len(set(pages)) != len(pages)
                or any(type(page) is not int or not 1 <= page <= book["page_count"] for page in pages)):
            raise PreparationSourceError("候选页序无法核对，未猜测或扩大阅读范围。")
        return {**source, "reading_mode": "concept", "concept_id": concept_id,
            "title": row["title"], "statement": row["summary"], "pdf_pages": list(pages)}

    def export_bundle(self, destination):
        """Back up only this shelf, including intact PDFs and candidate bytes."""
        from .model_provider_settings import _assert_components_not_reparse
        destination = Path(destination)
        _assert_components_not_reparse(destination)
        with identity_guard(self.paths.state_root):
            record = deepcopy(self._record(self.state.snapshot()))
            names = [book["source_sha256"] + ".pdf" for book in record["books"]]
            if record.get("candidate_snapshot"):
                names.append(record["candidate_snapshot"]["sha256"] + ".jsonl")
            files = []
            total = 0
            for name in names:
                check_read_cancelled()
                path = self.root / name
                _assert_components_not_reparse(path)
                size = path.stat().st_size
                total += size
                if size > MAX_BOOK_BYTES or total > 2 * 1024 ** 3:
                    raise PreparationSourceError("教材备份超过单份256MB或总量2GB的限制，请分开保管原件。")
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if sha != name.split(".")[0]:
                    raise PreparationSourceError("教材归档内容发生变化，未生成备份；请核对原件。")
                files.append({"name": name, "sha256": sha, "size_bytes": size})
            manifest = {"schema": "shchem.textbook-backup.v1", "record": record, "files": files}
            created = False
            try:
                with destination.open("xb") as handle:
                    created = True
                    with ZipFile(handle, "w", compression=ZIP_DEFLATED) as archive:
                        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                        for file in files:
                            check_read_cancelled()
                            raw = (self.root / file["name"]).read_bytes()
                            if len(raw) != file["size_bytes"] or hashlib.sha256(raw).hexdigest() != file["sha256"]:
                                raise PreparationSourceError("教材在备份时变化，请重新核对后备份。")
                            archive.writestr("textbook-sources/" + file["name"], raw)
            except Exception:
                if created:
                    destination.unlink(missing_ok=True)
                raise
        return {"books": len(record["books"]), "files": len(files)}

    @staticmethod
    def restore_bundle(bundle, destination):
        """Validate a bundle and restore into a new, independent state root."""
        from .desktop_state import DesktopStateStore
        from .model_provider_settings import _assert_components_not_reparse
        destination = Path(destination).absolute()
        _assert_components_not_reparse(destination)
        _assert_components_not_reparse(Path(bundle).absolute())
        if destination.exists() or not destination.parent.is_dir():
            raise PreparationSourceError("请选择已有目录下尚不存在的恢复目录；不会覆盖当前资料。")
        temporary = Path(tempfile.mkdtemp(prefix=".textbook-restore-", dir=destination.parent))
        try:
            with ZipFile(bundle) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if len(names) != len(set(names)) or len(names) > 1002:
                    raise PreparationSourceError("教材备份包含重复或过多文件，未恢复。")
                if any(entry.file_size > MAX_BOOK_BYTES for entry in entries) or sum(e.file_size for e in entries) > 2 * 1024 ** 3:
                    raise PreparationSourceError("教材备份体积超过限制，未恢复。")
                manifest_entry = archive.getinfo("manifest.json")
                if manifest_entry.file_size > MAX_CANDIDATE_BYTES:
                    raise PreparationSourceError("教材备份清单过大，未恢复。")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("schema") != "shchem.textbook-backup.v1" or not isinstance(manifest.get("files"), list):
                    raise PreparationSourceError("教材备份版本无法识别，未恢复。")
                record = TextbookWorkspaceService._record({"textbook_workspace": manifest["record"]})
                expected = {book["source_sha256"] + ".pdf" for book in record["books"]}
                snapshot = record.get("candidate_snapshot")
                if snapshot:
                    if not re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("sha256", ""))):
                        raise PreparationSourceError("知识候选快照记录不完整，未恢复。")
                    expected.add(snapshot["sha256"] + ".jsonl")
                listed = [file["name"] for file in manifest["files"]]
                if (len(set(listed)) != len(listed) or set(listed) != expected
                        or set(names) != {"manifest.json", *("textbook-sources/" + name for name in expected)}):
                    raise PreparationSourceError("教材备份清单与文件不一致，未恢复。")
                for file in manifest["files"]:
                    check_read_cancelled()
                    name = file["name"]
                    if not re.fullmatch(r"[0-9a-f]{64}\.(?:pdf|jsonl)", name):
                        raise PreparationSourceError("教材备份路径无法核对，未恢复。")
                    raw = archive.read("textbook-sources/" + name)
                    sha = hashlib.sha256(raw).hexdigest()
                    if len(raw) != file["size_bytes"] or sha != file["sha256"] or sha != name.split(".")[0]:
                        raise PreparationSourceError("教材备份内容校验失败，未恢复。")
                    if name.endswith(".jsonl"):
                        _candidates(raw)
                    _atomic_bytes(temporary / "textbook-sources" / name, raw)
                state = DesktopStateStore(temporary)
                state._update(lambda value: value.update(textbook_workspace=record))
                from .desktop_backup import MARKER, RESTORED_SCHEMA
                _atomic_bytes(temporary / MARKER, json.dumps({"schema_version": RESTORED_SCHEMA,
                    "scope": "textbook-workspace", "restored_at": utc_now()}, ensure_ascii=False).encode())
            # Reserve a new directory, then publish the launch marker last.
            # This also prevents POSIX rename from replacing an existing empty
            # directory created after the initial destination check.
            from .desktop_backup import MARKER
            destination.mkdir(exist_ok=False)
            try:
                for child in temporary.iterdir():
                    if child.name != MARKER:
                        os.replace(child, destination / child.name)
                os.replace(temporary / MARKER, destination / MARKER)
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                raise
            temporary.rmdir()
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return {"books": len(record["books"]), "files": len(manifest["files"])}
