"""Read-only transfer of a saved blueprint into the teacher's preparation brief."""

from __future__ import annotations

import json

from .desktop_blueprint_drafts import BlueprintDraftError, BlueprintDraftService
from .desktop_blueprint_generation import format_blueprint
from .desktop_preparation_limits import MAX_MATERIALS
from .desktop_state import DesktopStateStore


class BlueprintPreparationService:
    def __init__(self, state: DesktopStateStore):
        self.state = state
        self.drafts = BlueprintDraftService(state)

    def options(self) -> list[dict]:
        records = self.state.snapshot()["drafts"]
        roots = sorted(
            (
                (key, value)
                for key, value in records.items()
                if isinstance(value, dict)
                and value.get("kind") == "textbook_prompt_blueprint"
                and value.get("status") == "completed"
            ),
            key=lambda pair: (str(pair[1].get("created_at", "")), pair[0]),
            reverse=True,
        )
        result = []
        for preview_id, _root in roots[:30]:
            try:
                sources = self.drafts.sources(preview_id)
            except BlueprintDraftError:
                continue
            for source in sources:
                result.append(
                    {
                        "preview_id": preview_id,
                        "source_id": source["source_id"],
                        "source_revision": source["source_revision"],
                        "label": source["candidate"]["theme_center"][:90]
                        + " · "
                        + source["source_label"],
                    }
                )
        return result

    def reference(
        self, preview_id: str, source_id: str, expected_revision: str
    ) -> dict:
        if not isinstance(expected_revision, str) or not expected_revision:
            raise BlueprintDraftError("blueprint_draft_stale", "请重新选择蓝图版本。")
        source = self.drafts.load_source(preview_id, source_id, expected_revision)
        lines = [
            "【蓝图备课参考摘录：待教师核验，不是完整试卷或已审核答案】",
            f"蓝图记录：{preview_id}",
            f"选用版本：{source_id} · {source['source_label']}",
            f"版本摘要：{source['source_revision']}",
            "以下为已保存的资料快照与设计思路，不代表原页仍有效或化学正确性已获认证。",
            "先依据对应Word讲义的知识结构与教材提炼内容安排讲解，再参考蓝图中的应用任务。",
            "教材与讲义决定本课知识范围；本地规则只约束组织，模型蓝图不是教材正文或既定授课顺序。",
            "将材料关系与任务推进用于课堂活动设计；保留不确定性，不把解答规划当作核定答案。",
            "不据此扩写完整试卷，不补造缺失实验条件、数据、教材页码或官方评分点。",
            "",
            "资料依据快照（E 编号沿用蓝图；不携带本地原文件路径或图片）",
        ]
        # Include all evidence: free-text knowledge/difficulty fields can refer to
        # E records beyond the material_refs; filtering would silently drop them.
        for index, evidence in enumerate(source["evidence"], 1):
            lines.append(f"\nE{index}")
            for field, label in (
                ("source_type", "来源类型"),
                ("scope", "资料范围"),
                ("supports", "支持内容"),
            ):
                if field not in evidence:
                    continue
                value = evidence[field]
                if isinstance(value, list) and all(
                    isinstance(item, str) for item in value
                ):
                    rendered = "\n".join("• " + item for item in value)
                elif isinstance(value, str):
                    rendered = value
                else:
                    rendered = json.dumps(value, ensure_ascii=False, indent=2)
                lines.append(label + "：" + rendered)
        lines.extend(
            [
                "",
                "蓝图设计参考（以下为模型或教师设计，不替代上述教材和讲义依据）",
                "题目链不等于课堂讲解顺序；概念讲解、学生笔记和反馈应按本课目标重新组织。",
                format_blueprint({"candidate": source["candidate"]}),
            ]
        )
        if source["source_note"]:
            lines.extend(["", "教师修订说明：" + source["source_note"]])
        text = "\n".join(lines)
        if len(text) > MAX_MATERIALS:
            raise BlueprintDraftError(
                "blueprint_preparation_too_large",
                f"这份蓝图和资料依据超过备课资料的{MAX_MATERIALS}字上限，未截断或导入。请先另存精简蓝图；若资料依据本身过长，请用较少参考资料重新编译蓝图。",
            )
        return {
            "materials": text,
            "source_id": source_id,
            "source_revision": expected_revision,
        }


def append_reference(existing: str, reference: str) -> str:
    """Append atomically; never trim a teacher's existing materials."""
    if reference in existing:
        raise BlueprintDraftError(
            "blueprint_preparation_duplicate", "这份参考已在资料框中，无需重复导入。"
        )
    combined = existing + ("\n\n" if existing else "") + reference
    if len(combined) > MAX_MATERIALS:
        raise BlueprintDraftError(
            "blueprint_preparation_too_large",
            f"合并后超过备课资料的{MAX_MATERIALS}字上限，原填写内容未变。请先精简资料再导入。",
        )
    return combined
