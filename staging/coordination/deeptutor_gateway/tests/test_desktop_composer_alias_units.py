from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
    PaperComposerModel,
    _theme_from_group,
)
from integrations.deeptutor_shchem_v1.paper_export_alias_projection import (
    PaperExportAliasProjectionError,
    project_explicit_alias_units,
)
from staging.coordination.deeptutor_gateway.tests.test_paper_export_alias_projection import (
    catalog,
    parent,
    selection,
    unit,
)


def test_composer_and_export_use_same_split_units_and_scores():
    parts = [unit("WAVE-1", 1), unit("WAVE-2", 2, "WAVE-1")]
    parts[0]["score"] = parts[1]["score"] = 2
    source = catalog([parent(aliases=parts)])
    original = deepcopy(source)
    group = source["papers"][0]["theme_groups"][0]
    theme = _theme_from_group(group, "fallback", scope="master")
    export = project_explicit_alias_units(source, [selection()], scope="master")
    rows = export.catalog["papers"][0]["theme_groups"][0]["atomic_chain"]
    assert [q.key for q in theme.questions] == [row["atomic_part_id"] for row in rows]
    assert len(theme.questions) == 2
    assert [q.source_question_number for q in theme.questions] == ["7", "7"]
    assert [q.source_ref["source_atomic_part_id"] for q in theme.questions] == [
        "WAVE-1", "WAVE-2",
    ]
    assert all(q.source_ref["master_parent_atomic_id"] == "MASTER-A" for q in theme.questions)
    assert "WAVE-1要求" in theme.questions[0].stem
    assert "WAVE-2要求" in theme.questions[1].stem
    model = PaperComposerModel(themes=[theme])
    assert model.total_questions == 2
    assert sum(q.score for q in theme.questions) == 4
    assert source == original


def test_composer_does_not_guess_unknown_alias_order():
    part = unit("WAVE-1", 1)
    part["atomic_sequence_in_printed"] = None
    group = catalog([parent(aliases=[part])])["papers"][0]["theme_groups"][0]
    with pytest.raises(PaperExportAliasProjectionError) as error:
        _theme_from_group(group, "fallback", scope="master")
    assert error.value.code == "paper_export_alias_order_unknown"


def test_plain_theme_keeps_existing_unknown_metadata_behavior():
    group = catalog([parent()])["papers"][0]["theme_groups"][0]
    group["atomic_chain"][0]["dependency"] = None
    theme = _theme_from_group(group, "fallback", scope="master")
    assert len(theme.questions) == 1
    assert theme.questions[0].key == "MASTER-A"
