"""Personal review of existing native OOXML candidates, without bank promotion."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .desktop_state import DesktopStateStore, utc_now

PRODUCT_PATH = "sh-chem-db/kb/teaching_handout_question_slices_v1_2026-08-28"
REVIEW_KIND = "native_handout_personal_review"
CLASSIFICATION_LABELS = {
    "native_text_complete": "原生文字完整",
    "hybrid_visual_required": "待视觉补全",
    "visual_only_required": "需查看原页",
}
BLOCKER_LABELS = {
    "answer_non_text_dependency_requires_visual_confirmation": "答案含非文本对象，需核对原页",
    "answer_page_binding_requires_visual_confirmation": "答案页对应关系待核对",
    "cross_page_candidate_requires_visual_confirmation": "跨页内容待核对",
    "native_layout_paragraph_unmatched": "部分原生段落未对应到页面",
    "native_text_references_visual_evidence": "题目引用了图形或表格信息",
    "non_text_dependency_present": "题目含图片、公式对象等非文本内容",
    "option_set_incomplete_in_native_text": "原生文字中的选项不完整",
    "parent_heading_requires_visual_completion": "所属题组标题待视觉补全",
    "render_page_anchor_missing": "缺少原页定位",
    "subpart_sequence_incomplete_or_repeated": "小问顺序缺失或重复",
}


class HandoutCandidateError(ValueError):
    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code, self.message_zh = code, message_zh


def load_reader(workspace: Path) -> Any:
    root = workspace / PRODUCT_PATH / "native_ooxml_v2"
    script = root / "scripts/handout_candidate_reader.py"
    spec = importlib.util.spec_from_file_location("_desktop_handout_reader", script)
    if spec is None or spec.loader is None:
        raise HandoutCandidateError(
            "handout_reader_missing", "已整理讲义的读取组件尚未准备完整。"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.HandoutCandidateReader(root)


class HandoutCandidateService:
    def __init__(
        self,
        workspace: Path,
        state: DesktopStateStore,
        *,
        reader_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.workspace, self.state = workspace, state
        self.reader_factory = reader_factory or load_reader

    def _read(self, key: str | None = None) -> dict[str, Any]:
        try:
            reader = self.reader_factory(self.workspace)
            return reader.catalog() if key is None else reader.get(key)
        except Exception as exc:
            raise HandoutCandidateError(
                "handout_source_unavailable",
                "讲义候选来源未通过读取核验，请刷新或检查本地资料完整性。",
            ) from exc

    def _reviews(self) -> dict[str, dict[str, Any]]:
        events = [
            r
            for r in self.state.snapshot()["drafts"].values()
            if isinstance(r, dict) and r.get("kind") == REVIEW_KIND
        ]
        latest = {}
        for record in sorted(
            events, key=lambda r: (r.get("created_at", ""), r.get("event_id", ""))
        ):
            latest[record["candidate_key"]] = record
        return latest

    @staticmethod
    def _decorate(item: dict[str, Any], reviews: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(item)
        review = reviews.get(item["key"])
        result["review"] = review
        result["review_state"] = (
            "pending"
            if review is None
            else "stale"
            if review.get("revision") != item["revision"]
            else review["decision"]
        )
        result["classification_label"] = CLASSIFICATION_LABELS.get(
            item["classification"], "完整性待核对"
        )
        result["blocker_labels"] = list(
            dict.fromkeys(
                BLOCKER_LABELS.get(code, "还有未完成的内容核对项，请查看原页。")
                for code in item.get("blockers", [])
            )
        )
        locators = (item.get("editable_source") or {}).get("question_locators", [])
        result["practice_eligible"] = bool(
            item["classification"] == "native_text_complete"
            and locators
            and re.fullmatch(
                r"word/document\.xml#/w:document/w:body/w:p\[[1-9][0-9]*\]", locators[0]
            )
        )
        if (
            item["classification"] == "native_text_complete"
            and not result["practice_eligible"]
        ):
            result["blocker_labels"].append(
                "独立题目起点或原生段落定位待核验，暂不加入可编辑练习。"
            )
            result["classification_label"] += " · 题目边界待核验"
        return result

    def catalog(self) -> dict[str, Any]:
        value = self._read()
        reviews = self._reviews()
        return {
            **value,
            "items": [self._decorate(item, reviews) for item in value["items"]],
        }

    def detail(self, key: str) -> dict[str, Any]:
        return self._decorate(self._read(key), self._reviews())

    def record_review(
        self,
        key: str,
        revision: str,
        *,
        decision: str,
        question_checked: bool,
        answer_checked: bool,
        note: str = "",
    ) -> dict[str, Any]:
        if (
            decision not in ("checked", "needs_correction")
            or type(question_checked) is not bool
            or type(answer_checked) is not bool
            or not isinstance(note, str)
            or len(note) > 2000
        ):
            raise HandoutCandidateError(
                "handout_review_invalid", "请选择核对结果，备注最多 2000 字。"
            )
        if decision == "checked" and not (question_checked and answer_checked):
            raise HandoutCandidateError(
                "handout_checks_required", "请分别确认已核对题面完整性和答案对应关系。"
            )
        item = self._read(key)
        if item["revision"] != revision:
            raise HandoutCandidateError(
                "handout_review_stale", "题面或答案来源已变化，请刷新后重新核对。"
            )
        event = {
            "kind": REVIEW_KIND,
            "event_id": uuid.uuid4().hex,
            "candidate_key": key,
            "revision": revision,
            "decision": decision,
            "question_checked": question_checked,
            "answer_checked": answer_checked,
            "note": note.strip(),
            "created_at": utc_now(),
            "candidate_only": True,
            "bank_ingest_allowed": False,
            "answer_correctness_verified": False,
        }
        self.state.save_draft("HANDOUT-REVIEW-" + event["event_id"], event)
        return event

    def page_bytes(self, key: str, revision: str, role: str, index: int) -> bytes:
        if role not in ("question", "answer") or type(index) is not int or index < 0:
            raise HandoutCandidateError(
                "handout_page_invalid", "请选择有效的题目页或答案页。"
            )
        item = self._read(key)
        if item["revision"] != revision:
            raise HandoutCandidateError(
                "handout_page_stale", "资料版本已变化，请刷新候选后查看原页。"
            )
        pages = item.get(role + "_pages", [])
        if index >= len(pages):
            raise HandoutCandidateError("handout_page_missing", "此候选暂无对应原页。")
        anchor = pages[index]
        root = (self.workspace / PRODUCT_PATH).resolve()
        try:
            relative = Path(anchor["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("invalid page path")
            path = (root / relative).resolve()
            path.relative_to(root / "batches")
            if (
                path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp")
                or path.stat().st_size > 32_000_000
            ):
                raise ValueError("invalid page file")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != anchor["sha256"]:
                raise ValueError("page changed")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HandoutCandidateError(
                "handout_page_unavailable",
                "原页缺失或校验不一致，未显示未经绑定的图像。",
            ) from exc
        return data

    def copy_text(self, key: str, revision: str) -> str:
        item = self.detail(key)
        if item["revision"] != revision:
            raise HandoutCandidateError(
                "handout_copy_stale", "资料版本已变化，请刷新后重新复制。"
            )
        review_label = (
            "已记录题面与答案对应核对"
            if item["review_state"] == "checked"
            else "尚未完成本机核对"
        )
        completeness = (
            "原生文字完整候选"
            if item["classification"] == "native_text_complete"
            else "原生文字片段，存在待视觉补全内容，不能替代完整原题"
        )
        return (
            f"{item['title']}\n来源：{item['source_name']}\n"
            f"{completeness}；{review_label}。参考答案非官方，化学正确性需独立判断。\n\n"
            f"题面\n{item['question_text']}\n\n非官方参考答案\n{item['answer_text']}\n"
        )
