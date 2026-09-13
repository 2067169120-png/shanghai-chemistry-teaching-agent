from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from collections import Counter
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

WORKSPACE = Path(__file__).resolve().parents[4]

from integrations.deeptutor_shchem_v1.adapters import (
    AdapterUnavailable,
    CCSwitchClient,
    ControllerRpcAdapter,
)
from integrations.deeptutor_shchem_v1.config import (
    AppConfig,
    ConfigError,
    Principal,
    token_digest,
)
from integrations.deeptutor_shchem_v1.domain_adapters import GenerationDomainAdapter
from integrations.deeptutor_shchem_v1.http_app import (
    PERSONAL_AUTO_AUTH_BEARER,
    create_server,
)
from integrations.deeptutor_shchem_v1.security import SecurityError, safe_join
from integrations.deeptutor_shchem_v1.service import GatewayService
from integrations.deeptutor_shchem_v1.student_bridge import (
    StudentBridgeError,
    StudentDomainBridge,
)
from integrations.student_learning_v1.domain import StudentStore

TOKEN_A = "teacher-a-token-0123456789"
TOKEN_B = "teacher-b-token-0123456789"
STUDENT_A = "00000000-0000-4000-8000-000000000001"
STUDENT_B = "00000000-0000-4000-8000-000000000002"
FIXTURE = (
    WORKSPACE / "staging/coordination/deeptutor_gateway/fixtures/mock_gateway_v1.json"
)
CONTROLLER_DIAGNOSIS_ACCEPTANCE = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/fixtures/controller_diagnosis_acceptance_v2.json"
)
RETIRED_R5_CHAIN = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/fixtures/retired_r5_chain_v1.json"
)
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"


class OverlayDomParser(HTMLParser):
    VOID_TAGS = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self):
        super().__init__()
        self.starts: Counter[str] = Counter()
        self.ends: Counter[str] = Counter()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID_TAGS:
            self.starts[tag] += 1
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)

    def handle_endtag(self, tag):
        if tag not in self.VOID_TAGS:
            self.ends[tag] += 1


def build_config(
    state_root: Path, *, mode: str = "mock", max_request_bytes: int = 8192
) -> AppConfig:
    config = AppConfig(
        bind_host="127.0.0.1",
        port=0,
        max_request_bytes=max_request_bytes,
        max_upload_bytes=min(4096, max_request_bytes),
        mode=mode,
        state_root=state_root,
        shchem_root=SHCHEM_ROOT,
        overlay_root=OVERLAY,
        fixture_path=FIXTURE if mode == "mock" else None,
        controller_script=SHCHEM_ROOT / "scripts/sh_chem_agent.py",
        ccswitch_enabled=False,
        principals=[
            Principal("teacher-a", "teacher", token_digest(TOKEN_A), (STUDENT_A,)),
            Principal("teacher-b", "teacher", token_digest(TOKEN_B), (STUDENT_B,)),
        ],
        students=(STUDENT_A, STUDENT_B),
    )
    config.validate()
    return config


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


def request(
    server,
    method: str,
    path: str,
    *,
    token: str | None = TOKEN_A,
    payload=None,
    headers: dict[str, str] | None = None,
    timeout: float = 5,
):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=timeout
    )
    request_headers = dict(headers or {})
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
        request_headers["Content-Length"] = str(len(body))
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    data = response.read()
    content_type = response.getheader("Content-Type") or ""
    connection.close()
    if "application/json" in content_type:
        return response.status, json.loads(data.decode("utf-8")), response.headers
    return response.status, data, response.headers


class ConfigAndContractTests(unittest.TestCase):
    def test_non_loopback_bind_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            config.bind_host = "0.0.0.0"
            with self.assertRaisesRegex(ConfigError, "loopback"):
                config.validate()

    def test_ccswitch_rejects_any_non_contract_endpoint(self):
        with self.assertRaisesRegex(ValueError, "loopback port 15721"):
            CCSwitchClient(True, "http://127.0.0.1:8888", 1)

    def test_controller_missing_is_unknown_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            missing_root = Path(temp) / "sh-chem-db"
            missing_root.mkdir()
            adapter = ControllerRpcAdapter(missing_root, None, 1)
            status = adapter.availability()
            self.assertFalse(status["available"])
            self.assertEqual(status["live_status"], "unknown_not_executed")
            self.assertEqual(status["live_validate"], "unknown_not_executed")

    def test_live_controller_status_uses_v2_json_contract(self):
        adapter = ControllerRpcAdapter(SHCHEM_ROOT, None, 10)
        status = adapter.call("status", {})
        self.assertEqual(status["contract_version"], "2.0.0")
        self.assertEqual(status["command"], "status")
        self.assertTrue(status["live_controller_output"])
        self.assertEqual(status["controller_exit_code"], 0)

    @mock.patch("integrations.deeptutor_shchem_v1.adapters.subprocess.run")
    def test_controller_subprocess_uses_no_visible_windows_flag(self, run):
        run.return_value = type(
            "Completed",
            (),
            {
                "stdout": json.dumps(
                    {"contract_version": "2.0.0", "command": "status", "ok": True}
                ),
                "stderr": "",
                "returncode": 0,
            },
        )()
        adapter = ControllerRpcAdapter(SHCHEM_ROOT, None, 10)
        adapter.call("status", {})
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        )

    def test_controller_preflight_rejects_paths_outside_allowlist(self):
        adapter = ControllerRpcAdapter(SHCHEM_ROOT, None, 10)
        with self.assertRaisesRegex(AdapterUnavailable, "configured roots"):
            adapter.call(
                "preflight",
                {"mode": "generate", "profile_path": WORKSPACE / "AGENTS.md"},
            )

    def test_safe_join_blocks_directory_traversal(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(SecurityError):
            safe_join(Path(temp), "..", "escape")

    def test_openapi_contract_contains_required_operations(self):
        text = (
            WORKSPACE
            / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
        ).read_text(encoding="utf-8")
        for route in (
            "/api/v1/status",
            "/api/v1/validate",
            "/api/v1/evidence/search",
            "/api/v1/preflight",
            "/api/v1/jobs/candidates",
            "/api/v1/students/{student_id}/diagnosis",
            "/api/v1/students/{student_id}/handouts/candidates",
            "/api/v1/figures/{figure_id}",
            "/api/v1/publication/preflight",
        ):
            self.assertIn(route, text)

    def test_teacher_console_contains_required_workflow_controls(self):
        html = (WORKSPACE / "runtime/deeptutor_shchem/overlay/index.html").read_text(
            encoding="utf-8"
        )
        for marker in (
            'id="student"',
            'id="images"',
            'id="grades"',
            'id="progress"',
            'id="attempts"',
            'id="timings"',
            'id="diagnose"',
            'id="paperCandidate"',
            'id="qaState"',
            'id="deliveryState"',
            'id="publicationState"',
            'id="failures"',
            'id="download"',
            'id="testDownload"',
            'id="diagnosisContributions"',
            'id="weeklySchedule"',
            'id="focusAllocation"',
            'id="masteryHistory"',
            'id="childChains"',
            'id="contentQaState"',
            'id="deliveryQaState"',
            'id="formalProviderState"',
            'id="artifactInventory"',
            "not_live_verified",
        ):
            self.assertIn(marker, html)
        script = (
            WORKSPACE / "runtime/deeptutor_shchem/overlay/app.js"
        ).read_text(encoding="utf-8")
        for dynamic_field in (
            "printed_count",
            "atomic_count",
            "exact_registered_child_count",
            "printed_question_ids",
            "atomic_part_ids",
            "exact_registered_child_ids",
        ):
            self.assertIn(dynamic_field, script)

    def test_teacher_console_utf8_dom_structure_is_balanced(self):
        html_path = WORKSPACE / "runtime/deeptutor_shchem/overlay/index.html"
        html = html_path.read_bytes().decode("utf-8", errors="strict")
        self.assertNotIn("\ufffd", html)
        self.assertIn("沪上化学智研台", html)
        parser = OverlayDomParser()
        parser.feed(html)
        parser.close()
        self.assertEqual(parser.starts, parser.ends)
        self.assertTrue(
            {
                "student",
                "images",
                "grades",
                "progress",
                "attempts",
                "timings",
                "diagnose",
                "handout",
                "paperCandidate",
                "qaState",
                "download",
                "diagnosisContributions",
                "weeklySchedule",
                "focusAllocation",
                "masteryHistory",
                "childChains",
                "contentQaState",
                "deliveryQaState",
                "formalProviderState",
                "artifactInventory",
            }.issubset(parser.ids)
        )

    def test_gateway_domain_bridges_do_not_reference_deleted_internal_providers(self):
        bridge_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                WORKSPACE / "integrations/deeptutor_shchem_v1/domain_adapters.py",
                WORKSPACE / "integrations/deeptutor_shchem_v1/student_bridge.py",
            )
        )
        self.assertNotIn("FixtureContentProvider", bridge_sources)
        self.assertNotIn("shchem_generation_v2.content import", bridge_sources)
        self.assertNotIn("shchem_generation_v2.core import", bridge_sources)

    def test_production_child_counts_are_never_fixed_constants(self):
        production = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                WORKSPACE / "integrations/deeptutor_shchem_v1/domain_adapters.py",
                WORKSPACE / "runtime/deeptutor_shchem/overlay/app.js",
            )
        )
        self.assertNotRegex(
            production, r"(?:printed|atomic)_count\s*[:=]\s*\d+\b"
        )

        handoff = (
            WORKSPACE / "staging/coordination/deeptutor_gateway/HANDOFF.md"
        ).read_text(encoding="utf-8")
        roadmap = (
            WORKSPACE
            / "sh-chem-db/kb/roadmaps/deeptutor_shchem_integration_detailed.md"
        ).read_text(encoding="utf-8")
        combined = f"{handoff}\n{roadmap}"
        for marker in (
            "R12 prefreeze",
            "根批准",
            "双审",
            "对抗",
            "注册",
            "最终 sidecar",
        ):
            self.assertIn(marker, combined)
        self.assertIn("retired R5 set remains a negative fixture only", handoff)
        self.assertNotIn("public `gateway_api.py` has a Python `SyntaxError`", handoff)
        self.assertNotIn("当前 generation `content.py` 的 `VERSION_ID` 已前进到 R11", roadmap)

    def test_generation_delivery_requires_external_final_zip_attestation(self):
        archive_hash = "a" * 64
        atomic_ids = ["ATOM-A", "ATOM-B"]
        subject_hierarchy = {
            "printed_question_ids": ["PRINT-A"],
            "atomic_part_ids": atomic_ids,
        }
        missing = GenerationDomainAdapter.validate_external_delivery_attestation(
            {"sha256": archive_hash, "subject_hierarchy": subject_hierarchy}
        )
        self.assertFalse(missing["allowed"])
        self.assertIn("external_final_zip_attestation_required", missing["blockers"])
        ready = GenerationDomainAdapter.validate_external_delivery_attestation(
            {
                "sha256": archive_hash,
                "subject_hierarchy": subject_hierarchy,
                "external_delivery_attestation": {
                    "record_type": "external_teacher_managed_delivery_attestation",
                    "archive_sha256": archive_hash,
                    "teacher_managed_delivery_candidate": True,
                    "delivery_scope": "teacher_managed_private_delivery",
                    "attestation_included_in_bound_archive": False,
                    "human_reviewed": False,
                    "external_publication_allowed": False,
                    "publication_allowed": False,
                    "release_allowed": False,
                    "content_governance": {
                        "registry_binding": {
                            "atomic": [
                                {
                                    "artifact_id": atomic_id,
                                    "exact_registry_member": True,
                                }
                                for atomic_id in atomic_ids
                            ],
                            "atomic_count": len(atomic_ids),
                            "all_exact_members": True,
                        }
                    },
                },
            }
        )
        self.assertTrue(ready["allowed"])
        mismatched = copy.deepcopy(ready["attestation"])
        mismatched["content_governance"]["registry_binding"]["atomic"] = mismatched[
            "content_governance"
        ]["registry_binding"]["atomic"][:-1]
        blocked = GenerationDomainAdapter.validate_external_delivery_attestation(
            {
                "sha256": archive_hash,
                "subject_hierarchy": subject_hierarchy,
                "external_delivery_attestation": mismatched,
            }
        )
        self.assertFalse(blocked["allowed"])
        self.assertIn(
            "delivery_registry_exact_set_must_match_current_candidate_atomic_ids",
            blocked["blockers"],
        )

    def test_dynamic_content_registry_rejects_legacy_and_set_mutations(self):
        # The retired set is data, never an expected production count.
        retired_fixture = json.loads(RETIRED_R5_CHAIN.read_text(encoding="utf-8"))
        self.assertTrue(retired_fixture["synthetic_fixture_only"])
        self.assertFalse(retired_fixture["current_candidate_authority"])
        legacy_r5_ids = retired_fixture["atomic_part_ids"]
        current_atomic_ids = [*legacy_r5_ids, "CURRENT-CANDIDATE-EXTRA"]
        printed_ids = ["PRINT-A", "PRINT-B"]
        parent_map = {
            atomic_id: printed_ids[0 if index < len(current_atomic_ids) // 2 else 1]
            for index, atomic_id in enumerate(current_atomic_ids)
        }
        hierarchy = {
            "paper_id": "CURRENT-PAPER",
            "version_id": "CURRENT-REVISION",
            "paper_sha256": "a" * 64,
            "printed_question_ids": printed_ids,
            "atomic_part_ids": current_atomic_ids,
        }

        def valid_bundle():
            return {
                "paper_id": hierarchy["paper_id"],
                "version_id": hierarchy["version_id"],
                "paper_sha256": hierarchy["paper_sha256"],
                "item_count": len(current_atomic_ids),
                "catalog": [
                    {
                        "atomic_part_id": atomic_id,
                        "printed_question_id": parent_map[atomic_id],
                        "governance": {
                            "state": "automated_verified_candidate",
                            "central_registry_exact_member": True,
                            "human_reviewed": False,
                        },
                    }
                    for atomic_id in current_atomic_ids
                ],
            }

        governed = GenerationDomainAdapter.validate_content_bundle(
            valid_bundle(), hierarchy
        )
        self.assertEqual(governed["printed_count"], len(printed_ids))
        self.assertEqual(governed["atomic_count"], len(current_atomic_ids))
        self.assertEqual(
            governed["exact_registered_child_count"], len(current_atomic_ids)
        )

        mutations = {}
        legacy = valid_bundle()
        legacy["catalog"] = legacy["catalog"][: len(legacy_r5_ids)]
        legacy["item_count"] = len(legacy["catalog"])
        mutations["legacy_r5_fixed_set"] = legacy
        fewer = valid_bundle()
        fewer["catalog"] = fewer["catalog"][:-2]
        fewer["item_count"] = len(fewer["catalog"])
        mutations["missing_children"] = fewer
        extra = valid_bundle()
        extra["catalog"].append(
            {
                "atomic_part_id": "NOT-IN-CURRENT-CANDIDATE",
                "printed_question_id": printed_ids[-1],
                "governance": {
                    "state": "automated_verified_candidate",
                    "central_registry_exact_member": True,
                    "human_reviewed": False,
                },
            }
        )
        extra["item_count"] = len(extra["catalog"])
        mutations["extra_child"] = extra
        duplicate = valid_bundle()
        duplicate["catalog"][-1]["atomic_part_id"] = current_atomic_ids[0]
        mutations["duplicate_child"] = duplicate
        printed_as_atomic = valid_bundle()
        printed_as_atomic["catalog"][-1]["atomic_part_id"] = printed_ids[0]
        mutations["printed_id_used_as_atomic"] = printed_as_atomic
        registry_missing = valid_bundle()
        registry_missing["catalog"][-1]["governance"][
            "central_registry_exact_member"
        ] = False
        mutations["registry_not_exact"] = registry_missing

        for name, mutated in mutations.items():
            with self.subTest(name=name), self.assertRaises(AdapterUnavailable):
                GenerationDomainAdapter.validate_content_bundle(
                    copy.deepcopy(mutated), hierarchy
                )

    def test_generation_adapter_probes_actual_v2_public_surface(self):
        adapter = GenerationDomainAdapter()
        status = adapter.status()
        if not status["available"]:
            self.assertIsInstance(status["reason"], str)
            self.assertTrue(status["reason"])
            with self.assertRaises(AdapterUnavailable):
                adapter.build_candidate()
            with self.assertRaises(AdapterUnavailable):
                adapter.figure_get("FIG-GEN-V2-EWASTE-ELECTROLYSIS-001")
            return
        self.assertEqual(status["domain_version"], "2.0.0")
        hierarchy = status["candidate_subject_hierarchy"]
        if hierarchy is None:
            self.assertEqual(
                status["candidate_subject_hierarchy_reason"],
                "generation_public_candidate_binding_stale",
            )
        else:
            self.assertEqual(
                hierarchy["printed_count"], len(hierarchy["printed_question_ids"])
            )
            self.assertEqual(
                hierarchy["atomic_count"], len(hierarchy["atomic_part_ids"])
            )
            self.assertEqual(
                len(set(hierarchy["atomic_part_ids"])), hierarchy["atomic_count"]
            )
            self.assertTrue(hierarchy["counts_derived_not_constant"])
        try:
            figure = adapter.figure_get("FIG-GEN-V2-EWASTE-ELECTROLYSIS-001")
        except AdapterUnavailable as exc:
            self.assertTrue(exc.code)
        else:
            self.assertEqual(figure["status"], "candidate")
            self.assertEqual(figure["media_type"], "image/svg+xml")
            self.assertFalse(figure["publication_allowed"])
        try:
            candidate = adapter.build_candidate()
        except AdapterUnavailable as exc:
            self.assertTrue(exc.code)
        else:
            self.assertEqual(candidate["status"], "candidate_staged")
            self.assertFalse(candidate["publication_allowed"])

    def test_teacher_delivery_gate_requires_hash_bound_automated_candidate(self):
        blocked = GatewayService._delivery_gate(
            {
                "content_status": "automated_verified_candidate",
                "candidate_sha256": "a" * 64,
                "machine_review_chain": {
                    "status": "pass",
                    "receipt_hashes": ["b" * 64, "c" * 64, "d" * 64],
                },
            }
        )
        self.assertFalse(blocked["allowed"])
        self.assertIn(
            "teacher_managed_delivery_candidate_true_required", blocked["blockers"]
        )

        allowed = GatewayService._delivery_gate(
            {
                "content_status": "automated_verified_candidate",
                "teacher_managed_delivery_candidate": True,
                "candidate_sha256": "a" * 64,
                "machine_review_chain": {
                    "status": "pass",
                    "receipt_hashes": ["b" * 64, "c" * 64, "d" * 64],
                },
            }
        )
        self.assertFalse(allowed["allowed"])
        self.assertIn("external_final_zip_attestation_required", allowed["blockers"])
        self.assertFalse(allowed["external_publication_allowed"])
        self.assertFalse(allowed["official_claim_allowed"])
        self.assertFalse(allowed["human_reviewed"])

    def test_student_bundle_delivery_gate_requires_full_formal_inventory(self):
        candidate_hash = "e" * 64
        sidecar_hash = "f" * 64
        incomplete = GatewayService._delivery_gate(
            {
                "weekly_bundle": {
                    "manifest": {
                        "schema_version": "student_week_zip_manifest_v1",
                        "manifest_scope": "sidecar_final_zip_hash_bound",
                        "claim_scope": "machine_only_real_student_candidate",
                        "teacher_managed_delivery_candidate": True,
                        "teacher_managed_delivery_reasons": [],
                        "zip_sha256": candidate_hash,
                        "human_reviewed": False,
                    },
                    "verification": {
                        "status": "PASS_MACHINE_ONLY_FORMAL_CONTENT_CANDIDATE",
                        "zip_sha256": candidate_hash,
                    },
                }
            }
        )
        self.assertFalse(incomplete["allowed"])
        self.assertIn(
            "23_artifact_8_document_inventory_required", incomplete["blockers"]
        )
        self.assertIn("live_generation_provider_required", incomplete["blockers"])
        self.assertIn("student_bundle_hash_verification_required", incomplete["blockers"])

        allowed = GatewayService._delivery_gate(
            {
                "weekly_bundle": {
                    "manifest": {
                        "schema_version": "student_week_zip_manifest_v1",
                        "manifest_scope": "sidecar_final_zip_hash_bound",
                        "claim_scope": "machine_only_real_student_candidate",
                        "teacher_managed_delivery_candidate": True,
                        "teacher_managed_delivery_reasons": [],
                        "zip_sha256": candidate_hash,
                        "sidecar_sha256": sidecar_hash,
                        "artifact_count": 23,
                        "formal_docx_pdf_included": True,
                        "provider_production_ready": True,
                        "human_reviewed": False,
                    },
                    "verification": {
                        "status": "PASS_MACHINE_ONLY_FORMAL_CONTENT_CANDIDATE",
                        "zip_sha256": candidate_hash,
                        "sidecar_sha256": sidecar_hash,
                        "artifact_count": 23,
                        "formal_document_count": 8,
                    },
                }
            }
        )
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["candidate_sha256"], candidate_hash)
        self.assertFalse(allowed["external_publication_allowed"])

    def test_teacher_delivery_returns_domain_final_zip_without_repacking(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            config = build_config(temp_root / "gateway", mode="mock")
            config.student_data_root = temp_root / "student-domain"
            service = GatewayService(config)
            job_id = "job-domain-final-byte-equality"
            profile_root = (
                config.student_data_root
                / "private_profiles"
                / STUDENT_A[:2]
                / STUDENT_A
                / "bundles"
            )
            profile_root.mkdir(parents=True)
            domain_zip = profile_root / "week-01.zip"
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for index in range(23):
                    archive.writestr(f"artifact-{index:02d}.json", b"{}\n")
            domain_bytes = zip_buffer.getvalue()
            domain_zip.write_bytes(domain_bytes)
            zip_sha256 = hashlib.sha256(domain_bytes).hexdigest()
            sidecar = {
                "manifest_scope": "sidecar_final_zip_hash_bound",
                "zip_sha256": zip_sha256,
                "artifact_count": 23,
            }
            sidecar_bytes = (
                json.dumps(sidecar, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            domain_zip.with_suffix(".manifest.sidecar.json").write_bytes(sidecar_bytes)
            sidecar_sha256 = hashlib.sha256(sidecar_bytes).hexdigest()
            service.store.write_job(
                STUDENT_A,
                {
                    "job_id": job_id,
                    "status": "completed",
                    "artifact": {
                        "access_scope": "teacher_managed_delivery_candidate",
                        "sha256": zip_sha256,
                    },
                    "delivery_gate": {"allowed": True},
                    "private_paths": {"domain_artifact": str(domain_zip)},
                    "result": {
                        "weekly_bundle": {
                            "manifest": {
                                "manifest_scope": "sidecar_final_zip_hash_bound",
                                "zip_sha256": zip_sha256,
                                "sidecar_sha256": sidecar_sha256,
                                "artifact_count": 23,
                                "formal_docx_pdf_included": True,
                                "provider_production_ready": True,
                            }
                        }
                    },
                },
            )

            returned = service.artifact_path(config.principals[0], STUDENT_A, job_id)
            self.assertEqual(returned, domain_zip.resolve())
            self.assertEqual(returned.read_bytes(), domain_bytes)
            self.assertEqual(hashlib.sha256(returned.read_bytes()).hexdigest(), zip_sha256)
            self.assertFalse(any(config.state_root.rglob(f"{job_id}.zip")))


class StudentDomainBridgeTests(unittest.TestCase):
    def test_attempt_and_timing_input_kinds_map_to_student_public_api(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudentStore(Path(temp))
            scope = store.create_profile(
                grade="\u9ad8\u4e09",
                grade_progress={
                    "module": "synthetic-input-map",
                    "completion_percent": 50,
                },
                consent_recorded=True,
                retention_days=7,
                synthetic=True,
            )
            bridge = StudentDomainBridge(
                Path(temp), {scope.profile_id: scope.capability}
            )
            attempt = bridge.append_structured_input(
                profile_id=scope.profile_id,
                kind="attempt",
                payload={
                    "attempt_id": "GW-UPLOAD-A1",
                    "question_id": "GW-UPLOAD-Q1",
                    "attempted_at": "2026-08-13T00:00:00Z",
                    "student_response": "synthetic response",
                    "result": {
                        "status": "incorrect",
                        "earned_points": 0,
                        "max_points": 2,
                    },
                    "error_types": ["concept"],
                    "elapsed_seconds": 60,
                    "question_evidence": {
                        "fixture_scope": "synthetic_fixture_only",
                        "synthetic": True,
                        "canonical_atomic_unit_id": "GW-UPLOAD-UNIT-1",
                        "source_group_id": "GW-UPLOAD-SOURCE-1",
                        "question_id": "GW-UPLOAD-Q1",
                        "knowledge_tag": "K-SYNTH",
                        "knowledge_name": "synthetic knowledge",
                        "ability_tag": "A-SYNTH",
                        "ability_name": "synthetic ability",
                    },
                },
            )
            timing = bridge.append_structured_input(
                profile_id=scope.profile_id,
                kind="timing",
                payload={"attempt_id": "GW-UPLOAD-A1", "elapsed_seconds": 60},
            )
            self.assertEqual(attempt["event_type"], "attempt")
            self.assertEqual(timing["event_type"], "timing")

    def test_gateway_uses_versioned_student_receipt_and_never_reads_blocked_raw(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudentStore(Path(temp))
            scope = store.create_profile(
                grade="高三",
                grade_progress={
                    "module": "synthetic-test",
                    "completion_percent": 50,
                },
                consent_recorded=True,
                retention_days=7,
                synthetic=True,
            )
            bridge = StudentDomainBridge(
                Path(temp), {scope.profile_id: scope.capability}
            )
            self.assertTrue(bridge.status()["available"])
            self.assertTrue(
                bridge.status()["capabilities"]["real_diagnosis"]
            )
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
            record, receipt = bridge.import_image(
                profile_id=scope.profile_id,
                image_bytes=png,
                mime_type="image/png",
                source_ref="synthetic-bridge-test",
                input_deidentified=None,
                input_contains_face=None,
            )
            self.assertEqual(
                receipt["contract_version"], "shchem.student-image-local-hold.v1"
            )
            self.assertFalse(receipt["raw_model_access_allowed"])
            self.assertFalse(receipt["egress_allowed"])
            self.assertFalse(receipt["ocr_invoked"])
            self.assertEqual(0, receipt["transport_attempt_count"])
            with self.assertRaises(StudentBridgeError):
                bridge.read_sanitized_for_model(
                    scope.profile_id, record["media_id"]
                )


class GatewaySecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = build_config(Path(self.temp.name))
        self.context = running_server(self.config)
        self.server = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_authentication_is_required_and_bad_token_is_rejected(self):
        status, body, _ = request(self.server, "GET", "/api/v1/status", token=None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "authentication_required")
        status, body, _ = request(
            self.server, "GET", "/api/v1/status", token="wrong-but-long-token"
        )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "invalid_credentials")

    def test_personal_auto_auth_is_explicit_loopback_only_and_cross_site_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            config.principals = [config.principals[0]]
            config.students = (STUDENT_A,)
            config.personal_auto_auth = True
            config.validate()
            with running_server(config) as server:
                origin = f"http://127.0.0.1:{server.server_address[1]}"
                auto_token = PERSONAL_AUTO_AUTH_BEARER.removeprefix("Bearer ")
                status, body, _ = request(
                    server,
                    "GET",
                    "/api/v1/status",
                    token=auto_token,
                    headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"},
                )
                self.assertEqual(status, 200)
                self.assertEqual(body["data"]["mode"], "mock")
                status, body, _ = request(
                    server,
                    "GET",
                    "/api/v1/status",
                    token=auto_token,
                    headers={
                        "Origin": "https://example.invalid",
                        "Sec-Fetch-Site": "cross-site",
                    },
                )
                self.assertEqual(status, 403)
                self.assertEqual(body["error"]["code"], "origin_denied")

    def test_cross_student_job_access_is_denied(self):
        status, job, _ = request(
            self.server,
            "POST",
            f"/api/v1/students/{STUDENT_A}/diagnosis",
            payload={"synthetic_fixture": True, "upload_ids": []},
        )
        self.assertEqual(status, 202)
        job_id = job["data"]["job_id"]
        status, denied, _ = request(
            self.server,
            "GET",
            f"/api/v1/students/{STUDENT_A}/jobs/{job_id}",
            token=TOKEN_B,
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied["error"]["code"], "student_scope_denied")

    def test_cross_student_learning_view_access_is_denied(self):
        status, denied, _ = request(
            self.server,
            "GET",
            f"/api/v1/students/{STUDENT_A}/learning-view",
            token=TOKEN_B,
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied["error"]["code"], "student_scope_denied")

    def test_path_traversal_is_not_routable(self):
        status, body, _ = request(
            self.server, "GET", "/api/v1/evidence/%2e%2e/%2e%2e/secret"
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "route_not_found")
        status, body, _ = request(
            self.server, "GET", "/overlay/%2e%2e/%2e%2e/AGENTS.md", token=None
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "path_not_allowed")

    def test_request_size_limit(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=5
        )
        connection.request(
            "POST",
            "/api/v1/evidence/search",
            body=b"{}",
            headers={
                "Authorization": f"Bearer {TOKEN_A}",
                "Content-Type": "application/json",
                "Content-Length": str(self.config.max_request_bytes + 1),
            },
        )
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        connection.close()
        self.assertEqual(response.status, 413)
        self.assertEqual(body["error"]["code"], "request_too_large")

    def test_audit_log_has_no_token_and_hashes_student_reference(self):
        request(self.server, "GET", "/api/v1/status")
        audit_path = Path(self.temp.name) / "audit/gateway.jsonl"
        for _ in range(50):
            if audit_path.is_file():
                break
            time.sleep(0.01)
        event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertNotIn(TOKEN_A, audit_path.read_text(encoding="utf-8"))
        self.assertNotIn("authorization", event)
        self.assertNotIn("student_id", event)

    def test_image_raw_is_saved_even_when_privacy_pipeline_is_unavailable(self):
        png = base64.b64encode(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        ).decode("ascii")
        status, body, _ = request(
            self.server,
            "POST",
            f"/api/v1/students/{STUDENT_A}/uploads",
            payload={
                "filename": "error.png",
                "mime_type": "image/png",
                "kind": "student_work_image",
                "content_base64": png,
            },
        )
        self.assertEqual(status, 201)
        upload = body["data"]
        self.assertTrue(upload["original_saved_local_private"])
        self.assertFalse(upload["deidentification"]["egress_allowed"])
        originals = list(
            (Path(self.temp.name) / f"students/{STUDENT_A}/private/originals").glob("*")
        )
        self.assertEqual(len(originals), 1)


class MockEndToEndTests(unittest.TestCase):
    def test_mock_generation_exposes_dynamic_child_chains_and_two_qa_layers(self):
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            with running_server(config) as server:
                status, envelope, _ = request(
                    server,
                    "POST",
                    "/api/v1/jobs/candidates",
                    payload={
                        "student_id": STUDENT_A,
                        "kind": "generation_candidate",
                        "synthetic_fixture": True,
                        "upload_ids": [],
                    },
                )
                self.assertEqual(status, 202)
                job = envelope["data"]
                content = job["result"]["diagnostic_content"]
                hierarchy = content["subject_hierarchy"]
                self.assertEqual(
                    content["catalog_item_count"], hierarchy["atomic_count"]
                )
                self.assertEqual(
                    len(content["content_only_child_chain"]),
                    hierarchy["atomic_count"],
                )
                self.assertEqual(
                    [row["atomic_part_id"] for row in content["content_only_child_chain"]],
                    hierarchy["atomic_part_ids"],
                )
                self.assertEqual(
                    hierarchy["exact_registered_child_count"],
                    len(hierarchy["exact_registered_child_ids"]),
                )
                self.assertIn("content", job["quality_layers"])
                self.assertIn("delivery", job["quality_layers"])
                self.assertFalse(job["delivery_gate"]["allowed"])
                self.assertEqual(job["artifact"]["access_scope"], "synthetic_test_only")

    def test_upload_generate_qa_job_status_and_zip_download(self):
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            with running_server(config) as server:
                upload_payload = {
                    "filename": "grades.json",
                    "mime_type": "application/json",
                    "kind": "grades",
                    "content_base64": base64.b64encode(
                        json.dumps({"score": 72, "phone": "13800138000"}).encode(
                            "utf-8"
                        )
                    ).decode("ascii"),
                }
                status, upload_envelope, _ = request(
                    server,
                    "POST",
                    f"/api/v1/students/{STUDENT_A}/uploads",
                    payload=upload_payload,
                )
                self.assertEqual(status, 201)
                upload = upload_envelope["data"]
                self.assertTrue(upload["deidentification"]["egress_allowed"])

                status, job_envelope, _ = request(
                    server,
                    "POST",
                    f"/api/v1/students/{STUDENT_A}/diagnosis",
                    payload={
                        "synthetic_fixture": True,
                        "upload_ids": [upload["upload_id"]],
                    },
                )
                self.assertEqual(status, 202)
                job = job_envelope["data"]
                self.assertEqual(job["status"], "completed")
                self.assertEqual(job["machine_qa"]["status"], "pass")
                self.assertEqual(job["claim_scope"], "synthetic_fixture_only")
                self.assertFalse(job["human_reviewed"])
                self.assertFalse(job["delivery_gate"]["allowed"])
                self.assertEqual(job["artifact"]["access_scope"], "synthetic_test_only")

                status, status_envelope, _ = request(
                    server,
                    "GET",
                    f"/api/v1/jobs/{job['job_id']}?student_id={STUDENT_A}",
                )
                self.assertEqual(status, 200)
                self.assertEqual(status_envelope["data"]["job_id"], job["job_id"])

                status, zip_data, headers = request(
                    server,
                    "GET",
                    job["artifact"]["download_path"],
                )
                self.assertEqual(status, 200)
                self.assertIn("application/zip", headers.get("Content-Type"))
                with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
                    names = set(archive.namelist())
                    self.assertEqual(
                        names,
                        {
                            "MANIFEST.json",
                            "candidate.json",
                            "job.json",
                            "machine_qa.json",
                        },
                    )
                    manifest = json.loads(archive.read("MANIFEST.json"))
                    self.assertFalse(manifest["raw_uploads_included"])
                    self.assertFalse(manifest["publication_allowed"])

    def test_mock_refuses_unmarked_real_request_and_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            with running_server(config) as server:
                status, body, _ = request(
                    server,
                    "POST",
                    f"/api/v1/students/{STUDENT_A}/diagnosis",
                    payload={"synthetic_fixture": False, "upload_ids": []},
                )
                self.assertEqual(status, 202)
                self.assertEqual(body["data"]["status"], "failed")
                self.assertIn(
                    "mock_requires_explicit_synthetic_fixture_true",
                    body["data"]["failure_reasons"],
                )
                status, publication, _ = request(
                    server,
                    "POST",
                    "/api/v1/publication/preflight",
                    payload={"job_id": body["data"]["job_id"]},
                )
                self.assertEqual(status, 200)
                self.assertFalse(publication["data"]["publication_allowed"])
                self.assertIn(
                    "synthetic_fixture_not_publishable", publication["data"]["blockers"]
                )


class ControllerModeTests(unittest.TestCase):
    def test_real_diagnosis_three_stage_controller_contract_acceptance_fixture(self):
        fixture = json.loads(
            CONTROLLER_DIAGNOSIS_ACCEPTANCE.read_text(encoding="utf-8")
        )
        self.assertFalse(
            fixture["privacy_and_claim_boundary"]["real_person_data"]
        )
        self.assertFalse(
            fixture["privacy_and_claim_boundary"][
                "teaching_effectiveness_claimed"
            ]
        )
        trace: list[str] = []

        class ContractStudentBridge:
            def prepare_real_diagnosis(self, *, profile_id):
                trace.append("student_prepare_private_input")
                self.assert_profile(profile_id)
                preparation = dict(fixture["preparation"])
                if "central_receipt" in preparation[
                    "controller_input_without_receipt"
                ]:
                    raise AssertionError("caller receipt leaked into preparation")
                return preparation

            def issue_real_diagnosis_receipt(self, *, profile_id, prepare_id):
                trace.append(
                    "controller_live_issue_preflight_and_receipt_verify"
                )
                self.assert_profile(profile_id)
                if prepare_id != fixture["preparation"]["prepare_id"]:
                    raise AssertionError("prepare_id mismatch")
                return dict(fixture["controller_issue"])

            def execute_real_diagnosis(self, *, profile_id, prepare_id):
                trace.append("student_execute_diagnosis")
                self.assert_profile(profile_id)
                if prepare_id != fixture["preparation"]["prepare_id"]:
                    raise AssertionError("prepare_id mismatch")
                return dict(fixture["diagnosis"])

            @staticmethod
            def assert_profile(profile_id):
                if profile_id != fixture["profile_id"]:
                    raise AssertionError("profile mismatch")

        class NoDuplicateController:
            calls = 0

            def call(self, operation, payload):
                del operation, payload
                self.calls += 1
                raise AssertionError(
                    "Gateway must not duplicate student-domain controller calls"
                )

        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp), mode="controller")
            service = GatewayService(config)
            service.student_bridge = ContractStudentBridge()
            controller = NoDuplicateController()
            service.adapter = controller
            job = service.create_candidate_job(
                config.principals[0],
                STUDENT_A,
                "student_diagnosis",
                {"synthetic_fixture": False, "upload_ids": []},
            )
        self.assertEqual(job["status"], "completed")
        self.assertEqual(trace, fixture["expected_sequence"])
        self.assertEqual(controller.calls, 0)
        self.assertTrue(
            job["result"]["diagnosis"]["controller_diagnose_preflight"][
                "receipt_live_revalidated"
            ]
        )
        self.assertFalse(job["result"]["human_reviewed"])
        self.assertTrue(
            job["result"]["diagnosis"]["teaching_effectiveness_unverified"]
        )

    def test_generation_candidate_and_chain_precede_post_generation_preflight(self):
        trace: list[str] = []

        class OrderedGenerationBridge:
            @staticmethod
            def observed_profile_path():
                return Path("fixture-profile.json")

            @staticmethod
            def prepare_candidate(request, profile_path, content_output_path):
                del request, profile_path, content_output_path
                trace.extend(
                    [
                        "generation_public_job_staged",
                        "content_only_child_chains_registered",
                    ]
                )
                return {
                    "status": "blocked",
                    "blockers": [
                        "controller_native_chain",
                        "external_final_zip_attestation_required",
                    ],
                    "candidate": {
                        "status": "candidate_staged",
                        "machine_only": True,
                        "human_reviewed": False,
                        "publication_allowed": False,
                    },
                    "diagnostic_content": {"catalog_item_count": 25},
                    "post_preflight_inputs": None,
                    "sequence": [
                        "generation_public_job_staged",
                        "content_only_child_chains_registered",
                        "controller_post_generation_preflight",
                        "external_zip_delivery_attestation",
                    ],
                }

        class OrderedController:
            def call(self, operation, payload):
                del operation, payload
                self.calls += 1
                raise AssertionError(
                    "Gateway must not duplicate generation's postflight"
                )

        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp), mode="controller")
            service = GatewayService(config)
            service.generation_bridge = OrderedGenerationBridge()
            controller = OrderedController()
            controller.calls = 0
            service.adapter = controller
            job = service.create_candidate_job(
                config.principals[0],
                STUDENT_A,
                "generation_candidate",
                {"synthetic_fixture": False, "upload_ids": []},
            )
        self.assertEqual(job["status"], "failed")
        self.assertEqual(
            trace,
            [
                "generation_public_job_staged",
                "content_only_child_chains_registered",
            ],
        )
        self.assertEqual(controller.calls, 0)
        self.assertFalse(
            job["result"]["generation_orchestration"][
                "gateway_duplicate_controller_preflight"
            ]
        )

    def test_controller_mode_preflight_returns_503_without_controller(self):
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp), mode="controller")
            missing_root = Path(temp) / "missing-shchem"
            missing_root.mkdir()
            config.shchem_root = missing_root
            config.controller_script = missing_root / "scripts/sh_chem_agent.py"
            with running_server(config) as server:
                status, body, _ = request(
                    server,
                    "POST",
                    "/api/v1/preflight",
                    payload={"mode": "generate"},
                )
                self.assertEqual(status, 503)
                self.assertEqual(body["error"]["code"], "controller_unavailable")

    def test_controller_mode_status_is_live_and_preflight_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp), mode="controller")
            with running_server(config) as server:
                status, body, _ = request(server, "GET", "/api/v1/status")
                self.assertEqual(status, 200)
                self.assertTrue(body["data"]["controller"]["available"])
                self.assertEqual(
                    body["data"]["controller_status"]["contract_version"], "2.0.0"
                )
                self.assertTrue(
                    body["data"]["controller_status"]["live_controller_output"]
                )

                status, body, _ = request(
                    server,
                    "POST",
                    "/api/v1/preflight",
                    payload={"mode": "generate"},
                )
                self.assertEqual(status, 200)
                self.assertFalse(body["data"]["ready"])
                self.assertEqual(body["data"]["status"], "blocked")
                self.assertTrue(body["data"]["live_controller_output"])
                self.assertFalse(body["data"]["human_reviewed"])

    def test_actual_student_domain_diagnosis_and_week_manifest_bridge(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            student_root = temp_root / "student-domain"
            store = StudentStore(student_root)
            scope = store.create_profile(
                grade="\u9ad8\u4e09",
                grade_progress={
                    "module": "synthetic-contract-test",
                    "completion_percent": 50,
                },
                consent_recorded=True,
                retention_days=7,
                synthetic=True,
            )
            store.append_input(
                scope,
                "attempt",
                {
                    "attempt_id": "GW-SYNTH-A1",
                    "question_id": "GW-SYNTH-Q1",
                    "attempted_at": "2026-08-13T00:00:00Z",
                    "student_response": "synthetic response",
                    "result": {
                        "status": "incorrect",
                        "earned_points": 0,
                        "max_points": 2,
                    },
                    "error_types": ["concept"],
                    "elapsed_seconds": 60,
                    "question_evidence": {
                        "fixture_scope": "synthetic_fixture_only",
                        "synthetic": True,
                        "canonical_atomic_unit_id": "GW-SYNTH-UNIT-1",
                        "source_group_id": "GW-SYNTH-SOURCE-1",
                        "question_id": "GW-SYNTH-Q1",
                        "knowledge_tag": "K-SYNTH",
                        "knowledge_name": "synthetic knowledge",
                        "ability_tag": "A-SYNTH",
                        "ability_name": "synthetic ability",
                    },
                },
            )
            store.append_input(
                scope,
                "timing",
                {
                    "attempt_id": "GW-SYNTH-A1",
                    "elapsed_seconds": 60,
                },
            )
            config = build_config(temp_root / "gateway", mode="controller")
            config.student_data_root = student_root
            config.student_capabilities = {scope.profile_id: scope.capability}
            config.students = (scope.profile_id,)
            config.principals = [
                Principal(
                    "teacher-a",
                    "teacher",
                    token_digest(TOKEN_A),
                    (scope.profile_id,),
                )
            ]
            config.validate()
            with running_server(config) as server:
                upload_payload = {
                    "filename": "grades.json",
                    "mime_type": "application/json",
                    "kind": "grades",
                    "content_base64": base64.b64encode(
                        json.dumps(
                            {
                                "earned": 72,
                                "maximum": 100,
                                "assessment_ref": "synthetic-contract-test",
                            }
                        ).encode("utf-8")
                    ).decode("ascii"),
                }
                status, uploaded, _ = request(
                    server,
                    "POST",
                    f"/api/v1/students/{scope.profile_id}/uploads",
                    payload=upload_payload,
                )
                self.assertEqual(status, 201)
                self.assertTrue(
                    uploaded["data"]["deidentification"]["student_domain_binding"][
                        "recorded"
                    ]
                )
                progress_payload = {
                    "filename": "progress.json",
                    "mime_type": "application/json",
                    "kind": "progress",
                    "content_base64": base64.b64encode(
                        json.dumps(
                            {
                                "module": "synthetic-contract-test",
                                "completion_percent": 50,
                            }
                        ).encode("utf-8")
                    ).decode("ascii"),
                }
                status, progress, _ = request(
                    server,
                    "POST",
                    f"/api/v1/students/{scope.profile_id}/uploads",
                    payload=progress_payload,
                )
                self.assertEqual(status, 201)
                self.assertTrue(
                    progress["data"]["deidentification"]["student_domain_binding"][
                        "recorded"
                    ]
                )

                status, diagnosed, _ = request(
                    server,
                    "POST",
                    f"/api/v1/students/{scope.profile_id}/diagnosis",
                    payload={"synthetic_fixture": True, "upload_ids": []},
                )
                self.assertEqual(status, 202)
                diagnosis_job = diagnosed["data"]
                self.assertEqual(diagnosis_job["status"], "completed")
                self.assertEqual(
                    diagnosis_job["result"]["diagnosis"]["schema_version"],
                    "student_diagnosis_v2",
                )
                self.assertTrue(
                    diagnosis_job["result"]["controller_preflight"][
                        "live_controller_output"
                    ]
                )

                status, handout, _ = request(
                    server,
                    "POST",
                    f"/api/v1/students/{scope.profile_id}/handouts/candidates",
                    payload={
                        "synthetic_fixture": True,
                        "upload_ids": [],
                        "week_number": 1,
                    },
                )
                self.assertEqual(status, 202)
                handout_job = handout["data"]
                self.assertEqual(handout_job["status"], "completed")
                self.assertEqual(
                    handout_job["result"]["weekly_bundle"]["manifest"][
                        "schema_version"
                    ],
                    "student_week_zip_manifest_v1",
                )
                self.assertFalse(handout_job["delivery_gate"]["allowed"])
                self.assertNotIn("artifact", handout_job)

    def test_actual_generation_routes_gate_then_use_domain_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp), mode="controller")
            with running_server(config) as server:
                status, figure, _ = request(
                    server,
                    "GET",
                    "/api/v1/figures/FIG-GEN-V2-EWASTE-ELECTROLYSIS-001",
                )
                if status == 200:
                    self.assertEqual(status, 200)
                    self.assertEqual(figure["data"]["status"], "candidate")
                    self.assertFalse(figure["data"]["publication_allowed"])
                else:
                    self.assertEqual(status, 503)
                    self.assertTrue(figure["error"]["code"])

                status, candidate, _ = request(
                    server,
                    "POST",
                    "/api/v1/jobs/candidates",
                    payload={
                        "student_id": STUDENT_A,
                        "kind": "generation_candidate",
                        "upload_ids": [],
                    },
                )
                self.assertEqual(status, 202)
                self.assertEqual(candidate["data"]["status"], "failed")
                self.assertNotIn(
                    "controller_operation_not_supported",
                    candidate["data"]["failure_reasons"],
                )

                status, publication, _ = request(
                    server,
                    "POST",
                    "/api/v1/publication/preflight",
                    payload={},
                )
                self.assertEqual(status, 200)
                self.assertFalse(publication["data"]["publication_allowed"])
                self.assertIn(
                    "external_publication_interface_intentionally_closed",
                    publication["data"]["blockers"],
                )


if __name__ == "__main__":
    unittest.main()
