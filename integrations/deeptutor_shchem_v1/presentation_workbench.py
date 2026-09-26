from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from zipfile import BadZipFile, ZipFile


INPUT_SCHEMA_VERSION = "shchem.presentation-input.v1"
DECK_SCHEMA_VERSION = "shchem.presentation-deck-json.v1"
QA_SCHEMA_VERSION = "shchem.presentation-qa.v1"
OUTPUT_MANIFEST_SCHEMA_VERSION = "shchem.presentation-output-manifest.v1"

SLIDE_WIDTH = 1280
SLIDE_HEIGHT = 720
MIN_DECK_TITLE_PT = 50
MIN_SLIDE_TITLE_PT = 35
MIN_SUBHEADING_PT = 24
MIN_BODY_PT = 16

MANDATORY_SLIDE_TYPES = (
    "cover",
    "objectives",
    "knowledge_thread",
    "textbook_evidence",
    "theme_shared_material",
    "question_prompt",
    "question_progression",
    "student_error_causes",
    "classroom_interaction",
    "practice",
    "answer_review",
    "homework",
)

ALLOWED_ASSET_ROLES = frozenset(
    {
        "apparatus",
        "chart",
        "chemical_structure",
        "data_table",
        "question_crop",
        "shared_material",
        "textbook_page",
    }
)
ALLOWED_ANSWER_STATUSES = frozenset(
    {"nonofficial_reference", "teacher_candidate", "blocked_pending_review", "absent"}
)
ALLOWED_DIAGNOSIS_STATUSES = frozenset(
    {"synthetic_fixture_only", "provisional_anonymized", "teacher_confirmed_anonymized"}
)
ALLOWED_REVEAL_STAGES = frozenset({"launch", "prompt", "scaffold", "reveal", "transfer"})
ALLOWED_RESPONSE_MODES = frozenset(
    {
        "choice",
        "fill_blank",
        "short_answer",
        "calculation",
        "equation",
        "structure",
        "experimental_evaluation",
        "reason_explanation",
        "unknown",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNRESOLVED_MARKERS = ("{{", "}}", "[TODO]", "<TODO>", "???")
_DIRECT_IDENTIFIER_KEYS = frozenset(
    {
        "student_name",
        "real_name",
        "name",
        "email",
        "phone",
        "mobile",
        "id_card",
        "address",
        "school_number",
        "student_number",
        "姓名",
        "真实姓名",
        "手机号",
        "身份证号",
        "学号",
        "住址",
    }
)
_FORBIDDEN_VISIBLE_GOVERNANCE_TERMS = (
    "publication_allowed",
    "blocked_pending_review",
    "synthetic_fixture_only",
    "teacher_confirmation_required",
)


class PresentationWorkbenchError(RuntimeError):
    def __init__(
        self,
        code: str,
        message_zh: str,
        status: int = 400,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.status = status
        self.details = dict(details or {})


@dataclass(frozen=True)
class PresentationToolchain:
    node: Path
    node_modules: Path
    bin_dir: Path
    python: Path
    skill_dir: Path

    @classmethod
    def from_environment(cls) -> "PresentationToolchain":
        required = {
            "RUNTIME_NODE": os.environ.get("RUNTIME_NODE"),
            "RUNTIME_NODE_MODULES": os.environ.get("RUNTIME_NODE_MODULES"),
            "RUNTIME_BIN_DIR": os.environ.get("RUNTIME_BIN_DIR"),
            "PRESENTATIONS_SKILL_DIR": os.environ.get("PRESENTATIONS_SKILL_DIR"),
        }
        missing = sorted(key for key, value in required.items() if not value)
        if missing:
            raise PresentationWorkbenchError(
                "presentation_toolchain_missing",
                "演示文稿运行环境未完整配置。",
                details={"missing_environment_variables": missing},
            )
        result = cls(
            node=Path(str(required["RUNTIME_NODE"])),
            node_modules=Path(str(required["RUNTIME_NODE_MODULES"])),
            bin_dir=Path(str(required["RUNTIME_BIN_DIR"])),
            python=Path(os.environ.get("PRESENTATIONS_PYTHON") or sys.executable),
            skill_dir=Path(str(required["PRESENTATIONS_SKILL_DIR"])),
        )
        result.validate()
        return result

    def validate(self) -> None:
        checks = {
            "node": self.node.is_file(),
            "node_modules": self.node_modules.is_dir(),
            "bin_dir": self.bin_dir.is_dir(),
            "python": self.python.is_file(),
            "skill_dir": self.skill_dir.is_dir(),
            "render_slides": (self.skill_dir / "container_tools" / "render_slides.py").is_file(),
            "slides_test": (self.skill_dir / "container_tools" / "slides_test.py").is_file(),
            "create_montage": (
                self.skill_dir / "container_tools" / "create_montage.py"
            ).is_file(),
        }
        failed = sorted(key for key, ok in checks.items() if not ok)
        if failed:
            raise PresentationWorkbenchError(
                "presentation_toolchain_invalid",
                "演示文稿运行环境中的必要文件不可用。",
                details={"failed_checks": failed},
            )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PresentationWorkbenchError(
            "presentation_input_invalid", f"{field}必须是对象。"
        )
    return value


def _list(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = 100,
) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise PresentationWorkbenchError(
            "presentation_input_invalid",
            f"{field}数量必须在{minimum}—{maximum}之间。",
        )
    return value


def _text(value: Any, field: str, *, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PresentationWorkbenchError(
            "presentation_input_invalid", f"{field}格式不正确。"
        )
    return value.strip()


def _optional_text(value: Any, field: str, *, limit: int = 2000) -> str | None:
    if value is None:
        return None
    return _text(value, field, limit=limit)


def _text_list(
    value: Any,
    field: str,
    *,
    minimum: int = 1,
    maximum: int = 20,
    item_limit: int = 500,
) -> list[str]:
    return [
        _text(row, f"{field}[{index}]", limit=item_limit)
        for index, row in enumerate(
            _list(value, field, minimum=minimum, maximum=maximum)
        )
    ]


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PresentationWorkbenchError(
            "presentation_input_invalid", f"{field}必须是{minimum}—{maximum}的整数。"
        )
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise PresentationWorkbenchError(
            "presentation_input_invalid", f"{field}必须是布尔值。"
        )
    return value


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresentationWorkbenchError(
            "presentation_input_invalid", f"{field}必须是0—1之间的数。"
        )
    result = float(value)
    if not 0 <= result <= 1:
        raise PresentationWorkbenchError(
            "presentation_input_invalid", f"{field}必须是0—1之间的数。"
        )
    return result


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str, code: str) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown or missing:
        raise PresentationWorkbenchError(
            code,
            f"{field}字段不完整或含未知字段。",
            details={"field": field, "unknown": unknown, "missing": missing},
        )


def _reject_direct_identifiers(value: Any, *, path: str = "input") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in _DIRECT_IDENTIFIER_KEYS:
                raise PresentationWorkbenchError(
                    "student_identifier_forbidden",
                    "课程PPT输入不得包含学生直接身份信息。",
                    details={"field_path": f"{path}.{key}"},
                )
            _reject_direct_identifiers(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_direct_identifiers(nested, path=f"{path}[{index}]")


def _find_unresolved_markers(value: Any) -> list[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(marker for marker in _UNRESOLVED_MARKERS if marker in value)
    elif isinstance(value, Mapping):
        for nested in value.values():
            found.update(_find_unresolved_markers(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_find_unresolved_markers(nested))
    return sorted(found)


def _resolve_asset_path(asset_root: Path, relative_path: str) -> Path:
    root = asset_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PresentationWorkbenchError(
            "presentation_asset_path_escape",
            "课件素材路径必须位于指定素材目录内。",
            details={"path": relative_path},
        ) from exc
    return candidate


def _image_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index : index + 2], "big")
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            } and index + 7 <= len(data):
                return (
                    int.from_bytes(data[index + 5 : index + 7], "big"),
                    int.from_bytes(data[index + 3 : index + 5], "big"),
                )
            index += max(length, 2)
    raise PresentationWorkbenchError(
        "presentation_asset_format_invalid",
        "课件素材必须是可核验尺寸的PNG或JPEG位图。",
        details={"path": str(path)},
    )


def _asset_content_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    raise PresentationWorkbenchError(
        "presentation_asset_format_invalid",
        "课件素材必须使用PNG或JPEG格式。",
        details={"path": path},
    )


def _validate_asset(value: Mapping[str, Any], *, asset_root: Path) -> dict[str, Any]:
    expected = {
        "asset_id",
        "role",
        "path",
        "sha256",
        "source_page",
        "source_bbox",
        "pixel_dimensions",
        "alt_text",
        "source_ref",
        "rights_boundary",
        "publication_allowed",
    }
    _exact_keys(value, expected, "theme.assets[]", "presentation_asset_contract_invalid")
    asset_id = _text(value["asset_id"], "asset.asset_id", limit=120)
    role = _text(value["role"], f"{asset_id}.role", limit=80)
    if role not in ALLOWED_ASSET_ROLES:
        raise PresentationWorkbenchError(
            "presentation_asset_role_invalid",
            "课件素材类型不受支持。",
            details={"asset_id": asset_id, "role": role},
        )
    relative_path = _text(value["path"], f"{asset_id}.path", limit=320)
    if Path(relative_path).is_absolute():
        raise PresentationWorkbenchError(
            "presentation_asset_path_absolute",
            "课件素材路径必须使用相对路径。",
            details={"asset_id": asset_id, "path": relative_path},
        )
    _asset_content_type(relative_path)
    expected_hash = _text(value["sha256"], f"{asset_id}.sha256", limit=64)
    if not _SHA256.fullmatch(expected_hash):
        raise PresentationWorkbenchError(
            "presentation_asset_hash_invalid", "课件素材SHA-256格式不正确。"
        )
    bbox = _list(value["source_bbox"], f"{asset_id}.source_bbox", minimum=4, maximum=4)
    if any(type(row) is not int or row < 0 for row in bbox) or bbox[2] <= 0 or bbox[3] <= 0:
        raise PresentationWorkbenchError(
            "presentation_asset_bbox_invalid", "课件素材bbox必须是[x,y,width,height]四个非负整数。"
        )
    dimensions = _list(
        value["pixel_dimensions"], f"{asset_id}.pixel_dimensions", minimum=2, maximum=2
    )
    if any(type(row) is not int or row <= 0 for row in dimensions):
        raise PresentationWorkbenchError(
            "presentation_asset_dimensions_invalid", "课件素材像素尺寸必须是两个正整数。"
        )
    if _boolean(value["publication_allowed"], f"{asset_id}.publication_allowed"):
        raise PresentationWorkbenchError(
            "presentation_asset_publication_boundary_invalid",
            "课程PPT素材不得在候选阶段标记为可发布。",
        )
    resolved = _resolve_asset_path(asset_root, relative_path)
    if not resolved.is_file():
        raise PresentationWorkbenchError(
            "presentation_asset_missing",
            "课件素材文件不存在。",
            details={"asset_id": asset_id, "path": relative_path},
        )
    observed_hash = _sha256_file(resolved)
    if observed_hash != expected_hash:
        raise PresentationWorkbenchError(
            "presentation_asset_hash_drift",
            "课件素材已发生哈希漂移。",
            details={
                "asset_id": asset_id,
                "expected_sha256": expected_hash,
                "observed_sha256": observed_hash,
            },
        )
    observed_dimensions = list(_image_dimensions(resolved))
    if observed_dimensions != [int(row) for row in dimensions]:
        raise PresentationWorkbenchError(
            "presentation_asset_dimensions_drift",
            "课件素材实际像素尺寸与声明不一致。",
            details={
                "asset_id": asset_id,
                "expected": dimensions,
                "observed": observed_dimensions,
            },
        )
    return {
        "asset_id": asset_id,
        "role": role,
        "path": relative_path.replace("\\", "/"),
        "sha256": expected_hash,
        "source_page": _integer(
            value["source_page"], f"{asset_id}.source_page", minimum=1, maximum=9999
        ),
        "source_bbox": [int(row) for row in bbox],
        "pixel_dimensions": [int(row) for row in dimensions],
        "alt_text": _text(value["alt_text"], f"{asset_id}.alt_text", limit=500),
        "source_ref": _text(value["source_ref"], f"{asset_id}.source_ref", limit=1000),
        "rights_boundary": _text(
            value["rights_boundary"], f"{asset_id}.rights_boundary", limit=1000
        ),
        "publication_allowed": False,
    }


def validate_presentation_input(
    value: Mapping[str, Any], *, asset_root: Path
) -> dict[str, Any]:
    value = _mapping(value, "input")
    top_keys = {
        "schema_version",
        "lesson_title",
        "grade",
        "duration_minutes",
        "candidate_use",
        "textbook",
        "lesson_goals",
        "theme",
        "diagnosis",
        "classroom_plan",
    }
    _exact_keys(value, top_keys, "input", "presentation_input_invalid")
    if value["schema_version"] != INPUT_SCHEMA_VERSION:
        raise PresentationWorkbenchError(
            "presentation_input_schema_unsupported", "课程PPT输入版本不受支持。"
        )
    _reject_direct_identifiers(value)
    unresolved = _find_unresolved_markers(value)
    if unresolved:
        raise PresentationWorkbenchError(
            "presentation_input_unresolved",
            "课程PPT输入仍含未解析占位标记。",
            details={"markers": unresolved},
        )

    candidate = _mapping(value["candidate_use"], "candidate_use")
    _exact_keys(
        candidate,
        {"intended_use", "publication_allowed", "teacher_confirmation_required"},
        "candidate_use",
        "presentation_candidate_boundary_invalid",
    )
    if candidate["intended_use"] != "local_personal_lesson_preparation_candidate":
        raise PresentationWorkbenchError(
            "presentation_candidate_boundary_invalid", "v1只支持本地个人备课候选。"
        )
    if _boolean(candidate["publication_allowed"], "candidate_use.publication_allowed"):
        raise PresentationWorkbenchError(
            "presentation_candidate_boundary_invalid", "课程PPT候选不得自动发布。"
        )
    if not _boolean(
        candidate["teacher_confirmation_required"],
        "candidate_use.teacher_confirmation_required",
    ):
        raise PresentationWorkbenchError(
            "presentation_candidate_boundary_invalid", "课程PPT候选必须要求教师确认。"
        )

    textbook = _mapping(value["textbook"], "textbook")
    textbook_keys = {
        "book_title",
        "volume",
        "chapter",
        "section",
        "publisher",
        "evidence_level",
        "evidence_anchors",
        "notes",
    }
    _exact_keys(textbook, textbook_keys, "textbook", "presentation_textbook_contract_invalid")
    anchors: list[dict[str, Any]] = []
    anchor_ids: set[str] = set()
    for index, row in enumerate(
        _list(textbook["evidence_anchors"], "textbook.evidence_anchors", minimum=1, maximum=30)
    ):
        anchor = _mapping(row, f"textbook.evidence_anchors[{index}]")
        anchor_keys = {
            "anchor_id",
            "source_path",
            "source_sha256",
            "page_number",
            "printed_page",
            "evidence_text",
            "source_ref",
        }
        _exact_keys(
            anchor,
            anchor_keys,
            f"textbook.evidence_anchors[{index}]",
            "presentation_textbook_contract_invalid",
        )
        anchor_id = _text(anchor["anchor_id"], "anchor.anchor_id", limit=120)
        if anchor_id in anchor_ids:
            raise PresentationWorkbenchError(
                "presentation_textbook_anchor_duplicate", "教材证据锚点标识不得重复。"
            )
        anchor_ids.add(anchor_id)
        source_hash = _text(anchor["source_sha256"], f"{anchor_id}.source_sha256", limit=64)
        if not _SHA256.fullmatch(source_hash):
            raise PresentationWorkbenchError(
                "presentation_textbook_hash_invalid", "教材证据SHA-256格式不正确。"
            )
        printed_page = anchor["printed_page"]
        if printed_page is not None:
            printed_page = _integer(
                printed_page, f"{anchor_id}.printed_page", minimum=1, maximum=9999
            )
        anchors.append(
            {
                "anchor_id": anchor_id,
                "source_path": _text(anchor["source_path"], f"{anchor_id}.source_path", limit=500),
                "source_sha256": source_hash,
                "page_number": _integer(
                    anchor["page_number"], f"{anchor_id}.page_number", minimum=1, maximum=9999
                ),
                "printed_page": printed_page,
                "evidence_text": _text(
                    anchor["evidence_text"], f"{anchor_id}.evidence_text", limit=800
                ),
                "source_ref": _text(anchor["source_ref"], f"{anchor_id}.source_ref", limit=1000),
            }
        )

    goals = _mapping(value["lesson_goals"], "lesson_goals")
    goal_keys = {
        "learning_objectives",
        "key_points",
        "difficult_points",
        "prerequisites",
        "lesson_emphasis",
    }
    _exact_keys(goals, goal_keys, "lesson_goals", "presentation_goals_contract_invalid")
    normalized_goals = {
        "learning_objectives": _text_list(
            goals["learning_objectives"], "lesson_goals.learning_objectives", maximum=8
        ),
        "key_points": _text_list(goals["key_points"], "lesson_goals.key_points", maximum=8),
        "difficult_points": _text_list(
            goals["difficult_points"], "lesson_goals.difficult_points", maximum=8
        ),
        "prerequisites": _text_list(
            goals["prerequisites"], "lesson_goals.prerequisites", maximum=8
        ),
        "lesson_emphasis": _text(goals["lesson_emphasis"], "lesson_goals.lesson_emphasis", limit=800),
    }

    theme = _mapping(value["theme"], "theme")
    theme_keys = {
        "paper",
        "theme_id",
        "title",
        "order",
        "page_span",
        "context_summary",
        "source_authority",
        "answer_authority",
        "human_reviewed",
        "publication_allowed",
        "assets",
        "shared_materials",
        "printed_questions",
    }
    _exact_keys(theme, theme_keys, "theme", "presentation_theme_contract_invalid")
    if _boolean(theme["human_reviewed"], "theme.human_reviewed"):
        raise PresentationWorkbenchError(
            "presentation_authority_boundary_invalid",
            "模型实现不得把主题预填为已完成人工审核。",
        )
    if _boolean(theme["publication_allowed"], "theme.publication_allowed"):
        raise PresentationWorkbenchError(
            "presentation_authority_boundary_invalid", "主题候选不得标记为可发布。"
        )

    paper = _mapping(theme["paper"], "theme.paper")
    paper_keys = {
        "paper_id",
        "title",
        "year",
        "region_or_school",
        "paper_type",
        "source_ref",
        "source_authority",
    }
    _exact_keys(paper, paper_keys, "theme.paper", "presentation_theme_contract_invalid")
    normalized_paper = {
        "paper_id": _text(paper["paper_id"], "theme.paper.paper_id", limit=160),
        "title": _text(paper["title"], "theme.paper.title", limit=500),
        "year": _integer(paper["year"], "theme.paper.year", minimum=1900, maximum=2100),
        "region_or_school": _text(
            paper["region_or_school"], "theme.paper.region_or_school", limit=200
        ),
        "paper_type": _text(paper["paper_type"], "theme.paper.paper_type", limit=160),
        "source_ref": _text(paper["source_ref"], "theme.paper.source_ref", limit=1200),
        "source_authority": _text(
            paper["source_authority"], "theme.paper.source_authority", limit=300
        ),
    }

    assets = [
        _validate_asset(_mapping(row, "theme.assets[]"), asset_root=asset_root)
        for row in _list(theme["assets"], "theme.assets", minimum=0, maximum=40)
    ]
    asset_ids = [row["asset_id"] for row in assets]
    if len(set(asset_ids)) != len(asset_ids):
        raise PresentationWorkbenchError(
            "presentation_asset_id_duplicate", "课件素材标识不得重复。"
        )
    asset_id_set = set(asset_ids)

    shared_materials: list[dict[str, Any]] = []
    shared_ids: set[str] = set()
    for index, row in enumerate(
        _list(theme["shared_materials"], "theme.shared_materials", minimum=1, maximum=20)
    ):
        material = _mapping(row, f"theme.shared_materials[{index}]")
        material_keys = {
            "shared_material_id",
            "title",
            "summary",
            "source_page",
            "asset_ids",
            "source_ref",
        }
        _exact_keys(
            material,
            material_keys,
            f"theme.shared_materials[{index}]",
            "presentation_shared_material_contract_invalid",
        )
        material_id = _text(
            material["shared_material_id"], "shared_material.shared_material_id", limit=160
        )
        if material_id in shared_ids:
            raise PresentationWorkbenchError(
                "presentation_shared_material_id_duplicate", "共同材料标识不得重复。"
            )
        shared_ids.add(material_id)
        material_asset_ids = _text_list(
            material["asset_ids"],
            f"{material_id}.asset_ids",
            minimum=0,
            maximum=10,
            item_limit=120,
        )
        missing_assets = sorted(set(material_asset_ids) - asset_id_set)
        if missing_assets:
            raise PresentationWorkbenchError(
                "presentation_asset_reference_missing",
                "共同材料引用了不存在的素材。",
                details={"shared_material_id": material_id, "missing_asset_ids": missing_assets},
            )
        shared_materials.append(
            {
                "shared_material_id": material_id,
                "title": _text(material["title"], f"{material_id}.title", limit=300),
                "summary": _text(material["summary"], f"{material_id}.summary", limit=1200),
                "source_page": _integer(
                    material["source_page"], f"{material_id}.source_page", minimum=1, maximum=9999
                ),
                "asset_ids": material_asset_ids,
                "source_ref": _text(
                    material["source_ref"], f"{material_id}.source_ref", limit=1200
                ),
            }
        )

    printed_questions: list[dict[str, Any]] = []
    printed_ids: set[str] = set()
    atomic_ids: set[str] = set()
    atomic_rows: list[dict[str, Any]] = []
    for question_index, row in enumerate(
        _list(theme["printed_questions"], "theme.printed_questions", minimum=1, maximum=12)
    ):
        question = _mapping(row, f"theme.printed_questions[{question_index}]")
        question_keys = {
            "printed_question_id",
            "display_number",
            "prompt",
            "source_page",
            "asset_ids",
            "shared_material_ids",
            "atomic_parts",
        }
        _exact_keys(
            question,
            question_keys,
            f"theme.printed_questions[{question_index}]",
            "presentation_question_contract_invalid",
        )
        printed_id = _text(
            question["printed_question_id"], "printed_question.printed_question_id", limit=160
        )
        if printed_id in printed_ids:
            raise PresentationWorkbenchError(
                "presentation_printed_question_id_duplicate", "印刷小题标识不得重复。"
            )
        printed_ids.add(printed_id)
        question_asset_ids = _text_list(
            question["asset_ids"],
            f"{printed_id}.asset_ids",
            minimum=0,
            maximum=12,
            item_limit=120,
        )
        question_shared_ids = _text_list(
            question["shared_material_ids"],
            f"{printed_id}.shared_material_ids",
            minimum=0,
            maximum=12,
            item_limit=160,
        )
        if set(question_asset_ids) - asset_id_set:
            raise PresentationWorkbenchError(
                "presentation_asset_reference_missing",
                "印刷小题引用了不存在的素材。",
                details={
                    "printed_question_id": printed_id,
                    "missing_asset_ids": sorted(set(question_asset_ids) - asset_id_set),
                },
            )
        if set(question_shared_ids) - shared_ids:
            raise PresentationWorkbenchError(
                "presentation_shared_material_reference_missing",
                "印刷小题引用了不存在的共同材料。",
                details={
                    "printed_question_id": printed_id,
                    "missing_shared_material_ids": sorted(set(question_shared_ids) - shared_ids),
                },
            )
        normalized_atomic: list[dict[str, Any]] = []
        for atomic_index, atomic_value in enumerate(
            _list(
                question["atomic_parts"],
                f"{printed_id}.atomic_parts",
                minimum=1,
                maximum=9,
            )
        ):
            atomic = _mapping(atomic_value, f"{printed_id}.atomic_parts[{atomic_index}]")
            atomic_keys = {
                "atomic_part_id",
                "label",
                "task",
                "response_mode",
                "answer_status",
                "answer_text",
                "answer_quality_note",
                "answer_authority",
                "dependencies",
                "chemical_expressions",
            }
            _exact_keys(
                atomic,
                atomic_keys,
                f"{printed_id}.atomic_parts[{atomic_index}]",
                "presentation_atomic_contract_invalid",
            )
            atomic_id = _text(atomic["atomic_part_id"], "atomic.atomic_part_id", limit=180)
            if atomic_id in atomic_ids:
                raise PresentationWorkbenchError(
                    "presentation_atomic_id_duplicate", "最小作答单元标识不得重复。"
                )
            atomic_ids.add(atomic_id)
            response_mode = _text(atomic["response_mode"], f"{atomic_id}.response_mode", limit=80)
            if response_mode not in ALLOWED_RESPONSE_MODES:
                raise PresentationWorkbenchError(
                    "presentation_response_mode_invalid",
                    "最小作答单元的作答形态不受支持。",
                    details={"atomic_part_id": atomic_id, "response_mode": response_mode},
                )
            answer_status = _text(atomic["answer_status"], f"{atomic_id}.answer_status", limit=80)
            if answer_status not in ALLOWED_ANSWER_STATUSES:
                raise PresentationWorkbenchError(
                    "presentation_answer_status_invalid",
                    "最小作答单元的答案状态不受支持。",
                    details={"atomic_part_id": atomic_id, "answer_status": answer_status},
                )
            answer_text = _optional_text(atomic["answer_text"], f"{atomic_id}.answer_text", limit=1600)
            if answer_status in {"blocked_pending_review", "absent"} and answer_text is not None:
                raise PresentationWorkbenchError(
                    "presentation_answer_boundary_invalid",
                    "阻断或缺失答案不得携带可见答案文本。",
                    details={"atomic_part_id": atomic_id},
                )
            if answer_status in {"nonofficial_reference", "teacher_candidate"} and answer_text is None:
                raise PresentationWorkbenchError(
                    "presentation_answer_boundary_invalid",
                    "候选答案状态必须携带答案文本。",
                    details={"atomic_part_id": atomic_id},
                )
            expressions: list[dict[str, str]] = []
            for expression_index, expression_value in enumerate(
                _list(
                    atomic["chemical_expressions"],
                    f"{atomic_id}.chemical_expressions",
                    minimum=0,
                    maximum=12,
                )
            ):
                expression = _mapping(
                    expression_value,
                    f"{atomic_id}.chemical_expressions[{expression_index}]",
                )
                _exact_keys(
                    expression,
                    {"display_text", "normalized_mhchem", "source_status"},
                    f"{atomic_id}.chemical_expressions[{expression_index}]",
                    "presentation_expression_contract_invalid",
                )
                expressions.append(
                    {
                        "display_text": _text(
                            expression["display_text"], "chemical_expression.display_text", limit=500
                        ),
                        "normalized_mhchem": _text(
                            expression["normalized_mhchem"],
                            "chemical_expression.normalized_mhchem",
                            limit=700,
                        ),
                        "source_status": _text(
                            expression["source_status"],
                            "chemical_expression.source_status",
                            limit=300,
                        ),
                    }
                )
            normalized_row = {
                "atomic_part_id": atomic_id,
                "label": _text(atomic["label"], f"{atomic_id}.label", limit=120),
                "task": _text(atomic["task"], f"{atomic_id}.task", limit=1200),
                "response_mode": response_mode,
                "answer_status": answer_status,
                "answer_text": answer_text,
                "answer_quality_note": _optional_text(
                    atomic["answer_quality_note"], f"{atomic_id}.answer_quality_note", limit=1200
                ),
                "answer_authority": _text(
                    atomic["answer_authority"], f"{atomic_id}.answer_authority", limit=500
                ),
                "dependencies": _text_list(
                    atomic["dependencies"],
                    f"{atomic_id}.dependencies",
                    minimum=0,
                    maximum=20,
                    item_limit=180,
                ),
                "chemical_expressions": expressions,
            }
            normalized_atomic.append(normalized_row)
            atomic_rows.append(normalized_row)
        printed_questions.append(
            {
                "printed_question_id": printed_id,
                "display_number": _text(
                    question["display_number"], f"{printed_id}.display_number", limit=120
                ),
                "prompt": _text(question["prompt"], f"{printed_id}.prompt", limit=2400),
                "source_page": _integer(
                    question["source_page"], f"{printed_id}.source_page", minimum=1, maximum=9999
                ),
                "asset_ids": question_asset_ids,
                "shared_material_ids": question_shared_ids,
                "atomic_parts": normalized_atomic,
            }
        )

    allowed_dependency_ids = atomic_ids | shared_ids
    for atomic in atomic_rows:
        missing = sorted(set(atomic["dependencies"]) - allowed_dependency_ids)
        if missing:
            raise PresentationWorkbenchError(
                "presentation_dependency_reference_missing",
                "最小作答单元引用了不存在的依赖。",
                details={"atomic_part_id": atomic["atomic_part_id"], "missing": missing},
            )

    diagnosis = _mapping(value["diagnosis"], "diagnosis")
    diagnosis_keys = {
        "label",
        "anonymized",
        "synthetic",
        "evidence_status",
        "summary",
        "common_errors",
        "counterevidence",
    }
    _exact_keys(
        diagnosis,
        diagnosis_keys,
        "diagnosis",
        "presentation_diagnosis_contract_invalid",
    )
    if not _boolean(diagnosis["anonymized"], "diagnosis.anonymized"):
        raise PresentationWorkbenchError(
            "presentation_diagnosis_not_anonymized", "学生诊断必须先完成匿名化。"
        )
    evidence_status = _text(
        diagnosis["evidence_status"], "diagnosis.evidence_status", limit=100
    )
    if evidence_status not in ALLOWED_DIAGNOSIS_STATUSES:
        raise PresentationWorkbenchError(
            "presentation_diagnosis_status_invalid", "学生诊断证据状态不受支持。"
        )
    synthetic = _boolean(diagnosis["synthetic"], "diagnosis.synthetic")
    if evidence_status == "synthetic_fixture_only" and not synthetic:
        raise PresentationWorkbenchError(
            "presentation_diagnosis_status_invalid", "合成fixture证据状态必须标记synthetic=true。"
        )
    common_errors: list[dict[str, Any]] = []
    error_ids: set[str] = set()
    for index, row in enumerate(
        _list(diagnosis["common_errors"], "diagnosis.common_errors", minimum=1, maximum=12)
    ):
        error = _mapping(row, f"diagnosis.common_errors[{index}]")
        error_keys = {
            "error_id",
            "description",
            "linked_atomic_part_ids",
            "cause_hypothesis",
            "confidence",
        }
        _exact_keys(
            error,
            error_keys,
            f"diagnosis.common_errors[{index}]",
            "presentation_diagnosis_contract_invalid",
        )
        error_id = _text(error["error_id"], "diagnosis.error_id", limit=160)
        if error_id in error_ids:
            raise PresentationWorkbenchError(
                "presentation_diagnosis_error_id_duplicate", "错因候选标识不得重复。"
            )
        error_ids.add(error_id)
        linked_ids = _text_list(
            error["linked_atomic_part_ids"],
            f"{error_id}.linked_atomic_part_ids",
            minimum=1,
            maximum=20,
            item_limit=180,
        )
        missing = sorted(set(linked_ids) - atomic_ids)
        if missing:
            raise PresentationWorkbenchError(
                "presentation_diagnosis_reference_missing",
                "匿名诊断引用了不存在的作答单元。",
                details={"error_id": error_id, "missing_atomic_part_ids": missing},
            )
        common_errors.append(
            {
                "error_id": error_id,
                "description": _text(error["description"], f"{error_id}.description", limit=800),
                "linked_atomic_part_ids": linked_ids,
                "cause_hypothesis": _text(
                    error["cause_hypothesis"], f"{error_id}.cause_hypothesis", limit=800
                ),
                "confidence": _confidence(error["confidence"], f"{error_id}.confidence"),
            }
        )

    classroom = _mapping(value["classroom_plan"], "classroom_plan")
    classroom_keys = {
        "teacher_questions",
        "anticipated_responses",
        "practice_atomic_part_ids",
        "homework",
    }
    _exact_keys(
        classroom,
        classroom_keys,
        "classroom_plan",
        "presentation_classroom_plan_contract_invalid",
    )
    teacher_questions = _text_list(
        classroom["teacher_questions"], "classroom_plan.teacher_questions", maximum=12
    )
    anticipated_responses = _text_list(
        classroom["anticipated_responses"],
        "classroom_plan.anticipated_responses",
        maximum=12,
    )
    if len(teacher_questions) != len(anticipated_responses):
        raise PresentationWorkbenchError(
            "presentation_classroom_plan_contract_invalid",
            "教师提问与预期回答必须逐项对应。",
        )
    practice_ids = _text_list(
        classroom["practice_atomic_part_ids"],
        "classroom_plan.practice_atomic_part_ids",
        minimum=1,
        maximum=12,
        item_limit=180,
    )
    if set(practice_ids) - atomic_ids:
        raise PresentationWorkbenchError(
            "presentation_practice_reference_missing",
            "课堂练习引用了不存在的作答单元。",
            details={"missing_atomic_part_ids": sorted(set(practice_ids) - atomic_ids)},
        )

    page_span = _list(theme["page_span"], "theme.page_span", minimum=2, maximum=2)
    if any(type(row) is not int or row < 1 for row in page_span) or page_span[1] < page_span[0]:
        raise PresentationWorkbenchError(
            "presentation_theme_page_span_invalid", "主题页码跨度格式不正确。"
        )

    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "lesson_title": _text(value["lesson_title"], "lesson_title", limit=300),
        "grade": _text(value["grade"], "grade", limit=200),
        "duration_minutes": _integer(
            value["duration_minutes"], "duration_minutes", minimum=30, maximum=120
        ),
        "candidate_use": {
            "intended_use": "local_personal_lesson_preparation_candidate",
            "publication_allowed": False,
            "teacher_confirmation_required": True,
        },
        "textbook": {
            "book_title": _text(textbook["book_title"], "textbook.book_title", limit=500),
            "volume": _text(textbook["volume"], "textbook.volume", limit=300),
            "chapter": _text(textbook["chapter"], "textbook.chapter", limit=300),
            "section": _text(textbook["section"], "textbook.section", limit=500),
            "publisher": _text(textbook["publisher"], "textbook.publisher", limit=300),
            "evidence_level": _text(
                textbook["evidence_level"], "textbook.evidence_level", limit=120
            ),
            "evidence_anchors": anchors,
            "notes": _text_list(textbook["notes"], "textbook.notes", maximum=12),
        },
        "lesson_goals": normalized_goals,
        "theme": {
            "paper": normalized_paper,
            "theme_id": _text(theme["theme_id"], "theme.theme_id", limit=180),
            "title": _text(theme["title"], "theme.title", limit=500),
            "order": _integer(theme["order"], "theme.order", minimum=1, maximum=99),
            "page_span": [int(row) for row in page_span],
            "context_summary": _text(
                theme["context_summary"], "theme.context_summary", limit=1800
            ),
            "source_authority": _text(
                theme["source_authority"], "theme.source_authority", limit=500
            ),
            "answer_authority": _text(
                theme["answer_authority"], "theme.answer_authority", limit=500
            ),
            "human_reviewed": False,
            "publication_allowed": False,
            "assets": assets,
            "shared_materials": shared_materials,
            "printed_questions": printed_questions,
        },
        "diagnosis": {
            "label": _text(diagnosis["label"], "diagnosis.label", limit=300),
            "anonymized": True,
            "synthetic": synthetic,
            "evidence_status": evidence_status,
            "summary": _text(diagnosis["summary"], "diagnosis.summary", limit=1200),
            "common_errors": common_errors,
            "counterevidence": _text_list(
                diagnosis["counterevidence"], "diagnosis.counterevidence", maximum=12
            ),
        },
        "classroom_plan": {
            "teacher_questions": teacher_questions,
            "anticipated_responses": anticipated_responses,
            "practice_atomic_part_ids": practice_ids,
            "homework": _text_list(classroom["homework"], "classroom_plan.homework", maximum=10),
        },
    }


def _source_ref(label: str, detail: str) -> dict[str, str]:
    return {"label": label, "detail": detail}


def _speaker_notes(body: str, source_refs: Sequence[Mapping[str, str]]) -> str:
    lines = [body.strip(), "", "[Sources]"]
    lines.extend(f"- {row['label']}: {row['detail']}" for row in source_refs)
    lines.append("[/Sources]")
    return "\n".join(lines)


def _chunks(rows: Sequence[Any], size: int) -> list[list[Any]]:
    return [list(rows[index : index + size]) for index in range(0, len(rows), size)]


def _allocate_minutes(total: int, weights: Sequence[int]) -> list[int]:
    if total < len(weights):
        raise PresentationWorkbenchError(
            "presentation_duration_too_short",
            "课时分钟数不足以保证每页至少一分钟。",
            details={"minutes": total, "slide_count": len(weights)},
        )
    remaining = total - len(weights)
    weight_total = sum(weights)
    raw = [remaining * weight / weight_total for weight in weights]
    allocations = [1 + int(value) for value in raw]
    missing = total - sum(allocations)
    order = sorted(
        range(len(weights)),
        key=lambda index: (raw[index] - int(raw[index]), weights[index], -index),
        reverse=True,
    )
    for index in order[:missing]:
        allocations[index] += 1
    return allocations


def _project_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **deepcopy(dict(asset)),
        "content_type": _asset_content_type(str(asset["path"])),
        "render_fit": "contain_preserve_aspect",
    }


def compose_deck_json(value: Mapping[str, Any], *, asset_root: Path) -> dict[str, Any]:
    validated = validate_presentation_input(value, asset_root=asset_root)
    textbook = validated["textbook"]
    goals = validated["lesson_goals"]
    theme = validated["theme"]
    diagnosis = validated["diagnosis"]
    classroom = validated["classroom_plan"]
    asset_by_id = {row["asset_id"]: row for row in theme["assets"]}
    shared_by_id = {row["shared_material_id"]: row for row in theme["shared_materials"]}
    atomic_by_id: dict[str, dict[str, Any]] = {}
    question_by_atomic_id: dict[str, dict[str, Any]] = {}
    for question in theme["printed_questions"]:
        for atomic in question["atomic_parts"]:
            atomic_by_id[atomic["atomic_part_id"]] = atomic
            question_by_atomic_id[atomic["atomic_part_id"]] = question

    first_anchor = textbook["evidence_anchors"][0]
    textbook_ref = _source_ref(
        "教材章节",
        f"{textbook['book_title']}｜{textbook['chapter']}｜{textbook['section']}｜{first_anchor['source_ref']}",
    )
    paper_ref = _source_ref("主题来源", theme["paper"]["source_ref"])
    diagnosis_ref = _source_ref(
        "匿名诊断摘要",
        f"{diagnosis['evidence_status']}；{diagnosis['summary']}",
    )

    slides: list[dict[str, Any]] = []
    weights: list[int] = []

    def assets_for(ids: Sequence[str]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for asset_id in ids:
            if asset_id not in seen:
                result.append(_project_asset(asset_by_id[asset_id]))
                seen.add(asset_id)
        return result

    def add_slide(
        *,
        slide_type: str,
        render_layout: str,
        title: str,
        learning_purpose: str,
        student_thinking_action: str,
        reveal_stage: str,
        content_blocks: list[dict[str, Any]],
        source_refs: Sequence[Mapping[str, str]],
        notes: str,
        weight: int,
        assets: Sequence[Mapping[str, Any]] = (),
        chemical_expressions: Sequence[Mapping[str, Any]] = (),
        teacher_questions: Sequence[str] = (),
        anticipated_responses: Sequence[str] = (),
        misconceptions: Sequence[str] = (),
        theme_context: Mapping[str, Any] | None = None,
    ) -> None:
        number = len(slides) + 1
        context = {
            "theme_id": theme["theme_id"],
            "printed_question_id": None,
            "atomic_part_ids": [],
            "shared_material_ids": [],
            **dict(theme_context or {}),
        }
        slide_without_id = {
            "slide_number": number,
            "slide_type": slide_type,
            "render_layout": render_layout,
            "title": title,
            "learning_purpose": learning_purpose,
            "student_thinking_action": student_thinking_action,
            "reveal_stage": reveal_stage,
            "content_blocks": deepcopy(content_blocks),
            "chemical_expressions": deepcopy(list(chemical_expressions)),
            "assets": deepcopy(list(assets)),
            "speaker_notes": _speaker_notes(notes, source_refs),
            "teacher_questions": list(teacher_questions),
            "anticipated_responses": list(anticipated_responses),
            "misconceptions": list(misconceptions),
            "source_refs": deepcopy(list(source_refs)),
            "estimated_minutes": 0,
            "theme_context": context,
        }
        slide_id = "SLIDE-" + _sha256_bytes(_canonical_json_bytes(slide_without_id))[:20]
        slides.append({"slide_id": slide_id, **slide_without_id})
        weights.append(weight)

    add_slide(
        slide_type="cover",
        render_layout="cover",
        title=validated["lesson_title"],
        learning_purpose="从教材证据进入完整主题任务。",
        student_thinking_action="带着一个核心问题进入课时：哪些结论能回到材料与教材证据？",
        reveal_stage="launch",
        content_blocks=[
            {
                "block_type": "hero_text",
                "heading": theme["title"],
                "body": f"{validated['grade']}｜{textbook['chapter']}｜{textbook['section']}",
            }
        ],
        source_refs=[textbook_ref, paper_ref],
        notes="开场只交代课时主线，不提前揭示题目答案。",
        weight=1,
    )
    add_slide(
        slide_type="objectives",
        render_layout="two_column",
        title="这节课要完成三类学习产出",
        learning_purpose="明确知识、证据和表达三个层面的目标。",
        student_thinking_action="用自己的话圈出最需要提升的一项目标。",
        reveal_stage="launch",
        content_blocks=[
            {
                "block_type": "bullet_list",
                "heading": "学习目标",
                "items": goals["learning_objectives"],
            },
            {
                "block_type": "bullet_list",
                "heading": "重点与难点",
                "items": goals["key_points"] + goals["difficult_points"],
            },
        ],
        source_refs=[textbook_ref, paper_ref],
        notes="目标页用于建立学习合同；具体完成度仍由教师课堂观察确认。",
        weight=2,
    )
    add_slide(
        slide_type="knowledge_thread",
        render_layout="sequence",
        title="知识脉络从已有概念走向主题解决",
        learning_purpose="把前置知识、核心关系与迁移任务连成学习路径。",
        student_thinking_action="指出链条中最容易跳步的位置，并说明需要什么证据。",
        reveal_stage="scaffold",
        content_blocks=[
            {
                "block_type": "sequence",
                "items": [
                    {"label": "已有基础", "body": "；".join(goals["prerequisites"])},
                    {"label": "核心关系", "body": "；".join(goals["key_points"])},
                    {"label": "迁移表达", "body": goals["lesson_emphasis"]},
                ],
            }
        ],
        source_refs=[textbook_ref, paper_ref],
        notes="知识脉络只说明课堂推进关系，不把题型提升为独立卷面板块。",
        weight=2,
    )

    for anchor_chunk_index, anchor_chunk in enumerate(_chunks(textbook["evidence_anchors"], 4), start=1):
        refs = [
            _source_ref(
                f"教材证据 {row['anchor_id']}",
                f"{row['source_ref']}｜文件SHA-256 {row['source_sha256']}",
            )
            for row in anchor_chunk
        ]
        add_slide(
            slide_type="textbook_evidence",
            render_layout="evidence",
            title=(
                "教材证据把概念落到可核对页面"
                if anchor_chunk_index == 1
                else f"教材证据续页 {anchor_chunk_index}"
            ),
            learning_purpose="将本课概念与页级教材证据绑定。",
            student_thinking_action="逐条说明教材证据将支持哪一步推理。",
            reveal_stage="scaffold",
            content_blocks=[
                {
                    "block_type": "evidence_list",
                    "items": [
                        {
                            "label": f"P{row['page_number']}｜{row['anchor_id']}",
                            "body": row["evidence_text"],
                        }
                        for row in anchor_chunk
                    ],
                }
            ],
            source_refs=refs,
            notes="教材页只支持其明确内容；缺少版本或印次证据时不扩大解释范围。",
            weight=2,
        )

    for material in theme["shared_materials"]:
        material_assets = assets_for(material["asset_ids"])
        refs = [paper_ref, _source_ref(material["title"], material["source_ref"])]
        refs.extend(_source_ref(asset["asset_id"], asset["source_ref"]) for asset in material_assets)
        add_slide(
            slide_type="theme_shared_material",
            render_layout="material",
            title=f"共同材料｜{material['title']}",
            learning_purpose="先读完整共同材料，再进入主题内具体作答。",
            student_thinking_action="标出后续题目会反复调用的条件、数据或图像关系。",
            reveal_stage="prompt",
            content_blocks=[
                {
                    "block_type": "shared_material",
                    "heading": material["title"],
                    "body": material["summary"],
                }
            ],
            source_refs=refs,
            notes="共同材料只展示一次；后续小题通过shared_material_ids保留依赖。",
            weight=2,
            assets=material_assets,
            theme_context={"shared_material_ids": [material["shared_material_id"]]},
        )

    export_warnings: list[str] = []
    for question in theme["printed_questions"]:
        inherited_asset_ids: list[str] = list(question["asset_ids"])
        for shared_id in question["shared_material_ids"]:
            inherited_asset_ids.extend(shared_by_id[shared_id]["asset_ids"])
        question_assets = assets_for(inherited_asset_ids)
        refs: list[dict[str, str]] = [
            paper_ref,
            _source_ref(
                f"题号 {question['display_number']}",
                f"第{question['source_page']}页｜{question['prompt']}",
            ),
        ]
        refs.extend(
            _source_ref(shared_by_id[row]["title"], shared_by_id[row]["source_ref"])
            for row in question["shared_material_ids"]
        )
        refs.extend(_source_ref(asset["asset_id"], asset["source_ref"]) for asset in question_assets)
        atomic_chunks = _chunks(question["atomic_parts"], 3)
        for chunk_index, atomic_chunk in enumerate(atomic_chunks, start=1):
            suffix = f"（{chunk_index}/{len(atomic_chunks)}）" if len(atomic_chunks) > 1 else ""
            context = {
                "printed_question_id": question["printed_question_id"],
                "atomic_part_ids": [row["atomic_part_id"] for row in atomic_chunk],
                "shared_material_ids": list(question["shared_material_ids"]),
            }
            add_slide(
                slide_type="question_prompt",
                render_layout="question_prompt",
                title=f"题号 {question['display_number']}｜先独立作答{suffix}",
                learning_purpose="保留题面层级，在揭示前形成独立证据链。",
                student_thinking_action="先写证据或中间关系，再完成最终作答。",
                reveal_stage="prompt",
                content_blocks=[
                    {"block_type": "prompt", "heading": "题面", "body": question["prompt"]},
                    {
                        "block_type": "atomic_tasks",
                        "items": [
                            {
                                "label": row["label"],
                                "task": row["task"],
                                "response_mode": row["response_mode"],
                            }
                            for row in atomic_chunk
                        ],
                    },
                ],
                source_refs=refs,
                notes="先完整呈现题面和作答单元，不在本页显示候选答案。",
                weight=3,
                assets=question_assets,
                teacher_questions=[
                    f"{row['label']}首先需要调用哪一条材料或前序结论？" for row in atomic_chunk
                ],
                theme_context=context,
            )
            answers: list[dict[str, str]] = []
            expressions: list[dict[str, Any]] = []
            misconceptions: list[str] = []
            for row in atomic_chunk:
                if row["answer_status"] in {"nonofficial_reference", "teacher_candidate"}:
                    visible_answer = str(row["answer_text"])
                else:
                    visible_answer = "当前材料不足以形成可靠结论，课堂只保留作答路径。"
                    export_warnings.append(
                        f"{row['atomic_part_id']}答案状态为{row['answer_status']}，教师讲评前必须补充或确认。"
                    )
                answers.append(
                    {"label": row["label"], "task": row["task"], "answer": visible_answer}
                )
                expressions.extend(row["chemical_expressions"])
                if row["answer_quality_note"]:
                    misconceptions.append(str(row["answer_quality_note"]))
            add_slide(
                slide_type="question_progression",
                render_layout="question_reveal",
                title=f"题号 {question['display_number']}｜讲评证据链{suffix}",
                learning_purpose="把作答结论还原为材料、关系与表达三步。",
                student_thinking_action="对照自己的中间步骤，补上缺失的证据或关系。",
                reveal_stage="reveal",
                content_blocks=[
                    {"block_type": "answer_items", "items": answers},
                    {
                        "block_type": "method",
                        "heading": "讲评方法",
                        "body": "材料证据 → 化学或数量关系 → 规范表达",
                    },
                ],
                source_refs=refs,
                notes="本页只显示输入中已有的候选答案；答案权威性和化学正确性仍由教师复核。",
                weight=3,
                assets=question_assets,
                chemical_expressions=expressions,
                misconceptions=misconceptions,
                theme_context=context,
            )

    for error_chunk_index, error_chunk in enumerate(_chunks(diagnosis["common_errors"], 3), start=1):
        add_slide(
            slide_type="student_error_causes",
            render_layout="error_analysis",
            title=(
                "错因不是答案错误，而是推理链断点"
                if error_chunk_index == 1
                else f"错因续页 {error_chunk_index}"
            ),
            learning_purpose="把匿名错误表现与可修复的原因假设分开。",
            student_thinking_action="为每类错误写一个可观察、可纠正的下一步。",
            reveal_stage="scaffold",
            content_blocks=[
                {
                    "block_type": "error_items",
                    "items": [
                        {
                            "label": f"表现 {index + 1}",
                            "description": row["description"],
                            "cause_hypothesis": row["cause_hypothesis"],
                        }
                        for index, row in enumerate(error_chunk)
                    ],
                }
            ],
            source_refs=[diagnosis_ref, paper_ref],
            notes="错因均为匿名诊断候选，不解释为稳定个体结论；保留反证。",
            weight=2,
            misconceptions=[row["description"] for row in error_chunk],
            theme_context={
                "atomic_part_ids": sorted(
                    {atomic_id for row in error_chunk for atomic_id in row["linked_atomic_part_ids"]}
                )
            },
        )

    for interaction_index, pairs in enumerate(
        _chunks(
            list(zip(classroom["teacher_questions"], classroom["anticipated_responses"], strict=True)),
            3,
        ),
        start=1,
    ):
        add_slide(
            slide_type="classroom_interaction",
            render_layout="interaction",
            title=(
                "课堂互动把证据说清楚"
                if interaction_index == 1
                else f"课堂互动续页 {interaction_index}"
            ),
            learning_purpose="通过提问、等待与追问显化推理。",
            student_thinking_action="先独立组织30秒，再与同伴互证。",
            reveal_stage="transfer",
            content_blocks=[
                {
                    "block_type": "qa_pairs",
                    "items": [
                        {"label": f"问题 {index + 1}", "question": q, "response": a}
                        for index, (q, a) in enumerate(pairs)
                    ],
                }
            ],
            source_refs=[paper_ref, diagnosis_ref],
            notes="预期回答只作教师追问参考，不替代学生现场表达。",
            weight=2,
            teacher_questions=[row[0] for row in pairs],
            anticipated_responses=[row[1] for row in pairs],
        )

    practice_rows = [atomic_by_id[row] for row in classroom["practice_atomic_part_ids"]]
    for practice_index, practice_chunk in enumerate(_chunks(practice_rows, 3), start=1):
        practice_asset_ids: list[str] = []
        practice_refs: list[dict[str, str]] = [paper_ref]
        for atomic in practice_chunk:
            question = question_by_atomic_id[atomic["atomic_part_id"]]
            practice_asset_ids.extend(question["asset_ids"])
            for shared_id in question["shared_material_ids"]:
                practice_asset_ids.extend(shared_by_id[shared_id]["asset_ids"])
        add_slide(
            slide_type="practice",
            render_layout="practice",
            title=(
                "分层练习｜先迁移方法再核对结论"
                if practice_index == 1
                else f"分层练习续页 {practice_index}"
            ),
            learning_purpose="在同一主题内迁移刚建立的方法。",
            student_thinking_action="独立完成，并在答案旁写出证据来源。",
            reveal_stage="prompt",
            content_blocks=[
                {
                    "block_type": "atomic_tasks",
                    "items": [
                        {
                            "label": row["label"],
                            "task": row["task"],
                            "response_mode": row["response_mode"],
                        }
                        for row in practice_chunk
                    ],
                }
            ],
            source_refs=practice_refs,
            notes="练习页不显示答案；如素材含裁片，保持原始纵横比。",
            weight=2,
            assets=assets_for(practice_asset_ids),
            theme_context={
                "atomic_part_ids": [row["atomic_part_id"] for row in practice_chunk]
            },
        )
        review_expressions = [
            expression
            for row in practice_chunk
            for expression in row["chemical_expressions"]
        ]
        add_slide(
            slide_type="answer_review",
            render_layout="answer_review",
            title=(
                "答案讲评｜看结论，也看中间关系"
                if practice_index == 1
                else f"答案讲评续页 {practice_index}"
            ),
            learning_purpose="核对候选结论并保留答案证据边界。",
            student_thinking_action="用另一种颜色补写自己缺失的中间步骤。",
            reveal_stage="reveal",
            content_blocks=[
                {
                    "block_type": "answer_items",
                    "items": [
                        {
                            "label": row["label"],
                            "task": row["task"],
                            "answer": (
                                row["answer_text"]
                                if row["answer_status"] in {"nonofficial_reference", "teacher_candidate"}
                                else "当前材料不足以形成可靠结论，课堂只保留作答路径。"
                            ),
                        }
                        for row in practice_chunk
                    ],
                }
            ],
            source_refs=practice_refs,
            notes="答案讲评仍是教师审核前候选；只有真实官方文件中的内容才可称官方采分点。",
            weight=2,
            chemical_expressions=review_expressions,
            misconceptions=[
                row["answer_quality_note"]
                for row in practice_chunk
                if row["answer_quality_note"]
            ],
            theme_context={
                "atomic_part_ids": [row["atomic_part_id"] for row in practice_chunk]
            },
        )

    add_slide(
        slide_type="homework",
        render_layout="homework",
        title="作业把课堂方法迁移到新任务",
        learning_purpose="用分层任务完成课后巩固与迁移。",
        student_thinking_action="选择起点，写清证据、关系与结论。",
        reveal_stage="transfer",
        content_blocks=[
            {"block_type": "homework", "heading": "课后任务", "items": classroom["homework"]},
            {
                "block_type": "reflection",
                "heading": "自检",
                "body": "我能否把每个结论指回教材页、共同材料或题面证据？",
            },
        ],
        source_refs=[textbook_ref, paper_ref],
        notes="作业发布前由教师确认题量、答案边界与适配班级。",
        weight=2,
    )

    allocations = _allocate_minutes(validated["duration_minutes"], weights)
    for slide, minutes in zip(slides, allocations, strict=True):
        slide["estimated_minutes"] = minutes

    printed_ids_in_order = [row["printed_question_id"] for row in theme["printed_questions"]]
    atomic_ids_in_order = [
        atomic["atomic_part_id"]
        for question in theme["printed_questions"]
        for atomic in question["atomic_parts"]
    ]
    deck_without_id = {
        "schema_version": DECK_SCHEMA_VERSION,
        "input_digest": _sha256_bytes(_canonical_json_bytes(validated)),
        "lesson_title": validated["lesson_title"],
        "grade": validated["grade"],
        "total_minutes": validated["duration_minutes"],
        "slide_size": {"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT, "aspect_ratio": "16:9"},
        "style": {
            "visual_route": "codex_grid_composition_reference",
            "palette": "codex_grid_blue_grayscale_print_safe",
            "canvas": "#FFFFFF",
            "ink": "#000000",
            "panel": "#EDEDED",
            "rule": "#B8BCC4",
            "accent": "#6DCBF4",
            "accent_strong": "#3D8DFF",
            "deck_title_font_size": MIN_DECK_TITLE_PT,
            "slide_title_font_size": MIN_SLIDE_TITLE_PT,
            "subheading_font_size": MIN_SUBHEADING_PT,
            "body_font_size": MIN_BODY_PT,
            "black_white_print_supported": True,
        },
        "candidate_boundary": {
            "intended_use": "local_personal_lesson_preparation_candidate",
            "publication_allowed": False,
            "teacher_confirmation_required": True,
            "chemistry_review": "pending_teacher_review",
            "student_diagnosis_anonymized": True,
            "student_diagnosis_synthetic": diagnosis["synthetic"],
        },
        "textbook_binding": deepcopy(textbook),
        "theme_binding": {
            "paper_id": theme["paper"]["paper_id"],
            "theme_id": theme["theme_id"],
            "theme_title": theme["title"],
            "theme_order": theme["order"],
            "page_span": theme["page_span"],
            "shared_material_ids_in_order": [
                row["shared_material_id"] for row in theme["shared_materials"]
            ],
            "printed_question_ids_in_order": printed_ids_in_order,
            "atomic_part_ids_in_order": atomic_ids_in_order,
            "asset_ids_in_order": [row["asset_id"] for row in theme["assets"]],
            "printed_question_count": len(printed_ids_in_order),
            "atomic_part_count": len(atomic_ids_in_order),
            "human_reviewed": False,
            "publication_allowed": False,
        },
        "diagnosis_binding": {
            "evidence_status": diagnosis["evidence_status"],
            "anonymized": True,
            "synthetic": diagnosis["synthetic"],
            "common_error_count": len(diagnosis["common_errors"]),
            "counterevidence": diagnosis["counterevidence"],
        },
        "lesson_goals": deepcopy(goals),
        "lesson_flow": [
            {
                "slide_number": row["slide_number"],
                "slide_id": row["slide_id"],
                "slide_type": row["slide_type"],
                "learning_purpose": row["learning_purpose"],
                "estimated_minutes": row["estimated_minutes"],
            }
            for row in slides
        ],
        "slides": slides,
        "homework": deepcopy(classroom["homework"]),
        "export_warnings": sorted(
            set(
                export_warnings
                + [
                    "PPTX仅为本地个人备课候选，教师确认前不得发布。",
                    "化学内容、答案与讲评边界仍为pending_teacher_review。",
                ]
            )
        ),
        "render_requirements": {
            "renderer": "@oai/artifact-tool",
            "editable_pptx_required": True,
            "render_every_slide_to_png": True,
            "export_layout_json_per_slide": True,
            "check_overflow": True,
            "inspect_overlap_and_readability_per_slide": True,
            "manual_visual_review_cannot_unlock_chemistry_or_publication": True,
        },
    }
    deck_id = "PRES-" + _sha256_bytes(_canonical_json_bytes(deck_without_id))[:24]
    return validate_deck_json({"deck_id": deck_id, **deck_without_id})


def validate_deck_json(value: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(value, "deck")
    deck_keys = {
        "deck_id",
        "schema_version",
        "input_digest",
        "lesson_title",
        "grade",
        "total_minutes",
        "slide_size",
        "style",
        "candidate_boundary",
        "textbook_binding",
        "theme_binding",
        "diagnosis_binding",
        "lesson_goals",
        "lesson_flow",
        "slides",
        "homework",
        "export_warnings",
        "render_requirements",
    }
    _exact_keys(value, deck_keys, "deck", "presentation_deck_contract_invalid")
    if value["schema_version"] != DECK_SCHEMA_VERSION:
        raise PresentationWorkbenchError(
            "presentation_deck_schema_unsupported", "Deck JSON版本不受支持。"
        )
    deck_id = _text(value["deck_id"], "deck.deck_id", limit=80)
    if not re.fullmatch(r"PRES-[0-9a-f]{24}", deck_id):
        raise PresentationWorkbenchError(
            "presentation_deck_id_invalid", "Deck标识格式不正确。"
        )
    digest = _text(value["input_digest"], "deck.input_digest", limit=64)
    if not _SHA256.fullmatch(digest):
        raise PresentationWorkbenchError(
            "presentation_deck_digest_invalid", "Deck输入摘要格式不正确。"
        )
    if value["slide_size"] != {
        "width": SLIDE_WIDTH,
        "height": SLIDE_HEIGHT,
        "aspect_ratio": "16:9",
    }:
        raise PresentationWorkbenchError(
            "presentation_deck_size_invalid", "课程PPT必须使用1280×720的16:9画布。"
        )
    style = _mapping(value["style"], "deck.style")
    if (
        style.get("deck_title_font_size", 0) < MIN_DECK_TITLE_PT
        or style.get("slide_title_font_size", 0) < MIN_SLIDE_TITLE_PT
        or style.get("subheading_font_size", 0) < MIN_SUBHEADING_PT
        or style.get("body_font_size", 0) < MIN_BODY_PT
        or style.get("black_white_print_supported") is not True
    ):
        raise PresentationWorkbenchError(
            "presentation_deck_typography_invalid", "课程PPT字号或打印样式低于最低要求。"
        )
    boundary = _mapping(value["candidate_boundary"], "deck.candidate_boundary")
    if (
        boundary.get("publication_allowed") is not False
        or boundary.get("teacher_confirmation_required") is not True
        or boundary.get("chemistry_review") != "pending_teacher_review"
        or boundary.get("student_diagnosis_anonymized") is not True
    ):
        raise PresentationWorkbenchError(
            "presentation_candidate_boundary_invalid", "Deck候选边界不正确。"
        )
    unresolved = _find_unresolved_markers(value)
    if unresolved:
        raise PresentationWorkbenchError(
            "presentation_deck_unresolved",
            "Deck JSON仍含未解析占位标记。",
            details={"markers": unresolved},
        )
    slides = _list(value["slides"], "deck.slides", minimum=12, maximum=80)
    expected_numbers = list(range(1, len(slides) + 1))
    observed_numbers: list[int] = []
    slide_ids: list[str] = []
    slide_types: list[str] = []
    minutes = 0
    slide_keys = {
        "slide_id",
        "slide_number",
        "slide_type",
        "render_layout",
        "title",
        "learning_purpose",
        "student_thinking_action",
        "reveal_stage",
        "content_blocks",
        "chemical_expressions",
        "assets",
        "speaker_notes",
        "teacher_questions",
        "anticipated_responses",
        "misconceptions",
        "source_refs",
        "estimated_minutes",
        "theme_context",
    }
    for index, row in enumerate(slides):
        slide = _mapping(row, f"deck.slides[{index}]")
        _exact_keys(
            slide,
            slide_keys,
            f"deck.slides[{index}]",
            "presentation_slide_contract_invalid",
        )
        number = _integer(
            slide["slide_number"], f"slide[{index}].slide_number", minimum=1, maximum=80
        )
        observed_numbers.append(number)
        slide_id = _text(slide["slide_id"], f"slide[{index}].slide_id", limit=80)
        if not re.fullmatch(r"SLIDE-[0-9a-f]{20}", slide_id):
            raise PresentationWorkbenchError(
                "presentation_slide_id_invalid", "页面标识格式不正确。"
            )
        slide_ids.append(slide_id)
        slide_type = _text(slide["slide_type"], f"slide[{index}].slide_type", limit=80)
        slide_types.append(slide_type)
        _text(slide["render_layout"], f"slide[{index}].render_layout", limit=80)
        _text(slide["title"], f"slide[{index}].title", limit=500)
        _text(slide["learning_purpose"], f"slide[{index}].learning_purpose", limit=800)
        _text(
            slide["student_thinking_action"],
            f"slide[{index}].student_thinking_action",
            limit=800,
        )
        if slide["reveal_stage"] not in ALLOWED_REVEAL_STAGES:
            raise PresentationWorkbenchError(
                "presentation_reveal_stage_invalid", "页面揭示阶段不受支持。"
            )
        _list(slide["content_blocks"], f"slide[{index}].content_blocks", minimum=1, maximum=12)
        _list(
            slide["chemical_expressions"],
            f"slide[{index}].chemical_expressions",
            minimum=0,
            maximum=30,
        )
        for asset_value in _list(
            slide["assets"], f"slide[{index}].assets", minimum=0, maximum=30
        ):
            asset = _mapping(asset_value, f"slide[{index}].asset")
            if (
                asset.get("render_fit") != "contain_preserve_aspect"
                or asset.get("publication_allowed") is not False
                or asset.get("content_type") not in {"image/png", "image/jpeg"}
                or not _SHA256.fullmatch(str(asset.get("sha256", "")))
            ):
                raise PresentationWorkbenchError(
                    "presentation_deck_asset_invalid", "Deck中的素材投影不完整。"
                )
        notes = _text(slide["speaker_notes"], f"slide[{index}].speaker_notes", limit=20000)
        if "[Sources]" not in notes or "[/Sources]" not in notes:
            raise PresentationWorkbenchError(
                "presentation_deck_source_notes_missing", "每页备注必须包含[Sources]来源块。"
            )
        _text_list(
            slide["teacher_questions"],
            f"slide[{index}].teacher_questions",
            minimum=0,
            maximum=20,
            item_limit=1200,
        )
        _text_list(
            slide["anticipated_responses"],
            f"slide[{index}].anticipated_responses",
            minimum=0,
            maximum=20,
            item_limit=1200,
        )
        _text_list(
            slide["misconceptions"],
            f"slide[{index}].misconceptions",
            minimum=0,
            maximum=20,
            item_limit=1600,
        )
        source_refs = _list(
            slide["source_refs"], f"slide[{index}].source_refs", minimum=1, maximum=30
        )
        for ref in source_refs:
            source = _mapping(ref, f"slide[{index}].source_ref")
            _exact_keys(
                source,
                {"label", "detail"},
                f"slide[{index}].source_ref",
                "presentation_deck_source_ref_invalid",
            )
            _text(source["label"], "source_ref.label", limit=300)
            _text(source["detail"], "source_ref.detail", limit=3000)
        minutes += _integer(
            slide["estimated_minutes"],
            f"slide[{index}].estimated_minutes",
            minimum=1,
            maximum=30,
        )
        visible = json.dumps(
            {
                "title": slide["title"],
                "learning_purpose": slide["learning_purpose"],
                "student_thinking_action": slide["student_thinking_action"],
                "content_blocks": slide["content_blocks"],
            },
            ensure_ascii=False,
        )
        forbidden = [term for term in _FORBIDDEN_VISIBLE_GOVERNANCE_TERMS if term in visible]
        if forbidden:
            raise PresentationWorkbenchError(
                "presentation_visible_governance_leak",
                "学生可见页面泄露了内部治理术语。",
                details={"slide_number": number, "terms": forbidden},
            )
    if observed_numbers != expected_numbers:
        raise PresentationWorkbenchError(
            "presentation_slide_number_invalid", "页面编号必须从1开始连续递增。"
        )
    if len(set(slide_ids)) != len(slide_ids):
        raise PresentationWorkbenchError(
            "presentation_slide_id_duplicate", "页面标识不得重复。"
        )
    missing_types = sorted(set(MANDATORY_SLIDE_TYPES) - set(slide_types))
    if missing_types:
        raise PresentationWorkbenchError(
            "presentation_required_slides_missing",
            "课程PPT缺少必要教学环节。",
            details={"missing_slide_types": missing_types},
        )
    total_minutes = _integer(
        value["total_minutes"], "deck.total_minutes", minimum=30, maximum=120
    )
    if minutes != total_minutes:
        raise PresentationWorkbenchError(
            "presentation_deck_timing_invalid",
            "页面预计分钟数之和必须等于课时总分钟数。",
            details={"expected": total_minutes, "observed": minutes},
        )
    flow = _list(value["lesson_flow"], "deck.lesson_flow", minimum=len(slides), maximum=len(slides))
    if [row.get("slide_id") for row in flow if isinstance(row, Mapping)] != slide_ids:
        raise PresentationWorkbenchError(
            "presentation_lesson_flow_invalid", "课时流程与页面标识未一一对应。"
        )
    return deepcopy(dict(value))


def _resolved_render_deck(deck: Mapping[str, Any], *, asset_root: Path) -> dict[str, Any]:
    result = deepcopy(dict(deck))
    for slide in result["slides"]:
        for asset in slide["assets"]:
            resolved = _resolve_asset_path(asset_root, asset["path"])
            if not resolved.is_file() or _sha256_file(resolved) != asset["sha256"]:
                raise PresentationWorkbenchError(
                    "presentation_asset_hash_drift", "渲染前素材哈希复核失败。"
                )
            asset["resolved_path"] = str(resolved)
    return result


def _builder_source() -> str:
    return r'''
import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const [deckPath, pptxPath, previewDir] = process.argv.slice(2);
const deck = JSON.parse(await fs.readFile(deckPath, "utf8"));
await fs.mkdir(previewDir, { recursive: true });

const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const C = { canvas: "#FFFFFF", ink: "#000000", panel: "#EDEDED", rule: "#B8BCC4", accent: "#6DCBF4", accentStrong: "#3D8DFF", muted: "#4B5563" };

function box(slide, name, geometry, position, fill = "none", lineFill = "none", lineWidth = 0) {
  return slide.shapes.add({ geometry, name, position, fill, line: { style: "solid", fill: lineFill, width: lineWidth } });
}

function textBox(slide, name, text, position, fontSize = 22, options = {}) {
  const shape = box(slide, name, "textbox", position, "none", "none", 0);
  shape.text = String(text ?? "");
  shape.text.style = {
    fontSize,
    fontFamily: "Microsoft YaHei",
    bold: Boolean(options.bold),
    color: options.color || C.ink,
    alignment: options.alignment || "left",
    verticalAlignment: options.verticalAlignment || "top",
  };
  return shape;
}

function header(slide, data) {
  textBox(slide, "slide-title", data.title, { left: 64, top: 42, width: 1120, height: 66 }, 40, { bold: true });
  box(slide, "title-rule", "rect", { left: 64, top: 121, width: 1152, height: 2 }, C.rule, C.rule, 0);
  textBox(slide, "slide-number", String(data.slide_number).padStart(2, "0"), { left: 1164, top: 666, width: 52, height: 22 }, 14, { alignment: "right", color: C.muted });
}

function itemText(item) {
  if (typeof item === "string") return item;
  if (!item || typeof item !== "object") return String(item ?? "");
  const lead = item.label || item.title || item.heading || "";
  const parts = [item.body, item.task, item.description, item.cause_hypothesis, item.question, item.response, item.answer, item.evidence_text].filter(Boolean);
  return [lead, ...parts].filter(Boolean).join("｜");
}

function blockLines(block) {
  const lines = [];
  if (block.heading) lines.push(String(block.heading));
  if (block.body) lines.push(String(block.body));
  if (Array.isArray(block.items)) lines.push(...block.items.map(itemText));
  return lines;
}

function allLines(data) {
  return data.content_blocks.flatMap(blockLines).map((line) => line.length > 280 ? line.slice(0, 277) + "…" : line);
}

function drawLines(slide, lines, frame, fontSize = 22) {
  const text = lines.map((line) => "• " + line).join("\n");
  textBox(slide, "content-lines-" + frame.left + "-" + frame.top, text, frame, fontSize, { color: C.ink });
}

async function imageBox(slide, asset, name, position, showCaption = true) {
  const bytes = await fs.readFile(asset.resolved_path);
  const blob = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  box(slide, name + "-back", "rect", position, "#F7F7F7", C.rule, 1);
  slide.images.add({ blob, contentType: asset.content_type, alt: asset.alt_text, fit: "contain", geometry: "rect", position });
  if (showCaption) {
    textBox(slide, name + "-caption", "来源页 " + asset.source_page + "｜" + asset.alt_text, { left: position.left, top: position.top + position.height + 8, width: position.width, height: 32 }, 14, { color: C.muted });
  }
}

async function renderCover(slide, data) {
  box(slide, "cover-accent", "rect", { left: 884, top: 0, width: 396, height: 720 }, "#EAF7FD", "none", 0);
  box(slide, "cover-rule", "rect", { left: 72, top: 106, width: 110, height: 8 }, C.accentStrong, "none", 0);
  const coverTitleSize = data.title.length > 12 ? 52 : 60;
  textBox(slide, "cover-title", data.title, { left: 72, top: 152, width: 780, height: 210 }, coverTitleSize, { bold: true });
  const lines = allLines(data);
  textBox(slide, "cover-subtitle", lines.join("\n"), { left: 72, top: 402, width: 710, height: 130 }, 24, { color: C.muted });
  textBox(slide, "cover-focus", data.student_thinking_action, { left: 916, top: 208, width: 290, height: 250 }, 25, { bold: true });
  textBox(slide, "cover-number", "01", { left: 1164, top: 666, width: 52, height: 22 }, 14, { alignment: "right", color: C.muted });
}

async function renderSequence(slide, data) {
  header(slide, data);
  const lines = allLines(data).slice(0, 3);
  box(slide, "sequence-line", "rect", { left: 120, top: 332, width: 1040, height: 2 }, C.ink, C.ink, 0);
  for (let index = 0; index < 3; index += 1) {
    const left = 86 + index * 400;
    box(slide, "sequence-dot-" + index, "ellipse", { left: left + 30, top: 319, width: 26, height: 26 }, index === 1 ? C.accentStrong : C.ink, "none", 0);
    textBox(slide, "sequence-step-" + index, lines[index] || "", { left, top: 380, width: 330, height: 170 }, 24, { bold: index === 1 });
  }
  textBox(slide, "thinking-action", data.student_thinking_action, { left: 72, top: 594, width: 1050, height: 52 }, 20, { color: C.muted });
}

async function renderWithAssets(slide, data) {
  header(slide, data);
  const lines = allLines(data);
  if (data.assets.length) {
    const count = data.assets.length;
    const columns = count <= 2 ? 1 : (count <= 6 ? 2 : 3);
    const rows = Math.ceil(count / columns);
    const gap = 12;
    const area = { left: 64, top: 162, width: 632, height: 410 };
    const cellWidth = (area.width - gap * (columns - 1)) / columns;
    const cellHeight = (area.height - gap * (rows - 1)) / rows;
    const showCaptions = count <= 2;
    for (let index = 0; index < count; index += 1) {
      const column = index % columns;
      const row = Math.floor(index / columns);
      const captionSpace = showCaptions ? 34 : 0;
      await imageBox(slide, data.assets[index], "asset-" + index, {
        left: area.left + column * (cellWidth + gap),
        top: area.top + row * (cellHeight + gap),
        width: cellWidth,
        height: Math.max(42, cellHeight - captionSpace),
      }, showCaptions);
    }
    drawLines(slide, lines.slice(0, 8), { left: 748, top: 166, width: 458, height: 388 }, 21);
    textBox(slide, "thinking-action", data.student_thinking_action, { left: 748, top: 572, width: 458, height: 72 }, 19, { color: C.muted });
  } else {
    const midpoint = Math.ceil(lines.length / 2);
    drawLines(slide, lines.slice(0, midpoint), { left: 72, top: 166, width: 528, height: 410 }, 22);
    drawLines(slide, lines.slice(midpoint), { left: 674, top: 166, width: 532, height: 410 }, 22);
    textBox(slide, "thinking-action", data.student_thinking_action, { left: 72, top: 594, width: 1100, height: 54 }, 19, { color: C.muted });
  }
}

async function renderTwoColumn(slide, data) {
  header(slide, data);
  const lines = allLines(data);
  const midpoint = Math.ceil(lines.length / 2);
  box(slide, "left-panel", "rect", { left: 64, top: 166, width: 550, height: 390 }, "#F4F4F4", "none", 0);
  drawLines(slide, lines.slice(0, midpoint), { left: 92, top: 190, width: 496, height: 340 }, 22);
  drawLines(slide, lines.slice(midpoint), { left: 676, top: 174, width: 530, height: 382 }, 22);
  textBox(slide, "thinking-action", data.student_thinking_action, { left: 676, top: 582, width: 530, height: 62 }, 19, { color: C.muted });
}

const renderers = {
  cover: renderCover,
  sequence: renderSequence,
  two_column: renderTwoColumn,
  evidence: renderTwoColumn,
  material: renderWithAssets,
  question_prompt: renderWithAssets,
  question_reveal: renderWithAssets,
  error_analysis: renderTwoColumn,
  interaction: renderTwoColumn,
  practice: renderWithAssets,
  answer_review: renderWithAssets,
  homework: renderTwoColumn,
};

for (const data of deck.slides) {
  const slide = presentation.slides.add();
  slide.background.fill = C.canvas;
  const renderer = renderers[data.render_layout];
  if (!renderer) throw new Error("unsupported render layout: " + data.render_layout);
  await renderer(slide, data);
  for (let index = 0; index < data.chemical_expressions.length; index += 1) {
    const expression = data.chemical_expressions[index];
    textBox(slide, "chemical-expression-" + index, expression.display_text, { left: 690, top: 520 + index * 34, width: 500, height: 32 }, 20, { bold: true });
  }
  slide.speakerNotes.textFrame.setText(data.speaker_notes);
  slide.speakerNotes.setVisible(true);
}

for (const [index, slide] of presentation.slides.items.entries()) {
  const stem = "slide-" + String(index + 1).padStart(2, "0");
  const png = await presentation.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(path.join(previewDir, stem + ".png"), new Uint8Array(await png.arrayBuffer()));
  const layout = await slide.export({ format: "layout" });
  await fs.writeFile(path.join(previewDir, stem + ".layout.json"), await layout.text());
}

const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(pptxPath);
'''


def _ensure_node_modules_link(build_dir: Path, node_modules: Path) -> None:
    link = build_dir / "node_modules"
    if link.exists() or link.is_symlink():
        return
    try:
        os.symlink(node_modules, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(node_modules)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not link.exists():
            raise PresentationWorkbenchError(
                "presentation_node_modules_link_failed",
                "无法为演示文稿构建目录连接指定运行时依赖。",
                details={"stdout": result.stdout[-1000:], "stderr": result.stderr[-1000:]},
            )


def _remove_node_modules_link(build_dir: Path) -> None:
    link = build_dir / "node_modules"
    if not (link.exists() or link.is_symlink()):
        return
    try:
        if link.is_symlink():
            link.unlink()
        else:
            os.rmdir(link)
    except OSError as exc:
        raise PresentationWorkbenchError(
            "presentation_node_modules_link_cleanup_failed",
            "临时演示文稿依赖连接无法安全移除。",
            details={"path": str(link), "error": str(exc)},
        ) from exc


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PresentationWorkbenchError(
            "presentation_command_failed",
            "演示文稿命令无法完成。",
            details={"command": list(command), "error": str(exc)},
        ) from exc


def _pptx_text_and_object_inventory(pptx_path: Path) -> dict[str, Any]:
    try:
        with ZipFile(pptx_path, "r") as archive:
            bad = archive.testzip()
            if bad is not None:
                raise PresentationWorkbenchError(
                    "presentation_pptx_invalid", "PPTX压缩包存在损坏条目。", details={"entry": bad}
                )
            names = archive.namelist()
            slide_names = sorted(
                (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=lambda name: int(re.search(r"(\d+)", name).group(1)),
            )
            note_names = sorted(
                name for name in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name)
            )
            media_names = sorted(name for name in names if name.startswith("ppt/media/"))
            namespace = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            slide_texts: list[str] = []
            shape_count = 0
            picture_count = 0
            for name in slide_names:
                root = ET.fromstring(archive.read(name))
                slide_texts.append("".join(node.text or "" for node in root.findall(".//a:t", namespace)))
                shape_count += sum(1 for node in root.iter() if node.tag.endswith("}sp"))
                picture_count += sum(1 for node in root.iter() if node.tag.endswith("}pic"))
            note_texts: list[str] = []
            for name in note_names:
                root = ET.fromstring(archive.read(name))
                note_texts.append("".join(node.text or "" for node in root.findall(".//a:t", namespace)))
            return {
                "slide_texts": slide_texts,
                "shape_count": shape_count,
                "picture_count": picture_count,
                "notes_count": len(note_names),
                "notes_have_sources": all("[Sources]" in text and "[/Sources]" in text for text in note_texts),
                "media_hashes": [_sha256_bytes(archive.read(name)) for name in media_names],
                "media_count": len(media_names),
            }
    except (OSError, BadZipFile, ET.ParseError) as exc:
        raise PresentationWorkbenchError(
            "presentation_pptx_invalid", "PPTX结构无法读取。", details={"path": str(pptx_path)}
        ) from exc


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PresentationWorkbenchError(
            "presentation_rendered_page_invalid", "逐页渲染结果不是有效PNG。", details={"path": str(path)}
        )
    return struct.unpack(">II", data[16:24])


def _write_source_notes(deck: Mapping[str, Any], path: Path) -> None:
    lines = [
        f"Deck ID: {deck['deck_id']}",
        "Boundary: local_personal_lesson_preparation_candidate; publication_allowed=false",
        "",
    ]
    seen: set[tuple[str, str]] = set()
    for slide in deck["slides"]:
        for ref in slide["source_refs"]:
            key = (ref["label"], ref["detail"])
            if key not in seen:
                lines.append(f"- {ref['label']}: {ref['detail']}")
                seen.add(key)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _build_machine_qa(
    *,
    deck: Mapping[str, Any],
    deck_json_path: Path,
    pptx_path: Path,
    author_preview_dir: Path,
    rendered_dir: Path,
    overflow_output: str,
) -> dict[str, Any]:
    inventory = _pptx_text_and_object_inventory(pptx_path)
    rendered = sorted(
        rendered_dir.glob("slide-*.png"),
        key=lambda path: int(re.search(r"(\d+)", path.stem).group(1)),
    )
    previews = sorted(author_preview_dir.glob("slide-*.png"))
    layouts = sorted(author_preview_dir.glob("slide-*.layout.json"))
    expected_asset_hashes = sorted(
        {asset["sha256"] for slide in deck["slides"] for asset in slide["assets"]}
    )
    missing_media = sorted(set(expected_asset_hashes) - set(inventory["media_hashes"]))
    all_expressions = [
        expression["display_text"]
        for slide in deck["slides"]
        for expression in slide["chemical_expressions"]
    ]
    joined_text = "\n".join(inventory["slide_texts"])
    checks = {
        "deck_json": {
            "status": "pass",
            "path": deck_json_path.name,
            "sha256": _sha256_file(deck_json_path),
        },
        "editable_pptx": {
            "status": "pass" if inventory["shape_count"] > len(deck["slides"]) else "fail",
            "path": pptx_path.name,
            "sha256": _sha256_file(pptx_path),
            "shape_count": inventory["shape_count"],
            "picture_count": inventory["picture_count"],
        },
        "artifact_author_previews": {
            "status": "pass"
            if len(previews) == len(deck["slides"]) and len(layouts) == len(deck["slides"])
            else "fail",
            "png_count": len(previews),
            "layout_count": len(layouts),
            "expected": len(deck["slides"]),
        },
        "rendered_every_slide": {
            "status": "pass"
            if len(rendered) == len(deck["slides"])
            and all(_png_dimensions(path) == (1600, 900) for path in rendered)
            else "fail",
            "observed": len(rendered),
            "expected": len(deck["slides"]),
            "paths": [path.name for path in rendered],
        },
        "overflow": {
            "status": "pass"
            if "Test passed. No overflow detected." in overflow_output and "ERROR:" not in overflow_output
            else "fail",
            "tool_output": overflow_output.strip(),
        },
        "speaker_notes_sources": {
            "status": "pass"
            if inventory["notes_count"] == len(deck["slides"]) and inventory["notes_have_sources"]
            else "fail",
            "notes_count": inventory["notes_count"],
            "expected": len(deck["slides"]),
        },
        "asset_provenance": {
            "status": "pass",
            "asset_instances": sum(len(slide["assets"]) for slide in deck["slides"]),
            "unique_asset_ids": sorted(
                {asset["asset_id"] for slide in deck["slides"] for asset in slide["assets"]}
            ),
        },
        "embedded_asset_bytes": {
            "status": "pass" if not missing_media else "fail",
            "expected_asset_hashes": expected_asset_hashes,
            "missing_asset_hashes": missing_media,
        },
        "chemical_expression_text": {
            "status": "pass" if all(row in joined_text for row in all_expressions) else "fail",
            "expression_count": len(all_expressions),
        },
    }
    machine_pass = all(row["status"] == "pass" for row in checks.values())
    return {
        "schema_version": QA_SCHEMA_VERSION,
        "deck_id": deck["deck_id"],
        "artifact_id": deck["deck_id"],
        "qa_status": "pending_full_page_visual_review" if machine_pass else "failed_machine_qa",
        "machine_checks_passed": machine_pass,
        "checks": checks,
        "visual_review": {
            "status": "pending",
            "review_kind": "model_full_page_visual_review",
            "human_review": False,
            "slides": [
                {
                    "slide_number": slide["slide_number"],
                    "slide_id": slide["slide_id"],
                    "status": "pending",
                }
                for slide in deck["slides"]
            ],
        },
        "chemistry_review": {"status": "pending_teacher_review", "human_reviewed": False},
        "publication_allowed": False,
        "teacher_confirmation_required": True,
    }


def render_presentation_bundle(
    deck: Mapping[str, Any],
    *,
    asset_root: Path,
    output_dir: Path,
    toolchain: PresentationToolchain,
    filename: str = "lesson_presentation.pptx",
) -> dict[str, Any]:
    toolchain.validate()
    deck = validate_deck_json(deck)
    if Path(filename).name != filename or Path(filename).suffix.lower() != ".pptx":
        raise PresentationWorkbenchError(
            "presentation_output_filename_invalid", "PPTX输出文件名格式不正确。"
        )
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    deck_path = output_dir / "deck.json"
    pptx_path = output_dir / filename
    preview_dir = output_dir / "author_previews"
    rendered_dir = output_dir / "rendered_slides"
    montage_path = output_dir / "rendered_montage.png"
    qa_path = output_dir / "qa_report.json"
    source_notes_path = output_dir / "source-notes.txt"
    _atomic_write_json(deck_path, deck)
    _write_source_notes(deck, source_notes_path)

    env = os.environ.copy()
    env.update(
        {
            "RUNTIME_NODE": str(toolchain.node),
            "RUNTIME_NODE_MODULES": str(toolchain.node_modules),
            "RUNTIME_BIN_DIR": str(toolchain.bin_dir),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    # Keep the executable working directory short on Windows.  Persistent job
    # paths deliberately include tenant/project/job identities and can exceed
    # the legacy Win32 cwd limit even though ordinary file I/O still succeeds.
    with tempfile.TemporaryDirectory(prefix="presentation-build-") as temporary:
        build_dir = Path(temporary)
        _ensure_node_modules_link(build_dir, toolchain.node_modules)
        try:
            builder_path = build_dir / "build_presentation.mjs"
            builder_path.write_text(_builder_source(), encoding="utf-8", newline="\n")
            render_input_path = build_dir / "render-deck.json"
            _atomic_write_json(
                render_input_path, _resolved_render_deck(deck, asset_root=asset_root)
            )
            author_result = _run(
                [
                    str(toolchain.node),
                    str(builder_path),
                    str(render_input_path),
                    str(pptx_path),
                    str(preview_dir),
                ],
                cwd=build_dir,
                env=env,
                timeout=300,
            )
            if author_result.returncode != 0 or not pptx_path.is_file():
                raise PresentationWorkbenchError(
                    "presentation_pptx_render_failed",
                    "可编辑PPTX生成失败。",
                    details={
                        "stdout": author_result.stdout[-5000:],
                        "stderr": author_result.stderr[-5000:],
                    },
                )
        finally:
            _remove_node_modules_link(build_dir)

    render_result = _run(
        [
            str(toolchain.python),
            "-X",
            "utf8",
            str(toolchain.skill_dir / "container_tools" / "render_slides.py"),
            str(pptx_path),
            "--output_dir",
            str(rendered_dir),
            "--width",
            "1600",
            "--height",
            "900",
        ],
        cwd=toolchain.skill_dir,
        env=env,
        timeout=300,
    )
    if render_result.returncode != 0:
        raise PresentationWorkbenchError(
            "presentation_page_render_failed",
            "PPTX逐页PNG渲染失败。",
            details={"stdout": render_result.stdout[-5000:], "stderr": render_result.stderr[-5000:]},
        )
    overflow_result = _run(
        [
            str(toolchain.python),
            "-X",
            "utf8",
            str(toolchain.skill_dir / "container_tools" / "slides_test.py"),
            str(pptx_path),
        ],
        cwd=toolchain.skill_dir,
        env=env,
        timeout=300,
    )
    overflow_output = (overflow_result.stdout or "") + (overflow_result.stderr or "")
    montage_result = _run(
        [
            str(toolchain.python),
            "-X",
            "utf8",
            str(toolchain.skill_dir / "container_tools" / "create_montage.py"),
            "--input_dir",
            str(rendered_dir),
            "--output_file",
            str(montage_path),
            "--num_col",
            "3",
            "--cell_width",
            "480",
            "--cell_height",
            "270",
            "--label_mode",
            "number",
            "--fail_on_image_error",
        ],
        cwd=toolchain.skill_dir,
        env=env,
        timeout=180,
    )
    if montage_result.returncode != 0 or not montage_path.is_file():
        raise PresentationWorkbenchError(
            "presentation_montage_failed",
            "PPTX逐页预览拼图生成失败。",
            details={
                "stdout": montage_result.stdout[-3000:],
                "stderr": montage_result.stderr[-3000:],
            },
        )
    qa = _build_machine_qa(
        deck=deck,
        deck_json_path=deck_path,
        pptx_path=pptx_path,
        author_preview_dir=preview_dir,
        rendered_dir=rendered_dir,
        overflow_output=overflow_output,
    )
    _atomic_write_json(qa_path, qa)
    return {
        "artifact_id": deck["deck_id"],
        "deck_json": deck_path,
        "pptx": pptx_path,
        "author_previews": preview_dir,
        "rendered_slides": rendered_dir,
        "rendered_montage": montage_path,
        "qa_report": qa_path,
        "source_notes": source_notes_path,
        "qa_status": qa["qa_status"],
    }


def finalize_slide_visual_review(
    qa_path: Path,
    *,
    slide_reviews: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PresentationWorkbenchError(
            "presentation_qa_report_unreadable",
            "QA报告无法读取。",
            details={"path": str(qa_path)},
        ) from exc
    expected = qa.get("visual_review", {}).get("slides", [])
    expected_numbers = [row.get("slide_number") for row in expected]
    observed_numbers = [row.get("slide_number") for row in slide_reviews]
    if observed_numbers != expected_numbers:
        raise PresentationWorkbenchError(
            "presentation_visual_review_incomplete",
            "逐页视觉复核必须按顺序覆盖每一页。",
            details={"expected": expected_numbers, "observed": observed_numbers},
        )
    required = {
        "slide_number",
        "status",
        "overflow",
        "overlap",
        "font_readability",
        "formula_fidelity",
        "question_image_clarity",
        "notes",
    }
    normalized: list[dict[str, Any]] = []
    for expected_row, review_value in zip(expected, slide_reviews, strict=True):
        review = _mapping(review_value, "slide_review")
        _exact_keys(
            review,
            required,
            "slide_review",
            "presentation_visual_review_invalid",
        )
        statuses = {
            key: review[key]
            for key in (
                "status",
                "overflow",
                "overlap",
                "font_readability",
                "formula_fidelity",
                "question_image_clarity",
            )
        }
        if any(value not in {"pass", "fail", "not_applicable"} for value in statuses.values()):
            raise PresentationWorkbenchError(
                "presentation_visual_review_invalid", "逐页视觉复核状态不受支持。"
            )
        if statuses["status"] == "pass" and any(
            value == "fail" for key, value in statuses.items() if key != "status"
        ):
            raise PresentationWorkbenchError(
                "presentation_visual_review_invalid", "存在失败分项时页面总状态不能为pass。"
            )
        normalized.append(
            {
                "slide_number": review["slide_number"],
                "slide_id": expected_row["slide_id"],
                **statuses,
                "notes": _text(review["notes"], "slide_review.notes", limit=1200),
            }
        )
    visual_pass = all(row["status"] == "pass" for row in normalized)
    qa["visual_review"] = {
        "status": "pass" if visual_pass else "fail",
        "review_kind": "model_full_page_visual_review",
        "human_review": False,
        "slides": normalized,
    }
    qa["qa_status"] = (
        "pass_layout_candidate_teacher_review_pending"
        if qa.get("machine_checks_passed") and visual_pass
        else "failed_qa"
    )
    qa["chemistry_review"] = {"status": "pending_teacher_review", "human_reviewed": False}
    qa["publication_allowed"] = False
    qa["teacher_confirmation_required"] = True
    _atomic_write_json(qa_path, qa)
    return qa


def write_output_manifest(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if not output_dir.is_dir():
        raise PresentationWorkbenchError(
            "presentation_output_missing", "演示文稿输出目录不存在。"
        )
    manifest_path = output_dir / "output_manifest.json"
    files: list[dict[str, Any]] = []
    for path in sorted(output_dir.rglob("*"), key=lambda value: value.as_posix()):
        if not path.is_file() or path == manifest_path:
            continue
        files.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    artifact_id: str | None = None
    deck_path = output_dir / "deck.json"
    if deck_path.is_file():
        try:
            artifact_id = json.loads(deck_path.read_text(encoding="utf-8")).get("deck_id")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            artifact_id = None
    manifest = {
        "schema_version": OUTPUT_MANIFEST_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "output_root": output_dir.name,
        "file_count": len(files),
        "files": files,
        "candidate_use": "local_personal_lesson_preparation_candidate",
        "publication_allowed": False,
        "teacher_confirmation_required": True,
        "chemistry_review": "pending_teacher_review",
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


__all__ = [
    "DECK_SCHEMA_VERSION",
    "INPUT_SCHEMA_VERSION",
    "MANDATORY_SLIDE_TYPES",
    "OUTPUT_MANIFEST_SCHEMA_VERSION",
    "PresentationToolchain",
    "PresentationWorkbenchError",
    "QA_SCHEMA_VERSION",
    "compose_deck_json",
    "finalize_slide_visual_review",
    "render_presentation_bundle",
    "validate_deck_json",
    "validate_presentation_input",
    "write_output_manifest",
]
