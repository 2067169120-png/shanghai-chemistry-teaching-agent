from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopWorkbenchFacade,
    _canonical_digest,
)
from integrations.deeptutor_shchem_v1.desktop_library import (
    LibraryImage,
    LibraryPartDetail,
    LibraryThemeDetail,
)
from integrations.deeptutor_shchem_v1.desktop_paper_preparation_sources import (
    enrich_paper_preparation_sources,
)


def _identity(scope: str = "master", paper: str = "P1", theme: str = "T1") -> str:
    return _canonical_digest({"scope": scope, "paper": paper, "theme": theme})


def _detail(identity: str, *, scope: str = "master") -> LibraryThemeDetail:
    image = LibraryImage(
        scope=scope,
        node_id="A1",
        crop_id="Q1",
        sha256="a" * 64,
        role="question",
        caption_zh="题面",
    )
    return LibraryThemeDetail(
        key=identity,
        scope=scope,
        title_zh="来源大题",
        paper_title_zh="2025 上海二模",
        source_zh="来源标签",
        page_zh="第 3、4 页",
        context_zh="来源共同材料",
        shared_images=(image,),
        parts=(
            LibraryPartDetail(
                key="A1",
                label_zh="原卷第 7 题 · 作答单元 1",
                summary_zh="来源题意摘要 A1",
                requirement_zh="来源作答要求 A1",
                dependency_zh="独立作答",
                question_images=(image,),
                reference_answer_zh="来源答案 A1",
                answer_boundary_zh="来源参考答案（非官方）；系统未独立核验。",
                analysis_zh=("先判断反应关系。",),
                quality_notes_zh=("质量备注 A1",),
            ),
            LibraryPartDetail(
                key="A2",
                label_zh="原卷第 7 题 · 作答单元 2",
                summary_zh="来源题意摘要 A2",
                requirement_zh="来源作答要求 A2",
                dependency_zh="承接前序作答单元",
                reference_answer_zh="来源答案 A2",
                answer_boundary_zh="来源参考答案（非官方）；系统未独立核验。",
                analysis_zh=("再核对条件。",),
                quality_notes_zh=("质量备注 A2",),
            ),
        ),
        source_fields=(("年份", "2025"), ("区域", "上海")),
        notes_zh=("来源备注",),
    )


def _snapshot(*, scope: str = "master", identity: str | None = None) -> dict:
    identity = identity or _identity(scope)
    return {
        "mode": "daily_practice",
        "title": "当前组卷标题",
        "extra_current_field": {"kept": True},
        "themes": [
            {
                "key": "edited-theme-key",
                "source_identity_sha256": identity,
                "source_ref": {
                    "scope": scope,
                    "paper_id": "P1",
                    "theme_id": "T1",
                    "data_snapshot_id": "b" * 64,
                },
                "title": "教师已修改的大题标题",
                "source": "教师当前来源显示",
                "chapter": "当前教材章节",
                "questions": [
                    {
                        "key": "edited-q1",
                        "stem": "教师已经编辑过的题面",
                        "answer": "教师已经编辑过的答案",
                        "analysis": "教师已经编辑过的解析",
                        "source_ref": {"atomic_part_id": "A1"},
                    },
                    {
                        "key": "edited-q2",
                        "stem": "第二问当前题面",
                        "answer": "第二问当前答案",
                        "analysis": "第二问当前解析",
                        "source_ref": {"atomic_part_id": "A2"},
                    },
                ],
            }
        ],
    }


def test_enrichment_preserves_current_edits_and_adds_text_only_source_details():
    snapshot = _snapshot()
    before = deepcopy(snapshot)
    calls = []

    def loader(card):
        calls.append(card)
        return _detail(card.source_identity_sha256)

    result = enrich_paper_preparation_sources(snapshot, detail_loader=loader)

    assert snapshot == before
    assert result is not snapshot
    assert len(calls) == 1
    assert calls[0].scope == "master"
    assert calls[0].source_identity_sha256 == _identity()
    theme = result["themes"][0]
    assert theme["title"] == "教师已修改的大题标题"
    assert theme["source_detail"] == {
        "paper_title": "2025 上海二模",
        "page": "第 3、4 页",
        "source_fields": [
            {"label": "年份", "value": "2025"},
            {"label": "区域", "value": "上海"},
        ],
        "context": "来源共同材料",
        "notes": ["来源备注"],
    }
    first, second = theme["questions"]
    assert first["stem"] == "教师已经编辑过的题面"
    assert first["answer"] == "教师已经编辑过的答案"
    assert first["analysis"] == "教师已经编辑过的解析"
    assert first["source_detail"]["summary"] == "来源题意摘要 A1"
    assert first["source_detail"]["reference_answer"] == "来源答案 A1"
    assert first["source_detail"]["answer_boundary"].startswith("来源参考答案（非官方）")
    assert first["source_detail"]["analysis"][0].startswith("模型候选解路")
    assert first["source_detail"]["notes"] == ["质量备注 A1"]
    assert "question_images" not in first["source_detail"]
    assert "a" * 64 not in repr(first["source_detail"])
    assert second["source_detail"]["reference_answer"] == "来源答案 A2"


def test_facade_thin_method_uses_current_snapshot_and_loader_without_state():
    facade = object.__new__(DesktopWorkbenchFacade)
    calls = []

    def loader(card):
        calls.append(card)
        return _detail(card.source_identity_sha256)

    facade.library_theme_detail = loader
    snapshot = _snapshot()
    result = facade.preparation_paper_source_snapshot(snapshot)

    assert result["themes"][0]["questions"][0]["source_detail"]["summary"] == "来源题意摘要 A1"
    assert len(calls) == 1


def test_identity_mismatch_fails_closed_and_does_not_call_loader():
    snapshot = _snapshot(identity="0" * 64)
    calls = []

    def loader(card):
        calls.append(card)
        return _detail(card.source_identity_sha256)

    result = enrich_paper_preparation_sources(snapshot, detail_loader=loader)
    theme = result["themes"][0]
    assert not calls
    assert "source_detail" not in theme
    assert "身份校验失败" in theme["source_detail_warning"]
    assert "source_detail" not in theme["questions"][0]
    assert theme["questions"][0]["answer"] == "教师已经编辑过的答案"


def test_replaced_question_identity_never_borrows_another_part_answer():
    snapshot = _snapshot()
    snapshot["themes"][0]["questions"][1]["source_ref"] = {
        "atomic_part_id": "OTHER-THEME-QUESTION"
    }

    result = enrich_paper_preparation_sources(
        snapshot,
        detail_loader=lambda card: _detail(card.source_identity_sha256),
    )
    first, second = result["themes"][0]["questions"]
    assert first["source_detail"]["reference_answer"] == "来源答案 A1"
    assert "source_detail" not in second
    assert second.get("source_detail_warning")
    assert "来源答案 A1" not in repr(second)
    assert second["answer"] == "第二问当前答案"


@pytest.mark.parametrize("duplicate_count", [2, 3])
def test_duplicate_source_part_keys_fail_closed_for_any_duplicate_count(
    duplicate_count,
):
    snapshot = _snapshot()
    identity = _identity()
    detail = _detail(identity)
    duplicate = replace(detail.parts[0], key="A1")
    detail = replace(
        detail,
        parts=(duplicate,) * duplicate_count + (detail.parts[1],),
    )

    result = enrich_paper_preparation_sources(
        snapshot,
        detail_loader=lambda _card: detail,
    )
    first = result["themes"][0]["questions"][0]
    assert "source_detail" not in first
    assert first.get("source_detail_warning")
    assert first["answer"] == "教师已经编辑过的答案"


def test_no_source_ref_personal_handout_and_loader_failure_are_explicit_gaps():
    no_ref = _snapshot()
    no_ref["themes"][0].pop("source_ref")
    result = enrich_paper_preparation_sources(no_ref, detail_loader=lambda _card: _detail(_identity()))
    assert "缺少明确 source_ref" in result["themes"][0]["source_detail_warning"]
    assert "缺少明确 atomic_part_id" in result["themes"][0]["questions"][0]["source_detail_warning"]

    personal = _snapshot(scope="personal_handouts", identity=_identity("personal_handouts"))
    calls = []
    personal_result = enrich_paper_preparation_sources(
        personal,
        detail_loader=lambda card: calls.append(card) or _detail(card.source_identity_sha256),
    )
    assert "个人讲义" in personal_result["themes"][0]["source_detail_warning"]
    assert not calls

    failed = _snapshot()

    def fail(_card):
        raise RuntimeError("private path or credential must not leak")

    failed_result = enrich_paper_preparation_sources(failed, detail_loader=fail)
    assert "来源详情暂时无法读取" in failed_result["themes"][0]["source_detail_warning"]
    assert "private" not in repr(failed_result)
    assert failed_result["themes"][0]["questions"][0]["answer"] == "教师已经编辑过的答案"


def test_missing_snapshot_id_is_visible_but_does_not_block_exact_theme_lookup():
    snapshot = _snapshot()
    snapshot["themes"][0]["source_ref"].pop("data_snapshot_id")
    result = enrich_paper_preparation_sources(
        snapshot,
        detail_loader=lambda card: _detail(card.source_identity_sha256),
    )
    theme = result["themes"][0]
    assert theme["source_detail"]["paper_title"] == "2025 上海二模"
    assert "快照标识缺失" in theme["source_detail_warning"]


def test_second_enrichment_failure_clears_old_details_and_warnings():
    snapshot = _snapshot()
    enriched = enrich_paper_preparation_sources(
        snapshot,
        detail_loader=lambda card: _detail(card.source_identity_sha256),
    )
    enriched["themes"][0]["source_detail_warning"] = "旧大题警告"
    enriched["themes"][0]["questions"][0]["source_detail_warning"] = "旧小问警告"

    def fail(_card):
        raise RuntimeError("private source diagnostic")

    second = enrich_paper_preparation_sources(enriched, detail_loader=fail)
    theme = second["themes"][0]
    first = theme["questions"][0]
    assert "source_detail" not in theme
    assert "旧大题警告" not in theme["source_detail_warning"]
    assert "来源详情暂时无法读取" in theme["source_detail_warning"]
    assert "source_detail" not in first
    assert "旧小问警告" not in first["source_detail_warning"]
    assert "来源详情暂时无法读取" in first["source_detail_warning"]
    assert first["answer"] == "教师已经编辑过的答案"
