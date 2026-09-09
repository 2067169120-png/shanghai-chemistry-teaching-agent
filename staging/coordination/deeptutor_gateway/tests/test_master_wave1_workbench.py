from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1 import master_wave1_workbench as workbench
from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    EXPECTED_COUNTS,
    PRODUCT_RELATIVE,
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from integrations.deeptutor_shchem_v1.question_visual_scan import (
    QuestionVisualScanReader,
)
from integrations.deeptutor_shchem_v1.reference_answer import (
    project_reference_answer,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
PRODUCT = SHCHEM_ROOT / PRODUCT_RELATIVE
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
STUDENT_TOKEN = "master-wave1-student-0123456789"


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


def isolated_root(temp: Path) -> Path:
    root = temp / "sh-chem-db"
    target_product = root / PRODUCT_RELATIVE
    target_product.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PRODUCT, target_product)
    manifest = json.loads((PRODUCT / "manifest.json").read_text(encoding="utf-8"))
    for binding in manifest["source_bindings"]:
        relative = Path(binding["path"])
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SHCHEM_ROOT / relative, target)
    return root


def rebind_mutated_records(root: Path, mutator) -> str:
    product = root / PRODUCT_RELATIVE
    records_path = product / "crosswalk_records.jsonl"
    rows = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mutator(rows)
    raw = b"".join(
        json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
        for row in rows
    )
    records_path.write_bytes(raw)
    manifest_path = product / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    binding = next(
        item
        for item in manifest["output_bindings"]
        if item["path"] == "crosswalk_records.jsonl"
    )
    binding["bytes"] = len(raw)
    binding["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["manifest_self_sha256"] = workbench._manifest_self_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(manifest["manifest_self_sha256"])


def all_items(reader: MasterWave1WorkbenchReader) -> list[dict[str, object]]:
    pages = [reader.list_atomic(limit=200, offset=offset) for offset in (0, 200, 400)]
    assert [page["count"] for page in pages] == [200, 200, 70]
    return [item for page in pages for item in page["items"]]


def test_runtime_verified_status_has_fixed_non_additive_counts_and_closed_gates():
    status = MasterWave1WorkbenchReader(SHCHEM_ROOT).status()
    assert status["counts"] == {
        "master_atomic_inventory": 470,
        "wave1_atomic_inventory_non_additive": 252,
        "exact_identity": 169,
        "candidate_overlay_allowed": 165,
        "label_overlay_blocked": 4,
        "split_wave_rows": 38,
        "split_master_targets": 17,
        "anchor_wave_rows": 45,
        "unmapped_master_atomic": 284,
        "verified_matches": 0,
        "complete_master_parent_chains": 427,
        "incomplete_master_parent_chains": 43,
    }
    assert status["non_additivity"]["must_not_be_added"] is True
    assert status["anchor_boundary"] == {
        "wave_rows": 45,
        "master_endpoint_type": "question_anchor",
        "included_in_master_atomic_details": False,
        "overlay_allowed": False,
    }
    assert status["integrity"] == {
        "manifest_self_sha256": workbench.EXPECTED_MANIFEST_SELF_SHA256,
        "hash_verified_on_read": True,
        "source_binding_count": 31,
        "output_binding_count": 8,
        "fail_closed": True,
    }
    assert status["authority"]["candidate_only"] is True
    assert status["authority"]["read_only"] is True
    assert all(
        value is False
        for key, value in status["authority"].items()
        if key not in {"candidate_only", "read_only"}
    )


def test_all_470_master_atomic_rows_are_unique_classified_and_exactly_chained():
    reader = MasterWave1WorkbenchReader(SHCHEM_ROOT)
    items = all_items(reader)
    assert len(items) == len({item["node_id"] for item in items}) == 470
    assert Counter(item["crosswalk_summary"]["state"] for item in items) == {
        "exact": 169,
        "split": 17,
        "unmapped": 284,
    }
    assert Counter(item["parent_chain"]["complete"] for item in items) == {
        True: 427,
        False: 43,
    }
    for item in items:
        assert set(item["classification"]) == {"K", "A", "C", "R", "RP", "D"}
        assert isinstance(item["classification_status"], str)
        assert "source_layer" in item
        chain = item["parent_chain"]
        assert chain["hierarchy_ids"]["atomic_part_id"] == item["node_id"]
        if not chain["complete"]:
            assert chain["status"] == "missing_parent_pending_review"
            assert chain["missing_parent"] is not None
        if item["crosswalk_summary"]["state"] == "unmapped":
            assert "wave1_node_ids" not in item["crosswalk_summary"]
        else:
            assert item["crosswalk_summary"]["wave1_node_ids"]


def test_overlay_split_blocked_and_unmapped_details_stay_namespaced_and_path_free():
    reader = MasterWave1WorkbenchReader(SHCHEM_ROOT)
    items = all_items(reader)
    exact = [item for item in items if item["crosswalk_summary"]["state"] == "exact"]
    eligible = [item for item in exact if item["crosswalk_summary"]["overlay_allowed"]]
    blocked = [
        item for item in exact if not item["crosswalk_summary"]["overlay_allowed"]
    ]
    split = [item for item in items if item["crosswalk_summary"]["state"] == "split"]
    unmapped = [
        item for item in items if item["crosswalk_summary"]["state"] == "unmapped"
    ]
    assert (len(eligible), len(blocked), len(split), len(unmapped)) == (165, 4, 17, 284)
    assert sum(item["crosswalk_summary"]["relation_count"] for item in split) == 38

    eligible_detail = reader.atomic_detail(eligible[0]["node_id"])
    assert eligible_detail["wave1_candidate_overlay"]["available"] is True
    assert (
        eligible_detail["wave1_candidate_overlay"]["namespace"]
        == "wave1_candidate_overlay"
    )
    assert eligible_detail["node"]["classification"] == eligible[0]["classification"]
    assert (
        eligible_detail["crosswalk_relation"]["namespace"]
        == "master_wave1_crosswalk_candidate_only"
    )
    assert eligible_detail["reference_answer"]["independently_verified"] is False
    assert "crop" not in json.dumps(
        eligible_detail["reference_answer"], ensure_ascii=False
    ).casefold()

    blocked_detail = reader.atomic_detail(blocked[0]["node_id"])
    assert blocked_detail["crosswalk_relation"]["relation_type"] == "exact_1_to_1"
    assert blocked_detail["wave1_candidate_overlay"] == {
        "available": False,
        "reason": "label_overlay_blocked",
    }
    assert blocked_detail["reference_answer"]["reference_answer_text"] is not None
    split_detail = reader.atomic_detail(split[0]["node_id"])
    assert split_detail["crosswalk_relation"]["relation_count"] >= 2
    assert split_detail["wave1_candidate_overlay"]["available"] is False
    assert split_detail["reference_answer"]["reference_answer_text"] is None
    unmapped_detail = reader.atomic_detail(unmapped[0]["node_id"])
    assert unmapped_detail["crosswalk_relation"]["records"] == []
    assert unmapped_detail["wave1_candidate_overlay"] == {
        "available": False,
        "reason": "master_atomic_unmapped",
    }
    assert unmapped_detail["reference_answer"]["reference_answer_text"] is None

    serialized = json.dumps(
        [items, eligible_detail, blocked_detail, split_detail, unmapped_detail],
        ensure_ascii=False,
    ).casefold()
    for forbidden in (
        "crop_path",
        "source_package_path",
        "original_source_path",
        "whole_page_path",
        "image_endpoint",
        "question-crops",
        "c:\\users",
        "kb/classification/",
        "kb/formal/",
        "staging/",
        ".png",
    ):
        assert forbidden not in serialized


def test_all_169_exact_master_nodes_reuse_only_the_same_wave_reference_projection():
    reader = MasterWave1WorkbenchReader(SHCHEM_ROOT)
    snapshot = reader._snapshot()
    projected = reader._exact_reference_answer_index(snapshot)
    identity = reader.visual_scan_identity_index()["exact_master_to_wave1"]
    assert len(projected) == len(identity) == 169
    assert Counter(value["availability"] for value in projected.values()) == {
        "present_part_aligned": 136,
        "absent": 33,
    }

    visual = QuestionVisualScanReader(SHCHEM_ROOT)._validated_catalog()
    wave_records = {
        node_id: record
        for _, visual_snapshot in visual
        for node_id, record in visual_snapshot.by_node_id.items()
    }
    assert len(wave_records) == 252
    for master_id, wave_node_id in identity.items():
        record = wave_records[wave_node_id]
        assert projected[master_id] == project_reference_answer(
            record["answer"], record["risks_and_limits"]
        )
        serialized = json.dumps(projected[master_id], ensure_ascii=False).casefold()
        for forbidden in (
            "visual_alignment_evidence",
            "answer_crop",
            "answer_page",
            "crop_path",
            "source_path",
            "http://",
            "https://",
        ):
            assert forbidden not in serialized


def test_http_requires_bearer_teacher_and_trusted_loopback_origin(tmp_path):
    config = build_config(tmp_path / "state")
    config.principals.append(
        Principal(
            "student-workbench",
            "student",
            token_digest(STUDENT_TOKEN),
        )
    )
    config.validate()
    with running_server(config) as server:
        path = "/api/v1/kb/workbench/master-atomic/status"
        status, _, _ = request(
            server, "GET", path, token=None, headers=browser_headers(server)
        )
        assert status == 401
        status, _, _ = request(
            server, "GET", path, token=STUDENT_TOKEN, headers=browser_headers(server)
        )
        assert status == 403
        status, _, _ = request(
            server,
            "GET",
            path,
            token=TOKEN_A,
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert status == 403
        status, payload, _ = request(
            server,
            "GET",
            path,
            token=TOKEN_A,
            headers=browser_headers(server),
            timeout=20,
        )
        assert status == 200
        assert payload["data"]["counts"]["master_atomic_inventory"] == 470


def test_http_pages_200_200_70_detail_and_requests_are_zero_write(tmp_path):
    state_root = tmp_path / "state"
    config = build_config(state_root)
    with running_server(config) as server:
        headers = browser_headers(server)
        state_before = tree_snapshot(state_root)
        product_before = tree_snapshot(PRODUCT)
        pages = []
        for offset in (0, 200, 400):
            status, payload, _ = request(
                server,
                "GET",
                f"/api/v1/kb/workbench/master-atomic?limit=200&offset={offset}",
                headers=headers,
                timeout=20,
            )
            assert status == 200
            pages.append(payload["data"])
        assert [page["count"] for page in pages] == [200, 200, 70]
        items = [item for page in pages for item in page["items"]]
        assert len({item["node_id"] for item in items}) == 470
        node_id = next(
            item["node_id"]
            for item in items
            if item["crosswalk_summary"]["overlay_allowed"]
        )
        status, payload, _ = request(
            server,
            "GET",
            f"/api/v1/kb/workbench/master-atomic/{node_id}",
            headers=headers,
            timeout=20,
        )
        assert status == 200
        assert payload["data"]["wave1_candidate_overlay"]["available"] is True
        assert tree_snapshot(state_root) == state_before
        assert tree_snapshot(PRODUCT) == product_before


def test_source_hash_mutation_fails_the_entire_http_api_closed(tmp_path):
    root = isolated_root(tmp_path)
    master_atomic = root / workbench.MASTER_FILES["atomic_part"]
    master_atomic.write_bytes(master_atomic.read_bytes() + b"\n")
    config = build_config(tmp_path / "state")
    config.shchem_root = root
    config.validate()
    with running_server(config) as server:
        status, payload, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/master-atomic/status",
            headers=browser_headers(server),
        )
        assert status == 409
        assert payload["error"]["code"] == "master_wave1_hash_mismatch"


def test_rehashed_gate_elevation_still_fails_closed(tmp_path, monkeypatch):
    root = isolated_root(tmp_path)

    def elevate(rows):
        rows[0]["authority_gates"]["human_reviewed"] = True

    new_self_hash = rebind_mutated_records(root, elevate)
    monkeypatch.setattr(workbench, "EXPECTED_MANIFEST_SELF_SHA256", new_self_hash)
    with pytest.raises(MasterWave1WorkbenchError) as error:
        MasterWave1WorkbenchReader(root).status()
    assert error.value.code == "master_wave1_gate_elevated"


def test_rehashed_endpoint_cardinality_mutation_still_fails_closed(
    tmp_path, monkeypatch
):
    root = isolated_root(tmp_path)

    def corrupt(rows):
        split = next(
            row for row in rows if row["relation_type"] == "wave_refines_master"
        )
        split["endpoint_cardinality"]["wave_rows_for_strong_key"] = 999

    new_self_hash = rebind_mutated_records(root, corrupt)
    monkeypatch.setattr(workbench, "EXPECTED_MANIFEST_SELF_SHA256", new_self_hash)
    with pytest.raises(MasterWave1WorkbenchError) as error:
        MasterWave1WorkbenchReader(root).status()
    assert error.value.code == "master_wave1_cardinality_invalid"


def test_openapi_publishes_only_the_three_read_only_master_atomic_routes():
    text = OPENAPI.read_text(encoding="utf-8")
    assert "/api/v1/kb/workbench/master-atomic/status:" in text
    assert "/api/v1/kb/workbench/master-atomic:" in text
    assert "/api/v1/kb/workbench/master-atomic/{node_id}:" in text
    assert "MasterAtomicWorkbenchStatusEnvelope" in text
    assert "MasterAtomicWorkbenchListEnvelope" in text
    assert "MasterAtomicWorkbenchDetailEnvelope" in text
    assert "pixel_reuse_allowed: { const: false }" in text
    assert EXPECTED_COUNTS["master_atomic_inventory"] == 470
