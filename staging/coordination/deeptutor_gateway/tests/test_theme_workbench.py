from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.theme_workbench import (
    AUTHORITY,
    SCHEMA_VERSION,
    WAVE_EXPECTED,
    ThemeWorkbenchError,
    ThemeWorkbenchReader,
    _explicit_sort,
    _reject_unsafe_projection,
    _scan_dependency,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
STUDENT_TOKEN = "theme-workbench-student-0123456789"


def browser_headers(server) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def projections() -> dict[str, dict]:
    reader = ThemeWorkbenchReader(SHCHEM_ROOT)
    return {scope: reader.groups(scope) for scope in ("wave1", "master")}


def flattened_atomic(data: dict) -> list[dict]:
    return [
        atomic
        for paper in data["papers"]
        for theme in paper["theme_groups"]
        for atomic in theme["atomic_chain"]
    ] + data["unassigned_pending_review"]["atomic_chain"]


def recursive_keys(value) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value] + [
            key for nested in value.values() for key in recursive_keys(nested)
        ]
    if isinstance(value, list):
        return [key for nested in value for key in recursive_keys(nested)]
    return []


def test_wave_scope_is_exactly_five_papers_twenty_five_themes_and_252_atomic(
    projections,
):
    data = projections["wave1"]
    assert "candidate_parent_chain_overlay" not in data
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["counts"] == {
        **WAVE_EXPECTED,
        "visual_scanned": 252,
        "unscanned": 0,
        "label_complete": 252,
        "label_pending": 0,
        "answer_aligned": 181,
        "answer_unaligned": 15,
        "answer_absent": 56,
        "quality_notes": 34,
    }
    assert data["unassigned_pending_review"] == {
        "status": "none",
        "count": 0,
        "reason_zh": None,
        "atomic_chain": [],
    }
    atomic = flattened_atomic(data)
    assert len(atomic) == len({item["atomic_part_id"] for item in atomic}) == 252
    assert all(item["visual_coverage_kind"] == "wave1_visual_scan" for item in atomic)
    assert all(item["printed_sequence_status"] == "known_explicit" for item in atomic)
    assert all(item["atomic_sequence_status"] == "known_explicit" for item in atomic)
    for paper in data["papers"]:
        assert len(paper["theme_groups"]) == 5
        sequences = [group["theme"]["sequence"] for group in paper["theme_groups"]]
        assert sequences == sorted(sequences) == [1, 2, 3, 4, 5]
        for group in paper["theme_groups"]:
            order = [
                (item["printed_sequence"], item["atomic_sequence_in_printed"])
                for item in group["atomic_chain"]
            ]
            assert order == sorted(order)
            assert group["counts"]["atomic"] == len(group["atomic_chain"])
            assert group["shared_context"]["material_count"] == len(
                group["shared_context"]["materials"]
            )


def test_wave_source_metadata_uses_all_five_structured_identity_records(projections):
    papers = {
        entry["paper"]["id"]: entry["paper"]["source_metadata"]
        for entry in projections["wave1"]["papers"]
    }
    expected = {
        "W1-DT2025-H1-MID": (
            "2025",
            "上海市大同中学",
            "期中考试试卷",
            "article_title_attribution_only",
        ),
        "W1-PT2026-EM": (
            "2026",
            "普陀区",
            "二模",
            "direct_paper_pixel_evidence",
        ),
        "W1-QP2026-EM": (
            "2026",
            "青浦区",
            "二模",
            "direct_paper_pixel_evidence",
        ),
        "W1-XH2026-EM": (
            "2026",
            "徐汇区",
            "二模",
            "article_title_attribution_only",
        ),
        "W1-YP2026-EM": (
            "2026",
            "杨浦区",
            "二模",
            "direct_paper_pixel_evidence",
        ),
    }
    assert set(papers) == set(expected)
    for paper_id, (year, region, paper_type, attribution) in expected.items():
        metadata = papers[paper_id]
        assert metadata["source_tier"] != "unknown"
        assert (
            metadata["year"],
            metadata["region"],
            metadata["paper_type"],
            metadata["attribution_status"],
        ) == (year, region, paper_type, attribution)


def test_master_scope_covers_470_once_keeps_43_unassigned_and_has_397_visible(
    projections,
):
    data = projections["master"]
    assert data["counts"] == {
        "papers": 20,
        "theme_groups": 48,
        "atomic_parts": 470,
        "display_atomic_units": 480,
        "unassigned_atomic_parts": 43,
        "visual_scanned": 407,
        "unscanned": 63,
        "label_complete": 379,
        "label_pending": 91,
        "answer_aligned": 371,
        "answer_unaligned": 13,
        "answer_absent": 96,
        "quality_notes": 70,
    }
    atomic = flattened_atomic(data)
    assert len(atomic) == len({item["atomic_part_id"] for item in atomic}) == 470
    for item in atomic:
        assert item["printed_sequence_status"] == (
            "known_explicit"
            if item["printed_sequence"] is not None
            else "unknown_pending_review"
        )
        assert item["atomic_sequence_status"] == (
            "known_explicit"
            if item["atomic_sequence_in_printed"] is not None
            else "unknown_pending_review"
        )
    assert data["unassigned_pending_review"]["count"] == 43
    assert data["unassigned_pending_review"]["status"] == (
        "missing_parent_pending_review"
    )
    aliases = [item for item in atomic if item["alias_units"]]
    assert len(aliases) == 24
    assert sum(len(item["alias_units"]) for item in aliases) == 34
    assert (
        len(
            {unit["atomic_part_id"] for item in aliases for unit in item["alias_units"]}
        )
        == 32
    )
    assert all(item["item_type"] is None for item in aliases)
    assert all(
        item["label_summary"]["status"] == "per_alias_unit_no_merge" for item in aliases
    )
    assert all(item["answer"]["availability"] == "per_alias_unit" for item in aliases)
    assert all(item["dependency"]["kind"] == "per_alias_unit" for item in aliases)
    blocked_exact = [
        item
        for item in atomic
        if item["label_summary"]["status"] == "blocked_exact_label_projection"
    ]
    assert len(blocked_exact) == 4
    assert all(item["item_type"] is None for item in blocked_exact)
    assert all(item["label_summary"]["primary_K"] is None for item in blocked_exact)


def test_master_parent_chain_candidate_overlay_is_sidecar_not_master_denominator(
    projections,
):
    data = projections["master"]
    overlay = data["candidate_parent_chain_overlay"]
    assert data["counts"]["atomic_parts"] == 470
    assert data["counts"]["display_atomic_units"] == 480
    assert data["counts"]["unassigned_atomic_parts"] == 43
    assert overlay["counts"] == {
        "papers": 1,
        "theme_groups": 5,
        "printed_questions": 41,
        "source_master_atomics": 43,
        "effective_atomics": 56,
        "split_source_master_atomics": 10,
        "dependency_edges": 8,
        "difficulty_factors": 560,
    }
    assert overlay["completion"] == {
        "denominator_unit": "source_master_atomic",
        "candidate_completed": 43,
        "candidate_total": 43,
        "candidate_remaining": 0,
        "candidate_status": "machine_candidate_complete_pending_central_confirmation",
        "candidate_label_zh": "候选整理43/43完成",
        "central_label_zh": "中央原记录待确认",
        "human_reviewed": False,
    }
    assert overlay["central_master"] == {
        "atomic_total": 470,
        "complete_parent_chain_atomics": 427,
        "original_pending_atomics": 43,
        "candidate_overlay_atomics": 43,
        "applied": False,
        "human_confirmed": False,
        "denominator_unchanged": True,
    }
    themes = overlay["paper_groups"][0]["theme_groups"]
    assert [group["theme"]["sequence"] for group in themes] == [1, 2, 3, 4, 5]
    assert [group["counts"]["effective_atomics"] for group in themes] == [
        15,
        11,
        12,
        9,
        9,
    ]
    source_rows = [row for group in themes for row in group["atomic_chain"]]
    source_ids = {row["atomic_part_id"] for row in source_rows}
    unassigned_ids = {
        row["atomic_part_id"]
        for row in data["unassigned_pending_review"]["atomic_chain"]
    }
    assert len(source_rows) == len(source_ids) == 43
    assert source_ids == unassigned_ids
    units = [unit for row in source_rows for unit in row["candidate_effective_units"]]
    assert len(units) == len({unit["atomic_part_id"] for unit in units}) == 56
    assert sum(len(row["candidate_effective_units"]) > 1 for row in source_rows) == 10
    assert all("alias_units" not in row for row in source_rows)
    assert all(unit["answer"]["availability"] == "absent" for unit in units)
    assert all(unit["authority"]["human_reviewed"] is False for unit in units)
    assert (
        sum(len(unit["textbook_mapping_candidate"]["entries"]) for unit in units) == 82
    )


def test_master_source_metadata_has_eighteen_explicit_years_and_keeps_two_unknown(
    projections,
):
    papers = {
        entry["paper"]["id"]: entry["paper"]
        for entry in projections["master"]["papers"]
    }
    known = {
        paper_id
        for paper_id, paper in papers.items()
        if paper["source_metadata"]["year"] != "unknown"
    }
    unknown = set(papers) - known
    assert len(known) == 18
    assert unknown == {
        "MASTER-PAPER-49323b87c2ba13c22b52",
        "MASTER-PAPER-df932ee3e19c621342a9",
    }
    # Both titles contain an academic-year literal, but neither upstream paper
    # record has a dedicated calendar-year field.  The projection must not
    # infer a searchable year from that title.
    assert all("2024-2025" in papers[paper_id]["title"] for paper_id in unknown)
    assert all(
        papers[paper_id]["source_metadata"]["year"] == "unknown" for paper_id in unknown
    )
    assert papers["PAPER-22b0a396c9c1c0a37591"]["source_metadata"] == {
        "source_tier": "core_hierarchy_audit",
        "year": "2026",
        "region": "浦东新区",
        "paper_type": "一模（卷面名称为第一学期期末教学质量检测）",
        "attribution_status": "unknown",
    }


def test_2026_recall_remains_four_themes_and_pudong_theme_only_is_separate(
    projections,
):
    recalls = [
        paper
        for paper in projections["master"]["papers"]
        if paper["paper"]["status"] == "observed_four_theme_recall_incomplete"
    ]
    assert len(recalls) == 1
    recall = recalls[0]
    assert recall["paper"]["observed_theme_count"] == 4
    assert recall["paper"]["missing_theme_note_zh"] == (
        "主题4题面缺失，不能称为五主题完整卷。"
    )
    assert [group["theme"]["sequence"] for group in recall["theme_groups"]] == [
        1,
        2,
        3,
        5,
    ]
    assert [group["counts"]["atomic"] for group in recall["theme_groups"]] == [
        8,
        11,
        10,
        6,
    ]

    pudong_theme_only = [
        paper
        for paper in projections["master"]["papers"]
        if paper["paper"]["status"] == "observed_theme_one_visual_scan_incomplete_paper"
    ]
    assert len(pudong_theme_only) == 1
    pudong = pudong_theme_only[0]
    assert pudong["paper"]["id"] != recall["paper"]["id"]
    assert pudong["paper"]["observed_theme_count"] == 1
    assert pudong["paper"]["missing_theme_note_zh"] == (
        "当前只完成主题一“水合肼”11个最小作答单元的逐题扫描；"
        "本记录不代表浦东一模整卷扫描完成。"
    )
    assert [group["theme"]["sequence"] for group in pudong["theme_groups"]] == [1]
    assert [group["counts"]["atomic"] for group in pudong["theme_groups"]] == [11]


def test_jiading_article_classified_theme_only_keeps_context_and_dependencies(
    projections,
):
    jiading = next(
        paper
        for paper in projections["master"]["papers"]
        if paper["paper"]["id"] == "PAPER-2f34ac7602572e37c8be"
    )
    assert jiading["paper"] == {
        "id": "PAPER-2f34ac7602572e37c8be",
        "title": (
            "2024学年高三年级第二次质量调研 化学试卷 / "
            "公众号标题归类：【高考二模】2025届上海市嘉定区高三二模化学试卷"
        ),
        "source_metadata": {
            "source_tier": "core_hierarchy_audit",
            "year": "2025",
            "region": "嘉定区（公众号标题；卷面未署地区）",
            "paper_type": "二模（公众号标题；卷面写第二次质量调研）",
            "attribution_status": "unknown",
        },
        "order": None,
        "order_status": "unknown_source_order_preserved",
        "status": "observed_theme_one_article_classified_incomplete_paper",
        "observed_theme_count": 1,
        "missing_theme_note_zh": (
            "当前只完成主题一“消毒剂”10个最小作答单元的逐题扫描；"
            "“嘉定区、2025届、二模”仅来自非官方公众号标题，卷面未署地区或“二模”，"
            "本记录也不代表整卷扫描完成。"
        ),
    }
    assert len(jiading["theme_groups"]) == 1
    group = jiading["theme_groups"][0]
    assert group["theme"]["id"] == "THEME-516deca72e2d9aca307d"
    assert group["theme"]["title"] == "消毒剂"
    assert group["theme"]["sequence"] == 1
    assert group["counts"] == {
        "printed": 8,
        "atomic": 10,
        "display_atomic_units": 10,
        "visual_scanned": 10,
        "unscanned": 0,
        "label_complete": 10,
        "label_pending": 0,
        "answer_aligned": 10,
        "answer_unaligned": 0,
        "answer_absent": 0,
        "quality_notes": 4,
    }
    assert {
        item["material_id"]: item["used_by_atomic_count"]
        for item in group["shared_context"]["materials"]
    } == {
        "JD2025-T1-SHARED-OPENING": 10,
        "JD2025-T1-SHARED-GRAPH-INTRO": 2,
        "JD2025-T1-SHARED-SPECIATION-GRAPH": 2,
    }
    assert group["dependencies"] == {
        "independent": 7,
        "shared_material_only": 2,
        "one_prior_part": 1,
        "multiple_prior_parts": 0,
        "per_alias_unit": 0,
        "explicit_prior_edge_count": 1,
        "blocked": 0,
    }
    by_id = {item["atomic_part_id"]: item for item in group["atomic_chain"]}
    assert by_id["JD2025-EM-S1-Q5-P2"]["dependency"] == {
        "kind": "one_prior_part",
        "prior_atomic_part_ids": ["JD2025-EM-S1-Q5-P1"],
        "explicit_prior_edge_count": 1,
        "status": "validated_explicit",
    }
    for atomic_id in ("JD2025-EM-S1-Q7-P2", "JD2025-EM-S1-Q8-P1"):
        assert by_id[atomic_id]["dependency"] == {
            "kind": "shared_material_only",
            "prior_atomic_part_ids": [],
            "explicit_prior_edge_count": 0,
            "status": "validated_explicit",
        }


def test_theme_projection_contains_no_answer_text_crop_locator_url_or_hash_inventory(
    projections,
):
    for data in projections.values():
        keys = {key.casefold() for key in recursive_keys(data)}
        assert "reference_answer_text" not in keys
        assert "answer_text" not in keys
        assert "answer_crop" not in keys
        assert "solution_path_zh" not in keys
        assert not any(
            key.endswith(("_path", "_url", "_sha256", "_hash", "_bindings"))
            for key in keys
        )
        serialized = json.dumps(data, ensure_ascii=False).casefold()
        for forbidden in (
            "http://",
            "https://",
            "file://",
            "c:\\users",
            "sh-chem-db/",
            "kb/classification/",
            "kb/formal/",
            "staging/",
            "visual_alignment_evidence",
        ):
            assert forbidden not in serialized
        assert data["authority"] == AUTHORITY
        assert data["integrity"]["answer_text_excluded"] is True
        assert data["integrity"]["pixel_reuse_allowed"] is False


def test_openapi_draft2020_schema_strictly_validates_both_live_dtos(projections):
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/ThemeWorkbenchData",
            "components": contract["components"],
        }
    )
    for scope, data in projections.items():
        errors = list(validator.iter_errors(data))
        assert errors == [], [
            {
                "scope": scope,
                "path": list(error.absolute_path),
                "message": error.message,
                "instance": repr(error.instance)[:200],
            }
            for error in errors[:10]
        ]
    schemas = contract["components"]["schemas"]
    for name, schema in schemas.items():
        if name.startswith("ThemeWorkbench") and schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, name


def test_mutations_reject_missing_duplicate_wrong_order_cross_theme_and_leak(
    projections,
):
    reader = ThemeWorkbenchReader(SHCHEM_ROOT)
    with pytest.raises(ThemeWorkbenchError, match="fixed scope counts"):
        reader._finalize(
            scope="wave1", papers=[], unassigned_entries=[], expected=WAVE_EXPECTED
        )

    papers = deepcopy(projections["wave1"]["papers"])
    duplicate = deepcopy(papers[0]["theme_groups"][0]["atomic_chain"][0])
    papers[0]["theme_groups"][1]["atomic_chain"].append(duplicate)
    with pytest.raises(ThemeWorkbenchError, match="duplicates an atomic"):
        reader._finalize(
            scope="wave1",
            papers=papers,
            unassigned_entries=[],
            expected={
                "papers": 5,
                "theme_groups": 25,
                "atomic_parts": 253,
                "display_atomic_units": 253,
                "unassigned_atomic_parts": 0,
            },
        )

    dependency_record = {
        "dependency": {
            "prior_atomic_part_ids": ["A"],
            "shared_material_crop_ids": [],
        }
    }
    with pytest.raises(ThemeWorkbenchError, match="cross-theme or not earlier"):
        _scan_dependency(
            dependency_record,
            current_id="B",
            positions={"A": ("T", 2), "B": ("T", 1)},
        )
    with pytest.raises(ThemeWorkbenchError, match="cross-theme or not earlier"):
        _scan_dependency(
            dependency_record,
            current_id="B",
            positions={"A": ("T1", 0), "B": ("T2", 1)},
        )
    with pytest.raises(ThemeWorkbenchError, match="duplicate explicit order"):
        _explicit_sort(
            [
                {"id": "A", "sequence": 1},
                {"id": "B", "sequence": 1},
            ],
            "sequence",
            identity_key="atomic",
        )
    with pytest.raises(ThemeWorkbenchError, match="forbidden"):
        _reject_unsafe_projection({"reference_answer_text": "C"})
    with pytest.raises(ThemeWorkbenchError, match="local locator"):
        _reject_unsafe_projection({"visible_summary_zh": "kb/private/item.json"})


def test_http_requires_exact_scope_teacher_origin_is_zero_write_and_no_store(tmp_path):
    state_root = tmp_path / "state"
    config = build_config(state_root)
    config.principals.append(
        Principal(
            "theme-student",
            "student",
            token_digest(STUDENT_TOKEN),
        )
    )
    config.validate()
    with running_server(config) as server:
        headers = browser_headers(server)
        for path in (
            "/api/v1/kb/workbench/theme-groups",
            "/api/v1/kb/workbench/theme-groups?scope=",
            "/api/v1/kb/workbench/theme-groups?scope=wave1&scope=master",
            "/api/v1/kb/workbench/theme-groups?scope=wave1&limit=1",
            "/api/v1/kb/workbench/theme-groups?scope=other",
        ):
            status, payload, _ = request(
                server, "GET", path, headers=headers, timeout=10
            )
            assert status == 400
            assert payload["error"]["code"].startswith("theme_workbench_")

        path = "/api/v1/kb/workbench/theme-groups?scope=master"
        status, _, _ = request(server, "GET", path, token=None, headers=headers)
        assert status == 401
        status, _, _ = request(
            server, "GET", path, token=STUDENT_TOKEN, headers=headers
        )
        assert status == 403
        status, _, _ = request(
            server,
            "GET",
            path,
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert status == 403

        before = tree_snapshot(state_root)
        status, payload, response_headers = request(
            server, "GET", path, headers=headers, timeout=30
        )
        assert status == 200
        assert payload["data"]["counts"]["atomic_parts"] == 470
        assert response_headers.get("Cache-Control") == "no-store"
        assert response_headers.get("X-Content-Type-Options") == "nosniff"
        assert tree_snapshot(state_root) == before

        def fail_theme_only(scope: str):
            raise ThemeWorkbenchError(
                "theme_workbench_dependency_unavailable",
                "synthetic dependency drift",
                409,
            )

        server.service.theme_workbench.groups = fail_theme_only
        status, failed, _ = request(server, "GET", path, headers=headers, timeout=10)
        assert status == 409
        assert failed["error"]["code"] == "theme_workbench_dependency_unavailable"
        # A theme aggregate failure never disables the native direct endpoint.
        status, direct, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/master-direct-scans/status",
            headers=headers,
            timeout=30,
        )
        assert status == 200
        assert direct["data"]["counts"]["direct_scan_records"] == 224


def test_openapi_has_one_controlled_read_only_theme_route():
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    operation = contract["paths"]["/api/v1/kb/workbench/theme-groups"]["get"]
    assert operation["parameters"] == [
        {
            "in": "query",
            "name": "scope",
            "required": True,
            "schema": {
                "type": "string",
                "enum": ["wave1", "master", "supplemental"],
            },
        }
    ]
    assert set(operation["responses"]) == {"200", "400", "401", "403", "409"}
    assert "post" not in contract["paths"]["/api/v1/kb/workbench/theme-groups"]
