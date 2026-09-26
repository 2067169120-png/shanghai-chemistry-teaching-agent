from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.candidate_review import (
    BATCH_RELATIVE,
    MAX_CROP_BYTES,
    CandidateReviewError,
    Wave1CandidateReviewReader,
)
from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
BATCH = SHCHEM_ROOT / BATCH_RELATIVE
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
STUDENT_TOKEN = "candidate-review-student-0123456789"


def browser_headers(server) -> dict[str, str]:
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return {"Origin": origin, "Sec-Fetch-Site": "same-origin"}


def batch_snapshot(root: Path = BATCH) -> dict[str, tuple[int, int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
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


def isolated_reader(temp: Path) -> tuple[Wave1CandidateReviewReader, Path]:
    root = temp / "sh-chem-db"
    target = root / BATCH_RELATIVE
    target.parent.mkdir(parents=True)
    shutil.copytree(BATCH, target)
    return Wave1CandidateReviewReader(root), target


def rebind_artifact(batch: Path, filename: str, raw: bytes) -> None:
    path = batch / filename
    path.write_bytes(raw)
    manifest_path = batch / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(
        item for item in manifest["generated_artifacts"] if item["path"] == filename
    )
    artifact["bytes"] = len(raw)
    artifact["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def source_crop_record(paper_id: str, crop_id: str) -> dict[str, object]:
    for line in (BATCH / "visual_crop_manifest.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        row = json.loads(line)
        if row["paper_id"] == paper_id and row["upstream_crop_id"] == crop_id:
            return row
    raise AssertionError(f"missing source crop fixture: {paper_id}/{crop_id}")


def copy_isolated_crop(
    temp: Path,
    node_id: str,
    crop_id: str,
    *,
    copy_bytes: bool = True,
) -> tuple[Wave1CandidateReviewReader, Path, Path, dict[str, object]]:
    reader, batch = isolated_reader(temp)
    atomic = next(
        json.loads(line)
        for line in (batch / "atomic_part_records.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if json.loads(line)["atomic_part_id"] == node_id
    )
    crop = source_crop_record(atomic["paper_id"], crop_id)
    root = temp / "sh-chem-db"
    target = root / str(crop["crop_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    if copy_bytes:
        shutil.copyfile(SHCHEM_ROOT / str(crop["crop_path"]), target)
    return reader, batch, target, crop


def request_headers_for(server) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def candidate_crop_route(node_id: str, crop_id: str) -> str:
    return (
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
        f"atomic_part/{node_id}/question-crops/{crop_id}"
    )


def test_reader_exposes_exact_wave1_counts_ids_and_same_source_boundary():
    reader = Wave1CandidateReviewReader(SHCHEM_ROOT)
    status = reader.status()
    assert status["source_namespace"] == "candidate_review_only"
    assert status["counts"] == {
        "papers": 5,
        "theme_big_questions": 25,
        "printed_questions": 207,
        "atomic_parts": 252,
        "duplicate_or_near_duplicate_candidates": 2,
    }
    assert "同五套来源卷" in status["same_source_disclosure"]
    assert status["additional_paper_count"] == 0
    assert status["all_protected_gates_false"] is True
    assert status["endpoints"] == {
        "read_only_get": True,
        "mutation": False,
        "apply": False,
        "promotion": False,
    }
    papers = reader.list_nodes(
        node_type="paper",
        query=None,
        paper_id=None,
        theme_id=None,
        printed_question_id=None,
        limit=20,
        offset=0,
    )
    assert papers["total"] == 5
    assert [item["node_id"] for item in papers["items"]] == [
        "W1-DT2025-H1-MID",
        "W1-PT2026-EM",
        "W1-QP2026-EM",
        "W1-XH2026-EM",
        "W1-YP2026-EM",
    ]
    assert all(item["title"] != "unknown" for item in papers["items"])


@pytest.mark.parametrize(
    "node_type,total",
    [
        ("paper", 5),
        ("theme_big_question", 25),
        ("printed_question", 207),
        ("atomic_part", 252),
    ],
)
def test_reader_list_totals_are_derived_from_exact_manifest_bound_rows(
    node_type, total
):
    value = Wave1CandidateReviewReader(SHCHEM_ROOT).list_nodes(
        node_type=node_type,
        query=None,
        paper_id=None,
        theme_id=None,
        printed_question_id=None,
        limit=1,
        offset=0,
    )
    assert value["total"] == total
    assert value["count"] == 1


def test_atomic_two_page_projection_is_complete_unique_path_free_and_has_question_evidence():
    reader = Wave1CandidateReviewReader(SHCHEM_ROOT)
    first = reader.list_nodes(
        node_type="atomic_part",
        query=None,
        paper_id=None,
        theme_id=None,
        printed_question_id=None,
        limit=200,
        offset=0,
    )
    second = reader.list_nodes(
        node_type="atomic_part",
        query=None,
        paper_id=None,
        theme_id=None,
        printed_question_id=None,
        limit=200,
        offset=200,
    )
    items = first["items"] + second["items"]
    assert (first["count"], second["count"], first["total"], second["total"]) == (
        200,
        52,
        252,
        252,
    )
    assert len({item["node_id"] for item in items}) == 252
    assert all(
        any(
            evidence["evidence_role"] == "question"
            for evidence in item["crop_refs"]["items"]
        )
        for item in items
    )
    serialized = json.dumps(items, ensure_ascii=False).casefold()
    for forbidden in (
        "crop_path",
        "original_source_path",
        "whole_page_path",
        "source_package_path",
        "c:\\users",
        "kb/formal/",
        "staging/",
    ):
        assert forbidden not in serialized


def test_global_crop_id_collision_is_scoped_by_paper_id_not_cross_wired():
    reader = Wave1CandidateReviewReader(SHCHEM_ROOT)
    expected = {
        "W1-QP2026-EM": "9ed9a2c96276f965a81e3f92a718cf398ac1bc3dbbe13c3b2c56232553810dfb",
        "W1-XH2026-EM": "de997828de2273fb2b78f3640a3cb18efd2cc754c60aa0246d2bfde770effe65",
    }
    seen: dict[str, set[str]] = {}
    for paper_id in expected:
        page = reader.list_nodes(
            node_type="atomic_part",
            query=None,
            paper_id=paper_id,
            theme_id=None,
            printed_question_id=None,
            limit=200,
            offset=0,
        )
        seen[paper_id] = {
            evidence["sha256"]
            for item in page["items"]
            for evidence in item["crop_refs"]["items"]
            if evidence["crop_id"] == "SHARED-T2_CELL"
        }
    assert seen == {paper_id: {sha256} for paper_id, sha256 in expected.items()}
    assert expected["W1-QP2026-EM"] != expected["W1-XH2026-EM"]


def test_atomic_detail_has_exact_four_level_chain_candidate_axes_and_safe_refs():
    reader = Wave1CandidateReviewReader(SHCHEM_ROOT)
    node_id = "W1-DT2025-H1-MID-AP-DT2025-H1-Q01-P01"
    value = reader.node("atomic_part", node_id)
    assert [item["node_type"] for item in value["parent_chain"]] == [
        "paper",
        "theme_big_question",
        "printed_question",
        "atomic_part",
    ]
    assert value["classification"]["K"]["values"][0]["id"] == "K02"
    for axis in ("K", "A", "C", "R", "RP"):
        assert value["classification"][axis]["status"] in {
            "candidate_pending_human",
            "unknown",
        }
    assert value["difficulty"]["measured_difficulty"] == "blocked_pending_review"
    assert value["theme_chain"]["human_reviewed"] is False
    assert value["crop_refs"]["count"] >= 1
    serialized = json.dumps(value, ensure_ascii=False).casefold()
    assert "c:\\users" not in serialized
    assert "private_state" not in serialized
    assert "crop_path" not in serialized
    assert "original_source_path" not in serialized
    assert "whole_page_path" not in serialized
    assert "source_package_path" not in serialized
    assert "kb/formal/" not in serialized
    assert "staging/" not in serialized
    for item in value["crop_refs"]["items"]:
        assert set(item) == {
            "crop_id",
            "evidence_role",
            "source_page",
            "sha256",
            "crop_sha256",
            "content_type",
            "access",
            "image_endpoint",
        }
        assert item["evidence_role"] in {"question", "shared_material"}
        assert item["sha256"] == item["crop_sha256"]
        assert item["content_type"] == "image/png"
        assert item["access"] == "teacher_loopback_read_only"
        assert item["image_endpoint"].startswith("/api/v1/")
    assert any(
        item["evidence_role"] == "question"
        for item in value["crop_refs"]["items"]
    )
    assert value["answer_status"]["answer_verified"] is False
    assert value["answer_status"]["official"] is False
    assert value["rubric_status"]["rubric_verified"] is False
    assert value["rubric_status"]["official"] is False
    assert value["authority"] == {
        "candidate_only": True,
        "read_only": True,
        "human_reviewed": False,
        "formal": False,
        "formal_promotion_allowed": False,
        "retrieval_ready": False,
        "unattended_retrieval_allowed": False,
        "teaching_use_allowed": False,
        "generation_allowed": False,
        "diagnosis_allowed": False,
        "publication_allowed": False,
        "promotion_allowed": False,
        "apply_available": False,
        "mutation_endpoint_present": False,
        "official": False,
    }


def test_hash_mismatch_duplicate_boundary_and_authority_mutations_fail_closed():
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        reader, batch = isolated_reader(temp)
        atomic_path = batch / "atomic_part_records.jsonl"
        atomic_path.write_bytes(atomic_path.read_bytes() + b"\n")
        with pytest.raises(CandidateReviewError) as captured:
            reader.status()
        assert captured.value.code == "candidate_review_hash_mismatch"

    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        reader, batch = isolated_reader(temp)
        duplicate_path = batch / "duplicate_and_near_duplicate_candidates.jsonl"
        raw = b"\n".join(duplicate_path.read_bytes().splitlines()[:1]) + b"\n"
        rebind_artifact(batch, duplicate_path.name, raw)
        with pytest.raises(CandidateReviewError) as captured:
            reader.status()
        assert captured.value.code == "candidate_review_duplicate_boundary_mismatch"

    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        reader, batch = isolated_reader(temp)
        atomic_path = batch / "atomic_part_records.jsonl"
        lines = atomic_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["gates"]["human_reviewed"] = True
        lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        raw = ("\n".join(lines) + "\n").encode("utf-8")
        rebind_artifact(batch, atomic_path.name, raw)
        with pytest.raises(CandidateReviewError) as captured:
            reader.status()
        assert captured.value.code == "candidate_review_authority_escalation"


def test_http_routes_are_teacher_origin_scoped_traversal_safe_and_zero_source_write():
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        config.principals.append(
            Principal("review-student", "student", token_digest(STUDENT_TOKEN), ())
        )
        before = batch_snapshot()
        with running_server(config) as server:
            route = "/api/v1/kb/sources/candidate_review_only/wave1/status"
            status, body, _ = request(server, "GET", route, token=None)
            assert status == 401
            assert body["error"]["code"] == "authentication_required"
            status, body, _ = request(
                server, "GET", route, token=STUDENT_TOKEN, headers=browser_headers(server)
            )
            assert status == 403
            assert body["error"]["code"] == "teacher_scope_required"
            status, body, _ = request(server, "GET", route)
            assert status == 403
            assert body["error"]["code"] == "origin_required"
            status, body, _ = request(
                server, "GET", route, headers={"Origin": "https://evil.example"}
            )
            assert status == 403
            assert body["error"]["code"] == "origin_denied"
            status, body, _ = request(
                server, "GET", route, headers=browser_headers(server)
            )
            assert status == 200
            assert body["data"]["counts"]["atomic_parts"] == 252

            nodes = "/api/v1/kb/sources/candidate_review_only/wave1/nodes"
            status, first, _ = request(
                server,
                "GET",
                nodes + "?node_type=atomic_part&limit=200&offset=0",
                headers=browser_headers(server),
            )
            assert status == 200
            assert (first["data"]["count"], first["data"]["limit"], first["data"]["total"]) == (200, 200, 252)
            status, second, _ = request(
                server,
                "GET",
                nodes + "?node_type=atomic_part&limit=200&offset=200",
                headers=browser_headers(server),
            )
            assert status == 200
            assert (second["data"]["count"], second["data"]["limit"], second["data"]["total"]) == (52, 200, 252)
            projected_ids = {
                item["node_id"]
                for item in first["data"]["items"] + second["data"]["items"]
            }
            assert len(projected_ids) == 252
            status, body, _ = request(
                server,
                "GET",
                nodes + "?paper_id=..%2F..%2FAGENTS.md",
                headers=browser_headers(server),
            )
            assert status == 400
            assert body["error"]["code"] == "candidate_review_filter_invalid"
            status, body, _ = request(
                server,
                "GET",
                nodes + "?paper_id=W1-DT2025-H1-MID&paper_id=W1-PT2026-EM",
                headers=browser_headers(server),
            )
            assert status == 400
            assert body["error"]["code"] == "candidate_review_query_ambiguous"
            status, body, _ = request(
                server,
                "GET",
                nodes + "/atomic_part/..%2F..%2FAGENTS.md",
                headers=browser_headers(server),
            )
            assert status == 404
            assert "AGENTS.md" not in json.dumps(body)
            status, body, _ = request(
                server,
                "POST",
                nodes,
                headers=browser_headers(server),
                payload={"apply": True},
            )
            assert status == 404
            assert body["error"]["code"] == "route_not_found"
        assert batch_snapshot() == before


def test_question_crop_http_is_teacher_loopback_scoped_hash_exact_and_zero_write():
    node_id = "W1-DT2025-H1-MID-AP-DT2025-H1-Q01-P01"
    detail = Wave1CandidateReviewReader(SHCHEM_ROOT).node("atomic_part", node_id)
    descriptor = next(
        item
        for item in detail["crop_refs"]["items"]
        if item["evidence_role"] == "question"
    )
    route = descriptor["image_endpoint"]
    answer_node = "W1-PT2026-EM-AP-PT2026-T1-Q01-P01-S01"
    answer_crop = "PT2026-T1-Q01-P01-ANSWER-S1"
    answer_detail = Wave1CandidateReviewReader(SHCHEM_ROOT).node(
        "atomic_part", answer_node
    )
    assert answer_detail["answer_status"]["authority"] == "nonofficial_reference"
    assert answer_detail["answer_status"]["answer_verified"] is False
    assert answer_detail["answer_status"]["official"] is False
    assert answer_detail["rubric_status"]["rubric_verified"] is False
    assert answer_detail["rubric_status"]["official"] is False
    assert answer_crop not in {
        item["crop_id"] for item in answer_detail["crop_refs"]["items"]
    }
    with tempfile.TemporaryDirectory() as temp_name:
        state_root = Path(temp_name)
        config = build_config(state_root)
        config.principals.append(
            Principal("review-student", "student", token_digest(STUDENT_TOKEN), ())
        )
        source_before = batch_snapshot()
        with running_server(config) as server:
            before = tree_snapshot(state_root)
            headers = request_headers_for(server)

            status, body, _ = request(
                server, "GET", route, token=None, headers=headers
            )
            assert status == 401
            assert body["error"]["code"] == "authentication_required"
            status, body, _ = request(
                server, "GET", route, token=STUDENT_TOKEN, headers=headers
            )
            assert status == 403
            assert body["error"]["code"] == "teacher_scope_required"
            status, body, _ = request(server, "GET", route)
            assert status == 403
            assert body["error"]["code"] == "origin_required"
            status, body, _ = request(
                server, "GET", route, headers={"Origin": "https://evil.example"}
            )
            assert status == 403
            assert body["error"]["code"] == "origin_denied"

            status, body, _ = request(
                server,
                "GET",
                candidate_crop_route(node_id, "UNKNOWN-CROP"),
                headers=headers,
            )
            assert status == 404
            assert body["error"]["code"] == "candidate_review_crop_not_found"
            status, body, _ = request(
                server,
                "GET",
                candidate_crop_route("UNKNOWN-ATOMIC", descriptor["crop_id"]),
                headers=headers,
            )
            assert status == 404
            assert body["error"]["code"] == "candidate_review_node_not_found"
            status, body, _ = request(
                server,
                "GET",
                candidate_crop_route(answer_node, answer_crop),
                headers=headers,
            )
            assert status == 403
            assert body["error"]["code"] == "candidate_review_crop_role_denied"

            status, raw, response_headers = request(
                server, "GET", route, headers=headers
            )
            assert status == 200
            assert response_headers.get("Content-Type") == "image/png"
            assert response_headers.get("Cache-Control") == "no-store"
            assert response_headers.get("X-Content-Type-Options") == "nosniff"
            assert response_headers.get("Content-Disposition") is None
            assert len(raw) <= MAX_CROP_BYTES
            assert raw.startswith(b"\x89PNG\r\n\x1a\n")
            assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
            assert tree_snapshot(state_root) == before
        assert batch_snapshot() == source_before


def test_question_crop_hash_mutation_non_png_and_oversize_fail_closed():
    node_id = "W1-DT2025-H1-MID-AP-DT2025-H1-Q01-P01"
    crop_id = "DT2025-H1-Q01-E1"
    alternate = source_crop_record(
        "W1-DT2025-H1-MID", "DT2025-H1-SHARED-SECTION_1"
    )
    alternate_png = (SHCHEM_ROOT / str(alternate["crop_path"])).read_bytes()

    with tempfile.TemporaryDirectory() as temp_name:
        reader, _, target, _ = copy_isolated_crop(
            Path(temp_name), node_id, crop_id
        )
        target.write_bytes(alternate_png)
        with pytest.raises(CandidateReviewError) as captured:
            reader.question_crop(node_id, crop_id)
        assert captured.value.code == "candidate_review_crop_hash_mismatch"

    with tempfile.TemporaryDirectory() as temp_name:
        reader, _, target, _ = copy_isolated_crop(
            Path(temp_name), node_id, crop_id
        )
        target.write_bytes(b"not a png")
        with pytest.raises(CandidateReviewError) as captured:
            reader.question_crop(node_id, crop_id)
        assert captured.value.code == "candidate_review_crop_not_png"

    with tempfile.TemporaryDirectory() as temp_name:
        reader, _, target, _ = copy_isolated_crop(
            Path(temp_name), node_id, crop_id
        )
        target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_CROP_BYTES)
        with pytest.raises(CandidateReviewError) as captured:
            reader.question_crop(node_id, crop_id)
        assert captured.value.code == "candidate_review_crop_too_large"
        assert captured.value.status == 413


def test_question_crop_traversal_and_symlink_escape_fail_closed():
    node_id = "W1-DT2025-H1-MID-AP-DT2025-H1-Q01-P01"
    crop_id = "DT2025-H1-Q01-E1"
    with tempfile.TemporaryDirectory() as temp_name:
        reader, batch, _, crop = copy_isolated_crop(
            Path(temp_name), node_id, crop_id, copy_bytes=False
        )
        malicious_path = "kb/formal/candidates/intake_round_2026-08-02/../AGENTS.md"
        crop_lines = [
            json.loads(line)
            for line in (batch / "visual_crop_manifest.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        manifest_crop = next(
            item
            for item in crop_lines
            if item["paper_id"] == crop["paper_id"]
            and item["upstream_crop_id"] == crop_id
        )
        manifest_crop["crop_path"] = malicious_path
        rebind_artifact(
            batch,
            "visual_crop_manifest.jsonl",
            ("\n".join(json.dumps(item, ensure_ascii=False) for item in crop_lines) + "\n").encode(
                "utf-8"
            ),
        )
        atomic_lines = [
            json.loads(line)
            for line in (batch / "atomic_part_records.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        atomic = next(item for item in atomic_lines if item["atomic_part_id"] == node_id)
        local_crop = next(
            item for item in atomic["question_evidence"] if item["crop_id"] == crop_id
        )
        local_crop["crop_path"] = malicious_path
        rebind_artifact(
            batch,
            "atomic_part_records.jsonl",
            ("\n".join(json.dumps(item, ensure_ascii=False) for item in atomic_lines) + "\n").encode(
                "utf-8"
            ),
        )
        with pytest.raises(CandidateReviewError) as captured:
            reader.question_crop(node_id, crop_id)
        assert captured.value.code == "candidate_review_crop_manifest_invalid"

    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        reader, _, target, crop = copy_isolated_crop(
            temp, node_id, crop_id, copy_bytes=False
        )
        outside = temp / "outside.png"
        shutil.copyfile(SHCHEM_ROOT / str(crop["crop_path"]), outside)
        try:
            target.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable on this Windows host: {exc}")
        with pytest.raises(CandidateReviewError) as captured:
            reader.question_crop(node_id, crop_id)
        assert captured.value.code == "candidate_review_crop_symlink_denied"


def test_webui_static_contract_hashes_mobile_overflow_and_no_apply_surface():
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")
    manifest = json.loads((OVERLAY / "overlay.manifest.json").read_text(encoding="utf-8"))
    root_manifest = json.loads(
        (WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for marker in (
        'id="tab-candidate-review"',
        'id="panel-candidate-review"',
        'id="candidateReviewPapers"',
        'id="candidateReviewChain"',
        'id="candidateReviewDetail"',
        'id="candidateReviewRefs"',
        "candidate_review_only",
        "不是新增五套卷",
    ):
        assert marker in html
    assert "/api/v1/kb/sources/candidate_review_only/wave1" in app
    assert "function candidateReviewSummary(item)" in app
    assert "task.text || task.summary || task.label" in app
    assert "function logCandidateReviewList(value)" in app
    assert "列表响应不重复铺到任务详情" in app
    assert "logCandidateReviewList(value);" in app
    assert "items.slice(0, 20)" in app
    assert "omitted_visible_node_ids" in app
    assert "function candidateReviewFieldSummary(value)" in app
    assert "认知预标" in app
    assert "完整字段见下方 API 任务详情" in app
    assert "classification?.RP" in app
    assert "blocked_pending_review" in app
    assert "candidateReviewApply" not in html + app
    assert "candidate-chain" in css
    assert "overflow-x: auto" in css
    assert "overflow-wrap: anywhere" in css
    assert "@media (max-width: 760px)" in css
    assert ".candidate-paper-grid, .candidate-review-filter" in css
    for filename, descriptor in manifest["files"].items():
        assert hashlib.sha256((OVERLAY / filename).read_bytes()).hexdigest() == descriptor[
            "sha256"
        ]
    review = manifest["candidate_review_only"]
    assert review["expected_counts"] == {
        "papers": 5,
        "theme_big_questions": 25,
        "printed_questions": 207,
        "atomic_parts": 252,
        "duplicate_or_near_duplicate_candidates": 2,
    }
    assert review["same_five_sources_refined_not_additional"] is True
    assert review["mutation_endpoint_present"] is False
    assert review["apply_endpoint_present"] is False
    assert root_manifest["candidate_review_only"][
        "mutation_apply_promotion_endpoints_present"
    ] is False
    contract = OPENAPI.read_text(encoding="utf-8")
    assert "/api/v1/kb/sources/candidate_review_only/wave1/status" in contract
    assert (
        "/api/v1/kb/sources/candidate_review_only/wave1/nodes/atomic_part/"
        "{node_id}/question-crops/{crop_id}"
    ) in contract
    assert "same five source papers refined from overlay v2" in contract
    assert "never a path" in contract
    assert "image/png" in contract
    assert "one MiB" in contract
