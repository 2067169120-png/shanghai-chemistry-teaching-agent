from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from PIL import Image

from integrations.deeptutor_shchem_v1.config import AppConfig, Principal, token_digest
from integrations.deeptutor_shchem_v1.http_app import create_server

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
FIXTURE = WORKSPACE / "staging/coordination/deeptutor_gateway/fixtures/mock_gateway_v1.json"
TOKEN = "student-visual-http-teacher-token"
REVISION = "rev_" + "a" * 32


def png_bytes(color: str) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (320, 240), color).save(stream, "PNG")
    return stream.getvalue()


@contextmanager
def running_server(config: AppConfig):
    server = create_server(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, method: str, path: str, *, payload=None, raw=None, content_type=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=5
    )
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    headers = {"Authorization": f"Bearer {TOKEN}", "Origin": origin}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        body = raw
        headers["Content-Type"] = content_type
    if body is not None:
        headers["Content-Length"] = str(len(body))
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, json.loads(data.decode())


def test_quick_student_visual_http_local_hold_and_restart(tmp_path: Path):
    config = AppConfig(
        bind_host="127.0.0.1",
        port=0,
        max_request_bytes=2 * 1024 * 1024,
        max_upload_bytes=1024 * 1024,
        mode="mock",
        state_root=tmp_path / "state",
        shchem_root=SHCHEM_ROOT,
        overlay_root=OVERLAY,
        fixture_path=FIXTURE,
        principals=[
            Principal(
                "teacher-visual",
                "teacher",
                token_digest(TOKEN),
                ("*",),
            )
        ],
        students=(),
    )
    config.validate()
    with running_server(config) as server:
        status, envelope = request(
            server,
            "POST",
            "/api/v1/students",
            payload={
                "grade": "高三",
                "retention_days": 30,
                "consent_recorded": True,
            },
        )
        assert status == 201
        student_id = envelope["data"]["student_id"]
        assert "capability" not in envelope["data"]
        status, envelope = request(
            server,
            "POST",
            "/api/v1/submissions",
            payload={"student_id": student_id, "workflow": "quick_single_work"},
        )
        assert status == 201
        submission = envelope["data"]
        for role, color in (("question_pages", "white"), ("student_work_pages", "ivory")):
            status, envelope = request(
                server,
                "POST",
                f"/api/v1/submissions/{submission['submission_id']}/files",
                payload={
                    "role": role,
                    "filename": f"{role}.png",
                    "mime_type": "image/png",
                    "expected_size_bytes": None,
                    "expected_sha256": None,
                    "expected_revision": submission["revision"],
                },
            )
            assert status == 201
            file_id = envelope["data"]["file"]["file_id"]
            status, envelope = request(
                server,
                "PUT",
                f"/api/v1/submissions/{submission['submission_id']}/files/{file_id}/content",
                raw=png_bytes(color),
                content_type="image/png",
            )
            assert status == 200
            submission = envelope["data"]
            file_record = next(
                item for item in submission["files"] if item["file_id"] == file_id
            )
            assert file_record["local_hold"]["contract_version"] == (
                "shchem.student-image-local-hold.v1"
            )
            assert file_record["local_hold"]["ocr_invoked"] is False
            assert file_record["local_hold"]["transport_attempt_count"] == 0
        hashes = [
            page["sha256"]
            for file_record in submission["files"]
            for page in file_record["pages"]
        ]
        status, envelope = request(
            server,
            "GET",
            f"/api/v1/submissions/{submission['submission_id']}/matching",
        )
        assert status == 200
        matching = envelope["data"]
        assert matching["matching"]["teacher_confirmed"] is False
        status, envelope = request(
            server,
            "PATCH",
            f"/api/v1/submissions/{submission['submission_id']}/matching",
            payload={
                "expected_revision": matching["revision"],
                "matches": matching["matching"]["matches"],
            },
        )
        assert status == 200
        matching = envelope["data"]
        assert matching["matching"]["teacher_confirmed"] is True
        submission["revision"] = matching["revision"]
        status, envelope = request(
            server,
            "POST",
            f"/api/v1/submissions/{submission['submission_id']}/privacy-decisions",
            payload={
                "expected_revision": submission["revision"],
                "decision": "approved",
                "contains_direct_identifiers": False,
                "confirmed_page_sha256": hashes,
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
                "teacher_confirmed_student_page_egress": True,
            },
        )
        assert status == 201
        submission = envelope["data"]
        status, envelope = request(
            server,
            "POST",
            f"/api/v1/submissions/{submission['submission_id']}/analyze",
            payload={
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        assert status == 202
        submission = envelope["data"]
        assert submission["status"] == "awaiting_visual_provider"
        assert submission["analysis_runs"][-1]["transport_attempt_count"] == 0
        assert submission["analysis_runs"][-1]["model_invoked"] is False
        submission_id = submission["submission_id"]

    with running_server(config) as restarted:
        status, envelope = request(
            restarted, "GET", f"/api/v1/submissions/{submission_id}/status"
        )
        assert status == 200
        assert envelope["data"]["status"] == "awaiting_visual_provider"
        assert envelope["data"]["analysis"] is None
