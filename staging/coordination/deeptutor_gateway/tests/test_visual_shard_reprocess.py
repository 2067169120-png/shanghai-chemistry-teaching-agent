"""Explicit selected reruns with real desktop adapters and synthetic transport."""
import json
from pathlib import Path

import pytest

from test_visual_shard_resume import (
    RecordingTransport, _batch, _preview, _records, _run,
    FakeProviderStore, _facade, desktop_paths,
)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_import_recovery import import_batch_lock
from integrations.deeptutor_shchem_v1.desktop_visual_import_v2 import DesktopImportBridgeError, DesktopImportCoordinatorV2
from integrations.deeptutor_shchem_v1.visual_provider_runtime import parse_structured_visual_response


def _saved_shards(paths, tmp_path, monkeypatch):
    facade, _, saved = _batch(paths, tmp_path)
    def interrupted(*args, **kwargs):
        raise DesktopImportBridgeError("synthetic_persistence_failed", "合成落盘中断", 409)
    with monkeypatch.context() as context:
        context.setattr(DesktopImportCoordinatorV2, "_persist_candidate", interrupted)
        assert _run(facade, saved, _preview(facade, saved)).visual_status == "failed"
    assert len(_records(paths, saved.batch_id)) == 2
    provider, transport = FakeProviderStore(), RecordingTransport()
    return _facade(paths, provider, transport=transport), provider, transport, saved


def _revise(facade, plan, ids):
    return facade.revise_visual_import_egress(plan["preview_id"], plan["revision"], ids)


def _all_bytes(paths, saved):
    root = paths.state_root / "visual-import-v2/shard-checkpoints" / saved.batch_id
    return {path: path.read_bytes() for path in root.rglob("*.json")}


def test_selected_group_reprocesses_only_its_pages_and_retains_both_originals(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved = _saved_shards(desktop_paths, tmp_path, monkeypatch)
    old = _all_bytes(desktop_paths, saved)
    plan = _preview(facade, saved)
    revised = _revise(facade, plan, [plan["shards"][1]["shard_id"]])
    assert revised["revision"] != plan["revision"]
    assert revised["resume"]["send_page_count"] == 1 and revised["resume"]["reused_page_count"] == 2
    assert revised["resume"]["reprocess_shards"] == 1
    assert [p["will_send"] for p in revised["pages"]] == [False, False, True]
    assert "旧记录保留" in revised["confirmation_text"] and "再次" in revised["confirmation_text"]
    assert _all_bytes(desktop_paths, saved) == old and provider.borrow_calls == 0
    with pytest.raises(DesktopFacadeError, match="预览"):
        _run(facade, saved, plan)
    assert _run(facade, saved, revised).visual_status == "completed"
    assert [phase for phase, _ in transport.events] == ["extract", "review"]
    assert all(shard.endswith(":0002") for _, shard in transport.events)
    assert all(path.read_bytes() == raw for path, raw in old.items())
    current = _all_bytes(desktop_paths, saved)
    assert len([p for p in current if p.name == "previous-state.json"]) == 1
    assert len([p for p in current if p.name.startswith("shard-")]) == 3
    assert all(b"fixture-secret" not in raw for raw in current.values())


@pytest.mark.parametrize("damage", ["json", "review_flag"])
def test_corrupt_record_needs_explicit_selection_and_is_retained(desktop_paths, tmp_path, monkeypatch, damage):
    facade, provider, transport, saved = _saved_shards(desktop_paths, tmp_path, monkeypatch)
    record = _records(desktop_paths, saved.batch_id)[0]
    if damage == "json":
        record.write_bytes(b'{"partial":')
    else:
        value = json.loads(record.read_bytes())
        value["human_reviewed"] = True
        record.write_text(json.dumps(value), encoding="utf-8")
    before = _all_bytes(desktop_paths, saved)
    plan = _preview(facade, saved)
    assert not plan["can_confirm"]
    assert plan["resume"]["blocked_page_count"] == 2 and plan["resume"]["reused_page_count"] == 1
    assert plan["resume"]["send_page_count"] == 0
    with pytest.raises(DesktopFacadeError) as caught:
        _run(facade, saved, plan)
    assert caught.value.code == "visual_checkpoint_invalid"
    assert provider.borrow_calls == 0 and not transport.events
    repaired = _revise(facade, plan, [plan["shards"][0]["shard_id"]])
    assert repaired["can_confirm"] and repaired["resume"]["send_page_count"] == 2
    assert _all_bytes(desktop_paths, saved) == before
    assert _run(facade, saved, repaired).visual_status == "completed"
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert len(transport.events) == 2 and all(s.endswith(":0001") for _, s in transport.events)


@pytest.mark.parametrize("change", ["append", "delete", "source"])
def test_changed_record_or_source_blocks_selected_run_before_credentials(desktop_paths, tmp_path, monkeypatch, change):
    facade, provider, transport, saved = _saved_shards(desktop_paths, tmp_path, monkeypatch)
    plan = _preview(facade, saved)
    revised = _revise(facade, plan, [plan["shards"][0]["shard_id"]])
    record = _records(desktop_paths, saved.batch_id)[0]
    if change == "append":
        record.write_bytes(record.read_bytes() + b"\n")
    elif change == "delete":
        record.unlink()
    else:
        descriptor = facade._saved_visual_import_batch(saved.batch_id)
        sources = facade._restore_visual_import_sources(descriptor)
        path = facade._visual_import_root / "sources" / f"{sources[0].source_sha256}.png"
        path.write_bytes(path.read_bytes() + b"changed")
    before = _all_bytes(desktop_paths, saved)
    with pytest.raises(DesktopFacadeError):
        _run(facade, saved, revised)
    assert provider.borrow_calls == 0 and not transport.events
    assert _all_bytes(desktop_paths, saved) == before


def test_cancel_or_refuse_confirmation_never_activates_selection(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved = _saved_shards(desktop_paths, tmp_path, monkeypatch)
    plan = _preview(facade, saved)
    revised = _revise(facade, plan, [plan["shards"][0]["shard_id"]])
    before = _all_bytes(desktop_paths, saved)
    with pytest.raises(DesktopFacadeError) as caught:
        facade.run_saved_visual_import_batch(batch_id=saved.batch_id, profile_id="vision",
            expected_profile_revision="REV-1", teacher_confirmed=False,
            egress_preview_id=revised["preview_id"], egress_revision=revised["revision"])
    assert caught.value.code == "teacher_confirmation_required"
    with pytest.raises(DesktopFacadeError) as caught:
        _run(facade, saved, revised, should_cancel=lambda: True)
    assert caught.value.code == "cancelled"
    assert facade.discard_visual_import_egress(revised["preview_id"])
    assert _all_bytes(desktop_paths, saved) == before and provider.borrow_calls == 0 and not transport.events


@pytest.mark.parametrize("selection", ["unknown", "duplicate", "pending", "wrong_type"])
def test_invalid_selection_is_rejected_without_writes(desktop_paths, tmp_path, selection):
    facade, provider, saved = _batch(desktop_paths, tmp_path, transport=RecordingTransport(fail_phase="extract"))
    _run(facade, saved, _preview(facade, saved))
    plan = _preview(facade, saved)
    first, second = [s["shard_id"] for s in plan["shards"]]
    values = {"unknown": ["other-batch"], "duplicate": [first, first], "pending": [second], "wrong_type": first}
    before = _all_bytes(desktop_paths, saved)
    with pytest.raises(DesktopFacadeError) as caught:
        _revise(facade, plan, values[selection])
    assert caught.value.code == "visual_checkpoint_selection_invalid"
    assert _all_bytes(desktop_paths, saved) == before and provider.borrow_calls == 1


def test_failed_atomic_activation_keeps_old_selection_and_all_saved_results(desktop_paths, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_visual_checkpoints as checkpoint
    facade, provider, transport, saved = _saved_shards(desktop_paths, tmp_path, monkeypatch)
    before = _all_bytes(desktop_paths, saved)
    plan = _preview(facade, saved)
    revised = _revise(facade, plan, [plan["shards"][0]["shard_id"]])
    replace = checkpoint.os.replace
    def disk_full(src, dst):
        if dst.name == "active-attempts.json":
            raise OSError("synthetic disk full")
        return replace(src, dst)
    with monkeypatch.context() as context:
        context.setattr(checkpoint.os, "replace", disk_full)
        with pytest.raises(DesktopFacadeError) as caught:
            _run(facade, saved, revised)
    assert caught.value.code == "visual_checkpoint_write_failed"
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert provider.borrow_calls == 0 and not transport.events
    assert _preview(facade, saved)["resume"]["send_page_count"] == 0


def test_activation_survives_restart_without_reusing_replaced_result(desktop_paths, tmp_path, monkeypatch):
    facade, provider, _, saved = _saved_shards(desktop_paths, tmp_path, monkeypatch)
    old = _all_bytes(desktop_paths, saved)
    plan = _preview(facade, saved)
    revised = _revise(facade, plan, [plan["shards"][0]["shard_id"]])
    def fail_borrow(*args, **kwargs):
        raise RuntimeError("synthetic interruption before provider call")
    monkeypatch.setattr(provider, "borrow_invocation_context", fail_borrow)
    with pytest.raises(DesktopFacadeError):
        _run(facade, saved, revised)
    restarted = _facade(desktop_paths, FakeProviderStore(), transport=RecordingTransport())
    fresh = _preview(restarted, saved)
    assert fresh["resume"]["send_page_count"] == 2 and fresh["resume"]["reused_page_count"] == 1
    assert _run(restarted, saved, fresh).visual_status == "completed"
    assert all(path.read_bytes() == raw for path, raw in old.items())
    with pytest.raises(DesktopFacadeError):
        _revise(restarted, fresh, [fresh["shards"][1]["shard_id"]])


def test_reprocess_obeys_existing_batch_lock(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved = _saved_shards(desktop_paths, tmp_path, monkeypatch)
    plan = _preview(facade, saved)
    revised = _revise(facade, plan, [plan["shards"][0]["shard_id"]])
    before = _all_bytes(desktop_paths, saved)
    with import_batch_lock(facade._visual_import_root, saved.batch_id):
        with pytest.raises(DesktopFacadeError) as caught:
            _run(facade, saved, revised)
    assert caught.value.code == "import_batch_busy"
    assert _all_bytes(desktop_paths, saved) == before and provider.borrow_calls == 0 and not transport.events


def test_cross_shard_evidence_conflict_can_be_repaired_by_explicit_second_group_rerun(desktop_paths, tmp_path):
    class ConflictingTransport(RecordingTransport):
        first_id = None

        def send(self, request, **kwargs):
            response = super().send(request, **kwargs)
            if self.events[-1][0] == "extract":
                fragment, _usage = parse_structured_visual_response(request.api_style, response.body)
                current_id = fragment["evidence"][0]["evidence_id"]
                if self.first_id is None:
                    self.first_id = current_id
                else:
                    fragment = json.loads(json.dumps(fragment).replace(json.dumps(current_id), json.dumps(self.first_id)))
                    return self._response(fragment)
            return response

    facade, _, saved = _batch(desktop_paths, tmp_path, transport=ConflictingTransport())
    failed = _run(facade, saved, _preview(facade, saved))
    assert failed.visual_status == "failed" and "provider_output_evidence_conflict" in failed.failure_codes
    assert len(_records(desktop_paths, saved.batch_id)) == 2
    original = _all_bytes(desktop_paths, saved)
    provider, transport = FakeProviderStore(), RecordingTransport()
    restarted = _facade(desktop_paths, provider, transport=transport)
    plan = _preview(restarted, saved)
    assert plan["resume"]["send_page_count"] == 0
    # Reusing the same individually valid records cannot repair their joint conflict.
    assert _run(restarted, saved, plan).visual_status == "failed"
    assert provider.borrow_calls == 0 and not transport.events
    plan = _preview(restarted, saved)
    revised = _revise(restarted, plan, [plan["shards"][1]["shard_id"]])
    assert _run(restarted, saved, revised).visual_status == "completed"
    assert [phase for phase, _ in transport.events] == ["extract", "review"]
    assert all(shard.endswith(":0002") for _, shard in transport.events)
    assert all(path.read_bytes() == raw for path, raw in original.items())


@pytest.mark.parametrize("level", ["root", "batch", "scope"])
def test_junction_checkpoint_directories_block_before_provider(desktop_paths, tmp_path, monkeypatch, level):
    facade, provider, transport, saved = _saved_shards(desktop_paths, tmp_path, monkeypatch)
    record = _records(desktop_paths, saved.batch_id)[0]
    blocked = {"scope": record.parent, "batch": record.parent.parent, "root": record.parent.parent.parent}[level]
    original = getattr(Path, "is_junction", lambda path: False)
    with monkeypatch.context() as context:
        context.setattr(Path, "is_junction", lambda path: path == blocked or original(path), raising=False)
        with pytest.raises(DesktopFacadeError) as caught:
            _preview(facade, saved)
    assert caught.value.code == "visual_checkpoint_invalid"
    assert provider.borrow_calls == 0 and not transport.events
