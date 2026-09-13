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
from copy import deepcopy

from PIL import Image

from .desktop_preparation_images import (
    PreparationImageStore,
    image_info,
    normalize_image_assets,
    verify_image_bytes,
)
from .desktop_preparation_limits import MAX_MATERIALS
from .desktop_word_question_attributes import load_attribute_catalog
from .intake_batches_v2 import CandidateCAS, candidate_sha256, strict_json_loads

SELECTION_DRAFT = "personal-visual-question-selection-v1"
_BATCH = re.compile(r"DESKTOPBATCH-[a-f0-9]{32}\Z")
_GROUPS = ("book", "chapter", "section", "knowledge", "grade", "exam", "source")
_UNKNOWN = "unknown"


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

    @staticmethod
    def _images(refs, role, evidence, pages):
        result = []
        for ref in dict.fromkeys(refs):
            source = evidence[ref]
            if (role == "answer") != (source["source_role"] == "answer"):
                raise PersonalVisualQuestionError("题面与答案图片角色冲突，请先核对识别范围。")
            page = pages[(source["source_file_id"], source["page_number"], source["page_sha256"])]
            box = source["bbox"]
            left, top = math.floor(box["x"] * page["width"]), math.floor(box["y"] * page["height"])
            right = math.ceil((box["x"] + box["width"]) * page["width"])
            bottom = math.ceil((box["y"] + box["height"]) * page["height"])
            result.append({
                "image_id": "PVIMG-" + _digest({"evidence": source, "role": role}),
                "evidence_id": ref, "role": role,
                "caption": {"question": "题面", "shared_material": "共同材料", "answer": "参考答案"}[role]
                           + f" · 第{source['page_number']}页 · AI识别范围待核对",
                "width": right - left,
                "height": bottom - top,
                "page_number": source["page_number"],
            })
        return result

    def _rows(self, batch_id, snapshot, pages):
        candidate = snapshot["candidate"]
        evidence = {row["evidence_id"]: row for row in candidate["evidence"]}
        paper = candidate["paper"]
        taxonomy = load_attribute_catalog(self.facade.paths.workspace_root)
        known_knowledge = {row["id"] for row in taxonomy["knowledge_points"]}
        curriculum_nodes = {row["node_key"]: row for row in taxonomy["nodes"]}
        curriculum_chapters = {row["chapter_id"]: row for row in taxonomy["nodes"]}
        result = []
        for theme in paper["theme_big_questions"]:
            theme_key = "visual-theme:" + _digest([batch_id, theme["theme_big_question_id"]])
            shared_refs, shared_visuals = _evidence_and_visuals(theme["shared_materials"])
            shared_refs += [ref for visual in theme["visual_objects"]
                            if visual["visual_object_id"] in shared_visuals for ref in visual["evidence_refs"]]
            shared_text = "\n\n".join(filter(None, [_text(theme["context"])] + [
                "\n".join(filter(None, [_text(row["content"])] + [
                    _text(expression["raw"]) for expression in row.get("chemical_expressions", [])
                ])) for row in theme["shared_materials"]
            ]))
            shared_images = self._images(shared_refs, "shared_material", evidence, pages)
            for printed in theme["printed_questions"]:
                atomics = printed["atomic_parts"]
                refs, visual_refs = _evidence_and_visuals([printed, *atomics])
                # Visual references are explicit graph edges, not guessed from captions.
                refs += [ref for visual in theme["visual_objects"]
                         if visual["visual_object_id"] in visual_refs for ref in visual["evidence_refs"]]
                answers = [row["answer"] for row in atomics if row["answer"]["status"] != "missing"]
                answer_refs, _ = _evidence_and_visuals(answers)
                question_images = self._images(refs, "question", evidence, pages)
                answer_images = self._images(answer_refs, "answer", evidence, pages)
                knowledge = sorted({tag for row in atomics for name in ("primary_knowledge_K", "supporting_knowledge_K")
                                    for tag in row["classification"][name] if tag in known_knowledge})
                source_names = list(dict.fromkeys(pages[(evidence[ref]["source_file_id"], evidence[ref]["page_number"], evidence[ref]["page_sha256"])]["source_name"] for ref in refs))
                facets = {group: [] for group in _GROUPS}
                facets.update(knowledge=knowledge, source=[batch_id], exam=[_text(paper["paper_type"]) or _UNKNOWN], grade=[_UNKNOWN])
                for atomic in atomics:
                    curriculum = atomic["curriculum"]
                    for key in [curriculum["primary_chapter"], *curriculum["secondary_chapters"]]:
                        node = curriculum_nodes.get(key) or curriculum_chapters.get(key)
                        if node:
                            facets["book"].append(node["volume_id"])
                            facets["chapter"].append(node["chapter_id"])
                            if key in curriculum_nodes:
                                facets["section"].append(node["node_key"])
                facets = {group: list(dict.fromkeys(values)) or [_UNKNOWN] for group, values in facets.items()}
                warnings = ["文字与裁剪范围为AI识别候选，请对照原页检查；未作教师化学审核。"]
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
                    "selection_ready": theme["merge_status"] == "complete" and bool(question_images),
                    "candidate_revision": snapshot["revision_token"],
                    "candidate_sha256": snapshot["candidate_sha256"],
                    "teacher_reviewed": False,
                }
                item["revision"] = _digest(item)
                result.append(item)
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
        for node in taxonomy["nodes"]:
            names.update({node["volume_id"]: node["volume_title"],
                          node["chapter_id"]: node["chapter_title"],
                          node["node_key"]: node["section_title"]})
        for row in items:
            for group, values in row["facets"].items():
                for value in values or [_UNKNOWN]:
                    options[group][value] = ("待标注" if value == _UNKNOWN else
                                            row["source_name"] if group == "source" else names.get(value, value))
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
                "filter_options": {group: [{"value": key, "label": label} for key, label in pairs.items()]
                                   for group, pairs in options.items()}}

    def _matched_row(self, batch_id, key, revision):
        snapshot, pages = self._batch(batch_id)
        matches = [row for row in self._rows(batch_id, snapshot, pages) if row["key"] == key]
        if len(matches) != 1 or matches[0]["revision"] != revision:
            raise PersonalVisualQuestionError("图片题目或来源已经变化，请刷新列表后重新核对。")
        return matches[0], snapshot, pages

    def detail(self, batch_id, key, revision):
        return self._matched_row(batch_id, key, revision)[0]

    def image(self, batch_id, key, revision, image_id, original=False):
        row, snapshot, pages = self._matched_row(batch_id, key, revision)
        return self._image_for_row(row, snapshot, pages, image_id, original=original)

    def _image_for_row(self, row, snapshot, pages, image_id, original=False, raw_pages=None):
        descriptors = [item for item in row["images"] if item["image_id"] == image_id]
        if len(descriptors) != 1 or type(original) is not bool:
            raise PersonalVisualQuestionError("未找到本题对应的图片。")
        descriptor = descriptors[0]
        if snapshot["revision_token"] != row["candidate_revision"]:
            raise PersonalVisualQuestionError("图片题目已变化，请刷新预览。")
        evidence = next(item for item in snapshot["candidate"]["evidence"] if item["evidence_id"] == descriptor["evidence_id"])
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
        bounds = (math.floor(box["x"] * page["width"]), math.floor(box["y"] * page["height"]),
                  math.ceil((box["x"] + box["width"]) * page["width"]),
                  math.ceil((box["y"] + box["height"]) * page["height"]))
        with Image.open(io.BytesIO(raw)) as source:
            cropped = source.crop(bounds)
            stream = io.BytesIO()
            cropped.save(stream, "PNG")
        return {"bytes": stream.getvalue(), "caption": descriptor["caption"]}

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
        rows, contexts = [], {}
        for batch in dict.fromkeys(row["batch_id"] for row in selected):
            snapshot, pages = self._batch(batch)
            if any(row["candidate_revision"] != snapshot["revision_token"] for row in selected if row["batch_id"] == batch):
                raise PersonalVisualQuestionError("图片题目版本已变化，请重新预览。")
            contexts[batch] = (snapshot, pages)
            rows.extend(row for row in self._rows(batch, snapshot, pages) if (batch, row["theme_key"]) in themes)
        if any(not row["selection_ready"] for row in rows):
            raise PersonalVisualQuestionError("所选主题存在拼接冲突或缺少题面，请先核对原页。")
        texts = ["【个人图片题备课参考：AI识别候选，须对照原图】",
                 "本次按完整主题带入共同材料、全部小题与独立参考答案；不将图片转写冒充教材原句。"]
        seen_themes, assets, raw_images, warnings, raw_pages = set(), {}, {}, [], {}
        for row in rows:
            if row["theme_key"] not in seen_themes:
                texts.extend([f"主题：{row['theme_title']}\n来源：{row['source_name']}", row["shared_text"]])
                seen_themes.add(row["theme_key"])
            texts.extend([f"第{row['question_number']}题\n{row['question_text']}",
                          "【教师参考答案／AI识别，未独立核验】\n" + row["answer_text"]])
            warnings.extend(row["warnings"])
            for descriptor in row["images"]:
                response = self._image_for_row(
                    row, *contexts[row["batch_id"]], descriptor["image_id"], raw_pages=raw_pages
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
                texts.append(f"第{row['question_number']}题 · {descriptor['caption']}：" + assets[digest]["caption"])
        for batch, (snapshot, _) in contexts.items():
            current, _ = self._batch(batch)
            if current["revision_token"] != snapshot["revision_token"]:
                raise PersonalVisualQuestionError("图片题目版本已变化，请重新预览。")
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
