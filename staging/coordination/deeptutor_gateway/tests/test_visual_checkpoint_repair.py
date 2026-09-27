"""Offline directory repair through real facade boundaries; synthetic inputs only."""
import json
from pathlib import Path

import pytest

from test_visual_shard_reprocess import _saved_shards
from test_visual_shard_resume import _preview, _records, _run, desktop_paths
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_import_recovery import import_batch_lock
from integrations.deeptutor_shchem_v1.desktop_visual_checkpoints import _sha


def _bytes(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def _broken(paths, tmp_path, monkeypatch, name="scope.json", raw=b'{"interrupted":'):
    facade, provider, transport, saved = _saved_shards(paths, tmp_path, monkeypatch)
    directory = _records(paths, saved.batch_id)[0].parent
    if raw is None:
        (directory / name).unlink(missing_ok=True)
    else:
        (directory / name).write_bytes(raw)
    return facade, provider, transport, saved, directory


def _choose(facade, plan, indexes=None):
    options = plan["repair"]["options"]
    selected = [options[i]["option_id"] for i in (range(len(options)) if indexes is None else indexes)]
    return facade.revise_visual_import_repair(plan["preview_id"], plan["revision"], selected)


def _repair(facade, saved, plan, **kwargs):
    return facade.repair_visual_import_egress(batch_id=saved.batch_id,
        preview_id=plan["preview_id"], revision=plan["revision"], teacher_confirmed=kwargs.pop("teacher_confirmed", True), **kwargs)


@pytest.mark.parametrize("name,raw", [("scope.json", None), ("scope.json", b'{"bad":'), ("active-attempts.json", b'{"bad":')])
def test_bad_index_is_readonly_until_confirmed_then_returns_offline_ordinary_preview(desktop_paths, tmp_path, monkeypatch, name, raw):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch, name, raw)
    before = _bytes(directory)
    plan = _preview(facade, saved)
    assert not plan["can_confirm"] and len(plan["repair"]["options"]) == 2
    assert not any(p["will_send"] for p in plan["pages"])
    revised = _choose(facade, plan)
    assert revised["can_confirm"] and revised["repair"]["selected_shards"] == 2
    assert _bytes(directory) == before and provider.borrow_calls == 0
    with pytest.raises(DesktopFacadeError) as exc:
        _run(facade, saved, revised)
    assert exc.value.code == "visual_checkpoint_repair_required"
    normal = _repair(facade, saved, revised)
    assert "repair" not in normal and normal["resume"]["send_page_count"] == 0
    assert provider.borrow_calls == 0 and not transport.events
    assert not (directory / "repair-pending.json").exists()
    backups = list(directory.glob("repairs/*/" + name + ".before"))
    assert (backups[0].read_bytes() == raw) if raw is not None else not backups
    for path, data in before.items():
        if path.startswith("shard-"):
            assert (directory / path).read_bytes() == data
    assert _run(facade, saved, normal).visual_status == "completed"
    assert provider.borrow_calls == 0 and not transport.events


def test_subset_does_not_reuse_unselected_records_or_send_without_second_confirmation(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    revised = _choose(facade, _preview(facade, saved), [1])
    normal = _repair(facade, saved, revised)
    assert normal["resume"]["reused_page_count"] == 1 and normal["resume"]["send_page_count"] == 2
    assert not transport.events and provider.borrow_calls == 0
    active = json.loads((directory / "active-attempts.json").read_bytes())["active"]
    assert len(set(active.values())) == 1
    attempt = directory / "attempts" / next(iter(active.values()))
    assert sorted(p.name for p in attempt.iterdir()) == ["shard-0002.json"]
    assert _run(facade, saved, normal).visual_status == "completed"
    assert len(transport.events) == 2 and all(shard.endswith(":0001") for _, shard in transport.events)


def _alternate(directory, *, suffix="a", changed=True):
    path = directory / "attempts" / (suffix * 32) / "shard-0001.json"
    path.parent.mkdir(parents=True)
    record = json.loads((directory / "shard-0001.json").read_bytes())
    if changed:
        record["fragment"]["theme_fragments"][0]["printed_questions"][0]["stem"] = "另一个合成识别版本 <b>不解释为 HTML</b>"
        record["fragment_sha256"] = _sha(record["fragment"])
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def test_missing_active_with_multiple_valid_versions_requires_explicit_one_per_group(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch, "active-attempts.json", None)
    alternate = _alternate(directory)
    before = _bytes(directory)
    plan = _preview(facade, saved)
    options = plan["repair"]["options"]
    first = [o for o in options if o["shard_index"] == 1]
    assert len(first) == 2 and not plan["can_confirm"]
    assert plan["repair"]["selected_option_ids"] == []
    assert any("版本记录缺失" in text for text in plan["repair"]["issues"])
    with pytest.raises(DesktopFacadeError) as exc:
        facade.revise_visual_import_repair(plan["preview_id"], plan["revision"], [o["option_id"] for o in first])
    assert exc.value.code == "visual_checkpoint_repair_selection"
    chosen = next(o for o in first if "另一个" in o["summary"])
    revised = facade.revise_visual_import_repair(plan["preview_id"], plan["revision"], [chosen["option_id"]])
    assert _bytes(directory) == before
    normal = _repair(facade, saved, revised)
    active = json.loads((directory / "active-attempts.json").read_bytes())["active"]
    new_record = directory / "attempts" / active[chosen["shard_id"]] / "shard-0001.json"
    assert new_record.read_bytes() == alternate.read_bytes()
    assert normal["resume"]["reused_page_count"] == 2 and not transport.events and provider.borrow_calls == 0


@pytest.mark.parametrize("change", ["record", "source", "new_version", "profile", "policy", "pixel"])
def test_change_after_repair_preview_blocks_before_any_write(desktop_paths, tmp_path, monkeypatch, change):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    revised = _choose(facade, _preview(facade, saved))
    if change == "record":
        path = directory / "shard-0001.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "new_version":
        _alternate(directory)
    elif change == "source":
        sources = facade._restore_visual_import_sources(facade._saved_visual_import_batch(saved.batch_id))
        path = facade._visual_import_root / "sources" / f"{sources[0].source_sha256}.png"
        path.write_bytes(path.read_bytes() + b"changed")
    elif change in {"profile", "policy"}:
        original = facade._visual_import_profile
        def updated(*args):
            profile = dict(original(*args))
            if change == "profile":
                profile["model_id"] = "other-model"
            else:
                profile["max_output_tokens"] = 9999
            return profile
        monkeypatch.setattr(facade, "_visual_import_profile", updated)
    else:
        service = facade._visual_egress_service_instance()
        snapshot = service._snapshots[revised["preview_id"]]
        object.__setattr__(snapshot.pages[0], "pixels", b"changed")
    before = _bytes(directory)
    with pytest.raises(DesktopFacadeError):
        _repair(facade, saved, revised)
    assert _bytes(directory) == before and provider.borrow_calls == 0 and not transport.events


@pytest.mark.parametrize("action", ["cancel", "refuse", "discard", "lock"])
def test_cancel_refusal_close_and_concurrent_writer_leave_exact_bytes(desktop_paths, tmp_path, monkeypatch, action):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    revised = _choose(facade, _preview(facade, saved))
    before = _bytes(directory)
    if action == "discard":
        facade.discard_visual_import_egress(revised["preview_id"])
    with pytest.raises(DesktopFacadeError):
        if action == "lock":
            with import_batch_lock(facade._visual_import_root, saved.batch_id):
                _repair(facade, saved, revised)
        else:
            _repair(facade, saved, revised, teacher_confirmed=action != "refuse", should_cancel=lambda: action == "cancel")
    assert _bytes(directory) == before and provider.borrow_calls == 0 and not transport.events


def test_half_commit_keeps_fence_and_original_bytes_then_can_repair_again(desktop_paths, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_visual_checkpoint_repair as module
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    before = _bytes(directory)
    revised = _choose(facade, _preview(facade, saved))
    replace = module.os.replace
    def fail_scope(src, dst):
        if dst == directory / "scope.json":
            raise OSError("synthetic full disk at second index")
        return replace(src, dst)
    with monkeypatch.context() as context:
        context.setattr(module.os, "replace", fail_scope)
        with pytest.raises(DesktopFacadeError) as exc:
            _repair(facade, saved, revised)
    assert exc.value.code == "visual_checkpoint_write_failed"
    assert (directory / "repair-pending.json").exists()
    assert (directory / "scope.json").read_bytes() == before["scope.json"]
    assert list(directory.glob("repairs/*/scope.json.before"))[0].read_bytes() == before["scope.json"]
    fresh = _preview(facade, saved)
    assert "repair" in fresh and any("未完成" in text for text in fresh["repair"]["issues"])
    assert len(fresh["repair"]["options"]) == 2  # Identical copied records do not create ambiguity.
    normal = _repair(facade, saved, _choose(facade, fresh))
    assert "repair" not in normal and not (directory / "repair-pending.json").exists()
    assert provider.borrow_calls == 0 and not transport.events


def test_repeated_old_confirmation_is_idempotently_rejected_without_new_writes(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    revised = _choose(facade, _preview(facade, saved))
    _repair(facade, saved, revised)
    before = _bytes(directory)
    with pytest.raises(DesktopFacadeError):
        _repair(facade, saved, revised)
    assert _bytes(directory) == before and provider.borrow_calls == 0 and not transport.events


@pytest.mark.parametrize("damage", ["duplicate", "record_duplicate", "oversize", "nested", "junction", "record_link", "foreign_scope", "foreign_active"])
def test_unsafe_or_identity_conflicting_metadata_cannot_be_repaired(desktop_paths, tmp_path, monkeypatch, damage):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    if damage == "duplicate":
        (directory / "scope.json").write_bytes(b'{"scope":1,"scope":2}')
    elif damage == "record_duplicate":
        (directory / "shard-0001.json").write_bytes(b'{"fragment":{},"fragment":{}}')
    elif damage == "oversize":
        (directory / "scope.json").write_bytes(b" " * (16 * 1024 * 1024 + 1))
    elif damage == "nested":
        (directory / "attempts" / ("a" * 32) / "nested").mkdir(parents=True)
    elif damage in {"junction", "record_link"}:
        original = getattr(Path, "is_junction", lambda _: False)
        target = directory if damage == "junction" else directory / "shard-0001.json"
        monkeypatch.setattr(Path, "is_junction", lambda p: p == target or original(p), raising=False)
    elif damage == "foreign_scope":
        (directory / "scope.json").write_bytes(b'{"batch_id":"different"}')
    else:
        (directory / "active-attempts.json").write_bytes(b'{"active":{"outside":"../outside"}}')
    before = _bytes(directory)
    with pytest.raises(DesktopFacadeError):
        _preview(facade, saved)
    assert _bytes(directory) == before and provider.borrow_calls == 0 and not transport.events


def test_foreign_shard_record_is_not_a_restore_option(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    path = directory / "shard-0001.json"
    value = json.loads(path.read_bytes())
    value["request_sha256"] = "0" * 64
    path.write_text(json.dumps(value), encoding="utf-8")
    plan = _preview(facade, saved)
    assert len(plan["repair"]["options"]) == 1 and plan["repair"]["unavailable_shards"] == 1
    assert "不一致" in plan["shards"][0]["unavailable_reason"]
    normal = _repair(facade, saved, _choose(facade, plan))
    assert normal["resume"]["reused_page_count"] == 1 and normal["resume"]["send_page_count"] == 2
    assert provider.borrow_calls == 0 and not transport.events


def test_final_cas_catches_record_change_during_staging_before_indexes_take_effect(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    revised = _choose(facade, _preview(facade, saved))
    old_scope = (directory / "scope.json").read_bytes()
    service = facade._visual_egress_service_instance()
    original, calls = service._revalidate_repair, []
    def change_at_commit(snapshot):
        original(snapshot)
        calls.append(True)
        if len(calls) == 3:
            record = directory / "shard-0001.json"
            record.write_bytes(record.read_bytes() + b"\n")
    monkeypatch.setattr(service, "_revalidate_repair", change_at_commit)
    with pytest.raises(DesktopFacadeError) as exc:
        _repair(facade, saved, revised)
    assert exc.value.code == "visual_checkpoint_stale"
    assert (directory / "scope.json").read_bytes() == old_scope
    assert not (directory / "active-attempts.json").exists() and not (directory / "repair-pending.json").exists()
    assert list(directory.glob("repairs/*/scope.json.before"))[0].read_bytes() == old_scope
    assert provider.borrow_calls == 0 and not transport.events


def test_cancel_at_last_prewrite_boundary_creates_no_backup_or_metadata(desktop_paths, tmp_path, monkeypatch):
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    revised = _choose(facade, _preview(facade, saved))
    before, polls = _bytes(directory), []
    def cancelled():
        polls.append(True)
        return len(polls) == 3
    with pytest.raises(DesktopFacadeError) as exc:
        _repair(facade, saved, revised, should_cancel=cancelled)
    assert exc.value.code == "visual_checkpoint_repair_cancelled"
    assert len(polls) == 3 and _bytes(directory) == before
    assert provider.borrow_calls == 0 and not transport.events


@pytest.mark.parametrize("limit", ["MAX_REPAIR_FILES", "MAX_REPAIR_BYTES"])
def test_repair_inventory_budget_is_enforced_before_writes(desktop_paths, tmp_path, monkeypatch, limit):
    from integrations.deeptutor_shchem_v1 import desktop_visual_checkpoint_repair as module
    facade, provider, transport, saved, directory = _broken(desktop_paths, tmp_path, monkeypatch)
    before = _bytes(directory)
    monkeypatch.setattr(module, limit, 4)
    with pytest.raises(DesktopFacadeError) as exc:
        _preview(facade, saved)
    assert exc.value.code == "visual_checkpoint_repair_unsafe"
    assert _bytes(directory) == before and provider.borrow_calls == 0 and not transport.events
