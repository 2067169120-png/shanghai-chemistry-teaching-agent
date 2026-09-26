"""Continuation queues use synthetic files and never mutate imported state."""

import csv
import gzip
import hashlib
import json
import sqlite3
from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1 import local_import_continuation as continuation
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import _digest
from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import (
    CACHE_REVISION,
    WordPreviewCache,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    WordQuestionAttributeStore,
    _seal,
    suggest_attributes,
)
from integrations.deeptutor_shchem_v1.word_native_text import NATIVE_WORD_TEXT_REVISION


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(continuation.json_bytes(value))


@pytest.fixture
def local_inputs(tmp_path):
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    source = (
        workspace
        / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-001/synthetic.docx"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"PK synthetic original archive, read without conversion")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    inventory = workspace / continuation.INVENTORY
    with inventory.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "package_id",
                "document_role",
                "output_relative_path",
                "bytes",
                "sha256",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "package_id": "PKG-001",
                "document_role": "解析版",
                "output_relative_path": "expanded/PKG-001/synthetic.docx",
                "bytes": source.stat().st_size,
                "sha256": digest,
            }
        )
    texts = [
        "【例1】请填空___。PRIVATE_QUESTION_ONE",
        "【答案】PRIVATE_ANSWER_ONE",
        "【例2】请根据本题附图填空___。PRIVATE_QUESTION_TWO",
        "【答案】PRIVATE_ANSWER_TWO",
        "【例3】请填空___。PRIVATE_TEACHER_QUESTION",
        "【答案】PRIVATE_ANSWER_THREE",
        "【例4】根据前题回答问题。PRIVATE_DEPENDENT_QUESTION",
        "【答案】PRIVATE_ANSWER_FOUR",
    ]
    preview = {
        "source_name": source.name,
        "source_sha256": digest,
        "extraction_revision": NATIVE_WORD_TEXT_REVISION,
        "blocks": [
            {"index": i, "text": text, "warnings": []}
            for i, text in enumerate(texts, 1)
        ],
        "assets": [
            {"asset_id": "synthetic-image", "block_index": 3, "preview_supported": True}
        ],
        "sections": [],
        "warnings": [],
    }
    preview["revision"] = _digest(preview)
    cache = WordPreviewCache(state / "word-question-previews")
    cache_path = cache._path(digest, source.name)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(
        gzip.compress(
            continuation.json_bytes(
                {"cache_revision": CACHE_REVISION, "preview": preview}
            ),
            mtime=0,
        )
    )
    descriptor = {
        "batch_id": "SYNTHETIC-BATCH",
        "central_question_bank_write": False,
        "plan": {
            "sources": [
                {
                    "source_sha256": digest,
                    "source_file_id": "synthetic-source",
                    "filename": source.name,
                    "size_bytes": source.stat().st_size,
                }
            ]
        },
    }
    dump(state / "visual-import-v2/batches/SYNTHETIC-BATCH.json", descriptor)
    dump(
        state / "desktop-state.v1.json",
        {"schema_version": "shchem.desktop-state.v1", "drafts": {}},
    )
    items = continuation.question_index.index_word_questions(preview)
    assert len(items) == 4
    rows = [suggest_attributes(item, {}, []) for item in items]
    rows[2] = _seal({**rows[2], "annotation_source": "teacher_modified"})
    surplus = deepcopy(items[0])
    surplus.update(key="synthetic-surplus", source_sha256="b" * 64)
    rows.append(suggest_attributes(surplus, {}, []))
    WordQuestionAttributeStore(state).save_many(rows)
    return {
        "workspace": workspace,
        "state": state,
        "source": source,
        "cache": cache_path,
        "preview": preview,
        "items": items,
        "inventory": inventory,
    }


def run(inputs, name="one"):
    output = inputs["workspace"] / "outputs" / name
    result = continuation.write_continuation(
        inputs["workspace"], inputs["state"], output, expected_sources=1
    )
    return output, result


def test_source_bound_queue_deterministic_private_and_read_only(
    local_inputs, monkeypatch
):
    connections = []
    connect = sqlite3.connect

    def tracked(*args, **kwargs):
        connections.append((args, kwargs))
        return connect(*args, **kwargs)

    monkeypatch.setattr(continuation.sqlite3, "connect", tracked)
    one, summary = run(local_inputs)
    two, repeated = run(local_inputs, "two")
    assert summary == repeated
    assert {p.name: p.read_bytes() for p in one.iterdir()} == {
        p.name: p.read_bytes() for p in two.iterdir()
    }
    assert summary["current_questions"] == 4
    assert summary["surplus_attribute_rows_not_counted_as_questions"] == 1
    assert summary["category_counts"] == {name: 1 for name in continuation.CATEGORIES}
    assert summary["labels_written"] == summary["provider_calls"] == 0
    assert summary["pending_tasks"] == 4
    assert all(args[0] == ":memory:" for args, _ in connections)
    audit = json.loads((one / "input_hash_audit.json").read_bytes())
    assert audit["all_unchanged"] and audit["changed_inputs"] == 0
    raw = b"".join(path.read_bytes() for path in one.iterdir())
    assert b"PRIVATE_" not in raw
    assert b"question_blocks" not in raw and b"answer_blocks" not in raw
    queue = [
        json.loads(line)
        for line in (one / "review_queue.jsonl").read_bytes().splitlines()
    ]
    assert all(
        task["source_sha256"] == local_inputs["preview"]["source_sha256"]
        for task in queue
    )
    assert all(
        task["question_revision"] and task["attribute_revision"] for task in queue
    )


@pytest.mark.parametrize(
    "damage", ["source", "cache", "range", "database", "duplicate_inventory"]
)
def test_invalid_or_missing_input_has_no_success_output(local_inputs, damage):
    state = local_inputs["state"]
    if damage == "source":
        local_inputs["source"].write_bytes(b"changed source")
    elif damage == "cache":
        local_inputs["cache"].write_bytes(b"not a gzip preview")
    elif damage == "database":
        (state / "word-question-attributes.sqlite3").unlink()
    elif damage == "range":
        dump(
            state / "desktop-state.v1.json",
            {
                "schema_version": "shchem.desktop-state.v1",
                "drafts": {
                    continuation.RANGES_DRAFT: {
                        "ranges": {
                            local_inputs["items"][0]["key"]: {
                                "source_revision": "stale",
                                "range": {},
                            }
                        }
                    }
                },
            },
        )
    else:
        path = local_inputs["inventory"]
        with path.open("a", encoding="utf-8") as stream:
            stream.write(path.read_text("utf-8-sig").splitlines()[-1] + "\n")
    with pytest.raises((continuation.ContinuationError, OSError)):
        run(local_inputs)
    assert not (local_inputs["workspace"] / "outputs/one/manifest.json").exists()


def test_stale_labels_are_queued_not_counted_as_current(local_inputs):
    store = WordQuestionAttributeStore(local_inputs["state"])
    old = store.get(local_inputs["items"][0]["key"])
    stale = _seal({**old, "question_revision": "c" * 64})
    stale = store.save_many([stale])[0]
    output, summary = run(local_inputs)
    assert summary["attribute_binding_counts"] == {"current": 3, "stale": 1}
    queue = [
        json.loads(line)
        for line in (output / "review_queue.jsonl").read_bytes().splitlines()
    ]
    task = next(row for row in queue if row["question_key"] == stale["key"])
    assert task["category"] == "scope_or_material_incomplete"
    assert task["attribute_binding_status"] == "stale"
    assert store.get(stale["key"]) == stale


def test_input_change_after_materialization_prevents_success_manifest(
    local_inputs, monkeypatch
):
    verify = continuation.InputAudit.verify
    calls = 0

    def changed_on_second(audit):
        nonlocal calls
        calls += 1
        if calls == 2:
            local_inputs["source"].write_bytes(b"changed concurrently")
        return verify(audit)

    monkeypatch.setattr(continuation.InputAudit, "verify", changed_on_second)
    with pytest.raises(
        continuation.ContinuationError, match="input_changed_during_run"
    ):
        run(local_inputs)
    assert not (local_inputs["workspace"] / "outputs/one/manifest.json").exists()


def test_output_cannot_replace_inputs_or_prior_report(local_inputs):
    one, _ = run(local_inputs)
    with pytest.raises(continuation.ContinuationError, match="output_already_exists"):
        run(local_inputs)
    with pytest.raises(
        continuation.ContinuationError, match="output_must_be_under_workspace_outputs"
    ):
        continuation.write_continuation(
            local_inputs["workspace"],
            local_inputs["state"],
            local_inputs["state"] / "new",
            expected_sources=1,
        )
    assert (one / "manifest.json").is_file()


@pytest.mark.parametrize("keep_writer_open", [True, False])
def test_wal_database_is_rejected_without_touching_any_source_bytes(
    local_inputs, keep_writer_open
):
    path = local_inputs["state"] / "word-question-attributes.sqlite3"
    writer = sqlite3.connect(path)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("CREATE TABLE synthetic_wal_marker (value INTEGER)")
    writer.commit()
    if not keep_writer_open:
        writer.close()
    paths = [
        path,
        *[
            path.with_name(path.name + suffix)
            for suffix in ("-wal", "-shm", "-journal")
        ],
    ]
    before = {str(p): continuation.InputAudit.fingerprint(p) for p in paths}
    try:
        with pytest.raises(
            continuation.ContinuationError, match="sqlite_.*requires?_offline_snapshot"
        ):
            run(local_inputs)
        assert {str(p): continuation.InputAudit.fingerprint(p) for p in paths} == before
        assert not (local_inputs["workspace"] / "outputs/one").exists()
    finally:
        if keep_writer_open:
            writer.close()
