"""Source-bound native Word and original-paper assembly, without model calls.

The public preview contains readable blocks and opaque image handles. Original
Word bytes are resolved only in memory; frozen core plans and verified images
live in a private, immutable preview directory, never in the master question bank.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from .desktop_library_session import snapshot_reader_graph
from .desktop_state import utc_now
from .reader_cancellation import check_read_cancelled

REQUEST_SCHEMA = "shchem.desktop-mixed-paper-request.v1"
PREVIEW_SCHEMA = "shchem.desktop-mixed-paper-preview.v1"
BASKET_SCHEMA = "shchem.desktop-mixed-basket.v1"
_ACTIVE = "mixed-paper-preview-active"
_PREFIX = "mixed-preview-"
_SCOPES = {"master", "wave1", "supplemental"}
_SHA = re.compile(r"[0-9a-f]{64}")
_NOTICE = (
    "本次统一导出学生版和教师版 DOCX；PDF 未生成，尚未进行成品分页与版面目视验收。"
)


class MixedPaperError(ValueError):
    def __init__(self, message: str, code: str = "mixed_paper_invalid"):
        super().__init__(message)
        self.message_zh = message
        self.code = code


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _kind(item: Mapping[str, Any]) -> str:
    kind = item.get("item_kind", "core_theme")
    if kind not in {"core_theme", "word_question"}:
        raise MixedPaperError("题篮包含不支持的来源类型，请重新选择。")
    if kind == "core_theme" and str(item.get("key", "")).startswith("word:"):
        raise MixedPaperError("Word 题篮来源类型缺失，未按原卷题猜测。")
    return kind


def is_mixed_preview_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def _word_key(native_key: str) -> str:
    return "word:" + _digest({"kind": "word_question", "native_key": native_key})


def _word_ref(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "word_question",
        **{
            key: row[key]
            for key in (
                "key",
                "revision",
                "source_sha256",
                "origin_block_start",
                "batch_id",
                "archive_source_id",
            )
        },
    }


def _word_content(row: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(
        {
            key: row.get(key)
            for key in (
                "title",
                "chapter",
                "question_blocks",
                "context_blocks",
                "answer_blocks",
                "export_ready",
                "selection_ready",
                "warnings",
                "boundary_status",
            )
        }
    )


def _read_verified(path: Path, digest: str) -> bytes:
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        raise MixedPaperError("本地预览缺少内容校验值。")
    data = path.read_bytes()
    if _sha(data) != digest:
        raise MixedPaperError(
            "已确认预览的内容发生变化，请重新预览。", "paper_preview_stale"
        )
    return data


class MixedPaperService:
    def __init__(
        self,
        facade,
        *,
        core_bundle_builder=None,
        docx_builder=None,
        word_validator=None,
    ):
        self.facade = facade
        self.state = facade.state_store
        self.root = facade.paths.state_root / "mixed-paper-previews"
        self._core_bundle_builder = core_bundle_builder
        self._docx_builder = docx_builder
        self._word_validator = word_validator

    def add_word_questions(self, selections) -> int:
        rows, _ = self.facade._word_questions()._resolve(selections)
        items = []
        for row in rows:
            if row.get("selection_ready") is not True:
                raise MixedPaperError("请先核对 Word 题目范围，再加入统一题篮。")
            items.append(
                {
                    "item_kind": "word_question",
                    "key": _word_key(row["key"]),
                    "title_zh": row["title"],
                    "source_zh": row["source_name"],
                    "word_selection": {
                        key: row[key] for key in ("key", "revision", "points")
                    },
                    "source_ref": _word_ref(row),
                    "added_at": utc_now(),
                }
            )
        return self.state.add_many_to_basket(items)

    def _resolve_words(self, basket):
        service = self.facade._word_questions()
        choices, bindings = [], []
        for item in basket:
            if _kind(item) != "word_question":
                continue
            chosen, ref = item.get("word_selection"), item.get("source_ref")
            if not isinstance(chosen, dict) or not isinstance(ref, dict):
                raise MixedPaperError("Word 题篮来源记录不完整，请重新选题。")
            if item.get("key") != _word_key(str(chosen.get("key", ""))):
                raise MixedPaperError("Word 题篮身份与所选原题不一致。")
            if ref.get("kind") != "word_question" or any(
                not ref.get(key)
                for key in ("batch_id", "archive_source_id", "source_sha256")
            ):
                raise MixedPaperError("Word 题篮原件定位不完整，请重新选题。")
            choices.append(chosen)
            bindings.append(ref)
        if not choices:
            return {}, {}
        # Persisted exact locators avoid opening unrelated imported Word files
        # after restart. _resolve still revalidates descriptor and source bytes.
        with service._lock:
            for chosen, ref in zip(choices, bindings, strict=True):
                service._locations[chosen["key"]] = (
                    ref["batch_id"],
                    ref["archive_source_id"],
                )
        rows, inventory = service._resolve(choices)
        result = {}
        for row, ref in zip(rows, bindings, strict=True):
            if _word_ref(row) != ref:
                raise MixedPaperError(
                    "Word 原件或题目范围已经变化，请刷新题篮。", "paper_preview_stale"
                )
            result[_word_key(row["key"])] = row
        return result, inventory

    def _resolve(self, basket, *, catalog_loader=None):
        if (
            not isinstance(basket, list)
            or len(basket) > 100
            or any(not isinstance(item, dict) for item in basket)
        ):
            raise MixedPaperError("题篮格式不正确。")
        raw_keys = [item.get("key") for item in basket]
        if any(not isinstance(key, str) or not key for key in raw_keys) or len(
            set(raw_keys)
        ) != len(raw_keys):
            raise MixedPaperError("题篮项目身份缺失或重复。")
        words, inventory = (
            self._resolve_words(basket)
            if any(_kind(item) == "word_question" for item in basket)
            else ({}, {})
        )
        catalogs, result, seen = {}, [], set()
        for item in basket:
            check_read_cancelled()
            key = item["key"]
            if _kind(item) == "word_question":
                row = words[key]
                result.append(
                    {
                        "kind": "word_question",
                        "key": key,
                        "title_zh": row["title"],
                        "source_zh": row["source_name"],
                        "source_ref": _word_ref(row),
                        "content": _word_content(row),
                        "settings": {"points": row["points"]},
                    }
                )
                continue
            scope = item.get("scope")
            if scope not in _SCOPES:
                raise MixedPaperError("原卷题库范围不正确。")
            if scope not in catalogs:
                catalogs[scope] = (catalog_loader or self.facade.paper_theme_catalog)(
                    scope
                )
            catalog = catalogs[scope]
            snapshot = self.facade._paper_catalog_snapshot_id(catalog)
            if item.get("data_snapshot_id") and item["data_snapshot_id"] != snapshot:
                raise MixedPaperError(
                    "原卷题篮来自旧题库版本，请重新选择。", "theme_snapshot_stale"
                )
            identity = item.get("source_identity_sha256") or key
            matches = []
            for entry in catalog.get("papers", []):
                paper = entry.get("paper", {})
                for group in entry.get("theme_groups", []):
                    actual = _digest(
                        {
                            "scope": scope,
                            "paper": paper.get("id"),
                            "theme": group.get("theme", {}).get("id"),
                        }
                    )
                    if actual == identity:
                        matches.append((paper, group, actual))
            if len(matches) != 1:
                raise MixedPaperError("原卷主题来源缺失或不唯一，不能按题名猜测。")
            paper, group, identity = matches[0]
            if identity in seen:
                continue  # Complete same-source themes merge at first occurrence.
            seen.add(identity)
            source_ref = {
                "kind": "core_theme",
                "scope": scope,
                "paper_id": paper["id"],
                "theme_id": group["theme"]["id"],
                "source_identity_sha256": identity,
                "data_snapshot_id": snapshot,
            }
            result.append(
                {
                    "kind": "core_theme",
                    "key": key,
                    "title_zh": group["theme"]["title"],
                    "source_zh": item.get("source_zh")
                    or paper.get("title", "原卷资料"),
                    "source_ref": source_ref,
                    "content": {
                        **deepcopy(group),
                        "paper": deepcopy(paper),
                        "scope": scope,
                        "data_snapshot_id": snapshot,
                    },
                    "settings": {
                        "score_per_atomic": 2,
                        "answer_space_lines": 0,
                        "atomic_settings": {},
                    },
                }
            )
        return result, words, inventory, catalogs

    def projection(self):
        basket = self.state.basket()
        items, _, _, _ = self._resolve(basket)
        return {
            "schema_version": BASKET_SCHEMA,
            "basket_sha256": _digest(basket),
            "items": items,
            "warnings": [
                "默认分值为本次练习设置，不是原卷官方分值；Word 原题答题区原样保留。"
            ],
        }

    def _core_context(self):
        readers = snapshot_reader_graph(
            (
                self.facade._themes,
                self.facade._supplemental,
                *self.facade._paper_export_readers(),
            )
        )
        themes, supplemental, visual, crops, workbench, direct = readers
        bindings = {}

        def catalog(scope):
            return self.facade._with_presentation_snapshot(
                supplemental.theme_groups()
                if scope == "supplemental"
                else themes.groups(scope)
            )

        def detail(scope, node):
            if scope == "supplemental":
                return supplemental.detail(node)
            if scope == "wave1":
                return visual.detail(node)
            try:
                return direct.detail(node)
            except Exception as exc:
                if getattr(exc, "status", None) != 404:
                    raise
            summary = (
                workbench.atomic_detail(node)
                .get("node", {})
                .get("crosswalk_summary", {})
            )
            ids = summary.get("wave1_node_ids")
            if (
                summary.get("state") != "exact"
                or not isinstance(ids, list)
                or len(ids) != 1
                or not isinstance(ids[0], str)
            ):
                raise MixedPaperError("这道原卷题尚无可导出的精确题图。")
            bindings[node] = ids[0]
            return visual.detail(ids[0])

        def crop(scope, node, image):
            if scope == "supplemental":
                return supplemental.question_crop(node, image)
            if scope == "wave1" or node in bindings:
                return crops.question_crop(bindings.get(node, node), image)
            return direct.question_crop(node, image)

        def answer(scope, node, image):
            if scope != "master" or node in bindings:
                raise MixedPaperError("该来源尚无独立答案图通路，未借用其他题图。")
            return direct.teacher_answer_crop(node, image)

        return {
            "theme_catalog_loader": catalog,
            "detail_loader": detail,
            "crop_loader": crop,
            "answer_crop_loader": answer,
        }

    @staticmethod
    def _settings(item, raw):
        if not isinstance(raw, dict):
            raise MixedPaperError("本次练习分值设置格式不正确。")
        if item["kind"] == "word_question":
            if set(raw) - {"points"}:
                raise MixedPaperError(
                    "Word 原题仅支持本次整题分值，不能添加重复答题线。"
                )
            points = raw.get("points", item["settings"]["points"])
            if (
                type(points) not in (int, float)
                or not math.isfinite(points)
                or not 0 < points <= 100
            ):
                raise MixedPaperError("Word 每题分值须大于 0 且不超过 100 分。")
            return {"points": points}
        if set(raw) - {"score_per_atomic", "answer_space_lines", "atomic_settings"}:
            raise MixedPaperError("原卷主题包含未知编排设置。")
        return {**item["settings"], **deepcopy(raw)}

    def create_preview(self, payload):
        allowed = {
            "schema_version",
            "title",
            "subtitle",
            "mode",
            "duration_minutes",
            "show_question_scores",
            "basket_sha256",
            "section_order",
            "settings_by_key",
        }
        if (
            not isinstance(payload, Mapping)
            or set(payload) - allowed
            or payload.get("schema_version") != REQUEST_SCHEMA
        ):
            raise MixedPaperError("统一组卷预览请求版本或字段不正确。")
        title, subtitle = payload.get("title"), payload.get("subtitle", "")
        duration, show = (
            payload.get("duration_minutes", 40),
            payload.get("show_question_scores", False),
        )
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > 120
            or not isinstance(subtitle, str)
            or len(subtitle) > 180
        ):
            raise MixedPaperError("请填写有效的练习标题和副标题。")
        if (
            type(duration) is not int
            or not 1 <= duration <= 300
            or type(show) is not bool
            or payload.get("mode", "daily_practice")
            not in {"daily_practice", "mock_exam"}
        ):
            raise MixedPaperError("练习时长、模式或题面分数设置不正确。")
        basket = self.state.basket()
        if payload.get("basket_sha256") != _digest(basket):
            raise MixedPaperError(
                "题篮已变化，请刷新后重新预览。", "paper_preview_stale"
            )
        context = (
            self._core_context()
            if any(_kind(item) == "core_theme" for item in basket)
            else {}
        )
        items, words, inventory, _ = self._resolve(
            basket, catalog_loader=context.get("theme_catalog_loader")
        )
        by_key = {item["key"]: item for item in items}
        order, settings = (
            payload.get("section_order"),
            payload.get("settings_by_key", {}),
        )
        if (
            not isinstance(order, list)
            or not order
            or any(not isinstance(key, str) or key not in by_key for key in order)
            or len(set(order)) != len(order)
        ):
            raise MixedPaperError(
                "请按题篮中的完整项目选择与排序，不能重复或插入其他来源。"
            )
        if not isinstance(settings, dict) or set(settings) - set(order):
            raise MixedPaperError("分值设置与当前卷所选项目不一致。")
        normalized = {
            key: self._settings(by_key[key], settings.get(key, {})) for key in order
        }
        word_rows = [
            {
                **deepcopy(words[key]),
                "points": normalized[key]["points"],
                "source_bytes": inventory[words[key]["source_id"]][0].content,
            }
            for key in order
            if by_key[key]["kind"] == "word_question"
        ]
        if any(row.get("export_ready") is not True for row in word_rows):
            raise MixedPaperError(
                "所选 Word 题目仍有题干答案范围待修订，请先在 Word 范围页核对后再生成统一预览。"
            )
        if word_rows:
            from .desktop_word_question_export import _prepare

            # The student preview must obey the same cross-question answer
            # separation as the final native XML export.
            (self._word_validator or _prepare)(word_rows)
        token = uuid4().hex
        folder = self.root / token
        folder.mkdir(parents=True, exist_ok=False)
        manifest, sections, images, blockers = [], [], {}, []
        for index, key in enumerate(order):
            check_read_cancelled()
            item = by_key[key]
            base = {
                field: deepcopy(item[field])
                for field in ("kind", "key", "title_zh", "source_zh", "source_ref")
            }
            if item["kind"] == "word_question":
                row = deepcopy(words[key])
                row["points"] = normalized[key]["points"]
                data = inventory[row["source_id"]][0].content
                if _sha(data) != row["source_sha256"]:
                    raise MixedPaperError("Word 原件已变化，请重新选择。")
                student = self._word_blocks(
                    row,
                    data,
                    ("context_blocks", "question_blocks"),
                    folder,
                    images,
                    blockers,
                )
                teacher = (
                    deepcopy(student)
                    + [{"kind": "text", "text": f"本次练习分值：{row['points']:g} 分"}]
                    + self._word_blocks(
                        row, data, ("answer_blocks",), folder, images, blockers
                    )
                )
                if not row.get("answer_blocks"):
                    teacher.append(
                        {
                            "kind": "text",
                            "text": "当前选定范围未识别到本题答案，请核对原教案与题答边界；这不表示原文没有答案。",
                        }
                    )
                sections.append(
                    {
                        **base,
                        "student_blocks": student,
                        "teacher_blocks": teacher,
                        "points": row["points"],
                    }
                )
                manifest.append(
                    {
                        "kind": "word_question",
                        "key": key,
                        "source_ref": base["source_ref"],
                        "settings": normalized[key],
                        "content_sha256": _digest(_word_content(row)),
                    }
                )
            else:
                from .paper_export_workbench import build_core_export_bundle

                ref = item["source_ref"]
                request = {
                    "title_zh": title.strip(),
                    "subtitle_zh": subtitle.strip() or None,
                    "duration_minutes": duration,
                    "numbering_mode": "continuous_across_paper",
                    "show_question_scores": show,
                    "score_per_atomic": normalized[key]["score_per_atomic"],
                    "answer_space_lines": normalized[key]["answer_space_lines"],
                    "selections": [
                        {
                            "scope": ref["scope"],
                            "selection_unit": "theme",
                            "theme_id": ref["theme_id"],
                            "target_atomic_id": None,
                            "expected_data_snapshot_id": ref["data_snapshot_id"],
                        }
                    ],
                }
                if normalized[key]["atomic_settings"]:
                    request["atomic_settings"] = normalized[key]["atomic_settings"]
                asset_root = folder / f"core-{index}"
                bundle = (self._core_bundle_builder or build_core_export_bundle)(
                    request,
                    **context,
                    asset_root=asset_root,
                    bundle_id=f"mixed_{token}_{index}",
                )
                if len(bundle["blueprint"]["theme_bundles"]) != 1:
                    raise MixedPaperError("原卷内容没有保持为单个完整主题。")
                bundle_bytes = _json_bytes(bundle)
                bundle_name = f"core-{index}.json"
                (folder / bundle_name).write_bytes(bundle_bytes)
                student, teacher = [
                    self._core_blocks(bundle, audience, asset_root, folder, images)
                    for audience in ("student", "teacher")
                ]
                points = sum(
                    section["theme_score"]
                    for section in bundle["student_plan"]["visible"]["theme_sections"]
                )
                sections.append(
                    {
                        **base,
                        "student_blocks": student,
                        "teacher_blocks": teacher,
                        "points": points,
                    }
                )
                manifest.append(
                    {
                        "kind": "core_plan",
                        "key": key,
                        "source_ref": ref,
                        "bundle_path": bundle_name,
                        "bundle_sha256": _sha(bundle_bytes),
                        "asset_root": f"core-{index}",
                        "asset_files": self._core_asset_hashes(bundle, asset_root),
                    }
                )
        blockers = list(dict.fromkeys(blockers))
        model = {
            "schema_version": PREVIEW_SCHEMA,
            "title": title.strip(),
            "subtitle": subtitle.strip(),
            "mode": payload.get("mode", "daily_practice"),
            "duration_minutes": duration,
            "show_question_scores": show,
            "sections": sections,
            "stats": {
                "section_count": len(sections),
                "total_score": sum(section["points"] for section in sections),
            },
            "notices": [_NOTICE],
            "blockers": blockers,
        }
        preview_hash = _digest(model)
        model["preview_snapshot_sha256"] = preview_hash
        preview_id = _PREFIX + token
        snapshot = {
            "preview_id": preview_id,
            "preview_hash": preview_hash,
            "basket_sha256": _digest(basket),
            "preview_model": model,
            "sections": manifest,
            "images": images,
        }
        snapshot_bytes = _json_bytes(snapshot)
        (folder / "snapshot.json").write_bytes(snapshot_bytes)
        if _digest(self.state.basket()) != snapshot["basket_sha256"]:
            raise MixedPaperError(
                "预览期间题篮已变化，请刷新后重试。", "paper_preview_stale"
            )
        record = {
            "kind": "mixed_paper_preview",
            "preview_id": preview_id,
            "preview_hash": preview_hash,
            "snapshot_sha256": _sha(snapshot_bytes),
            "basket_sha256": snapshot["basket_sha256"],
            "approval": None,
            "created_at": utc_now(),
        }
        self.state.save_draft(preview_id, record)
        self.state.save_draft(
            _ACTIVE, {"preview_id": preview_id, "preview_hash": preview_hash}
        )
        from .desktop_facade import PaperPreview

        return PaperPreview(
            preview_id=preview_id,
            title_zh=title.strip(),
            mode_zh="模拟考试" if model["mode"] == "mock_exam" else "平时练习",
            theme_count=len(sections),
            theme_titles=tuple(section["title_zh"] for section in sections),
            export_ready=False,
            blockers=tuple(blockers),
            preview_model=model,
            preview_hash=preview_hash,
            approved=False,
        )

    @staticmethod
    def _core_asset_hashes(bundle, asset_root):
        references = set()

        def visit(value):
            if isinstance(value, dict):
                if value.get("asset_ref"):
                    references.add(value["asset_ref"])
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(bundle["student_plan"])
        visit(bundle["teacher_plan"])
        hashes = {}
        for reference in sorted(references):
            path = (asset_root / reference).resolve()
            if not path.is_relative_to(asset_root.resolve()):
                raise MixedPaperError("原卷资源越出已验证的预览目录。")
            digest = _sha(path.read_bytes())
            if path.stem != digest:
                raise MixedPaperError("原卷资源校验值不匹配。")
            hashes[reference] = digest
        return hashes

    @staticmethod
    def _save_image(folder, images, data, mime, caption):
        if not isinstance(data, bytes) or mime not in {
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/tiff",
        }:
            raise MixedPaperError("题图格式暂不能用于本地预览。")
        digest = _sha(data)
        image_id = "image-" + digest
        relative = f"preview-images/{digest}"
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            _read_verified(target, digest)
        else:
            target.write_bytes(data)
        images[image_id] = {"path": relative, "sha256": digest, "content_type": mime}
        return {
            "kind": "image",
            "image_id": image_id,
            "caption_zh": str(caption or "来源题图"),
        }

    def _word_blocks(self, row, data, groups, folder, images, blockers):
        result = []
        for group in groups:
            for block in row.get(group, []):
                text = str(block.get("text") or "")
                if text:
                    result.append({"kind": "text", "text": text})
                assets = block.get("assets", [])
                if not assets and any(
                    marker in text
                    for marker in (
                        "【待查看原文：图片或图形】",
                        "【待查看原文：图片或旧式图形】",
                        "【待查看原文：嵌入对象或旧公式】",
                    )
                ):
                    blockers.append(
                        f"Word 区块 {block['index']} 的原图或对象没有可读取预览，未补写图中条件。"
                    )
                for asset in assets:
                    try:
                        payload = self.facade._word_questions().reader.word_asset_bytes(
                            data,
                            asset["asset_id"],
                            expected_sha256=asset["sha256"],
                            render_metafiles=True,
                        )
                        raw = payload["bytes"]
                        if payload.get("derived_preview"):
                            if payload.get("original_sha256") != asset[
                                "sha256"
                            ] or payload.get("preview_sha256") != _sha(raw):
                                raise MixedPaperError("Word 衍生预览的原图绑定不正确。")
                        elif _sha(raw) != asset["sha256"]:
                            raise MixedPaperError("Word 原图校验值发生变化。")
                        result.append(
                            self._save_image(
                                folder,
                                images,
                                raw,
                                payload.get("mime_type", "image/png"),
                                f"原Word区块 {block['index']} 原图",
                            )
                        )
                    except (ValueError, OSError, KeyError, TypeError) as exc:
                        message = f"Word 区块 {block['index']} 的原图暂不能预览：{getattr(exc, 'message_zh', '请查看原Word后修订资料。')}"
                        blockers.append(message)
                        result.append({"kind": "text", "text": message})
        return result

    def _core_blocks(self, bundle, audience, asset_root, folder, images):
        from . import paper_export_renderer as renderer

        plan = bundle[f"{audience}_plan"]
        sections = plan["visible"]["theme_sections"]
        blueprints = renderer._blueprint_theme_sections(
            plan["visible"], bundle["blueprint"]
        )
        suppressed = renderer._shared_question_visual_dedup(plan, asset_root)[
            "suppressed_shared_keys"
        ]
        result = []

        def blocks(values):
            for block in values:
                if block.get("text_zh"):
                    result.append({"kind": "text", "text": block["text_zh"]})
                if block.get("asset_ref"):
                    path = (asset_root / block["asset_ref"]).resolve()
                    if not path.is_relative_to(asset_root.resolve()):
                        raise MixedPaperError("原卷题图越出已验证的预览目录。")
                    data = path.read_bytes()
                    # The existing resolver uses a content-addressed PNG path.
                    if path.stem != _sha(data):
                        raise MixedPaperError("原卷题图校验值不匹配。")
                    result.append(
                        self._save_image(
                            folder, images, data, "image/png", block.get("alt_text_zh")
                        )
                    )

        for section, blueprint in zip(sections, blueprints, strict=True):
            schedule = renderer._shared_material_schedule(section, blueprint)
            for index, printed in enumerate(section["printed_questions"]):
                for material in schedule.get(index, []):
                    if material["render_once_key"] not in suppressed:
                        blocks(material["content_blocks"])
                printed_blocks, _ = renderer._printed_question_blocks(
                    printed["atomic_parts"]
                )
                for part, question_blocks in zip(
                    printed["atomic_parts"], printed_blocks, strict=True
                ):
                    if (
                        audience == "student"
                        and bundle["preset"]["student_version"]["show_item_scores"]
                    ):
                        result.append(
                            {
                                "kind": "text",
                                "text": f"本次练习分值：{part['score']:g} 分",
                            }
                        )
                    blocks(question_blocks)
                    if audience != "teacher":
                        lines = part.get("answer_space", {}).get("lines", 0)
                        if lines:
                            result.append(
                                {
                                    "kind": "text",
                                    "text": "\n".join(
                                        "________________________" for _ in range(lines)
                                    ),
                                }
                            )
                        continue
                    notes = part["teacher_notes"]
                    reference, supplement = (
                        notes["source_reference_answer"],
                        notes.get("supplemental_answer") or {},
                    )
                    result.append(
                        {"kind": "text", "text": f"本次练习分值：{part['score']:g} 分"}
                    )
                    result.append(
                        {
                            "kind": "text",
                            "text": f"{notes['answer_label_zh']}：{supplement.get('text_zh') or reference.get('text_zh') or '本题暂无已对齐参考答案。'}",
                        }
                    )
                    blocks(reference.get("content_blocks") or [])
                    if supplement.get("diagram_key"):
                        result.append(
                            self._save_image(
                                folder,
                                images,
                                renderer.answer_diagram_png(supplement["diagram_key"]),
                                "image/png",
                                supplement.get("text_zh"),
                            )
                        )
                    for label, value in (
                        ("解析", notes.get("explanation_zh")),
                        ("易错点", "；".join(notes.get("pitfalls_zh") or [])),
                        ("来源", notes.get("source_label_zh")),
                    ):
                        if value:
                            result.append({"kind": "text", "text": f"{label}：{value}"})
        return result

    def _load(self, preview_id, *, require_current=False):
        if not isinstance(preview_id, str) or not re.fullmatch(
            r"mixed-preview-[0-9a-f]{32}", preview_id
        ):
            raise MixedPaperError("统一预览标识不正确。")
        drafts = self.state.snapshot()["drafts"]
        record = drafts.get(preview_id)
        if not isinstance(record, dict) or record.get("preview_id") != preview_id:
            raise MixedPaperError("统一预览已缺失，请重新生成。")
        folder = self.root / preview_id.removeprefix(_PREFIX)
        snapshot = json.loads(
            _read_verified(folder / "snapshot.json", record.get("snapshot_sha256"))
        )
        if snapshot.get("preview_id") != preview_id or snapshot.get(
            "preview_hash"
        ) != record.get("preview_hash"):
            raise MixedPaperError("统一预览快照身份不一致。")
        model = deepcopy(snapshot["preview_model"])
        claimed = model.pop("preview_snapshot_sha256", None)
        if _digest(model) != claimed or claimed != snapshot["preview_hash"]:
            raise MixedPaperError("统一预览内容与确认哈希不一致。")
        if require_current and (
            drafts.get(_ACTIVE)
            != {
                "preview_id": preview_id,
                "preview_hash": snapshot["preview_hash"],
            }
            or snapshot["basket_sha256"] != _digest(self.state.basket())
        ):
            raise MixedPaperError(
                "题篮或当前预览已变化，请重新确认。", "paper_preview_stale"
            )
        return folder, record, snapshot

    def image(self, preview_id, image_id):
        folder, _, snapshot = self._load(preview_id)
        image = snapshot["images"].get(image_id) if isinstance(image_id, str) else None
        if not isinstance(image, dict) or image_id != "image-" + image.get(
            "sha256", ""
        ):
            raise MixedPaperError("图片不属于这个统一预览。")
        path = (folder / image["path"]).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise MixedPaperError("图片路径不属于这个统一预览。")
        return {
            "data": _read_verified(path, image["sha256"]),
            "content_type": image["content_type"],
            "sha256": image["sha256"],
        }

    def approve(self, preview_id, preview_hash):
        _, record, snapshot = self._load(preview_id, require_current=True)
        if preview_hash != snapshot["preview_hash"]:
            raise MixedPaperError("确认的预览版本不一致。", "paper_preview_stale")
        if snapshot["preview_model"]["blockers"]:
            raise MixedPaperError(
                "所选题目仍有未解决的题答范围或图文预览缺口，不能确认导出。"
            )
        record["approval"] = {
            "status": "approved",
            "preview_hash": preview_hash,
            "approved_at": utc_now(),
        }
        self.state.save_draft(preview_id, record)
        return {
            "status": "approved",
            "message_zh": "本次统一图文预览已确认，可以导出学生和教师 DOCX。",
        }

    def export(self, preview_id, preview_hash):
        folder, record, snapshot = self._load(preview_id, require_current=True)
        if (
            preview_hash != snapshot["preview_hash"]
            or (record.get("approval") or {}).get("preview_hash") != preview_hash
            or (record.get("approval") or {}).get("status") != "approved"
        ):
            raise MixedPaperError("请先确认本次统一图文预览。")
        items, words, inventory, _ = self._resolve(self.state.basket())
        current = {item["key"]: item for item in items}
        sections = []
        for frozen in snapshot["sections"]:
            check_read_cancelled()
            key = frozen["key"]
            if key not in current or current[key]["source_ref"] != frozen["source_ref"]:
                raise MixedPaperError(
                    "所选题目来源已经变化，请重新预览。", "paper_preview_stale"
                )
            if frozen["kind"] == "word_question":
                row = deepcopy(words[key])
                if (
                    row.get("export_ready") is not True
                    or _digest(_word_content(row)) != frozen["content_sha256"]
                ):
                    raise MixedPaperError("Word 题面、答案或范围已变化，请重新预览。")
                row["points"] = frozen["settings"]["points"]
                row["source_bytes"] = inventory[row["source_id"]][0].content
                if _sha(row["source_bytes"]) != row["source_sha256"]:
                    raise MixedPaperError("Word 原件已变化，未导出。")
                sections.append({"kind": "word_question", "question": row})
            else:
                bundle_path = (folder / frozen["bundle_path"]).resolve()
                asset_root = (folder / frozen["asset_root"]).resolve()
                if not bundle_path.is_relative_to(
                    folder.resolve()
                ) or not asset_root.is_relative_to(folder.resolve()):
                    raise MixedPaperError("原卷预览目录不正确。")
                bundle = json.loads(
                    _read_verified(bundle_path, frozen["bundle_sha256"])
                )
                if self._core_asset_hashes(bundle, asset_root) != frozen.get(
                    "asset_files"
                ):
                    raise MixedPaperError(
                        "原卷资源与已确认预览不一致。", "paper_preview_stale"
                    )
                sections.append(
                    {"kind": "core_plan", "bundle": bundle, "asset_root": asset_root}
                )
        for image_id in snapshot["images"]:
            self.image(preview_id, image_id)
        from .desktop_mixed_paper_export import build_mixed_paper_docx

        model = snapshot["preview_model"]
        payload = (self._docx_builder or build_mixed_paper_docx)(
            model["title"],
            sections,
            duration_minutes=model["duration_minutes"],
            show_student_scores=model["show_question_scores"],
            subtitle=model["subtitle"],
        )
        if not all(
            isinstance(payload.get(key), bytes) and payload[key].startswith(b"PK")
            for key in ("student_bytes", "teacher_bytes")
        ):
            raise MixedPaperError("统一导出未生成完整的学生与教师 DOCX。")
        # Check again before registering output: edits during construction must
        # not silently turn an old approval into an export of a different basket.
        self._load(preview_id, require_current=True)
        output = folder / ("export-" + uuid4().hex)
        output.mkdir(exist_ok=False)
        artifacts = []
        for audience, filename in (
            ("student", "学生练习.docx"),
            ("teacher", "教师答案.docx"),
        ):
            data = payload[f"{audience}_bytes"]
            path = output / filename
            path.write_bytes(data)
            artifacts.append(
                {
                    "artifact_id": f"{audience}_docx",
                    "path": str(path.resolve()),
                    "filename": filename,
                    "sha256": _sha(data),
                }
            )
        return {
            "status": "completed",
            "preview_id": preview_id,
            "artifacts": artifacts,
            "pdf_status": "not_generated",
            "warnings": list(payload.get("warnings", [])) + [_NOTICE],
            "message_zh": "统一学生版与教师版 DOCX 已生成；PDF 未生成，成品分页尚待目视检查。",
        }


__all__ = [
    "BASKET_SCHEMA",
    "PREVIEW_SCHEMA",
    "REQUEST_SCHEMA",
    "MixedPaperError",
    "MixedPaperService",
    "is_mixed_preview_id",
]
