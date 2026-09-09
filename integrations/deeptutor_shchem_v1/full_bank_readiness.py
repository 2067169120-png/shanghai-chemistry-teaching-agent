from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


REPORT_RELATIVE = Path(
    "kb/question_classification_v1/reports/full_bank_readiness_queue_v1.json"
)
REPORT_SCHEMA = "1.0.0-full-bank-classification-readiness"
REPORT_QUEUE_ID = "FULL-BANK-READINESS-2026-08-24-V1"

_SOURCE_BINDINGS = {
    "paper_inventory": "sh-chem-db/kb/paper_learning_v1/paper_inventory.jsonl",
    "paper_profiles": "sh-chem-db/kb/paper_learning_v1/profiles/index.jsonl",
    "formalization_queue": "sh-chem-db/kb/paper_learning_v1/reports/formalization_queue.json",
    "wave1_paper_candidates": "sh-chem-db/kb/formal/candidates/wave1_formalization_2026-08-04/paper_records.jsonl",
    "teaching_pack_inventory": "sh-chem-db/.intake/2026-07-30-user-teaching-pack/analysis/pack_inventory.jsonl",
}
_COHORTS = {
    "shanghai_paper_inventory": "paper_queue",
    "quarantined_user_teaching_pack": "teaching_pack_queue",
}
_FILTER_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,120}\Z")

_AUTHORITY = {
    "read_only": True,
    "candidate_only": True,
    "formal_question_ready": False,
    "human_reviewed": False,
    "retrieval_allowed": False,
    "generation_allowed": False,
    "diagnosis_allowed": False,
    "teaching_use_allowed": False,
    "publication_allowed": False,
    "official_claim_allowed": False,
    "mutation_endpoint_present": False,
}

_NEXT_ACTION_ZH = {
    "render both role documents, recover OLE visually, split exact items, then independently solve": "渲染原卷版与解析版，逐个视觉还原 OLE 公式/结构，拆分精确作答单元后再独立作答核验",
    "convert one complete coherent theme or cross-page chain into exact paper/theme_big_question/printed_question/atomic_part candidates": "选择一个完整连贯主题或跨页链，按整卷→主题大题→印刷小题→最小作答单元建立候选",
    "complete multimodal whole-paper deep read before exact question boundaries": "先完成整卷多模态深读，再确定精确题目边界",
    "resolve missing-body-image/source-localization blockers before rendering": "先解决正文图片缺失或来源定位阻塞，再进入页面渲染",
    "review existing candidate parent chains and labels; continue only uncovered themes without creating a duplicate candidate version": "复核已有候选四层父链与标签；只补尚未覆盖的主题，避免生成重复候选版本",
    "verify page continuity, identity and answer boundary before content deep read": "先核验页面连续性、试卷身份与答案边界，再进行内容深读",
    "complete content deep read; structure metadata alone cannot create questions": "完成内容深读；仅凭结构元数据不能建立题目",
    "reconcile to its independent paper record; do not create a second paper identity": "与对应独立试卷记录完成对账；不得创建第二个试卷身份",
}


def _next_action_projection(value: Any) -> tuple[str, str]:
    code = value if isinstance(value, str) and value else "blocked_pending_review"
    return code, _NEXT_ACTION_ZH.get(code, "待人工核验后确定下一步")


class FullBankReadinessError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.status = status


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _all_gates_closed(row: dict[str, Any]) -> bool:
    gates = row.get("gates")
    return isinstance(gates, dict) and bool(gates) and all(
        value is False for value in gates.values()
    )


class FullBankReadinessReader:
    """Exact, hash-verified projection of the full-bank readiness ledger."""

    def __init__(self, shchem_root: Path):
        self.shchem_root = shchem_root.resolve()
        self.report_path = (self.shchem_root / REPORT_RELATIVE).resolve()
        if not self.report_path.is_relative_to(self.shchem_root):
            raise FullBankReadinessError(
                "full_bank_readiness_path_boundary_invalid",
                "full-bank readiness report is outside the chemistry root",
            )

    def _load(self) -> tuple[dict[str, Any], bytes]:
        if not self.report_path.is_file():
            raise FullBankReadinessError(
                "full_bank_readiness_report_missing",
                "full-bank readiness report is missing",
            )
        raw = self.report_path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FullBankReadinessError(
                "full_bank_readiness_report_invalid",
                "full-bank readiness report is not valid UTF-8 JSON",
            ) from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != REPORT_SCHEMA
            or value.get("queue_id") != REPORT_QUEUE_ID
        ):
            raise FullBankReadinessError(
                "full_bank_readiness_identity_invalid",
                "full-bank readiness report identity is invalid",
            )

        expected_self_hash = value.get("self_hash")
        clone = dict(value)
        clone.pop("self_hash", None)
        if (
            value.get("self_hash_algorithm")
            != "sha256_canonical_json_without_self_hash_v1"
            or not isinstance(expected_self_hash, str)
            or _canonical_sha256(clone) != expected_self_hash
        ):
            raise FullBankReadinessError(
                "full_bank_readiness_self_hash_mismatch",
                "full-bank readiness report self hash does not match",
                409,
            )

        self._verify_source_bindings(value)
        self._verify_closed_queue(value)
        return value, raw

    def _verify_source_bindings(self, value: dict[str, Any]) -> None:
        bindings = value.get("source_bindings")
        if not isinstance(bindings, dict) or set(bindings) != set(_SOURCE_BINDINGS):
            raise FullBankReadinessError(
                "full_bank_readiness_source_binding_invalid",
                "full-bank readiness source binding inventory is not exact",
                409,
            )
        if value.get("input_set_sha256") != _canonical_sha256(bindings):
            raise FullBankReadinessError(
                "full_bank_readiness_input_set_mismatch",
                "full-bank readiness input-set hash does not match",
                409,
            )
        for key, expected_path in _SOURCE_BINDINGS.items():
            descriptor = bindings.get(key)
            if not isinstance(descriptor, dict) or descriptor.get("path") != expected_path:
                raise FullBankReadinessError(
                    "full_bank_readiness_source_binding_invalid",
                    "full-bank readiness source path binding is invalid",
                    409,
                )
            relative = Path(expected_path.removeprefix("sh-chem-db/"))
            source = (self.shchem_root / relative).resolve()
            if not source.is_relative_to(self.shchem_root) or not source.is_file():
                raise FullBankReadinessError(
                    "full_bank_readiness_source_missing",
                    "a bound full-bank readiness source is missing",
                )
            raw = source.read_bytes()
            if descriptor.get("bytes") != len(raw) or descriptor.get("sha256") != _sha256(raw):
                raise FullBankReadinessError(
                    "full_bank_readiness_source_hash_mismatch",
                    "a bound full-bank readiness source no longer matches",
                    409,
                )

    @staticmethod
    def _verify_closed_queue(value: dict[str, Any]) -> None:
        counts = value.get("counts")
        paper_rows = value.get("paper_queue")
        teaching_rows = value.get("teaching_pack_queue")
        gates = value.get("gates")
        if (
            not isinstance(counts, dict)
            or not isinstance(paper_rows, list)
            or not isinstance(teaching_rows, list)
            or len(paper_rows) != 80
            or len(teaching_rows) != 98
            or counts.get("paper_inventory_records") != len(paper_rows)
            or counts.get("teaching_package_records") != len(teaching_rows)
            or counts.get("total_queue_records") != len(paper_rows) + len(teaching_rows)
            or counts.get("formal_question_ready_records") != 0
        ):
            raise FullBankReadinessError(
                "full_bank_readiness_count_mismatch",
                "full-bank readiness cohort counts no longer match",
                409,
            )
        if (
            value.get("all_downstream_gates_closed") is not True
            or not isinstance(gates, dict)
            or not gates
            or any(flag is not False for flag in gates.values())
            or any(
                not isinstance(row, dict) or not _all_gates_closed(row)
                for row in [*paper_rows, *teaching_rows]
            )
        ):
            raise FullBankReadinessError(
                "full_bank_readiness_authority_escalation",
                "full-bank readiness ledger does not keep every downstream gate closed",
                409,
            )

    @staticmethod
    def _paper_projection(row: dict[str, Any]) -> dict[str, Any]:
        next_action_code, next_action = _next_action_projection(row.get("next_action"))
        return {
            "queue_id": row.get("queue_id", "unknown"),
            "cohort": "shanghai_paper_inventory",
            "title": row.get("title", "unknown"),
            "year": row.get("year", "unknown"),
            "region_or_school": row.get("region_or_school", "unknown"),
            "paper_type": row.get("paper_type", "unknown"),
            "source_form": row.get("source_form", "unknown"),
            "question_page_count": row.get("question_page_count", "unknown"),
            "answer_page_count": row.get("answer_page_count", "unknown"),
            "profile_status": row.get("profile_status", "unknown"),
            "readiness_stage": row.get("readiness_stage", "unknown"),
            "priority_rank": row.get("priority_rank", "unknown"),
            "versioned_candidate_count": len(row.get("versioned_candidate_refs", [])),
            "four_layer_crosswalk_candidate_count": len(
                row.get("four_layer_crosswalk_candidates", [])
            ),
            "existing_formal_question_records": row.get(
                "existing_formal_question_records", 0
            ),
            "measured_difficulty_status": row.get(
                "measured_difficulty_status", "blocked_pending_review"
            ),
            "next_action_code": next_action_code,
            "next_action": next_action,
            "formal_question_ready": False,
        }

    @staticmethod
    def _teaching_projection(row: dict[str, Any]) -> dict[str, Any]:
        documents = row.get("documents")
        documents = documents if isinstance(documents, list) else []
        next_action_code, next_action = _next_action_projection(row.get("next_action"))
        return {
            "queue_id": row.get("queue_id", "unknown"),
            "cohort": "quarantined_user_teaching_pack",
            "package_id": row.get("package_id", "unknown"),
            "module": row.get("module", "unknown"),
            "topic": row.get("topic", "unknown"),
            "material_type": row.get("material_type", "unknown"),
            "document_count": len(documents),
            "embedded_ole_objects": row.get("embedded_ole_objects", 0),
            "body_external_image_relationships": row.get(
                "body_external_image_relationships", 0
            ),
            "non_shanghai_or_national_trace_relationships": row.get(
                "non_shanghai_or_national_trace_relationships", 0
            ),
            "source_authority": row.get("source_authority", "unknown"),
            "content_review_status": row.get("content_review_status", "unknown"),
            "readiness_stage": row.get("readiness_stage", "unknown"),
            "priority_rank": row.get("priority_rank", "unknown"),
            "next_action_code": next_action_code,
            "next_action": next_action,
            "formal_question_ready": False,
        }

    def status(self) -> dict[str, Any]:
        value, raw = self._load()
        counts = value["counts"]
        return {
            "queue_id": value["queue_id"],
            "status": "readiness_accounting_only_gates_closed",
            "counts": counts,
            "stage_counts": {
                "papers": counts["paper_readiness_stage_counts"],
                "teaching_packages": counts["teaching_readiness_stage_counts"],
            },
            "cohort_boundary": (
                "两个独立 cohort：80 份上海试卷库存与 98 个隔离教学资料包"
                "不能相加成 178 份试卷。"
            ),
            "boundaries": value.get("boundaries", []),
            "integrity": {
                "report_sha256": _sha256(raw),
                "self_hash": value["self_hash"],
                "input_set_sha256": value["input_set_sha256"],
                "source_bindings_verified": True,
            },
            "authority": dict(_AUTHORITY),
            "endpoints": {"read_only_get": True, "mutation": False, "apply": False},
        }

    def list_records(
        self,
        *,
        cohort: str | None,
        stage: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        value, _ = self._load()
        if cohort is not None and cohort not in _COHORTS:
            raise FullBankReadinessError(
                "full_bank_readiness_filter_invalid", "unsupported cohort filter", 400
            )
        for label, candidate in (("stage", stage), ("q", query)):
            if candidate is not None and _FILTER_TEXT.fullmatch(candidate) is None:
                raise FullBankReadinessError(
                    "full_bank_readiness_filter_invalid",
                    f"invalid full-bank readiness {label} filter",
                    400,
                )
        selected = list(_COHORTS) if cohort is None else [cohort]
        rows: list[dict[str, Any]] = []
        for name in selected:
            source_rows = value[_COHORTS[name]]
            projector = (
                self._paper_projection
                if name == "shanghai_paper_inventory"
                else self._teaching_projection
            )
            rows.extend(projector(row) for row in source_rows)
        if stage is not None:
            rows = [row for row in rows if row["readiness_stage"] == stage]
        if query is not None:
            needle = query.casefold()
            rows = [
                row
                for row in rows
                if needle
                in " ".join(str(item) for item in row.values()).casefold()
            ]
        rows.sort(
            key=lambda row: (
                int(row["priority_rank"])
                if type(row["priority_rank"]) is int
                else 999,
                str(row["cohort"]),
                str(row["queue_id"]),
            )
        )
        total = len(rows)
        return {
            "queue_id": value["queue_id"],
            "total": total,
            "count": len(rows[offset : offset + limit]),
            "offset": offset,
            "limit": limit,
            "items": rows[offset : offset + limit],
            "content_exposed": False,
            "source_paths_exposed": False,
            "authority": dict(_AUTHORITY),
        }
