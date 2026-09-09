from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.curriculum_workbench import (
    CurriculumWorkbenchReader,
)
from integrations.deeptutor_shchem_v1.question_search_workbench import (
    AUTHORITY,
    FACET_LABELS_ZH,
    QuestionSearchError,
    QuestionSearchWorkbench,
)
from integrations.deeptutor_shchem_v1.supplemental_visual_scan import (
    SupplementalVisualScanReader,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"


@pytest.fixture(scope="module")
def real_theme_projections() -> dict[str, dict]:
    themes = ThemeWorkbenchReader(SHCHEM_ROOT)
    return {
        "master": themes.groups("master"),
        "wave1": themes.groups("wave1"),
        "supplemental": SupplementalVisualScanReader(SHCHEM_ROOT).theme_groups(),
    }


def atomic(
    node_id: str,
    *,
    summary: str,
    knowledge: str,
    ability: str,
    item_type: str = "reasoned_explanation",
    answer: str = "present_part_aligned",
    prior: tuple[str, ...] = (),
) -> dict:
    return {
        "atomic_part_id": node_id,
        "printed_question_id": f"PQ-{node_id}",
        "printed_question_number": node_id.removeprefix("A"),
        "printed_sequence": int(node_id.removeprefix("A")),
        "printed_sequence_status": "known_explicit",
        "atomic_sequence_in_printed": 1,
        "atomic_sequence_status": "known_explicit",
        "item_type": item_type,
        "label_summary": {
            "status": "complete",
            "source": "model_visual_scan_candidate",
            "primary_K": knowledge,
            "supporting_K": [],
            "A": [ability],
            "C": ["C03"],
            "R": ["R05"],
            "RP": ["RP02"],
            "cognitive_prelabel": "D3",
        },
        "answer": {
            "availability": answer,
            "source_authority": (
                "none" if answer == "absent" else "nonofficial_reference"
            ),
            "has_quality_note": False,
        },
        "visual_coverage_kind": "direct_new_visual_scan",
        "dependency": {
            "kind": "one_prior_part" if prior else "shared_material_only",
            "prior_atomic_part_ids": list(prior),
            "explicit_prior_edge_count": len(prior),
            "status": "model_visual_scan_pending_human",
        },
        "visible_summary_zh": summary,
        "response_requirement_zh": "根据题图与共同材料作答。",
        "theme_chain_role": {
            "value": "解释主题材料",
            "status": "model_candidate_pending_human",
        },
        "detail_endpoint": f"/api/v1/kb/workbench/master-direct-scans/{node_id}",
        "alias_units": [],
    }


def group(
    theme_id: str,
    title: str,
    atoms: list[dict],
    *,
    context: str,
    paper_id: str = "PAPER-1",
    paper_title: str = "2025 测试卷",
) -> dict:
    paper = {
        "id": paper_id,
        "title": paper_title,
        "order": None,
        "order_status": "unknown_source_order_preserved",
        "status": "complete_parent_chain_inventory",
        "observed_theme_count": 2,
        "missing_theme_note_zh": None,
    }
    return {
        "paper": paper,
        "theme": {
            "id": theme_id,
            "title": title,
            "sequence": int(theme_id.removeprefix("T")),
            "sequence_status": "known_explicit",
            "page_span": {
                "start_page": 1,
                "end_page": 2,
                "page_numbers": [1, 2],
                "status": "explicit_visual_evidence_union",
            },
            "parent_chain_status": "complete",
        },
        "counts": {
            "printed": len(atoms),
            "atomic": len(atoms),
            "display_atomic_units": len(atoms),
            "visual_scanned": len(atoms),
            "unscanned": 0,
            "label_complete": len(atoms),
            "label_pending": 0,
            "answer_aligned": sum(
                item["answer"]["availability"] == "present_part_aligned"
                for item in atoms
            ),
            "answer_unaligned": 0,
            "answer_absent": sum(
                item["answer"]["availability"] == "absent" for item in atoms
            ),
            "quality_notes": 0,
        },
        "shared_context": {
            "context_summary_zh": context,
            "context_status": "visual_scan_candidate_context",
            "material_count": 1,
            "materials": [
                {
                    "material_id": f"M-{theme_id}",
                    "type": "shared_graph",
                    "page": 1,
                    "candidate_description_zh": "主题共享图",
                    "preview_allowed": True,
                    "used_by_atomic_count": len(atoms),
                }
            ],
        },
        "dependencies": {
            "independent": 0,
            "shared_material_only": sum(
                not item["dependency"]["prior_atomic_part_ids"] for item in atoms
            ),
            "one_prior_part": sum(
                bool(item["dependency"]["prior_atomic_part_ids"]) for item in atoms
            ),
            "multiple_prior_parts": 0,
            "per_alias_unit": 0,
            "explicit_prior_edge_count": sum(
                item["dependency"]["explicit_prior_edge_count"] for item in atoms
            ),
            "blocked": 0,
        },
        "atomic_chain": atoms,
    }


def theme_projection(scope: str = "master") -> dict:
    first = group(
        "T1",
        "电化学与金属防护",
        [
            atomic(
                "A1",
                summary="分析原电池电子流向",
                knowledge="K10",
                ability="A03",
            ),
            atomic(
                "A2",
                summary="解释盐桥的作用",
                knowledge="K10",
                ability="A04",
                prior=("A1",),
            ),
        ],
        context="以海水中钢铁腐蚀和防护为共同情境。",
    )
    second = group(
        "T2",
        "有机合成",
        [
            atomic(
                "A3",
                summary="书写有机反应结构简式",
                knowledge="K17",
                ability="A03",
                item_type="organic_structure_or_route",
                answer="absent",
            )
        ],
        context="以药物合成为共同情境。",
    )
    return {
        "schema_version": "1.0.0-theme-workbench-candidate",
        "scope": scope,
        "counts": {},
        "papers": [
            {
                "paper": first["paper"],
                "theme_groups": [first, second],
            }
        ],
        "unassigned_pending_review": {
            "status": "none",
            "count": 0,
            "reason_zh": None,
            "atomic_chain": [],
        },
    }


def curriculum_projection(
    atomic_ids: tuple[str, ...] = ("A1",),
    *,
    source_layer: str = "master_direct_active",
) -> dict:
    items = [
        {
            "atomic_id": atomic_id,
            "source_layer": source_layer,
            "source_batch": "test-explicit-mapping",
            "mapping_status": "complete",
            "matched_entry_count": 1,
            "matched_volume_ids": ["TB-E1"],
            "matched_chapter_ids": ["TB-E1-C1"],
            "matched_section_keys": ["TB-E1-C1:1.1"],
        }
        for atomic_id in atomic_ids
    ]
    return {
        "schema_version": "1.0.0-curriculum-workbench",
        "data_snapshot_id": "CURRICULUM-WORKBENCH-TEST",
        "scope": "candidate_only_read_only_curriculum_workbench",
        "query": {
            "volume_id": "TB-E1",
            "chapter_id": "TB-E1-C1",
            "section": None,
            "resolved_section_key": None,
            "mapping_status": "complete",
        },
        "counts": {
            "active_atomic_total": len(items),
            "matched_atomic_count": len(items),
            "matched_entry_count": len(items),
            "source_group_count": 1,
        },
        "atomic_ids": list(atomic_ids),
        "groups": [],
        "items": items,
        "authority": {},
        "integrity": {
            "explicit_directory_mapping_only": True,
            "knowledge_tag_inference_used": False,
        },
    }


def test_search_returns_whole_theme_and_only_highlights_matching_atomics() -> None:
    workbench = QuestionSearchWorkbench()
    projection = theme_projection()
    response = workbench.search(
        {
            "scope": "master",
            "q": "原电池",
            "filters": {"K": ["K10"], "A": ["A03"]},
            "limit": 20,
        },
        theme_loader=lambda scope: projection,
        snapshot_id="a" * 64,
    )

    assert response["authority"] == AUTHORITY
    assert response["counts"] == {
        "theme_cards_scanned": 2,
        "theme_cards_matched": 1,
        "atomic_parts_scanned": 3,
        "atomic_parts_matched": 1,
        "returned_theme_cards": 1,
    }
    card = response["items"][0]
    assert card["theme"]["title"] == "电化学与金属防护"
    assert card["matched_atomic_ids"] == ["A1"]
    assert [item["atomic_part_id"] for item in card["atomic_chain"]] == ["A1", "A2"]
    assert card["atomic_chain"][1]["dependency"]["prior_atomic_part_ids"] == ["A1"]
    assert response["facets"]["K"]["label_zh"] == FACET_LABELS_ZH["K"]
    assert response["facets"]["K"]["values"] == [
        {
            "value": "K10",
            "label_zh": "水溶液中的离子反应与平衡",
            "atomic_count": 1,
        }
    ]
    assert response["integrity"]["atomic_matches_are_highlights_only"] is True
    assert response["integrity"]["complete_theme_chain_returned"] is True


def test_unassigned_atomics_are_isolated_and_never_claim_a_complete_theme() -> None:
    projection = theme_projection()
    projection["unassigned_pending_review"] = {
        "status": "pending_parent_review",
        "count": 2,
        "reason_zh": "父链待补",
        "atomic_chain": [
            atomic(
                "A4",
                summary="唯一待补线索甲",
                knowledge="K10",
                ability="A03",
            ),
            atomic(
                "A5",
                summary="另一待补线索乙",
                knowledge="K17",
                ability="A04",
            ),
        ],
    }
    response = QuestionSearchWorkbench().search(
        {"scope": "master", "q": "唯一待补线索甲"},
        theme_loader=lambda scope: projection,
        snapshot_id="0" * 64,
    )

    assert response["counts"]["atomic_parts_matched"] == 1
    assert response["page"]["total_theme_cards"] == 1
    card = response["items"][0]
    assert card["group_kind"] == "unassigned_pending_review"
    assert card["paper"] is None
    assert card["theme"] is None
    assert card["shared_context"] is None
    assert card["matched_atomic_ids"] == ["A4"]
    assert [atom["atomic_part_id"] for atom in card["atomic_chain"]] == ["A4"]
    assert response["integrity"]["complete_theme_chain_returned"] is False


def test_curriculum_selector_highlights_only_explicit_atomics_and_keeps_theme() -> None:
    response = QuestionSearchWorkbench().search(
        {
            "scope": "master",
            "curriculum": {
                "volume_id": "TB-E1",
                "mapping_status": "complete",
            },
        },
        theme_loader=lambda scope: theme_projection(scope),
        curriculum_loader=lambda selector: curriculum_projection(),
        snapshot_id="9" * 64,
    )

    assert response["counts"]["atomic_parts_matched"] == 1
    card = response["items"][0]
    assert card["matched_atomic_ids"] == ["A1"]
    assert [atom["atomic_part_id"] for atom in card["atomic_chain"]] == [
        "A1",
        "A2",
    ]
    assert card["shared_context"]["material_count"] == 1
    assert card["dependencies"]["one_prior_part"] == 1
    assert card["match_details"] == [
        {
            "atomic_part_id": "A1",
            "reason_codes": ["curriculum_explicit_mapping_match"],
        }
    ]
    assert response["curriculum"] == {
        "selector": {"volume_id": "TB-E1", "mapping_status": "complete"},
        "resolved_query": curriculum_projection()["query"],
        "allowed_atomic_counts_by_scope": {
            "master": 1,
            "wave1": 0,
            "supplemental": 0,
        },
        "matched_atomic_count": 1,
        "reason_code": "curriculum_explicit_mapping_match",
        "explicit_mapping_only": True,
        "knowledge_tag_inference_used": False,
    }

    wave = QuestionSearchWorkbench().search(
        {"scope": "wave1", "curriculum": {"volume_id": "TB-E1"}},
        theme_loader=lambda scope: theme_projection(scope),
        curriculum_loader=lambda selector: curriculum_projection(),
    )
    assert wave["items"] == []
    assert wave["curriculum"]["allowed_atomic_counts_by_scope"]["wave1"] == 0


def test_cursor_is_stable_for_same_query_and_rejected_for_a_changed_query() -> None:
    workbench = QuestionSearchWorkbench()
    projection = theme_projection()
    loader = lambda scope: projection
    first = workbench.search(
        {"scope": "master", "limit": 1},
        theme_loader=loader,
        snapshot_id="b" * 64,
    )
    assert first["page"]["has_more"] is True
    assert first["page"]["next_cursor"]
    second = workbench.search(
        {
            "scope": "master",
            "limit": 1,
            "cursor": first["page"]["next_cursor"],
        },
        theme_loader=loader,
        snapshot_id="b" * 64,
    )
    assert second["items"][0]["theme"]["id"] == "T2"
    with pytest.raises(QuestionSearchError) as captured:
        workbench.search(
            {
                "scope": "master",
                "q": "有机",
                "limit": 1,
                "cursor": first["page"]["next_cursor"],
            },
            theme_loader=loader,
            snapshot_id="b" * 64,
        )
    assert captured.value.code == "question_search_cursor_stale"


def test_cursor_is_bound_to_curriculum_selector() -> None:
    workbench = QuestionSearchWorkbench()
    loader = lambda selector: curriculum_projection(("A1", "A3"))
    first = workbench.search(
        {
            "scope": "master",
            "curriculum": {"volume_id": "TB-E1"},
            "limit": 1,
        },
        theme_loader=lambda scope: theme_projection(scope),
        curriculum_loader=loader,
        snapshot_id="8" * 64,
    )
    assert first["page"]["next_cursor"]
    with pytest.raises(QuestionSearchError) as captured:
        workbench.search(
            {
                "scope": "master",
                "curriculum": {"chapter_id": "TB-E1-C1"},
                "limit": 1,
                "cursor": first["page"]["next_cursor"],
            },
            theme_loader=lambda scope: theme_projection(scope),
            curriculum_loader=loader,
            snapshot_id="8" * 64,
        )
    assert captured.value.code == "question_search_cursor_stale"


def test_missing_structured_source_fields_are_unknown_not_title_inferences() -> None:
    projection = theme_projection()
    projection["papers"][0]["paper"].update(
        {
            "title": "2025年徐汇区二模（恶意标题不能成为来源证据）",
            "year": 2025,
            "region": "徐汇区",
            "source_tier": "untrusted_top_level",
        }
    )
    response = QuestionSearchWorkbench().search(
        {"scope": "master", "filters": {"year": ["unknown"]}},
        theme_loader=lambda scope: projection,
        snapshot_id="c" * 64,
    )
    assert response["counts"]["theme_cards_matched"] == 2
    assert all(
        item["source_metadata"] == {
            "question_bank_layer": "master",
            "source_tier": "unknown",
            "year": "unknown",
            "region": "unknown",
            "paper_type": "unknown",
            "attribution_status": "unknown",
        }
        for item in response["items"]
    )
    empty = QuestionSearchWorkbench().search(
        {"scope": "master", "filters": {"year": ["2025"]}},
        theme_loader=lambda scope: projection,
        snapshot_id="d" * 64,
    )
    assert empty["items"] == []


def test_real_year_region_and_source_facets_cover_all_three_scopes(
    real_theme_projections,
) -> None:
    loader = lambda scope: real_theme_projections[scope]

    wave = QuestionSearchWorkbench().search(
        {"scope": "wave1", "limit": 50},
        theme_loader=loader,
        snapshot_id="1" * 64,
    )
    assert [
        (item["value"], item["atomic_count"])
        for item in wave["facets"]["year"]["values"]
    ] == [("2026", 196), ("2025", 56)]
    assert {
        item["value"]: item["atomic_count"]
        for item in wave["facets"]["region"]["values"]
    } == {
        "上海市大同中学": 56,
        "杨浦区": 53,
        "普陀区": 52,
        "徐汇区": 46,
        "青浦区": 45,
    }

    master_unknown = QuestionSearchWorkbench().search(
        {"scope": "master", "filters": {"year": ["unknown"]}, "limit": 50},
        theme_loader=loader,
        snapshot_id="2" * 64,
    )
    assert master_unknown["counts"]["theme_cards_matched"] == 53
    assert master_unknown["counts"]["atomic_parts_matched"] == 124
    assert master_unknown["facets"]["year"]["values"] == [
        {"value": "unknown", "label_zh": "待补", "atomic_count": 124}
    ]
    assert all(
        len(item["atomic_chain"]) == 1
        for item in master_unknown["items"]
        if item["group_kind"] == "unassigned_pending_review"
    )
    assert master_unknown["integrity"]["complete_theme_chain_returned"] is False

    supplemental = QuestionSearchWorkbench().search(
        {"scope": "supplemental", "limit": 50},
        theme_loader=loader,
        snapshot_id="3" * 64,
    )
    assert [
        (item["value"], item["atomic_count"])
        for item in supplemental["facets"]["source_tier"]["values"]
    ] == [
        ("shanghai_exam_wechat_archive", 57),
        ("external_handout", 29),
    ]
    assert [
        (item["value"], item["atomic_count"])
        for item in supplemental["facets"]["year"]["values"]
    ] == [("2026", 47), ("unknown", 39)]
    assert {
        item["value"]: item["atomic_count"]
        for item in supplemental["facets"]["region"]["values"]
    } == {
        "unknown": 57,
        "虹口区（仅文章标题归属；卷面未署区）": 19,
        "金山区（仅文章标题归属；卷面未署区）": 10,
    }

    handout = QuestionSearchWorkbench().search(
        {
            "scope": "supplemental",
            "filters": {"source_tier": ["external_handout"]},
            "limit": 50,
        },
        theme_loader=loader,
        snapshot_id="4" * 64,
    )
    assert handout["counts"]["theme_cards_matched"] == 4
    assert handout["counts"]["atomic_parts_matched"] == 29
    assert all(
        item["source_metadata"]["source_tier"] == "external_handout"
        for item in handout["items"]
    )


def test_real_curriculum_index_joins_exactly_zero_30_57_by_scope(
    real_theme_projections,
) -> None:
    curriculum = CurriculumWorkbenchReader(SHCHEM_ROOT)
    response = QuestionSearchWorkbench().search(
        {"scope": "all", "curriculum": {}, "limit": 50},
        theme_loader=lambda scope: real_theme_projections[scope],
        curriculum_loader=lambda selector: curriculum.search(**selector),
        snapshot_id="6" * 64,
    )
    assert response["curriculum"]["allowed_atomic_counts_by_scope"] == {
        "master": 30,
        "wave1": 0,
        "supplemental": 57,
    }
    assert response["counts"]["atomic_parts_matched"] == 87
    assert response["scope_counts"]["wave1"]["atomic_parts_matched"] == 0
    assert response["scope_counts"]["master"]["atomic_parts_matched"] == 30
    assert response["scope_counts"]["supplemental"]["atomic_parts_matched"] == 57
    assert all(
        "curriculum_explicit_mapping_match" in detail["reason_codes"]
        for card in response["items"]
        for detail in card["match_details"]
    )


class _FrozenThemes:
    def __init__(self, projection: dict, curriculum: dict | None = None):
        self.projection = projection
        self.curriculum = curriculum or curriculum_projection()
        self.calls: list[str] = []
        self.curriculum_calls: list[dict[str, str]] = []

    def manifest(self) -> dict:
        return {"browse_snapshot_id": "7" * 64}

    def theme_groups(self, scope: str) -> dict:
        self.calls.append(scope)
        value = deepcopy(self.projection)
        value["scope"] = scope
        return value

    def curriculum_search(self, **selector: str) -> dict:
        self.curriculum_calls.append(dict(selector))
        return deepcopy(self.curriculum)

    def curriculum_catalog(self) -> dict:
        return {"counts": {"volumes": 5, "chapters": 19, "sections": 60}}


class _NoLiveFallback:
    def __getattr__(self, name: str):
        raise AssertionError(f"live fallback attempted: {name}")


def browser_headers(server) -> dict[str, str]:
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return {"Origin": origin, "Sec-Fetch-Site": "same-origin"}


def test_http_post_is_read_only_and_uses_only_the_selected_frozen_themes() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        audit_path = config.state_root / "audit" / "gateway.jsonl"
        with running_server(config) as server:
            frozen = _FrozenThemes(theme_projection())
            server.service.frozen_browse = frozen
            server.service.workbench_product_registry = _NoLiveFallback()
            server.service.theme_workbench = _NoLiveFallback()
            headers = browser_headers(server)

            status, body, _ = request(
                server,
                "POST",
                "/api/v1/questions/search",
                headers=headers,
                payload={
                    "scope": "master",
                    "filters": {
                        "item_type": ["organic_structure_or_route"],
                        "answer_status": ["absent"],
                    },
                },
            )
            assert status == 200
            assert body["data"]["items"][0]["theme"]["title"] == "有机合成"
            assert frozen.calls == ["master"]
            assert not audit_path.exists()

            status, body, _ = request(
                server,
                "POST",
                "/api/v1/questions/search?scope=master",
                headers=headers,
                payload={},
            )
            assert status == 400
            assert body["error"]["code"] == (
                "question_search_query_parameters_unsupported"
            )
            status, body, _ = request(
                server,
                "POST",
                "/api/v1/questions/search",
                payload={"scope": "master"},
            )
            assert status == 403
            assert body["error"]["code"] == "origin_required"


def test_http_curriculum_search_is_frozen_only_and_old_snapshot_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        with running_server(config) as server:
            frozen = _FrozenThemes(theme_projection())
            server.service.frozen_browse = frozen
            server.service.curriculum_workbench = _NoLiveFallback()
            server.service.workbench_product_registry = _NoLiveFallback()
            server.service.theme_workbench = _NoLiveFallback()
            headers = browser_headers(server)
            status, body, _ = request(
                server,
                "POST",
                "/api/v1/questions/search",
                headers=headers,
                payload={
                    "scope": "master",
                    "curriculum": {
                        "volume_id": "TB-E1",
                        "mapping_status": "complete",
                    },
                },
            )
            assert status == 200
            assert body["data"]["data_snapshot_id"] == "7" * 64
            assert frozen.curriculum_calls == [
                {"volume_id": "TB-E1", "mapping_status": "complete"}
            ]
            assert not (config.state_root / "audit" / "gateway.jsonl").exists()

            class _LegacyFrozen:
                def theme_groups(self, scope: str) -> dict:
                    return theme_projection(scope)

            server.service.frozen_browse = _LegacyFrozen()
            status, body, _ = request(
                server,
                "POST",
                "/api/v1/questions/search",
                headers=headers,
                payload={
                    "scope": "master",
                    "curriculum": {"volume_id": "TB-E1"},
                },
            )
            assert status == 503
            assert body["error"]["code"] == "browse_snapshot_runtime_incompatible"


def test_live_http_textbooks_and_all_explicit_mappings_share_one_service() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        audit_path = config.state_root / "audit" / "gateway.jsonl"
        with running_server(config) as server:
            headers = browser_headers(server)
            status, body, _ = request(
                server,
                "GET",
                "/api/v1/textbooks",
                headers=headers,
                timeout=60,
            )
            assert status == 200
            assert body["data"]["counts"]["volumes"] == 5
            assert body["data"]["counts"]["chapters"] == 19
            assert body["data"]["counts"]["sections"] == 60

            status, body, _ = request(
                server,
                "POST",
                "/api/v1/questions/search",
                headers=headers,
                payload={"scope": "all", "curriculum": {}, "limit": 50},
                timeout=60,
            )
            assert status == 200
            assert body["data"]["curriculum"][
                "allowed_atomic_counts_by_scope"
            ] == {"master": 30, "wave1": 0, "supplemental": 57}
            assert body["data"]["counts"]["atomic_parts_matched"] == 87
            assert not audit_path.exists()


def test_request_rejects_unknown_fields_duplicate_filters_and_bad_limits() -> None:
    workbench = QuestionSearchWorkbench()
    loader = lambda scope: theme_projection()
    invalid = (
        ({"unsafe": True}, "question_search_request_invalid"),
        ({"curriculum": {"volume_id": ""}}, "question_search_curriculum_invalid"),
        (
            {"curriculum": {"mapping_status": "guessed"}},
            "question_search_curriculum_invalid",
        ),
        (
            {"curriculum": {"volume_id": "TB-E1", "unsafe": "x"}},
            "question_search_curriculum_invalid",
        ),
        (
            {"filters": {"K": ["K10", "K10"]}},
            "question_search_filter_invalid",
        ),
        ({"filters": {"unsupported": ["x"]}}, "question_search_filter_invalid"),
        ({"limit": 0}, "question_search_limit_invalid"),
    )
    for payload, code in invalid:
        with pytest.raises(QuestionSearchError) as captured:
            workbench.search(payload, theme_loader=loader)
        assert captured.value.code == code

    serialized = json.dumps(
        workbench.search({}, theme_loader=loader), ensure_ascii=False
    ).casefold()
    for forbidden in (
        '"answer_text":',
        '"question_text":',
        '"source_path":',
        '"local_path":',
        "http://",
        "https://",
        '"student_id":',
    ):
        assert forbidden not in serialized
