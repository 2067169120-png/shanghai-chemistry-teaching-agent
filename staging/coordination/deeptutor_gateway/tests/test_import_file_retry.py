from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from test_desktop_visual_import_facade import _docx, _png, _facade, desktop_paths, FakeProviderStore, FakeVisualTransport
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_import_recovery import import_batch_lock
from integrations.deeptutor_shchem_v1.word_handout_import import WordHandoutImportError, WordHandoutImporter


def _partial(paths, tmp_path, monkeypatch, *, failed=("two.docx",)):
    files = []
    for name in ("one.docx", "two.docx", "three.docx"):
        path = tmp_path / name
        path.write_bytes(_docx())
        files.append(path)
    calls = []
    original = WordHandoutImporter.scan_document
    failing = set(failed)

    def scan(self, path):
        calls.append(Path(path).name)
        if Path(path).name in failing:
            raise WordHandoutImportError("docx_parse_failed", "合成读取失败")
        return original(self, path)

    monkeypatch.setattr(WordHandoutImporter, "scan_document", scan)
    facade = _facade(paths, FakeProviderStore(configured=False))
    result = facade.save_visual_import_batch(question_files=files, source_type="本机合成资料")
    return facade, result, calls, failing


def _hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*.json")}


def test_partial_failure_survives_restart_and_retries_only_selected_file(desktop_paths, tmp_path, monkeypatch):
    facade, saved, calls, failing = _partial(desktop_paths, tmp_path, monkeypatch)
    assert saved.status == "failed"
    assert saved.visual_status == "not_required"
    assert saved.native_failed_count == 1
    assert saved.native_completed_count == 2
    assert "Word读取成功 2 份，失败 1 份" in saved.message_zh
    completed = {item.source_id: item for item in saved.native_files if item.status == "completed"}
    cache = desktop_paths.state_root / "word-handout-import" / "documents"
    before = _hashes(cache)
    restarted = _facade(desktop_paths, FakeProviderStore(configured=False))
    receipt = restarted.list_import_batches()[0]
    assert receipt.native_revision == saved.native_revision
    assert receipt.status == "failed"
    failed = [item.source_id for item in receipt.native_files if item.status == "failed"]
    failing.clear()
    calls.clear()
    result = restarted.retry_failed_word_import_files(receipt.batch_id, expected_revision=receipt.native_revision, source_ids=failed)
    assert calls == ["two.docx"]
    assert result.status == "candidate_ready_for_review"
    assert result.native_failed_count == 0
    assert result.native_completed_count == 3
    assert restarted.list_resumable_visual_import_batches() == ()
    assert all(_hashes(cache)[key] == digest for key, digest in before.items())
    assert all(item == completed[item.source_id] for item in result.native_files if item.source_id in completed)
    assert next(item for item in result.native_files if item.source_id in failed).attempt_count == 2
    assert receipt.native_revision != result.native_revision
    with pytest.raises(DesktopFacadeError, match="变化"):
        restarted.retry_failed_word_import_files(receipt.batch_id, expected_revision=receipt.native_revision, source_ids=failed)


def test_two_failures_retry_one_preserves_other_result(desktop_paths, tmp_path, monkeypatch):
    facade, saved, calls, failing = _partial(desktop_paths, tmp_path, monkeypatch, failed=("two.docx", "three.docx"))
    old_third = next(item for item in saved.native_files if item.filename == "three.docx")
    second = next(item for item in saved.native_files if item.filename == "two.docx")
    failing.clear()
    calls.clear()
    result = facade.retry_failed_word_import_files(saved.batch_id, expected_revision=saved.native_revision, source_ids=[second.source_id])
    assert calls == ["two.docx"]
    assert result.native_failed_count == 1 and result.status == "failed"
    assert next(item for item in result.native_files if item.filename == "three.docx") == old_third


@pytest.mark.parametrize("selection", ["empty", "unknown", "completed", "duplicate"])
def test_invalid_selection_does_not_run_importer(desktop_paths, tmp_path, monkeypatch, selection):
    facade, saved, calls, _ = _partial(desktop_paths, tmp_path, monkeypatch)
    failed = next(item.source_id for item in saved.native_files if item.status == "failed")
    success = next(item.source_id for item in saved.native_files if item.status == "completed")
    values = {"empty": [], "unknown": ["missing"], "completed": [success], "duplicate": [failed, failed]}
    calls.clear()
    with pytest.raises(DesktopFacadeError):
        facade.retry_failed_word_import_files(saved.batch_id, expected_revision=saved.native_revision, source_ids=values[selection])
    assert calls == []


def test_changed_source_and_missing_success_cache_block_retry(desktop_paths, tmp_path, monkeypatch):
    facade, saved, calls, failing = _partial(desktop_paths, tmp_path, monkeypatch)
    failed = next(item.source_id for item in saved.native_files if item.status == "failed")
    cache_file = next((desktop_paths.state_root / "word-handout-import" / "documents").glob("*.json"))
    raw = cache_file.read_bytes()
    cache_file.write_bytes(b"{}")
    calls.clear()
    with pytest.raises(DesktopFacadeError):
        facade.retry_failed_word_import_files(saved.batch_id, expected_revision=saved.native_revision, source_ids=[failed])
    assert calls == []
    cache_file.write_bytes(raw)
    source = next((desktop_paths.state_root / "visual-import-v2" / "sources").glob("*.docx"))
    source.write_bytes(b"changed")
    with pytest.raises(DesktopFacadeError, match="变化"):
        facade.retry_failed_word_import_files(saved.batch_id, expected_revision=saved.native_revision, source_ids=[failed])
    assert calls == []


def test_batch_lock_rejects_concurrent_retry_and_releases(desktop_paths, tmp_path, monkeypatch):
    facade, saved, calls, _ = _partial(desktop_paths, tmp_path, monkeypatch)
    failed = next(item.source_id for item in saved.native_files if item.status == "failed")
    calls.clear()
    with import_batch_lock(facade._visual_import_root, saved.batch_id):
        with pytest.raises(DesktopFacadeError, match="正在处理"):
            facade.retry_failed_word_import_files(saved.batch_id, expected_revision=saved.native_revision, source_ids=[failed])
    assert calls == []
    with import_batch_lock(facade._visual_import_root, saved.batch_id):
        pass


def test_legacy_count_only_receipt_resolves_exact_failures_without_writing(desktop_paths, tmp_path, monkeypatch):
    facade, saved, calls, failing = _partial(desktop_paths, tmp_path, monkeypatch)
    path = facade._visual_import_root / "batches" / f"{saved.batch_id}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["native_import_receipt"].pop("documents")
    from integrations.deeptutor_shchem_v1.visual_provider_runtime import canonical_json_bytes
    path.write_bytes(canonical_json_bytes(value))
    before = _hashes(desktop_paths.state_root)
    calls.clear()
    receipt = facade.import_batch_details(saved.batch_id)
    assert receipt.native_failed_count == 1 and len(receipt.native_files) == 3
    assert all(not row.attempt_count_known for row in receipt.native_files)
    assert before == _hashes(desktop_paths.state_root)
    assert calls == []
    failing.clear()
    result = facade.retry_failed_word_import_files(
        saved.batch_id, expected_revision=receipt.native_revision,
        source_ids=[item.source_id for item in receipt.native_files if item.status == "failed"],
    )
    assert calls == ["two.docx"] and result.native_failed_count == 0
    assert all(not row.attempt_count_known for row in result.native_files)


def test_save_again_does_not_mask_or_automatically_retry_failures(desktop_paths, tmp_path, monkeypatch):
    facade, saved, calls, _ = _partial(desktop_paths, tmp_path, monkeypatch)
    calls.clear()
    repeated = facade.save_visual_import_batch(
        question_files=[tmp_path / name for name in ("one.docx", "two.docx", "three.docx")], source_type="本机合成资料",
    )
    assert repeated.status == "failed" and repeated.native_failed_count == 1
    assert repeated.native_revision == saved.native_revision
    assert calls == []


def test_legacy_failure_map_survives_interrupt_after_native_commit(desktop_paths, tmp_path, monkeypatch):
    facade, saved, calls, failing = _partial(desktop_paths, tmp_path, monkeypatch)
    path = facade._visual_import_root / "batches" / f"{saved.batch_id}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["native_import_receipt"].pop("documents")
    from integrations.deeptutor_shchem_v1.visual_provider_runtime import canonical_json_bytes
    from integrations.deeptutor_shchem_v1.desktop_visual_import_v2 import DesktopImportBridgeError
    path.write_bytes(canonical_json_bytes(value))
    legacy = facade.import_batch_details(saved.batch_id)
    original = facade._visual_import_native_importer.retry
    failing.clear()

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise DesktopImportBridgeError("synthetic_interruption", "模拟中断", 503)

    monkeypatch.setattr(facade._visual_import_native_importer, "retry", interrupted)
    selected = [row.source_id for row in legacy.native_files if row.status == "failed"]
    with pytest.raises(DesktopFacadeError, match="模拟中断"):
        facade.retry_failed_word_import_files(saved.batch_id, expected_revision=legacy.native_revision, source_ids=selected)
    restarted = _facade(desktop_paths, FakeProviderStore(configured=False))
    fresh = restarted.import_batch_details(saved.batch_id)
    assert fresh.native_failed_count == 1 and len(fresh.native_files) == 3
    calls.clear()
    recovered = restarted.retry_failed_word_import_files(saved.batch_id, expected_revision=fresh.native_revision, source_ids=selected)
    assert calls == [] and recovered.native_failed_count == 0


def test_retry_conflict_cannot_overwrite_newer_visual_manifest(desktop_paths, tmp_path, monkeypatch):
    facade, saved, calls, failing = _partial(desktop_paths, tmp_path, monkeypatch)
    path = facade._visual_import_root / "batches" / f"{saved.batch_id}.json"
    adapter = facade._visual_import_native_importer
    original = adapter.retry
    failing.clear()

    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["blockers"].append({"code": "newer-result", "message_zh": "模拟并发结果"})
        from integrations.deeptutor_shchem_v1.visual_provider_runtime import canonical_json_bytes
        path.write_bytes(canonical_json_bytes(value))
        return result

    monkeypatch.setattr(adapter, "retry", race)
    with pytest.raises(DesktopFacadeError):
        facade.retry_failed_word_import_files(saved.batch_id, expected_revision=saved.native_revision,
            source_ids=[item.source_id for item in saved.native_files if item.status == "failed"])
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["blockers"][-1]["code"] == "newer-result"
    assert value["native_import_receipt"]["documents_failed"] == 1
    # The native cache was committed before the enclosing manifest conflicted.
    # Refresh and retry must recover that result without parsing it twice.
    monkeypatch.setattr(adapter, "retry", original)
    calls.clear()
    refreshed = facade.import_batch_details(saved.batch_id)
    recovered = facade.retry_failed_word_import_files(saved.batch_id,
        expected_revision=refreshed.native_revision,
        source_ids=[item.source_id for item in refreshed.native_files if item.status == "failed"])
    assert recovered.native_failed_count == 0 and calls == []
    assert json.loads(path.read_text(encoding="utf-8"))["blockers"] == value["blockers"]


def test_native_retry_preserves_completed_visual_candidate_and_blocks_repeat_egress(desktop_paths, tmp_path, monkeypatch):
    _unused, _saved, calls, failing = _partial(desktop_paths, tmp_path, monkeypatch)
    picture = tmp_path / "synthetic.png"
    picture.write_bytes(_png())
    provider, transport = FakeProviderStore(), FakeVisualTransport()
    facade = _facade(desktop_paths, provider, transport=transport)
    saved = facade.save_visual_import_batch(
        question_files=[tmp_path / name for name in ("one.docx", "two.docx", "three.docx")] + [picture],
        source_type="混合合成资料",
    )
    preview = facade.preview_saved_visual_import_batch(batch_id=saved.batch_id,
        profile_id="vision", expected_profile_revision="REV-1")
    pending_descriptor = facade._saved_visual_import_batch(saved.batch_id)
    completed = facade.run_saved_visual_import_batch(batch_id=saved.batch_id, profile_id="vision",
        expected_profile_revision="REV-1", teacher_confirmed=True,
        egress_preview_id=preview["preview_id"], egress_revision=preview["revision"])
    assert completed.status == "failed" and completed.visual_status == "completed"
    assert completed.native_failed_count == 1 and transport.calls == 2
    with monkeypatch.context() as stale_descriptor:
        stale_descriptor.setattr(facade, "_saved_visual_import_batch", lambda _id: pending_descriptor)
        with pytest.raises(DesktopFacadeError, match="不能重复发送"):
            facade.preview_saved_visual_import_batch(batch_id=saved.batch_id,
                profile_id="vision", expected_profile_revision="REV-1")
        with pytest.raises(DesktopFacadeError, match="不能重复覆盖"):
            facade.run_saved_visual_import_batch(batch_id=saved.batch_id, profile_id="vision",
                expected_profile_revision="REV-1", teacher_confirmed=True,
                egress_preview_id=preview["preview_id"], egress_revision=preview["revision"])
    candidate_root = facade._visual_import_root / "candidates"
    candidate_before = _hashes(candidate_root)
    path = facade._visual_import_root / "batches" / f"{saved.batch_id}.json"
    before = json.loads(path.read_text(encoding="utf-8"))
    failing.clear()
    calls.clear()
    result = facade.retry_failed_word_import_files(saved.batch_id,
        expected_revision=completed.native_revision,
        source_ids=[item.source_id for item in completed.native_files if item.status == "failed"])
    after = json.loads(path.read_text(encoding="utf-8"))
    before.pop("native_import_receipt")
    after.pop("native_import_receipt")
    assert before == after and candidate_before == _hashes(candidate_root)
    assert calls == ["two.docx"] and transport.calls == 2
    assert result.status == "candidate_ready_for_review" and result.visual_status == "completed"


def test_duplicate_names_keep_stable_file_identity_on_retry(desktop_paths, tmp_path, monkeypatch):
    files = []
    for index, name in enumerate(("same.docx", "003-same.docx", "same.docx"), 1):
        folder = tmp_path / str(index)
        folder.mkdir()
        path = folder / name
        raw = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(_docx())) as source, zipfile.ZipFile(raw, "w") as target:
            xml = source.read("word/document.xml").decode("utf-8")
            target.writestr("word/document.xml", xml.replace("测试候选", f"合成候选{index}"))
        path.write_bytes(raw.getvalue())
        files.append(path)
    original = WordHandoutImporter.scan_document
    calls, failing = [], {"003-003-same.docx"}

    def scan(self, path):
        calls.append(Path(path).name)
        if Path(path).name in failing:
            raise WordHandoutImportError("docx_parse_failed", "合成读取失败")
        return original(self, path)

    monkeypatch.setattr(WordHandoutImporter, "scan_document", scan)
    facade = _facade(desktop_paths, FakeProviderStore(configured=False))
    saved = facade.save_visual_import_batch(question_files=files, source_type="同名合成资料")
    assert calls == ["same.docx", "003-same.docx", "003-003-same.docx"]
    assert len({row.source_id for row in saved.native_files}) == 3
    failing.clear()
    calls.clear()
    result = facade.retry_failed_word_import_files(saved.batch_id, expected_revision=saved.native_revision,
        source_ids=[row.source_id for row in saved.native_files if row.status == "failed"])
    assert calls == ["003-003-same.docx"] and result.native_failed_count == 0
