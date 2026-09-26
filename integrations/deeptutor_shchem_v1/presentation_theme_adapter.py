from __future__ import annotations

"""Project one real theme-workbench record into the generic PPT input contract.

The browser submits only teacher-facing lesson settings and an opaque theme
selection.  Question/answer/source facts are taken from the active local theme
catalog and its already-bound detail projections.  Local paths, asset hashes,
authority flags, and question text are never accepted from the browser.
"""

import hashlib
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .presentation_workbench import (
    INPUT_SCHEMA_VERSION,
    PresentationWorkbenchError,
)

THEME_REQUEST_SCHEMA_VERSION = "shchem.presentation-theme-request.v1"


def _text(value: Any, field: str, *, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise PresentationWorkbenchError(
            "presentation_theme_request_invalid",
            f"{field}格式不正确。",
            400,
        )
    return value.strip()


def _text_list(
    value: Any,
    field: str,
    *,
    minimum: int = 1,
    maximum: int = 12,
    item_maximum: int = 500,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise PresentationWorkbenchError(
            "presentation_theme_request_invalid",
            f"{field}数量必须在{minimum}—{maximum}之间。",
            400,
        )
    return [
        _text(item, f"{field}[{index}]", maximum=item_maximum)
        for index, item in enumerate(value)
    ]


def _exact(value: Any, keys: set[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PresentationWorkbenchError(
            "presentation_theme_request_invalid",
            f"{field}字段不完整或含未知字段。",
            400,
            details={
                "field": field,
                "missing": sorted(keys - set(value) if isinstance(value, Mapping) else keys),
                "unknown": sorted(set(value) - keys if isinstance(value, Mapping) else []),
            },
        )
    return value


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PresentationWorkbenchError(
            "presentation_theme_request_invalid",
            f"{field}必须是{minimum}—{maximum}的整数。",
            400,
        )
    return value


def _request(value: Mapping[str, Any]) -> dict[str, Any]:
    top = _exact(
        value,
        {
            "schema_version",
            "scope",
            "data_snapshot_id",
            "theme_id",
            "lesson_title",
            "grade",
            "duration_minutes",
            "textbook_selection",
            "lesson_goals",
            "diagnosis",
            "classroom_plan",
        },
        "request",
    )
    if top["schema_version"] != THEME_REQUEST_SCHEMA_VERSION:
        raise PresentationWorkbenchError(
            "presentation_theme_request_schema_unsupported",
            "PPT 主题请求版本不受支持。",
            400,
        )
    scope = _text(top["scope"], "scope", maximum=32)
    if scope not in {"wave1", "master", "supplemental"}:
        raise PresentationWorkbenchError(
            "presentation_theme_scope_invalid", "这个题库范围不能用于课程 PPT。", 400
        )
    snapshot = _text(top["data_snapshot_id"], "data_snapshot_id", maximum=64)
    if len(snapshot) != 64 or any(character not in "0123456789abcdef" for character in snapshot):
        raise PresentationWorkbenchError(
            "presentation_theme_snapshot_invalid", "题库快照标识不正确。", 400
        )

    textbook = _exact(
        top["textbook_selection"],
        {
            "book_title",
            "volume",
            "chapter",
            "section",
            "publisher",
            "evidence_note",
        },
        "textbook_selection",
    )
    goals = _exact(
        top["lesson_goals"],
        {
            "learning_objectives",
            "key_points",
            "difficult_points",
            "prerequisites",
            "lesson_emphasis",
        },
        "lesson_goals",
    )
    diagnosis = _exact(
        top["diagnosis"],
        {
            "label",
            "summary",
            "common_error",
            "cause_hypothesis",
            "confidence",
            "counterevidence",
        },
        "diagnosis",
    )
    confidence = diagnosis["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise PresentationWorkbenchError(
            "presentation_theme_request_invalid", "匿名薄弱点置信度必须在0—1之间。", 400
        )
    classroom = _exact(
        top["classroom_plan"],
        {"teacher_questions", "anticipated_responses", "homework"},
        "classroom_plan",
    )
    questions = _text_list(
        classroom["teacher_questions"], "classroom_plan.teacher_questions", maximum=12
    )
    responses = _text_list(
        classroom["anticipated_responses"],
        "classroom_plan.anticipated_responses",
        maximum=12,
    )
    if len(questions) != len(responses):
        raise PresentationWorkbenchError(
            "presentation_theme_request_invalid",
            "教师提问与预期回答必须逐项对应。",
            400,
        )
    return {
        "scope": scope,
        "data_snapshot_id": snapshot,
        "theme_id": _text(top["theme_id"], "theme_id", maximum=220),
        "lesson_title": _text(top["lesson_title"], "lesson_title", maximum=300),
        "grade": _text(top["grade"], "grade", maximum=200),
        "duration_minutes": _integer(
            top["duration_minutes"], "duration_minutes", minimum=30, maximum=120
        ),
        "textbook_selection": {
            key: _text(textbook[key], f"textbook_selection.{key}", maximum=600)
            for key in (
                "book_title",
                "volume",
                "chapter",
                "section",
                "publisher",
                "evidence_note",
            )
        },
        "lesson_goals": {
            "learning_objectives": _text_list(
                goals["learning_objectives"], "lesson_goals.learning_objectives", maximum=8
            ),
            "key_points": _text_list(
                goals["key_points"], "lesson_goals.key_points", maximum=8
            ),
            "difficult_points": _text_list(
                goals["difficult_points"], "lesson_goals.difficult_points", maximum=8
            ),
            "prerequisites": _text_list(
                goals["prerequisites"], "lesson_goals.prerequisites", maximum=8
            ),
            "lesson_emphasis": _text(
                goals["lesson_emphasis"], "lesson_goals.lesson_emphasis", maximum=800
            ),
        },
        "diagnosis": {
            "label": _text(diagnosis["label"], "diagnosis.label", maximum=300),
            "summary": _text(diagnosis["summary"], "diagnosis.summary", maximum=1200),
            "common_error": _text(
                diagnosis["common_error"], "diagnosis.common_error", maximum=800
            ),
            "cause_hypothesis": _text(
                diagnosis["cause_hypothesis"],
                "diagnosis.cause_hypothesis",
                maximum=800,
            ),
            "confidence": float(confidence),
            "counterevidence": _text_list(
                diagnosis["counterevidence"],
                "diagnosis.counterevidence",
                maximum=12,
            ),
        },
        "classroom_plan": {
            "teacher_questions": questions,
            "anticipated_responses": responses,
            "homework": _text_list(
                classroom["homework"], "classroom_plan.homework", maximum=10
            ),
        },
    }


def validate_theme_request(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical, re-validatable browser DTO before local mutation."""

    normalized = deepcopy(_request(value))
    return {"schema_version": THEME_REQUEST_SCHEMA_VERSION, **normalized}


def _find_theme(catalog: Mapping[str, Any], scope: str, theme_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(catalog, Mapping) or catalog.get("scope") != scope:
        raise PresentationWorkbenchError(
            "presentation_theme_catalog_invalid",
            "当前活动主题题库与请求范围不一致。",
            409,
        )
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for paper_row in catalog.get("papers", []):
        if not isinstance(paper_row, Mapping) or not isinstance(paper_row.get("paper"), Mapping):
            continue
        for group in paper_row.get("theme_groups", []):
            if (
                isinstance(group, Mapping)
                and isinstance(group.get("theme"), Mapping)
                and group["theme"].get("id") == theme_id
            ):
                found.append((deepcopy(dict(paper_row["paper"])), deepcopy(dict(group))))
    if len(found) != 1:
        raise PresentationWorkbenchError(
            "presentation_theme_not_found" if not found else "presentation_theme_ambiguous",
            "找不到这个完整主题，或当前题库中存在重复主题。",
            404 if not found else 409,
        )
    return found[0]


def _response_mode(item_type: Any) -> str:
    mapping = {
        "embedded_single_choice": "choice",
        "embedded_indeterminate_choice": "choice",
        "short_fill": "fill_blank",
        "quantitative_calculation": "calculation",
        "chemical_equation_or_notation": "equation",
        "organic_structure_or_route": "structure",
        "experiment_operation_apparatus_plan": "experimental_evaluation",
        "reasoned_explanation": "reason_explanation",
        "graph_read_draw_complete": "short_answer",
    }
    return mapping.get(item_type, "unknown")


def _unique_text(values: list[Any], *, fallback: str, maximum: int) -> str:
    rows: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in rows:
            rows.append(value.strip())
    text = "；".join(rows) if rows else fallback
    return text[:maximum]


def _asset_ids_for_detail(
    detail: Mapping[str, Any],
    asset_bindings: Mapping[str, Mapping[str, Any]],
    role: str,
) -> list[str]:
    rows: list[str] = []
    for evidence in detail.get("evidence_descriptors", []):
        if not isinstance(evidence, Mapping) or evidence.get("evidence_role") != role:
            continue
        binding = asset_bindings.get(str(evidence.get("crop_id")))
        asset_id = binding.get("asset_id") if isinstance(binding, Mapping) else None
        if isinstance(asset_id, str) and asset_id and asset_id not in rows:
            rows.append(asset_id)
    return rows


def _theme_asset_requirements(
    atomic_ids: Sequence[str],
    details_by_atomic: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    requirements: dict[str, dict[str, Any]] = {}
    coverage: dict[str, dict[str, set[str]]] = {}
    for atomic_id in atomic_ids:
        detail = details_by_atomic.get(atomic_id)
        descriptors = (
            detail.get("evidence_descriptors") if isinstance(detail, Mapping) else None
        )
        if not isinstance(descriptors, list):
            raise PresentationWorkbenchError(
                "presentation_theme_evidence_invalid",
                "逐题图像证据清单不完整。",
                409,
                details={"atomic_part_id": atomic_id},
            )
        dependency = detail.get("dependency") if isinstance(detail, Mapping) else None
        shared_references = (
            dependency.get("shared_material_crop_ids")
            if isinstance(dependency, Mapping)
            else None
        )
        if (
            not isinstance(shared_references, list)
            or any(
                not isinstance(value, str) or not value
                for value in shared_references
            )
            or len(shared_references) != len(set(shared_references))
        ):
            raise PresentationWorkbenchError(
                "presentation_theme_dependency_evidence_invalid",
                "逐题共同材料依赖清单不完整。",
                409,
                details={"atomic_part_id": atomic_id},
            )
        coverage[atomic_id] = {
            "question": set(),
            "shared_references": set(shared_references),
        }
        for descriptor in descriptors:
            if not isinstance(descriptor, Mapping):
                raise PresentationWorkbenchError(
                    "presentation_theme_evidence_invalid",
                    "逐题图像证据记录不正确。",
                    409,
                    details={"atomic_part_id": atomic_id},
                )
            evidence_role = descriptor.get("evidence_role")
            if evidence_role not in {"question", "shared_material"}:
                continue
            crop_id = descriptor.get("crop_id")
            source_page = descriptor.get("source_page")
            width = descriptor.get("width")
            height = descriptor.get("height")
            digest = descriptor.get("sha256")
            if (
                not isinstance(crop_id, str)
                or not crop_id
                or len(crop_id) > 220
                or type(source_page) is not int
                or not 1 <= source_page <= 9999
                or type(width) is not int
                or type(height) is not int
                or not 1 <= width <= 100_000
                or not 1 <= height <= 100_000
                or width * height > 100_000_000
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise PresentationWorkbenchError(
                    "presentation_theme_evidence_invalid",
                    "逐题图像证据的标识、页码、尺寸或 SHA-256 不正确。",
                    409,
                    details={"atomic_part_id": atomic_id, "crop_id": crop_id},
                )
            candidate = {
                "crop_id": crop_id,
                "source_page": source_page,
                "width": width,
                "height": height,
                "sha256": digest,
                "role": evidence_role,
            }
            existing = requirements.get(crop_id)
            if existing is not None:
                if any(
                    existing[field] != candidate[field]
                    for field in ("source_page", "width", "height", "sha256")
                ):
                    raise PresentationWorkbenchError(
                        "presentation_theme_evidence_ambiguous",
                        "同一题图标识对应了不同来源绑定，已停止生成。",
                        409,
                        details={"crop_id": crop_id},
                    )
                if evidence_role == "shared_material":
                    existing["role"] = "shared_material"
            else:
                requirements[crop_id] = candidate
            if evidence_role == "question":
                coverage[atomic_id]["question"].add(crop_id)

    shared_material_ids = {
        crop_id
        for crop_id, requirement in requirements.items()
        if requirement.get("role") == "shared_material"
    }
    for atomic_id, atomic_coverage in coverage.items():
        shared_references = atomic_coverage["shared_references"]
        missing_shared = shared_references - shared_material_ids
        if missing_shared:
            raise PresentationWorkbenchError(
                "presentation_theme_shared_evidence_missing",
                "逐题引用的共同材料裁片不存在或未标为共同材料。",
                409,
                details={
                    "atomic_part_id": atomic_id,
                    "missing_crop_ids": sorted(missing_shared),
                },
            )
        if not atomic_coverage["question"] and not shared_references:
            raise PresentationWorkbenchError(
                "presentation_theme_atomic_evidence_missing",
                "逐题既没有题面裁片，也没有可继承的共同材料裁片。",
                409,
                details={"atomic_part_id": atomic_id},
            )
    return requirements


def _validate_asset_binding_closure(
    asset_bindings: Mapping[str, Mapping[str, Any]] | None,
    requirements: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if asset_bindings is None:
        bindings: Mapping[str, Mapping[str, Any]] = {}
    elif isinstance(asset_bindings, Mapping):
        bindings = asset_bindings
    else:
        raise PresentationWorkbenchError(
            "presentation_theme_asset_binding_invalid",
            "PPT 题图素材绑定必须是服务端结构化对象。",
            409,
        )
    if any(not isinstance(key, str) or not key for key in bindings):
        raise PresentationWorkbenchError(
            "presentation_theme_asset_binding_invalid",
            "PPT 题图素材绑定标识不正确。",
            409,
        )
    required_ids = set(requirements)
    supplied_ids = set(bindings)
    if required_ids != supplied_ids:
        raise PresentationWorkbenchError(
            "presentation_theme_asset_binding_incomplete",
            "PPT 题图素材绑定没有精确覆盖完整主题的视觉证据。",
            409,
            details={
                "missing_crop_ids": sorted(required_ids - supplied_ids),
                "unexpected_crop_ids": sorted(supplied_ids - required_ids),
            },
        )

    expected_keys = {
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
    normalized: dict[str, dict[str, Any]] = {}
    by_asset_id: dict[str, dict[str, Any]] = {}
    for crop_id in sorted(required_ids):
        binding = bindings[crop_id]
        requirement = requirements[crop_id]
        if not isinstance(binding, Mapping) or set(binding) != expected_keys:
            raise PresentationWorkbenchError(
                "presentation_theme_asset_binding_invalid",
                "PPT 题图素材绑定字段不完整或含未知字段。",
                409,
                details={"crop_id": crop_id},
            )
        digest = requirement["sha256"]
        expected_asset_id = f"PPTASSET-{digest[:32]}"
        path = binding.get("path")
        allowed_paths = {f"{digest}.png", f"{digest}.jpg"}
        allowed_roles = (
            {"shared_material"}
            if requirement["role"] == "shared_material"
            else {"question_crop", "shared_material"}
        )
        if (
            binding.get("asset_id") != expected_asset_id
            or binding.get("role") not in allowed_roles
            or path not in allowed_paths
            or binding.get("sha256") != digest
            or binding.get("source_page") != requirement["source_page"]
            or binding.get("source_bbox")
            != [0, 0, requirement["width"], requirement["height"]]
            or binding.get("pixel_dimensions")
            != [requirement["width"], requirement["height"]]
            or not isinstance(binding.get("alt_text"), str)
            or not binding["alt_text"].strip()
            or not isinstance(binding.get("source_ref"), str)
            or not binding["source_ref"].strip()
            or not isinstance(binding.get("rights_boundary"), str)
            or not binding["rights_boundary"].strip()
            or binding.get("publication_allowed") is not False
        ):
            raise PresentationWorkbenchError(
                "presentation_theme_asset_binding_mismatch",
                "PPT 题图素材绑定与逐题证据的路径、SHA-256 或尺寸不一致。",
                409,
                details={"crop_id": crop_id},
            )
        row = deepcopy(dict(binding))
        previous = by_asset_id.get(expected_asset_id)
        if previous is not None and previous != row:
            raise PresentationWorkbenchError(
                "presentation_theme_asset_binding_ambiguous",
                "同一内容素材标识对应了冲突的来源绑定。",
                409,
                details={"asset_id": expected_asset_id},
            )
        by_asset_id[expected_asset_id] = row
        normalized[crop_id] = row
    return normalized


def materialize_theme_assets(
    *,
    theme_id: str,
    atomic_chain: Sequence[Mapping[str, Any]],
    details_by_atomic: Mapping[str, Mapping[str, Any]],
    crop_loader: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
    asset_store: Callable[[str, bytes], Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Materialize trusted server-side theme crops into one project's assets.

    The HTTP request never participates in this contract. ``crop_loader`` must
    resolve an already-bound local crop by atomic/evidence identity and return
    only its bytes, MIME type, and SHA-256. ``asset_store`` is expected to be a
    closure over the authenticated owner/session/project and to return the
    content-addressed record produced by ``PresentationJobManager.store_asset``.
    """

    normalized_theme_id = _text(theme_id, "theme_id", maximum=220)
    if (
        isinstance(atomic_chain, (str, bytes, bytearray))
        or not isinstance(atomic_chain, Sequence)
        or not atomic_chain
        or not isinstance(details_by_atomic, Mapping)
        or not callable(crop_loader)
        or not callable(asset_store)
    ):
        raise PresentationWorkbenchError(
            "presentation_theme_materialization_contract_invalid",
            "完整主题的服务端题图物化合同不正确。",
            409,
        )

    atomic_ids: list[str] = []
    for row in atomic_chain:
        atomic_id = row.get("atomic_part_id") if isinstance(row, Mapping) else None
        if not isinstance(atomic_id, str) or not atomic_id or atomic_id in atomic_ids:
            raise PresentationWorkbenchError(
                "presentation_theme_catalog_invalid",
                "这个主题的作答单元父链不完整。",
                409,
            )
        atomic_ids.append(atomic_id)
    if set(atomic_ids) != set(details_by_atomic):
        raise PresentationWorkbenchError(
            "presentation_theme_detail_closure_invalid",
            "完整主题的逐题详情尚未闭合，不能物化课程 PPT 题图。",
            409,
        )

    # Validate per-atomic visual coverage before the first loader call or
    # project asset write.  A unit may inherit a declared shared crop, but it
    # may not silently disappear merely because another unit has some image.
    _theme_asset_requirements(atomic_ids, details_by_atomic)

    # Phase 1 reads and verifies the complete crop closure before it creates
    # any project asset. Reused crop IDs must carry identical provenance;
    # shared-material semantics take precedence over an ordinary question role.
    crop_sources: dict[str, dict[str, Any]] = {}
    for atomic_id in atomic_ids:
        detail = details_by_atomic.get(atomic_id)
        descriptors = (
            detail.get("evidence_descriptors") if isinstance(detail, Mapping) else None
        )
        if not isinstance(descriptors, list):
            raise PresentationWorkbenchError(
                "presentation_theme_evidence_invalid",
                "逐题图像证据清单不完整。",
                409,
                details={"atomic_part_id": atomic_id},
            )
        for descriptor_value in descriptors:
            if not isinstance(descriptor_value, Mapping):
                raise PresentationWorkbenchError(
                    "presentation_theme_evidence_invalid",
                    "逐题图像证据记录不正确。",
                    409,
                    details={"atomic_part_id": atomic_id},
                )
            role = descriptor_value.get("evidence_role")
            if role not in {"question", "shared_material"}:
                continue
            crop_id = descriptor_value.get("crop_id")
            source_page = descriptor_value.get("source_page")
            width = descriptor_value.get("width")
            height = descriptor_value.get("height")
            expected_hash = descriptor_value.get("sha256")
            if (
                not isinstance(crop_id, str)
                or not crop_id
                or len(crop_id) > 220
                or type(source_page) is not int
                or not 1 <= source_page <= 9999
                or type(width) is not int
                or type(height) is not int
                or not 1 <= width <= 100_000
                or not 1 <= height <= 100_000
                or width * height > 100_000_000
                or not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or any(character not in "0123456789abcdef" for character in expected_hash)
            ):
                raise PresentationWorkbenchError(
                    "presentation_theme_evidence_invalid",
                    "逐题图像证据的标识、页码、尺寸或 SHA-256 不正确。",
                    409,
                    details={"atomic_part_id": atomic_id, "crop_id": crop_id},
                )

            loaded = crop_loader(atomic_id, deepcopy(dict(descriptor_value)))
            allowed_loader_keys = {"data", "content_type", "sha256"}
            if not isinstance(loaded, Mapping) or set(loaded) != allowed_loader_keys:
                raise PresentationWorkbenchError(
                    "presentation_theme_asset_loader_contract_invalid",
                    "服务端题图读取器只能返回图像字节、MIME 类型和 SHA-256。",
                    409,
                    details={
                        "crop_id": crop_id,
                        "unknown": sorted(
                            set(loaded) - allowed_loader_keys
                            if isinstance(loaded, Mapping)
                            else []
                        ),
                    },
                )
            data = loaded.get("data")
            content_type = loaded.get("content_type")
            loaded_hash = loaded.get("sha256")
            if type(data) is not bytes or content_type not in {"image/png", "image/jpeg"}:
                raise PresentationWorkbenchError(
                    "presentation_theme_asset_loader_contract_invalid",
                    "服务端题图读取结果不是受支持的 PNG/JPEG 字节。",
                    409,
                    details={"crop_id": crop_id},
                )
            observed_hash = hashlib.sha256(data).hexdigest()
            if loaded_hash != expected_hash or observed_hash != expected_hash:
                raise PresentationWorkbenchError(
                    "presentation_theme_evidence_drift",
                    "题面裁片与逐题记录不一致，请先刷新题库版本。",
                    409,
                    details={"atomic_part_id": atomic_id, "crop_id": crop_id},
                )

            source = {
                "crop_id": crop_id,
                "role": role,
                "source_page": source_page,
                "width": width,
                "height": height,
                "sha256": expected_hash,
                "content_type": content_type,
                "data": data,
            }
            existing = crop_sources.get(crop_id)
            if existing is not None:
                comparable = (
                    "source_page",
                    "width",
                    "height",
                    "sha256",
                    "content_type",
                    "data",
                )
                if any(existing[field] != source[field] for field in comparable):
                    raise PresentationWorkbenchError(
                        "presentation_theme_evidence_ambiguous",
                        "同一题图标识对应了不同内容或来源绑定，已停止生成。",
                        409,
                        details={"crop_id": crop_id},
                    )
                if role == "shared_material":
                    existing["role"] = "shared_material"
            else:
                crop_sources[crop_id] = source

    if not crop_sources:
        raise PresentationWorkbenchError(
            "presentation_theme_evidence_missing",
            "完整主题没有可核验的题面或共同材料裁片，不能生成课程 PPT。",
            409,
        )

    # Different evidence IDs can occasionally point at one exact crop. Keep
    # one content-addressed file and one canonical asset projection; provenance
    # must still agree because the generic deck contract has one source page
    # and one bounding box per logical asset ID.
    sources_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in crop_sources.values():
        sources_by_hash[source["sha256"]].append(source)

    bindings: dict[str, dict[str, Any]] = {}
    for digest, sources in sorted(sources_by_hash.items()):
        canonical = sources[0]
        if any(
            source[field] != canonical[field]
            for source in sources[1:]
            for field in ("source_page", "width", "height", "content_type", "data")
        ):
            raise PresentationWorkbenchError(
                "presentation_theme_evidence_ambiguous",
                "相同题图内容对应了不同页码或尺寸，已停止生成。",
                409,
                details={"crop_ids": sorted(source["crop_id"] for source in sources)},
            )
        role = (
            "shared_material"
            if any(source["role"] == "shared_material" for source in sources)
            else "question_crop"
        )
        suffix = ".jpg" if canonical["content_type"] == "image/jpeg" else ".png"
        stored = asset_store(f"source-crop{suffix}", canonical["data"])
        expected_path = f"{digest}{suffix}"
        expected_asset_id = f"PPTASSET-{digest[:32]}"
        if (
            not isinstance(stored, Mapping)
            or stored.get("asset_id") != expected_asset_id
            or stored.get("path") != expected_path
            or stored.get("sha256") != digest
            or stored.get("size_bytes") != len(canonical["data"])
            or stored.get("content_type") != canonical["content_type"]
            or stored.get("pixel_dimensions")
            != [canonical["width"], canonical["height"]]
            or stored.get("content_addressed") is not True
            or stored.get("immutable") is not True
        ):
            raise PresentationWorkbenchError(
                "presentation_theme_asset_store_contract_invalid",
                "项目私有题图的路径、SHA-256 或尺寸绑定不一致。",
                409,
                details={"crop_ids": sorted(source["crop_id"] for source in sources)},
            )
        crop_ids = sorted(source["crop_id"] for source in sources)
        binding = {
            "asset_id": expected_asset_id,
            "role": role,
            "path": expected_path,
            "sha256": digest,
            "source_page": canonical["source_page"],
            "source_bbox": [0, 0, canonical["width"], canonical["height"]],
            "pixel_dimensions": [canonical["width"], canonical["height"]],
            "alt_text": (
                "完整主题共同材料裁片"
                if role == "shared_material"
                else "主题内印刷小题题面裁片"
            ),
            "source_ref": f"{normalized_theme_id} / {', '.join(crop_ids)}",
            "rights_boundary": "本地个人备课候选；保留来源引用，不对外发布。",
            "publication_allowed": False,
        }
        for crop_id in crop_ids:
            bindings[crop_id] = deepcopy(binding)
    return bindings


def build_presentation_input(
    request: Mapping[str, Any],
    *,
    theme_catalog: Mapping[str, Any],
    details_by_atomic: Mapping[str, Mapping[str, Any]],
    active_snapshot_id: str,
    asset_bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a strict generic PPT input derived from one real complete theme."""

    normalized = validate_theme_request(request)
    if (
        not isinstance(active_snapshot_id, str)
        or len(active_snapshot_id) != 64
        or any(
            character not in "0123456789abcdef"
            for character in active_snapshot_id
        )
    ):
        raise PresentationWorkbenchError(
            "presentation_theme_snapshot_unbound",
            "服务端没有提供可核验的活动题库快照。",
            409,
        )
    if normalized["data_snapshot_id"] != active_snapshot_id:
        raise PresentationWorkbenchError(
            "presentation_theme_snapshot_stale",
            "题库版本已经变化，请刷新主题后再生成 PPT。",
            409,
        )
    catalog_snapshot = theme_catalog.get("data_snapshot_id")
    if catalog_snapshot != active_snapshot_id:
        raise PresentationWorkbenchError(
            "presentation_theme_catalog_snapshot_mismatch",
            "当前活动主题题库与已核验快照不一致。",
            409,
        )
    paper, group = _find_theme(
        theme_catalog, normalized["scope"], normalized["theme_id"]
    )
    theme = group.get("theme")
    chain = group.get("atomic_chain")
    if not isinstance(theme, Mapping) or not isinstance(chain, list) or not chain:
        raise PresentationWorkbenchError(
            "presentation_theme_catalog_invalid",
            "这个主题缺少完整题链，不能生成课程 PPT。",
            409,
        )
    atomic_ids = [
        row.get("atomic_part_id") for row in chain if isinstance(row, Mapping)
    ]
    if (
        len(atomic_ids) != len(chain)
        or len(set(atomic_ids)) != len(atomic_ids)
        or any(not isinstance(value, str) or not value for value in atomic_ids)
        or set(atomic_ids) != set(details_by_atomic)
    ):
        raise PresentationWorkbenchError(
            "presentation_theme_detail_closure_invalid",
            "完整主题的逐题详情尚未闭合，不能生成课程 PPT。",
            409,
        )

    requirements = _theme_asset_requirements(atomic_ids, details_by_atomic)
    bindings = _validate_asset_binding_closure(asset_bindings, requirements)
    assets: list[dict[str, Any]] = []
    seen_asset_ids: set[str] = set()
    for _, value in sorted(bindings.items()):
        asset_id = value.get("asset_id") if isinstance(value, Mapping) else None
        if isinstance(asset_id, str) and asset_id in seen_asset_ids:
            continue
        assets.append(deepcopy(dict(value)))
        if isinstance(asset_id, str):
            seen_asset_ids.add(asset_id)
    asset_ids = [value.get("asset_id") for value in assets]
    if len(set(asset_ids)) != len(asset_ids) or any(
        not isinstance(value, str) or not value for value in asset_ids
    ):
        raise PresentationWorkbenchError(
            "presentation_theme_asset_binding_invalid",
            "PPT 题图素材绑定不完整。",
            409,
        )

    page_span = theme.get("page_span")
    if not isinstance(page_span, Mapping):
        raise PresentationWorkbenchError(
            "presentation_theme_catalog_invalid", "主题页码跨度缺失。", 409
        )
    start_page = page_span.get("start_page")
    end_page = page_span.get("end_page")
    if type(start_page) is not int or type(end_page) is not int:
        raise PresentationWorkbenchError(
            "presentation_theme_catalog_invalid", "主题页码跨度不正确。", 409
        )

    shared_context = group.get("shared_context")
    context_summary = (
        shared_context.get("context_summary_zh")
        if isinstance(shared_context, Mapping)
        else None
    )
    raw_materials = (
        shared_context.get("materials")
        if isinstance(shared_context, Mapping) and isinstance(shared_context.get("materials"), list)
        else []
    )
    shared_materials: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_materials):
        if not isinstance(raw, Mapping):
            continue
        material_id = raw.get("material_id")
        if not isinstance(material_id, str) or not material_id:
            continue
        binding = bindings.get(material_id)
        shared_materials.append(
            {
                "shared_material_id": material_id,
                "title": _unique_text(
                    [raw.get("candidate_description_zh")],
                    fallback=f"主题共同材料{index + 1}",
                    maximum=300,
                ),
                "summary": _unique_text(
                    [raw.get("candidate_description_zh"), context_summary],
                    fallback="沿用来源卷的完整主题共同材料。",
                    maximum=1200,
                ),
                "source_page": raw.get("page") if type(raw.get("page")) is int else start_page,
                "asset_ids": (
                    [binding["asset_id"]]
                    if isinstance(binding, Mapping)
                    and isinstance(binding.get("asset_id"), str)
                    else []
                ),
                "source_ref": f"{paper.get('id')} / {theme.get('id')} / {material_id}",
            }
        )
    if not shared_materials:
        digest = hashlib.sha256(str(theme.get("id")).encode("utf-8")).hexdigest()[:16]
        shared_materials = [
            {
                "shared_material_id": f"THEME-CONTEXT-{digest}",
                "title": "完整主题上下文",
                "summary": _unique_text(
                    [context_summary],
                    fallback="按来源卷的完整主题题链组织课堂推进。",
                    maximum=1200,
                ),
                "source_page": start_page,
                "asset_ids": [],
                "source_ref": f"{paper.get('id')} / {theme.get('id')}",
            }
        ]
    shared_id_set = {row["shared_material_id"] for row in shared_materials}

    rows_by_printed: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in chain:
        if not isinstance(row, Mapping) or not isinstance(row.get("printed_question_id"), str):
            raise PresentationWorkbenchError(
                "presentation_theme_catalog_invalid", "主题印刷题父链不完整。", 409
            )
        rows_by_printed[row["printed_question_id"]].append(row)
    if not 1 <= len(rows_by_printed) <= 12:
        raise PresentationWorkbenchError(
            "presentation_theme_size_unsupported",
            "当前主题的印刷小题数量超出课程 PPT v1 支持范围。",
            409,
        )

    printed_questions: list[dict[str, Any]] = []
    for printed_id, printed_rows in sorted(
        rows_by_printed.items(),
        key=lambda item: min(
            row.get("printed_sequence", 10**9) for row in item[1]
        ),
    ):
        printed_rows = sorted(
            printed_rows,
            key=lambda row: (
                row.get("atomic_sequence_in_printed", 10**9),
                row.get("atomic_part_id", ""),
            ),
        )
        if len(printed_rows) > 9:
            raise PresentationWorkbenchError(
                "presentation_theme_size_unsupported",
                "一个印刷小题包含过多作答单元，课程 PPT v1 暂不支持。",
                409,
            )
        question_asset_ids: list[str] = []
        question_shared_ids: list[str] = []
        atomic_parts: list[dict[str, Any]] = []
        for row in printed_rows:
            atomic_id = row["atomic_part_id"]
            detail = details_by_atomic[atomic_id]
            for asset_id in _asset_ids_for_detail(detail, bindings, "question"):
                if asset_id not in question_asset_ids:
                    question_asset_ids.append(asset_id)
            detail_dependency = detail.get("dependency")
            shared_crop_ids = (
                detail_dependency.get("shared_material_crop_ids")
                if isinstance(detail_dependency, Mapping)
                and isinstance(detail_dependency.get("shared_material_crop_ids"), list)
                else []
            )
            for shared_id in shared_crop_ids:
                if shared_id in shared_id_set and shared_id not in question_shared_ids:
                    question_shared_ids.append(shared_id)
            prior_ids = (
                detail_dependency.get("prior_atomic_part_ids")
                if isinstance(detail_dependency, Mapping)
                and isinstance(detail_dependency.get("prior_atomic_part_ids"), list)
                else []
            )
            answer = detail.get("reference_answer")
            answer_text = (
                answer.get("reference_answer_text")
                if isinstance(answer, Mapping)
                and isinstance(answer.get("reference_answer_text"), str)
                and answer.get("reference_answer_text").strip()
                else None
            )
            availability = answer.get("availability") if isinstance(answer, Mapping) else None
            if answer_text is not None and isinstance(availability, str) and availability.startswith("present"):
                answer_status = "nonofficial_reference"
            elif availability == "absent":
                answer_status = "absent"
                answer_text = None
            else:
                answer_status = "blocked_pending_review"
                answer_text = None
            atomic_parts.append(
                {
                    "atomic_part_id": atomic_id,
                    "label": _unique_text(
                        [row.get("printed_question_number")],
                        fallback=str(len(atomic_parts) + 1),
                        maximum=120,
                    ),
                    "task": _unique_text(
                        [detail.get("response_requirement_zh"), row.get("response_requirement_zh")],
                        fallback="按题面完成本作答单元。",
                        maximum=1200,
                    ),
                    "response_mode": _response_mode(row.get("item_type")),
                    "answer_status": answer_status,
                    "answer_text": answer_text,
                    "answer_quality_note": (
                        answer.get("quality_note")
                        if isinstance(answer, Mapping)
                        and isinstance(answer.get("quality_note"), str)
                        and answer.get("quality_note").strip()
                        else None
                    ),
                    "answer_authority": (
                        answer.get("source_authority")
                        if isinstance(answer, Mapping)
                        and isinstance(answer.get("source_authority"), str)
                        else "none"
                    ),
                    "dependencies": list(dict.fromkeys(
                        [value for value in prior_ids if isinstance(value, str)]
                        + question_shared_ids
                    )),
                    "chemical_expressions": [],
                }
            )
        if not question_shared_ids:
            question_shared_ids = [shared_materials[0]["shared_material_id"]]
            for atomic in atomic_parts:
                if not atomic["dependencies"]:
                    atomic["dependencies"] = list(question_shared_ids)
        printed_questions.append(
            {
                "printed_question_id": printed_id,
                "display_number": _unique_text(
                    [printed_rows[0].get("printed_question_number")],
                    fallback=str(len(printed_questions) + 1),
                    maximum=120,
                ),
                "prompt": _unique_text(
                    [row.get("visible_summary_zh") for row in printed_rows],
                    fallback="按来源题面完成该印刷小题。",
                    maximum=2400,
                ),
                "source_page": start_page,
                "asset_ids": question_asset_ids,
                "shared_material_ids": question_shared_ids,
                "atomic_parts": atomic_parts,
            }
        )

    source_metadata = paper.get("source_metadata")
    if not isinstance(source_metadata, Mapping):
        source_metadata = {}
    try:
        year = int(source_metadata.get("year"))
    except (TypeError, ValueError) as exc:
        raise PresentationWorkbenchError(
            "presentation_theme_catalog_invalid", "来源年份不完整。", 409
        ) from exc
    if not 1900 <= year <= 2100:
        raise PresentationWorkbenchError(
            "presentation_theme_catalog_invalid", "来源年份不正确。", 409
        )

    textbook = normalized["textbook_selection"]
    goals = normalized["lesson_goals"]
    diagnosis = normalized["diagnosis"]
    classroom = normalized["classroom_plan"]
    practice_ids = atomic_ids[: min(3, len(atomic_ids))]
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "lesson_title": normalized["lesson_title"],
        "grade": normalized["grade"],
        "duration_minutes": normalized["duration_minutes"],
        "candidate_use": {
            "intended_use": "local_personal_lesson_preparation_candidate",
            "publication_allowed": False,
            "teacher_confirmation_required": True,
        },
        "textbook": {
            "book_title": textbook["book_title"],
            "volume": textbook["volume"],
            "chapter": textbook["chapter"],
            "section": textbook["section"],
            "publisher": textbook["publisher"],
            "evidence_level": "teacher_selected_local_catalog",
            "evidence_anchors": [
                {
                    "anchor_id": f"WB-TEXTBOOK-{normalized['data_snapshot_id'][:16]}",
                    "source_path": "local-workbench/textbook-selection",
                    "source_sha256": active_snapshot_id,
                    "page_number": 1,
                    "printed_page": None,
                    "evidence_text": textbook["evidence_note"],
                    "source_ref": (
                        f"教师选择：{textbook['volume']} / {textbook['chapter']} / "
                        f"{textbook['section']}"
                    ),
                }
            ],
            "notes": ["教材落点来自教师在当前本地目录中的明确选择。"],
        },
        "lesson_goals": deepcopy(goals),
        "theme": {
            "paper": {
                "paper_id": _text(paper.get("id"), "paper.id", maximum=160),
                "title": _text(paper.get("title"), "paper.title", maximum=500),
                "year": year,
                "region_or_school": _unique_text(
                    [source_metadata.get("region")], fallback="上海", maximum=200
                ),
                "paper_type": _unique_text(
                    [source_metadata.get("paper_type")], fallback="来源试卷", maximum=160
                ),
                "source_ref": f"活动题库 {normalized['scope']} / {paper.get('id')}",
                "source_authority": _unique_text(
                    [source_metadata.get("source_tier")],
                    fallback="candidate_source_record",
                    maximum=300,
                ),
            },
            "theme_id": normalized["theme_id"],
            "title": _text(theme.get("title"), "theme.title", maximum=500),
            "order": _integer(theme.get("sequence"), "theme.sequence", minimum=1, maximum=99),
            "page_span": [start_page, end_page],
            "context_summary": _unique_text(
                [context_summary], fallback="按完整主题题链组织课堂推进。", maximum=1800
            ),
            "source_authority": _unique_text(
                [source_metadata.get("source_tier")],
                fallback="candidate_source_record",
                maximum=500,
            ),
            "answer_authority": "按逐题来源参考答案状态投影",
            "human_reviewed": False,
            "publication_allowed": False,
            "assets": assets,
            "shared_materials": shared_materials,
            "printed_questions": printed_questions,
        },
        "diagnosis": {
            "label": diagnosis["label"],
            "anonymized": True,
            "synthetic": False,
            "evidence_status": "provisional_anonymized",
            "summary": diagnosis["summary"],
            "common_errors": [
                {
                    "error_id": f"TEACHER-ERROR-{normalized['data_snapshot_id'][:16]}",
                    "description": diagnosis["common_error"],
                    "linked_atomic_part_ids": list(atomic_ids),
                    "cause_hypothesis": diagnosis["cause_hypothesis"],
                    "confidence": diagnosis["confidence"],
                }
            ],
            "counterevidence": diagnosis["counterevidence"],
        },
        "classroom_plan": {
            "teacher_questions": classroom["teacher_questions"],
            "anticipated_responses": classroom["anticipated_responses"],
            "practice_atomic_part_ids": practice_ids,
            "homework": classroom["homework"],
        },
    }


__all__ = [
    "THEME_REQUEST_SCHEMA_VERSION",
    "build_presentation_input",
    "materialize_theme_assets",
    "validate_theme_request",
]
