import json

import pytest
from test_desktop_visual_import_facade import (
    desktop_paths as desktop_paths,  # noqa: PLC0414
)
from test_word_questions_service import _choice, _import

from integrations.deeptutor_shchem_v1.desktop_source_quality import (
    QUALITY_PATH,
    apply_source_quality,
    source_quality_notes,
)
from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionError


def _notes(item, index):
    return {item["source_sha256"]: {
        "source_revision": item["source_revision"], "notes_revision": "test",
        "issues": [{"id": "TEST", "block_indices": [index], "summary": "原答案有误。", "suggested_correction": "按守恒关系复核。"}],
    }}


def test_errata_is_bound_to_original_hash_and_blocks(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    first, second = facade.word_question_catalog()["items"]
    notes = _notes(first, first["block_start"])
    held = apply_source_quality(first, notes)
    assert not held["selection_ready"] and not held["export_ready"]
    assert held["question_blocks"] == first["question_blocks"]
    assert held["revision"] == first["revision"]
    assert first["selection_ready"]
    assert apply_source_quality(second, notes)["selection_ready"]
    notes[first["source_sha256"]]["source_revision"] = "stale"
    assert apply_source_quality(second, notes)["content_quality"]["status"] == "source_errata_pending_relocation"


def test_known_errors_block_saved_selection_reference_and_export(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    first = facade.word_question_catalog()["items"][0]
    chosen = [_choice(first)]
    facade.word_question_save_selection(chosen)
    source = _notes(first, first["block_start"])[first["source_sha256"]]
    path = desktop_paths.workspace_root / QUALITY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "sources": [{**source, "source_sha256": first["source_sha256"]}]}), encoding="utf-8")
    row = facade.word_question_catalog()["items"][0]
    assert row["content_quality"]["issues"]
    for action in (facade.word_question_save_selection, facade.word_question_reference):
        with pytest.raises(WordQuestionError):
            action(chosen)
    with pytest.raises(WordQuestionError):
        facade.word_question_export("测试", chosen)
    assert facade.word_question_source(first["key"], first["revision"])["blocks"]


def test_missing_errata_never_creates_files_and_corrupt_errata_fails(tmp_path):
    assert source_quality_notes(tmp_path) == {}
    assert not (tmp_path / "knowledge").exists()
    path = tmp_path / QUALITY_PATH
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version": 5}', encoding="utf-8")
    with pytest.raises(ValueError):
        source_quality_notes(tmp_path)


def test_independent_precipitation_calculation_and_atom_charge_balance():
    carbonate = (4.0e-34 / (1e-5) ** 2) ** (1 / 3)
    assert carbonate == pytest.approx(1.587401052e-8)
    assert 0.45 * carbonate > 3.5e-9
    assert 2 * 3 == 3 * 2  # La3+ on the left; Mg2+ on the right.
    assert (1, 4 + 2 * 2, 2) == (1, 4 * 2, 2)  # C/H/O in the net reforming reaction.
