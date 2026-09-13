"""Browse saved visual imports as personal questions, without central promotion.

The CAS remains the source of the recognition and its revision history. This
projection adds no OCR, provider calls or duplicate question database. Pixels
come only from the matching import's archived pages; model boxes are explicitly
reviewable display windows, never proof of a clean or complete crop.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy

from PIL import Image

from .desktop_personal_visual_attributes import (
    EXAM_TYPE_LABELS,
    GRADE_LABELS,
    TEACHING_USE_LABELS,
    PersonalVisualAttributeError,
    PersonalVisualAttributeStore,
    attribute_catalog,
    initial_attributes,
)
from .desktop_personal_visual_crops import (
    COPY_WARNING,
    PersonalVisualCropError,
    PersonalVisualCropStore,
    crop_bytes,
    pixel_bounds,
    preview_registry,
    resolved_crop,
    source_binding,
    validate_bbox,
)
from .desktop_preparation_images import (
    PreparationImageStore,
    image_info,
    normalize_image_assets,
    verify_image_bytes,
)
from .desktop_preparation_limits import MAX_MATERIALS
from .desktop_word_question_attributes import load_attribute_catalog
from .desktop_word_question_filters import chapter_filter_id, section_filter_id
from .intake_batches_v2 import CandidateCAS, candidate_sha256, strict_json_loads

SELECTION_DRAFT = "personal-visual-question-selection-v1"
_BATCH = re.compile(r"DESKTOPBATCH-[a-f0-9]{32}\Z")
_GROUPS = ("book", "chapter", "section", "knowledge", "grade", "exam", "source", "teaching_use")
_UNKNOWN = "unknown"
_CURRICULUM_GROUPS = ("book", "chapter", "section")


class PersonalVisualQuestionError(ValueError):
    def __init__(self, message):
        self.message_zh = message
        self.code = "personal_visual_question_invalid"
        super().__init__(message)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() != _UNKNOWN else ""


def _curriculum_references(nodes):
    """Resolve explicit IDs only; a bare chapter ID cannot choose between books."""
    sections, chapters = {}, {}
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        key, volume, chapter = (
            _text(node.get(field)) for field in ("node_key", "volume_id", "chapter_id")
        )
        if not all((key, volume, chapter)):
            continue
        node = {**node, "node_key": key, "volume_id": volume, "chapter_id": chapter}
        sections.setdefault(key, {})[(volume, chapter)] = node
        chapters.setdefault(chapter, {})[volume] = node
    resolved = {}
    for key, candidates in chapters.items():
        if len(candidates) == 1 and key not in sections:
            node = next(iter(candidates.values()))
            resolved[key] = {**node, "node_key": _UNKNOWN}
    for key, candidates in sections.items():
        if len(candidates) == 1:
            resolved[key] = next(iter(candidates.values()))
    return resolved


def _path_facets(path):
    volume, chapter, section = (
        path[field] for field in ("volume_id", "chapter_id", "section_key")
    )
    return {
        "book": volume,
        "chapter": chapter_filter_id(volume, chapter),
        "section": section_filter_id(volume, chapter, section)
        if section != _UNKNOWN else _UNKNOWN,
    }


def matches_personal_visual_filters(row, selection):
    """Same-group OR, cross-group AND, with one explicit curriculum path.

    Older facet-only rows remain browsable and support non-curriculum filters;
    their flattened labels cannot establish a book/chapter/section relationship.
    """
    if not isinstance(row, Mapping) or not isinstance(selection, Mapping):
        return False
    chosen = {}
    for group in _GROUPS:
        values = selection.get(group, ())
        if not isinstance(values, (list, tuple, set, frozenset)) or any(
            not isinstance(value, str) or not value for value in values
        ):
            return False
        chosen[group] = set(values)
    if selection.get("knowledge_mode", "any") not in {"any", "all"}:
        return False
    facets = row.get("facets")
    facets = facets if isinstance(facets, Mapping) else {}
    for group in _GROUPS:
        if group in _CURRICULUM_GROUPS or not chosen[group]:
            continue
        raw = facets.get(group, ())
        values = {value for value in raw if isinstance(value, str)} if isinstance(
            raw, (list, tuple, set, frozenset)
        ) else set()
        if group == "knowledge" and selection.get("knowledge_mode", "any") == "all":
            if not chosen[group].issubset(values):
                return False
        elif not chosen[group].intersection(values):
            return False
    if not any(chosen[group] for group in _CURRICULUM_GROUPS):
        return True
    paths = row.get("curriculum_paths")
    if not isinstance(paths, list):
        return False
    candidates = []
    for path in paths:
        if not isinstance(path, Mapping) or not all(
            _text(path.get(field)) for field in ("volume_id", "chapter_id")
        ) or not isinstance(path.get("section_key"), str) or not path["section_key"]:
            continue
        candidates.append(_path_facets(path))
    if not paths:
        candidates = [{group: _UNKNOWN for group in _CURRICULUM_GROUPS}]
    return any(all(not chosen[group] or path[group] in chosen[group]
                   for group in _CURRICULUM_GROUPS) for path in candidates)


def _question_text(node):
    parts = [_text(node.get("stem"))]
    for option in node.get("options", []):
        content = [_text(option.get("content"))]
        content.extend(_text(row.get("raw")) for row in option.get("chemical_expressions", []))
        parts.append(f"{_text(option.get('label'))}  " + "\n".join(dict.fromkeys(filter(None, content))))
    requirement = _text(node.get("response_requirements"))
    if requirement and requirement not in parts:
        parts.append("作答要求：" + requirement)
    # Expressions may carry information omitted in the observed prose.
    parts.extend(_text(row.get("raw")) for row in node.get("chemical_expressions", []))
    return "\n".join(dict.fromkeys(part for part in parts if part))


def _evidence_and_visuals(nodes):
    """Follow all explicit content edges, including option-only figures."""
    refs, visual_refs = [], set()
    for node in nodes:
        for content in [node, *node.get("options", [])]:
            refs.extend(content.get("evidence_refs", []))
            visual_refs.update(content.get("visual_object_refs", []))
            for expression in content.get("chemical_expressions", []):
                refs.extend(expression.get("evidence_refs", []))
    return refs, visual_refs


def _visual_evidence(theme, visual_refs):
    """Resolve explicit visual IDs; an absent edge never borrows a nearby figure."""
    known = {visual["visual_object_id"] for visual in theme["visual_objects"]}
    warnings = (["部分图形引用尚未对应到本主题的明确图形，请对照原页核对；未借用其他图形。"]
                if visual_refs - known else [])
    return [ref for visual in theme["visual_objects"]
            if visual["visual_object_id"] in visual_refs for ref in visual["evidence_refs"]], warnings


def _shared_projection(theme, nodes=None):
    """All theme materials for compilation, or only a question's explicit edges."""
    materials = theme["shared_materials"]
    warnings = []
    if nodes is not None:
        requested = {ref for node in nodes for ref in node.get("shared_material_refs", [])}
        known = {material["shared_material_id"] for material in materials}
        if requested - known:
            warnings.append("部分共同材料引用尚未对应到本主题的明确材料，请对照原页核对；未推测关联。")
        if (materials or _text(theme["context"])) and not requested:
            warnings.append("本题与主题共同材料的关联尚未明确，暂未显示未关联材料，请对照原页核对。")
        materials = [material for material in materials if material["shared_material_id"] in requested]
    refs, visual_refs = _evidence_and_visuals(materials)
    visual_evidence, visual_warnings = _visual_evidence(theme, visual_refs)
    refs.extend(visual_evidence)
    warnings.extend(visual_warnings)
    # Theme context can summarize unrelated subquestions. Keep it at the theme
    # boundary; only material IDs establish a per-question text association.
    parts = [_text(theme["context"])] if nodes is None else []
    parts.extend("\n".join(filter(None, [_text(material["content"])] + [
        _text(expression["raw"]) for expression in material.get("chemical_expressions", [])
    ])) for material in materials)
    return "\n\n".join(filter(None, parts)), refs, warnings


def _answer_text(atomic):
    answer = atomic["answer"]
    label = _text(atomic["part_label"])
    if answer["status"] == "missing":
        return f"{label} 参考答案待补充"
    parts = [label, _text(answer["answer_body"]), _text(answer["analysis"])]
    parts.extend(_text(expression["raw"]) for expression in answer["chemical_expressions"])
    if answer["max_score"] > 0:
        parts.append(f"来源参考分值：{answer['max_score']:g}分（AI识别，待对照原页）")
    else:
        parts.append("来源分值待核对")
    parts.extend(f"{point['score']:g}分：{point['description']}" for point in answer["scoring_points"])
    return "\n".join(dict.fromkeys(filter(None, parts)))


def _selection(value):
    if not isinstance(value, dict) or any(
        not isinstance(value.get(k), str) or not value[k]
        for k in ("batch_id", "key", "revision")
    ) or not _BATCH.fullmatch(value["batch_id"]):
        raise PersonalVisualQuestionError("图片选题定位不完整，请重新打开题目。")
    points = value.get("points", 2)
    if type(points) not in (int, float) or not math.isfinite(points) or not 0 < points <= 100:
        raise PersonalVisualQuestionError("本次练习分值须在0至100分之间。")
    return {k: value[k] for k in ("batch_id", "key", "revision")} | {"points": points}


class PersonalVisualQuestionService:
    def __init__(self, facade):
        self.facade = facade
        self.root = facade.paths.state_root / "visual-import-v2"
        self.state = facade.state_store
        self.attribute_store = PersonalVisualAttributeStore(facade.paths.state_root)
        self.crop_store = PersonalVisualCropStore(facade.paths.state_root)
        self.crop_previews = preview_registry(facade)

    def _file(self, relative, limit=32 * 1024 * 1024):
        path = self.root / relative
        if path.is_absolute() and not path.resolve().is_relative_to(self.root.resolve()):
            raise PersonalVisualQuestionError("图片来源位置不正确。")
        for current in (path, *path.parents):
            if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                raise PersonalVisualQuestionError("图片来源不能使用链接目录。")
            if current == self.root:
                break
        if not path.is_file() or not 0 < path.stat().st_size <= limit:
            raise PersonalVisualQuestionError("图片来源文件缺失或过大，请重新核对导入资料。")
        raw = path.read_bytes()
        if len(raw) > limit:
            raise PersonalVisualQuestionError("图片来源文件读取期间发生变化。")
        return raw

    def _batch(self, batch_id):
        from .desktop_facade import DesktopFacadeError

        if not isinstance(batch_id, str) or not _BATCH.fullmatch(batch_id):
            raise PersonalVisualQuestionError("图片导入批次不正确。")
        try:
            descriptor = self.facade._saved_visual_import_batch(batch_id)
            sources = self.facade._restore_visual_import_sources(descriptor)
        except DesktopFacadeError as exc:
            raise PersonalVisualQuestionError(exc.message_zh) from exc
        if descriptor.get("visual_status") != "completed":
            raise PersonalVisualQuestionError("该批图片尚未完成识别，原件仍保留在导入历史。")
        manifest = strict_json_loads(self._file(f"batches/{batch_id}.json"))
        if (
            manifest.get("batch_id") != batch_id
            or manifest.get("visual_status") != "completed"
            or manifest.get("candidate_only") is not True
            or manifest.get("central_question_bank_write") is not False
            or manifest.get("plan", {}).get("sources") != [source.as_manifest() for source in sources]
            or candidate_sha256(manifest.get("visual_candidate", {})) != manifest.get("visual_candidate_sha256")
        ):
            raise PersonalVisualQuestionError("图片识别结果与已保存来源不一致，请重新核对。")
        self._file(f"candidates/{batch_id}/head.json")
        cas = CandidateCAS.open(self.root / "candidates" / batch_id)
        snapshot = cas.snapshot()
        if (cas._initial_candidate_sha256 != manifest["visual_candidate_sha256"]
                or snapshot["candidate"]["batch"]["batch_id"] != batch_id):
            raise PersonalVisualQuestionError("图片题目版本与原导入批次不一致。")
        pages = {}
        sources_by_id = {source.effective_source_file_id: source for source in sources}
        for page in manifest["pixel_pages"]:
            identity = (page["source_file_id"], page["page_number"], page["page_sha256"])
            source = sources_by_id.get(page["source_file_id"])
            if (identity in pages or source is None
                    or source.role != page["source_role"]
                    or source.source_sha256 != page["source_sha256"]):
                raise PersonalVisualQuestionError("图片页面与题目、答案来源关联不一致。")
            pages[identity] = dict(page, source_name=source.filename)
        for evidence in snapshot["candidate"]["evidence"]:
            page = pages.get((evidence["source_file_id"], evidence["page_number"], evidence["page_sha256"]))
            if page is None or page["source_role"] != evidence["source_role"]:
                raise PersonalVisualQuestionError("题目引用的原始页面不完整，不能省略图片继续。")
        return snapshot, pages

    def _effective_evidence(self, batch_id, snapshot, pages, records=None):
        records = self.crop_store.get_batch(batch_id) if records is None else records
        effective, states = {}, {}
        for evidence in snapshot["candidate"]["evidence"]:
            page = pages[(evidence["source_file_id"], evidence["page_number"], evidence["page_sha256"])]
            state = resolved_crop(source_binding(batch_id, snapshot, evidence, page),
                                  evidence["bbox"], records.get(evidence["evidence_id"]))
            effective[evidence["evidence_id"]] = deepcopy(evidence)
            if state["active"]:
                effective[evidence["evidence_id"]]["bbox"] = deepcopy(state["bbox"])
            states[evidence["evidence_id"]] = state
        return effective, states

    @staticmethod
    def _images(refs, role, evidence, pages, crop_states=None):
        result = []
        for ref in dict.fromkeys(refs):
            source = evidence[ref]
            if (role == "answer") != (source["source_role"] == "answer"):
                raise PersonalVisualQuestionError("题面与答案图片角色冲突，请先核对识别范围。")
            page = pages[(source["source_file_id"], source["page_number"], source["page_sha256"])]
            box = source["bbox"]
            state = (crop_states or {}).get(ref, {})
            bounds = pixel_bounds(box, page["width"], page["height"], snap_roundoff=bool(state.get("active")))
            subject = {"evidence": source, "role": role}
            if state.get("active"):
                subject["crop_revision"] = state["crop_revision"]
            result.append({
                "image_id": "PVIMG-" + _digest(subject),
                "evidence_id": ref, "role": role,
                "caption": {"question": "题面", "shared_material": "共同材料", "answer": "参考答案"}[role]
                           + f" · 第{source['page_number']}页 · AI识别范围待核对",
                "width": bounds["right"] - bounds["left"],
                "height": bounds["bottom"] - bounds["top"],
                "page_number": source["page_number"],
            })
            if state.get("active"):
                result[-1]["crop_revision"] = state["crop_revision"]
                result[-1]["caption"] = result[-1]["caption"].replace("AI识别范围待核对", "个人裁剪范围·未作化学审核")
        return result

    def _rows(self, batch_id, snapshot, pages, crop_records=None):
        candidate = snapshot["candidate"]
        evidence, crop_states = self._effective_evidence(batch_id, snapshot, pages, crop_records)
        paper = candidate["paper"]
        taxonomy = load_attribute_catalog(self.facade.paths.workspace_root)
        known_knowledge = {row["id"] for row in taxonomy["knowledge_points"]}
        curriculum_references = _curriculum_references(taxonomy["nodes"])
        result = []
        for theme in paper["theme_big_questions"]:
            theme_key = "visual-theme:" + _digest([batch_id, theme["theme_big_question_id"]])
            for printed in theme["printed_questions"]:
                atomics = printed["atomic_parts"]
                shared_text, shared_refs, scope_warnings = _shared_projection(theme, [printed, *atomics])
                shared_images = self._images(shared_refs, "shared_material", evidence, pages, crop_states)
                refs, visual_refs = _evidence_and_visuals([printed, *atomics])
                # Visual references are explicit graph edges, not guessed from captions.
                visual_evidence, visual_warnings = _visual_evidence(theme, visual_refs)
                refs.extend(visual_evidence)
                scope_warnings.extend(visual_warnings)
                answers = [row["answer"] for row in atomics if row["answer"]["status"] != "missing"]
                answer_refs, _ = _evidence_and_visuals(answers)
                shared_ids = set(shared_refs)
                question_images = self._images([ref for ref in refs if ref not in shared_ids],
                                               "question", evidence, pages, crop_states)
                answer_images = self._images(answer_refs, "answer", evidence, pages, crop_states)
                knowledge = sorted({tag for row in atomics for name in ("primary_knowledge_K", "supporting_knowledge_K")
                                    for tag in row["classification"][name] if tag in known_knowledge})
                source_names = list(dict.fromkeys(pages[(evidence[ref]["source_file_id"], evidence[ref]["page_number"], evidence[ref]["page_sha256"])]["source_name"] for ref in refs))
                facets = {group: [] for group in _GROUPS if group != "teaching_use"}
                facets.update(knowledge=knowledge, source=[batch_id], exam=[_text(paper["paper_type"]) or _UNKNOWN], grade=[_UNKNOWN])
                curriculum_paths = []
                for atomic in atomics:
                    curriculum = atomic["curriculum"]
                    for key in [curriculum["primary_chapter"], *curriculum["secondary_chapters"]]:
                        node = curriculum_references.get(key)
                        if node:
                            path = {"volume_id": node["volume_id"], "chapter_id": node["chapter_id"],
                                    "section_key": node["node_key"]}
                            if path not in curriculum_paths:
                                curriculum_paths.append(path)
                                for group, value in _path_facets(path).items():
                                    facets[group].append(value)
                facets = {group: list(dict.fromkeys(values)) or [_UNKNOWN] for group, values in facets.items()}
                warnings = ["文字与裁剪范围为AI识别候选，请对照原页检查；未作教师化学审核。"]
                warnings.extend(scope_warnings)
                if theme["merge_status"] != "complete":
                    warnings.append("主题存在跨页拼接或内容冲突，目前不能带入备课。")
                if len(answers) != len(atomics):
                    warnings.append("本题部分小问未找到唯一对应答案；不从其他题借用答案。")
                if candidate["unaligned_answer_candidates"]:
                    warnings.append(f"本批另有{len(candidate['unaligned_answer_candidates'])}份答案尚未对应到唯一小问。")
                question_text = "\n\n".join(dict.fromkeys(filter(None, [_question_text(printed)] + [
                    (_text(row["part_label"]) + " " + _question_text(row)).strip()
                    for row in atomics
                ])))
                answer_text = "\n\n".join(_answer_text(row) for row in atomics)
                item = {
                    "batch_id": batch_id,
                    "key": "visual-question:" + _digest([batch_id, printed["printed_question_id"]]),
                    "theme_key": theme_key, "theme_title": theme["title"],
                    "title": f"{theme['title']} · 第{printed['question_number']}题",
                    "source_name": "、".join(source_names) or _text(paper["title"]),
                    "question_number": printed["question_number"],
                    "question_text": question_text, "shared_text": shared_text,
                    "answer_text": answer_text, "warnings": warnings,
                    "images": shared_images + question_images + answer_images,
                    "facets": facets,
                    "curriculum_paths": curriculum_paths,
                    "selection_ready": theme["merge_status"] == "complete" and bool(refs),
                    "candidate_revision": snapshot["revision_token"],
                    "candidate_sha256": snapshot["candidate_sha256"],
                    "teacher_reviewed": False,
                }
                # Teaching labels are not recognition content. Seal the source
                # projection before adding the independent local-label overlay.
                item["revision"] = _digest(item)
                stale_crop = any(crop_states[image["evidence_id"]]["stale"] for image in item["images"])
                item["crop_warning"] = ("识别来源已变化，旧个人裁剪未套用；修订历史保留，请重新核对范围。" if stale_crop else "")
                item["facets"]["teaching_use"] = [_UNKNOWN]
                item["attributes"] = initial_attributes(item, snapshot, pages, printed, taxonomy)
                result.append(item)
        saved = self.attribute_store.get_many(row["key"] for row in result)
        for item in result:
            attributes = item["attributes"]
            stored = saved.get(item["key"])
            item["stored_attribute_revision"] = stored["revision"] if stored else None
            if stored and stored["source_sha256"] != attributes["source_sha256"]:
                raise PersonalVisualQuestionError("图片题教学标签与原始图片来源不一致。")
            bound = bool(stored and stored["question_revision"] == item["revision"]
                         and stored["source_binding"] == attributes["source_binding"])
            item["attribute_warning"] = (
                "题面或识别范围已变化；旧教学标签仍保留在历史中，请对照当前原图重新核对。"
                if stored and not bound else ""
            )
            if bound:
                attributes = stored
                item["attributes"] = attributes
                origins = attributes["field_origins"]
                if any(origins[field] != "source_observed" for field in ("primary_knowledge", "supporting_knowledge")):
                    item["facets"]["knowledge"] = list(dict.fromkeys(
                        entry["id"] for entry in [attributes["primary_knowledge"], *attributes["supporting_knowledge"]]
                        if entry["id"] != _UNKNOWN)) or [_UNKNOWN]
                if origins["applicable_grades"] != "source_observed":
                    item["facets"]["grade"] = attributes["applicable_grades"]["values"] or [_UNKNOWN]
                if origins["original_source.exam_type"] != "source_observed":
                    item["facets"]["exam"] = [attributes["original_source"]["exam_type"]["value"]]
                item["facets"]["teaching_use"] = attributes["teaching_use_tags"] or [_UNKNOWN]
                if origins["curriculum_candidates"] != "source_observed":
                    item["curriculum_paths"] = [{field: entry[field] for field in (
                        "volume_id", "chapter_id", "section_key")} for entry in attributes["curriculum_candidates"]]
                    for group in _CURRICULUM_GROUPS:
                        item["facets"][group] = list(dict.fromkeys(
                            _path_facets(path)[group] for path in item["curriculum_paths"])) or [_UNKNOWN]
            item["attribute_revision"] = attributes["revision"]
        return result

    def catalog(self, batch_id=None):
        items, warnings = [], []
        batches = [batch_id] if batch_id is not None else [
            value["batch_id"] for value in self.state.snapshot().get("drafts", {}).values()
            if isinstance(value, dict) and value.get("kind") == "desktop_visual_import_v2"
            and value.get("visual_status") == "completed"
        ]
        for batch in dict.fromkeys(batches):
            try:
                snapshot, pages = self._batch(batch)
                items.extend(self._rows(batch, snapshot, pages))
            except Exception as exc:  # noqa: BLE001 - isolate one unavailable saved batch
                warnings.append(getattr(exc, "message_zh", "一批已识别图片暂时无法读取；请到导入历史核对。"))
        options = {group: {} for group in _GROUPS}
        taxonomy = load_attribute_catalog(self.facade.paths.workspace_root)
        names = {row["id"]: row["name"] for row in taxonomy["knowledge_points"]}
        names.update(GRADE_LABELS)
        names.update(EXAM_TYPE_LABELS)
        names.update(TEACHING_USE_LABELS)
        curriculum_options = {}
        for node in _curriculum_references(taxonomy["nodes"]).values():
            volume, chapter, section = node["volume_id"], node["chapter_id"], node["node_key"]
            book_label = _text(node.get("volume_title")) or volume
            chapter_label = book_label + " / " + (_text(node.get("chapter_title")) or chapter)
            curriculum_options[volume] = {"value": volume, "label": book_label, "volume_id": volume}
            chapter_value = chapter_filter_id(volume, chapter)
            curriculum_options[chapter_value] = {"value": chapter_value, "label": chapter_label,
                                                "volume_id": volume, "chapter_id": chapter}
            if section != _UNKNOWN:
                section_value = section_filter_id(volume, chapter, section)
                curriculum_options[section_value] = {
                    "value": section_value,
                    "label": chapter_label + " / " + (_text(node.get("section_title")) or section),
                    "volume_id": volume, "chapter_id": chapter, "section_key": section,
                }
        for row in items:
            for group, values in row["facets"].items():
                for value in values or [_UNKNOWN]:
                    if group in _CURRICULUM_GROUPS and value != _UNKNOWN:
                        if value in curriculum_options:
                            options[group][value] = curriculum_options[value]
                        continue
                    options[group][value] = {"value": value, "label": (
                        "待标注" if value == _UNKNOWN else row["source_name"]
                        if group == "source" else names.get(value, value))}
        draft = self.state.snapshot().get("drafts", {}).get(SELECTION_DRAFT, {})
        saved = draft.get("selections", []) if isinstance(draft, dict) else None
        if not isinstance(saved, list):
            warnings.append("旧选题记录不完整，请重新勾选并保存。")
            saved = []
        by_key = {row["key"]: row for row in items}
        valid, selected_keys = [], set()
        for value in saved:
            try:
                selection = _selection(value)
            except PersonalVisualQuestionError:
                continue
            row = by_key.get(selection["key"])
            if row and row["key"] not in selected_keys and all(
                row[field] == selection[field] for field in ("batch_id", "revision")
            ):
                valid.append(selection)
                selected_keys.add(row["key"])
        if len(valid) != len(saved) and batch_id is None:
            warnings.append("部分旧选题的来源已变化或暂不可读，请重新打开后勾选。")
        return {"items": items, "warnings": list(dict.fromkeys(warnings)),
                "selection": deepcopy(valid),
                "filter_options": {group: list(pairs.values())
                                   for group, pairs in options.items()}}

    def _matched_row(self, batch_id, key, revision):
        snapshot, pages = self._batch(batch_id)
        matches = [row for row in self._rows(batch_id, snapshot, pages) if row["key"] == key]
        if len(matches) != 1 or matches[0]["revision"] != revision:
            raise PersonalVisualQuestionError("图片题目或来源已经变化，请刷新列表后重新核对。")
        return matches[0], snapshot, pages

    def detail(self, batch_id, key, revision):
        return self._matched_row(batch_id, key, revision)[0]

    def attribute_options(self, batch_id, key, revision):
        """Read labels bound to the verified current image content; no writes."""
        try:
            row = self._matched_row(batch_id, key, revision)[0]
            return {
                "attributes": deepcopy(row["attributes"]),
                "catalog": attribute_catalog(load_attribute_catalog(self.facade.paths.workspace_root)),
                "history": self.attribute_store.history(key),
                "stored_revision": row["stored_attribute_revision"],
                "warning": row["attribute_warning"],
            }
        except PersonalVisualAttributeError as exc:
            raise PersonalVisualQuestionError(exc.message_zh) from exc

    def save_attributes(self, batch_id, key, revision, updates, *,
                        expected_attribute_revision, teacher_confirmed=False, edit_origin="teacher"):
        """CAS local labels only; image/CAS/content revisions never change here."""
        options = self.attribute_options(batch_id, key, revision)
        attributes = options["attributes"]
        if attributes["revision"] != expected_attribute_revision:
            raise PersonalVisualQuestionError("图片题标签已有新版本，请刷新后修改。")
        try:
            saved = self.attribute_store.save_teacher_edit(
                attributes, updates, expected_stored_revision=options["stored_revision"],
                curriculum_entries=options["catalog"], teacher_confirmed=teacher_confirmed,
                edit_origin=edit_origin,
            )
        except PersonalVisualAttributeError as exc:
            raise PersonalVisualQuestionError(exc.message_zh) from exc
        detail = self.detail(batch_id, key, revision)
        return {"detail": detail, "attributes": saved, "attribute_revision": saved["revision"]}

    def image(self, batch_id, key, revision, image_id, original=False):
        row, snapshot, pages = self._matched_row(batch_id, key, revision)
        return self._image_for_row(row, snapshot, pages, image_id, original=original)

    def _image_for_row(self, row, snapshot, pages, image_id, original=False, raw_pages=None, crop_records=None):
        descriptors = [item for item in row["images"] if item["image_id"] == image_id]
        if len(descriptors) != 1 or type(original) is not bool:
            raise PersonalVisualQuestionError("未找到本题对应的图片。")
        descriptor = descriptors[0]
        if snapshot["revision_token"] != row["candidate_revision"]:
            raise PersonalVisualQuestionError("图片题目已变化，请刷新预览。")
        effective, states = self._effective_evidence(row["batch_id"], snapshot, pages, crop_records)
        evidence = effective[descriptor["evidence_id"]]
        current = self._images([descriptor["evidence_id"]], descriptor["role"], effective, pages, states)[0]
        if current != descriptor:
            raise PersonalVisualQuestionError("个人裁剪范围已变化，请刷新预览后重新使用。")
        page = pages[(evidence["source_file_id"], evidence["page_number"], evidence["page_sha256"])]
        expected_suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(page["mime_type"])
        expected_path = f"pages/{page['page_sha256']}{expected_suffix}"
        if page["relative_path"] != expected_path:
            raise PersonalVisualQuestionError("原页位置与归档摘要不一致。")
        raw = raw_pages.get(expected_path) if raw_pages is not None else None
        if raw is None:
            raw = self._file(expected_path)
        if hashlib.sha256(raw).hexdigest() != page["page_sha256"]:
            raise PersonalVisualQuestionError("原页图片已经变化，不能使用旧识别范围。")
        info = image_info(raw)
        if (info["width"], info["height"], info["content_type"]) != (page["width"], page["height"], page["mime_type"]):
            raise PersonalVisualQuestionError("原页尺寸或格式与识别记录不一致。")
        if raw_pages is not None:
            raw_pages[expected_path] = raw
        if original:
            return {"bytes": raw, "caption": descriptor["caption"] + " · 完整原页（可能含其他题或答案）"}
        box = evidence["bbox"]
        if states[descriptor["evidence_id"]]["active"]:
            return {"bytes": crop_bytes(raw, box, page["width"], page["height"]), "caption": descriptor["caption"]}
        bounds = (math.floor(box["x"] * page["width"]), math.floor(box["y"] * page["height"]),
                  math.ceil((box["x"] + box["width"]) * page["width"]),
                  math.ceil((box["y"] + box["height"]) * page["height"]))
        with Image.open(io.BytesIO(raw)) as source:
            cropped = source.crop(bounds)
            stream = io.BytesIO()
            cropped.save(stream, "PNG")
        return {"bytes": stream.getvalue(), "caption": descriptor["caption"]}

    def _crop_context(self, batch_id, key, revision, image_id):
        snapshot, pages = self._batch(batch_id)
        records = self.crop_store.get_batch(batch_id)
        rows = self._rows(batch_id, snapshot, pages, records)
        matches = [row for row in rows if row["key"] == key and row["revision"] == revision]
        if len(matches) != 1:
            raise PersonalVisualQuestionError("图片题目或裁剪版本已变化，请刷新后重新核对。")
        row = matches[0]
        descriptors = [image for image in row["images"] if image["image_id"] == image_id]
        if len(descriptors) != 1:
            raise PersonalVisualQuestionError("未找到本题对应的裁剪图片。")
        descriptor = descriptors[0]
        evidence = next(item for item in snapshot["candidate"]["evidence"] if item["evidence_id"] == descriptor["evidence_id"])
        page = pages[(evidence["source_file_id"], evidence["page_number"], evidence["page_sha256"])]
        state = resolved_crop(source_binding(batch_id, snapshot, evidence, page), evidence["bbox"], records.get(evidence["evidence_id"]))
        raw_pages = {}
        original = self._image_for_row(row, snapshot, pages, image_id, original=True, raw_pages=raw_pages, crop_records=records)
        current = self._image_for_row(row, snapshot, pages, image_id, raw_pages=raw_pages, crop_records=records)
        affected = [{"key": item["key"], "title": item["title"], "revision": item["revision"]}
                    for item in rows if any(image["evidence_id"] == evidence["evidence_id"] for image in item["images"])]
        options = {
            "batch_id": batch_id, "key": key, "revision": revision, "image_id": image_id,
            "evidence_id": evidence["evidence_id"], "source_role": evidence["source_role"],
            "role": descriptor["role"], "role_label": {"question": "题面", "shared_material": "共同材料", "answer": "参考答案"}[descriptor["role"]],
            "source_label": page["source_name"], "theme_title": row["theme_title"],
            "page_number": page["page_number"], "page_sha256": page["page_sha256"],
            "width": page["width"], "height": page["height"],
            "original_bbox": state["original_bbox"], "current_bbox": state["bbox"],
            "pixel_bounds": pixel_bounds(state["bbox"], page["width"], page["height"], snap_roundoff=state["active"]),
            "crop_revision": state["crop_revision"], "source_binding": state["binding"],
            "crop_active": state["active"],
            "original_image": original, "current_image": current,
            "affected_questions": affected, "affected_question_keys": [item["key"] for item in affected],
            "history": self.crop_store.history(batch_id, evidence["evidence_id"]),
            "warning": (row["crop_warning"] + " " + COPY_WARNING).strip(),
        }
        return options, state, self.crop_store.batch_revision(records)

    def crop_options(self, batch_id, key, revision, image_id):
        try:
            return self._crop_context(batch_id, key, revision, image_id)[0]
        except PersonalVisualCropError as exc:
            raise PersonalVisualQuestionError(exc.message_zh) from exc

    def preview_crop(self, batch_id, key, revision, image_id, bbox, *, expected_crop_revision):
        try:
            bbox = validate_bbox(bbox)
            options, state, batch_revision = self._crop_context(batch_id, key, revision, image_id)
            if state["crop_revision"] != expected_crop_revision:
                raise PersonalVisualCropError("裁剪版本已变化，请重新打开范围编辑。")
            if pixel_bounds(bbox, options["width"], options["height"]) == options["pixel_bounds"]:
                raise PersonalVisualCropError("裁剪后的像素范围没有变化，无需保存。")
            rendered = crop_bytes(options["original_image"]["bytes"], bbox, options["width"], options["height"])
            frozen = self.crop_previews.add({
                "identity": {field: options[field] for field in ("batch_id", "key", "revision", "image_id")},
                "state": state, "batch_overlay_revision": batch_revision,
                "affected_questions": options["affected_questions"], "bbox": bbox,
                "original_sha256": hashlib.sha256(options["original_image"]["bytes"]).hexdigest(),
                "current_sha256": hashlib.sha256(options["current_image"]["bytes"]).hexdigest(),
                "preview_sha256": hashlib.sha256(rendered).hexdigest(),
            })
            return {**options, "preview_id": frozen["preview_id"], "preview_revision": frozen["preview_revision"],
                    "old_bbox": options["current_bbox"], "new_bbox": bbox,
                    "preview_image": {"bytes": rendered, "caption": options["role_label"] + " · 新裁剪范围预览（尚未保存）"}}
        except PersonalVisualCropError as exc:
            raise PersonalVisualQuestionError(exc.message_zh) from exc

    def save_crop(self, preview_id, preview_revision, *, confirmed=False, edit_origin="teacher"):
        from .intake_batches_v2 import _cas_write_lock

        try:
            if confirmed is not True:
                raise PersonalVisualCropError("请先查看新旧裁片并明确确认，再保存个人范围。")
            if not isinstance(edit_origin, str) or edit_origin not in {"teacher", "ai_source_review"}:
                raise PersonalVisualCropError("裁剪修订来源不正确。")
            with self.crop_previews.lock:
                frozen = self.crop_previews.get(preview_id, preview_revision)
                options, state, batch_revision = self._crop_context(**frozen["identity"])
                rendered = crop_bytes(options["original_image"]["bytes"], frozen["bbox"], options["width"], options["height"])
                if (state != frozen["state"] or batch_revision != frozen["batch_overlay_revision"]
                        or options["affected_questions"] != frozen["affected_questions"]
                        or hashlib.sha256(options["original_image"]["bytes"]).hexdigest() != frozen["original_sha256"]
                        or hashlib.sha256(options["current_image"]["bytes"]).hexdigest() != frozen["current_sha256"]
                        or hashlib.sha256(rendered).hexdigest() != frozen["preview_sha256"]):
                    raise PersonalVisualCropError("原页、裁剪范围或预览内容已变化，请重新预览。")
                cas_root = self.root / "candidates" / options["batch_id"]
                # Recheck the immutable CAS head under its existing lock. The
                # only mutation below is the independent local crop database.
                with _cas_write_lock(cas_root):
                    current = CandidateCAS.open(cas_root)._snapshot_unlocked()
                    if (current["revision_token"] != state["binding"]["candidate_revision"]
                            or current["candidate_sha256"] != state["binding"]["candidate_sha256"]):
                        raise PersonalVisualCropError("识别版本已变化，请重新预览裁剪范围。")
                    stored = self.crop_store.save(state, frozen["bbox"], expected_batch_revision=batch_revision, edit_origin=edit_origin)
                self.crop_previews.discard(preview_id)
            snapshot, pages = self._batch(options["batch_id"])
            affected_keys = set(options["affected_question_keys"])
            affected = [row for row in self._rows(options["batch_id"], snapshot, pages) if row["key"] in affected_keys]
            previous = {row["key"]: row["revision"] for row in options["affected_questions"]}
            detail = next(row for row in affected if row["key"] == options["key"])
            return {"batch_id": options["batch_id"], "detail": detail, "affected_rows": affected,
                    "revision_changes": [{"key": row["key"], "old_revision": previous[row["key"]], "new_revision": row["revision"]} for row in affected],
                    "crop_revision": stored["revision"],
                    "warnings": ["本次只保存个人裁剪范围；未修改原页、答案文字、识别CAS或教师化学审核状态。", COPY_WARNING]}
        except PersonalVisualCropError as exc:
            raise PersonalVisualQuestionError(exc.message_zh) from exc

    def discard_crop(self, preview_id):
        return self.crop_previews.discard(preview_id)

    def _resolve(self, selections):
        if not isinstance(selections, list) or not 1 <= len(selections) <= 100:
            raise PersonalVisualQuestionError("请选择1至100道图片题。")
        normalized = [_selection(value) for value in selections]
        if len({row["key"] for row in normalized}) != len(normalized):
            raise PersonalVisualQuestionError("图片选题重复，请重新选择。")
        available = {}
        for batch_id in dict.fromkeys(row["batch_id"] for row in normalized):
            snapshot, pages = self._batch(batch_id)
            available.update({row["key"]: row for row in self._rows(batch_id, snapshot, pages)})
        selected = []
        for selection in normalized:
            row = available.get(selection["key"])
            if row is None or any(row[key] != selection[key] for key in ("batch_id", "revision")):
                raise PersonalVisualQuestionError("图片题目或来源已经变化，请刷新列表后重新核对。")
            selected.append(row)
        return selected, normalized

    def save_selection(self, selections):
        if not isinstance(selections, list):
            raise PersonalVisualQuestionError("选题记录不完整，请重新勾选。")
        normalized = self._resolve(selections)[1] if selections else []
        self.state.save_draft(SELECTION_DRAFT, {"selections": normalized})

    def _compile_reference(self, selections):
        selected, normalized = self._resolve(selections)
        themes = {(row["batch_id"], row["theme_key"]) for row in selected}
        rows, contexts, crop_heads, crop_records, theme_contexts = [], {}, {}, {}, {}
        for batch in dict.fromkeys(row["batch_id"] for row in selected):
            snapshot, pages = self._batch(batch)
            if any(row["candidate_revision"] != snapshot["revision_token"] for row in selected if row["batch_id"] == batch):
                raise PersonalVisualQuestionError("图片题目版本已变化，请重新预览。")
            contexts[batch] = (snapshot, pages)
            crops = self.crop_store.get_batch(batch)
            crop_records[batch] = crops
            crop_heads[batch] = self.crop_store.batch_revision(crops)
            rows.extend(row for row in self._rows(batch, snapshot, pages, crops) if (batch, row["theme_key"]) in themes)
            effective, states = self._effective_evidence(batch, snapshot, pages, crops)
            for theme in snapshot["candidate"]["paper"]["theme_big_questions"]:
                theme_key = "visual-theme:" + _digest([batch, theme["theme_big_question_id"]])
                if (batch, theme_key) not in themes:
                    continue
                shared_text, shared_refs, shared_warnings = _shared_projection(theme)
                theme_contexts[(batch, theme_key)] = (
                    shared_text, self._images(shared_refs, "shared_material", effective, pages, states),
                    shared_warnings,
                )
        if any(not row["selection_ready"] for row in rows):
            raise PersonalVisualQuestionError("所选主题存在拼接冲突或缺少题面，请先核对原页。")
        texts = ["【个人图片题备课参考：AI识别候选，须对照原图】",
                 "本次按完整主题带入共同材料、全部小题与独立参考答案；不将图片转写冒充教材原句。"]
        seen_themes, assets, raw_images, warnings, raw_pages = set(), {}, {}, [], {}

        def append_images(row, descriptors, prefix):
            for descriptor in descriptors:
                response = self._image_for_row(
                    row, *contexts[row["batch_id"]], descriptor["image_id"], raw_pages=raw_pages,
                    crop_records=crop_records[row["batch_id"]],
                )
                raw = response["bytes"]
                digest = hashlib.sha256(raw).hexdigest()
                if digest not in assets:
                    assets[digest] = {
                        "asset_id": "IMG-" + digest, "sha256": digest,
                        "caption": f"{row['theme_title'][:70]} · {descriptor['caption']}",
                        "source": row["source_name"][:500],
                        "purpose": "教师参考答案，不用于学生题面" if descriptor["role"] == "answer" else "依据原页核对题干、共享材料与化学图形",
                        **image_info(raw),
                    }
                    raw_images[digest] = raw
                elif descriptor["role"] == "answer" and assets[digest]["purpose"] != "教师参考答案，不用于学生题面":
                    # Identical pixels may be cited on both roles. Preserve the
                    # restrictive use and every occurrence instead of masking answers.
                    assets[digest]["purpose"] = "教师参考答案，不用于学生题面"
                    warnings.append("一张相同图片同时被题面和答案引用；已限定为教师参考，请核对范围后使用。")
                texts.append(f"{prefix} · {descriptor['caption']}：" + assets[digest]["caption"])

        for row in rows:
            theme_identity = (row["batch_id"], row["theme_key"])
            if theme_identity not in seen_themes:
                shared_text, shared_images, shared_warnings = theme_contexts[theme_identity]
                texts.extend([f"主题：{row['theme_title']}\n来源：{row['source_name']}", shared_text])
                warnings.extend(shared_warnings)
                # Compilation has the whole theme's scope, including shared
                # materials that no printed question references. This internal
                # projection uses the same source/role/crop checks as a detail.
                append_images({**row, "images": shared_images}, shared_images, "主题共同材料")
                seen_themes.add(theme_identity)
            texts.extend([f"第{row['question_number']}题\n{row['question_text']}",
                          "【教师参考答案／AI识别，未独立核验】\n" + row["answer_text"]])
            warnings.extend(row["warnings"])
            append_images(row, [image for image in row["images"] if image["role"] != "shared_material"],
                          f"第{row['question_number']}题")
        for batch, (snapshot, _) in contexts.items():
            current, _ = self._batch(batch)
            if current["revision_token"] != snapshot["revision_token"]:
                raise PersonalVisualQuestionError("图片题目版本已变化，请重新预览。")
            if self.crop_store.batch_revision(self.crop_store.get_batch(batch)) != crop_heads[batch]:
                raise PersonalVisualQuestionError("个人裁剪范围已变化，请重新预览完整主题。")
        warnings = list(dict.fromkeys(warnings))
        materials = "\n\n".join(filter(None, texts)) + "\n\n待核对提醒：\n" + "\n".join(warnings)
        if len(materials) > MAX_MATERIALS:
            raise PersonalVisualQuestionError("完整主题参考超过40000字，请减少选题；没有截断内容。")
        reference = {"source_kind": "personal_visual", "materials": materials,
                     "warnings": warnings, "selections": normalized, "include_images": True,
                     "image_assets": normalize_image_assets(list(assets.values())), "image_issues": [],
                     "theme_count": len(themes), "question_count": len(rows)}
        return reference, raw_images

    def reference(self, selections):
        return self._compile_reference(selections)[0]

    def import_reference(self, reference, existing_assets):
        if not isinstance(reference, dict):
            raise PersonalVisualQuestionError("图片题参考不完整，请重新预览。")
        compiled, raw_images = self._compile_reference(reference.get("selections"))
        if _digest(reference) != _digest(compiled):
            raise PersonalVisualQuestionError("图片题参考已经变化，请重新预览后带入备课。")
        existing = normalize_image_assets(existing_assets)
        if existing != existing_assets:
            raise PersonalVisualQuestionError("当前备课图片记录发生变化，请重新核对。")
        additions = [row for row in compiled["image_assets"] if row["sha256"] not in {asset["sha256"] for asset in existing}]
        merged = normalize_image_assets(existing + additions)
        for asset in compiled["image_assets"]:
            verify_image_bytes(asset, raw_images[asset["sha256"]])
        store = PreparationImageStore(self.facade.paths.task_root / "preparation-v1" / "images")
        for asset in existing:
            store.load(asset)
        for asset in additions:
            if store.import_bytes(raw_images[asset["sha256"]], asset["caption"], asset["source"], asset["purpose"]) != asset:
                raise PersonalVisualQuestionError("图片保存后不一致，请重新核对。")
        return {"materials": compiled["materials"], "warnings": compiled["warnings"], "image_assets": merged}
