"""Read-only, source-bound complete personal visual themes for mixed papers.

``PersonalVisualThemeExportService(question_service).compile(selections)`` returns
``(items, assets)``. Items are JSON-safe mixed-basket projections; assets maps
opaque asset IDs to verified PNG bytes. The caller must freeze these bytes and
recompile/compare source_ref before exporting. This module neither renders a
document nor writes a basket, CAS, crop, label, preparation image or provider.

The original typed theme is retained, with additive image_refs. Printed nodes
also expose aggregate question_image_refs/answer_image_refs; shared materials
belong at the theme boundary. Answer nodes and assets remain teacher-only.
Source scores are not the teacher's exercise allocation or official scoring.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib

from .desktop_personal_visual_crops import pixel_bounds, source_binding
from .desktop_personal_visual_questions import PersonalVisualQuestionError, _digest
from .desktop_preparation_images import image_info
from .intake_batches_v2 import candidate_sha256
from .reader_cancellation import check_read_cancelled

SCHEMA_VERSION = "shchem.personal-visual-theme-export.v1"
ITEM_KIND = "personal_visual_theme"
_NOTICE = "个人图片题及来源参考答案为识别候选；未作教师化学审核，来源分值不是本次练习设置分值。"
_MAX_ASSETS = 2000
_MAX_BYTES = 256 * 1024 * 1024


class PersonalVisualThemeExportError(PersonalVisualQuestionError):
    def __init__(self, message_zh, code="personal_visual_theme_export_invalid"):
        super().__init__(message_zh)
        self.code = code


def _fail(message):
    raise PersonalVisualThemeExportError(message)


def _edges(node):
    """Only explicit edges; do not descend from a question into its answer."""
    evidence, visuals = [], []

    def walk(value):
        if isinstance(value, Mapping):
            for name, target in value.items():
                if name in {"atomic_parts", "answer"}:
                    continue
                if name in {"evidence_refs", "visual_object_refs"}:
                    if not isinstance(target, list) or any(not isinstance(ref, str) for ref in target):
                        _fail("图片引用格式不完整，请重新核对来源。")
                    (evidence if name == "evidence_refs" else visuals).extend(target)
                elif isinstance(target, (Mapping, list)):
                    walk(target)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return list(dict.fromkeys(evidence)), list(dict.fromkeys(visuals))


def _unique(values):
    return list(dict.fromkeys(values))


class PersonalVisualThemeExportService:
    """Adapter over an existing PersonalVisualQuestionService; no state writes."""

    def __init__(self, question_service):
        self.questions = question_service

    def compile(self, selections):
        """Expand selected printed questions to complete themes in selection order.

        The source_ref selections are the canonical full-theme selections, with
        neutral default points. Caller-selected points belong in mixed settings,
        never in original answer scores. Identical theme selections deduplicate.
        All assets are verified before anything is returned.
        """
        check_read_cancelled()
        selected, _normalized = self.questions._resolve(selections)
        order = _unique((row["batch_id"], row["theme_key"]) for row in selected)
        contexts, items, assets, raw_pages, pixel_roles = {}, [], {}, {}, {}
        for batch_id in _unique(row["batch_id"] for row in selected):
            check_read_cancelled()
            snapshot, pages = self.questions._batch(batch_id)
            if candidate_sha256(snapshot["candidate"]) != snapshot["candidate_sha256"]:
                _fail("识别内容与版本摘要不一致，请重新核对来源。")
            crops = self.questions.crop_store.get_batch(batch_id)
            rows = self.questions._rows(batch_id, snapshot, pages, crops)
            by_key = {row["key"]: row for row in rows}
            for old in selected:
                if old["batch_id"] == batch_id and (
                    old["key"] not in by_key or old["revision"] != by_key[old["key"]]["revision"]
                    or old["candidate_revision"] != snapshot["revision_token"]
                ):
                    _fail("图片题目或裁剪版本已变化，请重新选择完整主题。")
            evidence, states = self.questions._effective_evidence(batch_id, snapshot, pages, crops)
            contexts[batch_id] = {
                "snapshot": snapshot, "pages": pages, "crops": crops, "rows": rows,
                "crop_head": self.questions.crop_store.batch_revision(crops),
                "evidence": evidence, "states": states,
            }

        for batch_id, theme_key in order:
            check_read_cancelled()
            context = contexts[batch_id]
            snapshot, pages = context["snapshot"], context["pages"]
            matches = [theme for theme in snapshot["candidate"]["paper"]["theme_big_questions"]
                       if "visual-theme:" + _digest([batch_id, theme["theme_big_question_id"]]) == theme_key]
            if len(matches) != 1:
                _fail("完整主题来源不唯一，不能按题名猜测。")
            original = matches[0]
            rows = [row for row in context["rows"] if row["theme_key"] == theme_key]
            if (original["merge_status"] != "complete" or not rows
                    or len(rows) != len(original["printed_questions"])
                    or any(not row["selection_ready"] for row in rows)):
                _fail("完整主题存在拼接冲突或缺少题面，请先核对原页。")
            item = self._theme(batch_id, theme_key, original, rows, context,
                               assets, raw_pages, pixel_roles)
            items.append(item)

        # Re-open every referenced original, not just the cached first read.
        # This catches replacement during assembly as well as hash mismatches.
        for relative, raw in raw_pages.items():
            check_read_cancelled()
            if self.questions._file(relative) != raw:
                _fail("原页图片在编排期间发生变化，请重新预览。")
        for batch_id, context in contexts.items():
            check_read_cancelled()
            current, pages = self.questions._batch(batch_id)
            if (current["revision_token"] != context["snapshot"]["revision_token"]
                    or current["candidate_sha256"] != context["snapshot"]["candidate_sha256"]
                    or candidate_sha256(current["candidate"]) != current["candidate_sha256"]
                    or pages != context["pages"]):
                _fail("图片题目或来源已变化，请重新预览完整主题。")
            if self.questions.crop_store.batch_revision(
                    self.questions.crop_store.get_batch(batch_id)) != context["crop_head"]:
                _fail("个人裁剪范围已变化，请重新预览完整主题。")
        check_read_cancelled()
        return items, assets

    def _theme(self, batch_id, theme_key, original, rows, context, assets, raw_pages, pixel_roles):
        snapshot, pages = context["snapshot"], context["pages"]
        evidence, states = context["evidence"], context["states"]
        originals = {row["evidence_id"]: row for row in snapshot["candidate"]["evidence"]}
        visual_objects = {row["visual_object_id"]: row for row in original["visual_objects"]}
        material_ids = {row["shared_material_id"] for row in original["shared_materials"]}
        images, warnings = {}, [_NOTICE]
        warnings.extend(warning for row in rows for warning in row["warnings"])

        def node_refs(node):
            refs, visuals = _edges(node)
            if any(ref not in visual_objects for ref in visuals):
                _fail("主题存在未对应的图形引用，不能省略图形继续编排。")
            for visual_id in visuals:
                refs.extend(visual_objects[visual_id]["evidence_refs"])
            return _unique(refs)

        def attach(node, role):
            refs = node_refs(node)
            if any(ref not in evidence for ref in refs):
                _fail("主题引用的来源图片缺失，不能省略图片继续编排。")
            ids = []
            for ref in refs:
                check_read_cancelled()
                state, source = states[ref], evidence[ref]
                if state["stale"]:
                    _fail("主题含有来源已变化的旧个人裁剪，请重新核对后编排。")
                descriptor = self.questions._images([ref], role, evidence, pages, states)[0]
                page = pages[(source["source_file_id"], source["page_number"], source["page_sha256"])]
                bounds = pixel_bounds(source["bbox"], page["width"], page["height"],
                                      snap_roundoff=state["active"])
                if not (0 <= bounds["left"] < bounds["right"] <= page["width"]
                        and 0 <= bounds["top"] < bounds["bottom"] <= page["height"]):
                    _fail("裁剪范围超出来源图片，不能用补白图继续编排。")
                row = {"batch_id": batch_id, "candidate_revision": snapshot["revision_token"],
                       "images": [descriptor]}
                raw = self.questions._image_for_row(
                    row, snapshot, pages, descriptor["image_id"], raw_pages=raw_pages,
                    crop_records=context["crops"],
                )["bytes"]
                digest = hashlib.sha256(raw).hexdigest()
                teacher_only = role == "answer"
                if digest in pixel_roles and pixel_roles[digest] != teacher_only:
                    _fail("相同图片同时用于题面与答案，不能安全编排学生版，请核对来源范围。")
                pixel_roles[digest] = teacher_only
                binding = {
                    **source_binding(batch_id, snapshot, originals[ref], page),
                    "bbox": deepcopy(source["bbox"]),
                    "pixel_xyxy": [bounds[name] for name in ("left", "top", "right", "bottom")],
                    "page_size": [page["width"], page["height"]],
                    "crop_revision": state["crop_revision"], "crop_active": state["active"],
                }
                asset_id = "PVTHEMEIMG-" + _digest({"binding": binding, "role": role, "sha256": digest})
                info = image_info(raw)
                if (info["width"], info["height"]) != (descriptor["width"], descriptor["height"]):
                    _fail("裁片尺寸与来源定位不一致，请重新预览。")
                images[asset_id] = {
                    "asset_id": asset_id, "sha256": digest, "role": role,
                    "teacher_only": teacher_only, "caption": descriptor["caption"],
                    "binding": binding, **info,
                }
                assets[asset_id] = raw
                if (len(assets) > _MAX_ASSETS or sum(map(len, assets.values())) > _MAX_BYTES
                        or sum(map(len, raw_pages.values())) > _MAX_BYTES):
                    _fail("本次完整主题图片过多或过大，请分批编排；未截断内容。")
                ids.append(asset_id)
            node["image_refs"] = ids
            return ids

        theme = deepcopy(original)
        theme["image_refs"] = []
        for material in theme["shared_materials"]:
            theme["image_refs"].extend(attach(material, "shared_material"))
        theme["image_refs"] = _unique(theme["image_refs"])
        source_scores = []
        for printed in theme["printed_questions"]:
            nodes = [printed, *printed["atomic_parts"]]
            for node in nodes:
                if set(node.get("shared_material_refs", [])) - material_ids:
                    _fail("主题存在未对应的共同材料引用，不能猜测关联后编排。")
            question_ids, answer_ids = list(attach(printed, "question")), []
            for atomic in printed["atomic_parts"]:
                question_ids.extend(attach(atomic, "question"))
                answer = atomic["answer"]
                answer["teacher_only"] = True
                answer_ids.extend(attach(answer, "answer"))
                source_scores.append({
                    "printed_question_id": printed["printed_question_id"],
                    "atomic_part_id": atomic["atomic_part_id"], "status": answer["status"],
                    "max_score": (answer["max_score"]
                                  if answer["status"] != "missing" and answer["max_score"] > 0 else None),
                    "scoring_points": deepcopy(answer["scoring_points"]),
                    "authority": "source_reference_unverified", "teacher_only": True,
                })
            printed["question_image_refs"] = _unique(question_ids)
            printed["answer_image_refs"] = _unique(answer_ids)
        paper = deepcopy(snapshot["candidate"]["paper"])
        paper.pop("theme_big_questions")
        content = {
            "schema_version": SCHEMA_VERSION, "paper": paper, "theme": theme,
            "images": list(images.values()), "source_scores": source_scores,
            "warnings": _unique(warnings), "candidate_only": True,
            "teacher_reviewed": False, "publication_allowed": False,
        }
        selections = [{name: row[name] for name in ("batch_id", "key", "revision")} | {"points": 2}
                      for row in rows]
        source_ref = {
            "kind": ITEM_KIND, "batch_id": batch_id, "theme_key": theme_key,
            "theme_id": original["theme_big_question_id"],
            "candidate_revision": snapshot["revision_token"],
            "candidate_sha256": snapshot["candidate_sha256"],
            "crop_head_sha256": context["crop_head"],
            "selections": selections, "selection_sha256": _digest(selections),
            "content_sha256": _digest(content),
        }
        return {
            "kind": ITEM_KIND, "key": theme_key, "title_zh": original["title"],
            "source_zh": paper["title"], "source_ref": source_ref, "content": content,
            "settings": {"use_source_scores": True},
        }

    def resolve_asset(self, item, asset_id, *, audience="teacher"):
        """Recheck current source bytes and snapshot before resolving a frozen ID.

        MixedPaperService should normally compile once, then freeze all assets.
        This slower independent resolver is useful for isolated image reads.
        Returned bytes never come from a caller-provided path or data URL.
        """
        if (not isinstance(item, Mapping) or item.get("kind") != ITEM_KIND
                or not isinstance(item.get("source_ref"), Mapping)
                or not isinstance(asset_id, str) or audience not in {"student", "teacher"}):
            _fail("完整主题图片定位不正确。")
        current_items, assets = self.compile(item["source_ref"].get("selections"))
        matches = [current for current in current_items if current["key"] == item.get("key")]
        if (len(matches) != 1 or matches[0]["source_ref"] != item["source_ref"]
                or matches[0]["content"] != item.get("content")):
            _fail("完整主题快照或图片已变化，请重新预览。")
        matches = [image for image in matches[0]["content"]["images"] if image["asset_id"] == asset_id]
        if len(matches) != 1 or (audience == "student" and matches[0]["teacher_only"]):
            _fail("该图片不属于当前版本或不能用于学生题面。")
        return {"bytes": assets[asset_id], "sha256": matches[0]["sha256"],
                "content_type": matches[0]["content_type"], "caption": matches[0]["caption"]}
