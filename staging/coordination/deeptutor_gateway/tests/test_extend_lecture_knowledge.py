"""Synthetic extension merges must remain readable before touching indexes."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1.desktop_lecture_library import INDEX_FILES, _index_cards


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "extend_lecture_knowledge_test_module", scripts / "extend_lecture_knowledge.py"
    )
    extension = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extension)
    monkeypatch.setattr(extension, "ROOT", tmp_path)

    folder = tmp_path / "knowledge/lectures"
    folder.mkdir(parents=True)
    inventory = tmp_path / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/archive_inventory.csv"
    inventory.parent.mkdir(parents=True)
    pdf = tmp_path / "synthetic-textbook.pdf"
    pdf.write_bytes(b"synthetic textbook bytes, not a real textbook")
    pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    directory = tmp_path / "sh-chem-db/kb/classification/supplemental_wechat_textbook_tagging_v1_2026-08-27/textbook_directory_nodes.json"
    directory.parent.mkdir(parents=True)
    directory.write_text(json.dumps({"nodes": [{
        "node_key": "TB-M1-C1:1.1", "volume_id": "TB-M1",
        "source_path": pdf.name, "source_sha256": pdf_sha,
        "content_pdf_pages": [3, 4],
    }]}), encoding="utf-8")
    rows, entries, sources = [], [], []
    for number in (1, 2):
        name = f"synthetic-{number}.docx"
        raw = f"synthetic source {number}".encode()
        (inventory.parent / name).write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        package = f"PKG-{number:03d}"
        sources.append({"package_id": package, "document_role": "解析版",
                        "output_relative_path": name, "sha256": digest})
        row = {
            "id": f"LECT-{package}", "package_id": package,
            "title": f"合成讲义{number}", "source_name": name, "source_sha256": digest,
            "human_reviewed": False, "review_status": "ai_distilled_pending_teacher_review",
            "source_preview_revision": "a" * 64,
            "knowledge": [{"summary": "已有合成知识", "block_indices": [1]}],
            "methods": [], "pitfalls": [],
        }
        rows.append(row)
        entries.append({
            "package_id": package, "source_sha256": digest,
            "knowledge": [{"summary": f"新增合成知识{number}", "block_indices": [2]}],
            "textbook_links": [{
                "volume_id": "TB-M1", "section_key": "TB-M1-C1:1.1",
                "source_sha256": pdf_sha, "pdf_pages": [3], "printed_pages": [1],
                "lecture_block_indices": [2], "review_method": "page_images_read_by_model",
                "human_reviewed": False,
            }],
        })
    with inventory.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(sources[0]))
        writer.writeheader()
        writer.writerows(sources)
    for index, name in enumerate(INDEX_FILES):
        (folder / name).write_text(
            json.dumps(rows[index], ensure_ascii=False) + "\n" if index < len(rows) else "",
            encoding="utf-8",
        )
    (folder / "coverage.json").write_text('{"previous":true}', encoding="utf-8")
    payload = {"schema_version": "shchem.lecture-source-extension.v1",
               "human_reviewed": False, "entries": entries}
    candidates = tmp_path / "extension.json"
    validations = []

    def validate(state_root):
        # Source validation is separately covered; do not access the 46 real
        # source documents from a regression for projected index acceptance.
        validations.append(state_root)
        return {"synthetic_coverage": True}

    class Cache:
        def __init__(self, root):
            pass

        def load(self, raw, name):
            return {"revision": "a" * 64, "blocks": [{"index": 1}, {"index": 2}]}

    monkeypatch.setattr(extension, "validate", validate)
    monkeypatch.setattr(extension, "WordPreviewCache", Cache)
    monkeypatch.setattr(sys, "argv", [str(scripts / "extend_lecture_knowledge.py"),
        "--state-root", str(tmp_path / "state"), "--extensions", str(candidates), "--apply"])
    return SimpleNamespace(root=tmp_path, script=extension, payload=payload,
                           candidates=candidates, validations=validations)


def _snapshot(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("case", ["review_method", "missing_method", "boolean_page", "zero_page", "empty_pages", "blank_claim", "raw_gap"])
def test_invalid_later_extension_is_rejected_before_any_index_or_coverage_write(corpus, case):
    entry = corpus.payload["entries"][1]
    link = entry["textbook_links"][0]
    if case == "review_method":
        link["review_method"] = "page_images_and_source_word_assets_read_by_model"
    elif case == "missing_method":
        link.pop("review_method")
    elif case == "boolean_page":
        link["printed_pages"] = [True]
    elif case == "zero_page":
        link["printed_pages"] = [0]
    elif case == "empty_pages":
        link["pdf_pages"] = link["printed_pages"] = []
    elif case == "blank_claim":
        entry["knowledge"][0]["summary"] = "   "
    else:
        entry["knowledge"][0]["summary"] = "【待查看原文：公式】"
    corpus.candidates.write_text(json.dumps(corpus.payload), encoding="utf-8")
    before = _snapshot(corpus.root)
    with pytest.raises(ValueError, match="reader validation|missing-object marker"):
        corpus.script.main()
    assert _snapshot(corpus.root) == before
    assert len(corpus.validations) == 1
    cards, warnings = _index_cards(corpus.root)
    assert len(cards) == 2 and not warnings


@pytest.mark.parametrize("apply", [False, True])
def test_valid_merge_uses_reader_contract_and_preview_never_writes(corpus, monkeypatch, capsys, apply):
    corpus.candidates.write_text(json.dumps(corpus.payload), encoding="utf-8")
    before = _snapshot(corpus.root)
    if not apply:
        monkeypatch.setattr(sys, "argv", [arg for arg in sys.argv if arg != "--apply"])
    corpus.script.main()
    result = json.loads(capsys.readouterr().out)
    assert result["new_note_count"] == 2
    assert result["mode"] == ("apply" if apply else "preview")
    cards, warnings = _index_cards(corpus.root)
    assert len(cards) == 2 and not warnings
    if apply:
        assert all(len(row["knowledge"]) == 2 for row in cards.values())
        assert all(row["textbook_links"][0]["review_method"] == "page_images_read_by_model" for row in cards.values())
        assert len(corpus.validations) == 2
    else:
        assert _snapshot(corpus.root) == before


def test_projected_validation_calls_the_existing_reader_without_disk_staging(corpus, monkeypatch):
    expected = deepcopy(corpus.payload)
    calls = []
    reader = corpus.script._index_cards

    def checked(workspace):
        calls.append(workspace)
        assert not isinstance(workspace, Path)
        return reader(workspace)

    monkeypatch.setattr(corpus.script, "_index_cards", checked)
    corpus.candidates.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [arg for arg in sys.argv if arg != "--apply"])
    before = _snapshot(corpus.root)
    corpus.script.main()
    assert len(calls) == 1 and _snapshot(corpus.root) == before
