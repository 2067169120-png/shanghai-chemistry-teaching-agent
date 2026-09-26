"""Actual desktop two-stage transport, frozen previews, restart and crash recovery."""

from __future__ import annotations

import json

import pytest

from test_desktop_visual_import_facade import (
    FakeProviderStore, FakeVisualTransport, _docx, _facade, _png, desktop_paths,
)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_import_recovery import import_batch_lock
from integrations.deeptutor_shchem_v1.desktop_visual_import_v2 import DesktopImportBridgeError, DesktopImportCoordinatorV2
from integrations.deeptutor_shchem_v1.visual_provider_runtime import parse_structured_visual_response


class RecordingTransport(FakeVisualTransport):
    def __init__(self, *, fail_phase=None, fail_shard=2):
        super().__init__()
        self.fail_phase, self.fail_shard = fail_phase, fail_shard
        self.events = []
        self.current_shard = None

    def send(self, request, **kwargs):
        body = json.loads(request.body)
        metadata = json.loads(body["input"][0]["content"][0]["text"].split("\n", 1)[1])
        phase = "review" if body["text"]["format"]["name"] == "shchem_visual_crop_review_v1" else "extract"
        if phase == "extract":
            self.current_shard = metadata["shard_id"]
        self.events.append((phase, self.current_shard))
        if phase == self.fail_phase and self.current_shard.endswith(f":{self.fail_shard:04d}"):
            raise RuntimeError("synthetic transport interruption")
        response = super().send(request, **kwargs)
        if phase == "extract":
            fragment, _usage = parse_structured_visual_response(request.api_style, response.body)
            for theme in fragment["theme_fragments"]:
                theme["sequence_in_paper"] = metadata["shard_index"]
                theme["theme_number"] = str(metadata["shard_index"])
            return self._response(fragment)
        return response


def _preview(facade, saved, revision="REV-1"):
    return facade.preview_saved_visual_import_batch(
        batch_id=saved.batch_id, profile_id="vision", expected_profile_revision=revision,
    )


def _run(facade, saved, plan, revision="REV-1", **kwargs):
    return facade.run_saved_visual_import_batch(
        batch_id=saved.batch_id, profile_id="vision", expected_profile_revision=revision,
        teacher_confirmed=True, egress_preview_id=plan["preview_id"], egress_revision=plan["revision"],
        **kwargs,
    )


def _batch(paths, tmp_path, *, transport=None, handout_gap=False):
    provider = FakeProviderStore()
    facade = _facade(paths, provider, transport=transport or RecordingTransport())
    files = []
    if handout_gap:
        for name, raw in (("native.docx", _docx()), ("hybrid.docx", _docx(hybrid=True))):
            path = tmp_path / name
            path.write_bytes(raw)
            files.append(path)
    for index, color in enumerate(("white", "blue", "red") if not handout_gap else ("white", "red"), 1):
        path = tmp_path / f"synthetic-{index}.png"
        path.write_bytes(_png(color))
        files.append(path)
    saved = facade.save_visual_import_batch(
        **{"handout_files" if handout_gap else "question_files": files}, source_type="合成分片恢复",
    )
    return facade, provider, saved


def _records(paths, batch_id):
    return sorted((paths.state_root / "visual-import-v2/shard-checkpoints" / batch_id).glob("*/shard-*.json"))


@pytest.mark.parametrize("phase", ["extract", "review"])
def test_restart_reuses_only_fully_reviewed_shards(desktop_paths, tmp_path, phase):
    first_transport = RecordingTransport(fail_phase=phase)
    facade, _, saved = _batch(desktop_paths, tmp_path, transport=first_transport)
    failed = _run(facade, saved, _preview(facade, saved))
    assert failed.status == "failed"
    records = _records(desktop_paths, saved.batch_id)
    assert len(records) == 1
    old_bytes = records[0].read_bytes()
    cached = json.loads(old_bytes)
    assert cached["crop_review"] == "machine_pass" and cached["human_reviewed"] is False

    transport = RecordingTransport()
    restarted = _facade(desktop_paths, FakeProviderStore(), transport=transport)
    before_preview = {str(path): path.read_bytes() for path in _records(desktop_paths, saved.batch_id)}
    plan = _preview(restarted, saved)
    assert plan["resume"]["completed_shards"] == 1 and plan["resume"]["pending_shards"] == 1
    assert plan["resume"]["reused_page_count"] == 2 and plan["resume"]["send_page_count"] == 1
    assert [page["will_send"] for page in plan["pages"]] == [False, False, True]
    assert all(path.read_bytes() == before_preview[str(path)] for path in _records(desktop_paths, saved.batch_id))
    assert transport.events == []
    progress = []
    completed = _run(restarted, saved, plan, progress_callback=progress.append)
    assert completed.visual_status == "completed", completed.failure_codes
    assert [event[0] for event in transport.events] == ["extract", "review"]
    assert all(event[1].endswith(":0002") for event in transport.events)
    assert records[0].read_bytes() == old_bytes
    assert [row["reused"] for row in progress if row["stage"] == "visual_shard_completed"] == [True, False]
    assert completed.candidate_only is True and completed.central_question_bank_write is False


def test_all_saved_shards_rebuild_after_candidate_persistence_failure_without_borrow(desktop_paths, tmp_path, monkeypatch):
    facade, _, saved = _batch(desktop_paths, tmp_path)
    original = DesktopImportCoordinatorV2._persist_candidate
    def fail(*args, **kwargs):
        raise DesktopImportBridgeError("synthetic_persistence_failed", "合成保存中断", 409)
    monkeypatch.setattr(DesktopImportCoordinatorV2, "_persist_candidate", fail)
    assert _run(facade, saved, _preview(facade, saved)).visual_status == "failed"
    assert len(_records(desktop_paths, saved.batch_id)) == 2
    monkeypatch.setattr(DesktopImportCoordinatorV2, "_persist_candidate", original)
    provider, transport = FakeProviderStore(), RecordingTransport()
    restarted = _facade(desktop_paths, provider, transport=transport)
    plan = _preview(restarted, saved)
    assert plan["resume"]["pending_shards"] == 0 and plan["resume"]["send_page_count"] == 0
    assert "不发送页面或调用模型" in plan["confirmation_text"]
    result = _run(restarted, saved, plan)
    assert result.visual_status == "completed", result.failure_codes
    assert transport.events == [] and provider.borrow_calls == 0


@pytest.mark.parametrize("change", ["append", "delete"])
def test_checkpoint_change_after_preview_blocks_before_credentials(desktop_paths, tmp_path, change):
    facade, _, saved = _batch(desktop_paths, tmp_path, transport=RecordingTransport(fail_phase="extract"))
    _run(facade, saved, _preview(facade, saved))
    provider, transport = FakeProviderStore(), RecordingTransport()
    restarted = _facade(desktop_paths, provider, transport=transport)
    plan = _preview(restarted, saved)
    record = _records(desktop_paths, saved.batch_id)[0]
    if change == "append":
        record.write_bytes(record.read_bytes() + b"\n")
    else:
        record.unlink()
    with pytest.raises(DesktopFacadeError) as caught:
        _run(restarted, saved, plan)
    assert caught.value.code == "visual_checkpoint_stale"
    assert transport.events == [] and provider.borrow_calls == 0


def test_corrupt_checkpoint_cannot_silently_restart_or_claim_review(desktop_paths, tmp_path):
    facade, provider, saved = _batch(desktop_paths, tmp_path, transport=RecordingTransport(fail_phase="extract"))
    _run(facade, saved, _preview(facade, saved))
    record = _records(desktop_paths, saved.batch_id)[0]
    value = json.loads(record.read_text(encoding="utf-8"))
    value["human_reviewed"] = True
    record.write_text(json.dumps(value), encoding="utf-8")
    before = record.read_bytes()
    plan = _preview(facade, saved)
    assert plan["can_confirm"] is False and plan["resume"]["blocked_shards"] == 1
    assert [page["checkpoint_state"] for page in plan["pages"]] == ["blocked", "blocked", "pending"]
    with pytest.raises(DesktopFacadeError) as caught:
        _run(facade, saved, plan)
    assert caught.value.code == "visual_checkpoint_invalid"
    assert record.read_bytes() == before and provider.borrow_calls == 1


def test_new_model_revision_is_disclosed_and_never_reuses_old_fragments(desktop_paths, tmp_path):
    facade, _, saved = _batch(desktop_paths, tmp_path, transport=RecordingTransport(fail_phase="extract"))
    _run(facade, saved, _preview(facade, saved))
    original_record = _records(desktop_paths, saved.batch_id)[0]
    before = original_record.read_bytes()
    transport = RecordingTransport()
    restarted = _facade(desktop_paths, FakeProviderStore(revision="REV-2"), transport=transport)
    plan = _preview(restarted, saved, "REV-2")
    assert plan["resume"]["completed_shards"] == 0 and plan["resume"]["has_other_scopes"]
    assert plan["resume"]["send_page_count"] == 3 and "重新处理" in plan["confirmation_text"]
    assert not transport.events
    assert _run(restarted, saved, plan, "REV-2").visual_status == "completed"
    assert len(transport.events) == 4 and original_record.read_bytes() == before


def test_cancel_after_persisting_first_shard_keeps_it_for_resume(desktop_paths, tmp_path):
    facade, _, saved = _batch(desktop_paths, tmp_path)
    cancelled = False
    def progress(value):
        nonlocal cancelled
        if value["stage"] == "visual_shard_completed":
            cancelled = True
    result = _run(facade, saved, _preview(facade, saved), progress_callback=progress, should_cancel=lambda: cancelled)
    assert result.visual_status == "failed"
    assert len(_records(desktop_paths, saved.batch_id)) == 1
    assert _preview(facade, saved)["resume"]["completed_shards"] == 1


def test_failed_crop_review_never_creates_success_checkpoint(desktop_paths, tmp_path):
    facade, _, saved = _batch(desktop_paths, tmp_path, transport=RecordingTransport(fail_phase="review", fail_shard=1))
    assert _run(facade, saved, _preview(facade, saved)).visual_status == "failed"
    assert _records(desktop_paths, saved.batch_id) == []
    assert _preview(facade, saved)["resume"]["completed_shards"] == 0


def test_visual_run_is_excluded_by_existing_batch_lock(desktop_paths, tmp_path):
    transport = RecordingTransport()
    facade, provider, saved = _batch(desktop_paths, tmp_path, transport=transport)
    plan = _preview(facade, saved)
    with import_batch_lock(facade._visual_import_root, saved.batch_id):
        with pytest.raises(DesktopFacadeError) as caught:
            _run(facade, saved, plan)
    assert caught.value.code == "import_batch_busy"
    assert transport.events == [] and provider.borrow_calls == 0


def test_visual_source_ordinals_match_preview_with_native_only_gap(desktop_paths, tmp_path):
    facade, _, saved = _batch(desktop_paths, tmp_path, transport=RecordingTransport(fail_phase="extract"), handout_gap=True)
    assert _run(facade, saved, _preview(facade, saved)).visual_status == "failed"
    assert len(_records(desktop_paths, saved.batch_id)) == 1
    transport = RecordingTransport()
    restarted = _facade(desktop_paths, FakeProviderStore(), transport=transport)
    plan = _preview(restarted, saved)
    assert plan["resume"]["reused_page_count"] == 2
    assert _run(restarted, saved, plan).visual_status == "completed"
    assert all(event[1].endswith(":0002") for event in transport.events)


def test_complete_batch_reader_uses_its_own_bound(desktop_paths, tmp_path, monkeypatch):
    facade, _, saved = _batch(desktop_paths, tmp_path)
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_facade._VISUAL_FAILURE_MANIFEST_MAX_BYTES", 100)
    assert facade.import_batch_details(saved.batch_id).batch_id == saved.batch_id
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_facade._VISUAL_BATCH_MANIFEST_MAX_BYTES", 100)
    with pytest.raises(DesktopFacadeError) as caught:
        facade.import_batch_details(saved.batch_id)
    assert caught.value.code == "visual_import_state_invalid"


@pytest.mark.parametrize("field", ["crop_review_version", "observation_prompt_version", "max_output_tokens"])
def test_changed_review_or_request_policy_does_not_reuse_saved_work(desktop_paths, tmp_path, monkeypatch, field):
    from integrations.deeptutor_shchem_v1 import desktop_visual_egress as egress
    facade, _, saved = _batch(desktop_paths, tmp_path, transport=RecordingTransport(fail_phase="extract"))
    _run(facade, saved, _preview(facade, saved))
    before = {str(path): path.read_bytes() for path in _records(desktop_paths, saved.batch_id)}
    original = egress._request_policy
    def changed(profile):
        policy = original(profile)
        policy[field] = policy[field] + 1 if isinstance(policy[field], int) else "synthetic-changed-contract"
        return policy
    monkeypatch.setattr(egress, "_request_policy", changed)
    plan = _preview(facade, saved)
    assert plan["resume"]["completed_shards"] == 0 and plan["resume"]["has_other_scopes"]
    assert all(page["will_send"] for page in plan["pages"])
    assert all(path.read_bytes() == before[str(path)] for path in _records(desktop_paths, saved.batch_id))


def test_changed_rendered_pixels_are_not_reused(desktop_paths, tmp_path):
    from integrations.deeptutor_shchem_v1.intake_batches_v2 import RenderedPixelPage
    facade, _, saved = _batch(desktop_paths, tmp_path, transport=RecordingTransport(fail_phase="extract"), handout_gap=True)
    _run(facade, saved, _preview(facade, saved))
    class ChangedRenderer:
        def render(self, source_file, *, source_role):
            return (RenderedPixelPage(pixels=_png("yellow"), mime_type="image/png", width=24, height=32, render_recipe_sha256="c" * 64),)
    facade._visual_import_renderer = ChangedRenderer()
    plan = _preview(facade, saved)
    assert plan["resume"]["completed_shards"] == 0 and plan["resume"]["has_other_scopes"]
    assert len(_records(desktop_paths, saved.batch_id)) == 1


def test_checkpoint_disk_failure_stops_before_next_shard(desktop_paths, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_visual_checkpoints as checkpoint
    transport = RecordingTransport()
    facade, _, saved = _batch(desktop_paths, tmp_path, transport=transport)
    original = checkpoint.os.replace
    def fail_shard(source, destination):
        if destination.name.startswith("shard-"):
            raise OSError("synthetic disk failure")
        return original(source, destination)
    monkeypatch.setattr(checkpoint.os, "replace", fail_shard)
    result = _run(facade, saved, _preview(facade, saved))
    assert result.visual_status == "failed"
    assert result.failure_codes == ("visual_checkpoint_write_failed",)
    assert "剩余空间" in result.message_zh
    assert len(transport.events) == 2 and all(event[1].endswith(":0001") for event in transport.events)
    assert _records(desktop_paths, saved.batch_id) == []
    assert _preview(facade, saved)["resume"]["completed_shards"] == 0


def test_success_checkpoints_store_no_provider_credentials(desktop_paths, tmp_path):
    facade, _, saved = _batch(desktop_paths, tmp_path)
    assert _run(facade, saved, _preview(facade, saved)).visual_status == "completed"
    folder = desktop_paths.state_root / "visual-import-v2/shard-checkpoints" / saved.batch_id
    for path in folder.rglob("*.json"):
        raw = path.read_text(encoding="utf-8")
        assert "fixture-secret" not in raw and '"api_key"' not in raw
