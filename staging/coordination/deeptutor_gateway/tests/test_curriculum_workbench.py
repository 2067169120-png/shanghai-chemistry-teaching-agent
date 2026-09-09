from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

import integrations.deeptutor_shchem_v1.curriculum_workbench as curriculum_module
from integrations.deeptutor_shchem_v1.curriculum_workbench import (
    ACTIVE_ATOMIC_COUNT,
    ACTIVE_MASTER_COUNT,
    ACTIVE_SUPPLEMENTAL_COUNT,
    DIRECTORY_RELATIVE,
    EDITION_STATUS_UNKNOWN,
    EXPECTED_CHAPTER_COUNT,
    EXPECTED_SECTION_COUNT,
    EXPECTED_VOLUME_COUNT,
    UNIT_STATUS_UNKNOWN,
    CurriculumWorkbenchError,
    CurriculumWorkbenchReader,
)
from integrations.deeptutor_shchem_v1.huangpu2025_theme4_direct_visual_scan import (
    EXPECTED_ATOMIC_IDS as HUANGPU_ATOMIC_IDS,
)
from integrations.deeptutor_shchem_v1.huangpu2025_theme4_direct_visual_scan import (
    HONGKOU2026_SECOND_MOCK_THEME4_CONFIG,
    QIBAO2025_OPENING_THEME4_CONFIG,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
DIRECTORY = SHCHEM_ROOT / DIRECTORY_RELATIVE
SUPPLEMENTAL_RECORDS = (
    SHCHEM_ROOT
    / "kb/classification/"
    "supplemental_wechat_textbook_tagging_v1_2026-08-27/"
    "tagging_records.jsonl"
)


@pytest.fixture(scope="module")
def real_reader() -> CurriculumWorkbenchReader:
    reader = CurriculumWorkbenchReader(SHCHEM_ROOT)
    # Build the expensive strict snapshot once for the module.  Every public
    # method below must reuse it and return a caller-owned projection.
    reader.catalog()
    return reader


def _walk(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key, nested
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _isolated_directory(tmp_path: Path, raw: bytes) -> Path:
    root = tmp_path / "sh-chem-db"
    target = root / DIRECTORY_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    return root


def _expect_error(reader: CurriculumWorkbenchReader, code: str) -> None:
    with pytest.raises(CurriculumWorkbenchError) as captured:
        reader.catalog()
    assert captured.value.code == code


def test_catalog_is_exact_safe_5_19_60_and_preserves_unknown_boundaries(
    real_reader: CurriculumWorkbenchReader,
) -> None:
    catalog = real_reader.catalog()

    assert catalog["counts"]["volumes"] == EXPECTED_VOLUME_COUNT == 5
    assert catalog["counts"]["chapters"] == EXPECTED_CHAPTER_COUNT == 19
    assert catalog["counts"]["sections"] == EXPECTED_SECTION_COUNT == 60
    assert catalog["counts"]["section_ids_known"] == 3
    assert catalog["counts"]["section_ids_unknown"] == 57

    volumes = catalog["volumes"]
    chapters = [chapter for volume in volumes for chapter in volume["chapters"]]
    sections = [section for chapter in chapters for section in chapter["sections"]]
    assert len({volume["volume_id"] for volume in volumes}) == 5
    assert len({chapter["chapter_id"] for chapter in chapters}) == 19
    assert len({section["section_key"] for section in sections}) == 60
    known_section_ids = [
        section["section_id"] for section in sections if section["section_id"]
    ]
    assert len(known_section_ids) == len(set(known_section_ids)) == 3

    assert all(volume["edition_or_printing"] is None for volume in volumes)
    assert all(
        volume["edition_status"] == EDITION_STATUS_UNKNOWN for volume in volumes
    )
    assert all(section["unit_id"] is None for section in sections)
    assert all(section["unit_title"] is None for section in sections)
    assert all(section["unit_status"] == UNIT_STATUS_UNKNOWN for section in sections)


def test_mapping_index_is_exact_master30_plus_supplemental57(
    real_reader: CurriculumWorkbenchReader,
) -> None:
    index = real_reader.mapping_index()
    records = index["records"]
    ids = [record["atomic_id"] for record in records]

    assert len(ids) == len(set(ids)) == ACTIVE_ATOMIC_COUNT == 87
    assert index["counts"] == {
        "active_atomic_mappings": 87,
        "mapping_entries": 163,
        "mapped_atomic_count": 87,
        "complete_atomic_count": 80,
        "partial_atomic_count": 7,
        "blocked_atomic_count": 7,
        "mapped_entry_count": 156,
        "blocked_entry_count": 7,
    }
    assert Counter(record["source_layer"] for record in records) == {
        "master_direct_active": ACTIVE_MASTER_COUNT,
        "supplemental_wechat_active": ACTIVE_SUPPLEMENTAL_COUNT,
    }
    assert Counter(record["source_batch"] for record in records) == {
        "master_direct_huangpu_2025_theme4": 11,
        "master_direct_qibao_2025_theme4": 10,
        "master_direct_hongkou_2026_theme4": 9,
        "supplemental_wechat_57": 57,
    }
    assert Counter(record["mapping_status"] for record in records) == {
        "complete": 80,
        "partial": 7,
    }

    expected_master = {
        *HUANGPU_ATOMIC_IDS,
        *QIBAO2025_OPENING_THEME4_CONFIG.atomic_ids,
        *HONGKOU2026_SECOND_MOCK_THEME4_CONFIG.atomic_ids,
    }
    actual_master = {
        record["atomic_id"]
        for record in records
        if record["source_layer"] == "master_direct_active"
    }
    assert actual_master == expected_master

    expected_supplemental = {
        json.loads(line)["hierarchy"]["atomic_part_id"]
        for line in SUPPLEMENTAL_RECORDS.read_text(encoding="utf-8").splitlines()
    }
    actual_supplemental = {
        record["atomic_id"]
        for record in records
        if record["source_layer"] == "supplemental_wechat_active"
    }
    assert actual_supplemental == expected_supplemental


def test_handout_datong_and_fengxian_are_explicitly_excluded_from_active(
    real_reader: CurriculumWorkbenchReader,
) -> None:
    index = real_reader.mapping_index()
    excluded = {
        item["layer_id"]: item for item in index["coverage"]["excluded_pending_layers"]
    }
    assert index["coverage"]["active_atomic_count"] == 87
    assert sum(
        item["active_atomic_count"]
        for item in index["coverage"]["active_layers"]
    ) == 87

    assert excluded["supplemental_external_handout"] == {
        "layer_id": "supplemental_external_handout",
        "label_zh": "外部教学讲义",
        "included_in_active": False,
        "pending_atomic_count": 29,
        "verification_status": "Supplemental 分层已核对",
    }
    assert excluded["datong_parent_chain_candidate"]["included_in_active"] is False
    assert excluded["datong_parent_chain_candidate"][
        "pending_source_atomic_count"
    ] == 43
    assert excluded["datong_parent_chain_candidate"][
        "pending_effective_atomic_count"
    ] == 56
    assert excluded["fengxian_candidate"] == {
        "layer_id": "fengxian_candidate",
        "label_zh": "奉贤候选",
        "included_in_active": False,
        "pending_atomic_count": None,
        "declared_scope_atomic_count": 10,
        "verification_status": "未载入，不计入分母",
    }
    active_batches = {record["source_batch"] for record in index["records"]}
    assert not active_batches & {
        "datong_parent_chain_candidate",
        "supplemental_external_handout",
        "fengxian_candidate",
    }


def test_blocked_mapping_never_fabricates_section_precision(
    real_reader: CurriculumWorkbenchReader,
) -> None:
    index = real_reader.mapping_index()
    blocked = [
        (record["atomic_id"], entry)
        for record in index["records"]
        for entry in record["entries"]
        if entry["mapping_status"] == "blocked"
    ]
    assert len(blocked) == 7
    assert len({atomic_id for atomic_id, _ in blocked}) == 7
    for _, entry in blocked:
        assert entry["section_key"] is None
        assert entry["section_id"] is None
        assert entry["section_number"] is None
        assert entry["section_title"] is None

    catalog = real_reader.catalog()
    sections = [
        section
        for volume in catalog["volumes"]
        for chapter in volume["chapters"]
        for section in chapter["sections"]
    ]
    assert all(
        section["mapping_counts"]["blocked_atomic_count"] == 0
        and section["mapping_counts"]["blocked_entry_count"] == 0
        for section in sections
    )
    chapter_blocked = real_reader.search(
        chapter_id="TB-E1-C3", mapping_status="blocked"
    )
    assert chapter_blocked["counts"]["matched_atomic_count"] == 4
    assert real_reader.search(
        section="TB-E1-C3:3.1", mapping_status="blocked"
    )["atomic_ids"] == []


def test_catalog_counts_match_pure_node_queries_and_search_contract(
    real_reader: CurriculumWorkbenchReader,
) -> None:
    catalog = real_reader.catalog()
    for volume in catalog["volumes"]:
        assert len(real_reader.atomic_ids_for("volume", volume["volume_id"])) == (
            volume["mapping_counts"]["mapped_atomic_count"]
        )
        for chapter in volume["chapters"]:
            assert len(
                real_reader.atomic_ids_for("chapter", chapter["chapter_id"])
            ) == chapter["mapping_counts"]["mapped_atomic_count"]
            for section in chapter["sections"]:
                assert len(
                    real_reader.atomic_ids_for("section", section["section_key"])
                ) == section["mapping_counts"]["mapped_atomic_count"]

    all_mapped = real_reader.search()
    assert all_mapped["counts"]["matched_atomic_count"] == 87
    assert all_mapped["counts"]["source_group_count"] == 4
    assert all(group["group_kind"] == "mapping_source" for group in all_mapped["groups"])
    assert all_mapped["integrity"]["complete_theme_chain_returned"] is False
    assert all_mapped["integrity"]["theme_join_required_downstream"] is True

    section_by_key = real_reader.search(section="TB-M1-C1:1.2")
    section_by_id = real_reader.search(section="TB-M1-C1-S1.2")
    assert section_by_key["atomic_ids"] == section_by_id["atomic_ids"]
    assert section_by_id["query"]["resolved_section_key"] == "TB-M1-C1:1.2"
    assert real_reader.atomic_ids_for("section", "TB-M1-C1:1.2") == frozenset(
        section_by_key["atomic_ids"]
    )

    complete = set(
        real_reader.search(chapter_id="TB-E1-C3", mapping_status="complete")[
            "atomic_ids"
        ]
    )
    partial = set(
        real_reader.search(chapter_id="TB-E1-C3", mapping_status="partial")[
            "atomic_ids"
        ]
    )
    mapped = set(real_reader.search(chapter_id="TB-E1-C3")["atomic_ids"])
    assert complete.isdisjoint(partial)
    assert complete | partial == mapped


def test_responses_never_expose_location_link_or_digest_material(
    real_reader: CurriculumWorkbenchReader,
) -> None:
    payloads = (
        real_reader.catalog(),
        real_reader.mapping_index(),
        real_reader.search(chapter_id="TB-E1-C3"),
    )
    for payload in payloads:
        serialized = json.dumps(payload, ensure_ascii=False)
        for key, value in _walk(payload):
            lowered = key.casefold()
            assert "path" not in lowered
            assert "url" not in lowered
            assert "sha256" not in lowered
            assert "hash" not in lowered
            if isinstance(value, str):
                assert not curriculum_module._HEX64.fullmatch(value)
        lowered_text = serialized.casefold()
        for forbidden in (
            "http://",
            "https://",
            "file://",
            "sh-chem-db/",
            "sh-chem-db\\",
            "staging/",
            "staging\\",
            ".intake/",
            ".intake\\",
            "课本/",
            "课本\\",
        ):
            assert forbidden not in lowered_text


def test_public_results_are_caller_owned_and_cached_snapshot_is_immutable(
    real_reader: CurriculumWorkbenchReader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = real_reader._snapshot()
    monkeypatch.setattr(
        real_reader,
        "_build_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("cache rebuilt")),
    )

    catalog = real_reader.catalog()
    catalog["counts"]["volumes"] = 999
    catalog["volumes"][0]["chapters"].clear()
    index = real_reader.mapping_index()
    index["records"][0]["entries"][0]["volume_title"] = "被调用方篡改"
    result = real_reader.search()
    result["atomic_ids"].clear()
    result["groups"].clear()

    fresh_catalog = real_reader.catalog()
    fresh_index = real_reader.mapping_index()
    fresh_search = real_reader.search()
    assert real_reader._snapshot() is cached
    assert fresh_catalog["counts"]["volumes"] == 5
    assert fresh_catalog["volumes"][0]["chapters"]
    assert fresh_index["records"][0]["entries"][0]["volume_title"] != (
        "被调用方篡改"
    )
    assert len(fresh_search["atomic_ids"]) == 87
    assert len(fresh_search["groups"]) == 4


def test_empty_bad_json_digest_drift_and_duplicate_nodes_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = b""
    monkeypatch.setattr(
        curriculum_module,
        "EXPECTED_DIRECTORY_FILE_SHA256",
        hashlib.sha256(empty).hexdigest(),
    )
    _expect_error(
        CurriculumWorkbenchReader(_isolated_directory(tmp_path / "empty", empty)),
        "curriculum_directory_size_invalid",
    )

    invalid = b"{not-json}"
    monkeypatch.setattr(
        curriculum_module,
        "EXPECTED_DIRECTORY_FILE_SHA256",
        hashlib.sha256(invalid).hexdigest(),
    )
    _expect_error(
        CurriculumWorkbenchReader(_isolated_directory(tmp_path / "bad", invalid)),
        "curriculum_directory_json_invalid",
    )

    original = DIRECTORY.read_bytes()
    monkeypatch.setattr(
        curriculum_module,
        "EXPECTED_DIRECTORY_FILE_SHA256",
        "0" * 64,
    )
    _expect_error(
        CurriculumWorkbenchReader(_isolated_directory(tmp_path / "drift", original)),
        "curriculum_directory_drift",
    )

    duplicated = json.loads(original.decode("utf-8"))
    duplicated["nodes"][-1] = deepcopy(duplicated["nodes"][0])
    duplicate_raw = json.dumps(duplicated, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(
        curriculum_module,
        "EXPECTED_DIRECTORY_FILE_SHA256",
        hashlib.sha256(duplicate_raw).hexdigest(),
    )
    _expect_error(
        CurriculumWorkbenchReader(
            _isolated_directory(tmp_path / "duplicate", duplicate_raw)
        ),
        "curriculum_directory_duplicate_node",
    )


def test_duplicate_active_mapping_and_invalid_queries_fail_closed(
    real_reader: CurriculumWorkbenchReader,
) -> None:
    snapshot = real_reader._snapshot()
    duplicated = (*snapshot.mappings[:-1], snapshot.mappings[0])
    reader = CurriculumWorkbenchReader(
        SHCHEM_ROOT,
        mapping_loader=lambda: (duplicated, ()),
    )
    _expect_error(reader, "curriculum_active_mapping_duplicate")

    invalid = (
        ({"volume_id": "TB-UNKNOWN"}, "curriculum_node_not_found"),
        (
            {"volume_id": "TB-M1", "chapter_id": "TB-E1-C3"},
            "curriculum_query_conflict",
        ),
        ({"section": "not-a-section"}, "curriculum_node_not_found"),
        ({"mapping_status": "guessed"}, "curriculum_mapping_status_invalid"),
    )
    for kwargs, code in invalid:
        with pytest.raises(CurriculumWorkbenchError) as captured:
            real_reader.search(**kwargs)
        assert captured.value.code == code
    with pytest.raises(CurriculumWorkbenchError) as captured:
        real_reader.atomic_ids_for("unit", "x")
    assert captured.value.code == "curriculum_query_level_invalid"
