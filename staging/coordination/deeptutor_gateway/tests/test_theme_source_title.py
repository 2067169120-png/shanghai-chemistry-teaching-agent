"""A selected source title needs complete consistent native member bindings."""

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.theme_workbench import (
    ThemeWorkbenchError,
    _apply_source_variant_order,
    _source_variant_theme_title,
)


def fixture():
    entries = [{"atomic_part_id": node} for node in ("q1", "q2")]
    direct = {
        node: (
            {
                "source_variant": "source_A",
                "source_identity": {
                    "source_id": "source",
                    "source_variant": "source_A",
                },
                "hierarchy": {
                    "paper_id": "paper",
                    "theme_id": "theme",
                    "atomic_part_id": node,
                    "theme_title": "来源A题面标题",
                },
            },
            {},
            {},
        )
        for node in ("q1", "q2")
    }
    return entries, direct


def test_consistent_complete_source_variant_title_without_mutation():
    entries, direct = fixture()
    before = deepcopy(direct)
    assert (
        _source_variant_theme_title(entries, direct, "paper", "theme")
        == "来源A题面标题"
    )
    assert direct == before


@pytest.mark.parametrize(
    "owner,key,value",
    [
        ("hierarchy", "theme_title", "另一个标题"),
        ("hierarchy", "theme_title", None),
        ("hierarchy", "paper_id", "other"),
        ("hierarchy", "theme_id", "other"),
        ("hierarchy", "atomic_part_id", "q1"),
        ("source_identity", "source_variant", "source_B"),
        ("source_identity", "source_id", "other"),
    ],
)
def test_conflicting_or_missing_bindings_stay_unknown(owner, key, value):
    entries, direct = fixture()
    direct["q2"][0][owner][key] = value
    assert _source_variant_theme_title(entries, direct, "paper", "theme") is None


def test_partial_legacy_and_empty_member_sets_do_not_invent_title():
    entries, direct = fixture()
    assert _source_variant_theme_title([], direct, "paper", "theme") is None
    direct.pop("q2")
    assert _source_variant_theme_title(entries, direct, "paper", "theme") is None


def test_selected_source_supplies_unknown_order_and_literal_number():
    _, direct = fixture()
    record = direct["q1"][0]
    record["hierarchy"].update(
        printed_question_id="printed",
        printed_sequence=7,
        atomic_sequence_in_printed=1,
        printed_question_number="3(7)",
    )
    entry = {
        "atomic_part_id": "q1",
        "printed_question_id": "printed",
        "printed_sequence": None,
        "atomic_sequence_in_printed": 1,
        "printed_question_number": None,
    }
    _apply_source_variant_order(entry, record)
    assert entry["printed_sequence"] == 7 and entry["printed_question_number"] == "3(7)"
    assert entry["printed_sequence_status"] == "known_explicit"
    entry["printed_sequence"] = 8
    with pytest.raises(ThemeWorkbenchError, match="source order conflicts"):
        _apply_source_variant_order(entry, record)
    entry["printed_sequence"] = 7
    record["hierarchy"]["atomic_part_id"] = "other"
    with pytest.raises(ThemeWorkbenchError, match="source order conflicts"):
        _apply_source_variant_order(entry, record)


def test_legacy_record_keeps_missing_order_unknown():
    entry = {"printed_sequence": None}
    _apply_source_variant_order(entry, {"hierarchy": {"printed_sequence": 1}})
    assert entry == {"printed_sequence": None}
    entries, direct = fixture()
    direct["q1"][0].pop("source_variant")
    assert _source_variant_theme_title(entries, direct, "paper", "theme") is None
