from __future__ import annotations

import hashlib
import json
import re
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

SOURCE_NAMESPACE = "candidate_review_only"
BATCH_ID = "WAVE1-FORMALIZATION-2026-08-04"
BATCH_RELATIVE = Path(
    "kb/formal/candidates/wave1_formalization_2026-08-04"
)
CROP_BATCH_RELATIVE = Path(
    "kb/formal/candidates/intake_round_2026-08-02"
)
OVERLAY_V2_SOURCE = "intake-complete-papers-overlay-v2-2026-08-03"
MAX_CROP_BYTES = 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_NODE_FILES = {
    "paper": ("paper_records.jsonl", "paper_id"),
    "theme_big_question": (
        "theme_big_question_records.jsonl",
        "theme_big_question_id",
    ),
    "printed_question": ("printed_question_records.jsonl", "printed_question_id"),
    "atomic_part": ("atomic_part_records.jsonl", "atomic_part_id"),
}
_SUPPORT_FILES = {
    "visual_crop_manifest.jsonl",
    "duplicate_and_near_duplicate_candidates.jsonl",
}
_ALLOWED_FILES = {
    "manifest.json",
    *_SUPPORT_FILES,
    *(item[0] for item in _NODE_FILES.values()),
}
_COUNT_KEYS = {
    "paper": "papers",
    "theme_big_question": "theme_big_questions",
    "printed_question": "printed_questions",
    "atomic_part": "atomic_parts",
}
_SAFE_REFERENCE_PREFIXES = (
    "kb/formal/candidates/intake_round_2026-08-02/",
    "staging/wechat/",
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_QUESTION_MANIFEST_ROLES = frozenset(
    {
        "explicit_printed_subpart_evidence",
        "explicit_subpart_evidence",
        "part_question",
        "printed_question",
        "question_and_default_part_evidence",
        "whole_printed_question_evidence",
    }
)
_QUESTION_LOCAL_ROLES = frozenset(
    {"atomic_part", "printed_question", "question", "question_evidence"}
)
_SHARED_LOCAL_ROLES = frozenset({"shared_material", "theme_shared_material"})
_FORBIDDEN_BROWSER_PATH_KEYS = frozenset(
    {
        "path",
        "crop_path",
        "crop_ref",
        "original_source_path",
        "original_source_ref",
        "whole_page_path",
        "source_package_path",
        "source_package_ref",
    }
)

_AUTHORITY = {
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


class CandidateReviewError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class CandidateCropPayload:
    """An already-verified in-memory PNG; HTTP must send this exact buffer."""

    data: bytes
    sha256: str
    content_type: str = "image/png"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unknown(value: Any, fallback: str = "unknown") -> Any:
    return fallback if value is None or value == "" else value


def _known_text(value: Any) -> str | None:
    if not isinstance(value, str) or value.strip().casefold() in {
        "",
        "unknown",
        "blocked_pending_review",
    }:
        return None
    return value.strip()


def _safe_reference(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    normalized = path.as_posix()
    if not normalized.startswith(_SAFE_REFERENCE_PREFIXES):
        return None
    return normalized


def _collect_safe_references(value: Any) -> list[str]:
    refs: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)
        else:
            safe = _safe_reference(item)
            if safe is not None:
                refs.add(safe)

    walk(value)
    return sorted(refs)


def _looks_like_local_path(value: str) -> bool:
    normalized = value.strip().replace("\\", "/").casefold()
    if normalized.startswith("/api/"):
        return False
    return bool(
        re.match(r"^[a-z]:/", normalized)
        or normalized.startswith(
            (
                "/",
                "./",
                "../",
                "kb/",
                "staging/",
                "runtime/",
                ".intake/",
                "sh-chem-db/",
            )
        )
    )


_OMIT = object()


def _browser_safe(value: Any) -> Any:
    """Recursively omit all local path keys and path-shaped string values."""

    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            folded = key.casefold()
            if folded in _FORBIDDEN_BROWSER_PATH_KEYS or folded.endswith("_path"):
                continue
            safe = _browser_safe(item)
            if safe is not _OMIT:
                projected[key] = safe
        return projected
    if isinstance(value, list):
        return [safe for item in value if (safe := _browser_safe(item)) is not _OMIT]
    if isinstance(value, tuple):
        return [safe for item in value if (safe := _browser_safe(item)) is not _OMIT]
    if isinstance(value, str) and _looks_like_local_path(value):
        return _OMIT
    return value


def _axis(value: Any, *, missing_status: str = "unknown") -> dict[str, Any]:
    items = value if isinstance(value, list) else []
    projected = []
    for item in items:
        if not isinstance(item, dict):
            continue
        projected.append(
            {
                "id": _unknown(item.get("id")),
                "name": _unknown(item.get("name")),
                "confidence": item.get("confidence", "unknown"),
                "review_status": _unknown(
                    item.get("review_status"), "blocked_pending_review"
                ),
                "basis": _unknown(item.get("basis"), "blocked_pending_review"),
                "evidence_refs": _collect_safe_references(
                    item.get("evidence_refs", [])
                ),
            }
        )
    return {
        "status": "candidate_pending_human" if projected else missing_status,
        "values": projected,
    }


class Wave1CandidateReviewReader:
    """Hash-verified, exact-file reader for the isolated Wave1 candidate batch."""

    def __init__(self, shchem_root: Path):
        self.shchem_root = shchem_root.resolve()
        self.batch_root = (self.shchem_root / BATCH_RELATIVE).resolve()
        if not self.batch_root.is_relative_to(self.shchem_root):
            raise CandidateReviewError(
                "candidate_review_path_boundary_invalid",
                "candidate review batch is outside the configured chemistry root",
            )
        crop_cursor = self.shchem_root
        for part in CROP_BATCH_RELATIVE.parts:
            crop_cursor /= part
            if crop_cursor.is_symlink():
                raise CandidateReviewError(
                    "candidate_review_crop_symlink_denied",
                    "Wave1 crop evidence root must not contain symlinks",
                    409,
                )
        self.crop_batch_root = crop_cursor.resolve()
        if not self.crop_batch_root.is_relative_to(self.shchem_root):
            raise CandidateReviewError(
                "candidate_review_crop_path_boundary_invalid",
                "Wave1 crop evidence root is outside the configured chemistry root",
                409,
            )

    def _exact_path(self, filename: str) -> Path:
        if filename not in _ALLOWED_FILES:
            raise CandidateReviewError(
                "candidate_review_file_denied",
                "requested candidate review artifact is not allowlisted",
                400,
            )
        path = (self.batch_root / filename).resolve()
        if path.parent != self.batch_root or not path.is_file():
            raise CandidateReviewError(
                "candidate_review_artifact_missing",
                "an allowlisted Wave1 candidate review artifact is missing",
            )
        return path

    def _manifest(self) -> tuple[dict[str, Any], bytes]:
        raw = self._exact_path("manifest.json").read_bytes()
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateReviewError(
                "candidate_review_manifest_invalid",
                "Wave1 candidate manifest is not valid UTF-8 JSON",
            ) from exc
        if not isinstance(value, dict) or value.get("batch_id") != BATCH_ID:
            raise CandidateReviewError(
                "candidate_review_manifest_invalid",
                "Wave1 candidate manifest identity is invalid",
            )
        gates = value.get("gates")
        if (
            not isinstance(gates, dict)
            or not gates
            or any(gate_value is not False for gate_value in gates.values())
        ):
            raise CandidateReviewError(
                "candidate_review_authority_escalation",
                "Wave1 candidate manifest does not keep every protected gate false",
                409,
            )
        return value, raw

    @staticmethod
    def _artifacts(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows = manifest.get("generated_artifacts")
        if not isinstance(rows, list):
            raise CandidateReviewError(
                "candidate_review_manifest_invalid",
                "Wave1 candidate artifact inventory is missing",
            )
        artifacts: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise CandidateReviewError(
                    "candidate_review_manifest_invalid",
                    "Wave1 candidate artifact inventory contains an invalid row",
                )
            name = row["path"]
            if name in artifacts:
                raise CandidateReviewError(
                    "candidate_review_manifest_invalid",
                    "Wave1 candidate artifact inventory contains duplicate paths",
                )
            artifacts[name] = row
        return artifacts

    def _verified_raw(
        self, filename: str, artifacts: dict[str, dict[str, Any]]
    ) -> bytes:
        reference = artifacts.get(filename)
        if not isinstance(reference, dict):
            raise CandidateReviewError(
                "candidate_review_hash_binding_missing",
                "an allowlisted Wave1 artifact is not bound by the manifest",
            )
        expected_hash = reference.get("sha256")
        expected_bytes = reference.get("bytes")
        if (
            not isinstance(expected_hash, str)
            or _SHA256.fullmatch(expected_hash) is None
            or type(expected_bytes) is not int
            or expected_bytes < 0
        ):
            raise CandidateReviewError(
                "candidate_review_hash_binding_invalid",
                "an allowlisted Wave1 artifact has an invalid hash/bytes binding",
            )
        raw = self._exact_path(filename).read_bytes()
        if len(raw) != expected_bytes or _sha256(raw) != expected_hash:
            raise CandidateReviewError(
                "candidate_review_hash_mismatch",
                "an allowlisted Wave1 artifact no longer matches its manifest",
                409,
            )
        return raw

    @staticmethod
    def _jsonl(raw: bytes, filename: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CandidateReviewError(
                    "candidate_review_artifact_invalid",
                    f"{filename} row {line_number} is not valid UTF-8 JSON",
                ) from exc
            if not isinstance(value, dict):
                raise CandidateReviewError(
                    "candidate_review_artifact_invalid",
                    f"{filename} row {line_number} is not an object",
                )
            rows.append(value)
        return rows

    @staticmethod
    def _closed_gates(row: dict[str, Any]) -> bool:
        gates = row.get("gates")
        return bool(gates) and isinstance(gates, dict) and all(
            value is False for value in gates.values()
        )

    def _snapshot(self) -> dict[str, Any]:
        manifest, manifest_raw = self._manifest()
        artifacts = self._artifacts(manifest)
        records: dict[str, list[dict[str, Any]]] = {}
        file_hashes: dict[str, dict[str, Any]] = {
            "manifest.json": {"sha256": _sha256(manifest_raw), "bytes": len(manifest_raw)}
        }
        for node_type, (filename, _) in _NODE_FILES.items():
            raw = self._verified_raw(filename, artifacts)
            records[node_type] = self._jsonl(raw, filename)
            file_hashes[filename] = {"sha256": _sha256(raw), "bytes": len(raw)}
        support: dict[str, list[dict[str, Any]]] = {}
        for filename in sorted(_SUPPORT_FILES):
            raw = self._verified_raw(filename, artifacts)
            support[filename] = self._jsonl(raw, filename)
            file_hashes[filename] = {"sha256": _sha256(raw), "bytes": len(raw)}

        counts = manifest.get("counts")
        if not isinstance(counts, dict):
            raise CandidateReviewError(
                "candidate_review_manifest_invalid",
                "Wave1 candidate manifest counts are missing",
            )
        indexes: dict[str, dict[str, dict[str, Any]]] = {}
        for node_type, rows in records.items():
            id_key = _NODE_FILES[node_type][1]
            index: dict[str, dict[str, Any]] = {}
            for row in rows:
                node_id = row.get(id_key)
                if not isinstance(node_id, str) or _IDENTIFIER.fullmatch(node_id) is None:
                    raise CandidateReviewError(
                        "candidate_review_identity_invalid",
                        f"Wave1 {node_type} contains an invalid node identity",
                    )
                if node_id in index:
                    raise CandidateReviewError(
                        "candidate_review_duplicate_node_id",
                        f"Wave1 {node_type} contains a duplicate node identity",
                        409,
                    )
                if not self._closed_gates(row):
                    raise CandidateReviewError(
                        "candidate_review_authority_escalation",
                        f"Wave1 {node_type} does not keep every protected gate false",
                        409,
                    )
                index[node_id] = row
            expected = counts.get(_COUNT_KEYS[node_type])
            if expected != len(rows):
                raise CandidateReviewError(
                    "candidate_review_count_mismatch",
                    f"Wave1 {node_type} count no longer matches its manifest",
                    409,
                )
            indexes[node_type] = index

        self._validate_hierarchy(indexes)
        crops = support["visual_crop_manifest.jsonl"]
        expected_crops = counts.get("exact_crop_records")
        if expected_crops != len(crops):
            raise CandidateReviewError(
                "candidate_review_crop_count_mismatch",
                "Wave1 visual crop count no longer matches its manifest",
                409,
            )
        crop_index = self._crop_index(crops, indexes["paper"])
        evidence_by_node = self._evidence_crosswalk(records, crop_index)
        duplicates = support["duplicate_and_near_duplicate_candidates.jsonl"]
        if any(not self._closed_gates(row) for row in duplicates):
            raise CandidateReviewError(
                "candidate_review_authority_escalation",
                "Wave1 duplicate boundary does not keep every protected gate false",
                409,
            )
        expected_duplicates = counts.get("duplicate_or_near_duplicate_candidates")
        if expected_duplicates != len(duplicates):
            raise CandidateReviewError(
                "candidate_review_duplicate_boundary_mismatch",
                "Wave1 duplicate/near-duplicate boundary no longer matches its manifest",
                409,
            )
        return {
            "manifest": manifest,
            "manifest_sha256": _sha256(manifest_raw),
            "records": records,
            "indexes": indexes,
            "crops": crops,
            "crop_index": crop_index,
            "evidence_by_node": evidence_by_node,
            "duplicates": duplicates,
            "file_hashes": file_hashes,
        }

    @staticmethod
    def _manifest_evidence_role(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        role = value.strip().casefold()
        if role in _QUESTION_MANIFEST_ROLES:
            return "question"
        if role.startswith("shared_"):
            return "shared_material"
        return None

    @staticmethod
    def _manifest_crop_path(value: Any) -> PurePosixPath | None:
        if not isinstance(value, str) or not value or "\\" in value:
            return None
        path = PurePosixPath(value)
        expected_root = PurePosixPath(CROP_BATCH_RELATIVE.as_posix())
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            return None
        try:
            path.relative_to(expected_root)
        except ValueError:
            return None
        return path

    def _crop_index(
        self,
        crops: list[dict[str, Any]],
        papers: dict[str, dict[str, Any]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        index: dict[tuple[str, str], dict[str, Any]] = {}
        for crop in crops:
            paper_id = crop.get("paper_id")
            crop_id = crop.get("upstream_crop_id")
            crop_sha256 = crop.get("crop_sha256")
            if (
                paper_id not in papers
                or not isinstance(crop_id, str)
                or _IDENTIFIER.fullmatch(crop_id) is None
                or not isinstance(crop_sha256, str)
                or _SHA256.fullmatch(crop_sha256) is None
                or self._manifest_crop_path(crop.get("crop_path")) is None
            ):
                raise CandidateReviewError(
                    "candidate_review_crop_manifest_invalid",
                    "Wave1 visual crop manifest contains an invalid identity, hash, or path binding",
                    409,
                )
            role = str(crop.get("evidence_role", "")).casefold()
            if "answer" in role or "rubric" in role:
                # Answer/rubric rows may remain in the isolated source manifest,
                # but they are never projected or served as question evidence.
                pass
            elif self._manifest_evidence_role(role) is None:
                raise CandidateReviewError(
                    "candidate_review_crop_role_invalid",
                    "Wave1 visual crop manifest contains an unsupported evidence role",
                    409,
                )
            if not self._closed_gates(crop):
                raise CandidateReviewError(
                    "candidate_review_authority_escalation",
                    "Wave1 visual crop evidence does not keep every protected gate false",
                    409,
                )
            key = (paper_id, crop_id)
            if key in index:
                raise CandidateReviewError(
                    "candidate_review_duplicate_crop_binding",
                    "Wave1 visual crop manifest repeats a paper/crop identity",
                    409,
                )
            index[key] = crop
        return index

    @staticmethod
    def _record_evidence(
        row: dict[str, Any], field: str, expected_role: str
    ) -> list[dict[str, Any]]:
        value = row.get(field, [])
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise CandidateReviewError(
                "candidate_review_record_evidence_invalid",
                "Wave1 record-local evidence is not a descriptor list",
                409,
            )
        allowed = (
            _QUESTION_LOCAL_ROLES
            if expected_role == "question"
            else _SHARED_LOCAL_ROLES
        )
        for item in value:
            role = str(item.get("evidence_role", "")).casefold()
            if role not in allowed:
                raise CandidateReviewError(
                    "candidate_review_record_evidence_role_invalid",
                    "Wave1 record-local evidence has an unsupported role",
                    409,
                )
        return value

    def _evidence_crosswalk(
        self,
        records: dict[str, list[dict[str, Any]]],
        crop_index: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        crosswalk: dict[tuple[str, str], list[dict[str, Any]]] = {}
        exact_fields = (
            "crop_path",
            "crop_sha256",
            "source_page_number",
            "original_source_sha256",
            "whole_page_sha256",
        )
        for node_type, rows in records.items():
            id_key = _NODE_FILES[node_type][1]
            for row in rows:
                paper_id = row.get("paper_id") or row.get("parent_paper_id")
                node_id = row[id_key]
                bindings: list[dict[str, Any]] = []
                seen: set[str] = set()
                fields = (
                    ("question_evidence", "question"),
                    ("shared_material_evidence", "shared_material"),
                )
                for field, expected_role in fields:
                    for local in self._record_evidence(row, field, expected_role):
                        crop_id = local.get("crop_id")
                        if (
                            not isinstance(paper_id, str)
                            or not isinstance(crop_id, str)
                            or _IDENTIFIER.fullmatch(crop_id) is None
                        ):
                            raise CandidateReviewError(
                                "candidate_review_record_evidence_invalid",
                                "Wave1 record-local evidence has an invalid paper/crop identity",
                                409,
                            )
                        manifest_crop = crop_index.get((paper_id, crop_id))
                        if manifest_crop is None:
                            raise CandidateReviewError(
                                "candidate_review_crop_crosswalk_missing",
                                "Wave1 record-local evidence has no exact paper/crop manifest match",
                                409,
                            )
                        manifest_role = self._manifest_evidence_role(
                            manifest_crop.get("evidence_role")
                        )
                        if manifest_role != expected_role or any(
                            local.get(name) != manifest_crop.get(name)
                            for name in exact_fields
                        ):
                            raise CandidateReviewError(
                                "candidate_review_crop_crosswalk_mismatch",
                                "Wave1 record-local evidence disagrees with its exact paper/crop manifest binding",
                                409,
                            )
                        if crop_id in seen:
                            raise CandidateReviewError(
                                "candidate_review_duplicate_record_crop",
                                "Wave1 record-local evidence repeats a crop identity",
                                409,
                            )
                        seen.add(crop_id)
                        bindings.append(
                            {
                                "crop_id": crop_id,
                                "evidence_role": expected_role,
                                "source_page": manifest_crop.get("source_page_number"),
                                "sha256": manifest_crop["crop_sha256"],
                                "manifest_crop": manifest_crop,
                            }
                        )
                if node_type == "atomic_part" and not any(
                    item["evidence_role"] == "question" for item in bindings
                ):
                    raise CandidateReviewError(
                        "candidate_review_atomic_question_evidence_missing",
                        "every Wave1 atomic part must retain exact question evidence",
                        409,
                    )
                crosswalk[(node_type, node_id)] = bindings
        return crosswalk

    @staticmethod
    def _validate_hierarchy(indexes: dict[str, dict[str, dict[str, Any]]]) -> None:
        papers = indexes["paper"]
        themes = indexes["theme_big_question"]
        printed = indexes["printed_question"]
        for row in themes.values():
            if row.get("parent_paper_id") not in papers:
                raise CandidateReviewError(
                    "candidate_review_parent_chain_invalid",
                    "Wave1 theme has no exact paper parent",
                    409,
                )
        for row in printed.values():
            theme = themes.get(row.get("theme_big_question_id"))
            if theme is None or row.get("paper_id") != theme.get("parent_paper_id"):
                raise CandidateReviewError(
                    "candidate_review_parent_chain_invalid",
                    "Wave1 printed question has no exact paper/theme parent chain",
                    409,
                )
        for row in indexes["atomic_part"].values():
            question = printed.get(row.get("printed_question_id"))
            if (
                question is None
                or row.get("theme_big_question_id") != question.get("theme_big_question_id")
                or row.get("paper_id") != question.get("paper_id")
            ):
                raise CandidateReviewError(
                    "candidate_review_parent_chain_invalid",
                    "Wave1 atomic part has no exact four-level parent chain",
                    409,
                )

    @staticmethod
    def _paper_title(row: dict[str, Any]) -> str:
        metadata = row.get("source_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        return next(
            (
                title
                for title in (
                    _known_text(row.get("original_title")),
                    _known_text(row.get("article_title")),
                    _known_text(metadata.get("paper_face_title")),
                    _known_text(metadata.get("title")),
                    _known_text(metadata.get("article_title")),
                )
                if title is not None
            ),
            "unknown",
        )

    def _parent_chain(
        self,
        node_type: str,
        row: dict[str, Any],
        indexes: dict[str, dict[str, dict[str, Any]]],
    ) -> list[dict[str, str]]:
        paper_id = row.get("paper_id") or row.get("parent_paper_id")
        paper = indexes["paper"].get(paper_id, {})
        chain = [
            {
                "node_type": "paper",
                "node_id": str(_unknown(paper_id)),
                "label": self._paper_title(paper),
            }
        ]
        if node_type == "paper":
            return chain
        theme_id = row.get("theme_big_question_id") or row.get("theme_id")
        theme = indexes["theme_big_question"].get(theme_id, {})
        chain.append(
            {
                "node_type": "theme_big_question",
                "node_id": str(_unknown(theme_id)),
                "label": str(_unknown(theme.get("printed_title"))),
            }
        )
        if node_type == "theme_big_question":
            return chain
        printed_id = row.get("printed_question_id")
        printed = indexes["printed_question"].get(printed_id, {})
        chain.append(
            {
                "node_type": "printed_question",
                "node_id": str(_unknown(printed_id)),
                "label": str(_unknown(printed.get("printed_question_number"))),
            }
        )
        if node_type == "printed_question":
            return chain
        chain.append(
            {
                "node_type": "atomic_part",
                "node_id": str(_unknown(row.get("atomic_part_id"))),
                "label": str(_unknown(row.get("printed_subpart_label"))),
            }
        )
        return chain

    def _crop_refs(
        self,
        node_type: str,
        node_id: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for binding in snapshot["evidence_by_node"].get((node_type, node_id), []):
            item = {
                "crop_id": binding["crop_id"],
                "evidence_role": binding["evidence_role"],
                "source_page": _unknown(binding.get("source_page")),
                "sha256": binding["sha256"],
                # Kept for backward-compatible consumers while the canonical
                # descriptor field is now `sha256`.
                "crop_sha256": binding["sha256"],
                "content_type": "image/png",
                "access": "teacher_loopback_read_only",
            }
            if node_type == "atomic_part":
                item["image_endpoint"] = (
                    "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
                    f"atomic_part/{quote(node_id, safe='')}/question-crops/"
                    f"{quote(binding['crop_id'], safe='')}"
                )
            items.append(item)
        items.sort(
            key=lambda item: (
                0 if item["evidence_role"] == "question" else 1,
                str(item["source_page"]),
                str(item["crop_id"]),
            )
        )
        return {
            "status": "candidate_teacher_only_visual_evidence" if items else "unknown",
            "count": len(items),
            "items": items,
            "paths_exposed": False,
            "human_reviewed": False,
        }

    @staticmethod
    def _difficulty(value: Any) -> dict[str, Any]:
        difficulty = value if isinstance(value, dict) else {}
        factors = []
        raw_factors = difficulty.get("factors")
        if isinstance(raw_factors, list):
            for factor in raw_factors:
                if not isinstance(factor, dict):
                    continue
                factors.append(
                    {
                        "dimension_id": _unknown(factor.get("dimension_id")),
                        "value": factor.get("value", "unknown"),
                        "confidence": factor.get("confidence", "unknown"),
                        "basis": _unknown(
                            factor.get("basis"), "blocked_pending_review"
                        ),
                        "evidence_refs": _collect_safe_references(
                            factor.get("evidence_refs", [])
                        ),
                    }
                )
        return {
            "cognitive_prelabel": _unknown(
                difficulty.get("cognitive_prelabel"), "blocked_pending_review"
            ),
            "cognitive_evidence_status": "candidate_pending_human"
            if factors
            else "blocked_pending_review",
            "measured_difficulty": "blocked_pending_review",
            "calibration_status": _unknown(
                difficulty.get("calibration_status"), "blocked_pending_review"
            ),
            "factors": factors,
        }

    @staticmethod
    def _answer_boundary(value: Any) -> dict[str, Any]:
        answer = value if isinstance(value, dict) else {}
        authority = str(_unknown(answer.get("authority"), "none"))
        verified = answer.get("answer_verified") is True
        official_claim = answer.get("official_answer_claim_allowed") is True
        if verified or official_claim or authority.casefold().startswith("official"):
            raise CandidateReviewError(
                "candidate_review_authority_escalation",
                "Wave1 candidate answer metadata attempts to claim verified or official authority",
                409,
            )
        evidence = answer.get("exact_image_evidence")
        return {
            "availability": _unknown(answer.get("availability")),
            "authority": authority,
            "answer_verified": False,
            "official": False,
            "official_answer_claim_allowed": False,
            "evidence_descriptor_count": len(evidence)
            if isinstance(evidence, list)
            else 0,
            "evidence_served": False,
        }

    @staticmethod
    def _rubric_boundary(value: Any) -> dict[str, Any]:
        rubric = value if isinstance(value, dict) else {}
        authority = str(_unknown(rubric.get("authority"), "none"))
        verified = rubric.get("rubric_verified") is True
        official_claim = rubric.get("official_scoring_claim_allowed") is True
        if verified or official_claim or authority.casefold().startswith("official"):
            raise CandidateReviewError(
                "candidate_review_authority_escalation",
                "Wave1 candidate rubric metadata attempts to claim verified or official authority",
                409,
            )
        evidence = rubric.get("exact_image_evidence")
        return {
            "availability": _unknown(rubric.get("availability")),
            "authority": authority,
            "rubric_verified": False,
            "official": False,
            "official_scoring_claim_allowed": False,
            "evidence_descriptor_count": len(evidence)
            if isinstance(evidence, list)
            else 0,
            "evidence_served": False,
        }

    def _project(
        self,
        node_type: str,
        row: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        indexes = snapshot["indexes"]
        node_id = row[_NODE_FILES[node_type][1]]
        source_ref_count = len(_collect_safe_references(row))
        base: dict[str, Any] = {
            "source_namespace": SOURCE_NAMESPACE,
            "batch_id": BATCH_ID,
            "node_type": node_type,
            "node_id": node_id,
            "parent_chain": self._parent_chain(node_type, row, indexes),
            "source_refs": {
                "status": "local_paths_not_exposed",
                "count": source_ref_count,
                "items": [],
            },
            "crop_refs": self._crop_refs(node_type, node_id, snapshot),
            "authority": dict(_AUTHORITY),
        }
        if node_type == "paper":
            base.update(
                {
                    "title": self._paper_title(row),
                    "title_status": _unknown(
                        row.get("original_title_status"), "blocked_pending_review"
                    ),
                    "source": {
                        "source_id": _unknown(row.get("source_id")),
                        "source_layer": _unknown(row.get("source_layer")),
                        "source_account": _unknown(row.get("source_account")),
                        "original_url": _unknown(row.get("original_url")),
                        "source_package_access": "local_path_not_exposed",
                        "source_package_manifest_sha256": _unknown(
                            row.get("source_package_manifest_sha256")
                        ),
                    },
                    "identity_axes": row.get("identity_axes", "unknown"),
                    "completeness": _unknown(row.get("completeness")),
                    "structure_observation": _unknown(
                        row.get("structure_observation"), "blocked_pending_review"
                    ),
                    "child_counts": {
                        "theme_big_questions": len(
                            row.get("theme_big_question_ids", [])
                        ),
                        "printed_questions": sum(
                            1
                            for item in indexes["printed_question"].values()
                            if item.get("paper_id") == node_id
                        ),
                        "atomic_parts": sum(
                            1
                            for item in indexes["atomic_part"].values()
                            if item.get("paper_id") == node_id
                        ),
                    },
                }
            )
        elif node_type == "theme_big_question":
            base.update(
                {
                    "title": _unknown(row.get("printed_title")),
                    "title_status": _unknown(
                        row.get("title_status"), "blocked_pending_review"
                    ),
                    "sequence_in_paper": _unknown(row.get("sequence_in_paper")),
                    "page_span": _unknown(row.get("page_span")),
                    "theme_context": _unknown(
                        row.get("theme_context"), "blocked_pending_review"
                    ),
                    "theme_core_knowledge_K": _unknown(
                        row.get("theme_core_knowledge_K"),
                        "blocked_pending_review",
                    ),
                    "theme_profile": _unknown(
                        row.get("theme_profile"), "blocked_pending_review"
                    ),
                    "shared_material_evidence": _unknown(
                        row.get("shared_material_evidence"),
                        "blocked_pending_review",
                    ),
                    "dependency_summary": _unknown(
                        row.get("dependency_summary"), "blocked_pending_review"
                    ),
                    "child_counts": {
                        "printed_questions": len(row.get("printed_question_ids", [])),
                        "atomic_parts": sum(
                            1
                            for item in indexes["atomic_part"].values()
                            if item.get("theme_big_question_id") == node_id
                        ),
                    },
                }
            )
        elif node_type == "printed_question":
            base.update(
                {
                    "title": _unknown(row.get("printed_question_number")),
                    "sequence_in_theme": _unknown(row.get("sequence_in_theme")),
                    "task_summary": _unknown(
                        row.get("task_summary"), "blocked_pending_review"
                    ),
                    "page_span": _unknown(row.get("page_span")),
                    "boundary_status": _unknown(
                        row.get("boundary_status"), "blocked_pending_review"
                    ),
                    "aggregate_profile": _unknown(
                        row.get("aggregate_profile"), "blocked_pending_review"
                    ),
                    "shared_material_evidence": _unknown(
                        row.get("shared_material_evidence"),
                        "blocked_pending_review",
                    ),
                    "dependency_summary": _unknown(
                        row.get("dependency_summary"), "blocked_pending_review"
                    ),
                    "child_counts": {
                        "atomic_parts": len(row.get("atomic_part_ids", []))
                    },
                }
            )
        else:
            base.update(
                {
                    "title": _unknown(row.get("printed_subpart_label")),
                    "task_summary": _unknown(
                        row.get("task_summary"), "blocked_pending_review"
                    ),
                    "page_span": _unknown(row.get("page_span")),
                    "item_type": {
                        "value": _unknown(
                            row.get("item_type"), "blocked_pending_review"
                        ),
                        "status": _unknown(
                            row.get("item_type_status"), "blocked_pending_review"
                        ),
                    },
                    "classification_status": _unknown(
                        row.get("classification_status"),
                        "blocked_pending_review",
                    ),
                    "classification": {
                        "K": _axis(row.get("knowledge_K")),
                        "primary_K": _unknown(
                            row.get("primary_knowledge_K"),
                            "blocked_pending_review",
                        ),
                        "supporting_K": _unknown(
                            row.get("supporting_knowledge_K"),
                            "blocked_pending_review",
                        ),
                        "A": _axis(row.get("ability_A")),
                        "C": _axis(row.get("context_C")),
                        "R": _axis(row.get("response_R")),
                        "RP": _axis(row.get("representation_RP")),
                    },
                    "difficulty": self._difficulty(row.get("difficulty")),
                    "dependency": _unknown(
                        row.get("dependency"), "blocked_pending_review"
                    ),
                    "shared_material_evidence": _unknown(
                        row.get("shared_material_evidence"),
                        "blocked_pending_review",
                    ),
                    "theme_chain": {
                        "status": _unknown(
                            row.get("theme_chain_role_status"),
                            "blocked_pending_review",
                        ),
                        "primary_role": _unknown(
                            row.get("primary_theme_chain_role"),
                            "blocked_pending_review",
                        ),
                        "supporting_roles": _unknown(
                            row.get("supporting_theme_chain_roles"),
                            "blocked_pending_review",
                        ),
                        "human_reviewed": False,
                    },
                    "answer_status": self._answer_boundary(
                        row.get("answer_candidate")
                    ),
                    "rubric_status": self._rubric_boundary(
                        row.get("rubric_candidate")
                    ),
                }
            )
        safe = _browser_safe(base)
        if not isinstance(safe, dict):
            raise CandidateReviewError(
                "candidate_review_projection_invalid",
                "Wave1 browser projection could not be made path-safe",
                409,
            )
        return safe

    @staticmethod
    def _search_text(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True).casefold()

    @staticmethod
    def _validated_identifier(value: str | None, field: str) -> str | None:
        if value is None or value == "":
            return None
        if _IDENTIFIER.fullmatch(value) is None:
            raise CandidateReviewError(
                "candidate_review_filter_invalid", f"invalid {field}", 400
            )
        return value

    @staticmethod
    def _pagination(limit: int, offset: int) -> None:
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 200 or offset < 0:
            raise CandidateReviewError(
                "candidate_review_pagination_invalid",
                "candidate review pagination is invalid",
                400,
            )

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        counts = snapshot["manifest"]["counts"]
        return {
            "schema_version": "shchem_candidate_review_only_wave1_status_v1",
            "source_namespace": SOURCE_NAMESPACE,
            "batch_id": BATCH_ID,
            "source_lineage": OVERLAY_V2_SOURCE,
            "same_source_disclosure": (
                "这是从 overlay v2 继续细化的同五套来源卷，不是额外新增五套试卷。"
            ),
            "additional_paper_count": 0,
            "counts": {
                "papers": counts["papers"],
                "theme_big_questions": counts["theme_big_questions"],
                "printed_questions": counts["printed_questions"],
                "atomic_parts": counts["atomic_parts"],
                "duplicate_or_near_duplicate_candidates": counts[
                    "duplicate_or_near_duplicate_candidates"
                ],
            },
            "manifest_sha256": snapshot["manifest_sha256"],
            "verified_files": snapshot["file_hashes"],
            "all_protected_gates_false": True,
            "authority": dict(_AUTHORITY),
            "endpoints": {
                "read_only_get": True,
                "mutation": False,
                "apply": False,
                "promotion": False,
            },
        }

    def list_nodes(
        self,
        *,
        node_type: str | None,
        query: str | None,
        paper_id: str | None,
        theme_id: str | None,
        printed_question_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self._pagination(limit, offset)
        if node_type in {None, ""}:
            node_types = tuple(_NODE_FILES)
            node_type = None
        elif node_type in _NODE_FILES:
            node_types = (node_type,)
        else:
            raise CandidateReviewError(
                "candidate_review_node_type_invalid", "unsupported node_type", 400
            )
        paper_id = self._validated_identifier(paper_id, "paper_id")
        theme_id = self._validated_identifier(theme_id, "theme_id")
        printed_question_id = self._validated_identifier(
            printed_question_id, "printed_question_id"
        )
        query = (query or "").strip()
        if len(query) > 120 or any(ord(character) < 32 for character in query):
            raise CandidateReviewError(
                "candidate_review_query_invalid", "invalid candidate review query", 400
            )
        needle = query.casefold()
        snapshot = self._snapshot()
        rows: list[dict[str, Any]] = []
        for current_type in node_types:
            for row in snapshot["records"][current_type]:
                row_paper_id = row.get("paper_id") or row.get("parent_paper_id")
                if paper_id is not None and row_paper_id != paper_id:
                    continue
                row_theme_id = row.get("theme_big_question_id") or row.get("theme_id")
                if theme_id is not None and row_theme_id != theme_id:
                    continue
                if (
                    printed_question_id is not None
                    and row.get("printed_question_id") != printed_question_id
                ):
                    continue
                projected = self._project(current_type, row, snapshot)
                if needle and needle not in self._search_text(projected):
                    continue
                rows.append(projected)
        rows.sort(key=lambda item: (item["parent_chain"][0]["node_id"], item["node_type"], item["node_id"]))
        total = len(rows)
        return {
            "schema_version": "shchem_candidate_review_only_wave1_list_v1",
            "source_namespace": SOURCE_NAMESPACE,
            "items": rows[offset : offset + limit],
            "count": len(rows[offset : offset + limit]),
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": {
                "node_type": node_type,
                "query": query,
                "paper_id": paper_id,
                "theme_id": theme_id,
                "printed_question_id": printed_question_id,
            },
            "authority": dict(_AUTHORITY),
        }

    def node(self, node_type: str, node_id: str) -> dict[str, Any]:
        if node_type not in _NODE_FILES:
            raise CandidateReviewError(
                "candidate_review_node_type_invalid", "unsupported node_type", 400
            )
        safe_id = self._validated_identifier(node_id, "node_id")
        snapshot = self._snapshot()
        row = snapshot["indexes"][node_type].get(safe_id)
        if row is None:
            raise CandidateReviewError(
                "candidate_review_node_not_found",
                "Wave1 candidate review node was not found",
                404,
            )
        return self._project(node_type, row, snapshot)

    @staticmethod
    def _nested_crop_ids(value: Any) -> set[str]:
        crop_ids: set[str] = set()

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if key in {"crop_id", "upstream_crop_id"} and isinstance(
                        nested, str
                    ):
                        crop_ids.add(nested)
                    else:
                        walk(nested)
            elif isinstance(item, list):
                for nested in item:
                    walk(nested)

        walk(value)
        return crop_ids

    def _resolved_crop_file(self, manifest_crop: dict[str, Any]) -> Path:
        posix_path = self._manifest_crop_path(manifest_crop.get("crop_path"))
        if posix_path is None:
            raise CandidateReviewError(
                "candidate_review_crop_path_boundary_invalid",
                "Wave1 crop path is outside the fixed evidence batch root",
                409,
            )
        relative = posix_path.relative_to(
            PurePosixPath(CROP_BATCH_RELATIVE.as_posix())
        )
        cursor = self.crop_batch_root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise CandidateReviewError(
                    "candidate_review_crop_symlink_denied",
                    "Wave1 crop path must not traverse a symlink",
                    409,
                )
        try:
            resolved = cursor.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise CandidateReviewError(
                "candidate_review_crop_file_missing",
                "Wave1 crop file is unavailable",
                409,
            ) from exc
        if (
            not resolved.is_relative_to(self.crop_batch_root)
            or not resolved.is_file()
        ):
            raise CandidateReviewError(
                "candidate_review_crop_path_boundary_invalid",
                "Wave1 crop path is outside the fixed evidence batch root",
                409,
            )
        return resolved

    @staticmethod
    def _valid_png(data: bytes) -> bool:
        if not data.startswith(PNG_SIGNATURE):
            return False
        offset = len(PNG_SIGNATURE)
        saw_ihdr = False
        saw_idat = False
        while offset < len(data):
            if offset + 12 > len(data):
                return False
            length = int.from_bytes(data[offset : offset + 4], "big")
            chunk_type = data[offset + 4 : offset + 8]
            chunk_end = offset + 12 + length
            if chunk_end > len(data):
                return False
            chunk_data = data[offset + 8 : offset + 8 + length]
            expected_crc = int.from_bytes(
                data[offset + 8 + length : chunk_end], "big"
            )
            actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
            if actual_crc != expected_crc:
                return False
            if not saw_ihdr:
                if chunk_type != b"IHDR" or length != 13:
                    return False
                width = int.from_bytes(chunk_data[0:4], "big")
                height = int.from_bytes(chunk_data[4:8], "big")
                if width < 1 or height < 1:
                    return False
                saw_ihdr = True
            elif chunk_type == b"IHDR":
                return False
            if chunk_type == b"IDAT":
                saw_idat = True
            if chunk_type == b"IEND":
                return length == 0 and saw_ihdr and saw_idat and chunk_end == len(data)
            offset = chunk_end
        return False

    def question_crop(self, node_id: str, crop_id: str) -> CandidateCropPayload:
        safe_node_id = self._validated_identifier(node_id, "node_id")
        safe_crop_id = self._validated_identifier(crop_id, "crop_id")
        snapshot = self._snapshot()
        row = snapshot["indexes"]["atomic_part"].get(safe_node_id)
        if row is None:
            raise CandidateReviewError(
                "candidate_review_node_not_found",
                "Wave1 candidate atomic part was not found",
                404,
            )
        binding = next(
            (
                item
                for item in snapshot["evidence_by_node"].get(
                    ("atomic_part", safe_node_id), []
                )
                if item["crop_id"] == safe_crop_id
                and item["evidence_role"] in {"question", "shared_material"}
            ),
            None,
        )
        protected_ids = self._nested_crop_ids(row.get("answer_candidate")) | self._nested_crop_ids(
            row.get("rubric_candidate")
        )
        if safe_crop_id in protected_ids:
            raise CandidateReviewError(
                "candidate_review_crop_role_denied",
                "answer and rubric evidence cannot be served by the question-crop endpoint",
                403,
            )
        if binding is None:
            raise CandidateReviewError(
                "candidate_review_crop_not_found",
                "the requested crop is not question/shared evidence for this atomic part",
                404,
            )
        manifest_crop = binding["manifest_crop"]
        manifest_role = self._manifest_evidence_role(manifest_crop.get("evidence_role"))
        if manifest_role != binding["evidence_role"]:
            raise CandidateReviewError(
                "candidate_review_crop_role_denied",
                "the requested crop is not an allowed question/shared evidence role",
                403,
            )
        path = self._resolved_crop_file(manifest_crop)
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_CROP_BYTES + 1)
        except OSError as exc:
            raise CandidateReviewError(
                "candidate_review_crop_read_failed",
                "Wave1 crop file could not be read",
                409,
            ) from exc
        if len(data) > MAX_CROP_BYTES:
            raise CandidateReviewError(
                "candidate_review_crop_too_large",
                "Wave1 crop exceeds the one MiB response limit",
                413,
            )
        if not self._valid_png(data):
            raise CandidateReviewError(
                "candidate_review_crop_not_png",
                "Wave1 crop is not a structurally valid PNG",
                409,
            )
        expected_sha256 = binding["sha256"]
        if _sha256(data) != expected_sha256:
            raise CandidateReviewError(
                "candidate_review_crop_hash_mismatch",
                "Wave1 crop bytes no longer match the exact evidence binding",
                409,
            )
        return CandidateCropPayload(data=data, sha256=expected_sha256)


__all__ = [
    "BATCH_ID",
    "BATCH_RELATIVE",
    "CROP_BATCH_RELATIVE",
    "MAX_CROP_BYTES",
    "SOURCE_NAMESPACE",
    "CandidateCropPayload",
    "CandidateReviewError",
    "Wave1CandidateReviewReader",
]
