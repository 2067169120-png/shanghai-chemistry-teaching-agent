from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from integrations.deeptutor_shchem_v1.config import AppConfig, Principal, token_digest
from integrations.deeptutor_shchem_v1.http_app import create_server

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
FIXTURE = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/fixtures/mock_gateway_v1.json"
)
TOKEN = "student-recommendation-http-teacher-token"
STUDENT_ID = "11111111-2222-4333-8444-555555555555"
SUBMISSION_ID = "SUB-" + "a" * 32
MATCH_ID = "match_candidate_1"
ATOMIC_ID = "provisional_atomic_candidate_1"
SCORING_ID = "SVDEC-" + "b" * 32


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


def request(server, path: str):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=60
    )
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    connection.request(
        "GET",
        path,
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": origin},
    )
    response = connection.getresponse()
    data = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, data


def config(tmp_path: Path) -> AppConfig:
    value = AppConfig(
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
                "teacher-recommendation",
                "teacher",
                token_digest(TOKEN),
                ("*",),
            )
        ],
        students=(),
    )
    value.validate()
    return value


def test_http_preview_uses_teacher_section_and_returns_complete_theme_cards(
    tmp_path: Path, monkeypatch
) -> None:
    with running_server(config(tmp_path)) as server:
        visual = server.service.student_visual_analysis
        monkeypatch.setattr(visual, "submission_owner", lambda _submission: STUDENT_ID)
        monkeypatch.setattr(
            visual,
            "get_review",
            lambda _student, _submission: {
                "analysis": {
                    "candidate": {
                        "matches": [
                            {
                                "match_id": MATCH_ID,
                                "atomic_part_id": ATOMIC_ID,
                            }
                        ]
                    }
                },
                "scoring_decisions": [
                    {
                        "decision_id": SCORING_ID,
                        "teacher_score": 0.0,
                        "maximum_score": 1.0,
                    }
                ],
            },
        )
        diagnostic = {
            "submission_id": SUBMISSION_ID,
            "student_id": STUDENT_ID,
            "revision": "rev_" + "c" * 32,
            "chain_head_sha256": "d" * 64,
            "review": {"status": "teacher_decisions_recorded"},
            "diagnostic_decisions": [
                {
                    "sequence": 1,
                    "match_id": MATCH_ID,
                    "atomic_part_id": ATOMIC_ID,
                    "scoring_decision_id": SCORING_ID,
                    "decision": "accept",
                    "curriculum_sections": [
                        {
                            "section_key": "TB-M1-C1:1.2",
                            "section_number": "1.2",
                            "section_title": "物质的量",
                            "display_label_zh": "1.2 物质的量",
                            "chapter_id": "TB-M1-C1",
                            "chapter_title_zh": "第1章 化学研究的天地",
                            "volume_id": "TB-M1",
                            "volume_title_zh": "必修第一册",
                        }
                    ],
                }
            ],
        }
        monkeypatch.setattr(
            visual,
            "get_diagnostic_review",
            lambda _student, _submission: diagnostic,
        )

        status, envelope = request(
            server,
            f"/api/v1/submissions/{SUBMISSION_ID}/recommendation-preview",
        )
        assert status == 200
        preview = envelope["data"]
        assert preview["student_id"] == STUDENT_ID
        assert preview["candidate_only"] is True
        assert preview["read_only"] is True
        assert preview["long_term_update_allowed"] is False
        assert preview["scoring_confirmation"] == "teacher_confirmed"
        assert preview["evidence_sufficiency"] == "insufficient_evidence"
        assert preview["diagnosis_status"] == "provisional_weakness"
        assert preview["metrics"]["independent_source_count"] == 1
        assert preview["metrics"]["stable_weakness_allowed"] is False
        cards = [
            item
            for group in preview["scope_groups"]
            for item in group["items"]
        ]
        assert cards
        assert all(item["group_kind"] == "theme" for item in cards)
        assert all(item["paper"] and item["theme"] for item in cards)
        assert all(item["atomic_chain"] for item in cards)
        assert all(item["basket_selection"]["unit"] == "theme" for item in cards)
        assert all("整道主题大题" in item["recommendation_reason_zh"] for item in cards)

        status, envelope = request(
            server,
            f"/api/v1/submissions/{SUBMISSION_ID}/diagnostic-review",
        )
        assert status == 200
        assert envelope["data"] == diagnostic
