"""Explicit textbook-to-teaching handoffs; no inferred mappings or promotion."""
from __future__ import annotations

from .desktop_preparation_sources import (
    PreparationSourceError,
    PreparationSourcesService,
    _digest,
)
from .desktop_textbook_workspace import _digest as candidate_digest
from .desktop_word_question_attributes import load_attribute_catalog
from .desktop_word_question_filters import (
    _directory,
    chapter_filter_id,
    section_filter_id,
)


def _native_selection(facade, concept_id, revision):
    catalog = facade.textbook_study_catalog()
    candidate = next((row for row in catalog["rows"] if row["concept_id"] == concept_id), None)
    option = catalog.get("native_options", {}).get(concept_id)
    if candidate is None or candidate_digest(candidate) != revision or option is None:
        raise PreparationSourceError("教材候选或原始概念绑定已变化，请刷新后重选；未扩大关联范围。")
    root=getattr(facade.paths,'content_root',facade.paths.workspace_root)
    native = PreparationSourcesService(root)._concepts().get(concept_id)
    if native is None or _digest(native) != option["revision"]:
        raise PreparationSourceError("教材概念版本已变化，请刷新后重选。")
    if any(candidate["curriculum"].get(key) != native.get(key)
           for key in ("volume_id", "chapter_id", "section_key", "supplement_node_key")):
        raise PreparationSourceError("教材候选与原概念的册章节归属不一致，请核对后重选。")
    # Use the activated concept's identity, never editable shelf labels.
    return native, {"concept_id": concept_id, "revision": option["revision"]}


def preparation_selection(facade, concept_id, revision):
    _native, option = _native_selection(facade, concept_id, revision)
    return option


def question_selection(facade, concept_id, revision):
    native, _option = _native_selection(facade, concept_id, revision)
    node_key = native.get("section_key") or native.get("supplement_node_key")
    root=getattr(facade.paths,'content_root',facade.paths.workspace_root)
    nodes = _directory(load_attribute_catalog(root))
    node = nodes.get(node_key)
    if node is None or any(native.get(key) != node.get(key) for key in ("volume_id", "chapter_id")):
        raise PreparationSourceError("该知识点没有可核对的教材单元关联，未放宽成全章或全册；请先核对目录映射。")
    volume, chapter = node["volume_id"], node["chapter_id"]
    return {
        "concept_id": concept_id,
        "candidate_revision": revision,
        "lane": "word_native",
        "label": " / ".join(str(node[key]) for key in ("volume_title", "chapter_title", "section_title") if node.get(key)) or native["title"],
        "filters": {
            "book": [volume],
            "chapter": [chapter_filter_id(volume, chapter)],
            "section": [section_filter_id(volume, chapter, node_key)],
        },
        "statement": "按已有教材映射筛选本地Word题目；候选标签仍需教师核验，题目为空时不会自动放宽。",
    }
