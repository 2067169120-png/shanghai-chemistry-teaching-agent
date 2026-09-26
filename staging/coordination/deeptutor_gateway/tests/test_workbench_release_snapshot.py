from __future__ import annotations

import base64
import json
import os
import shutil
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
from integrations.deeptutor_shchem_v1.config import CONTRACT_VERSION, Principal
from integrations.deeptutor_shchem_v1.curriculum_workbench import (
    CurriculumWorkbenchReader,
)
from integrations.deeptutor_shchem_v1.material_intake_workbench import (
    MaterialIntakeWorkbenchReader,
)
from integrations.deeptutor_shchem_v1.service import GatewayService
from integrations.deeptutor_shchem_v1.workbench_product_registry import REGISTRY_ID
from integrations.deeptutor_shchem_v1.workbench_release_snapshot import (
    AUTHORITY,
    CURRICULUM_CATALOG_ROUTE,
    CURRICULUM_MAPPING_INDEX_ROUTE,
    DEFAULT_BACKEND_SOURCE_RELATIVES,
    DYNAMIC_DOMAINS_EXCLUDED,
    DYNAMIC_ROUTE_PREFIXES,
    MANIFEST_RELATIVE,
    MATERIAL_INTAKE_SOURCE_FILES,
    REVIEW_TASK_CATALOG_ARTIFACT,
    ROUTE_INDEX_RELATIVE,
    BrowseCapture,
    FrozenWorkbenchBrowseReader,
    WorkbenchReleaseSnapshotError,
    WorkbenchReleaseSnapshotMaterializer,
    current_backend_build_id,
    verify_materialized_snapshot,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SCHEMA_SOURCE = (
    WORKSPACE
    / "sh-chem-db/kb/workbench/workbench_release_snapshot_v1/"
    "browse_snapshot.schema.json"
)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@lru_cache(maxsize=1)
def _material_intake_fixture() -> tuple[
    dict[str, dict[str, Any]], dict[str, bytes], dict[str, int]
]:
    reader = MaterialIntakeWorkbenchReader(WORKSPACE / "sh-chem-db")
    snapshot = reader._load()
    reader._load = lambda snapshot=snapshot: snapshot
    status = reader.status()
    batches = reader.list_batches()
    routes: dict[str, dict[str, Any]] = {
        "/api/v1/intake/status": status,
        "/api/v1/intake/batches": batches,
    }
    for item in batches["items"]:
        batch_id = item["batch_id"]
        routes[f"/api/v1/intake/batches/{quote(batch_id, safe='')}"] = (
            reader.batch_detail(batch_id)
        )
    total = status["counts"]["entity_record_count_non_additive"]
    page_count = 0
    for offset in range(0, total, 200):
        routes[f"/api/v1/intake/records?limit=200&offset={offset}"] = (
            reader.list_records(
                kind=None,
                stage=None,
                status=None,
                query=None,
                limit=200,
                offset=offset,
            )
        )
        page_count += 1
    sources = {
        key: (WORKSPACE / source_relative).read_bytes()
        for key, (_, source_relative) in MATERIAL_INTAKE_SOURCE_FILES.items()
    }
    return routes, sources, {
        "records": total,
        "batches": 3,
        "batch_details": 3,
        "record_pages": page_count,
    }


def _material_intake_routes() -> dict[str, dict[str, Any]]:
    return deepcopy(_material_intake_fixture()[0])


def _material_intake_sources() -> dict[str, bytes]:
    return dict(_material_intake_fixture()[1])


@lru_cache(maxsize=1)
def _curriculum_fixture() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, tuple[str, ...]]
]:
    reader = CurriculumWorkbenchReader(WORKSPACE / "sh-chem-db")
    catalog = reader.catalog()
    mapping_index = reader.mapping_index()
    by_scope: dict[str, list[str]] = {
        "wave1": [],
        "master": [],
        "supplemental": [],
    }
    for record in mapping_index["records"]:
        scope = {
            "master_direct_active": "master",
            "supplemental_wechat_active": "supplemental",
        }[record["source_layer"]]
        by_scope[scope].append(record["atomic_id"])
    return catalog, mapping_index, {
        scope: tuple(values) for scope, values in by_scope.items()
    }


def _curriculum_theme_projection(scope: str) -> dict[str, Any]:
    atomic_ids = _curriculum_fixture()[2][scope]
    return {
        "scope": scope,
        "papers": (
            []
            if not atomic_ids
            else [
                {
                    "paper": {"id": f"CURRICULUM-{scope}"},
                    "theme_groups": [
                        {
                            "atomic_chain": [
                                {"atomic_part_id": atomic_id}
                                for atomic_id in atomic_ids
                            ]
                        }
                    ],
                }
            ]
        ),
        "unassigned_pending_review": {"atomic_chain": []},
        "authority": deepcopy(AUTHORITY),
    }


class _Source:
    def __init__(self, capture: BrowseCapture):
        self.value = capture

    def capture(self) -> BrowseCapture:
        return deepcopy(self.value)


def _page(items: list[dict[str, Any]], *, scope: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "items": items,
        "count": len(items),
        "total": len(items),
        "limit": 200,
        "offset": 0,
    }
    if scope == "wave1":
        value.update(
            {
                "schema_version": "test.wave.list",
                "source_namespace": "candidate_review_only",
                "filters": {
                    "node_type": "atomic_part",
                    "query": "",
                    "paper_id": None,
                    "theme_id": None,
                    "printed_question_id": None,
                },
                "authority": deepcopy(AUTHORITY),
            }
        )
    elif scope == "master":
        value.update(
            {
                "product_id": "TEST-MASTER",
                "scope": "candidate_only",
                "read_only": True,
                "authority": deepcopy(AUTHORITY),
                "integrity": {"fail_closed": True},
                "visual_scan_coverage": {"dynamic": True},
            }
        )
    else:
        value.update(
            {
                "schema_version": "test.supp.list",
                "scope": "supplemental",
                "data_snapshot_id": "2" * 64,
            }
        )
    return value


def _capture(*, marker: str = "A") -> BrowseCapture:
    wave_id = "W1"
    master_ids = ["M1", "D1", "A1"]
    supplemental_id = "S1"
    wave_crop = (
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes/atomic_part/"
        "W1/question-crops/CW"
    )
    direct_crop = (
        "/api/v1/kb/workbench/master-direct-scans/D1/question-crops/CD"
    )
    alias_crop = (
        "/api/v1/kb/workbench/master-visual-scan-aliases/A1/question-crops/CA"
    )
    supplemental_crop = (
        "/api/v1/kb/workbench/supplemental-scans/S1/question-crops/CS"
    )
    json_routes: dict[str, Any] = {
        "/api/v1/readiness": {
            "schema_version": "test.readiness",
            "status": "ready_candidate_browse",
            "api_contract_version": CONTRACT_VERSION,
            "ui_build_id": "1" * 64,
            "data_snapshot_id": "2" * 64,
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/workbench/product-registry": {
            "registry_id": REGISTRY_ID,
            "api_contract_version": CONTRACT_VERSION,
            "ui_build_id": "1" * 64,
            "data_snapshot_id": "2" * 64,
            "manifest_sha256": "3" * 64,
            "products": ["wave1", "master", "supplemental"],
            "marker": marker,
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/theme-groups?scope=wave1": _curriculum_theme_projection("wave1"),
        "/api/v1/kb/workbench/theme-groups?scope=master": _curriculum_theme_projection("master"),
        "/api/v1/kb/workbench/theme-groups?scope=supplemental": _curriculum_theme_projection("supplemental"),
        CURRICULUM_CATALOG_ROUTE: deepcopy(_curriculum_fixture()[0]),
        CURRICULUM_MAPPING_INDEX_ROUTE: deepcopy(_curriculum_fixture()[1]),
        "/api/v1/kb/question-processing-progress?limit=100&offset=0&scope=wave1": {
            "scope": "wave1",
            "items": [
                {
                    "paper": {"id": "PAPER1"},
                    "theme": {"id": "THEME1"},
                    "next_gaps": [],
                }
            ],
            "count": 1,
            "total": 1,
        },
        "/api/v1/kb/question-processing-progress?limit=100&offset=0&scope=master": {
            "scope": "master",
            "items": [
                {
                    "paper": {"id": "MPAPER"},
                    "theme": {"id": "MTHEME"},
                    "next_gaps": [{"category": "visual_scan"}],
                }
            ],
            "count": 1,
            "total": 1,
        },
        "/api/v1/kb/sources/candidate_review_only/wave1/status": {
            "counts": {"atomic_parts": 1},
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes?limit=200&offset=0&node_type=atomic_part": _page(
            [
                {
                    "node_type": "atomic_part",
                    "node_id": wave_id,
                    "task_summary": "电化学测试题",
                    "parent_chain": [
                        {"node_type": "paper", "node_id": "PAPER1"},
                        {"node_type": "theme_big_question", "node_id": "THEME1"},
                        {"node_type": "printed_question", "node_id": "PRINT1"},
                    ],
                }
            ],
            scope="wave1",
        ),
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes?limit=200&offset=0": _page(
            [
                {
                    "node_type": "paper",
                    "node_id": "PAPER1",
                    "title": "测试卷",
                    "parent_chain": [],
                },
                {
                    "node_type": "atomic_part",
                    "node_id": wave_id,
                    "task_summary": "电化学测试题",
                    "parent_chain": [
                        {"node_type": "paper", "node_id": "PAPER1"},
                        {"node_type": "theme_big_question", "node_id": "THEME1"},
                        {"node_type": "printed_question", "node_id": "PRINT1"},
                    ],
                },
            ],
            scope="wave1",
        ),
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes/atomic_part/W1": {
            "node_id": wave_id,
            "crop_refs": {
                "items": [
                    {
                        "crop_id": "CW",
                        "sha256": __import__("hashlib").sha256(PNG).hexdigest(),
                        "bytes": len(PNG),
                        "image_endpoint": wave_crop,
                    }
                ]
            },
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes/paper/PAPER1": {
            "node_type": "paper",
            "node_id": "PAPER1",
            "title": "测试卷",
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/question-visual-scans/status": {
            "counts": {"atomic_parts": 1},
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/question-visual-scans/catalog": {
            "node_ids": [wave_id],
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/question-visual-scans/W1": {
            "node_id": wave_id,
            "visible_summary_zh": "测试题",
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/master-atomic/status": {
            "counts": {"master_atomic_inventory": 3},
            "visual_scan_coverage": {"dynamic": True},
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/master-atomic?limit=200&offset=0": _page(
            [{"node_type": "atomic_part", "node_id": value} for value in master_ids],
            scope="master",
        ),
        "/api/v1/kb/workbench/master-direct-scans/status": {
            "counts": {"atomic_parts": 1},
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/master-direct-scans/catalog": {
            "master_node_ids": ["D1"],
            "items": [{"master_node_id": "D1"}],
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/master-direct-scans/D1": {
            "master_node_id": "D1",
            "evidence_descriptors": [
                {
                    "crop_id": "CD",
                    "sha256": __import__("hashlib").sha256(PNG).hexdigest(),
                    "bytes": len(PNG),
                }
            ],
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/master-visual-scan-aliases/status": {
            "counts": {"atomic_parts": 1},
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/master-visual-scan-aliases/catalog": {
            "master_node_ids": ["A1"],
            "items": [{"master_node_id": "A1"}],
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/master-visual-scan-aliases/A1": {
            "master_node_id": "A1",
            "question_evidence": {
                "crop_id": "CA",
                "sha256": __import__("hashlib").sha256(PNG).hexdigest(),
                "bytes": len(PNG),
            },
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/supplemental-scans/status": {
            "counts": {"atomic_parts": 1},
            "authority": deepcopy(AUTHORITY),
        },
        "/api/v1/kb/workbench/supplemental-scans?limit=200&offset=0": _page(
            [{"node_type": "atomic_part", "node_id": supplemental_id}],
            scope="supplemental",
        ),
        "/api/v1/kb/workbench/supplemental-scans/S1": {
            "node": {
                "node_id": supplemental_id,
                "crop_refs": {
                    "items": [
                        {
                            "crop_id": "CS",
                            "sha256": __import__("hashlib").sha256(PNG).hexdigest(),
                            "bytes": len(PNG),
                            "image_endpoint": supplemental_crop,
                        }
                    ]
                },
            },
            "authority": deepcopy(AUTHORITY),
        },
    }
    json_routes.update(_material_intake_routes())
    for node_id in master_ids:
        json_routes[f"/api/v1/kb/workbench/master-atomic/{node_id}"] = {
            "node": {"node_id": node_id},
            "authority": deepcopy(AUTHORITY),
        }
    payload = CandidateCropPayload(
        data=PNG,
        sha256=__import__("hashlib").sha256(PNG).hexdigest(),
    )
    return BrowseCapture(
        json_routes=json_routes,
        binary_routes={
            wave_crop: payload,
            direct_crop: payload,
            alias_crop: payload,
            supplemental_crop: payload,
        },
        counts={
            "atomic_details_by_scope": {
                "wave1": 1,
                "master": 3,
                "supplemental": 1,
            },
            "wave1_all_hierarchy_nodes": 2,
            "source_status_routes": 6,
            "catalog_routes": 3,
            "theme_routes": 3,
            "master_crosswalk_atomic_records": 3,
            "direct_visual_details": 1,
            "alias_visual_details": 1,
            "question_crop_routes": 4,
            "material_intake_records": _material_intake_fixture()[2]["records"],
            "material_intake_batches": 3,
            "material_intake_batch_details": 3,
            "material_intake_record_pages": _material_intake_fixture()[2][
                "record_pages"
            ],
            "curriculum_volumes": 5,
            "curriculum_chapters": 19,
            "curriculum_sections": 60,
            "curriculum_atomic_mappings": 87,
            "curriculum_mapping_entries": 163,
            "curriculum_atomic_mappings_by_scope": {
                "wave1": 0,
                "master": 30,
                "supplemental": 57,
            },
            "json_routes": len(json_routes),
        },
        identity={
            "registry_id": REGISTRY_ID,
            "api_contract_version": CONTRACT_VERSION,
            "ui_build_id": "1" * 64,
            "data_snapshot_id": "2" * 64,
            "registry_manifest_sha256": "3" * 64,
        },
    )


@pytest.fixture()
def fixture_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    overlay = root / "runtime/deeptutor_shchem/overlay"
    overlay.mkdir(parents=True)
    static = {
        "app.js": b"'use strict';\n",
        "index.html": b"<!doctype html><html lang=\"zh-CN\"></html>\n",
        "styles.css": b"body { color: #111; }\n",
    }
    import hashlib

    files = {}
    for name, raw in static.items():
        (overlay / name).write_bytes(raw)
        files[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    manifest = {
        "gateway_contract": CONTRACT_VERSION,
        "mount_strategy": "loopback_sidecar",
        "files": files,
    }
    (overlay / "overlay.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (overlay.parent / "overlay.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    contract = root / "contracts"
    contract.mkdir()
    (contract / "gateway.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: test\n  version: 1\n", encoding="utf-8"
    )
    shutil.copyfile(SCHEMA_SOURCE, contract / "browse_snapshot.schema.json")
    (root / "backend.py").write_text("BUILD = 'test'\n", encoding="utf-8")
    return root


def _materializer(
    workspace: Path, factory: Any
) -> WorkbenchReleaseSnapshotMaterializer:
    return WorkbenchReleaseSnapshotMaterializer(
        workspace,
        capture_factory=factory,
        openapi_relative="contracts/gateway.yaml",
        schema_relative="contracts/browse_snapshot.schema.json",
        backend_source_relatives=("backend.py",),
        material_intake_source_factory=_material_intake_sources,
    )


def test_schema_is_strict_draft_2020_12_and_backend_identity_includes_release_chain():
    schema = json.loads(SCHEMA_SOURCE.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["dynamic_state_embedded"] == {"const": False}
    assert schema["properties"]["answer_pixels_embedded"] == {"const": False}
    assert schema["properties"]["dynamic_domains_excluded"] == {
        "const": list(DYNAMIC_DOMAINS_EXCLUDED)
    }
    assert "integrations/deeptutor_shchem_v1/workbench_release_control.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/workbench_release_gateway.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/workbench_release_regression.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/theme_review_workbench.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/public_kb.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/paper_export_renderer.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/paper_export_workbench.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/paper_format_presets.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/presentation_jobs.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/presentation_theme_adapter.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "integrations/deeptutor_shchem_v1/presentation_workbench.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert (
        "integrations/deeptutor_shchem_v1/master_parent_chain_repair_overlay.py"
        in DEFAULT_BACKEND_SOURCE_RELATIVES
    )
    assert (
        "integrations/deeptutor_shchem_v1/huangpu2025_theme4_direct_visual_scan.py"
        in DEFAULT_BACKEND_SOURCE_RELATIVES
    )
    assert (
        "integrations/deeptutor_shchem_v1/question_search_workbench.py"
        in DEFAULT_BACKEND_SOURCE_RELATIVES
    )
    assert (
        "integrations/deeptutor_shchem_v1/curriculum_workbench.py"
        in DEFAULT_BACKEND_SOURCE_RELATIVES
    )
    assert (
        "integrations/deeptutor_shchem_v1/material_intake_workbench.py"
        in DEFAULT_BACKEND_SOURCE_RELATIVES
    )
    assert "integrations/shchem_review_workbench_v1/store.py" in DEFAULT_BACKEND_SOURCE_RELATIVES
    assert "mutable_prep_export_jobs_and_artifacts" in DYNAMIC_DOMAINS_EXCLUDED
    assert "mutable_presentation_projects_jobs_and_artifacts" in DYNAMIC_DOMAINS_EXCLUDED
    assert "/api/v1/prep/exports" in DYNAMIC_ROUTE_PREFIXES
    assert "/api/v1/presentations" in DYNAMIC_ROUTE_PREFIXES
    assert "/api/v1/review" in DYNAMIC_ROUTE_PREFIXES
    assert "material_intake_sources" in schema["required"]
    assert schema["properties"]["material_intake_sources"] == {
        "$ref": "#/$defs/material_intake_sources"
    }
    for field in (
        "curriculum_volumes",
        "curriculum_chapters",
        "curriculum_sections",
        "curriculum_atomic_mappings",
        "curriculum_mapping_entries",
        "curriculum_atomic_mappings_by_scope",
    ):
        assert field in schema["properties"]["counts"]["required"]


def test_default_materializer_builds_the_real_frozen_review_task_catalog():
    materializer = WorkbenchReleaseSnapshotMaterializer(WORKSPACE)
    raw = materializer.review_catalog_factory()
    catalog = json.loads(raw)
    assert catalog["schema_version"] == "shchem_gateway_theme_review_task_catalog_v2"
    assert catalog["counts"] == {
        "tasks": 147,
        "theme_big_question_tasks": 48,
        "paper_theme_boundary_review_tasks": 99,
        "candidate_theme_boundary_tasks": 5,
        "atomic_boundary_only_tasks": 94,
        "target_printed_questions": 501,
        "target_atomic_parts": 470,
        "missing_theme_printed_questions": 135,
        "candidate_group_printed_questions": 41,
        "candidate_group_atomic_parts": 43,
        "no_atomic_boundary_printed_questions": 94,
    }
    assert catalog["authority"]["candidate_only"] is True
    assert catalog["authority"]["central_master_mutated"] is False
    assert catalog["integrity"]["mutable_store_excluded"] is True
    crosswalk = catalog["source_snapshot"]["source_version_crosswalk"]
    assert crosswalk["record_count"] == 3
    receipt = crosswalk["upstream_verification_receipt"]
    assert receipt["verified"] is True
    assert receipt["catalog_row_count"] == 108
    assert receipt["corpus_row_count"] == 108
    assert receipt["paper_check_count"] == 3
    assert receipt["upstream_file_count"] == 9
    assert receipt["receipt_sha256"] == (
        "10881cc56929cfac4fba6324062a2dc926a31a7ac2a8683ba8e628110050d37e"
    )
    candidate_tasks = [
        task for task in catalog["tasks"] if task["source_binding_candidates"]
    ]
    assert len(candidate_tasks) == 3
    assert {task["paper_id"] for task in candidate_tasks} == {
        "MASTER-PAPER-21b2686786eef90582ca",
        "MASTER-PAPER-4a39a5ecb376c90f8ed7",
        "MASTER-PAPER-eca872096473cd6ec91a",
    }
    assert sum(
        candidate["accept_allowed"] is True
        for task in candidate_tasks
        for candidate in task["source_binding_candidates"]
    ) == 1


def test_materializer_closes_details_crops_static_contracts_and_runtime_reader(
    fixture_workspace: Path,
):
    artifacts = _materializer(
        fixture_workspace, lambda: _Source(_capture())
    ).materialize()
    verified = verify_materialized_snapshot(artifacts)
    assert verified["valid"] is True
    assert verified["atomic_details_by_scope"] == {
        "wave1": 1,
        "master": 3,
        "supplemental": 1,
    }
    assert verified["binary_route_count"] == 4
    assert verified["unique_crop_assets"] == 1
    assert verified["material_intake"] == _material_intake_fixture()[2]
    assert verified["curriculum"] == {
        "volumes": 5,
        "chapters": 19,
        "sections": 60,
        "atomic_mappings": 87,
        "mapping_entries": 163,
        "atomic_mappings_by_scope": {
            "wave1": 0,
            "master": 30,
            "supplemental": 57,
        },
    }
    assert {"webui/app.js", "webui/index.html", "webui/styles.css"} <= set(artifacts)
    assert {
        "manifests/overlay.inner.json",
        "manifests/overlay.outer.json",
        "contracts/gateway_openapi_v1.yaml",
        "snapshot/backend-build.json",
        REVIEW_TASK_CATALOG_ARTIFACT,
        ROUTE_INDEX_RELATIVE,
        MANIFEST_RELATIVE,
        *(artifact for artifact, _ in MATERIAL_INTAKE_SOURCE_FILES.values()),
    } <= set(artifacts)

    reader = FrozenWorkbenchBrowseReader.from_artifacts(artifacts)
    review_catalog = json.loads(reader.review_task_catalog_bytes())
    assert review_catalog["catalog_available"] is False
    assert review_catalog["dynamic_state_embedded"] is False
    assert reader.registry()["registry_id"] == REGISTRY_ID
    assert reader.readiness()["status"] == "ready_candidate_browse"
    assert reader.theme_groups("master")["scope"] == "master"
    assert reader.curriculum_catalog()["counts"]["volumes"] == 5
    section = reader.curriculum_search(section="TB-M1-C1:1.2")
    assert section["query"]["resolved_section_key"] == "TB-M1-C1:1.2"
    assert section["counts"]["matched_atomic_count"] == len(
        section["atomic_ids"]
    )
    assert section["atomic_ids"] == [
        item["atomic_id"] for item in section["items"]
    ]
    assert section["counts"]["matched_entry_count"] == sum(
        item["matched_entry_count"] for item in section["items"]
    )
    assert section["counts"]["source_group_count"] == len(section["groups"])
    assert sorted(section["atomic_ids"]) == sorted(
        atomic_id
        for group in section["groups"]
        for atomic_id in group["atomic_ids"]
    )
    assert all(
        group["atomic_count"] == len(group["atomic_ids"])
        for group in section["groups"]
    )
    progress = reader.question_processing_progress(
        scope="master",
        paper_id="MPAPER",
        theme_id=None,
        gap="visual_scan",
        limit=1,
        offset=0,
    )
    assert progress["count"] == progress["total"] == 1
    assert progress["items"][0]["theme"]["id"] == "MTHEME"
    assert reader.static("index.html").data.startswith(b"<!doctype html>")
    assert reader.json("/api/v1/kb/workbench/master-atomic/M1")["node"]["node_id"] == "M1"
    crop = reader.crop(
        "/api/v1/kb/workbench/master-direct-scans/D1/question-crops/CD"
    )
    assert crop.data == PNG
    assert crop.sha256 == __import__("hashlib").sha256(PNG).hexdigest()

    page = reader.json("/api/v1/kb/workbench/master-atomic?limit=1&offset=1")
    assert page["limit"] == 1
    assert page["offset"] == 1
    assert page["total"] == 3
    assert [item["node_id"] for item in page["items"]] == ["D1"]
    wave_page = reader.json(
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes"
        "?offset=0&limit=1&node_type=atomic_part"
    )
    assert wave_page["items"][0]["node_id"] == "W1"
    filtered = reader.candidate_list(
        node_type="atomic_part",
        query="电化学",
        paper_id="PAPER1",
        theme_id="THEME1",
        printed_question_id="PRINT1",
        limit=20,
        offset=0,
    )
    assert filtered["total"] == 1
    assert filtered["filters"] == {
        "node_type": "atomic_part",
        "query": "电化学",
        "paper_id": "PAPER1",
        "theme_id": "THEME1",
        "printed_question_id": "PRINT1",
    }
    via_route = reader.json(
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes"
        "?limit=20&offset=0&node_type=atomic_part&q=%E7%94%B5%E5%8C%96%E5%AD%A6"
        "&paper_id=PAPER1&theme_id=THEME1&printed_question_id=PRINT1"
    )
    assert via_route["total"] == 1
    paper_page = reader.candidate_list(node_type="paper", limit=20, offset=0)
    assert paper_page["total"] == 1
    assert paper_page["items"][0]["node_id"] == "PAPER1"
    all_nodes = reader.candidate_list(node_type=None, limit=20, offset=0)
    assert all_nodes["total"] == 2
    assert len(reader.catalog_items("master")) == 3
    assert reader.json("/api/v1/intake/status")["counts"][
        "entity_record_count_non_additive"
    ] == _material_intake_fixture()[2]["records"]
    intake_page = reader.material_intake_records(
        kind="teaching_document",
        stage=None,
        status=None,
        query="原卷版",
        limit=3,
        offset=0,
    )
    assert intake_page["count"] == 3
    assert intake_page["total"] == 98
    via_intake_route = reader.json(
        "/api/v1/intake/records?kind=teaching_document&limit=3&offset=0"
        "&q=%E5%8E%9F%E5%8D%B7%E7%89%88"
    )
    assert via_intake_route == intake_page
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        reader.json(
            "/api/v1/kb/sources/candidate_review_only/wave1/nodes"
            "?limit=1&offset=0&node_type=unknown"
        )
    assert captured.value.code == "browse_snapshot_query_invalid"

    expected_build = current_backend_build_id(
        fixture_workspace,
        openapi_relative="contracts/gateway.yaml",
        backend_source_relatives=("backend.py",),
    )
    assert reader.assert_current_backend(
        fixture_workspace,
        openapi_relative="contracts/gateway.yaml",
        backend_source_relatives=("backend.py",),
    ) == expected_build
    (fixture_workspace / "backend.py").write_text("BUILD = 'new!'\n", encoding="utf-8")
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        reader.assert_current_backend(
            fixture_workspace,
            openapi_relative="contracts/gateway.yaml",
            backend_source_relatives=("backend.py",),
        )
    assert captured.value.code == "browse_snapshot_backend_build_mismatch"


def test_curriculum_routes_are_required_for_new_capture_and_join_fail_closed(
    fixture_workspace: Path,
) -> None:
    missing = _capture()
    missing_routes = dict(missing.json_routes)
    missing_routes.pop(CURRICULUM_MAPPING_INDEX_ROUTE)
    missing = BrowseCapture(
        missing_routes,
        missing.binary_routes,
        missing.counts,
        missing.identity,
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(missing)).materialize()
    assert captured.value.code == "browse_snapshot_capture_incomplete"

    malformed = _capture()
    malformed_routes = deepcopy(dict(malformed.json_routes))
    del malformed_routes[CURRICULUM_MAPPING_INDEX_ROUTE]["records"][0][
        "mapping_status"
    ]
    malformed = BrowseCapture(
        malformed_routes,
        malformed.binary_routes,
        malformed.counts,
        malformed.identity,
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(malformed)).materialize()
    assert captured.value.code == "browse_snapshot_curriculum_invalid"

    for section_name, field, value in (
        ("authority", "teaching_use_allowed", True),
        ("integrity", "fail_closed", False),
    ):
        escalated = _capture()
        escalated_routes = deepcopy(dict(escalated.json_routes))
        for route in (CURRICULUM_CATALOG_ROUTE, CURRICULUM_MAPPING_INDEX_ROUTE):
            escalated_routes[route][section_name][field] = value
        escalated = BrowseCapture(
            escalated_routes,
            escalated.binary_routes,
            escalated.counts,
            escalated.identity,
        )
        with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
            _materializer(
                fixture_workspace, lambda escalated=escalated: _Source(escalated)
            ).materialize()
        assert captured.value.code == "browse_snapshot_curriculum_invalid"

    mismatched = _capture()
    mismatched_routes = deepcopy(dict(mismatched.json_routes))
    index = mismatched_routes[CURRICULUM_MAPPING_INDEX_ROUTE]
    first = index["records"][0]
    first["source_layer"] = "supplemental_wechat_active"
    first["source_batch"] = "tampered-cross-scope"
    mismatched = BrowseCapture(
        mismatched_routes,
        mismatched.binary_routes,
        mismatched.counts,
        mismatched.identity,
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(mismatched)).materialize()
    assert captured.value.code in {
        "browse_snapshot_curriculum_count_mismatch",
        "browse_snapshot_curriculum_join_invalid",
    }


def test_material_intake_batch_closure_tamper_and_dynamic_routes_fail_closed(
    fixture_workspace: Path,
):
    missing = _capture()
    routes = dict(missing.json_routes)
    detail_route = next(
        route
        for route in routes
        if route.startswith("/api/v1/intake/batches/")
    )
    del routes[detail_route]
    missing = BrowseCapture(
        routes, missing.binary_routes, missing.counts, missing.identity
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(missing)).materialize()
    assert captured.value.code == "browse_snapshot_material_intake_incomplete"

    dynamic = _capture()
    routes = dict(dynamic.json_routes)
    routes["/api/v1/intake/uploads/FUTURE-UPLOAD"] = {
        "status": "future_dynamic_state"
    }
    dynamic = BrowseCapture(
        routes, dynamic.binary_routes, dynamic.counts, dynamic.identity
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(dynamic)).materialize()
    assert captured.value.code == "browse_snapshot_dynamic_domain_forbidden"

    artifacts = _materializer(
        fixture_workspace, lambda: _Source(_capture())
    ).materialize()
    mutated = dict(artifacts)
    ledger_artifact = MATERIAL_INTAKE_SOURCE_FILES["ledger"][0]
    ledger_raw = bytearray(mutated[ledger_artifact])
    ledger_raw[-2] ^= 1
    mutated[ledger_artifact] = bytes(ledger_raw)
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        verify_materialized_snapshot(mutated)
    assert captured.value.code == "browse_snapshot_artifact_hash_mismatch"


def test_frozen_service_material_intake_never_reads_live_ledger(
    fixture_workspace: Path,
):
    artifacts = _materializer(
        fixture_workspace, lambda: _Source(_capture())
    ).materialize()
    reader = FrozenWorkbenchBrowseReader.from_artifacts(artifacts)

    class _DriftedLiveLedger:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"live material intake reader was opened: {name}")

    service = GatewayService.__new__(GatewayService)
    service.frozen_browse = reader
    service.material_intake = _DriftedLiveLedger()
    teacher = Principal("release-test-teacher", "teacher", "digest", ())

    status = service.material_intake_status(teacher)
    batches = service.material_intake_batches(teacher)
    assert status == reader.json("/api/v1/intake/status")
    assert batches == reader.json("/api/v1/intake/batches")
    batch_id = batches["items"][0]["batch_id"]
    assert service.material_intake_batch_detail(teacher, batch_id) == reader.json(
        f"/api/v1/intake/batches/{quote(batch_id, safe='')}"
    )
    records = service.material_intake_records(
        teacher,
        kind="teaching_document",
        stage=None,
        status=None,
        query="原卷版",
        limit=3,
        offset=0,
    )
    assert records["count"] == 3
    assert records["total"] == 98
    assert not any(
        route.startswith(("/api/v1/intake/uploads", "/api/v1/intake/attempts"))
        for route in reader.route_keys()
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        reader.json("/api/v1/intake/uploads/FUTURE-UPLOAD")
    assert captured.value.code == "browse_snapshot_route_not_found"


def test_disk_loader_rejects_rogue_missing_and_is_independent_after_load(
    fixture_workspace: Path, tmp_path: Path
):
    artifacts = _materializer(
        fixture_workspace, lambda: _Source(_capture())
    ).materialize()
    closure = tmp_path / "candidate/browse-closure"
    for relative, raw in artifacts.items():
        path = closure.joinpath(*Path(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    reader = FrozenWorkbenchBrowseReader(closure)
    (fixture_workspace / "backend.py").write_text("BUILD = 'changed'\n", encoding="utf-8")
    assert reader.registry()["registry_id"] == REGISTRY_ID
    (closure / "rogue.bin").write_bytes(b"rogue")
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        FrozenWorkbenchBrowseReader(closure)
    assert captured.value.code == "browse_snapshot_artifact_inventory_mismatch"
    (closure / "rogue.bin").unlink()
    (closure / "webui/app.js").unlink()
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        FrozenWorkbenchBrowseReader(closure)
    assert captured.value.code == "browse_snapshot_artifact_inventory_mismatch"


def test_verifier_rejects_rogue_missing_and_same_length_crop_mutation(
    fixture_workspace: Path,
):
    artifacts = _materializer(
        fixture_workspace, lambda: _Source(_capture())
    ).materialize()
    rogue = dict(artifacts)
    rogue["rogue.bin"] = b"rogue"
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        verify_materialized_snapshot(rogue)
    assert captured.value.code == "browse_snapshot_artifact_inventory_mismatch"

    missing = dict(artifacts)
    object_path = next(path for path in missing if path.startswith("objects/json/"))
    del missing[object_path]
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        verify_materialized_snapshot(missing)
    assert captured.value.code == "browse_snapshot_artifact_inventory_mismatch"

    mutated = dict(artifacts)
    image_path = next(path for path in mutated if path.startswith("objects/images/"))
    image = bytearray(mutated[image_path])
    image[-1] ^= 1
    mutated[image_path] = bytes(image)
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        verify_materialized_snapshot(mutated)
    assert captured.value.code == "browse_snapshot_artifact_hash_mismatch"


def test_materializer_rejects_missing_detail_and_dynamic_private_domains(
    fixture_workspace: Path,
):
    missing = _capture()
    routes = dict(missing.json_routes)
    del routes["/api/v1/kb/workbench/master-atomic/M1"]
    missing = BrowseCapture(routes, missing.binary_routes, missing.counts, missing.identity)
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(missing)).materialize()
    assert captured.value.code == "browse_snapshot_detail_missing"

    dynamic_route = _capture()
    routes = dict(dynamic_route.json_routes)
    routes["/api/v1/students"] = {"items": []}
    dynamic_route = BrowseCapture(
        routes, dynamic_route.binary_routes, dynamic_route.counts, dynamic_route.identity
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(dynamic_route)).materialize()
    assert captured.value.code == "browse_snapshot_dynamic_domain_forbidden"

    dynamic_review = _capture()
    routes = dict(dynamic_review.json_routes)
    routes["/api/v1/review/tasks"] = {"items": []}
    dynamic_review = BrowseCapture(
        routes,
        dynamic_review.binary_routes,
        dynamic_review.counts,
        dynamic_review.identity,
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(dynamic_review)).materialize()
    assert captured.value.code == "browse_snapshot_dynamic_domain_forbidden"

    dynamic_field = _capture()
    routes = dict(dynamic_field.json_routes)
    routes["/api/v1/readiness"] = {"api_key": "secret"}
    dynamic_field = BrowseCapture(
        routes, dynamic_field.binary_routes, dynamic_field.counts, dynamic_field.identity
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(dynamic_field)).materialize()
    assert captured.value.code == "browse_snapshot_dynamic_domain_forbidden"


@pytest.mark.parametrize(
    ("route", "payload_kind"),
    (
        ("/api/v1/prep/exports", "json"),
        ("/api/v1/prep/exports/WBEXP-0123456789abcdef0123456789abcdef", "json"),
        (
            (
                "/api/v1/prep/exports/WBEXP-0123456789abcdef0123456789abcdef/"
                "artifacts/student_pdf"
            ),
            "binary",
        ),
    ),
    ids=("export-job-start", "export-job-status", "export-artifact"),
)
def test_materializer_excludes_mutable_prep_export_routes(
    fixture_workspace: Path,
    route: str,
    payload_kind: str,
):
    capture = _capture()
    json_routes = dict(capture.json_routes)
    binary_routes = dict(capture.binary_routes)
    if payload_kind == "json":
        json_routes[route] = {"status": "queued"}
    else:
        binary_routes[route] = CandidateCropPayload(
            data=PNG,
            sha256=__import__("hashlib").sha256(PNG).hexdigest(),
        )
    dynamic_export = BrowseCapture(
        json_routes,
        binary_routes,
        capture.counts,
        capture.identity,
    )

    with pytest.raises(WorkbenchReleaseSnapshotError) as captured_error:
        _materializer(
            fixture_workspace,
            lambda: _Source(dynamic_export),
        ).materialize()
    assert captured_error.value.code == "browse_snapshot_dynamic_domain_forbidden"


def test_two_independent_captures_reject_same_size_projection_drift(
    fixture_workspace: Path,
):
    calls = 0

    def factory() -> _Source:
        nonlocal calls
        calls += 1
        return _Source(_capture(marker="A" if calls == 1 else "B"))

    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, factory).materialize()
    assert captured.value.code == "browse_snapshot_source_drift"
    assert calls == 2


def test_same_size_file_double_read_drift_and_malformed_png_fail_closed(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    original_read_bytes = Path.read_bytes
    backend = (fixture_workspace / "backend.py").absolute()
    backend_reads = 0

    def drifting_read_bytes(path: Path) -> bytes:
        nonlocal backend_reads
        raw = original_read_bytes(path)
        if path.absolute() == backend:
            backend_reads += 1
            if backend_reads == 2:
                replacement = raw.replace(b"test", b"tesu")
                assert len(replacement) == len(raw)
                return replacement
        return raw

    monkeypatch.setattr(Path, "read_bytes", drifting_read_bytes)
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(_capture())).materialize()
    assert captured.value.code == "browse_snapshot_source_drift"
    assert backend_reads == 2
    monkeypatch.undo()

    malformed = _capture()
    broken = b"\x89PNG\r\n\x1a\n" + b"not-a-real-png"
    broken_payload = CandidateCropPayload(
        data=broken,
        sha256=__import__("hashlib").sha256(broken).hexdigest(),
    )
    malformed = BrowseCapture(
        malformed.json_routes,
        {route: broken_payload for route in malformed.binary_routes},
        malformed.counts,
        malformed.identity,
    )
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(malformed)).materialize()
    assert captured.value.code == "browse_snapshot_crop_invalid"


def test_overlay_rogue_and_hardlinked_source_fail_closed(
    fixture_workspace: Path, tmp_path: Path
):
    overlay = fixture_workspace / "runtime/deeptutor_shchem/overlay"
    (overlay / "rogue.txt").write_text("rogue", encoding="utf-8")
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(_capture())).materialize()
    assert captured.value.code == "browse_snapshot_static_inventory_invalid"
    (overlay / "rogue.txt").unlink()

    outside = tmp_path / "linked-app.js"
    os.link(overlay / "app.js", outside)
    with pytest.raises(WorkbenchReleaseSnapshotError) as captured:
        _materializer(fixture_workspace, lambda: _Source(_capture())).materialize()
    assert captured.value.code == "browse_snapshot_source_unsafe"
