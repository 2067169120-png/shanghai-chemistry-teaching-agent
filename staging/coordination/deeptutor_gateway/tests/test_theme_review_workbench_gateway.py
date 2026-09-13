from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.public_kb import PublicKBReader
from integrations.deeptutor_shchem_v1.theme_review_workbench import (
    BOUNDARY_MODE_ATOMIC_ONLY,
    BOUNDARY_MODE_CANDIDATE_THEME,
    BOUNDARY_TASK,
    DEPENDENCY_RELATIONSHIP_KINDS,
    REVIEW_CANDIDATE_WRITE_CAPABILITY,
    REVIEW_DECISION_WRITE_CAPABILITY,
    REVIEW_TASK_WRITE_CAPABILITY,
    THEME_TASK,
    ThemeReviewGateway,
    ThemeReviewGatewayError,
    ThemeReviewTaskCatalog,
    _current_tag_values,
    _evidence_value,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader
from integrations.shchem_review_workbench_v1 import canonical_json_bytes

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"


@pytest.fixture(scope="module")
def catalog() -> ThemeReviewTaskCatalog:
    return ThemeReviewTaskCatalog.build_live(
        PublicKBReader(SHCHEM_ROOT), ThemeWorkbenchReader(SHCHEM_ROOT)
    )


def _assert_no_path_or_url(value):
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.casefold()
            assert lowered not in {
                "path",
                "relative_path",
                "absolute_path",
                "source_path",
                "url",
                "uri",
                "source_url",
                "event_relative_path",
                "commit_relative_path",
            }
            assert not lowered.endswith(("_path", "_url", "_uri"))
            _assert_no_path_or_url(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_path_or_url(child)
    elif isinstance(value, str):
        assert "://" not in value
        assert not value.startswith(("/", "\\"))
        assert not (len(value) >= 3 and value[1:3] in {":\\", ":/"})


def _authority_is_closed(value):
    assert value["candidate_only"] is True
    for key, item in value.items():
        if key != "candidate_only":
            assert item is False, key


def _empty_change_payload(detail, suffix: str) -> dict:
    return {
        "expected_revision": detail["revision"],
        "idempotency_key": f"changes-{suffix}-0001",
        "base_task_input_sha256": detail["base"]["task_input_sha256"],
        "tag_replacements": [],
        "hierarchy_replacements": [],
        "atomic_boundary_candidates": [],
        "dependency_replacements": [],
        "source_binding_candidates": [],
    }


def test_null_evidence_uses_frozen_candidates_without_fabricating_item_type():
    assert _evidence_value(
        {"value": None, "candidate_values": ["K03", "K10"]}
    ) == ["K03", "K10"]
    assert _evidence_value({"value": "K01", "candidate_values": ["K03"]}) == "K01"

    projected = _current_tag_values(
        {
            "item_type": {
                "value": None,
                "candidate_values": ["short_fill"],
            },
            "core_item_type": "reasoned_explanation",
            "selection_rule": "not_applicable",
            "primary_knowledge_K": {
                "value": None,
                "candidate_values": ["K03", "K10"],
            },
            "supporting_knowledge_K": {
                "value": None,
                "candidate_values": ["K05"],
            },
            "ability_A": {"value": None, "candidate_values": ["A02", "A05"]},
            "context_C": {"value": None, "candidate_values": ["C03"]},
            "response_R_evidence": {
                "value": None,
                "candidate_values": ["R03"],
            },
            "representation_RP_evidence": {
                "value": None,
                "candidate_values": ["RP01", "RP02"],
            },
            "difficulty": {"declared_prelabel": "D3"},
        }
    )
    assert projected == {
        "item_type": "short_fill",
        "selection_rule": "not_applicable",
        "primary_knowledge_K": "K03",
        "supporting_knowledge_K": ["K05"],
        "ability_A": ["A02", "A05"],
        "context_C": ["C03"],
        "response_R": "R03",
        "representation_RP": ["RP01", "RP02"],
        "cognitive_prelabel": "D3",
        "difficulty_factors": None,
    }

    unresolved = _current_tag_values(
        {
            "item_type": {"value": "unknown", "candidate_values": []},
            "core_item_type": "short_fill",
        }
    )
    assert unresolved["item_type"] is None


def test_real_master_tag_projection_preserves_candidate_coverage_and_gaps(
    catalog: ThemeReviewTaskCatalog,
):
    values = {
        row["atomic_part_id"]: row["values"]
        for task in catalog.tasks
        for row in task["current_tags"]
    }
    assert len(values) == 470
    assert {
        "item_type": sum(bool(row["item_type"]) for row in values.values()),
        "primary_knowledge_K": sum(
            bool(row["primary_knowledge_K"]) for row in values.values()
        ),
        "ability_A": sum(bool(row["ability_A"]) for row in values.values()),
        "context_C": sum(bool(row["context_C"]) for row in values.values()),
        "response_R": sum(bool(row["response_R"]) for row in values.values()),
        "representation_RP": sum(
            bool(row["representation_RP"]) for row in values.values()
        ),
        "cognitive_prelabel": sum(
            bool(row["cognitive_prelabel"]) for row in values.values()
        ),
    } == {
        "item_type": 448,
        "primary_knowledge_K": 470,
        "ability_A": 470,
        "context_C": 389,
        "response_R": 470,
        "representation_RP": 437,
        "cognitive_prelabel": 156,
    }
    assert sum(row["item_type"] is None for row in values.values()) == 22


def test_catalog_is_canonical_pure_and_accounts_for_every_master_gap(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    raw = catalog.canonical_bytes()
    restored = ThemeReviewTaskCatalog.from_bytes(raw, expected_sha256=catalog.sha256)
    assert restored.canonical_bytes() == raw
    assert restored.sha256 == catalog.sha256

    value = restored.as_dict()
    assert value["counts"] == {
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
    assert Counter(row["task_kind"] for row in value["tasks"]) == Counter(
        {THEME_TASK: 48, BOUNDARY_TASK: 99}
    )
    assert sum(
        row["boundary_mode"] == BOUNDARY_MODE_ATOMIC_ONLY
        for row in value["tasks"]
    ) == 94
    assert all(
        not row["target_atomic_part_ids"]
        for row in value["tasks"]
        if row["boundary_mode"] == BOUNDARY_MODE_ATOMIC_ONLY
    )

    first_batch = value["tasks"][:5]
    assert {row["boundary_mode"] for row in first_batch} == {
        BOUNDARY_MODE_CANDIDATE_THEME
    }
    assert {row["task_kind"] for row in first_batch} == {BOUNDARY_TASK}
    assert len({row["paper_id"] for row in first_batch}) == 1
    assert sum(len(row["target_printed_question_ids"]) for row in first_batch) == 41
    assert sum(len(row["target_atomic_part_ids"]) for row in first_batch) == 43
    assert all(row["candidate_theme_id"] == row["theme_id"] for row in first_batch)
    assert all("候选主题边界复核" in row["title_zh"] for row in first_batch)

    printed = [
        node_id
        for row in value["tasks"]
        for node_id in row["target_printed_question_ids"]
    ]
    atomic = [
        node_id
        for row in value["tasks"]
        for node_id in row["target_atomic_part_ids"]
    ]
    assert len(printed) == len(set(printed)) == 501
    assert len(atomic) == len(set(atomic)) == 470
    _authority_is_closed(value["authority"])
    _assert_no_path_or_url(value)

    state_root = tmp_path / "never-created-by-catalog-snapshot"
    gateway = ThemeReviewGateway(catalog=restored, state_root=state_root)
    snapshot = gateway.catalog_snapshot()
    assert not state_root.exists()
    assert snapshot["counts"]["tasks"] == 147
    assert len(snapshot["tasks"]) == 147
    assert snapshot["tasks"][0]["unit_kind"] == BOUNDARY_TASK
    assert snapshot["tasks"][0]["revision"] is None
    assert snapshot["tasks"][0]["status"] == "available"
    assert snapshot["tasks"][0]["base"]["task_input_sha256"]
    assert set(snapshot["tasks"][0]["allowed_actions"]) == {
        "claim",
        "release",
        "submit_change_set",
        "record_decision",
    }
    _assert_no_path_or_url(snapshot)


def test_catalog_hash_and_projection_mutations_fail_closed(
    catalog: ThemeReviewTaskCatalog,
):
    value = catalog.as_dict()
    value["tasks"][0]["task_input_sha256"] = "0" * 64
    with pytest.raises(ThemeReviewGatewayError) as captured:
        ThemeReviewTaskCatalog.from_bytes(canonical_json_bytes(value))
    assert captured.value.code == "theme_review_catalog_invalid"

    value = catalog.as_dict()
    value["tasks"][0]["source_path"] = "C:\\secret\\source.png"
    with pytest.raises(ThemeReviewGatewayError) as captured:
        ThemeReviewTaskCatalog.from_bytes(canonical_json_bytes(value))
    assert captured.value.code in {
        "theme_review_contract_invalid",
        "theme_review_catalog_invalid",
    }

    raw = catalog.canonical_bytes()
    with pytest.raises(ThemeReviewGatewayError) as captured:
        ThemeReviewTaskCatalog.from_bytes(raw, expected_sha256="f" * 64)
    assert captured.value.code == "theme_review_catalog_hash_mismatch"


def test_source_binding_projection_is_frozen_separated_and_conservative(
    catalog: ThemeReviewTaskCatalog,
):
    tasks = catalog.tasks
    candidate_counts = Counter(
        row["paper_id"] for row in tasks if row["source_binding_candidates"]
    )
    assert candidate_counts == Counter(
        {
            "MASTER-PAPER-21b2686786eef90582ca": 1,
            "MASTER-PAPER-4a39a5ecb376c90f8ed7": 1,
            "MASTER-PAPER-eca872096473cd6ec91a": 1,
        }
    )
    assert sum(bool(row["source_binding_candidates"]) for row in tasks) == 3
    assert sum(
        row["source_binding_candidates"][0]["accept_allowed"]
        for row in tasks
        if row["source_binding_candidates"]
    ) == 1
    assert Counter(
        row["source_binding"]["catalog_binding"]["status"] for row in tasks
    ) == Counter({"blocked": 104, "partial": 43})

    huaer = next(
        row
        for row in tasks
        if row["paper_id"] == "MASTER-PAPER-21b2686786eef90582ca"
        and row["source_binding_candidates"]
    )
    assert set(huaer["source_binding"]) == {
        "schema_version",
        "paper_id",
        "master_source_id",
        "source_version_id",
        "source_version_sha256",
        "source_layer",
        "package_id",
        "paper_face",
        "article_attribution",
        "catalog_binding",
        "authority",
        "blockers",
        "evidence_ids",
    }
    assert huaer["source_binding"]["paper_face"]["school_zh"] is None
    assert (
        huaer["source_binding"]["article_attribution"]["school_zh"]
        == "华东师范大学第二附属中学"
    )
    assert huaer["source_binding"]["catalog_binding"]["status"] == "partial"
    exact = huaer["source_binding_candidates"][0]
    assert exact["binding_state"] == "exact_content_set_candidate"
    assert exact["accept_allowed"] is True
    assert exact["source_id"].startswith("file-")

    fudan = next(
        row
        for row in tasks
        if row["paper_id"] == "MASTER-PAPER-4a39a5ecb376c90f8ed7"
        and row["source_binding_candidates"]
    )
    assert fudan["source_binding"]["paper_face"]["school_zh"] == "复旦大学附属中学"
    assert fudan["source_binding_candidates"][0]["binding_state"] == (
        "blocked_missing_source"
    )
    assert fudan["source_binding_candidates"][0]["accept_allowed"] is False

    datong = next(
        row
        for row in tasks
        if row["paper_id"] == "MASTER-PAPER-eca872096473cd6ec91a"
    )
    assert datong["source_binding"]["paper_face"]["school_zh"] is None
    assert datong["source_binding"]["article_attribution"]["school_zh"] == "上海市大同中学"
    assert datong["source_binding"]["catalog_binding"]["status"] == "blocked"

    unbound = next(
        row
        for row in tasks
        if row["source_binding"]["source_version_id"] is None
    )
    assert unbound["source_binding"]["source_version_id"] is None
    assert unbound["source_binding"]["source_version_sha256"] is None
    assert unbound["source_binding"]["catalog_binding"] == {
        "status": "blocked",
        "source_id": None,
        "match_basis": "no_frozen_crosswalk_candidate",
        "accept_allowed": False,
    }
    _assert_no_path_or_url(catalog.as_dict())


def test_source_crosswalk_raw_drift_fails_live_catalog_build(
    monkeypatch: pytest.MonkeyPatch,
):
    reader = PublicKBReader(SHCHEM_ROOT)
    original = reader._read_exact

    def tampered(relative: str, *, verify_manifest: bool = False):
        raw, digest = original(relative, verify_manifest=verify_manifest)
        if relative == reader.SOURCE_VERSION_CROSSWALK_DATA:
            raw = raw + b" "
            digest = hashlib.sha256(raw).hexdigest()
        return raw, digest

    monkeypatch.setattr(reader, "_read_exact", tampered)
    with pytest.raises(ThemeReviewGatewayError) as captured:
        ThemeReviewTaskCatalog.build_live(reader, ThemeWorkbenchReader(SHCHEM_ROOT))
    assert captured.value.code == "theme_review_source_binding_artifact_invalid"


@pytest.mark.parametrize(
    "relative",
    [
        "catalog.csv",
        "kb/corpus_manifest.jsonl",
        (
            "04_市重点校考卷/华东师范大学第二附属中学_文章标注/高二/"
            "2024学年第一学期期中-化学试卷与非官方参考答案/manifest.json"
        ),
        (
            "kb/formal/candidates/intake_round_09_2026-08-03/"
            "huaer_affiliated_high2_2024_fall_midterm/page_reviews.jsonl"
        ),
    ],
)
def test_relevant_live_crosswalk_upstream_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
):
    reader = PublicKBReader(SHCHEM_ROOT)
    original = reader._read_exact

    def tampered(requested: str, *, verify_manifest: bool = False):
        raw, digest = original(requested, verify_manifest=verify_manifest)
        if requested != relative:
            return raw, digest
        if requested == "catalog.csv":
            raw = raw.replace(
                b"file-c8c09896aa9dfe4835affdb6876773f9751b1a0132d5a86bbaaac462567b3a48",
                b"file-" + b"0" * 64,
                1,
            )
        elif requested == "kb/corpus_manifest.jsonl":
            raw = raw.replace(
                b"file-c8c09896aa9dfe4835affdb6876773f9751b1a0132d5a86bbaaac462567b3a48",
                b"file-" + b"0" * 64,
                1,
            )
        elif requested.endswith("page_reviews.jsonl"):
            lines = raw.splitlines()
            first = json.loads(lines[0].decode("utf-8"))
            first["sha256"] = "0" * 64
            lines[0] = json.dumps(
                first,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            raw = b"\n".join(lines) + (b"\n" if raw.endswith(b"\n") else b"")
        else:
            raw = raw + b" "
        return raw, hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(reader, "_read_exact", tampered)
    with pytest.raises(ThemeReviewGatewayError) as captured:
        ThemeReviewTaskCatalog.build_live(reader, ThemeWorkbenchReader(SHCHEM_ROOT))
    assert captured.value.code == "theme_review_source_binding_artifact_invalid"


def test_upstream_verification_receipt_is_safe_frozen_and_task_bound(
    catalog: ThemeReviewTaskCatalog,
):
    value = catalog.as_dict()
    receipt = value["source_snapshot"]["source_version_crosswalk"][
        "upstream_verification_receipt"
    ]
    assert receipt["verified"] is True
    assert receipt["catalog_row_count"] == 108
    assert receipt["corpus_row_count"] == 108
    assert receipt["paper_check_count"] == 3
    assert receipt["upstream_file_count"] == 9
    assert len(receipt["upstream_file_bindings"]) == 9
    assert sum(row["accept_allowed"] for row in receipt["paper_checks"]) == 1
    for task in value["tasks"]:
        assert any(
            row["role"] == "source_version_upstream_verification_receipt"
            for row in task["evidence_bindings"]
        )
    _assert_no_path_or_url(receipt)


def test_same_paper_has_one_source_action_task_and_sibling_cannot_conflict(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    paper_id = "MASTER-PAPER-21b2686786eef90582ca"
    paper_tasks = [row for row in catalog.tasks if row["paper_id"] == paper_id]
    canonical = [row for row in paper_tasks if row["source_binding_candidates"]]
    siblings = [row for row in paper_tasks if not row["source_binding_candidates"]]
    assert len(canonical) == 1
    assert len(siblings) == 42
    candidate = canonical[0]["source_binding_candidates"][0]
    assert candidate["accept_allowed"] is True
    assert all(
        row["source_binding"]["source_version_id"]
        == canonical[0]["source_binding"]["source_version_id"]
        for row in siblings
    )

    gateway = ThemeReviewGateway(
        catalog=catalog, state_root=tmp_path / "one-paper-one-source-action"
    )
    sibling = siblings[0]
    gateway.claim(
        sibling["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-source-sibling-0001"},
        principal_id="teacher.local",
    )
    detail = gateway.get(sibling["task_id"])
    payload = _empty_change_payload(detail, "source-sibling-conflict")
    payload["source_binding_candidates"] = [
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_sha256": candidate["candidate_sha256"],
            "source_version_id": candidate["source_version_id"],
            "action": "reject_binding_candidate",
            "reason_zh": "不得在同卷第二个任务写入冲突来源决定。",
            "evidence_ids": candidate["evidence_binding_ids"],
        }
    ]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(
            sibling["task_id"], payload, principal_id="teacher.local"
        )
    assert captured.value.code == "theme_review_source_binding_candidate_invalid"
    assert gateway.get(sibling["task_id"])["state"]["change_set_count"] == 0


def test_gateway_dto_claim_candidate_preview_decision_and_release(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    gateway = ThemeReviewGateway(
        catalog=catalog,
        state_root=tmp_path / "theme-review-state",
        release_context={
            "serving_release_id": "WBREL-test",
            "data_snapshot_id": "WB-DATA-test",
            "browse_snapshot_id": "WB-BROWSE-test",
        },
    )
    task_id = catalog.tasks[0]["task_id"]
    detail = gateway.get(task_id)
    assert detail["unit_kind"] == BOUNDARY_TASK
    assert detail["status"] == "available"
    assert detail["revision"] is None
    assert detail["assignee"] is None
    assert detail["base"]["data_snapshot_id"] == "WB-DATA-test"
    assert detail["gap_counts"]["missing_theme_parent_printed"] == 9
    assert len(detail["printed_questions"]) == 9
    assert len(detail["atomic_parts"]) == 9
    assert detail["latest_change_set"] is None
    assert detail["latest_decision"] is None
    _authority_is_closed(detail["authority"])

    claim = gateway.claim(
        task_id,
        {"expected_revision": None, "idempotency_key": "claim-theme-0001"},
        principal_id="teacher.local",
    )
    assert claim["state"]["claimed"] is True
    assert claim["state"]["claimed_by_principal_id"] == "teacher.local"
    detail = gateway.get(task_id)
    printed = detail["printed_questions"][0]
    payload = _empty_change_payload(detail, "hierarchy")
    payload["hierarchy_replacements"] = [
        {
            "printed_question_id": printed["printed_question_id"],
            "proposed_theme_id": detail["theme_id"],
            "reason_zh": "依据精确 crosswalk 证据复核该候选父链。",
            "evidence_ids": [printed["evidence_ids"][0]],
        }
    ]
    changed = gateway.change_set(task_id, payload, principal_id="teacher.local")
    assert changed["candidate_overlay_only"] is True
    change_set_id = changed["change_set_id"]

    preview = gateway.preview(task_id, change_set_id)
    assert set(preview) == {
        "schema_version",
        "candidate_overlay_preview",
        "validation",
        "authority",
    }
    assert preview["validation"]["valid"] is True
    assert preview["validation"]["status"] == "pass"
    assert preview["validation"]["central_apply_available"] is False
    projected = preview["candidate_overlay_preview"]
    assert projected["eligible_for_central_apply"] is False
    assert projected["hierarchy_replacements"][0]["before_parent_id"] is None
    assert (
        projected["hierarchy_replacements"][0]["after_parent_id"]
        == detail["theme_id"]
    )

    detail = gateway.get(task_id)
    decision = gateway.decision(
        task_id,
        {
            "expected_revision": detail["revision"],
            "idempotency_key": "decision-theme-0001",
            "change_set_id": change_set_id,
            "verdict": "accept_candidate_overlay",
            "reason_zh": "确认仅保留为候选覆盖，不写入 Master。",
            "evidence_ids": [printed["evidence_ids"][0]],
        },
        principal_id="teacher.local",
    )
    assert decision["verdict"] == "accept_candidate_overlay"
    assert decision["evidence_ids"] == [printed["evidence_ids"][0]]
    detail = gateway.get(task_id)
    assert detail["status"] == "decision_recorded_candidate_only"
    assert detail["latest_decision"]["human_reviewed"] is False

    released = gateway.release(
        task_id,
        {
            "expected_revision": detail["revision"],
            "idempotency_key": "release-theme-0001",
            "reason_zh": "本轮候选复核已结束。",
        },
        principal_id="teacher.local",
    )
    assert released["state"]["claimed"] is False
    _assert_no_path_or_url(gateway.get(task_id))


def test_browser_cannot_spoof_before_principal_or_authority(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    gateway = ThemeReviewGateway(
        catalog=catalog, state_root=tmp_path / "theme-review-state"
    )
    detail = gateway.get(catalog.tasks[0]["task_id"])
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.claim(
            detail["task_id"],
            {
                "expected_revision": None,
                "idempotency_key": "claim-spoof-0001",
                "principal_id": "attacker",
            },
            principal_id="teacher.local",
        )
    assert captured.value.code == "theme_review_contract_invalid"

    gateway.claim(
        detail["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-safe-0001"},
        principal_id="teacher.local",
    )
    detail = gateway.get(detail["task_id"])
    printed = detail["printed_questions"][0]
    payload = _empty_change_payload(detail, "spoof")
    payload["hierarchy_replacements"] = [
        {
            "printed_question_id": printed["printed_question_id"],
            "proposed_theme_id": detail["theme_id"],
            "reason_zh": "尝试伪造浏览器 before。",
            "evidence_ids": [printed["evidence_ids"][0]],
            "before_parent_id": "ATTACKER-CONTROLLED",
        }
    ]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(detail["task_id"], payload, principal_id="teacher.local")
    assert captured.value.code == "theme_review_contract_invalid"

    payload = _empty_change_payload(detail, "authority")
    payload["authority"] = {"human_reviewed": True}
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(detail["task_id"], payload, principal_id="teacher.local")
    assert captured.value.code == "theme_review_contract_invalid"


def test_frozen_source_candidate_actions_accept_only_exact_and_preview_v2(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    gateway = ThemeReviewGateway(
        catalog=catalog, state_root=tmp_path / "theme-review-state"
    )
    huaer = next(
        row
        for row in catalog.tasks
        if row["paper_id"] == "MASTER-PAPER-21b2686786eef90582ca"
        and row["source_binding_candidates"]
    )
    gateway.claim(
        huaer["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-source-exact-0001"},
        principal_id="teacher.local",
    )
    detail = gateway.get(huaer["task_id"])
    candidate = detail["source_binding_candidates"][0]
    payload = _empty_change_payload(detail, "source-exact")
    payload["source_binding_candidates"] = [
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_sha256": candidate["candidate_sha256"],
            "source_version_id": candidate["source_version_id"],
            "action": "accept_binding_candidate",
            "reason_zh": "九页页面哈希集合与中央记录精确一致，仅接受来源版本候选。",
            "evidence_ids": candidate["evidence_binding_ids"],
        }
    ]
    changed = gateway.change_set(
        huaer["task_id"], payload, principal_id="teacher.local"
    )
    preview = gateway.preview(huaer["task_id"], changed["change_set_id"])
    projected = preview["candidate_overlay_preview"]["source_binding_candidates"]
    assert projected[0]["action"] == "accept_binding_candidate"
    assert projected[0]["source_version_id"] == candidate["source_version_id"]
    assert preview["validation"]["frozen_source_binding_candidate_validated"] is True
    assert preview["candidate_overlay_preview"]["eligible_for_central_apply"] is False
    _assert_no_path_or_url(preview)
    current = gateway.get(huaer["task_id"])
    decision = gateway.decision(
        huaer["task_id"],
        {
            "expected_revision": current["revision"],
            "idempotency_key": "decision-source-exact-0001",
            "change_set_id": changed["change_set_id"],
            "verdict": "accept_candidate_overlay",
            "reason_zh": "确认仅接受卷级来源版本候选覆盖，不写中央主库。",
            "evidence_ids": candidate["evidence_binding_ids"],
        },
        principal_id="teacher.local",
    )
    assert decision["verdict"] == "accept_candidate_overlay"
    decided_preview = gateway.preview(huaer["task_id"], changed["change_set_id"])
    assert decided_preview["candidate_overlay_preview"]["verdict"] == (
        "accept_candidate_overlay"
    )
    assert decided_preview["candidate_overlay_preview"][
        "source_binding_candidates"
    ][0]["action"] == "accept_binding_candidate"

    blocked = next(
        row
        for row in catalog.tasks
        if row["paper_id"] == "MASTER-PAPER-4a39a5ecb376c90f8ed7"
        and row["source_binding_candidates"]
    )
    gateway.claim(
        blocked["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-source-blocked-0001"},
        principal_id="teacher.local",
    )
    blocked_detail = gateway.get(blocked["task_id"])
    blocked_candidate = blocked_detail["source_binding_candidates"][0]
    rejected = _empty_change_payload(blocked_detail, "source-blocked")
    rejected["source_binding_candidates"] = [
        {
            "candidate_id": blocked_candidate["candidate_id"],
            "candidate_sha256": blocked_candidate["candidate_sha256"],
            "source_version_id": blocked_candidate["source_version_id"],
            "action": "accept_binding_candidate",
            "reason_zh": "缺少中央同版本记录时不得接受。",
            "evidence_ids": blocked_candidate["evidence_binding_ids"],
        }
    ]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(
            blocked["task_id"], rejected, principal_id="teacher.local"
        )
    assert captured.value.code == "theme_review_source_binding_blocked"
    assert gateway.get(blocked["task_id"])["state"]["change_set_count"] == 0

    request_evidence = copy.deepcopy(rejected)
    request_evidence["idempotency_key"] = "changes-source-request-0001"
    request_evidence["source_binding_candidates"][0]["action"] = (
        "request_source_evidence"
    )
    request_evidence["source_binding_candidates"][0]["reason_zh"] = (
        "请补充中央 catalog 与 corpus 的同版本记录。"
    )
    accepted = gateway.change_set(
        blocked["task_id"], request_evidence, principal_id="teacher.local"
    )
    assert accepted["candidate_overlay_only"] is True


def test_source_candidate_hash_smuggling_and_extra_authority_fail_before_append(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    gateway = ThemeReviewGateway(
        catalog=catalog, state_root=tmp_path / "theme-review-state"
    )
    task = next(row for row in catalog.tasks if row["source_binding_candidates"])
    gateway.claim(
        task["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-source-smuggle-0001"},
        principal_id="teacher.local",
    )
    detail = gateway.get(task["task_id"])
    candidate = detail["source_binding_candidates"][0]
    base_row = {
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "source_version_id": candidate["source_version_id"],
        "action": "request_source_evidence",
        "reason_zh": "仅请求补充来源证据。",
        "evidence_ids": candidate["evidence_binding_ids"],
    }

    stale = _empty_change_payload(detail, "source-stale")
    stale["source_binding_candidates"] = [{**base_row, "candidate_sha256": "0" * 64}]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(task["task_id"], stale, principal_id="teacher.local")
    assert captured.value.code == "theme_review_source_binding_stale"

    smuggled = _empty_change_payload(detail, "source-smuggled")
    smuggled["source_binding_candidates"] = [
        {**base_row, "authority": {"official": True}}
    ]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(task["task_id"], smuggled, principal_id="teacher.local")
    assert captured.value.code == "theme_review_contract_invalid"
    assert gateway.get(task["task_id"])["state"]["change_set_count"] == 0


def test_legacy_v1_catalog_and_four_array_browser_payload_remain_compatible(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    value = catalog.as_dict()
    value["schema_version"] = "shchem_gateway_theme_review_task_catalog_v1"
    value["source_snapshot"].pop("source_version_crosswalk")
    value["integrity"].pop("source_version_crosswalk_hash_verified")
    value["integrity"].pop("source_version_candidate_authority_closed")
    for task in value["tasks"]:
        task.pop("source_binding")
        task.pop("source_binding_candidates")
        seed = {
            key: copy.deepcopy(task[key])
            for key in sorted(
                set(task)
                - {
                    "task_id",
                    "base_snapshot_sha256",
                    "task_input_sha256",
                    "candidate_only",
                    "human_reviewed",
                    "central_master_mutated",
                }
            )
        }
        digest = hashlib.sha256(canonical_json_bytes(seed)).hexdigest()
        task["task_id"] = f"TRTASK-{digest}"
        task["base_snapshot_sha256"] = digest
        task["task_input_sha256"] = digest
    legacy = ThemeReviewTaskCatalog.from_bytes(canonical_json_bytes(value))
    gateway = ThemeReviewGateway(
        catalog=legacy, state_root=tmp_path / "legacy-theme-review-state"
    )
    task = legacy.tasks[0]
    gateway.claim(
        task["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-legacy-v1-0001"},
        principal_id="teacher.local",
    )
    detail = gateway.get(task["task_id"])
    assert detail["source_binding"] is None
    assert detail["source_binding_candidates"] == []
    payload = _empty_change_payload(detail, "legacy-v1")
    payload.pop("source_binding_candidates")
    printed = detail["printed_questions"][0]
    payload["hierarchy_replacements"] = [
        {
            "printed_question_id": printed["printed_question_id"],
            "proposed_theme_id": detail["theme_id"],
            "reason_zh": "旧任务继续使用四类候选合同。",
            "evidence_ids": [printed["evidence_ids"][0]],
        }
    ]
    changed = gateway.change_set(
        task["task_id"], payload, principal_id="teacher.local"
    )
    assert changed["candidate_overlay_only"] is True


def test_no_atomic_task_accepts_only_server_derived_boundary_candidate(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    gateway = ThemeReviewGateway(
        catalog=catalog, state_root=tmp_path / "theme-review-state"
    )
    task = next(
        row
        for row in catalog.tasks
        if row["boundary_mode"] == BOUNDARY_MODE_ATOMIC_ONLY
    )
    gateway.claim(
        task["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-boundary-0001"},
        principal_id="teacher.local",
    )
    detail = gateway.get(task["task_id"])
    printed = detail["printed_questions"][0]

    invalid = _empty_change_payload(detail, "boundary-tag")
    invalid["tag_replacements"] = [
        {
            "atomic_part_id": "NOT-IN-TASK",
            "changes": {"item_type": "short_fill"},
            "reason_zh": "无 atomic 任务不应接受标签。",
            "evidence_ids": [printed["evidence_ids"][0]],
        }
    ]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(task["task_id"], invalid, principal_id="teacher.local")
    assert captured.value.code in {
        "theme_review_tag_target_invalid",
        "theme_review_boundary_only",
    }

    valid = _empty_change_payload(detail, "boundary-split")
    valid["atomic_boundary_candidates"] = [
        {
            "printed_question_id": printed["printed_question_id"],
            "operation": "split_into_atomic_parts",
            "candidate_atomic_parts": [
                {
                    "client_key": "response-unit-a",
                    "source_order": 1,
                    "response_requirement_zh": "根据题面可见作答位置形成第一候选作答单元。",
                }
            ],
            "reason_zh": "该 printed 当前无 atomic，仅提交拆题候选。",
            "evidence_ids": [printed["evidence_ids"][0]],
        }
    ]
    changed = gateway.change_set(
        task["task_id"], valid, principal_id="teacher.local"
    )
    preview = gateway.preview(task["task_id"], changed["change_set_id"])
    candidate = preview["candidate_overlay_preview"]["atomic_boundary_candidates"][0]
    assert candidate["before_atomic_part_ids"] == []
    assert candidate["candidate_atomic_parts"][0]["candidate_atomic_part_id"].startswith(
        "TRATOM-"
    )
    assert candidate["candidate_atomic_parts"][0]["prompt_locator_id"] in printed[
        "evidence_ids"
    ]


def test_tag_vocabulary_and_dependency_direction_are_fail_closed(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    gateway = ThemeReviewGateway(
        catalog=catalog, state_root=tmp_path / "theme-review-state"
    )
    task = catalog.tasks[0]
    gateway.claim(
        task["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-dependency-0001"},
        principal_id="teacher.local",
    )
    detail = gateway.get(task["task_id"])
    atoms = detail["atomic_parts"]

    invalid_tag = _empty_change_payload(detail, "bad-vocabulary")
    invalid_tag["tag_replacements"] = [
        {
            "atomic_part_id": atoms[0]["atomic_part_id"],
            "changes": {"item_type": "fabricated_item_type"},
            "reason_zh": "验证非法题型必须失败关闭。",
            "evidence_ids": [atoms[0]["evidence_ids"][0]],
        }
    ]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(
            task["task_id"], invalid_tag, principal_id="teacher.local"
        )
    assert captured.value.code == "theme_review_tag_vocabulary_invalid"

    target = next(
        atom
        for atom in atoms[1:]
        if atom["dependency"]["dependencies"] == []
    )
    prior = next(atom for atom in atoms if atom["source_order"] < target["source_order"])
    invalid_edge = _empty_change_payload(detail, "bad-edge-kind")
    invalid_edge["dependency_replacements"] = [
        {
            "dependent_atomic_part_id": target["atomic_part_id"],
            "prior_atomic_part_ids": [prior["atomic_part_id"]],
            "relationship_kinds": ["shared_theme_context"],
            "evidence_ids": [target["evidence_ids"][0]],
            "reason_zh": "共享材料不是前序作答依赖边。",
        }
    ]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(
            task["task_id"], invalid_edge, principal_id="teacher.local"
        )
    assert captured.value.code == "theme_review_dependency_not_strictly_forward"

    reverse = _empty_change_payload(detail, "reverse-edge")
    reverse["dependency_replacements"] = [
        {
            "dependent_atomic_part_id": prior["atomic_part_id"],
            "prior_atomic_part_ids": [target["atomic_part_id"]],
            "relationship_kinds": ["uses_prior_answer"],
            "evidence_ids": [prior["evidence_ids"][0]],
            "reason_zh": "后向依赖必须失败关闭。",
        }
    ]
    with pytest.raises(ThemeReviewGatewayError) as captured:
        gateway.change_set(task["task_id"], reverse, principal_id="teacher.local")
    assert captured.value.code == "theme_review_dependency_not_strictly_forward"

    valid = _empty_change_payload(detail, "valid-edge")
    valid["dependency_replacements"] = [
        {
            "dependent_atomic_part_id": target["atomic_part_id"],
            "prior_atomic_part_ids": [prior["atomic_part_id"]],
            "relationship_kinds": ["uses_prior_answer"],
            "evidence_ids": [target["evidence_ids"][0]],
            "reason_zh": "本候选明确使用同主题前序小题答案。",
        }
    ]
    changed = gateway.change_set(task["task_id"], valid, principal_id="teacher.local")
    preview = gateway.preview(task["task_id"], changed["change_set_id"])
    edge = preview["candidate_overlay_preview"]["dependency_replacements"][0]
    assert edge["before_dependencies"] == []
    assert edge["after_dependencies"] == [
        {
            "atomic_part_id": prior["atomic_part_id"],
            "relationship_kind": "uses_prior_answer",
        }
    ]
    assert DEPENDENCY_RELATIONSHIP_KINDS == {
        "uses_prior_answer",
        "uses_prior_calculated_value",
        "uses_prior_identified_substance",
        "uses_prior_structure",
        "uses_prior_experimental_conclusion",
    }


def test_empty_ledger_first_tag_change_uses_frozen_candidate_baseline(
    catalog: ThemeReviewTaskCatalog, tmp_path: Path
):
    state_root = tmp_path / "empty-theme-review-state"
    gateway = ThemeReviewGateway(catalog=catalog, state_root=state_root)
    task = next(
        row
        for row in catalog.tasks
        if any(
            tag["values"]["primary_knowledge_K"]
            for tag in row["current_tags"]
        )
    )
    detail = gateway.get(task["task_id"])
    atom = next(
        row
        for row in detail["atomic_parts"]
        if row["current_tags"]["primary_knowledge_K"]
    )
    assert detail["revision"] is None
    assert detail["state"]["change_set_count"] == 0
    assert atom["candidate_tags"] == {}
    frozen_before = atom["current_tags"]["primary_knowledge_K"]
    replacement = next(
        value
        for value in catalog.taxonomy_axes["K"]
        if value != frozen_before
    )

    gateway.claim(
        task["task_id"],
        {"expected_revision": None, "idempotency_key": "claim-first-tag-0001"},
        principal_id="teacher.local",
    )
    detail = gateway.get(task["task_id"])
    atom = next(
        row
        for row in detail["atomic_parts"]
        if row["atomic_part_id"] == atom["atomic_part_id"]
    )
    assert atom["candidate_tags"] == {}
    payload = _empty_change_payload(detail, "first-frozen-tag")
    payload["tag_replacements"] = [
        {
            "atomic_part_id": atom["atomic_part_id"],
            "changes": {"primary_knowledge_K": replacement},
            "reason_zh": "首次复核直接使用冻结候选标签作为服务端基线。",
            "evidence_ids": [atom["evidence_ids"][0]],
        }
    ]
    changed = gateway.change_set(
        task["task_id"], payload, principal_id="teacher.local"
    )
    preview = gateway.preview(task["task_id"], changed["change_set_id"])
    tag_patch = preview["candidate_overlay_preview"]["tag_replacements"][0]
    assert tag_patch["before"] == frozen_before
    assert tag_patch["after"] == replacement
    updated_atom = next(
        row
        for row in gateway.get(task["task_id"])["atomic_parts"]
        if row["atomic_part_id"] == atom["atomic_part_id"]
    )
    assert updated_atom["current_tags"]["primary_knowledge_K"] == frozen_before
    assert updated_atom["candidate_tags"]["primary_knowledge_K"] == replacement


def test_capability_names_are_split_by_authority_surface():
    assert REVIEW_TASK_WRITE_CAPABILITY == "review_task_write"
    assert REVIEW_CANDIDATE_WRITE_CAPABILITY == "review_candidate_write"
    assert REVIEW_DECISION_WRITE_CAPABILITY == "review_decision_write"
