from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .full_bank_readiness import FullBankReadinessError, FullBankReadinessReader

LEDGER_RELATIVE = Path(
    "kb/workbench/material_intake_ledger_v1/material_intake_ledger.json"
)
LEDGER_SCHEMA_VERSION = "shchem.material_intake_ledger.v1"
LEDGER_ID = "MATERIAL-INTAKE-LEDGER-2026-08-26-V1"
PIPELINE = (
    "registered",
    "rendered",
    "pages_split",
    "visual_objects_indexed",
    "themes_segmented",
    "questions_segmented",
    "deduplicated",
    "pending_review",
    "browse_ready",
)
RECORD_SECTIONS = {
    "catalog_source": "catalog_sources",
    "paper_processing_view": "paper_processing_views",
    "teaching_package": "teaching_packages",
    "teaching_document": "teaching_documents",
}
STATUSES = frozenset(
    {"tracked", "in_progress", "pending_review", "blocked", "browse_ready"}
)
FILTER_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,120}\Z")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION_ID = re.compile(r"^SV-[0-9a-f]{64}$")

PIPELINE_ZH = {
    "registered": "已登记",
    "rendered": "已渲染",
    "pages_split": "已拆页",
    "visual_objects_indexed": "视觉对象已盘点",
    "themes_segmented": "主题已拆分",
    "questions_segmented": "题目已拆分",
    "deduplicated": "已去重",
    "pending_review": "待复核",
    "browse_ready": "可浏览",
}

STATUS_ZH = {
    "tracked": "已登记待处理",
    "in_progress": "处理中",
    "pending_review": "待人工复核",
    "blocked": "有阻断",
    "browse_ready": "可浏览",
}

BLOCKER_ZH = {
    "ole_object_level_inventory_missing": "13,296 个 OLE 目前只有文档级总量，尚未建立逐对象身份、页码、坐标和哈希。",
    "teaching_page_state_inventory_missing": "196 个 DOCX 尚未进入统一逐页状态账本。",
    "unified_render_receipts_missing": "尚缺统一渲染任务及其不可变回执。",
    "theme_and_atomic_boundaries_incomplete": "仍有材料未完成主题大题与最小作答单元边界。",
    "rights_review_incomplete": "材料使用权限尚未逐项确认。",
    "human_review_incomplete": "模型或结构化候选尚未完成真人教师复核。",
    "catalog_directory_tree_hash_incomplete": "中央目录中的目录型来源尚未全部具备当前目录树哈希；已登记不等于版本完全冻结。",
    "rights_review_pending": "使用权限待确认",
    "downstream_processing_pending": "后续处理尚未开始",
    "theme_and_atomic_processing_pending": "主题与题目边界待处理",
    "human_review_pending": "真人教师复核待完成",
    "duplicate_source_reconcile_required": "重复来源需与主版本对账",
    "source_or_body_asset_blocker": "来源定位或正文资源存在阻断",
    "unified_render_pending": "统一渲染待执行",
    "ole_object_index_pending": "OLE 对象级索引待建立",
    "ole_visual_recovery_pending": "OLE 视觉还原待执行",
    "page_state_inventory_pending": "逐页状态待建立",
    "visual_review_pending": "视觉复核待执行",
}

AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "official_claim_allowed": False,
    "retrieval_ready": False,
    "recommendation_allowed": False,
    "manual_scoring_allowed": False,
    "ai_scoring_allowed": False,
    "auto_scoring_allowed": False,
    "generation_allowed": False,
    "export_allowed": False,
    "publication_allowed": False,
}

SOURCE_BINDINGS = {
    "catalog": "sh-chem-db/catalog.csv",
    "full_bank_readiness": (
        "sh-chem-db/kb/question_classification_v1/reports/"
        "full_bank_readiness_queue_v1.json"
    ),
}


class MaterialIntakeError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.status = status


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


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


def _catalog_value(value: Any) -> str:
    return str(value) if value not in {None, ""} else "unknown"


def _expected_batch_id(kind: str, rows: list[dict[str, Any]]) -> str:
    return (
        "INTAKE-BATCH-"
        + _canonical_sha256(
            {
                "kind": kind,
                "source_version_ids": sorted(
                    str(row["source_version_id"]) for row in rows
                ),
                "pipeline": list(PIPELINE),
                "pipeline_contract": LEDGER_SCHEMA_VERSION,
                "algorithm": "sorted-source-versions-plus-pipeline-v1",
            }
        )[:40]
    )


def _forbidden_record_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            folded = str(key).casefold()
            if folded in {
                "path",
                "local_path",
                "source_path",
                "absolute_path",
                "url",
                "source_url",
                "manifest_path",
            }:
                return True
            if _forbidden_record_key(child):
                return True
    elif isinstance(value, list):
        return any(_forbidden_record_key(item) for item in value)
    return False


class MaterialIntakeWorkbenchReader:
    """Verify and project the immutable seed ledger for existing materials."""

    def __init__(self, shchem_root: Path):
        self.shchem_root = shchem_root.resolve()
        self.workspace_root = self.shchem_root.parent.resolve()
        self.ledger_path = (self.shchem_root / LEDGER_RELATIVE).resolve()
        if not self.ledger_path.is_relative_to(self.shchem_root):
            raise MaterialIntakeError(
                "material_intake_path_boundary_invalid",
                "material intake ledger is outside the chemistry root",
            )
        self.readiness = FullBankReadinessReader(self.shchem_root)

    def _load(self) -> tuple[dict[str, Any], bytes]:
        if not self.ledger_path.is_file():
            raise MaterialIntakeError(
                "material_intake_ledger_missing",
                "material intake ledger is missing",
            )
        raw = self.ledger_path.read_bytes()
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise MaterialIntakeError(
                "material_intake_ledger_invalid",
                "material intake ledger is not strict UTF-8 JSON",
            ) from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != LEDGER_SCHEMA_VERSION
            or value.get("ledger_id") != LEDGER_ID
            or tuple(value.get("pipeline") or ()) != PIPELINE
        ):
            raise MaterialIntakeError(
                "material_intake_identity_invalid",
                "material intake ledger identity or pipeline is invalid",
                409,
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
            raise MaterialIntakeError(
                "material_intake_self_hash_mismatch",
                "material intake ledger self hash does not match",
                409,
            )
        self._verify_source_bindings(value)
        self._verify_records(value)
        return value, raw

    def _verify_source_bindings(self, value: dict[str, Any]) -> None:
        bindings = value.get("source_bindings")
        if not isinstance(bindings, dict) or set(bindings) != set(SOURCE_BINDINGS):
            raise MaterialIntakeError(
                "material_intake_source_binding_invalid",
                "material intake source binding inventory is not exact",
                409,
            )
        if value.get("input_set_sha256") != _canonical_sha256(bindings):
            raise MaterialIntakeError(
                "material_intake_input_set_mismatch",
                "material intake input-set hash does not match",
                409,
            )
        bound_raw: dict[str, bytes] = {}
        for key, expected_path in SOURCE_BINDINGS.items():
            descriptor = bindings.get(key)
            if (
                not isinstance(descriptor, dict)
                or descriptor.get("path") != expected_path
            ):
                raise MaterialIntakeError(
                    "material_intake_source_binding_invalid",
                    "material intake source path binding is invalid",
                    409,
                )
            source = (self.workspace_root / expected_path).resolve()
            if not source.is_relative_to(self.workspace_root) or not source.is_file():
                raise MaterialIntakeError(
                    "material_intake_source_missing",
                    "a bound material intake source is missing",
                )
            source_raw = source.read_bytes()
            bound_raw[key] = source_raw
            if descriptor.get("bytes") != len(source_raw) or descriptor.get(
                "sha256"
            ) != _sha256(source_raw):
                raise MaterialIntakeError(
                    "material_intake_source_hash_mismatch",
                    "a bound material intake source no longer matches",
                    409,
                )
        try:
            catalog_rows = list(
                csv.DictReader(io.StringIO(bound_raw["catalog"].decode("utf-8-sig")))
            )
        except (UnicodeDecodeError, csv.Error) as exc:
            raise MaterialIntakeError(
                "material_intake_catalog_invalid",
                "the bound material catalog is not valid UTF-8 CSV",
                409,
            ) from exc
        catalog_by_id = {row.get("record_id"): row for row in catalog_rows}
        ledger_sources = value.get("catalog_sources") or []
        if (
            None in catalog_by_id
            or len(catalog_by_id) != len(catalog_rows)
            or len(catalog_rows) != bindings["catalog"].get("records")
            or set(catalog_by_id) != {row.get("source_id") for row in ledger_sources}
        ):
            raise MaterialIntakeError(
                "material_intake_catalog_crosswalk_invalid",
                "material intake sources are not an exact catalog projection",
                409,
            )
        for source_record in ledger_sources:
            catalog_row = catalog_by_id[source_record["source_id"]]
            facts = source_record.get("facts") or {}
            if (
                source_record.get("source_sha256") != catalog_row.get("sha256")
                or source_record.get("display_title") != catalog_row.get("title")
                or str(facts.get("year")) != _catalog_value(catalog_row.get("year"))
                or facts.get("region_or_school")
                != _catalog_value(catalog_row.get("region_or_school"))
                or facts.get("material_type")
                != _catalog_value(catalog_row.get("content_type"))
                or facts.get("completeness")
                != _catalog_value(catalog_row.get("completeness"))
                or facts.get("answer_status")
                != _catalog_value(catalog_row.get("answer_status"))
                or facts.get("rubric_status")
                != _catalog_value(catalog_row.get("rubric_status"))
            ):
                raise MaterialIntakeError(
                    "material_intake_catalog_projection_mismatch",
                    "a material intake source disagrees with the bound catalog row",
                    409,
                )
        counts = value.get("counts") or {}
        if bindings["catalog"].get("records") != counts.get(
            "catalog_source_records"
        ) or bindings["full_bank_readiness"].get("records") != counts.get(
            "paper_processing_records"
        ) + counts.get("teaching_package_records"):
            raise MaterialIntakeError(
                "material_intake_source_record_count_mismatch",
                "material intake binding record counts do not match",
                409,
            )
        try:
            readiness = self.readiness.status()
        except FullBankReadinessError as exc:
            raise MaterialIntakeError(exc.code, str(exc), exc.status) from exc
        counts = value.get("counts") or {}
        upstream = readiness.get("counts") or {}
        pairs = {
            "paper_processing_records": "paper_inventory_records",
            "teaching_package_records": "teaching_package_records",
            "teaching_document_records": "teaching_document_records",
            "ole_objects_aggregate_registered": "teaching_embedded_ole_objects",
            "formal_question_ready_records": "formal_question_ready_records",
        }
        if any(
            counts.get(left) != upstream.get(right) for left, right in pairs.items()
        ):
            raise MaterialIntakeError(
                "material_intake_transitive_count_mismatch",
                "material intake counts disagree with the verified readiness source",
                409,
            )

    @staticmethod
    def _verify_records(value: dict[str, Any]) -> None:
        sections: dict[str, list[dict[str, Any]]] = {}
        for kind, name in RECORD_SECTIONS.items():
            rows = value.get(name)
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or row.get("record_kind") != kind
                for row in rows
            ):
                raise MaterialIntakeError(
                    "material_intake_record_section_invalid",
                    "a material intake record section is invalid",
                    409,
                )
            sections[kind] = rows
        all_rows = [row for rows in sections.values() for row in rows]
        ids = {row.get("record_id") for row in all_rows}
        if None in ids or len(ids) != len(all_rows):
            raise MaterialIntakeError(
                "material_intake_record_identity_invalid",
                "material intake record IDs are missing or duplicated",
                409,
            )
        catalog_by_source = {
            row.get("source_id"): row for row in sections["catalog_source"]
        }
        package_ids = {row.get("record_id") for row in sections["teaching_package"]}
        if None in catalog_by_source or len(catalog_by_source) != len(
            sections["catalog_source"]
        ):
            raise MaterialIntakeError(
                "material_intake_source_identity_invalid",
                "catalog source IDs are missing or duplicated",
                409,
            )
        if any(
            row.get("source_id") not in catalog_by_source
            or row.get("parent_record_id")
            != catalog_by_source[row.get("source_id")].get("record_id")
            or row.get("source_version_id")
            != catalog_by_source[row.get("source_id")].get("source_version_id")
            for row in sections["paper_processing_view"]
        ):
            raise MaterialIntakeError(
                "material_intake_paper_crosswalk_invalid",
                "paper processing views are not an exact subset of catalog sources",
                409,
            )
        if any(
            row.get("parent_record_id") not in package_ids
            for row in sections["teaching_document"]
        ):
            raise MaterialIntakeError(
                "material_intake_document_parent_invalid",
                "a teaching document does not bind to its package",
                409,
            )
        document_counts = Counter(
            row.get("parent_record_id") for row in sections["teaching_document"]
        )
        if any(document_counts[package_id] != 2 for package_id in package_ids):
            raise MaterialIntakeError(
                "material_intake_document_pair_invalid",
                "every teaching package must retain its two document roles",
                409,
            )
        batch_ids = {
            batch.get("batch_id")
            for batch in value.get("batches") or []
            if isinstance(batch, dict)
        }
        if len(batch_ids) != 3 or any(
            row.get("batch_id") not in batch_ids for row in all_rows
        ):
            raise MaterialIntakeError(
                "material_intake_batch_binding_invalid",
                "material intake records do not bind to the exact batch set",
                409,
            )
        expected_batches = {
            _expected_batch_id("catalog_source", sections["catalog_source"]),
            _expected_batch_id(
                "paper_processing_view", sections["paper_processing_view"]
            ),
            _expected_batch_id(
                "teaching_package_and_document",
                [
                    *sections["teaching_package"],
                    *sections["teaching_document"],
                ],
            ),
        }
        if batch_ids != expected_batches:
            raise MaterialIntakeError(
                "material_intake_batch_identity_invalid",
                "material intake batch IDs are not deterministic from their inputs",
                409,
            )
        if any(
            row.get("ingest_stage") not in PIPELINE
            or row.get("status") not in STATUSES
            or not VERSION_ID.fullmatch(str(row.get("source_version_id") or ""))
            or row.get("authority_gates") != AUTHORITY
            or _forbidden_record_key(row)
            for row in all_rows
        ):
            raise MaterialIntakeError(
                "material_intake_record_contract_invalid",
                "a material intake record violates stage, authority or privacy boundaries",
                409,
            )
        counts = value.get("counts")
        expected = {
            "catalog_source_records": len(sections["catalog_source"]),
            "paper_processing_records": len(sections["paper_processing_view"]),
            "teaching_package_records": len(sections["teaching_package"]),
            "teaching_document_records": len(sections["teaching_document"]),
            "entity_record_count_non_additive": len(all_rows),
        }
        if not isinstance(counts, dict) or any(
            counts.get(key) != count for key, count in expected.items()
        ):
            raise MaterialIntakeError(
                "material_intake_count_mismatch",
                "material intake entity counts do not match the record set",
                409,
            )
        ole_total = sum(
            int(row.get("facts", {}).get("embedded_ole_objects", 0))
            for row in sections["teaching_document"]
        )
        if (
            counts.get("ole_objects_aggregate_registered") != ole_total
            or counts.get("ole_objects_individually_indexed") != 0
            or counts.get("page_state_records") != 0
            or counts.get("formal_question_ready_records") != 0
            or counts.get("status_counts")
            != dict(sorted(Counter(row["status"] for row in all_rows).items()))
            or counts.get("pipeline_stage_counts")
            != dict(sorted(Counter(row["ingest_stage"] for row in all_rows).items()))
        ):
            raise MaterialIntakeError(
                "material_intake_progress_mismatch",
                "material intake progress or honest zero-state counts do not match",
                409,
            )
        if value.get("authority") != AUTHORITY:
            raise MaterialIntakeError(
                "material_intake_authority_escalation",
                "material intake authority must remain closed",
                409,
            )

    @staticmethod
    def _record_projection(row: dict[str, Any]) -> dict[str, Any]:
        facts = dict(row.get("facts") or {})
        return {
            "record_id": row["record_id"],
            "record_kind": row["record_kind"],
            "batch_id": row["batch_id"],
            "source_id": row["source_id"],
            "source_version_id": row["source_version_id"],
            "parent_record_id": row.get("parent_record_id"),
            "title": row["display_title"],
            "source_tier": row["source_tier"],
            "authority": row["authority"],
            "rights_status": row["rights_status"],
            "ingest_stage": row["ingest_stage"],
            "ingest_stage_zh": PIPELINE_ZH[row["ingest_stage"]],
            "upstream_stage": row["upstream_stage"],
            "status": row["status"],
            "status_zh": STATUS_ZH[row["status"]],
            "error_code": row.get("error_code"),
            "progress": dict(row["progress"]),
            "blockers": [
                {
                    "code": code,
                    "message_zh": BLOCKER_ZH.get(code, "待进一步核验"),
                }
                for code in row.get("blocker_codes") or []
            ],
            "next_action_zh": row["next_action_zh"],
            "facts": facts,
            "authority_gates": dict(AUTHORITY),
        }

    def status(self) -> dict[str, Any]:
        value, raw = self._load()
        counts = dict(value["counts"])
        return {
            "ledger_id": value["ledger_id"],
            "schema_version": value["schema_version"],
            "status": "existing_materials_tracked_candidate_only",
            "pipeline": [
                {"stage": stage, "label_zh": PIPELINE_ZH[stage]} for stage in PIPELINE
            ],
            "counts": counts,
            "batches": [dict(batch) for batch in value["batches"]],
            "non_additive_boundary_zh": (
                "108 条是中央来源目录；80 份试卷是其中的处理视图；"
                "98 个讲义包是 196 个 DOCX 的父记录。以上层级不能相加成材料总数。"
            ),
            "ole_boundary_zh": (
                "13,296 是 196 个 DOCX 内的 OLE 出现次数总量；当前逐对象索引为 0，"
                "不能声称已完成页码、坐标、对象哈希或视觉复核。"
            ),
            "global_blockers": [
                {"code": code, "message_zh": BLOCKER_ZH[code]}
                for code in value["global_blockers"]
            ],
            "integrity": {
                "ledger_sha256": _sha256(raw),
                "self_hash": value["self_hash"],
                "input_set_sha256": value["input_set_sha256"],
                "source_bindings_verified": True,
                "transitive_readiness_bindings_verified": True,
            },
            "authority": dict(AUTHORITY),
            "endpoints": {
                "read_only_get": True,
                "batch_create": False,
                "worker_execution": False,
                "mutation": False,
            },
        }

    def list_batches(self) -> dict[str, Any]:
        value, _ = self._load()
        records = [
            row for section in RECORD_SECTIONS.values() for row in value[section]
        ]
        items: list[dict[str, Any]] = []
        for batch in value["batches"]:
            batch_rows = [
                row for row in records if row["batch_id"] == batch["batch_id"]
            ]
            items.append(
                {
                    **dict(batch),
                    "status_counts": dict(
                        sorted(Counter(row["status"] for row in batch_rows).items())
                    ),
                    "stage_counts": dict(
                        sorted(
                            Counter(row["ingest_stage"] for row in batch_rows).items()
                        )
                    ),
                    "record_kind_counts": dict(
                        sorted(
                            Counter(row["record_kind"] for row in batch_rows).items()
                        )
                    ),
                }
            )
        return {
            "ledger_id": value["ledger_id"],
            "total": len(items),
            "items": items,
            "source_paths_exposed": False,
            "content_exposed": False,
            "authority": dict(AUTHORITY),
        }

    def batch_detail(self, batch_id: str) -> dict[str, Any]:
        value, _ = self._load()
        batch = next(
            (item for item in value["batches"] if item.get("batch_id") == batch_id),
            None,
        )
        if batch is None:
            raise MaterialIntakeError(
                "material_intake_batch_not_found",
                "material intake batch not found",
                404,
            )
        rows = [
            row
            for section in RECORD_SECTIONS.values()
            for row in value[section]
            if row["batch_id"] == batch_id
        ]
        blocker_counts = Counter(
            blocker for row in rows for blocker in row.get("blocker_codes") or []
        )
        return {
            **dict(batch),
            "ledger_id": value["ledger_id"],
            "status_counts": dict(
                sorted(Counter(row["status"] for row in rows).items())
            ),
            "stage_counts": dict(
                sorted(Counter(row["ingest_stage"] for row in rows).items())
            ),
            "record_kind_counts": dict(
                sorted(Counter(row["record_kind"] for row in rows).items())
            ),
            "blockers": [
                {
                    "code": code,
                    "count": count,
                    "message_zh": BLOCKER_ZH.get(code, "待进一步核验"),
                }
                for code, count in sorted(blocker_counts.items())
            ],
            "source_paths_exposed": False,
            "content_exposed": False,
            "authority": dict(AUTHORITY),
        }

    def list_records(
        self,
        *,
        kind: str | None,
        stage: str | None,
        status: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        value, _ = self._load()
        if kind is not None and kind not in RECORD_SECTIONS:
            raise MaterialIntakeError(
                "material_intake_filter_invalid", "unsupported record kind", 400
            )
        if stage is not None and stage not in PIPELINE:
            raise MaterialIntakeError(
                "material_intake_filter_invalid", "unsupported ingest stage", 400
            )
        if status is not None and status not in STATUSES:
            raise MaterialIntakeError(
                "material_intake_filter_invalid", "unsupported record status", 400
            )
        if query is not None and FILTER_TEXT.fullmatch(query) is None:
            raise MaterialIntakeError(
                "material_intake_filter_invalid", "invalid material intake query", 400
            )
        selected_sections = (
            [RECORD_SECTIONS[kind]]
            if kind is not None
            else list(RECORD_SECTIONS.values())
        )
        rows = [row for name in selected_sections for row in value[name]]
        if stage is not None:
            rows = [row for row in rows if row["ingest_stage"] == stage]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        projections = [self._record_projection(row) for row in rows]
        if query is not None:
            needle = query.casefold()
            projections = [
                row
                for row in projections
                if needle
                in " ".join(
                    [
                        str(row["title"]),
                        str(row["record_kind"]),
                        str(row["source_id"]),
                        str(row["ingest_stage"]),
                        str(row["status"]),
                        json.dumps(row["facts"], ensure_ascii=False, sort_keys=True),
                    ]
                ).casefold()
            ]
        projections.sort(
            key=lambda row: (
                PIPELINE.index(row["ingest_stage"]),
                row["status"],
                row["record_kind"],
                row["record_id"],
            )
        )
        total = len(projections)
        return {
            "ledger_id": value["ledger_id"],
            "total": total,
            "count": len(projections[offset : offset + limit]),
            "offset": offset,
            "limit": limit,
            "items": projections[offset : offset + limit],
            "source_paths_exposed": False,
            "content_exposed": False,
            "authority": dict(AUTHORITY),
        }
