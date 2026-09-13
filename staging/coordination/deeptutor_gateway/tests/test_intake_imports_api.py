from __future__ import annotations

import http.client
import io
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from PIL import Image

from integrations.deeptutor_shchem_v1.config import AppConfig, Principal, token_digest
from integrations.deeptutor_shchem_v1.http_app import create_server
from integrations.deeptutor_shchem_v1.intake_imports import (
    INTAKE_IMPORT_WRITE_CAPABILITY,
    INTAKE_VISUAL_EXECUTE_CAPABILITY,
    IntakeImportJobManager,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import ProbeTransportResponse
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsStore,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
FIXTURE = (
    WORKSPACE / "staging/coordination/deeptutor_gateway/fixtures/mock_gateway_v1.json"
)
FULL_TOKEN = "intake-full-teacher-token-0123456789"
WRITE_TOKEN = "intake-write-teacher-token-0123456789"
READ_TOKEN = "intake-read-teacher-token-0123456789"
STUDENT_TOKEN = "intake-student-token-0123456789"
SECRET = "sk-api-intake-fake-secret-0123456789"


class FakeCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def available(self) -> bool:
        return True

    def write(self, target_name: str, secret: str) -> None:
        self.values[target_name] = secret

    def read(self, target_name: str) -> str | None:
        return self.values.get(target_name)

    def exists(self, target_name: str) -> bool:
        return target_name in self.values

    def delete(self, target_name: str) -> bool:
        return self.values.pop(target_name, None) is not None


def _candidate() -> dict[str, Any]:
    evidence = [
        {
            "page": 1,
            "bbox": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.2},
        }
    ]
    return {
        "schema_version": "shchem.intake-visual-candidate.v1",
        "candidate_status": "candidate_only",
        "paper_identity_candidates": [],
        "theme_boundaries": [
            {
                "theme_candidate_id": "THEME-1",
                "title_zh": "测试主题",
                "order": 1,
                "page_span": {"start_page": 1, "end_page": 1},
                "confidence": 0.9,
                "evidence": evidence,
            }
        ],
        "printed_question_candidates": [
            {
                "printed_candidate_id": "PRINTED-1",
                "theme_candidate_id": "THEME-1",
                "question_number": "1",
                "page_span": {"start_page": 1, "end_page": 1},
                "shared_material_refs": [],
                "confidence": 0.9,
                "evidence": evidence,
            }
        ],
        "atomic_part_candidates": [
            {
                "atomic_candidate_id": "ATOMIC-1",
                "printed_candidate_id": "PRINTED-1",
                "part_label": None,
                "item_type": "short_fill",
                "confidence": 0.9,
                "evidence": evidence,
            }
        ],
        "shared_material_candidates": [],
        "answer_page_mappings": [],
        "textbook_mapping_candidates": [],
        "cognitive_difficulty_candidates": [],
        "review_blockers": [
            {
                "code": "unreadable_visual",
                "details_zh": "测试页不含真实题面，保留教师复核阻断。",
                "evidence": [],
            }
        ],
        "requires_teacher_review": True,
        "central_registry_write": False,
    }


class FakeVisualTransport:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        del deadline_monotonic
        self.requests.append(request)
        assert cancel_event.is_set() is False
        response = {
            "status": "completed",
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(_candidate(), ensure_ascii=False),
                        }
                    ]
                }
            ]
        }
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=json.dumps(response, ensure_ascii=False).encode("utf-8"),
            latency_ms=5,
            model_invoked=True,
        )


def _png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (64, 96), "white").save(stream, format="PNG")
    return stream.getvalue()


@contextmanager
def running_server(tmp_path: Path) -> Iterator[tuple[Any, FakeVisualTransport, Path]]:
    config = AppConfig(
        bind_host="127.0.0.1",
        port=0,
        max_request_bytes=1024 * 1024,
        max_upload_bytes=512 * 1024,
        mode="mock",
        state_root=tmp_path / "gateway-state",
        shchem_root=SHCHEM_ROOT,
        overlay_root=OVERLAY,
        fixture_path=FIXTURE,
        intake_import_root=tmp_path / "external-intake",
        principals=[
            Principal(
                "teacher-full",
                "teacher",
                token_digest(FULL_TOKEN),
                (),
                (INTAKE_IMPORT_WRITE_CAPABILITY, INTAKE_VISUAL_EXECUTE_CAPABILITY),
            ),
            Principal(
                "teacher-write",
                "teacher",
                token_digest(WRITE_TOKEN),
                (),
                (INTAKE_IMPORT_WRITE_CAPABILITY,),
            ),
            Principal("teacher-read", "teacher", token_digest(READ_TOKEN), (), ()),
            Principal("student", "student", token_digest(STUDENT_TOKEN), (), ()),
        ],
        allowed_origins=(),
    )
    config.validate()
    backend = FakeCredentialBackend()
    store = ModelProviderSettingsStore(
        tmp_path / "provider-settings",
        project_root=WORKSPACE,
        credential_backend=backend,
    )
    profile = store.upsert_metadata(
        {
            "profile_id": "vision-profile",
            "provider_id": "openai",
            "model_id": "gpt-5-mini",
            "base_url_policy": "openai_official_https_v1",
            "allowed_data_classes": ["synthetic_only", "source_page_image"],
            "image_egress": "teacher_confirmed_source_pages",
            "last_probe": None,
        },
        expected_revision=None,
    )
    profile = store.put_credential(
        "vision-profile", SECRET, expected_revision=profile["revision"]
    )
    transport = FakeVisualTransport()
    manager = IntakeImportJobManager(
        config.intake_import_root,
        project_root=WORKSPACE,
        provider_store=store,
        transport=transport,
        max_upload_bytes=config.max_upload_bytes,
    )
    server = create_server(config)
    server.service.model_provider_settings = store
    server.service.model_provider_settings_error = None
    server.service.intake_import_jobs = manager
    server.intake_profile = profile
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, transport, config.intake_import_root
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(
    server: Any,
    method: str,
    path: str,
    *,
    token: str | None = FULL_TOKEN,
    json_body: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    content_type: str | None = None,
    origin: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=10
    )
    headers = dict(extra_headers or {})
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = f"http://127.0.0.1:{server.server_address[1]}"
        headers["Sec-Fetch-Site"] = "same-origin"
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        body = raw_body
        if content_type is not None:
            headers["Content-Type"] = content_type
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = {key: value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, json.loads(raw), response_headers


def request_bytes(
    server: Any,
    method: str,
    path: str,
    *,
    token: str = FULL_TOKEN,
    origin: bool = True,
) -> tuple[int, bytes, dict[str, str]]:
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=10
    )
    headers = {"Authorization": f"Bearer {token}"}
    if origin:
        headers["Origin"] = f"http://127.0.0.1:{server.server_address[1]}"
        headers["Sec-Fetch-Site"] = "same-origin"
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = {key: value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, raw, response_headers


def create_import(server: Any, *, token: str = FULL_TOKEN) -> tuple[str, bytes, dict[str, Any]]:
    raw = _png()
    status, envelope, _headers = request(
        server,
        "POST",
        "/api/v1/intake/imports",
        token=token,
        json_body={
            "source_role": "question_paper",
            "year": "unknown",
            "region_or_school": "unknown",
            "paper_type": "unknown",
            "filename": "paper.png",
            "mime_type": "image/png",
            "size_bytes": len(raw),
        },
    )
    assert status == 201
    return envelope["data"]["import_id"], raw, envelope["data"]


def test_http_full_lifecycle_raw_put_and_multimodal_candidate(tmp_path: Path) -> None:
    with running_server(tmp_path) as (server, transport, intake_root):
        import_id, raw, created = create_import(server)
        assert created["status"] == "awaiting_upload"
        status, uploaded, headers = request(
            server,
            "PUT",
            f"/api/v1/intake/imports/{import_id}/source",
            raw_body=raw,
            content_type="image/png",
        )
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        assert uploaded["data"]["status"] == "ready_for_analysis"
        assert uploaded["data"]["source"]["sha256"]

        profile = server.intake_profile
        status, queued, _headers = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/analyze",
            json_body={
                "profile_id": "vision-profile",
                "expected_revision": profile["revision"],
                "teacher_confirmed_egress": True,
            },
        )
        assert status == 202
        assert queued["data"]["status"] in {"queued_for_analysis", "analyzing"}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, current, _headers = request(
                server, "GET", f"/api/v1/intake/imports/{import_id}"
            )
            assert status == 200
            if current["data"]["status"] == "completed":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("HTTP intake analysis did not complete")
        job = current["data"]
        assert job["candidate"]["candidate_status"] == "candidate_only"
        assert job["candidate_sha256"]
        assert job["review"] == {
            "required": True,
            "status": "pending",
            "revision": 0,
            "decisions": [],
            "personal_library_visible": False,
        }
        assert job["central_registry_write"] is False
        assert job["attempts"][-1]["model_invoked"] is True
        assert job["attempts"][-1]["visual_api_invocation_allowed"] is True
        assert job["attempts"][-1]["request"]["direct_page_images"] is True
        assert job["attempts"][-1]["request"]["recognized_text_input_present"] is False
        assert job["attempts"][-1]["request"]["fallback_text_input_present"] is False
        assert len(transport.requests) == 1
        request_body = json.loads(transport.requests[0].body)
        assert any(
            item["type"] == "input_image"
            for item in request_body["input"][0]["content"]
        )
        serialized_job = json.dumps(job, ensure_ascii=False)
        assert SECRET not in serialized_job
        assert str(intake_root) not in serialized_job
        for path in intake_root.rglob("*"):
            if path.is_file() and path.name != ".intake-import.v1.lock":
                assert SECRET.encode() not in path.read_bytes()

        status, page_raw, page_headers = request_bytes(
            server,
            "GET",
            f"/api/v1/intake/imports/{import_id}/pages/1/content",
        )
        assert status == 200
        assert page_raw == raw
        assert page_headers["Content-Type"] == "image/png"
        assert page_headers["Cache-Control"] == "no-store"

        status, accepted, _headers = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/review",
            json_body={
                "expected_review_revision": 0,
                "candidate_sha256": job["candidate_sha256"],
                "decision": "accept_personal_library",
                "acknowledged_blocker_codes": ["unreadable_visual"],
                "teacher_note_zh": "已核对页面与候选结构，收录到个人题库。",
            },
        )
        assert status == 200
        assert accepted["data"]["review"]["status"] == "accepted_personal_library"
        assert accepted["data"]["review"]["revision"] == 1
        assert accepted["data"]["review"]["personal_library_visible"] is True

        status, library, _headers = request(
            server, "GET", "/api/v1/intake/personal-library"
        )
        assert status == 200
        assert library["data"]["schema_version"] == "shchem.personal-import-library.v1"
        assert library["data"]["count"] == 1
        assert library["data"]["items"][0]["import_id"] == import_id
        assert library["data"]["items"][0]["candidate_only"] is True

        status, repeated, _headers = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/review",
            json_body={
                "expected_review_revision": 1,
                "candidate_sha256": job["candidate_sha256"],
                "decision": "reject",
                "acknowledged_blocker_codes": [],
                "teacher_note_zh": "重复决定应失败。",
            },
        )
        assert status == 409
        assert repeated["error"]["code"] == "review_already_final"


def test_http_origin_role_and_separate_execute_capability(tmp_path: Path) -> None:
    with running_server(tmp_path) as (server, transport, _root):
        raw = _png()
        create_body = {
            "source_role": "question_paper",
            "year": "unknown",
            "region_or_school": "unknown",
            "paper_type": "unknown",
            "filename": "paper.png",
            "mime_type": "image/png",
            "size_bytes": len(raw),
        }
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/intake/imports",
            json_body=create_body,
            origin=False,
        )
        assert status == 403
        assert body["error"]["code"] == "csrf_origin_required"
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/intake/imports",
            token=READ_TOKEN,
            json_body=create_body,
        )
        assert status == 403
        assert body["error"]["code"] == "intake_import_write_capability_required"
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/intake/imports",
            token=STUDENT_TOKEN,
            json_body=create_body,
        )
        assert status == 403
        assert body["error"]["code"] == "teacher_scope_required"

        import_id, raw, _created = create_import(server, token=WRITE_TOKEN)
        status, _body, _ = request(
            server,
            "PUT",
            f"/api/v1/intake/imports/{import_id}/source",
            token=WRITE_TOKEN,
            raw_body=raw,
            content_type="image/png",
        )
        assert status == 200
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/analyze",
            token=WRITE_TOKEN,
            json_body={
                "profile_id": "vision-profile",
                "expected_revision": server.intake_profile["revision"],
                "teacher_confirmed_egress": True,
            },
        )
        assert status == 403
        assert body["error"]["code"] == "intake_visual_execute_capability_required"
        assert transport.requests == []
        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/intake/imports/{import_id}",
            token=STUDENT_TOKEN,
        )
        assert status == 403
        assert body["error"]["code"] == "teacher_scope_required"
        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/intake/imports/{import_id}",
            token=READ_TOKEN,
        )
        assert status == 404
        assert body["error"]["code"] == "import_not_found"
        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/intake/imports/{import_id}/pages/1/content",
            token=READ_TOKEN,
        )
        assert status == 404
        assert body["error"]["code"] == "import_not_found"
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/review",
            token=READ_TOKEN,
            json_body={
                "expected_review_revision": 0,
                "candidate_sha256": "0" * 64,
                "decision": "reject",
                "acknowledged_blocker_codes": [],
                "teacher_note_zh": "",
            },
        )
        assert status == 403
        assert body["error"]["code"] == "intake_import_write_capability_required"
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/review?x=1",
            token=WRITE_TOKEN,
            json_body={
                "expected_review_revision": 0,
                "candidate_sha256": "0" * 64,
                "decision": "reject",
                "acknowledged_blocker_codes": [],
                "teacher_note_zh": "",
            },
        )
        assert status == 400
        assert body["error"]["code"] == "intake_import_query_unsupported"
        status, personal, _ = request(
            server,
            "GET",
            "/api/v1/intake/personal-library",
            token=READ_TOKEN,
        )
        assert status == 200
        assert personal["data"] == {
            "schema_version": "shchem.personal-import-library.v1",
            "items": [],
            "count": 0,
        }
        status, body, _ = request(
            server,
            "GET",
            "/api/v1/intake/personal-library",
            token=STUDENT_TOKEN,
        )
        assert status == 403
        assert body["error"]["code"] == "teacher_scope_required"
        status, body, _ = request(
            server,
            "PUT",
            f"/api/v1/intake/imports/{import_id}/source",
            token=FULL_TOKEN,
            raw_body=raw,
            content_type="image/png",
        )
        assert status == 404
        assert body["error"]["code"] == "import_not_found"


def test_raw_body_is_accepted_only_on_exact_source_route_and_is_strict(tmp_path: Path) -> None:
    with running_server(tmp_path) as (server, _transport, _root):
        import_id, raw, _created = create_import(server)
        status, body, _ = request(
            server,
            "PUT",
            f"/api/v1/intake/imports/{import_id}/source?x=1",
            raw_body=raw,
            content_type="image/png",
        )
        assert status == 400
        assert body["error"]["code"] == "intake_import_query_unsupported"
        status, body, _ = request(
            server,
            "PUT",
            f"/api/v1/intake/imports/{import_id}/source",
            raw_body=raw,
            content_type="application/pdf",
        )
        assert status == 415
        assert body["error"]["code"] == "upload_mime_mismatch"
        status, body, _ = request(
            server,
            "PUT",
            f"/api/v1/intake/imports/{import_id}/source",
            raw_body=raw,
            content_type="image/png",
            extra_headers={"Content-Encoding": "gzip"},
        )
        assert status == 415
        assert body["error"]["code"] == "content_encoding_unsupported"
        status, body, _ = request(
            server,
            "PUT",
            "/api/v1/not-intake-source",
            raw_body=raw,
            content_type="image/png",
        )
        assert status == 415
        assert body["error"]["code"] == "unsupported_content_type"


def test_cancel_empty_body_contract_and_no_query_parameters(tmp_path: Path) -> None:
    with running_server(tmp_path) as (server, _transport, _root):
        import_id, _raw, _created = create_import(server)
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/cancel",
            json_body={"reason": "not accepted"},
        )
        assert status == 400
        assert body["error"]["code"] == "intake_cancel_request_invalid"
        status, body, _ = request(
            server,
            "POST",
            f"/api/v1/intake/imports/{import_id}/cancel",
            json_body={},
        )
        assert status == 200
        assert body["data"]["status"] == "cancelled"
