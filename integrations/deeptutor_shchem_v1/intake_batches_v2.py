from __future__ import annotations

"""Formal multi-file, page-pixel visual intake candidate core (v2).

The module deliberately has no document-text input, text-layer adapter, or
text-recognition fallback.  A provider receives only original/rendered raster
page bytes plus a closed page manifest.  Every accepted visual observation is
bound back to the exact pixels sent to that provider by page SHA-256 and bbox.

This is a candidate-only desktop core.  It does not import, call, or write the
central question bank, runtime application, HTTP service, OpenAPI surface, or
any staging-tree dependency.
"""

import base64
import hashlib
import io
import itertools
import json
import os
import re
import threading
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, UnidentifiedImageError

INTAKE_BATCH_CANDIDATE_V2_SCHEMA_VERSION = "shchem.intake-batch-candidate.v2"
INTAKE_BATCH_VISUAL_FRAGMENT_V2_SCHEMA_VERSION = (
    "shchem.intake-batch-visual-fragment.v2"
)
INTAKE_BATCH_VISUAL_REQUEST_V2_SCHEMA_VERSION = "shchem.intake-batch-visual-request.v2"
INTAKE_BATCH_CAS_V2_SCHEMA_VERSION = "shchem.intake-batch-cas.v2"

DIRECT_PAGE_PIXEL_MODE = "direct_original_or_rendered_page_pixels"
CANDIDATE_STATUS = "candidate_only"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION_TOKEN = re.compile(r"^rev_[0-9]{8}_[0-9a-f]{64}$")
_SOURCE_ROLES = ("question", "answer", "handout")
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_DOCUMENT_MIMES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
_ALLOWED_MIMES = _IMAGE_MIMES | _DOCUMENT_MIMES
_MAX_SOURCE_FILES = 100
_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_PAGE_BYTES = 32 * 1024 * 1024
_MAX_PAGES = 500
_MAX_IMAGE_EDGE = 40_000
_FACTOR_IDS = frozenset(
    {
        "information_transformations",
        "reasoning_chain_steps",
        "knowledge_module_span",
        "representation_switches",
        "calculation_load",
        "experiment_load",
        "openness",
        "unfamiliarity",
        "language_density",
        "prior_dependency",
    }
)
_DEPENDENCY_RELATIONS = frozenset(
    {
        "uses_previous_result",
        "continues_calculation",
        "uses_previous_conclusion",
        "teacher_split_sequence",
        "other",
    }
)
_CAS_ROOT_LOCKS: dict[Path, threading.RLock] = {}
_CAS_ROOT_LOCKS_GUARD = threading.Lock()


class IntakeBatchV2Error(ValueError):
    """Stable fail-closed error exposed by the standalone core."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status!r})"


def canonical_json_bytes(value: Any) -> bytes:
    """The one canonical byte contract used for every v2 subject hash."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise IntakeBatchV2Error(
            "canonical_json_invalid", "value cannot be encoded as canonical JSON"
        ) from None


def strict_json_loads(raw: bytes | str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite number")

    try:
        text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
        return json.loads(
            text,
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise IntakeBatchV2Error(
            "provider_output_invalid_json",
            "visual provider output is not strict UTF-8 JSON",
            502,
        ) from None


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def candidate_sha256(candidate: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(candidate))


def _record_sha256(record: Mapping[str, Any], hash_field: str) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {key: value for key, value in record.items() if key != hash_field}
        )
    )


def _event_sha256(event: Mapping[str, Any]) -> str:
    """Hash an event without its derived hash/revision-token fields."""

    return sha256_bytes(
        canonical_json_bytes(
            {
                key: value
                for key, value in event.items()
                if key not in {"event_sha256", "result_revision_token"}
            }
        )
    )


def _deepcopy_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(value))


def _require_safe_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise IntakeBatchV2Error(f"{field}_invalid", f"{field} is invalid")
    return value


def _require_actor(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_ACTOR.fullmatch(value) is None:
        raise IntakeBatchV2Error("actor_id_invalid", "actor id is invalid")
    return value


def _require_idempotency(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_IDEMPOTENCY.fullmatch(value) is None:
        raise IntakeBatchV2Error(
            "idempotency_key_invalid", "idempotency key is invalid"
        )
    return value


def _require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise IntakeBatchV2Error(f"{field}_invalid", f"{field} is invalid")
    return value


def _require_exact_keys(
    value: Any,
    *,
    required: set[str],
    optional: set[str] | None = None,
    code: str = "provider_output_schema_invalid",
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntakeBatchV2Error(code, "structured object is invalid", 502)
    keys = set(value)
    allowed = required | (optional or set())
    if not required <= keys or not keys <= allowed:
        raise IntakeBatchV2Error(code, "structured object keys are invalid", 502)
    return dict(value)


@dataclass(frozen=True, slots=True, repr=False)
class IntakeBatchFile:
    filename: str
    mime_type: str
    content: bytes
    source_file_id: str | None = None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(filename={self.filename!r}, "
            f"mime_type={self.mime_type!r}, size_bytes={len(self.content)!r}, "
            "content_hidden=True)"
        )


BatchInputFile = IntakeBatchFile


@dataclass(frozen=True, slots=True, repr=False)
class RenderedPixelPage:
    pixels: bytes
    mime_type: str
    width: int
    height: int
    render_recipe_sha256: str

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(mime_type={self.mime_type!r}, "
            f"width={self.width!r}, height={self.height!r}, "
            f"size_bytes={len(self.pixels)!r}, pixels_hidden=True)"
        )


class PixelPageRenderer(Protocol):
    def render(
        self, source_file: IntakeBatchFile, *, source_role: str
    ) -> Sequence[RenderedPixelPage]: ...


@dataclass(frozen=True, slots=True, repr=False)
class VisualPixelPage:
    source_file_id: str
    source_role: str
    source_order: int
    page_number: int
    mime_type: str
    width: int
    height: int
    page_sha256: str
    render_recipe_sha256: str
    pixels: bytes

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(source_file_id={self.source_file_id!r}, "
            f"source_role={self.source_role!r}, page_number={self.page_number!r}, "
            f"page_sha256={self.page_sha256!r}, pixels_hidden=True)"
        )

    def public_manifest(self) -> dict[str, Any]:
        return {
            "source_file_id": self.source_file_id,
            "source_role": self.source_role,
            "source_order": self.source_order,
            "page_number": self.page_number,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "size_bytes": len(self.pixels),
            "page_sha256": self.page_sha256,
            "render_recipe_sha256": self.render_recipe_sha256,
        }

    def image_data_url(self) -> str:
        encoded = base64.b64encode(self.pixels).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


@dataclass(frozen=True, slots=True, repr=False)
class VisualShardRequest:
    batch_id: str
    shard_id: str
    shard_index: int
    source_role: str
    pages: tuple[VisualPixelPage, ...]

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(batch_id={self.batch_id!r}, "
            f"shard_id={self.shard_id!r}, source_role={self.source_role!r}, "
            f"page_count={len(self.pages)!r}, pixels_hidden=True)"
        )

    def transport_payload(self) -> dict[str, Any]:
        return {
            "schema_version": INTAKE_BATCH_VISUAL_REQUEST_V2_SCHEMA_VERSION,
            "batch_id": self.batch_id,
            "shard_id": self.shard_id,
            "shard_index": self.shard_index,
            "source_role": self.source_role,
            "input_contract": {
                "mode": DIRECT_PAGE_PIXEL_MODE,
                "source_text_layer_supplied": False,
                "fallback_allowed": False,
                "page_manifest_required": True,
            },
            "instructions": (
                "Directly inspect only the attached original/rendered page pixels. "
                "Return a candidate fragment bound to the supplied page SHA-256 and "
                "normalized bbox. Do not use or request a document text layer, and do "
                "not infer missing characters, chemistry, answers, or scoring points."
            ),
            "pages": [
                {
                    **page.public_manifest(),
                    "image_data_url": page.image_data_url(),
                }
                for page in self.pages
            ],
            "output_schema": intake_batch_visual_fragment_v2_schema(),
        }


class VisualShardProvider(Protocol):
    def analyze_shard(
        self, request: VisualShardRequest
    ) -> Mapping[str, Any] | bytes | str: ...


def _image_size(raw: bytes, mime_type: str) -> tuple[int, int]:
    if not raw or len(raw) > _MAX_PAGE_BYTES:
        raise IntakeBatchV2Error("page_pixels_invalid", "page pixel bytes are invalid")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            actual_format = (image.format or "").upper()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise IntakeBatchV2Error(
            "page_pixels_invalid", "page bytes are not a supported raster image"
        ) from None
    expected = {
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "image/webp": "WEBP",
    }[mime_type]
    if actual_format != expected:
        raise IntakeBatchV2Error(
            "page_mime_mismatch", "page MIME does not match its raster bytes"
        )
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= _MAX_IMAGE_EDGE
        or not 1 <= height <= _MAX_IMAGE_EDGE
    ):
        raise IntakeBatchV2Error(
            "page_dimensions_invalid", "page dimensions are invalid"
        )
    return width, height


def _validate_filename(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 180
        or Path(value).name != value
        or any(character in value for character in ("/", "\\", "\x00"))
    ):
        raise IntakeBatchV2Error("filename_invalid", "source filename is invalid")
    return value


def _coerce_input_file(value: IntakeBatchFile | Mapping[str, Any]) -> IntakeBatchFile:
    if isinstance(value, IntakeBatchFile):
        result = value
    elif isinstance(value, Mapping):
        keys = set(value)
        if not {"filename", "mime_type", "content"} <= keys or not keys <= {
            "filename",
            "mime_type",
            "content",
            "source_file_id",
        }:
            raise IntakeBatchV2Error(
                "source_file_invalid", "source file keys are invalid"
            )
        result = IntakeBatchFile(
            filename=value["filename"],
            mime_type=value["mime_type"],
            content=value["content"],
            source_file_id=value.get("source_file_id"),
        )
    else:
        raise IntakeBatchV2Error("source_file_invalid", "source file is invalid")
    filename = _validate_filename(result.filename)
    if result.mime_type not in _ALLOWED_MIMES:
        raise IntakeBatchV2Error("source_mime_invalid", "source MIME is unsupported")
    if (
        not isinstance(result.content, bytes)
        or not 1 <= len(result.content) <= _MAX_SOURCE_BYTES
    ):
        raise IntakeBatchV2Error("source_bytes_invalid", "source bytes are invalid")
    source_file_id = result.source_file_id
    if source_file_id is not None:
        source_file_id = _require_safe_id(source_file_id, field="source_file_id")
    return IntakeBatchFile(filename, result.mime_type, result.content, source_file_id)


def _direct_image_page(source: IntakeBatchFile) -> RenderedPixelPage:
    width, height = _image_size(source.content, source.mime_type)
    recipe = sha256_bytes(
        canonical_json_bytes(
            {"recipe": "identity_source_image_bytes_v1", "mime_type": source.mime_type}
        )
    )
    return RenderedPixelPage(
        pixels=source.content,
        mime_type=source.mime_type,
        width=width,
        height=height,
        render_recipe_sha256=recipe,
    )


def intake_batch_visual_fragment_v2_schema() -> dict[str, Any]:
    """Deeply closed visual-observation contract sent with every shard.

    The provider reports only facts visible in the supplied page pixels.  The
    richer internal candidate fields (curriculum mapping, classification,
    cognitive difficulty, answer authority, and scoring metadata) are filled
    deterministically after the response crosses back into the local process.
    """

    def closed(properties: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": dict(properties),
            "required": list(properties),
        }

    # Keep the outbound dialect deliberately small for OpenAI-compatible
    # providers.  Lengths, identifiers, ranges, and uniqueness remain enforced
    # by the local semantic validators below.
    safe_id = {"type": "string"}
    nonempty_text = {"type": "string"}
    nullable_nonempty_text = {"type": ["string", "null"]}
    string_refs = {
        "type": "array",
        "items": safe_id,
    }
    evidence_refs = dict(string_refs)
    definitions: dict[str, Any] = {
        "bbox": closed(
            {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "width": {"type": "number"},
                "height": {"type": "number"},
            }
        ),
        "evidence": closed(
            {
                "evidence_id": safe_id,
                "source_file_id": safe_id,
                "source_role": {"enum": list(_SOURCE_ROLES)},
                "page_number": {"type": "integer"},
                "page_sha256": {"type": "string"},
                "bbox": {"$ref": "#/$defs/bbox"},
            }
        ),
        "chemical_expression": closed(
            {
                "raw": nonempty_text,
                "kind": {
                    "enum": [
                        "formula",
                        "equation",
                        "ionic_equation",
                        "organic_structure",
                        "condition",
                        "unit",
                        "other",
                    ]
                },
                "status": {"enum": ["observed", "uncertain"]},
                "evidence_refs": evidence_refs,
            }
        ),
        "option": closed(
            {
                "label": {"type": "string"},
                "content": nonempty_text,
                "chemical_expressions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/chemical_expression"},
                },
                "visual_object_refs": string_refs,
                "evidence_refs": evidence_refs,
            }
        ),
        "shared_material": closed(
            {
                "shared_material_id": safe_id,
                "material_type": nonempty_text,
                "content": nonempty_text,
                "chemical_expressions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/chemical_expression"},
                },
                "visual_object_refs": string_refs,
                "evidence_refs": evidence_refs,
            }
        ),
        "visual_object": closed(
            {
                "visual_object_id": safe_id,
                "kind": {
                    "enum": [
                        "table",
                        "graph",
                        "apparatus",
                        "flowsheet",
                        "reaction_route",
                        "organic_structure",
                        "crystal_cell",
                        "image",
                        "other",
                    ]
                },
                "description": nonempty_text,
                "evidence_refs": evidence_refs,
            }
        ),
        "dependency_edge": closed(
            {
                "dependency_edge_id": safe_id,
                "from_atomic_part_id": safe_id,
                "to_atomic_part_id": safe_id,
                "relation": {"enum": sorted(_DEPENDENCY_RELATIONS)},
                "evidence_refs": evidence_refs,
            }
        ),
        "atomic_part": closed(
            {
                "atomic_part_id": safe_id,
                "part_label": nullable_nonempty_text,
                "sequence_in_printed": {"type": "integer"},
                "stem": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/option"},
                },
                "response_requirements": nonempty_text,
                "chemical_expressions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/chemical_expression"},
                },
                "visual_object_refs": string_refs,
                "evidence_refs": evidence_refs,
            }
        ),
        "printed_question": closed(
            {
                "printed_question_id": safe_id,
                "question_number": nonempty_text,
                "sequence_in_theme": {"type": "integer"},
                "stem": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/option"},
                },
                "response_requirements": {"type": "string"},
                "chemical_expressions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/chemical_expression"},
                },
                "shared_material_refs": string_refs,
                "visual_object_refs": string_refs,
                "atomic_parts": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/atomic_part"},
                },
                "evidence_refs": evidence_refs,
            }
        ),
        "theme_fragment": closed(
            {
                "theme_big_question_id": safe_id,
                "theme_number": nonempty_text,
                "title": nonempty_text,
                "context": nonempty_text,
                "sequence_in_paper": {"type": "integer"},
                "fragment_position": {"enum": ["complete", "start", "middle", "end"]},
                "shared_materials": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/shared_material"},
                },
                "visual_objects": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/visual_object"},
                },
                "dependency_edges": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/dependency_edge"},
                },
                "printed_questions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/printed_question"},
                },
                "evidence_refs": evidence_refs,
            }
        ),
        "paper_identity": closed(
            {
                "title": nonempty_text,
                "source_year": {
                    "anyOf": [
                        {"type": "integer"},
                        {"enum": ["unknown"]},
                    ]
                },
                "source_region_or_school": nonempty_text,
                "paper_type": nonempty_text,
                "evidence_refs": evidence_refs,
            }
        ),
        "answer_candidate": closed(
            {
                "answer_candidate_id": safe_id,
                "question_number": nonempty_text,
                "part_label": nullable_nonempty_text,
                "atomic_part_id": {"anyOf": [safe_id, {"type": "null"}]},
                "answer_body": nonempty_text,
                "chemical_expressions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/chemical_expression"},
                },
                "evidence_refs": evidence_refs,
            }
        ),
    }
    properties = {
        "schema_version": {"enum": [INTAKE_BATCH_VISUAL_FRAGMENT_V2_SCHEMA_VERSION]},
        "shard_id": safe_id,
        "source_role": {"enum": list(_SOURCE_ROLES)},
        "input_mode": {"enum": [DIRECT_PAGE_PIXEL_MODE]},
        "source_text_layer_used": {"type": "boolean", "enum": [False]},
        "fallback_used": {"type": "boolean", "enum": [False]},
        "evidence": {
            "type": "array",
            "items": {"$ref": "#/$defs/evidence"},
        },
        "paper_identity": {
            "anyOf": [{"$ref": "#/$defs/paper_identity"}, {"type": "null"}]
        },
        "theme_fragments": {
            "type": "array",
            "items": {"$ref": "#/$defs/theme_fragment"},
        },
        "answer_candidates": {
            "type": "array",
            "items": {"$ref": "#/$defs/answer_candidate"},
        },
        "warnings": {
            "type": "array",
            "items": nonempty_text,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": definitions,
        **closed(properties),
    }


def _postfill_visual_observation_defaults(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Complete non-visual candidate fields without interpreting page content.

    This conversion is intentionally deterministic and idempotent.  It keeps
    every field supplied by the legacy full fragment contract, while adding
    only the internal placeholders omitted from the lean provider contract.
    """

    result = deepcopy(dict(value))

    def mapping_rows(parent: Mapping[str, Any], field: str) -> list[dict[str, Any]]:
        rows = parent.get(field)
        if not isinstance(rows, list):
            return []
        mutable: list[dict[str, Any]] = []
        for index, item in enumerate(rows):
            if not isinstance(item, Mapping):
                continue
            row = item if isinstance(item, dict) else dict(item)
            if row is not item:
                rows[index] = row
            mutable.append(row)
        return mutable

    def postfill_chemistry(parent: Mapping[str, Any]) -> None:
        for expression in mapping_rows(parent, "chemical_expressions"):
            expression.setdefault("normalized", None)

    def unknown_curriculum(evidence_refs: list[str]) -> dict[str, Any]:
        return {
            "textbook_edition": None,
            "primary_chapter": None,
            "secondary_chapters": [],
            "mapping_status": "unknown",
            "rationale": ("视觉导入阶段不作教材映射，等待教师或本地知识图谱补充。"),
            "evidence_refs": list(evidence_refs),
        }

    def unknown_classification(evidence_refs: list[str]) -> dict[str, Any]:
        return {
            "item_type": "unknown",
            "primary_knowledge_K": [],
            "supporting_knowledge_K": [],
            "ability_A": [],
            "context_C": [],
            "response_R": [],
            "representation_RP": [],
            "evidence_refs": list(evidence_refs),
        }

    def unknown_difficulty(evidence_refs: list[str]) -> dict[str, Any]:
        return {
            "cognitive_prelabel": "unknown",
            "status": "unknown",
            "rationale": ("视觉导入阶段不作认知难度标定，等待教师或本地规则补充。"),
            "factors": [],
            "measured_difficulty": None,
            "human_verified": False,
            "student_data_used": False,
            "evidence_refs": list(evidence_refs),
        }

    for theme in mapping_rows(result, "theme_fragments"):
        for shared in mapping_rows(theme, "shared_materials"):
            postfill_chemistry(shared)
        for visual in mapping_rows(theme, "visual_objects"):
            visual.setdefault("structured_representation", None)
            visual.setdefault("requires_review", True)
        for printed in mapping_rows(theme, "printed_questions"):
            postfill_chemistry(printed)
            for option in mapping_rows(printed, "options"):
                postfill_chemistry(option)
            for atomic in mapping_rows(printed, "atomic_parts"):
                postfill_chemistry(atomic)
                for option in mapping_rows(atomic, "options"):
                    postfill_chemistry(option)
                refs = atomic.get("evidence_refs")
                evidence_refs = list(refs) if isinstance(refs, list) else []
                atomic.setdefault("curriculum", unknown_curriculum(evidence_refs))
                atomic.setdefault(
                    "classification", unknown_classification(evidence_refs)
                )
                atomic.setdefault(
                    "cognitive_difficulty", unknown_difficulty(evidence_refs)
                )

    for answer in mapping_rows(result, "answer_candidates"):
        postfill_chemistry(answer)
        answer.setdefault("analysis", "")
        answer.setdefault("max_score", 0)
        answer.setdefault("scoring_points", [])
        answer.setdefault("authority", "unknown")
        answer.setdefault("independently_verified", False)

    return result


def intake_batch_candidate_v2_schema() -> dict[str, Any]:
    """Public structural summary; semantic validation is stricter than this schema."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": INTAKE_BATCH_CANDIDATE_V2_SCHEMA_VERSION},
            "candidate_status": {"const": CANDIDATE_STATUS},
            "recognition_mode": {"const": DIRECT_PAGE_PIXEL_MODE},
            "input_contract": {"type": "object"},
            "batch": {"type": "object"},
            "evidence": {"type": "array"},
            "paper": {"type": "object"},
            "unaligned_answer_candidates": {"type": "array"},
            "review_blockers": {"type": "array"},
            "teacher_operations": {"type": "array"},
            "requires_teacher_review": {"const": True},
            "human_reviewed": {"const": False},
            "retrieval_ready": {"const": False},
            "publication_allowed": {"const": False},
            "central_question_bank_write": {"const": False},
        },
        "required": [
            "schema_version",
            "candidate_status",
            "recognition_mode",
            "input_contract",
            "batch",
            "evidence",
            "paper",
            "unaligned_answer_candidates",
            "review_blockers",
            "teacher_operations",
            "requires_teacher_review",
            "human_reviewed",
            "retrieval_ready",
            "publication_allowed",
            "central_question_bank_write",
        ],
    }


def _require_text(
    value: Any,
    *,
    field: str,
    allow_empty: bool = False,
    maximum: int = 20_000,
) -> str:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or len(value) > maximum
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", f"{field} is invalid", 502
        )
    return value


def _require_string_list(
    value: Any,
    *,
    field: str,
    maximum_items: int = 200,
    allow_empty: bool = True,
) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > maximum_items
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", f"{field} is invalid", 502
        )
    return list(value)


def _validate_evidence_refs(
    value: Any,
    known_evidence: set[str],
    *,
    field: str = "evidence_refs",
    require: bool = True,
) -> list[str]:
    refs = _require_string_list(
        value, field=field, maximum_items=100, allow_empty=not require
    )
    if any(reference not in known_evidence for reference in refs):
        raise IntakeBatchV2Error(
            "provider_output_evidence_invalid",
            f"{field} refers to evidence outside its visual shard",
            502,
        )
    return refs


def _validate_evidence_row(
    value: Any,
    *,
    request: VisualShardRequest,
) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "evidence_id",
            "source_file_id",
            "source_role",
            "page_number",
            "page_sha256",
            "bbox",
        },
    )
    evidence_id = _require_safe_id(row["evidence_id"], field="evidence_id")
    source_file_id = _require_safe_id(row["source_file_id"], field="source_file_id")
    if row["source_role"] != request.source_role:
        raise IntakeBatchV2Error(
            "provider_output_evidence_invalid",
            "evidence source role differs from the visual shard role",
            502,
        )
    if type(row["page_number"]) is not int or row["page_number"] < 1:
        raise IntakeBatchV2Error(
            "provider_output_evidence_invalid", "evidence page number is invalid", 502
        )
    page_sha = _require_sha256(row["page_sha256"], field="page_sha256")
    matches = [
        page
        for page in request.pages
        if page.source_file_id == source_file_id
        and page.page_number == row["page_number"]
    ]
    if len(matches) != 1 or matches[0].page_sha256 != page_sha:
        raise IntakeBatchV2Error(
            "provider_output_evidence_invalid",
            "evidence is not bound to pixels in the visual shard",
            502,
        )
    bbox = _require_exact_keys(row["bbox"], required={"x", "y", "width", "height"})
    for key in ("x", "y", "width", "height"):
        coordinate = bbox[key]
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise IntakeBatchV2Error(
                "provider_output_evidence_invalid", "evidence bbox is invalid", 502
            )
    if (
        not 0 <= bbox["x"] < 1
        or not 0 <= bbox["y"] < 1
        or not 0 < bbox["width"] <= 1
        or not 0 < bbox["height"] <= 1
        or bbox["x"] + bbox["width"] > 1.000001
        or bbox["y"] + bbox["height"] > 1.000001
    ):
        raise IntakeBatchV2Error(
            "provider_output_evidence_invalid", "evidence bbox is out of bounds", 502
        )
    return {
        "evidence_id": evidence_id,
        "source_file_id": source_file_id,
        "source_role": request.source_role,
        "page_number": row["page_number"],
        "page_sha256": page_sha,
        "bbox": {
            "x": float(bbox["x"]),
            "y": float(bbox["y"]),
            "width": float(bbox["width"]),
            "height": float(bbox["height"]),
        },
    }


def _validate_chemical_expression(
    value: Any, known_evidence: set[str]
) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={"raw", "normalized", "kind", "status", "evidence_refs"},
    )
    _require_text(row["raw"], field="chemical expression raw", maximum=2000)
    if row["normalized"] is not None:
        _require_text(
            row["normalized"], field="chemical expression normalized", maximum=4000
        )
    if row["kind"] not in {
        "formula",
        "equation",
        "ionic_equation",
        "organic_structure",
        "condition",
        "unit",
        "other",
    }:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "chemical expression kind is invalid",
            502,
        )
    if row["status"] not in {"observed", "uncertain"}:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "chemical expression status is invalid",
            502,
        )
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_visual_object(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "visual_object_id",
            "kind",
            "description",
            "structured_representation",
            "requires_review",
            "evidence_refs",
        },
    )
    _require_safe_id(row["visual_object_id"], field="visual_object_id")
    if row["kind"] not in {
        "table",
        "graph",
        "apparatus",
        "flowsheet",
        "reaction_route",
        "organic_structure",
        "crystal_cell",
        "image",
        "other",
    }:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "visual object kind is invalid", 502
        )
    _require_text(row["description"], field="visual object description", maximum=5000)
    if row["structured_representation"] is not None and not isinstance(
        row["structured_representation"], Mapping
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "visual object structured representation is invalid",
            502,
        )
    if type(row["requires_review"]) is not bool:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "visual object review flag is invalid",
            502,
        )
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_option(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "label",
            "content",
            "chemical_expressions",
            "visual_object_refs",
            "evidence_refs",
        },
    )
    _require_text(row["label"], field="option label", maximum=40)
    _require_text(row["content"], field="option content", maximum=10_000)
    if not isinstance(row["chemical_expressions"], list):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "option chemistry is invalid", 502
        )
    for item in row["chemical_expressions"]:
        _validate_chemical_expression(item, known_evidence)
    _require_string_list(row["visual_object_refs"], field="option visual object refs")
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_curriculum(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "textbook_edition",
            "primary_chapter",
            "secondary_chapters",
            "mapping_status",
            "rationale",
            "evidence_refs",
        },
    )
    for key in ("textbook_edition", "primary_chapter"):
        if row[key] is not None:
            _require_text(row[key], field=key, maximum=500)
    _require_string_list(
        row["secondary_chapters"], field="secondary chapters", maximum_items=30
    )
    if row["mapping_status"] not in {
        "candidate_pending_teacher",
        "blocked_pending_review",
        "unknown",
    }:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "curriculum mapping status is invalid",
            502,
        )
    _require_text(row["rationale"], field="curriculum rationale", maximum=5000)
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_classification(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "item_type",
            "primary_knowledge_K",
            "supporting_knowledge_K",
            "ability_A",
            "context_C",
            "response_R",
            "representation_RP",
            "evidence_refs",
        },
    )
    _require_text(row["item_type"], field="item type", maximum=160)
    _require_string_list(
        row["primary_knowledge_K"], field="primary knowledge K", maximum_items=20
    )
    for key in (
        "supporting_knowledge_K",
        "ability_A",
        "context_C",
        "response_R",
        "representation_RP",
    ):
        _require_string_list(row[key], field=key, maximum_items=50)
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_difficulty(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "cognitive_prelabel",
            "status",
            "rationale",
            "factors",
            "measured_difficulty",
            "human_verified",
            "student_data_used",
            "evidence_refs",
        },
    )
    if row["cognitive_prelabel"] not in {"D1", "D2", "D3", "unknown"}:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "cognitive difficulty is invalid", 502
        )
    if row["status"] not in {
        "candidate_pending_teacher",
        "blocked_pending_review",
        "unknown",
    }:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "difficulty status is invalid", 502
        )
    _require_text(row["rationale"], field="difficulty rationale", maximum=5000)
    if row["measured_difficulty"] is not None:
        raise IntakeBatchV2Error(
            "candidate_authority_escalation",
            "visual intake cannot claim measured difficulty",
            409,
        )
    if row["human_verified"] is not False or row["student_data_used"] is not False:
        raise IntakeBatchV2Error(
            "candidate_authority_escalation",
            "visual intake cannot claim human verification or student measurement",
            409,
        )
    factors = row["factors"]
    if not isinstance(factors, list) or len(factors) > 10:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "difficulty factors are invalid", 502
        )
    factor_ids: set[str] = set()
    for factor_value in factors:
        factor = _require_exact_keys(
            factor_value,
            required={"dimension_id", "level", "rationale", "evidence_refs"},
        )
        if factor["dimension_id"] not in _FACTOR_IDS:
            raise IntakeBatchV2Error(
                "provider_output_schema_invalid",
                "difficulty factor id is invalid",
                502,
            )
        if factor["dimension_id"] in factor_ids:
            raise IntakeBatchV2Error(
                "provider_output_schema_invalid",
                "difficulty factor id is duplicated",
                502,
            )
        factor_ids.add(factor["dimension_id"])
        if type(factor["level"]) is not int or not 0 <= factor["level"] <= 3:
            raise IntakeBatchV2Error(
                "provider_output_schema_invalid",
                "difficulty factor level is invalid",
                502,
            )
        _require_text(
            factor["rationale"], field="difficulty factor rationale", maximum=2000
        )
        _validate_evidence_refs(factor["evidence_refs"], known_evidence)
    if row["status"] == "candidate_pending_teacher" and factor_ids != _FACTOR_IDS:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "complete candidate difficulty requires all ten evidence factors",
            502,
        )
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_atomic(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "atomic_part_id",
            "part_label",
            "sequence_in_printed",
            "stem",
            "options",
            "response_requirements",
            "chemical_expressions",
            "visual_object_refs",
            "curriculum",
            "classification",
            "cognitive_difficulty",
            "evidence_refs",
        },
    )
    _require_safe_id(row["atomic_part_id"], field="atomic_part_id")
    if row["part_label"] is not None:
        _require_text(row["part_label"], field="part label", maximum=80)
    if type(row["sequence_in_printed"]) is not int or row["sequence_in_printed"] < 1:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "atomic sequence is invalid", 502
        )
    _require_text(row["stem"], field="atomic stem", allow_empty=True)
    if not isinstance(row["options"], list) or len(row["options"]) > 30:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "atomic options are invalid", 502
        )
    for option in row["options"]:
        _validate_option(option, known_evidence)
    _require_text(
        row["response_requirements"], field="response requirements", maximum=10_000
    )
    if not isinstance(row["chemical_expressions"], list):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "atomic chemistry is invalid", 502
        )
    for expression in row["chemical_expressions"]:
        _validate_chemical_expression(expression, known_evidence)
    _require_string_list(row["visual_object_refs"], field="atomic visual refs")
    _validate_curriculum(row["curriculum"], known_evidence)
    _validate_classification(row["classification"], known_evidence)
    _validate_difficulty(row["cognitive_difficulty"], known_evidence)
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_printed(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "printed_question_id",
            "question_number",
            "sequence_in_theme",
            "stem",
            "options",
            "response_requirements",
            "chemical_expressions",
            "shared_material_refs",
            "visual_object_refs",
            "atomic_parts",
            "evidence_refs",
        },
    )
    _require_safe_id(row["printed_question_id"], field="printed_question_id")
    _require_text(row["question_number"], field="question number", maximum=80)
    if type(row["sequence_in_theme"]) is not int or row["sequence_in_theme"] < 1:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "printed sequence is invalid", 502
        )
    _require_text(row["stem"], field="printed stem", allow_empty=True)
    if not isinstance(row["options"], list) or len(row["options"]) > 30:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "printed options are invalid", 502
        )
    for option in row["options"]:
        _validate_option(option, known_evidence)
    _require_text(
        row["response_requirements"],
        field="printed response requirements",
        allow_empty=True,
    )
    if not isinstance(row["chemical_expressions"], list):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "printed chemistry is invalid", 502
        )
    for expression in row["chemical_expressions"]:
        _validate_chemical_expression(expression, known_evidence)
    _require_string_list(row["shared_material_refs"], field="shared material refs")
    _require_string_list(row["visual_object_refs"], field="printed visual refs")
    if not isinstance(row["atomic_parts"], list) or not row["atomic_parts"]:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "printed question must contain atomic parts",
            502,
        )
    atomics = [_validate_atomic(item, known_evidence) for item in row["atomic_parts"]]
    if [item["sequence_in_printed"] for item in atomics] != list(
        range(1, len(atomics) + 1)
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "atomic sequence is not contiguous", 502
        )
    if len({item["atomic_part_id"] for item in atomics}) != len(atomics):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "atomic id is duplicated", 502
        )
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_shared_material(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "shared_material_id",
            "material_type",
            "content",
            "chemical_expressions",
            "visual_object_refs",
            "evidence_refs",
        },
    )
    _require_safe_id(row["shared_material_id"], field="shared_material_id")
    _require_text(row["material_type"], field="material type", maximum=160)
    _require_text(row["content"], field="shared material content")
    if not isinstance(row["chemical_expressions"], list):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "shared material chemistry is invalid",
            502,
        )
    for expression in row["chemical_expressions"]:
        _validate_chemical_expression(expression, known_evidence)
    _require_string_list(row["visual_object_refs"], field="shared material visual refs")
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_dependency(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "dependency_edge_id",
            "from_atomic_part_id",
            "to_atomic_part_id",
            "relation",
            "evidence_refs",
        },
    )
    for key in ("dependency_edge_id", "from_atomic_part_id", "to_atomic_part_id"):
        _require_safe_id(row[key], field=key)
    if row["relation"] not in _DEPENDENCY_RELATIONS:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "dependency relation is invalid", 502
        )
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_theme_fragment(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "theme_big_question_id",
            "theme_number",
            "title",
            "context",
            "sequence_in_paper",
            "fragment_position",
            "shared_materials",
            "visual_objects",
            "dependency_edges",
            "printed_questions",
            "evidence_refs",
        },
    )
    _require_safe_id(row["theme_big_question_id"], field="theme_big_question_id")
    _require_text(row["theme_number"], field="theme number", maximum=80)
    _require_text(row["title"], field="theme title", maximum=1000)
    _require_text(row["context"], field="theme context")
    if type(row["sequence_in_paper"]) is not int or row["sequence_in_paper"] < 1:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "theme sequence is invalid", 502
        )
    if row["fragment_position"] not in {"complete", "start", "middle", "end"}:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "theme fragment position is invalid", 502
        )
    for key in (
        "shared_materials",
        "visual_objects",
        "dependency_edges",
        "printed_questions",
    ):
        if not isinstance(row[key], list):
            raise IntakeBatchV2Error(
                "provider_output_schema_invalid", f"theme {key} is invalid", 502
            )
    for item in row["shared_materials"]:
        _validate_shared_material(item, known_evidence)
    for item in row["visual_objects"]:
        _validate_visual_object(item, known_evidence)
    for item in row["dependency_edges"]:
        _validate_dependency(item, known_evidence)
    for item in row["printed_questions"]:
        _validate_printed(item, known_evidence)
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_scoring_point(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={"scoring_point_id", "description", "score", "evidence_refs"},
    )
    _require_safe_id(row["scoring_point_id"], field="scoring_point_id")
    _require_text(row["description"], field="scoring point description", maximum=5000)
    if (
        isinstance(row["score"], bool)
        or not isinstance(row["score"], (int, float))
        or not 0 <= row["score"] <= 1000
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "scoring point score is invalid", 502
        )
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_answer_candidate(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "answer_candidate_id",
            "question_number",
            "part_label",
            "atomic_part_id",
            "answer_body",
            "analysis",
            "max_score",
            "scoring_points",
            "chemical_expressions",
            "authority",
            "independently_verified",
            "evidence_refs",
        },
    )
    _require_safe_id(row["answer_candidate_id"], field="answer_candidate_id")
    _require_text(row["question_number"], field="answer question number", maximum=80)
    if row["part_label"] is not None:
        _require_text(row["part_label"], field="answer part label", maximum=80)
    if row["atomic_part_id"] is not None:
        _require_safe_id(row["atomic_part_id"], field="atomic_part_id")
    _require_text(row["answer_body"], field="answer body")
    _require_text(row["analysis"], field="answer analysis", allow_empty=True)
    if (
        isinstance(row["max_score"], bool)
        or not isinstance(row["max_score"], (int, float))
        or not 0 <= row["max_score"] <= 1000
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "answer max score is invalid", 502
        )
    if not isinstance(row["scoring_points"], list) or len(row["scoring_points"]) > 100:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "answer scoring points are invalid", 502
        )
    for item in row["scoring_points"]:
        _validate_scoring_point(item, known_evidence)
    if (
        sum(float(item["score"]) for item in row["scoring_points"])
        > float(row["max_score"]) + 1e-9
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid",
            "scoring points exceed the answer max score",
            502,
        )
    if not isinstance(row["chemical_expressions"], list):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "answer chemistry is invalid", 502
        )
    for expression in row["chemical_expressions"]:
        _validate_chemical_expression(expression, known_evidence)
    if row["authority"] not in {
        "nonofficial_reference",
        "teacher_material_reference",
        "unknown",
    }:
        raise IntakeBatchV2Error(
            "candidate_authority_escalation",
            "visual answer candidate cannot claim official authority",
            409,
        )
    if row["independently_verified"] is not False:
        raise IntakeBatchV2Error(
            "candidate_authority_escalation",
            "visual answer candidate cannot claim independent verification",
            409,
        )
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_paper_identity(
    value: Any, known_evidence: set[str]
) -> dict[str, Any] | None:
    if value is None:
        return None
    row = _require_exact_keys(
        value,
        required={
            "title",
            "source_year",
            "source_region_or_school",
            "paper_type",
            "evidence_refs",
        },
    )
    _require_text(row["title"], field="paper title", maximum=1000)
    if row["source_year"] != "unknown" and (
        type(row["source_year"]) is not int or not 1900 <= row["source_year"] <= 2200
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "paper source year is invalid", 502
        )
    _require_text(
        row["source_region_or_school"],
        field="paper source region or school",
        maximum=1000,
    )
    _require_text(row["paper_type"], field="paper type", maximum=500)
    _validate_evidence_refs(row["evidence_refs"], known_evidence)
    return deepcopy(row)


def _validate_fragment(
    raw: Mapping[str, Any] | bytes | str,
    *,
    request: VisualShardRequest,
) -> dict[str, Any]:
    value = strict_json_loads(raw) if isinstance(raw, (bytes, str)) else raw
    fragment = _require_exact_keys(
        value,
        required={
            "schema_version",
            "shard_id",
            "source_role",
            "input_mode",
            "source_text_layer_used",
            "fallback_used",
            "evidence",
            "paper_identity",
            "theme_fragments",
            "answer_candidates",
            "warnings",
        },
    )
    fragment = _postfill_visual_observation_defaults(fragment)
    if (
        fragment["schema_version"] != INTAKE_BATCH_VISUAL_FRAGMENT_V2_SCHEMA_VERSION
        or fragment["shard_id"] != request.shard_id
        or fragment["source_role"] != request.source_role
        or fragment["input_mode"] != DIRECT_PAGE_PIXEL_MODE
        or fragment["source_text_layer_used"] is not False
        or fragment["fallback_used"] is not False
    ):
        raise IntakeBatchV2Error(
            "provider_output_contract_invalid",
            "visual provider response violates the direct-pixel contract",
            502,
        )
    if not isinstance(fragment["evidence"], list) or len(fragment["evidence"]) > 5000:
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "fragment evidence is invalid", 502
        )
    evidence = [
        _validate_evidence_row(item, request=request) for item in fragment["evidence"]
    ]
    evidence_ids = {item["evidence_id"] for item in evidence}
    if len(evidence_ids) != len(evidence):
        raise IntakeBatchV2Error(
            "provider_output_evidence_invalid",
            "fragment evidence id is duplicated",
            502,
        )
    paper_identity = _validate_paper_identity(fragment["paper_identity"], evidence_ids)
    if (
        not isinstance(fragment["theme_fragments"], list)
        or len(fragment["theme_fragments"]) > 500
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "theme fragments are invalid", 502
        )
    themes = [
        _validate_theme_fragment(item, evidence_ids)
        for item in fragment["theme_fragments"]
    ]
    if (
        not isinstance(fragment["answer_candidates"], list)
        or len(fragment["answer_candidates"]) > 5000
    ):
        raise IntakeBatchV2Error(
            "provider_output_schema_invalid", "answer candidates are invalid", 502
        )
    answers = [
        _validate_answer_candidate(item, evidence_ids)
        for item in fragment["answer_candidates"]
    ]
    warnings = _require_string_list(
        fragment["warnings"], field="fragment warnings", maximum_items=500
    )
    if request.source_role == "answer" and themes:
        raise IntakeBatchV2Error(
            "provider_output_role_invalid",
            "answer shards cannot create question hierarchy nodes",
            502,
        )
    if request.source_role != "answer" and answers:
        raise IntakeBatchV2Error(
            "provider_output_role_invalid",
            "question or handout shards cannot create independent answer records",
            502,
        )
    return {
        "schema_version": fragment["schema_version"],
        "shard_id": fragment["shard_id"],
        "source_role": fragment["source_role"],
        "input_mode": fragment["input_mode"],
        "source_text_layer_used": False,
        "fallback_used": False,
        "evidence": evidence,
        "paper_identity": paper_identity,
        "theme_fragments": themes,
        "answer_candidates": answers,
        "warnings": warnings,
    }


def _empty_answer() -> dict[str, Any]:
    return {
        "status": "missing",
        "answer_candidate_id": None,
        "answer_body": "",
        "analysis": "",
        "max_score": 0,
        "scoring_points": [],
        "chemical_expressions": [],
        "authority": "unknown",
        "independently_verified": False,
        "alignment": {
            "status": "missing",
            "confidence": 0.0,
            "evidence_refs": [],
        },
        "evidence_refs": [],
    }


def _answer_from_candidate(
    answer: Mapping[str, Any], *, alignment_status: str
) -> dict[str, Any]:
    return {
        "status": "source_answer_candidate",
        "answer_candidate_id": answer["answer_candidate_id"],
        "answer_body": answer["answer_body"],
        "analysis": answer["analysis"],
        "max_score": answer["max_score"],
        "scoring_points": deepcopy(answer["scoring_points"]),
        "chemical_expressions": deepcopy(answer["chemical_expressions"]),
        "authority": answer["authority"],
        "independently_verified": False,
        "alignment": {
            "status": alignment_status,
            "confidence": 1.0 if alignment_status.startswith("teacher_") else 0.98,
            "evidence_refs": list(answer["evidence_refs"]),
        },
        "evidence_refs": list(answer["evidence_refs"]),
    }


def _invoke_provider(
    provider: VisualShardProvider
    | Callable[[VisualShardRequest], Mapping[str, Any] | bytes | str],
    request: VisualShardRequest,
) -> Mapping[str, Any] | bytes | str:
    analyze_shard = getattr(provider, "analyze_shard", None)
    if callable(analyze_shard):
        return analyze_shard(request)
    analyze = getattr(provider, "analyze", None)
    if callable(analyze):
        return analyze(request)
    if callable(provider):
        return provider(request)
    raise IntakeBatchV2Error(
        "visual_provider_invalid", "visual shard provider is invalid", 503
    )


def _render_source_pages(
    source: IntakeBatchFile,
    *,
    source_role: str,
    renderer: PixelPageRenderer | None,
) -> list[RenderedPixelPage]:
    if source.mime_type in _IMAGE_MIMES:
        rendered = [_direct_image_page(source)]
    else:
        if renderer is None:
            raise IntakeBatchV2Error(
                "page_renderer_required",
                "PDF/DOCX intake requires an explicit page-pixel renderer",
                409,
            )
        try:
            rendered = list(renderer.render(source, source_role=source_role))
        except IntakeBatchV2Error:
            raise
        except Exception:  # noqa: BLE001 - renderer is an injected plugin boundary.
            raise IntakeBatchV2Error(
                "page_render_failed", "source could not be rendered to page pixels", 409
            ) from None
    if not rendered:
        raise IntakeBatchV2Error(
            "page_render_empty", "source produced no rendered page pixels", 409
        )
    validated: list[RenderedPixelPage] = []
    for page in rendered:
        if (
            not isinstance(page, RenderedPixelPage)
            or page.mime_type not in _IMAGE_MIMES
        ):
            raise IntakeBatchV2Error(
                "rendered_page_invalid", "renderer returned an invalid page", 409
            )
        width, height = _image_size(page.pixels, page.mime_type)
        if width != page.width or height != page.height:
            raise IntakeBatchV2Error(
                "rendered_page_dimensions_mismatch",
                "renderer page dimensions do not match its pixels",
                409,
            )
        _require_sha256(page.render_recipe_sha256, field="render_recipe_sha256")
        validated.append(page)
    return validated


def _prepare_sources(
    *,
    question_files: Sequence[IntakeBatchFile | Mapping[str, Any]],
    answer_files: Sequence[IntakeBatchFile | Mapping[str, Any]],
    handout_files: Sequence[IntakeBatchFile | Mapping[str, Any]],
    renderer: PixelPageRenderer | None,
) -> tuple[list[dict[str, Any]], list[VisualPixelPage]]:
    groups = {
        "question": list(question_files),
        "answer": list(answer_files),
        "handout": list(handout_files),
    }
    total_files = sum(len(items) for items in groups.values())
    if not 1 <= total_files <= _MAX_SOURCE_FILES or not (
        groups["question"] or groups["handout"]
    ):
        raise IntakeBatchV2Error(
            "batch_sources_invalid",
            "batch requires question or handout files within the safe file limit",
        )
    source_records: list[dict[str, Any]] = []
    pages: list[VisualPixelPage] = []
    seen_ids: set[str] = set()
    for source_role in _SOURCE_ROLES:
        for source_order, raw_source in enumerate(groups[source_role], 1):
            source = _coerce_input_file(raw_source)
            source_sha = sha256_bytes(source.content)
            source_file_id = source.source_file_id or (
                f"SRC-{source_role.upper()}-{source_order:03d}-{source_sha[:16]}"
            )
            _require_safe_id(source_file_id, field="source_file_id")
            if source_file_id in seen_ids:
                raise IntakeBatchV2Error(
                    "source_file_id_duplicate", "source file id is duplicated"
                )
            seen_ids.add(source_file_id)
            rendered = _render_source_pages(
                source, source_role=source_role, renderer=renderer
            )
            page_records: list[dict[str, Any]] = []
            for page_number, page in enumerate(rendered, 1):
                page_sha = sha256_bytes(page.pixels)
                visual_page = VisualPixelPage(
                    source_file_id=source_file_id,
                    source_role=source_role,
                    source_order=source_order,
                    page_number=page_number,
                    mime_type=page.mime_type,
                    width=page.width,
                    height=page.height,
                    page_sha256=page_sha,
                    render_recipe_sha256=page.render_recipe_sha256,
                    pixels=page.pixels,
                )
                pages.append(visual_page)
                page_records.append(visual_page.public_manifest())
            source_records.append(
                {
                    "source_file_id": source_file_id,
                    "source_role": source_role,
                    "source_order": source_order,
                    "filename": source.filename,
                    "mime_type": source.mime_type,
                    "size_bytes": len(source.content),
                    "source_file_sha256": source_sha,
                    "pages": page_records,
                }
            )
    if len(pages) > _MAX_PAGES:
        raise IntakeBatchV2Error(
            "batch_page_limit_exceeded", "batch has too many rendered pages"
        )
    return source_records, pages


def _batch_subject(source_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "shchem.intake-batch-source-subject.v2",
        "sources": [deepcopy(dict(source)) for source in source_records],
    }


def _make_shards(
    pages: Sequence[VisualPixelPage], *, batch_id: str, max_pages_per_shard: int
) -> list[VisualShardRequest]:
    if type(max_pages_per_shard) is not int or not 1 <= max_pages_per_shard <= 20:
        raise IntakeBatchV2Error(
            "shard_page_limit_invalid", "visual shard page limit is invalid"
        )
    requests: list[VisualShardRequest] = []
    shard_index = 0
    for source_role in _SOURCE_ROLES:
        role_pages = [page for page in pages if page.source_role == source_role]
        for offset in range(0, len(role_pages), max_pages_per_shard):
            shard_index += 1
            requests.append(
                VisualShardRequest(
                    batch_id=batch_id,
                    shard_id=f"{batch_id}:{source_role}:{shard_index:04d}",
                    shard_index=shard_index,
                    source_role=source_role,
                    pages=tuple(role_pages[offset : offset + max_pages_per_shard]),
                )
            )
    return requests


def _merge_identity(
    identities: Sequence[Mapping[str, Any]], blockers: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not identities:
        blockers.append(
            {
                "code": "paper_identity_missing",
                "message": "No page-bound paper identity candidate was returned.",
                "target_id": "paper",
                "evidence_refs": [],
            }
        )
        return (
            {
                "title": "unknown",
                "source_year": "unknown",
                "source_region_or_school": "unknown",
                "paper_type": "unknown",
                "evidence_refs": [],
            },
            [],
        )
    fields = ("title", "source_year", "source_region_or_school", "paper_type")
    variants = {
        canonical_json_bytes({field: identity[field] for field in fields})
        for identity in identities
    }
    if len(variants) == 1:
        merged = {field: identities[0][field] for field in fields}
        merged["evidence_refs"] = sorted(
            {
                reference
                for identity in identities
                for reference in identity["evidence_refs"]
            }
        )
        return merged, []
    evidence_refs = sorted(
        {
            reference
            for identity in identities
            for reference in identity["evidence_refs"]
        }
    )
    blockers.append(
        {
            "code": "paper_identity_conflict",
            "message": "Visual shards returned conflicting paper identity candidates.",
            "target_id": "paper",
            "evidence_refs": evidence_refs,
        }
    )
    return (
        {
            "title": "unknown",
            "source_year": "unknown",
            "source_region_or_school": "unknown",
            "paper_type": "unknown",
            "evidence_refs": evidence_refs,
        },
        [deepcopy(dict(identity)) for identity in identities],
    )


def _merge_by_id(
    target: dict[str, dict[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    *,
    id_field: str,
    theme_id: str,
    conflicts: list[dict[str, Any]],
) -> None:
    for row_value in rows:
        row = deepcopy(dict(row_value))
        row_id = row[id_field]
        previous = target.get(row_id)
        if previous is None:
            target[row_id] = row
        elif canonical_json_bytes(previous) != canonical_json_bytes(row):
            conflicts.append(
                {
                    "field": id_field,
                    "node_id": row_id,
                    "theme_big_question_id": theme_id,
                    "variants": [deepcopy(previous), row],
                }
            )


def _theme_fragments_to_themes(
    fragments: Sequence[Mapping[str, Any]], blockers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fragment in fragments:
        grouped[fragment["theme_big_question_id"]].append(deepcopy(dict(fragment)))
    themes: list[dict[str, Any]] = []
    for theme_id, group in grouped.items():
        metadata_fields = (
            "theme_number",
            "title",
            "context",
            "sequence_in_paper",
        )
        base_metadata = {field: group[0][field] for field in metadata_fields}
        conflicts: list[dict[str, Any]] = []
        for fragment in group[1:]:
            for field in metadata_fields:
                if canonical_json_bytes(fragment[field]) != canonical_json_bytes(
                    base_metadata[field]
                ):
                    conflicts.append(
                        {
                            "field": field,
                            "node_id": theme_id,
                            "theme_big_question_id": theme_id,
                            "variants": [
                                deepcopy(base_metadata[field]),
                                deepcopy(fragment[field]),
                            ],
                        }
                    )
        shared: dict[str, dict[str, Any]] = {}
        visuals: dict[str, dict[str, Any]] = {}
        dependencies: dict[str, dict[str, Any]] = {}
        printed: dict[str, dict[str, Any]] = {}
        for fragment in group:
            _merge_by_id(
                shared,
                fragment["shared_materials"],
                id_field="shared_material_id",
                theme_id=theme_id,
                conflicts=conflicts,
            )
            _merge_by_id(
                visuals,
                fragment["visual_objects"],
                id_field="visual_object_id",
                theme_id=theme_id,
                conflicts=conflicts,
            )
            _merge_by_id(
                dependencies,
                fragment["dependency_edges"],
                id_field="dependency_edge_id",
                theme_id=theme_id,
                conflicts=conflicts,
            )
            _merge_by_id(
                printed,
                fragment["printed_questions"],
                id_field="printed_question_id",
                theme_id=theme_id,
                conflicts=conflicts,
            )
        positions = {fragment["fragment_position"] for fragment in group}
        complete = "complete" in positions or {"start", "end"} <= positions
        merge_status = (
            "complete" if complete and not conflicts else "blocked_pending_review"
        )
        evidence_refs = sorted(
            {reference for fragment in group for reference in fragment["evidence_refs"]}
        )
        if not complete:
            blockers.append(
                {
                    "code": "theme_fragment_incomplete",
                    "message": "Theme fragments do not contain a complete start/end boundary.",
                    "target_id": theme_id,
                    "evidence_refs": evidence_refs,
                }
            )
        if conflicts:
            blockers.append(
                {
                    "code": "theme_merge_conflict",
                    "message": "Conflicting page-shard variants require teacher review.",
                    "target_id": theme_id,
                    "evidence_refs": evidence_refs,
                }
            )
        themes.append(
            {
                "theme_big_question_id": theme_id,
                **base_metadata,
                "merge_status": merge_status,
                "merge_conflicts": conflicts,
                "shared_materials": sorted(
                    shared.values(), key=lambda item: item["shared_material_id"]
                ),
                "visual_objects": sorted(
                    visuals.values(), key=lambda item: item["visual_object_id"]
                ),
                "dependency_edges": sorted(
                    dependencies.values(), key=lambda item: item["dependency_edge_id"]
                ),
                "printed_questions": sorted(
                    printed.values(), key=lambda item: item["sequence_in_theme"]
                ),
                "evidence_refs": evidence_refs,
            }
        )
    themes.sort(key=lambda item: item["sequence_in_paper"])
    return themes


def _iter_atomics(
    candidate_or_themes: Mapping[str, Any] | Sequence[Mapping[str, Any]],
):
    if isinstance(candidate_or_themes, Mapping):
        themes = candidate_or_themes["paper"]["theme_big_questions"]
    else:
        themes = candidate_or_themes
    for theme in themes:
        for printed in theme["printed_questions"]:
            for atomic in printed["atomic_parts"]:
                yield theme, printed, atomic


def _auto_align_answers(
    themes: list[dict[str, Any]],
    answer_candidates: Sequence[Mapping[str, Any]],
    blockers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    atomic_by_id: dict[str, dict[str, Any]] = {}
    number_part_map: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(
        list
    )
    for _theme, printed, atomic in _iter_atomics(themes):
        atomic["answer"] = _empty_answer()
        atomic_by_id[atomic["atomic_part_id"]] = atomic
        number_part_map[(printed["question_number"], atomic["part_label"])].append(
            atomic
        )
    proposed: dict[str, list[tuple[Mapping[str, Any], str]]] = defaultdict(list)
    unaligned: list[dict[str, Any]] = []
    for answer in answer_candidates:
        target: dict[str, Any] | None = None
        status = ""
        atomic_id = answer["atomic_part_id"]
        if atomic_id is not None and atomic_id in atomic_by_id:
            target = atomic_by_id[atomic_id]
            status = "auto_exact_atomic_id"
        else:
            matches = (
                number_part_map.get(
                    (answer["question_number"], answer["part_label"]), []
                )
                if answer["question_number"] != "unknown"
                else []
            )
            if len(matches) == 1:
                target = matches[0]
                status = "auto_exact_question_and_part"
        if target is None:
            unaligned.append(deepcopy(dict(answer)))
        else:
            proposed[target["atomic_part_id"]].append((answer, status))
    for atomic_id, proposals in proposed.items():
        if len(proposals) == 1:
            answer, status = proposals[0]
            atomic_by_id[atomic_id]["answer"] = _answer_from_candidate(
                answer, alignment_status=status
            )
        else:
            unaligned.extend(deepcopy(dict(answer)) for answer, _status in proposals)
    for answer in unaligned:
        blockers.append(
            {
                "code": "answer_alignment_pending",
                "message": "Independent answer candidate could not be aligned uniquely.",
                "target_id": answer["answer_candidate_id"],
                "evidence_refs": list(answer["evidence_refs"]),
            }
        )
    return sorted(unaligned, key=lambda item: item["answer_candidate_id"])


def _build_candidate(
    *,
    batch_id: str,
    batch_sha256: str,
    source_records: Sequence[Mapping[str, Any]],
    requests: Sequence[VisualShardRequest],
    fragments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    identities: list[dict[str, Any]] = []
    theme_fragments: list[dict[str, Any]] = []
    answer_candidates: list[dict[str, Any]] = []
    answer_ids: set[str] = set()
    for fragment in fragments:
        for evidence in fragment["evidence"]:
            evidence_id = evidence["evidence_id"]
            previous = evidence_by_id.get(evidence_id)
            if previous is not None and canonical_json_bytes(
                previous
            ) != canonical_json_bytes(evidence):
                raise IntakeBatchV2Error(
                    "provider_output_evidence_conflict",
                    "evidence id has conflicting page anchors across shards",
                    502,
                )
            evidence_by_id[evidence_id] = deepcopy(evidence)
        if fragment["paper_identity"] is not None:
            identities.append(deepcopy(fragment["paper_identity"]))
        theme_fragments.extend(deepcopy(fragment["theme_fragments"]))
        for answer in fragment["answer_candidates"]:
            answer_id = answer["answer_candidate_id"]
            if answer_id in answer_ids:
                raise IntakeBatchV2Error(
                    "provider_output_answer_id_duplicate",
                    "answer candidate id is duplicated",
                    502,
                )
            answer_ids.add(answer_id)
            answer_candidates.append(deepcopy(answer))
        for warning in fragment["warnings"]:
            blockers.append(
                {
                    "code": "visual_fragment_warning",
                    "message": warning,
                    "target_id": fragment["shard_id"],
                    "evidence_refs": [],
                }
            )
    identity, identity_conflicts = _merge_identity(identities, blockers)
    themes = _theme_fragments_to_themes(theme_fragments, blockers)
    if not themes:
        blockers.append(
            {
                "code": "question_hierarchy_missing",
                "message": "No paper/theme/printed/atomic hierarchy was returned.",
                "target_id": "paper",
                "evidence_refs": [],
            }
        )
    unaligned = _auto_align_answers(themes, answer_candidates, blockers)
    receipts = []
    for request, fragment in zip(requests, fragments, strict=True):
        request_manifest = {
            "schema_version": INTAKE_BATCH_VISUAL_REQUEST_V2_SCHEMA_VERSION,
            "batch_id": request.batch_id,
            "shard_id": request.shard_id,
            "shard_index": request.shard_index,
            "source_role": request.source_role,
            "input_mode": DIRECT_PAGE_PIXEL_MODE,
            "pages": [page.public_manifest() for page in request.pages],
        }
        receipts.append(
            {
                "shard_id": request.shard_id,
                "shard_index": request.shard_index,
                "source_role": request.source_role,
                "page_count": len(request.pages),
                "request_manifest_sha256": sha256_bytes(
                    canonical_json_bytes(request_manifest)
                ),
                "fragment_sha256": sha256_bytes(canonical_json_bytes(fragment)),
            }
        )
    candidate = {
        "schema_version": INTAKE_BATCH_CANDIDATE_V2_SCHEMA_VERSION,
        "candidate_status": CANDIDATE_STATUS,
        "recognition_mode": DIRECT_PAGE_PIXEL_MODE,
        "input_contract": {
            "source_text_layer_supplied": False,
            "fallback_allowed": False,
            "provider_observed_only_page_pixels": True,
        },
        "batch": {
            "batch_id": batch_id,
            "batch_sha256": batch_sha256,
            "sources": [deepcopy(dict(source)) for source in source_records],
            "question_source_order": [
                source["source_file_id"]
                for source in source_records
                if source["source_role"] == "question"
            ],
            "answer_source_order": [
                source["source_file_id"]
                for source in source_records
                if source["source_role"] == "answer"
            ],
            "handout_source_order": [
                source["source_file_id"]
                for source in source_records
                if source["source_role"] == "handout"
            ],
            "shard_receipts": receipts,
        },
        "evidence": sorted(
            evidence_by_id.values(), key=lambda item: item["evidence_id"]
        ),
        "paper": {
            "paper_id": f"PAPER-{batch_sha256[:24]}",
            **identity,
            "identity_conflicts": identity_conflicts,
            "theme_big_questions": themes,
        },
        "unaligned_answer_candidates": unaligned,
        "review_blockers": blockers,
        "teacher_operations": [],
        "requires_teacher_review": True,
        "human_reviewed": False,
        "retrieval_ready": False,
        "publication_allowed": False,
        "central_question_bank_write": False,
    }
    return validate_candidate_v2(candidate)


def _validate_answer_final(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "status",
            "answer_candidate_id",
            "answer_body",
            "analysis",
            "max_score",
            "scoring_points",
            "chemical_expressions",
            "authority",
            "independently_verified",
            "alignment",
            "evidence_refs",
        },
        code="candidate_schema_invalid",
    )
    if row["status"] not in {"missing", "source_answer_candidate"}:
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "answer status is invalid", 409
        )
    if row["answer_candidate_id"] is not None:
        _require_safe_id(row["answer_candidate_id"], field="answer_candidate_id")
    if not isinstance(row["answer_body"], str) or not isinstance(row["analysis"], str):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "answer text fields are invalid", 409
        )
    if (
        isinstance(row["max_score"], bool)
        or not isinstance(row["max_score"], (int, float))
        or not 0 <= row["max_score"] <= 1000
    ):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "answer max score is invalid", 409
        )
    if not isinstance(row["scoring_points"], list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "answer scoring points are invalid", 409
        )
    for point in row["scoring_points"]:
        _validate_scoring_point(point, known_evidence)
    if (
        sum(float(point["score"]) for point in row["scoring_points"])
        > float(row["max_score"]) + 1e-9
    ):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "answer scoring exceeds max score", 409
        )
    if not isinstance(row["chemical_expressions"], list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "answer chemistry is invalid", 409
        )
    for expression in row["chemical_expressions"]:
        _validate_chemical_expression(expression, known_evidence)
    if (
        row["authority"]
        not in {
            "nonofficial_reference",
            "teacher_material_reference",
            "unknown",
        }
        or row["independently_verified"] is not False
    ):
        raise IntakeBatchV2Error(
            "candidate_authority_escalation",
            "candidate answer authority is invalid",
            409,
        )
    alignment = _require_exact_keys(
        row["alignment"],
        required={"status", "confidence", "evidence_refs"},
        code="candidate_schema_invalid",
    )
    if alignment["status"] not in {
        "missing",
        "auto_exact_atomic_id",
        "auto_exact_question_and_part",
        "teacher_confirmed",
    }:
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "answer alignment status is invalid", 409
        )
    confidence = alignment["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "answer alignment confidence is invalid", 409
        )
    _validate_evidence_refs(alignment["evidence_refs"], known_evidence, require=False)
    _validate_evidence_refs(row["evidence_refs"], known_evidence, require=False)
    if row["status"] == "missing" and (
        row["answer_candidate_id"] is not None
        or row["answer_body"]
        or row["analysis"]
        or row["max_score"] != 0
        or row["scoring_points"]
        or row["chemical_expressions"]
        or row["authority"] != "unknown"
        or row["alignment"]["status"] != "missing"
        or row["evidence_refs"]
    ):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid",
            "missing answer contains fabricated content",
            409,
        )
    if row["status"] == "source_answer_candidate" and (
        row["answer_candidate_id"] is None
        or not row["answer_body"]
        or not row["evidence_refs"]
        or row["alignment"]["status"] == "missing"
    ):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid",
            "source answer candidate lacks content, evidence, or alignment",
            409,
        )
    return deepcopy(row)


def _validate_final_atomic(value: Any, known_evidence: set[str]) -> dict[str, Any]:
    row = _require_exact_keys(
        value,
        required={
            "atomic_part_id",
            "part_label",
            "sequence_in_printed",
            "stem",
            "options",
            "response_requirements",
            "chemical_expressions",
            "visual_object_refs",
            "curriculum",
            "classification",
            "cognitive_difficulty",
            "answer",
            "evidence_refs",
        },
        code="candidate_schema_invalid",
    )
    source_shape = {
        key: deepcopy(value) for key, value in row.items() if key != "answer"
    }
    _validate_atomic(source_shape, known_evidence)
    _validate_answer_final(row["answer"], known_evidence)
    return deepcopy(row)


def _validate_source_records(
    batch: Mapping[str, Any],
) -> dict[tuple[str, int], dict[str, Any]]:
    sources = batch.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= _MAX_SOURCE_FILES:
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "candidate batch sources are invalid", 409
        )
    page_lookup: dict[tuple[str, int], dict[str, Any]] = {}
    source_ids: set[str] = set()
    observed_order: dict[str, list[str]] = {role: [] for role in _SOURCE_ROLES}
    expected_order: dict[str, int] = {role: 1 for role in _SOURCE_ROLES}
    for source_value in sources:
        source = _require_exact_keys(
            source_value,
            required={
                "source_file_id",
                "source_role",
                "source_order",
                "filename",
                "mime_type",
                "size_bytes",
                "source_file_sha256",
                "pages",
            },
            code="candidate_schema_invalid",
        )
        source_id = _require_safe_id(source["source_file_id"], field="source_file_id")
        if source_id in source_ids or source["source_role"] not in _SOURCE_ROLES:
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "candidate source identity is invalid", 409
            )
        source_ids.add(source_id)
        role = source["source_role"]
        if source["source_order"] != expected_order[role]:
            raise IntakeBatchV2Error(
                "candidate_schema_invalid",
                "candidate source order is not contiguous",
                409,
            )
        expected_order[role] += 1
        observed_order[role].append(source_id)
        _validate_filename(source["filename"])
        if source["mime_type"] not in _ALLOWED_MIMES:
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "candidate source MIME is invalid", 409
            )
        if type(source["size_bytes"]) is not int or source["size_bytes"] < 1:
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "candidate source size is invalid", 409
            )
        _require_sha256(source["source_file_sha256"], field="source_file_sha256")
        pages = source["pages"]
        if not isinstance(pages, list) or not pages:
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "candidate source pages are invalid", 409
            )
        for expected_page, page_value in enumerate(pages, 1):
            page = _require_exact_keys(
                page_value,
                required={
                    "source_file_id",
                    "source_role",
                    "source_order",
                    "page_number",
                    "mime_type",
                    "width",
                    "height",
                    "size_bytes",
                    "page_sha256",
                    "render_recipe_sha256",
                },
                code="candidate_schema_invalid",
            )
            if (
                page["source_file_id"] != source_id
                or page["source_role"] != role
                or page["source_order"] != source["source_order"]
                or page["page_number"] != expected_page
                or page["mime_type"] not in _IMAGE_MIMES
                or type(page["width"]) is not int
                or type(page["height"]) is not int
                or not 1 <= page["width"] <= _MAX_IMAGE_EDGE
                or not 1 <= page["height"] <= _MAX_IMAGE_EDGE
                or type(page["size_bytes"]) is not int
                or not 1 <= page["size_bytes"] <= _MAX_PAGE_BYTES
            ):
                raise IntakeBatchV2Error(
                    "candidate_schema_invalid",
                    "candidate page descriptor is invalid",
                    409,
                )
            _require_sha256(page["page_sha256"], field="page_sha256")
            _require_sha256(page["render_recipe_sha256"], field="render_recipe_sha256")
            page_lookup[(source_id, expected_page)] = deepcopy(page)
    for role, field in (
        ("question", "question_source_order"),
        ("answer", "answer_source_order"),
        ("handout", "handout_source_order"),
    ):
        if batch.get(field) != observed_order[role]:
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", f"{field} is invalid", 409
            )
    return page_lookup


def validate_candidate_v2(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate hierarchy, provenance, answer, and candidate-only gates."""

    row = _require_exact_keys(
        candidate,
        required={
            "schema_version",
            "candidate_status",
            "recognition_mode",
            "input_contract",
            "batch",
            "evidence",
            "paper",
            "unaligned_answer_candidates",
            "review_blockers",
            "teacher_operations",
            "requires_teacher_review",
            "human_reviewed",
            "retrieval_ready",
            "publication_allowed",
            "central_question_bank_write",
        },
        code="candidate_schema_invalid",
    )
    if (
        row["schema_version"] != INTAKE_BATCH_CANDIDATE_V2_SCHEMA_VERSION
        or row["candidate_status"] != CANDIDATE_STATUS
        or row["recognition_mode"] != DIRECT_PAGE_PIXEL_MODE
        or row["requires_teacher_review"] is not True
        or row["human_reviewed"] is not False
        or row["retrieval_ready"] is not False
        or row["publication_allowed"] is not False
        or row["central_question_bank_write"] is not False
    ):
        raise IntakeBatchV2Error(
            "candidate_authority_escalation",
            "candidate-only protected gates are invalid",
            409,
        )
    input_contract = _require_exact_keys(
        row["input_contract"],
        required={
            "source_text_layer_supplied",
            "fallback_allowed",
            "provider_observed_only_page_pixels",
        },
        code="candidate_schema_invalid",
    )
    if input_contract != {
        "source_text_layer_supplied": False,
        "fallback_allowed": False,
        "provider_observed_only_page_pixels": True,
    }:
        raise IntakeBatchV2Error(
            "candidate_input_contract_invalid",
            "candidate violates the direct-page-pixel input contract",
            409,
        )
    batch = _require_exact_keys(
        row["batch"],
        required={
            "batch_id",
            "batch_sha256",
            "sources",
            "question_source_order",
            "answer_source_order",
            "handout_source_order",
            "shard_receipts",
        },
        code="candidate_schema_invalid",
    )
    _require_safe_id(batch["batch_id"], field="batch_id")
    _require_sha256(batch["batch_sha256"], field="batch_sha256")
    page_lookup = _validate_source_records(batch)
    if batch["batch_sha256"] != sha256_bytes(
        canonical_json_bytes(_batch_subject(batch["sources"]))
    ):
        raise IntakeBatchV2Error(
            "candidate_batch_hash_mismatch",
            "candidate batch source closure hash is invalid",
            409,
        )
    receipts = batch["shard_receipts"]
    if not isinstance(receipts, list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "candidate shard receipts are invalid", 409
        )
    for index, receipt_value in enumerate(receipts, 1):
        receipt = _require_exact_keys(
            receipt_value,
            required={
                "shard_id",
                "shard_index",
                "source_role",
                "page_count",
                "request_manifest_sha256",
                "fragment_sha256",
            },
            code="candidate_schema_invalid",
        )
        if (
            receipt["shard_index"] != index
            or receipt["source_role"] not in _SOURCE_ROLES
            or type(receipt["page_count"]) is not int
            or receipt["page_count"] < 1
        ):
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "candidate shard receipt is invalid", 409
            )
        if (
            not isinstance(receipt["shard_id"], str)
            or len(receipt["shard_id"]) > 220
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", receipt["shard_id"]) is None
        ):
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "candidate shard id is invalid", 409
            )
        _require_sha256(
            receipt["request_manifest_sha256"], field="request_manifest_sha256"
        )
        _require_sha256(receipt["fragment_sha256"], field="fragment_sha256")
    evidence_values = row["evidence"]
    if not isinstance(evidence_values, list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "candidate evidence is invalid", 409
        )
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for evidence_value in evidence_values:
        evidence = _require_exact_keys(
            evidence_value,
            required={
                "evidence_id",
                "source_file_id",
                "source_role",
                "page_number",
                "page_sha256",
                "bbox",
            },
            code="candidate_schema_invalid",
        )
        evidence_id = _require_safe_id(evidence["evidence_id"], field="evidence_id")
        if evidence_id in evidence_by_id:
            raise IntakeBatchV2Error(
                "candidate_evidence_duplicate",
                "candidate evidence id is duplicated",
                409,
            )
        page = page_lookup.get((evidence["source_file_id"], evidence["page_number"]))
        if (
            page is None
            or page["source_role"] != evidence["source_role"]
            or page["page_sha256"] != evidence["page_sha256"]
        ):
            raise IntakeBatchV2Error(
                "candidate_evidence_hash_mismatch",
                "candidate evidence does not bind to a source page hash",
                409,
            )
        bbox = _require_exact_keys(
            evidence["bbox"],
            required={"x", "y", "width", "height"},
            code="candidate_schema_invalid",
        )
        if any(
            isinstance(bbox[key], bool) or not isinstance(bbox[key], (int, float))
            for key in ("x", "y", "width", "height")
        ) or (
            not 0 <= bbox["x"] < 1
            or not 0 <= bbox["y"] < 1
            or not 0 < bbox["width"] <= 1
            or not 0 < bbox["height"] <= 1
            or bbox["x"] + bbox["width"] > 1.000001
            or bbox["y"] + bbox["height"] > 1.000001
        ):
            raise IntakeBatchV2Error(
                "candidate_evidence_bbox_invalid",
                "candidate evidence bbox is invalid",
                409,
            )
        evidence_by_id[evidence_id] = deepcopy(evidence)
    known_evidence = set(evidence_by_id)
    paper = _require_exact_keys(
        row["paper"],
        required={
            "paper_id",
            "title",
            "source_year",
            "source_region_or_school",
            "paper_type",
            "evidence_refs",
            "identity_conflicts",
            "theme_big_questions",
        },
        code="candidate_schema_invalid",
    )
    _require_safe_id(paper["paper_id"], field="paper_id")
    for key in ("title", "source_region_or_school", "paper_type"):
        _require_text(paper[key], field=key, maximum=1000)
    if paper["source_year"] != "unknown" and (
        type(paper["source_year"]) is not int
        or not 1900 <= paper["source_year"] <= 2200
    ):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "candidate source year is invalid", 409
        )
    _validate_evidence_refs(
        paper["evidence_refs"], known_evidence, require=paper["title"] != "unknown"
    )
    if not isinstance(paper["identity_conflicts"], list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "paper identity conflicts are invalid", 409
        )
    for conflict in paper["identity_conflicts"]:
        _validate_paper_identity(conflict, known_evidence)
    themes = paper["theme_big_questions"]
    if not isinstance(themes, list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "theme hierarchy is invalid", 409
        )
    if [theme.get("sequence_in_paper") for theme in themes] != list(
        range(1, len(themes) + 1)
    ):
        raise IntakeBatchV2Error(
            "candidate_parent_chain_invalid", "theme sequence is not contiguous", 409
        )
    theme_ids: set[str] = set()
    printed_ids: set[str] = set()
    atomic_ids: set[str] = set()
    aligned_answer_ids: set[str] = set()
    for theme_value in themes:
        theme = _require_exact_keys(
            theme_value,
            required={
                "theme_big_question_id",
                "theme_number",
                "title",
                "context",
                "sequence_in_paper",
                "merge_status",
                "merge_conflicts",
                "shared_materials",
                "visual_objects",
                "dependency_edges",
                "printed_questions",
                "evidence_refs",
            },
            code="candidate_schema_invalid",
        )
        theme_id = _require_safe_id(
            theme["theme_big_question_id"], field="theme_big_question_id"
        )
        if theme_id in theme_ids or theme["merge_status"] not in {
            "complete",
            "blocked_pending_review",
        }:
            raise IntakeBatchV2Error(
                "candidate_parent_chain_invalid",
                "theme identity/status is invalid",
                409,
            )
        theme_ids.add(theme_id)
        _require_text(theme["theme_number"], field="theme number", maximum=80)
        _require_text(theme["title"], field="theme title", maximum=1000)
        _require_text(theme["context"], field="theme context")
        _validate_evidence_refs(theme["evidence_refs"], known_evidence)
        if not isinstance(theme["merge_conflicts"], list):
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "theme merge conflicts are invalid", 409
            )
        if theme["merge_status"] == "complete" and theme["merge_conflicts"]:
            raise IntakeBatchV2Error(
                "candidate_schema_invalid",
                "complete theme has unresolved conflicts",
                409,
            )
        shared_ids: set[str] = set()
        for shared in theme["shared_materials"]:
            validated_shared = _validate_shared_material(shared, known_evidence)
            shared_id = validated_shared["shared_material_id"]
            if shared_id in shared_ids:
                raise IntakeBatchV2Error(
                    "candidate_parent_chain_invalid",
                    "shared material id is duplicated",
                    409,
                )
            shared_ids.add(shared_id)
        visual_ids: set[str] = set()
        for visual in theme["visual_objects"]:
            validated_visual = _validate_visual_object(visual, known_evidence)
            visual_id = validated_visual["visual_object_id"]
            if visual_id in visual_ids:
                raise IntakeBatchV2Error(
                    "candidate_parent_chain_invalid",
                    "visual object id is duplicated",
                    409,
                )
            visual_ids.add(visual_id)
        printed_questions = theme["printed_questions"]
        if not isinstance(printed_questions, list) or not printed_questions:
            raise IntakeBatchV2Error(
                "candidate_parent_chain_invalid", "theme has no printed questions", 409
            )
        if [item.get("sequence_in_theme") for item in printed_questions] != list(
            range(1, len(printed_questions) + 1)
        ):
            raise IntakeBatchV2Error(
                "candidate_parent_chain_invalid",
                "printed question sequence is not contiguous",
                409,
            )
        theme_atomic_sequence: dict[str, int] = {}
        sequence_cursor = 0
        for printed_value in printed_questions:
            printed = _require_exact_keys(
                printed_value,
                required={
                    "printed_question_id",
                    "question_number",
                    "sequence_in_theme",
                    "stem",
                    "options",
                    "response_requirements",
                    "chemical_expressions",
                    "shared_material_refs",
                    "visual_object_refs",
                    "atomic_parts",
                    "evidence_refs",
                },
                code="candidate_schema_invalid",
            )
            printed_id = _require_safe_id(
                printed["printed_question_id"], field="printed_question_id"
            )
            if printed_id in printed_ids:
                raise IntakeBatchV2Error(
                    "candidate_parent_chain_invalid", "printed id is duplicated", 409
                )
            printed_ids.add(printed_id)
            _require_text(
                printed["question_number"], field="question number", maximum=80
            )
            _require_text(printed["stem"], field="printed stem", allow_empty=True)
            if not isinstance(printed["options"], list):
                raise IntakeBatchV2Error(
                    "candidate_schema_invalid", "printed options are invalid", 409
                )
            for option in printed["options"]:
                _validate_option(option, known_evidence)
            _require_text(
                printed["response_requirements"],
                field="printed response requirements",
                allow_empty=True,
            )
            if not isinstance(printed["chemical_expressions"], list):
                raise IntakeBatchV2Error(
                    "candidate_schema_invalid", "printed chemistry is invalid", 409
                )
            for expression in printed["chemical_expressions"]:
                _validate_chemical_expression(expression, known_evidence)
            shared_refs = _require_string_list(
                printed["shared_material_refs"], field="shared material refs"
            )
            visual_refs = _require_string_list(
                printed["visual_object_refs"], field="printed visual refs"
            )
            if any(reference not in shared_ids for reference in shared_refs) or any(
                reference not in visual_ids for reference in visual_refs
            ):
                raise IntakeBatchV2Error(
                    "candidate_parent_chain_invalid",
                    "printed question has an invalid shared/visual reference",
                    409,
                )
            _validate_evidence_refs(printed["evidence_refs"], known_evidence)
            atomics = printed["atomic_parts"]
            if not isinstance(atomics, list) or not atomics:
                raise IntakeBatchV2Error(
                    "candidate_parent_chain_invalid",
                    "printed question has no atomic parts",
                    409,
                )
            if [item.get("sequence_in_printed") for item in atomics] != list(
                range(1, len(atomics) + 1)
            ):
                raise IntakeBatchV2Error(
                    "candidate_parent_chain_invalid",
                    "atomic sequence is not contiguous",
                    409,
                )
            for atomic_value in atomics:
                atomic = _validate_final_atomic(atomic_value, known_evidence)
                atomic_id = atomic["atomic_part_id"]
                if atomic_id in atomic_ids:
                    raise IntakeBatchV2Error(
                        "candidate_parent_chain_invalid", "atomic id is duplicated", 409
                    )
                atomic_ids.add(atomic_id)
                sequence_cursor += 1
                theme_atomic_sequence[atomic_id] = sequence_cursor
                if any(
                    reference not in visual_ids
                    for reference in atomic["visual_object_refs"]
                ):
                    raise IntakeBatchV2Error(
                        "candidate_parent_chain_invalid",
                        "atomic part has an invalid visual reference",
                        409,
                    )
                answer_id = atomic["answer"]["answer_candidate_id"]
                if answer_id is not None:
                    if answer_id in aligned_answer_ids:
                        raise IntakeBatchV2Error(
                            "candidate_answer_duplicate",
                            "answer candidate is aligned more than once",
                            409,
                        )
                    aligned_answer_ids.add(answer_id)
        dependencies = theme["dependency_edges"]
        if not isinstance(dependencies, list):
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "theme dependencies are invalid", 409
            )
        dependency_ids: set[str] = set()
        pairs: set[tuple[str, str, str]] = set()
        for dependency_value in dependencies:
            dependency = _validate_dependency(dependency_value, known_evidence)
            dependency_id = dependency["dependency_edge_id"]
            source_id = dependency["from_atomic_part_id"]
            target_id = dependency["to_atomic_part_id"]
            pair = (source_id, target_id, dependency["relation"])
            if (
                dependency_id in dependency_ids
                or pair in pairs
                or source_id not in theme_atomic_sequence
                or target_id not in theme_atomic_sequence
                or theme_atomic_sequence[source_id] >= theme_atomic_sequence[target_id]
            ):
                raise IntakeBatchV2Error(
                    "candidate_dependency_invalid",
                    "dependency must be unique, same-theme, and strictly forward",
                    409,
                )
            dependency_ids.add(dependency_id)
            pairs.add(pair)
    unaligned = row["unaligned_answer_candidates"]
    if not isinstance(unaligned, list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "unaligned answers are invalid", 409
        )
    unaligned_ids: set[str] = set()
    for answer_value in unaligned:
        answer = _validate_answer_candidate(answer_value, known_evidence)
        answer_id = answer["answer_candidate_id"]
        if answer_id in unaligned_ids or answer_id in aligned_answer_ids:
            raise IntakeBatchV2Error(
                "candidate_answer_duplicate",
                "answer candidate id is duplicated or both aligned and unaligned",
                409,
            )
        unaligned_ids.add(answer_id)
    blockers = row["review_blockers"]
    if not isinstance(blockers, list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "review blockers are invalid", 409
        )
    for blocker_value in blockers:
        blocker = _require_exact_keys(
            blocker_value,
            required={"code", "message", "target_id", "evidence_refs"},
            code="candidate_schema_invalid",
        )
        _require_text(blocker["code"], field="blocker code", maximum=160)
        _require_text(blocker["message"], field="blocker message", maximum=5000)
        _require_text(blocker["target_id"], field="blocker target", maximum=160)
        _validate_evidence_refs(blocker["evidence_refs"], known_evidence, require=False)
    operations = row["teacher_operations"]
    if not isinstance(operations, list):
        raise IntakeBatchV2Error(
            "candidate_schema_invalid", "teacher operations are invalid", 409
        )
    for expected_sequence, operation_value in enumerate(operations, 1):
        operation = _require_exact_keys(
            operation_value,
            required={
                "sequence",
                "operation",
                "actor_id",
                "target_ids",
                "base_candidate_sha256",
                "base_revision_token",
            },
            code="candidate_schema_invalid",
        )
        if operation["sequence"] != expected_sequence:
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "teacher operation sequence is invalid", 409
            )
        _require_text(operation["operation"], field="teacher operation", maximum=160)
        _require_actor(operation["actor_id"])
        _require_string_list(
            operation["target_ids"],
            field="teacher operation targets",
            allow_empty=False,
        )
        _require_sha256(
            operation["base_candidate_sha256"], field="base_candidate_sha256"
        )
        if (
            not isinstance(operation["base_revision_token"], str)
            or _REVISION_TOKEN.fullmatch(operation["base_revision_token"]) is None
        ):
            raise IntakeBatchV2Error(
                "candidate_schema_invalid", "teacher operation revision is invalid", 409
            )
    return deepcopy(row)


class MultiFileVisualIntakeV2:
    """Render, shard, visually inspect, and merge one candidate-only batch."""

    def __init__(
        self,
        provider: VisualShardProvider
        | Callable[[VisualShardRequest], Mapping[str, Any] | bytes | str],
        *,
        renderer: PixelPageRenderer | None = None,
        max_pages_per_shard: int = 2,
    ) -> None:
        if not (
            callable(provider)
            or callable(getattr(provider, "analyze_shard", None))
            or callable(getattr(provider, "analyze", None))
        ):
            raise IntakeBatchV2Error(
                "visual_provider_invalid", "visual shard provider is invalid", 503
            )
        if type(max_pages_per_shard) is not int or not 1 <= max_pages_per_shard <= 20:
            raise IntakeBatchV2Error(
                "shard_page_limit_invalid", "visual shard page limit is invalid"
            )
        self.provider = provider
        self.renderer = renderer
        self.max_pages_per_shard = max_pages_per_shard

    def process(
        self,
        *,
        question_files: Sequence[IntakeBatchFile | Mapping[str, Any]],
        answer_files: Sequence[IntakeBatchFile | Mapping[str, Any]] = (),
        handout_files: Sequence[IntakeBatchFile | Mapping[str, Any]] = (),
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        source_records, pages = _prepare_sources(
            question_files=question_files,
            answer_files=answer_files,
            handout_files=handout_files,
            renderer=self.renderer,
        )
        batch_sha = sha256_bytes(canonical_json_bytes(_batch_subject(source_records)))
        effective_batch_id = batch_id or f"INTBATCH-{batch_sha[:32]}"
        _require_safe_id(effective_batch_id, field="batch_id")
        requests = _make_shards(
            pages,
            batch_id=effective_batch_id,
            max_pages_per_shard=self.max_pages_per_shard,
        )
        fragments = []
        for request in requests:
            response = _invoke_provider(self.provider, request)
            fragments.append(_validate_fragment(response, request=request))
        return _build_candidate(
            batch_id=effective_batch_id,
            batch_sha256=batch_sha,
            source_records=source_records,
            requests=requests,
            fragments=fragments,
        )

    def process_to_cas(
        self,
        *,
        question_files: Sequence[IntakeBatchFile | Mapping[str, Any]],
        answer_files: Sequence[IntakeBatchFile | Mapping[str, Any]] = (),
        handout_files: Sequence[IntakeBatchFile | Mapping[str, Any]] = (),
        batch_id: str | None = None,
        cas_root: str | Path | None = None,
    ) -> CandidateCAS:
        candidate = self.process(
            question_files=question_files,
            answer_files=answer_files,
            handout_files=handout_files,
            batch_id=batch_id,
        )
        return CandidateCAS(candidate, root=cas_root)


IntakeBatchV2 = MultiFileVisualIntakeV2


def _deep_merge_json(base: Any, patch: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(patch, Mapping):
        result = deepcopy(dict(base))
        for key, value in patch.items():
            result[key] = _deep_merge_json(result.get(key), value)
        return result
    return deepcopy(patch)


def _find_atomic_location(
    candidate: Mapping[str, Any], atomic_part_id: str
) -> tuple[dict[str, Any], dict[str, Any], int, dict[str, Any]]:
    matches = []
    for theme in candidate["paper"]["theme_big_questions"]:
        for printed in theme["printed_questions"]:
            for index, atomic in enumerate(printed["atomic_parts"]):
                if atomic["atomic_part_id"] == atomic_part_id:
                    matches.append((theme, printed, index, atomic))
    if len(matches) != 1:
        raise IntakeBatchV2Error(
            "atomic_part_not_found", "atomic part was not found", 404
        )
    return matches[0]


def _find_printed_location(
    candidate: Mapping[str, Any], printed_question_id: str
) -> tuple[dict[str, Any], int, dict[str, Any]]:
    matches = []
    for theme in candidate["paper"]["theme_big_questions"]:
        for index, printed in enumerate(theme["printed_questions"]):
            if printed["printed_question_id"] == printed_question_id:
                matches.append((theme, index, printed))
    if len(matches) != 1:
        raise IntakeBatchV2Error(
            "printed_question_not_found", "printed question was not found", 404
        )
    return matches[0]


def _find_theme_location(
    candidate: Mapping[str, Any], theme_big_question_id: str
) -> tuple[int, dict[str, Any]]:
    matches = [
        (index, theme)
        for index, theme in enumerate(candidate["paper"]["theme_big_questions"])
        if theme["theme_big_question_id"] == theme_big_question_id
    ]
    if len(matches) != 1:
        raise IntakeBatchV2Error(
            "theme_big_question_not_found", "theme big question was not found", 404
        )
    return matches[0]


def _all_node_ids(candidate: Mapping[str, Any]) -> set[str]:
    result = {candidate["paper"]["paper_id"]}
    for theme in candidate["paper"]["theme_big_questions"]:
        result.add(theme["theme_big_question_id"])
        result.update(item["shared_material_id"] for item in theme["shared_materials"])
        result.update(item["visual_object_id"] for item in theme["visual_objects"])
        result.update(item["dependency_edge_id"] for item in theme["dependency_edges"])
        for printed in theme["printed_questions"]:
            result.add(printed["printed_question_id"])
            result.update(item["atomic_part_id"] for item in printed["atomic_parts"])
    return result


def _stable_union(values: Sequence[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[bytes] = set()
    for value in values:
        marker = canonical_json_bytes(value)
        if marker not in seen:
            seen.add(marker)
            result.append(deepcopy(value))
    return result


def _retarget_blockers(
    candidate: dict[str, Any], old_ids: set[str], replacement_id: str
) -> None:
    for blocker in candidate["review_blockers"]:
        if blocker["target_id"] in old_ids:
            blocker["target_id"] = replacement_id


def _dedupe_dependencies(edges: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    ids: set[str] = set()
    for value in edges:
        edge = deepcopy(dict(value))
        if edge["from_atomic_part_id"] == edge["to_atomic_part_id"]:
            continue
        key = (
            edge["from_atomic_part_id"],
            edge["to_atomic_part_id"],
            edge["relation"],
        )
        if key in seen:
            continue
        seen.add(key)
        if edge["dependency_edge_id"] in ids:
            edge["dependency_edge_id"] = (
                "DEP-"
                + sha256_bytes(
                    canonical_json_bytes({"key": key, "ordinal": len(result)})
                )[:24]
            )
        ids.add(edge["dependency_edge_id"])
        result.append(edge)
    return result


def _revision_token(sequence: int, event_head_sha256: str) -> str:
    return f"rev_{sequence:08d}_{event_head_sha256}"


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise IntakeBatchV2Error(
            "candidate_cas_write_failed", "candidate CAS could not be written", 503
        ) from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_strict_json_file(
    path: Path, *, maximum: int = 64 * 1024 * 1024
) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise IntakeBatchV2Error(
            "candidate_cas_corrupt", "candidate CAS artifact is missing", 409
        ) from None
    if not raw or len(raw) > maximum:
        raise IntakeBatchV2Error(
            "candidate_cas_corrupt", "candidate CAS artifact size is invalid", 409
        )
    try:
        value = strict_json_loads(raw)
    except IntakeBatchV2Error:
        raise IntakeBatchV2Error(
            "candidate_cas_corrupt", "candidate CAS artifact is invalid", 409
        ) from None
    if not isinstance(value, dict):
        raise IntakeBatchV2Error(
            "candidate_cas_corrupt", "candidate CAS artifact is invalid", 409
        )
    return value


def _root_thread_lock(root: Path) -> threading.RLock:
    with _CAS_ROOT_LOCKS_GUARD:
        lock = _CAS_ROOT_LOCKS.get(root)
        if lock is None:
            lock = threading.RLock()
            _CAS_ROOT_LOCKS[root] = lock
        return lock


@contextmanager
def _cas_write_lock(root: Path | None):
    if root is None:
        yield
        return
    thread_lock = _root_thread_lock(root)
    with thread_lock:
        lock_path = root / ".candidate-cas-v2.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        stream = os.fdopen(descriptor, "r+b", closefd=True)
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            yield
        except OSError:
            raise IntakeBatchV2Error(
                "candidate_cas_lock_failed",
                "candidate CAS lock could not be acquired",
                503,
            ) from None
        finally:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            stream.close()


class CandidateCAS:
    """Immutable candidate objects plus hash-chained, ABA-safe teacher revisions."""

    def __init__(
        self,
        initial_candidate: Mapping[str, Any],
        *,
        root: str | Path | None = None,
    ) -> None:
        candidate = validate_candidate_v2(initial_candidate)
        if candidate["teacher_operations"]:
            raise IntakeBatchV2Error(
                "candidate_initial_revision_invalid",
                "initial candidate cannot contain teacher operations",
            )
        self._lock = threading.RLock()
        self.root = Path(root).resolve() if root is not None else None
        self._objects: dict[str, bytes] = {}
        self._events: list[dict[str, Any]] = []
        self._idempotency: dict[str, dict[str, Any]] = {}
        initial_hash = candidate_sha256(candidate)
        initial_head = sha256_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "shchem.intake-batch-initial-head.v2",
                    "batch_id": candidate["batch"]["batch_id"],
                    "initial_candidate_sha256": initial_hash,
                }
            )
        )
        self._initial_candidate_sha256 = initial_hash
        self._current_candidate_sha256 = initial_hash
        self._event_head_sha256 = initial_head
        self._revision_sequence = 0
        self._revision_token = _revision_token(0, initial_head)
        if self.root is not None:
            if self.root.exists() and any(self.root.iterdir()):
                raise IntakeBatchV2Error(
                    "candidate_cas_exists",
                    "candidate CAS root already contains state; use CandidateCAS.open",
                    409,
                )
            self.root.mkdir(parents=True, exist_ok=True)
        self._store_object(candidate)
        self._persist_head()

    @classmethod
    def open(cls, root: str | Path) -> CandidateCAS:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise IntakeBatchV2Error(
                "candidate_cas_missing", "candidate CAS root is missing", 404
            )
        instance = cls.__new__(cls)
        instance._lock = threading.RLock()
        instance.root = resolved
        instance._objects = {}
        instance._events = []
        instance._idempotency = {}
        instance._load_and_verify()
        return instance

    def _object_path(self, digest: str) -> Path:
        assert self.root is not None
        return self.root / "objects" / digest[:2] / f"{digest}.json"

    def _event_path(self, event_id: str) -> Path:
        assert self.root is not None
        return self.root / "events" / f"{event_id}.json"

    def _head_path(self) -> Path:
        assert self.root is not None
        return self.root / "head.json"

    def _store_object(self, candidate: Mapping[str, Any]) -> str:
        validated = validate_candidate_v2(candidate)
        raw = canonical_json_bytes(validated)
        digest = sha256_bytes(raw)
        previous = self._objects.get(digest)
        if previous is not None and previous != raw:
            raise IntakeBatchV2Error(
                "candidate_object_collision",
                "candidate object hash collision was detected",
                409,
            )
        self._objects[digest] = raw
        if self.root is not None:
            path = self._object_path(digest)
            if path.exists():
                try:
                    existing = path.read_bytes()
                except OSError:
                    raise IntakeBatchV2Error(
                        "candidate_cas_corrupt", "candidate object cannot be read", 409
                    ) from None
                if existing != raw or sha256_bytes(existing) != digest:
                    raise IntakeBatchV2Error(
                        "candidate_object_tampered",
                        "candidate object does not match its content address",
                        409,
                    )
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(descriptor, "wb", closefd=True) as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    raise IntakeBatchV2Error(
                        "candidate_cas_write_failed",
                        "candidate object could not be written",
                        503,
                    ) from None
        return digest

    def _load_object(self, digest: str) -> dict[str, Any]:
        _require_sha256(digest, field="candidate_sha256")
        raw = self._objects.get(digest)
        if self.root is not None:
            path = self._object_path(digest)
            try:
                disk_raw = path.read_bytes()
            except OSError:
                raise IntakeBatchV2Error(
                    "candidate_object_missing", "candidate object is missing", 409
                ) from None
            if raw is not None and raw != disk_raw:
                raise IntakeBatchV2Error(
                    "candidate_object_tampered", "candidate object changed on disk", 409
                )
            raw = disk_raw
        if raw is None or sha256_bytes(raw) != digest:
            raise IntakeBatchV2Error(
                "candidate_object_tampered",
                "candidate object does not match its content address",
                409,
            )
        value = strict_json_loads(raw)
        if not isinstance(value, Mapping):
            raise IntakeBatchV2Error(
                "candidate_object_tampered", "candidate object is invalid", 409
            )
        return validate_candidate_v2(value)

    def _head_value(self) -> dict[str, Any]:
        head = {
            "schema_version": INTAKE_BATCH_CAS_V2_SCHEMA_VERSION,
            "batch_id": self._load_object(self._current_candidate_sha256)["batch"][
                "batch_id"
            ],
            "initial_candidate_sha256": self._initial_candidate_sha256,
            "current_candidate_sha256": self._current_candidate_sha256,
            "revision_sequence": self._revision_sequence,
            "event_head_sha256": self._event_head_sha256,
            "revision_token": self._revision_token,
            "event_ids": [event["event_id"] for event in self._events],
            "idempotency": deepcopy(self._idempotency),
        }
        head["record_sha256"] = _record_sha256(head, "record_sha256")
        return head

    def _persist_head(self) -> None:
        if self.root is None:
            return
        _atomic_write(self._head_path(), canonical_json_bytes(self._head_value()))

    def _load_and_verify(self) -> None:
        head = _read_strict_json_file(self._head_path())
        required = {
            "schema_version",
            "batch_id",
            "initial_candidate_sha256",
            "current_candidate_sha256",
            "revision_sequence",
            "event_head_sha256",
            "revision_token",
            "event_ids",
            "idempotency",
            "record_sha256",
        }
        if set(head) != required or (
            head.get("schema_version") != INTAKE_BATCH_CAS_V2_SCHEMA_VERSION
            or head.get("record_sha256") != _record_sha256(head, "record_sha256")
        ):
            raise IntakeBatchV2Error(
                "candidate_cas_corrupt", "candidate CAS head is invalid", 409
            )
        initial_hash = _require_sha256(
            head["initial_candidate_sha256"], field="initial_candidate_sha256"
        )
        current_hash = _require_sha256(
            head["current_candidate_sha256"], field="current_candidate_sha256"
        )
        event_head = _require_sha256(
            head["event_head_sha256"], field="event_head_sha256"
        )
        sequence = head["revision_sequence"]
        if type(sequence) is not int or sequence < 0:
            raise IntakeBatchV2Error(
                "candidate_cas_corrupt", "candidate CAS revision is invalid", 409
            )
        if (
            not isinstance(head["revision_token"], str)
            or head["revision_token"] != _revision_token(sequence, event_head)
            or _REVISION_TOKEN.fullmatch(head["revision_token"]) is None
            or not isinstance(head["event_ids"], list)
            or len(head["event_ids"]) != sequence
            or not isinstance(head["idempotency"], Mapping)
        ):
            raise IntakeBatchV2Error(
                "candidate_cas_corrupt", "candidate CAS revision chain is invalid", 409
            )
        initial_candidate = self._read_disk_object(initial_hash)
        expected_initial_head = sha256_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "shchem.intake-batch-initial-head.v2",
                    "batch_id": initial_candidate["batch"]["batch_id"],
                    "initial_candidate_sha256": initial_hash,
                }
            )
        )
        previous_event_sha = expected_initial_head
        previous_candidate_hash = initial_hash
        events: list[dict[str, Any]] = []
        seen_idempotency: dict[str, dict[str, Any]] = {}
        for expected_sequence, event_id in enumerate(head["event_ids"], 1):
            if not isinstance(event_id, str) or not event_id.startswith("INTB2EV-"):
                raise IntakeBatchV2Error(
                    "candidate_cas_corrupt", "candidate CAS event id is invalid", 409
                )
            event = _read_strict_json_file(self._event_path(event_id))
            if (
                event.get("event_id") != event_id
                or event.get("event_sha256") != _event_sha256(event)
                or event.get("sequence") != expected_sequence
                or event.get("previous_event_sha256") != previous_event_sha
                or event.get("base_candidate_sha256") != previous_candidate_hash
                or event.get("result_revision_token")
                != _revision_token(expected_sequence, event.get("event_sha256", ""))
            ):
                raise IntakeBatchV2Error(
                    "candidate_cas_corrupt", "candidate CAS event chain is invalid", 409
                )
            result_hash = _require_sha256(
                event.get("result_candidate_sha256"),
                field="result_candidate_sha256",
            )
            result_candidate = self._read_disk_object(result_hash)
            if len(result_candidate["teacher_operations"]) != expected_sequence:
                raise IntakeBatchV2Error(
                    "candidate_cas_corrupt",
                    "candidate event result revision is invalid",
                    409,
                )
            idempotency_key = _require_idempotency(event.get("idempotency_key"))
            if idempotency_key in seen_idempotency:
                raise IntakeBatchV2Error(
                    "candidate_cas_corrupt",
                    "candidate CAS idempotency key is duplicated",
                    409,
                )
            seen_idempotency[idempotency_key] = {
                "request_sha256": event["request_sha256"],
                "event_id": event_id,
                "revision_token": event["result_revision_token"],
                "candidate_sha256": result_hash,
            }
            previous_event_sha = event["event_sha256"]
            previous_candidate_hash = result_hash
            events.append(event)
        if (
            previous_event_sha != event_head
            or previous_candidate_hash != current_hash
            or dict(head["idempotency"]) != seen_idempotency
        ):
            raise IntakeBatchV2Error(
                "candidate_cas_corrupt",
                "candidate CAS head does not match its events",
                409,
            )
        self._initial_candidate_sha256 = initial_hash
        self._current_candidate_sha256 = current_hash
        self._event_head_sha256 = event_head
        self._revision_sequence = sequence
        self._revision_token = head["revision_token"]
        self._events = events
        self._idempotency = seen_idempotency
        self._objects = {}
        if head["batch_id"] != initial_candidate["batch"]["batch_id"]:
            raise IntakeBatchV2Error(
                "candidate_cas_corrupt", "candidate CAS batch identity is invalid", 409
            )

    def _read_disk_object(self, digest: str) -> dict[str, Any]:
        path = self._object_path(digest)
        try:
            raw = path.read_bytes()
        except OSError:
            raise IntakeBatchV2Error(
                "candidate_object_missing", "candidate object is missing", 409
            ) from None
        if sha256_bytes(raw) != digest:
            raise IntakeBatchV2Error(
                "candidate_object_tampered",
                "candidate object does not match its content address",
                409,
            )
        value = strict_json_loads(raw)
        if not isinstance(value, Mapping):
            raise IntakeBatchV2Error(
                "candidate_object_tampered", "candidate object is invalid", 409
            )
        return validate_candidate_v2(value)

    def _snapshot_unlocked(self) -> dict[str, Any]:
        candidate = self._load_object(self._current_candidate_sha256)
        return {
            "schema_version": INTAKE_BATCH_CAS_V2_SCHEMA_VERSION,
            "candidate": candidate,
            "candidate_sha256": self._current_candidate_sha256,
            "revision_sequence": self._revision_sequence,
            "event_head_sha256": self._event_head_sha256,
            "revision_token": self._revision_token,
            "events": deepcopy(self._events),
            "candidate_only": True,
            "central_question_bank_write": False,
        }

    def snapshot(self) -> dict[str, Any]:
        with _cas_write_lock(self.root), self._lock:
            if self.root is not None:
                self._load_and_verify()
            return self._snapshot_unlocked()

    def object(self, digest: str) -> dict[str, Any]:
        with self._lock:
            return self._load_object(digest)

    def _snapshot_for_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        sequence = event["sequence"]
        return {
            "schema_version": INTAKE_BATCH_CAS_V2_SCHEMA_VERSION,
            "candidate": self._load_object(event["result_candidate_sha256"]),
            "candidate_sha256": event["result_candidate_sha256"],
            "revision_sequence": sequence,
            "event_head_sha256": event["event_sha256"],
            "revision_token": event["result_revision_token"],
            "events": deepcopy(self._events[:sequence]),
            "candidate_only": True,
            "central_question_bank_write": False,
        }

    def compare_and_swap(
        self,
        new_candidate: Mapping[str, Any],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        operation: str,
        target_ids: Sequence[str],
        idempotency_key: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with _cas_write_lock(self.root):
            with self._lock:
                if self.root is not None:
                    self._load_and_verify()
            return self._compare_and_swap_local(
                new_candidate,
                expected_revision_token=expected_revision_token,
                expected_candidate_sha256=expected_candidate_sha256,
                actor_id=actor_id,
                operation=operation,
                target_ids=target_ids,
                idempotency_key=idempotency_key,
                details=details,
            )

    def _compare_and_swap_local(
        self,
        new_candidate: Mapping[str, Any],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        operation: str,
        target_ids: Sequence[str],
        idempotency_key: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = _require_actor(actor_id)
        idempotency = _require_idempotency(idempotency_key)
        expected_hash = _require_sha256(
            expected_candidate_sha256, field="expected_candidate_sha256"
        )
        if (
            not isinstance(expected_revision_token, str)
            or _REVISION_TOKEN.fullmatch(expected_revision_token) is None
        ):
            raise IntakeBatchV2Error(
                "expected_revision_invalid", "expected revision token is invalid"
            )
        _require_text(operation, field="operation", maximum=160)
        targets = _require_string_list(
            list(target_ids), field="target ids", allow_empty=False
        )
        for target in targets:
            _require_safe_id(target, field="target_id")
        detail_value = deepcopy(dict(details or {}))
        candidate_without_operation = validate_candidate_v2(new_candidate)
        request_subject = {
            "schema_version": "shchem.intake-batch-cas-request.v2",
            "expected_revision_token": expected_revision_token,
            "expected_candidate_sha256": expected_hash,
            "actor_id": actor,
            "operation": operation,
            "target_ids": targets,
            "new_candidate_sha256_before_operation_record": candidate_sha256(
                candidate_without_operation
            ),
            "details": detail_value,
        }
        request_hash = sha256_bytes(canonical_json_bytes(request_subject))
        with self._lock:
            prior_idempotency = self._idempotency.get(idempotency)
            if prior_idempotency is not None:
                if prior_idempotency["request_sha256"] != request_hash:
                    raise IntakeBatchV2Error(
                        "idempotency_conflict",
                        "idempotency key was already used for a different mutation",
                        409,
                    )
                event = next(
                    item
                    for item in self._events
                    if item["event_id"] == prior_idempotency["event_id"]
                )
                return self._snapshot_for_event(event)
            if expected_revision_token != self._revision_token:
                raise IntakeBatchV2Error(
                    "candidate_revision_conflict",
                    "candidate revision changed; refresh before editing",
                    409,
                )
            if expected_hash != self._current_candidate_sha256:
                raise IntakeBatchV2Error(
                    "candidate_hash_conflict",
                    "candidate hash changed; refresh before editing",
                    409,
                )
            current = self._load_object(self._current_candidate_sha256)
            if len(candidate_without_operation["teacher_operations"]) != len(
                current["teacher_operations"]
            ):
                raise IntakeBatchV2Error(
                    "candidate_operation_chain_invalid",
                    "mutation must not prewrite a teacher operation record",
                    409,
                )
            for existing, proposed in zip(
                current["teacher_operations"],
                candidate_without_operation["teacher_operations"],
                strict=True,
            ):
                if canonical_json_bytes(existing) != canonical_json_bytes(proposed):
                    raise IntakeBatchV2Error(
                        "candidate_operation_chain_invalid",
                        "mutation altered prior teacher operation records",
                        409,
                    )
            next_candidate = deepcopy(candidate_without_operation)
            next_sequence = self._revision_sequence + 1
            next_candidate["teacher_operations"].append(
                {
                    "sequence": next_sequence,
                    "operation": operation,
                    "actor_id": actor,
                    "target_ids": targets,
                    "base_candidate_sha256": self._current_candidate_sha256,
                    "base_revision_token": self._revision_token,
                }
            )
            next_candidate = validate_candidate_v2(next_candidate)
            result_hash = candidate_sha256(next_candidate)
            if result_hash == self._current_candidate_sha256:
                raise IntakeBatchV2Error(
                    "candidate_no_change", "candidate mutation produced no change", 409
                )
            event_subject = {
                "schema_version": "shchem.intake-batch-cas-event.v2",
                "sequence": next_sequence,
                "idempotency_key": idempotency,
                "request_sha256": request_hash,
                "actor_id": actor,
                "operation": operation,
                "target_ids": targets,
                "details": detail_value,
                "base_revision_token": self._revision_token,
                "base_candidate_sha256": self._current_candidate_sha256,
                "result_candidate_sha256": result_hash,
                "previous_event_sha256": self._event_head_sha256,
            }
            event_id = f"INTB2EV-{sha256_bytes(canonical_json_bytes(event_subject))}"
            event = {**event_subject, "event_id": event_id}
            event["event_sha256"] = _event_sha256(event)
            event["result_revision_token"] = _revision_token(
                next_sequence, event["event_sha256"]
            )
            self._store_object(next_candidate)
            if self.root is not None:
                event_path = self._event_path(event_id)
                if event_path.exists():
                    raise IntakeBatchV2Error(
                        "candidate_event_collision",
                        "candidate event id is not unique",
                        409,
                    )
                event_path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    event_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                try:
                    with os.fdopen(descriptor, "wb", closefd=True) as stream:
                        stream.write(canonical_json_bytes(event))
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError:
                    try:
                        event_path.unlink()
                    except OSError:
                        pass
                    raise IntakeBatchV2Error(
                        "candidate_cas_write_failed",
                        "candidate event could not be written",
                        503,
                    ) from None
            self._current_candidate_sha256 = result_hash
            self._revision_sequence = next_sequence
            self._event_head_sha256 = event["event_sha256"]
            self._revision_token = event["result_revision_token"]
            self._events.append(event)
            self._idempotency[idempotency] = {
                "request_sha256": request_hash,
                "event_id": event_id,
                "revision_token": self._revision_token,
                "candidate_sha256": result_hash,
            }
            self._persist_head()
            return self._snapshot_unlocked()

    def _candidate_at_revision(
        self, expected_revision_token: str, expected_candidate_sha256: str
    ) -> dict[str, Any]:
        expected_hash = _require_sha256(
            expected_candidate_sha256, field="expected_candidate_sha256"
        )
        if (
            not isinstance(expected_revision_token, str)
            or _REVISION_TOKEN.fullmatch(expected_revision_token) is None
        ):
            raise IntakeBatchV2Error(
                "expected_revision_invalid", "expected revision token is invalid"
            )
        with self._lock:
            initial_token = _revision_token(
                0,
                sha256_bytes(
                    canonical_json_bytes(
                        {
                            "schema_version": "shchem.intake-batch-initial-head.v2",
                            "batch_id": self._load_object(
                                self._initial_candidate_sha256
                            )["batch"]["batch_id"],
                            "initial_candidate_sha256": self._initial_candidate_sha256,
                        }
                    )
                ),
            )
            if (
                expected_revision_token == initial_token
                and expected_hash == self._initial_candidate_sha256
            ):
                return self._load_object(expected_hash)
            for event in self._events:
                if (
                    event["result_revision_token"] == expected_revision_token
                    and event["result_candidate_sha256"] == expected_hash
                ):
                    return self._load_object(expected_hash)
        raise IntakeBatchV2Error(
            "candidate_revision_conflict",
            "candidate revision/hash pair is not in the revision chain",
            409,
        )

    def edit_atomic_part(
        self,
        atomic_part_id: str,
        patch: Mapping[str, Any],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        atomic_id = _require_safe_id(atomic_part_id, field="atomic_part_id")
        allowed = {
            "part_label",
            "stem",
            "options",
            "response_requirements",
            "chemical_expressions",
            "visual_object_refs",
            "curriculum",
            "classification",
            "cognitive_difficulty",
            "answer",
            "evidence_refs",
        }
        if not isinstance(patch, Mapping) or not patch or not set(patch) <= allowed:
            raise IntakeBatchV2Error(
                "atomic_patch_invalid", "atomic part patch is invalid"
            )
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        _theme, _printed, index, atomic = _find_atomic_location(candidate, atomic_id)
        _printed["atomic_parts"][index] = _deep_merge_json(atomic, patch)
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="edit_atomic_part",
            target_ids=[atomic_id],
            idempotency_key=idempotency_key,
            details={"changed_fields": sorted(patch)},
        )

    edit_atomic = edit_atomic_part

    def edit_printed_question(
        self,
        printed_question_id: str,
        patch: Mapping[str, Any],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        printed_id = _require_safe_id(printed_question_id, field="printed_question_id")
        allowed = {
            "stem",
            "options",
            "response_requirements",
            "chemical_expressions",
            "shared_material_refs",
            "visual_object_refs",
            "evidence_refs",
        }
        if not isinstance(patch, Mapping) or not patch or not set(patch) <= allowed:
            raise IntakeBatchV2Error(
                "printed_patch_invalid", "printed question patch is invalid"
            )
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        theme, index, printed = _find_printed_location(candidate, printed_id)
        theme["printed_questions"][index] = _deep_merge_json(printed, patch)
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="edit_printed_question",
            target_ids=[printed_id],
            idempotency_key=idempotency_key,
            details={"changed_fields": sorted(patch)},
        )

    def edit_theme_big_question(
        self,
        theme_big_question_id: str,
        patch: Mapping[str, Any],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        theme_id = _require_safe_id(
            theme_big_question_id, field="theme_big_question_id"
        )
        allowed = {
            "theme_number",
            "title",
            "context",
            "shared_materials",
            "visual_objects",
            "dependency_edges",
            "evidence_refs",
        }
        if not isinstance(patch, Mapping) or not patch or not set(patch) <= allowed:
            raise IntakeBatchV2Error(
                "theme_patch_invalid", "theme big question patch is invalid"
            )
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        index, theme = _find_theme_location(candidate, theme_id)
        candidate["paper"]["theme_big_questions"][index] = _deep_merge_json(
            theme, patch
        )
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="edit_theme_big_question",
            target_ids=[theme_id],
            idempotency_key=idempotency_key,
            details={"changed_fields": sorted(patch)},
        )

    def correct_question_number(
        self,
        printed_question_id: str,
        new_question_number: str,
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        printed_id = _require_safe_id(printed_question_id, field="printed_question_id")
        _require_text(new_question_number, field="new question number", maximum=80)
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        _theme, _index, printed = _find_printed_location(candidate, printed_id)
        old_number = printed["question_number"]
        printed["question_number"] = new_question_number
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="correct_question_number",
            target_ids=[printed_id],
            idempotency_key=idempotency_key,
            details={"before": old_number, "after": new_question_number},
        )

    renumber_question = correct_question_number

    def align_answer(
        self,
        answer_candidate_id: str,
        atomic_part_id: str,
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        answer_id = _require_safe_id(answer_candidate_id, field="answer_candidate_id")
        atomic_id = _require_safe_id(atomic_part_id, field="atomic_part_id")
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        matches = [
            (index, answer)
            for index, answer in enumerate(candidate["unaligned_answer_candidates"])
            if answer["answer_candidate_id"] == answer_id
        ]
        if len(matches) != 1:
            raise IntakeBatchV2Error(
                "answer_candidate_not_found",
                "unaligned answer candidate was not found",
                404,
            )
        answer_index, answer = matches[0]
        _theme, _printed, _atomic_index, atomic = _find_atomic_location(
            candidate, atomic_id
        )
        if atomic["answer"]["status"] != "missing":
            raise IntakeBatchV2Error(
                "answer_target_occupied",
                "target atomic part already has an aligned answer",
                409,
            )
        atomic["answer"] = _answer_from_candidate(
            answer, alignment_status="teacher_confirmed"
        )
        del candidate["unaligned_answer_candidates"][answer_index]
        candidate["review_blockers"] = [
            blocker
            for blocker in candidate["review_blockers"]
            if not (
                blocker["code"] == "answer_alignment_pending"
                and blocker["target_id"] == answer_id
            )
        ]
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="align_answer",
            target_ids=[answer_id, atomic_id],
            idempotency_key=idempotency_key,
            details={"answer_candidate_id": answer_id, "atomic_part_id": atomic_id},
        )

    correct_answer_alignment = align_answer

    def split_atomic_part(
        self,
        atomic_part_id: str,
        parts: Sequence[Mapping[str, Any]],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        atomic_id = _require_safe_id(atomic_part_id, field="atomic_part_id")
        if (
            not isinstance(parts, Sequence)
            or isinstance(parts, (str, bytes))
            or len(parts) < 2
        ):
            raise IntakeBatchV2Error(
                "atomic_split_invalid", "atomic split requires at least two parts"
            )
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        theme, printed, index, original = _find_atomic_location(candidate, atomic_id)
        existing_ids = _all_node_ids(candidate) - {atomic_id}
        new_parts: list[dict[str, Any]] = []
        for part_value in parts:
            if not isinstance(part_value, Mapping) or not {
                "atomic_part_id",
                "part_label",
                "stem",
                "response_requirements",
            } <= set(part_value):
                raise IntakeBatchV2Error(
                    "atomic_split_invalid",
                    "each split part requires id, label, stem, and response requirements",
                )
            if not set(part_value) <= {
                "atomic_part_id",
                "part_label",
                "stem",
                "options",
                "response_requirements",
                "chemical_expressions",
                "visual_object_refs",
                "curriculum",
                "classification",
                "cognitive_difficulty",
                "answer",
                "evidence_refs",
            }:
                raise IntakeBatchV2Error(
                    "atomic_split_invalid", "split part contains unsupported fields"
                )
            new_id = _require_safe_id(
                part_value["atomic_part_id"], field="atomic_part_id"
            )
            if new_id in existing_ids or any(
                item["atomic_part_id"] == new_id for item in new_parts
            ):
                raise IntakeBatchV2Error(
                    "candidate_node_id_collision", "split atomic id already exists", 409
                )
            if original["answer"]["status"] != "missing" and "answer" not in part_value:
                raise IntakeBatchV2Error(
                    "atomic_split_answer_required",
                    "splitting an answered atomic part requires explicit answer mapping for every new part",
                    409,
                )
            new_part = _deep_merge_json(original, part_value)
            new_parts.append(new_part)
        printed["atomic_parts"][index : index + 1] = new_parts
        for sequence, atomic in enumerate(printed["atomic_parts"], 1):
            atomic["sequence_in_printed"] = sequence
        first_id = new_parts[0]["atomic_part_id"]
        last_id = new_parts[-1]["atomic_part_id"]
        remapped_edges: list[dict[str, Any]] = []
        for edge_value in theme["dependency_edges"]:
            edge = deepcopy(edge_value)
            if edge["from_atomic_part_id"] == atomic_id:
                edge["from_atomic_part_id"] = last_id
            if edge["to_atomic_part_id"] == atomic_id:
                edge["to_atomic_part_id"] = first_id
            remapped_edges.append(edge)
        for left, right in itertools.pairwise(new_parts):
            edge_subject = {
                "from": left["atomic_part_id"],
                "to": right["atomic_part_id"],
                "relation": "teacher_split_sequence",
            }
            remapped_edges.append(
                {
                    "dependency_edge_id": "DEP-SPLIT-"
                    + sha256_bytes(canonical_json_bytes(edge_subject))[:20],
                    "from_atomic_part_id": left["atomic_part_id"],
                    "to_atomic_part_id": right["atomic_part_id"],
                    "relation": "teacher_split_sequence",
                    "evidence_refs": _stable_union(
                        [*left["evidence_refs"], *right["evidence_refs"]]
                    ),
                }
            )
        theme["dependency_edges"] = _dedupe_dependencies(remapped_edges)
        _retarget_blockers(candidate, {atomic_id}, first_id)
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="split_atomic_part",
            target_ids=[atomic_id, *[part["atomic_part_id"] for part in new_parts]],
            idempotency_key=idempotency_key,
            details={
                "source_atomic_part_id": atomic_id,
                "result_atomic_part_ids": [
                    part["atomic_part_id"] for part in new_parts
                ],
            },
        )

    split_question = split_atomic_part

    def merge_atomic_parts(
        self,
        atomic_part_ids: Sequence[str],
        merged_atomic: Mapping[str, Any],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        ids = list(atomic_part_ids)
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise IntakeBatchV2Error(
                "atomic_merge_invalid", "atomic merge requires distinct parts"
            )
        for atomic_id in ids:
            _require_safe_id(atomic_id, field="atomic_part_id")
        if not isinstance(merged_atomic, Mapping) or not {
            "atomic_part_id",
            "part_label",
            "stem",
            "response_requirements",
        } <= set(merged_atomic):
            raise IntakeBatchV2Error(
                "atomic_merge_invalid",
                "merged atomic requires id, label, stem, and response requirements",
            )
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        locations = [_find_atomic_location(candidate, atomic_id) for atomic_id in ids]
        themes = {location[0]["theme_big_question_id"] for location in locations}
        printed_ids = {location[1]["printed_question_id"] for location in locations}
        indexes = [location[2] for location in locations]
        if (
            len(themes) != 1
            or len(printed_ids) != 1
            or indexes != list(range(min(indexes), max(indexes) + 1))
        ):
            raise IntakeBatchV2Error(
                "atomic_merge_invalid",
                "atomic parts must be contiguous within one printed question",
                409,
            )
        theme, printed, _first_index, _first_atomic = locations[0]
        selected = [location[3] for location in locations]
        new_id = _require_safe_id(
            merged_atomic["atomic_part_id"], field="atomic_part_id"
        )
        if new_id in (_all_node_ids(candidate) - set(ids)):
            raise IntakeBatchV2Error(
                "candidate_node_id_collision", "merged atomic id already exists", 409
            )
        if any(item["answer"]["status"] != "missing" for item in selected) and (
            "answer" not in merged_atomic
        ):
            raise IntakeBatchV2Error(
                "atomic_merge_answer_required",
                "merging answered atomic parts requires an explicit merged answer",
                409,
            )
        merged = _deep_merge_json(selected[0], merged_atomic)
        for field in ("evidence_refs", "visual_object_refs", "chemical_expressions"):
            if field not in merged_atomic:
                merged[field] = _stable_union(
                    [item for atomic in selected for item in atomic[field]]
                )
        start = min(indexes)
        printed["atomic_parts"][start : max(indexes) + 1] = [merged]
        for sequence, atomic in enumerate(printed["atomic_parts"], 1):
            atomic["sequence_in_printed"] = sequence
        selected_ids = set(ids)
        remapped_edges: list[dict[str, Any]] = []
        for edge_value in theme["dependency_edges"]:
            edge = deepcopy(edge_value)
            if edge["from_atomic_part_id"] in selected_ids:
                edge["from_atomic_part_id"] = new_id
            if edge["to_atomic_part_id"] in selected_ids:
                edge["to_atomic_part_id"] = new_id
            remapped_edges.append(edge)
        theme["dependency_edges"] = _dedupe_dependencies(remapped_edges)
        _retarget_blockers(candidate, selected_ids, new_id)
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="merge_atomic_parts",
            target_ids=[*ids, new_id],
            idempotency_key=idempotency_key,
            details={"source_atomic_part_ids": ids, "result_atomic_part_id": new_id},
        )

    merge_questions = merge_atomic_parts

    def split_printed_question(
        self,
        printed_question_id: str,
        parts: Sequence[Mapping[str, Any]],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        printed_id = _require_safe_id(printed_question_id, field="printed_question_id")
        if (
            not isinstance(parts, Sequence)
            or isinstance(parts, (str, bytes))
            or len(parts) < 2
        ):
            raise IntakeBatchV2Error(
                "printed_split_invalid", "printed split requires at least two questions"
            )
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        theme, index, original = _find_printed_location(candidate, printed_id)
        atomic_by_id = {
            atomic["atomic_part_id"]: atomic for atomic in original["atomic_parts"]
        }
        assigned: list[str] = []
        existing_ids = _all_node_ids(candidate) - {printed_id}
        new_printed: list[dict[str, Any]] = []
        for part_value in parts:
            if not isinstance(part_value, Mapping) or not {
                "printed_question_id",
                "question_number",
                "atomic_part_ids",
            } <= set(part_value):
                raise IntakeBatchV2Error(
                    "printed_split_invalid",
                    "each printed split requires id, number, and atomic partition",
                )
            allowed = {
                "printed_question_id",
                "question_number",
                "atomic_part_ids",
                "stem",
                "options",
                "response_requirements",
                "chemical_expressions",
                "shared_material_refs",
                "visual_object_refs",
                "evidence_refs",
            }
            if not set(part_value) <= allowed:
                raise IntakeBatchV2Error(
                    "printed_split_invalid", "printed split contains unsupported fields"
                )
            new_id = _require_safe_id(
                part_value["printed_question_id"], field="printed_question_id"
            )
            if new_id in existing_ids or any(
                item["printed_question_id"] == new_id for item in new_printed
            ):
                raise IntakeBatchV2Error(
                    "candidate_node_id_collision",
                    "split printed id already exists",
                    409,
                )
            atomic_ids = _require_string_list(
                part_value["atomic_part_ids"],
                field="atomic part partition",
                allow_empty=False,
            )
            if any(atomic_id not in atomic_by_id for atomic_id in atomic_ids):
                raise IntakeBatchV2Error(
                    "printed_split_invalid",
                    "printed split references an atomic outside the source question",
                    409,
                )
            assigned.extend(atomic_ids)
            patch = {
                key: deepcopy(value)
                for key, value in part_value.items()
                if key != "atomic_part_ids"
            }
            node = _deep_merge_json(original, patch)
            node["atomic_parts"] = [deepcopy(atomic_by_id[item]) for item in atomic_ids]
            for sequence, atomic in enumerate(node["atomic_parts"], 1):
                atomic["sequence_in_printed"] = sequence
            new_printed.append(node)
        if len(assigned) != len(set(assigned)) or set(assigned) != set(atomic_by_id):
            raise IntakeBatchV2Error(
                "printed_split_invalid",
                "printed split must partition every source atomic exactly once",
                409,
            )
        theme["printed_questions"][index : index + 1] = new_printed
        for sequence, printed in enumerate(theme["printed_questions"], 1):
            printed["sequence_in_theme"] = sequence
        replacement_id = new_printed[0]["printed_question_id"]
        _retarget_blockers(candidate, {printed_id}, replacement_id)
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="split_printed_question",
            target_ids=[
                printed_id,
                *[item["printed_question_id"] for item in new_printed],
            ],
            idempotency_key=idempotency_key,
            details={
                "source_printed_question_id": printed_id,
                "result_printed_question_ids": [
                    item["printed_question_id"] for item in new_printed
                ],
            },
        )

    split_printed = split_printed_question

    def merge_printed_questions(
        self,
        printed_question_ids: Sequence[str],
        merged_printed: Mapping[str, Any],
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        ids = list(printed_question_ids)
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise IntakeBatchV2Error(
                "printed_merge_invalid", "printed merge requires distinct questions"
            )
        for printed_id in ids:
            _require_safe_id(printed_id, field="printed_question_id")
        if not isinstance(merged_printed, Mapping) or not {
            "printed_question_id",
            "question_number",
            "stem",
            "response_requirements",
        } <= set(merged_printed):
            raise IntakeBatchV2Error(
                "printed_merge_invalid",
                "merged printed question requires id, number, stem, and response requirements",
            )
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        locations = [
            _find_printed_location(candidate, printed_id) for printed_id in ids
        ]
        theme_ids = {location[0]["theme_big_question_id"] for location in locations}
        indexes = [location[1] for location in locations]
        if len(theme_ids) != 1 or indexes != list(
            range(min(indexes), max(indexes) + 1)
        ):
            raise IntakeBatchV2Error(
                "printed_merge_invalid",
                "printed questions must be contiguous within one theme",
                409,
            )
        theme = locations[0][0]
        selected = [location[2] for location in locations]
        new_id = _require_safe_id(
            merged_printed["printed_question_id"], field="printed_question_id"
        )
        if new_id in (_all_node_ids(candidate) - set(ids)):
            raise IntakeBatchV2Error(
                "candidate_node_id_collision", "merged printed id already exists", 409
            )
        merged = _deep_merge_json(selected[0], merged_printed)
        merged["atomic_parts"] = [
            deepcopy(atomic)
            for printed in selected
            for atomic in printed["atomic_parts"]
        ]
        for sequence, atomic in enumerate(merged["atomic_parts"], 1):
            atomic["sequence_in_printed"] = sequence
        for field in (
            "evidence_refs",
            "shared_material_refs",
            "visual_object_refs",
            "chemical_expressions",
        ):
            if field not in merged_printed:
                merged[field] = _stable_union(
                    [item for printed in selected for item in printed[field]]
                )
        start = min(indexes)
        theme["printed_questions"][start : max(indexes) + 1] = [merged]
        for sequence, printed in enumerate(theme["printed_questions"], 1):
            printed["sequence_in_theme"] = sequence
        _retarget_blockers(candidate, set(ids), new_id)
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="merge_printed_questions",
            target_ids=[*ids, new_id],
            idempotency_key=idempotency_key,
            details={
                "source_printed_question_ids": ids,
                "result_printed_question_id": new_id,
            },
        )

    merge_printed = merge_printed_questions

    def realign_answer(
        self,
        answer_candidate_id: str,
        target_atomic_part_id: str,
        *,
        expected_revision_token: str,
        expected_candidate_sha256: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        answer_id = _require_safe_id(answer_candidate_id, field="answer_candidate_id")
        target_id = _require_safe_id(target_atomic_part_id, field="atomic_part_id")
        candidate = self._candidate_at_revision(
            expected_revision_token, expected_candidate_sha256
        )
        aligned = [
            atomic
            for _theme, _printed, atomic in _iter_atomics(candidate)
            if atomic["answer"]["answer_candidate_id"] == answer_id
        ]
        if len(aligned) != 1:
            raise IntakeBatchV2Error(
                "aligned_answer_not_found",
                "aligned answer candidate was not found",
                404,
            )
        source_atomic = aligned[0]
        if source_atomic["atomic_part_id"] == target_id:
            raise IntakeBatchV2Error(
                "answer_alignment_unchanged",
                "answer is already aligned to the target",
                409,
            )
        _theme, _printed, _index, target_atomic = _find_atomic_location(
            candidate, target_id
        )
        if target_atomic["answer"]["status"] != "missing":
            raise IntakeBatchV2Error(
                "answer_target_occupied",
                "target atomic part already has an aligned answer",
                409,
            )
        answer = deepcopy(source_atomic["answer"])
        answer["alignment"] = {
            "status": "teacher_confirmed",
            "confidence": 1.0,
            "evidence_refs": list(answer["evidence_refs"]),
        }
        source_atomic["answer"] = _empty_answer()
        target_atomic["answer"] = answer
        return self.compare_and_swap(
            candidate,
            expected_revision_token=expected_revision_token,
            expected_candidate_sha256=expected_candidate_sha256,
            actor_id=actor_id,
            operation="realign_answer",
            target_ids=[
                answer_id,
                source_atomic["atomic_part_id"],
                target_atomic["atomic_part_id"],
            ],
            idempotency_key=idempotency_key,
            details={
                "answer_candidate_id": answer_id,
                "from_atomic_part_id": source_atomic["atomic_part_id"],
                "to_atomic_part_id": target_atomic["atomic_part_id"],
            },
        )
