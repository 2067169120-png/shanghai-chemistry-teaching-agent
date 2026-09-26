from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1.datong_answer_bindings import (
    QUESTION_BINDINGS,
    SHARED_CONTEXT_BINDINGS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCAN_RECORDS = (
    PROJECT_ROOT / "sh-chem-db/kb/classification/"
    "question_visual_scan_dt2025_h1_mid_v1_2026-08-25/scan_records.jsonl"
)
INVENTORY = (
    PROJECT_ROOT / "runtime/deeptutor_shchem/qa/datong-repairs-20260909/"
    "inventory/datong_h1_midterm_theme2-5_inventory.json"
)


def _scan_records() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in SCAN_RECORDS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _remaining_scan_records() -> list[dict[str, Any]]:
    return [
        row
        for row in _scan_records()
        if 2 <= int(row["hierarchy"]["theme_sequence"]) <= 5
    ]


def _bindings_from_scan(
    records: list[dict[str, Any]], evidence_role: str
) -> dict[str, tuple[tuple[str, str], ...]]:
    return {
        row["hierarchy"]["atomic_part_id"]: tuple(
            (evidence["crop_id"], evidence["sha256"])
            for evidence in row["viewed_evidence"]
            if evidence["evidence_role"] == evidence_role
        )
        for row in records
    }


def _bindings_from_inventory(
    evidence_field: str,
) -> dict[str, tuple[tuple[str, str], ...]]:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    return {
        row["atomic_id"]: tuple(
            (evidence["crop_id"], evidence["crop_sha256"])
            for evidence in row[evidence_field]
        )
        for row in inventory["records"]
    }


@pytest.mark.skipif(
    not SCAN_RECORDS.is_file(), reason="local source archive is not distributed"
)
def test_question_bindings_match_inventory_and_archived_scan_records():
    records = _remaining_scan_records()
    expected_from_scan = _bindings_from_scan(records, "question")

    assert len(records) == 41
    assert len(expected_from_scan) == 41
    if INVENTORY.is_file():
        assert _bindings_from_inventory("question_evidence") == expected_from_scan
    assert QUESTION_BINDINGS == expected_from_scan
    assert all(value for value in QUESTION_BINDINGS.values())


@pytest.mark.skipif(
    not SCAN_RECORDS.is_file(), reason="local source archive is not distributed"
)
def test_shared_context_bindings_match_inventory_and_archived_scan_records():
    records = _remaining_scan_records()
    expected_from_scan = _bindings_from_scan(records, "shared_material")

    assert len(records) == 41
    if INVENTORY.is_file():
        assert (
            _bindings_from_inventory("shared_material_evidence") == expected_from_scan
        )
    assert SHARED_CONTEXT_BINDINGS == expected_from_scan
    assert sum(len(value) for value in SHARED_CONTEXT_BINDINGS.values()) == 49
    assert all(value for value in SHARED_CONTEXT_BINDINGS.values())


def test_q24_keeps_distinct_question_crops_and_all_shared_context():
    q24_s01 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q24-P01-S01"
    q24_s02 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q24-P01-S02"
    shared = (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q24_DIALYSIS",
            "e6e750f28baf42873483bf062641f3ff4eca10ea2284778850c1b2a50cd213e4",
        ),
    )

    assert QUESTION_BINDINGS[q24_s01] == (
        (
            "DT2025-H1-Q24-E1",
            "b3702f83b998ab5f1d210d29b9aa30b17b35ef755734233d3f3b65af1db96493",
        ),
    )
    assert QUESTION_BINDINGS[q24_s02] == (
        (
            "DT2025-H1-Q24-E2",
            "9693e79141cd2565299a48ffc477708bef161f346e4b8e15661b6bf53a60a56b",
        ),
    )
    assert SHARED_CONTEXT_BINDINGS[q24_s01] == shared
    assert SHARED_CONTEXT_BINDINGS[q24_s02] == shared


def test_q33_keeps_subpart_question_identity_and_shared_conditions():
    q33_p01 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P01"
    q33_p02 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P02"
    q33_p03_s01 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S01"
    q33_p03_s02 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S02"
    shared = (
        (
            "DT2025-H1-SHARED-SECTION_4",
            "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        ),
    )

    assert QUESTION_BINDINGS[q33_p01] == (
        (
            "DT2025-H1-Q33-P01-E1",
            "34f8309d3a846d97281ac50fcc4e19fa63e067183f647e4275a2b64109571304",
        ),
    )
    assert QUESTION_BINDINGS[q33_p02] == (
        (
            "DT2025-H1-Q33-P02-E1",
            "9863c0334b39588456f8bcac6fb7a93f873f95aed9e195c80f769ca3b3ceeee3",
        ),
    )
    assert (
        QUESTION_BINDINGS[q33_p03_s01]
        == QUESTION_BINDINGS[q33_p03_s02]
        == (
            (
                "DT2025-H1-Q33-P03-E1",
                "2ed718a55136798eeaf2698c782006d95012a9909b4af423dfb1c61ed3a764e7",
            ),
        )
    )
    for atomic_id in (q33_p01, q33_p02, q33_p03_s01, q33_p03_s02):
        assert SHARED_CONTEXT_BINDINGS[atomic_id] == shared


def test_required_shared_context_is_not_reduced_to_question_rows():
    q26 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q26-P01"
    q38_s01 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S01"
    q38_s02 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S02"
    q38_s03 = "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S03"
    assert SHARED_CONTEXT_BINDINGS[q26] == (
        (
            "DT2025-H1-SHARED-SECTION_3",
            "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        ),
    )
    expected_q38 = (
        (
            "DT2025-H1-SHARED-SECTION_5",
            "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        ),
        (
            "DT2025-H1-SHARED-VIS_Q38_ACID_LABEL",
            "41a5f7587d29d8c56330bfab4b6d3eb2ee9bb81ee0063874dfbfc21b74ac6d92",
        ),
    )
    assert SHARED_CONTEXT_BINDINGS[q38_s01] == expected_q38
    assert SHARED_CONTEXT_BINDINGS[q38_s02] == expected_q38
    assert SHARED_CONTEXT_BINDINGS[q38_s03] == expected_q38
