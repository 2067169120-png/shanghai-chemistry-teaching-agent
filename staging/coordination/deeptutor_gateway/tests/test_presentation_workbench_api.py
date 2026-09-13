from __future__ import annotations

import hashlib
import struct
from copy import deepcopy
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
from integrations.deeptutor_shchem_v1.config import Principal
from integrations.deeptutor_shchem_v1.presentation_jobs import PresentationJobManager
from integrations.deeptutor_shchem_v1.security import canonical_json_sha256
from integrations.deeptutor_shchem_v1.service import ApiError, GatewayService
from staging.coordination.deeptutor_gateway.tests.test_presentation_jobs import (
    FakeToolchain,
)
from staging.coordination.deeptutor_gateway.tests.test_presentation_theme_adapter import (
    _catalog,
    _details,
    _request as _real_theme_request,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    request,
    running_server,
)


PROJECT_ID = "PPTPRJ-" + "a" * 32
VERSION_ID = "PPTVER-" + "b" * 64
JOB_ID = "PPTJOB-" + "c" * 64
WORKSPACE = Path(__file__).resolve().parents[4]


def _origin(server) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def _assert_chinese_error(body: dict) -> None:
    message = body["error"]["message"]
    assert any("\u4e00" <= character <= "\u9fff" for character in message), message


def test_all_presentation_routes_use_expected_statuses_and_binary_types(
    tmp_path: Path,
) -> None:
    calls: list[tuple] = []
    with running_server(build_config(tmp_path / "state")) as server:
        service = server.service
        service.presentation_projects = lambda principal: {
            "schema_version": "shchem.presentation-project-list.v1",
            "projects": [],
            "count": 0,
        }
        service.presentation_create_from_theme = lambda principal, payload: (
            calls.append(("create", payload))
            or {
                "schema_version": "shchem.presentation-project-create.v1",
                "project": {"project_id": PROJECT_ID},
                "outline": {"revision": "PPTOL-" + "d" * 32},
            }
        )
        service.presentation_project_get = lambda principal, project_id: {
            "schema_version": "shchem.presentation-project-detail.v1",
            "project": {"project_id": project_id},
            "outline": {"revision": "PPTOL-" + "d" * 32},
            "versions": [],
        }
        service.presentation_outline_update = (
            lambda principal, project_id, payload: calls.append(
                ("outline", project_id, payload)
            )
            or {"project_id": project_id, "revision": "PPTOL-" + "e" * 32}
        )
        service.presentation_outline_get = lambda principal, project_id: {
            "project_id": project_id,
            "revision": "PPTOL-" + "d" * 32,
        }
        service.presentation_version_create = (
            lambda principal, project_id, payload: calls.append(
                ("version", project_id, payload)
            )
            or {"project_id": project_id, "version_id": VERSION_ID}
        )
        service.presentation_versions = lambda principal, project_id: {
            "schema_version": "shchem.presentation-version-list.v1",
            "versions": [{"project_id": project_id, "version_id": VERSION_ID}],
            "count": 1,
        }
        service.presentation_version_get = (
            lambda principal, project_id, version_id: {
                "project_id": project_id,
                "version_id": version_id,
            }
        )
        service.presentation_render_start = (
            lambda principal, project_id, version_id, payload: calls.append(
                ("render", project_id, version_id, payload)
            )
            or {"job_id": JOB_ID, "status": "queued"}
        )
        service.presentation_job_get = lambda principal, job_id: {
            "job_id": job_id,
            "status": "completed",
            "artifacts": [],
        }
        service.presentation_job_cancel = (
            lambda principal, job_id, payload: calls.append(
                ("cancel", job_id, payload)
            )
            or {"job_id": job_id, "status": "cancel_requested"}
        )
        service.presentation_job_retry = (
            lambda principal, job_id, payload: calls.append(
                ("retry", job_id, payload)
            )
            or {"job_id": job_id, "status": "queued"}
        )
        binary = {
            "deck_json": (b"{}", "application/json", "deck.json"),
            "pptx": (
                b"PK\x03\x04pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "lesson_presentation.pptx",
            ),
            "qa_report": (b"{}", "application/json", "qa_report.json"),
            "preview_montage": (
                b"\x89PNG\r\n\x1a\npreview",
                "image/png",
                "rendered_montage.png",
            ),
        }
        service.presentation_artifact_bytes = (
            lambda principal, job_id, artifact_id: binary[artifact_id]
        )
        headers = _origin(server)

        status, body, _ = request(
            server,
            "GET",
            "/api/v1/presentations/projects",
            token=TOKEN_A,
            headers=headers,
        )
        assert status == 200 and body["data"]["count"] == 0

        create_payload = {"scope": "wave1", "theme_id": "THEME-1"}
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/presentations/projects",
            token=TOKEN_A,
            payload=create_payload,
            headers=headers,
        )
        assert status == 201 and body["data"]["project"]["project_id"] == PROJECT_ID
        assert calls[-1] == ("create", create_payload)

        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/presentations/projects/{PROJECT_ID}",
            token=TOKEN_A,
            headers=headers,
        )
        assert status == 200 and body["data"]["project"]["project_id"] == PROJECT_ID

        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/presentations/projects/{PROJECT_ID}/outline",
            token=TOKEN_A,
            headers=headers,
        )
        assert status == 200 and body["data"]["revision"].startswith("PPTOL-")

        outline_payload = {
            "expected_revision": "PPTOL-" + "d" * 32,
            "deck_json": {"schema_version": "fixture"},
        }
        status, body, _ = request(
            server,
            "PATCH",
            f"/api/v1/presentations/projects/{PROJECT_ID}/outline",
            token=TOKEN_A,
            payload=outline_payload,
            headers=headers,
        )
        assert status == 200 and body["data"]["revision"].startswith("PPTOL-")

        version_payload = {"expected_outline_revision": "PPTOL-" + "e" * 32}
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/presentations/projects/{PROJECT_ID}/versions",
            token=TOKEN_A,
            payload=version_payload,
            headers=headers,
        )
        assert status == 201 and body["data"]["version_id"] == VERSION_ID

        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/presentations/projects/{PROJECT_ID}/versions",
            token=TOKEN_A,
            headers=headers,
        )
        assert status == 200 and body["data"]["count"] == 1

        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/presentations/projects/{PROJECT_ID}/versions/{VERSION_ID}",
            token=TOKEN_A,
            headers=headers,
        )
        assert status == 200 and body["data"]["version_id"] == VERSION_ID

        status, body, _ = request(
            server,
            "POST",
            (
                f"/api/v1/presentations/projects/{PROJECT_ID}/versions/"
                f"{VERSION_ID}/renders"
            ),
            token=TOKEN_A,
            payload={},
            headers=headers,
        )
        assert status == 202 and body["data"]["job_id"] == JOB_ID

        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/presentations/jobs/{JOB_ID}",
            token=TOKEN_A,
            headers=headers,
        )
        assert status == 200 and body["data"]["status"] == "completed"

        for action, expected_status in (("cancel", 200), ("retry", 202)):
            status, body, _ = request(
                server,
                "POST",
                f"/api/v1/presentations/jobs/{JOB_ID}/{action}",
                token=TOKEN_A,
                payload={},
                headers=headers,
            )
            assert status == expected_status and body["data"]["job_id"] == JOB_ID

        for artifact_id, (data, content_type, filename) in binary.items():
            status, observed, response_headers = request(
                server,
                "GET",
                f"/api/v1/presentations/jobs/{JOB_ID}/artifacts/{artifact_id}",
                token=TOKEN_A,
                headers=headers,
            )
            expected_body = {} if content_type == "application/json" else data
            assert status == 200 and observed == expected_body
            assert response_headers.get("Content-Type") == content_type
            disposition = response_headers.get("Content-Disposition") or ""
            assert disposition.startswith("attachment;")
            assert filename in disposition or "filename*=UTF-8''" in disposition


def test_presentation_routes_require_origin_auth_and_reject_query_parameters(
    tmp_path: Path,
) -> None:
    with running_server(build_config(tmp_path / "state")) as server:
        server.service.presentation_projects = lambda principal: {
            "schema_version": "shchem.presentation-project-list.v1",
            "projects": [],
            "count": 0,
        }
        status, body, _ = request(
            server,
            "GET",
            "/api/v1/presentations/projects",
            token=None,
            headers=_origin(server),
        )
        assert status == 401 and body["error"]["code"] == "authentication_required"

        status, body, _ = request(
            server,
            "GET",
            "/api/v1/presentations/projects?path=C%3A%2Fforbidden",
            token=TOKEN_A,
            headers=_origin(server),
        )
        assert status == 400 and body["error"]["code"] == "presentation_query_unsupported"


def test_every_presentation_write_requires_origin_and_every_route_rejects_query(
    tmp_path: Path,
) -> None:
    write_routes = (
        ("POST", "/api/v1/presentations/projects", {}),
        (
            "PATCH",
            f"/api/v1/presentations/projects/{PROJECT_ID}/outline",
            {},
        ),
        (
            "POST",
            f"/api/v1/presentations/projects/{PROJECT_ID}/versions",
            {},
        ),
        (
            "POST",
            (
                f"/api/v1/presentations/projects/{PROJECT_ID}/versions/"
                f"{VERSION_ID}/renders"
            ),
            {},
        ),
        ("POST", f"/api/v1/presentations/jobs/{JOB_ID}/cancel", {}),
        ("POST", f"/api/v1/presentations/jobs/{JOB_ID}/retry", {}),
    )
    read_routes = (
        "/api/v1/presentations/projects",
        f"/api/v1/presentations/projects/{PROJECT_ID}",
        f"/api/v1/presentations/projects/{PROJECT_ID}/outline",
        f"/api/v1/presentations/projects/{PROJECT_ID}/versions",
        f"/api/v1/presentations/projects/{PROJECT_ID}/versions/{VERSION_ID}",
        f"/api/v1/presentations/jobs/{JOB_ID}",
        f"/api/v1/presentations/jobs/{JOB_ID}/artifacts/pptx",
    )
    with running_server(build_config(tmp_path / "state")) as server:
        for method, path, payload in write_routes:
            status, body, _ = request(
                server,
                method,
                path,
                token=TOKEN_A,
                payload=payload,
            )
            assert status == 403
            assert body["error"]["code"] == "csrf_origin_required"
            _assert_chinese_error(body)

        status, body, _ = request(
            server,
            "GET",
            read_routes[0],
            token=TOKEN_A,
        )
        assert status == 403 and body["error"]["code"] == "origin_required"
        _assert_chinese_error(body)

        headers = _origin(server)
        for method, path, payload in write_routes:
            status, body, _ = request(
                server,
                method,
                f"{path}?local_path=C%3A%5Cforbidden",
                token=TOKEN_A,
                payload=payload,
                headers=headers,
            )
            assert status == 400
            assert body["error"]["code"] == "presentation_query_unsupported"
            _assert_chinese_error(body)
        for path in read_routes:
            status, body, _ = request(
                server,
                "GET",
                f"{path}?local_path=C%3A%5Cforbidden",
                token=TOKEN_A,
                headers=headers,
            )
            assert status == 400
            assert body["error"]["code"] == "presentation_query_unsupported"
            _assert_chinese_error(body)


def test_theme_request_rejects_client_path_before_question_reads_or_project_writes(
    tmp_path: Path,
) -> None:
    config = build_config(tmp_path / "state")
    service = GatewayService(config)
    presentation_root = tmp_path / "presentation-state"
    service.presentation_jobs = PresentationJobManager(
        presentation_root, FakeToolchain()
    )
    service.workbench_registry = lambda principal: pytest.fail(
        "invalid client fields must fail before question-bank reads"
    )
    request_payload = _real_theme_request()
    request_payload["output_path"] = "C:\\Users\\teacher\\Desktop\\lesson.pptx"
    try:
        with pytest.raises(ApiError) as captured:
            service.presentation_create_from_theme(
                config.principals[0], request_payload
            )
        assert captured.value.status == 400
        assert captured.value.code == "presentation_theme_request_invalid"
        assert any(
            "\u4e00" <= character <= "\u9fff" for character in str(captured.value)
        )
        assert list(presentation_root.glob("tenants/*/projects/*")) == []
    finally:
        service.shutdown()


def test_missing_presentation_toolchain_is_a_ppt_only_chinese_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "RUNTIME_NODE",
        "RUNTIME_NODE_MODULES",
        "RUNTIME_BIN_DIR",
        "PRESENTATIONS_SKILL_DIR",
        "PRESENTATIONS_PYTHON",
    ):
        monkeypatch.delenv(name, raising=False)
    config = build_config(tmp_path / "state")
    service = GatewayService(config)
    try:
        with pytest.raises(ApiError) as captured:
            service.presentation_projects(config.principals[0])
        assert captured.value.status == 503
        assert captured.value.code == "presentation_toolchain_missing"
        assert any(
            "\u4e00" <= character <= "\u9fff" for character in str(captured.value)
        )

        # The optional PPT failure must not poison the established offline
        # question-bank registry in the same service process.
        catalog = service.theme_workbench_groups(
            config.principals[0], scope="wave1"
        )
        assert catalog.get("scope") == "wave1"
        assert isinstance(catalog.get("papers"), list)
    finally:
        service.shutdown()


def test_personal_presentation_session_survives_rotating_launcher_token(
    tmp_path: Path,
) -> None:
    config = build_config(tmp_path / "state")
    service = GatewayService(config)
    service.presentation_jobs = PresentationJobManager(
        tmp_path / "presentation-state", FakeToolchain()
    )
    first = config.principals[0]
    rotated = Principal(
        principal_id=first.principal_id,
        role=first.role,
        token_sha256="f" * 64,
        students=first.students,
        capabilities=first.capabilities,
    )
    other_teacher = Principal(
        principal_id="another-local-teacher",
        role="teacher",
        token_sha256="e" * 64,
        students=("*",),
        capabilities=first.capabilities,
    )
    owner_id, session_id = service._presentation_identity(first)
    try:
        service.presentation_jobs.create_project(
            owner_id,
            session_id,
            {"title_zh": "跨重启保留", "description_zh": "本机个人备课"},
        )
        assert service.presentation_projects(rotated)["count"] == 1
        assert service.presentation_projects(other_teacher)["count"] == 0
    finally:
        service.shutdown()


def test_service_projects_one_real_theme_and_materializes_verified_assets(
    tmp_path: Path,
) -> None:
    config = build_config(tmp_path / "state")
    service = GatewayService(config)
    manager = PresentationJobManager(
        tmp_path / "presentation-state", FakeToolchain()
    )
    service.presentation_jobs = manager
    catalog = _catalog()
    snapshot = canonical_json_sha256(catalog)
    details = deepcopy(_details())
    image_path = (
        WORKSPACE
        / "staging/coordination/deeptutor_gateway/fixtures/presentation_workbench_v1/fixture_a/assets/a-apparatus.png"
    )
    image = image_path.read_bytes()
    width, height = struct.unpack(">II", image[16:24])
    digest = hashlib.sha256(image).hexdigest()
    details["ATOM-01"]["evidence_descriptors"] = [
        {
            "crop_id": "QUESTION-CROP-01",
            "evidence_role": "question",
            "source_page": 3,
            "width": width,
            "height": height,
            "sha256": digest,
        }
    ]
    details["ATOM-02"]["evidence_descriptors"] = [
        {
            "crop_id": "SHARED-CELL-01",
            "evidence_role": "shared_material",
            "source_page": 3,
            "width": width,
            "height": height,
            "sha256": digest,
        }
    ]
    service.workbench_registry = lambda principal: {
        "products": [{"scope": "wave1", "data_snapshot_id": snapshot}]
    }
    service.theme_workbench_groups = lambda principal, *, scope: deepcopy(catalog)
    service._presentation_detail = (
        lambda principal, scope, atomic_id: deepcopy(details[atomic_id])
    )
    service._presentation_crop = (
        lambda principal, scope, atomic_id, crop_id: CandidateCropPayload(
            data=image, sha256=digest
        )
    )
    principal = config.principals[0]
    try:
        request = _real_theme_request()
        request["data_snapshot_id"] = snapshot
        result = service.presentation_create_from_theme(principal, request)
        assert result["project"]["title_zh"] == "电化学证据链"
        assert result["outline"]["deck_json"]["theme_binding"]["theme_id"] == (
            "THEME-REAL-01"
        )
        assert "presentation_input" not in result["outline"]
        assert "presentation_input_sha256" not in result["outline"]
        assert result["outline"]["deck_json"]["theme_binding"][
            "asset_ids_in_order"
        ]
        assets = list((tmp_path / "presentation-state").glob("tenants/*/projects/*/assets/*"))
        # Both source roles used the same verified bytes, so content addressing
        # stores one immutable file while both crop IDs bind to it.
        assert len(assets) == 1 and assets[0].read_bytes() == image

        listing = service.presentation_projects(principal)
        assert listing["count"] == 1
        detail = service.presentation_project_get(
            principal, result["project"]["project_id"]
        )
        assert detail["outline"]["revision"] == result["outline"]["revision"]
        assert detail["versions"] == []

        outline = service.presentation_outline_get(
            principal, result["project"]["project_id"]
        )
        replay = service.presentation_outline_update(
            principal,
            result["project"]["project_id"],
            {
                "expected_revision": outline["revision"],
                "deck_json": outline["deck_json"],
            },
        )
        assert replay["revision"] == outline["revision"]
        assert replay["idempotent_replay"] is True
        with pytest.raises(ApiError) as stale:
            service.presentation_outline_update(
                principal,
                result["project"]["project_id"],
                {
                    "expected_revision": "PPTOL-" + "f" * 32,
                    "deck_json": outline["deck_json"],
                },
            )
        assert stale.value.status == 409
        assert stale.value.code == "presentation_outline_revision_conflict"

        version = service.presentation_version_create(
            principal,
            result["project"]["project_id"],
            {"expected_outline_revision": outline["revision"]},
        )
        versions = service.presentation_versions(
            principal, result["project"]["project_id"]
        )
        assert versions["count"] == 1
        assert versions["versions"][0]["version_id"] == version["version_id"]
        assert service.presentation_version_get(
            principal,
            result["project"]["project_id"],
            version["version_id"],
        ) == versions["versions"][0]
    finally:
        service.shutdown()
