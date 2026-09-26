"""Preview the exact frozen import pages before a provider sees any pixels."""

import hashlib
import re
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLayout

from ..desktop_visual_import_v2 import _image_dimensions
from .preparation_egress_dialog import PreparationEgressDialog

_ROLES = {"question": "题目 / 共同材料", "answer": "参考答案", "handout": "教师讲义"}
_MIMES = {"image/png", "image/jpeg", "image/webp"}


class VisualImportEgressDialog(PreparationEgressDialog):
    """Share the gallery UX, not the preparation workflow's 48-picture limit.

    A visual import can have up to 500 raster pages of at most 32 MiB each.
    The facade owns its frozen snapshot; this dialog keeps metadata, thumbnails
    and the currently selected full raster only. No source path is accepted.
    """

    def __init__(self, plan, image_loader, parent=None):
        self._plan = deepcopy(plan) if isinstance(plan, dict) else {}
        self._page_loader = image_loader
        self._valid_plan = False
        assets = []
        try:
            pages = self._plan["pages"]
            if not isinstance(pages, list) or not 1 <= len(pages) <= 500:
                raise ValueError("Invalid page list")
            for field in ("preview_id", "revision", "batch_id", "model_label", "confirmation_text"):
                if not isinstance(self._plan.get(field), str) or not self._plan[field].strip():
                    raise ValueError("Incomplete snapshot")
            seen = set()
            for page in pages:
                page_id = page["page_id"]
                if not isinstance(page_id, str) or not page_id or page_id in seen:
                    raise ValueError("Invalid page identity")
                seen.add(page_id)
                role = _ROLES[page["source_role"]]
                name = page["source_name"]
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Missing source")
                if type(page["page_number"]) is not int or page["page_number"] < 1:
                    raise ValueError("Invalid page order")
                if not isinstance(page["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", page["sha256"]):
                    raise ValueError("Invalid page digest")
                if any(type(page[d]) is not int or not 1 <= page[d] <= 40_000 for d in ("width", "height")):
                    raise ValueError("Invalid dimensions")
                if page["mime_type"] not in _MIMES:
                    raise ValueError("Invalid page format")
                if type(page.get("will_send", True)) is not bool:
                    raise ValueError("Invalid resume state")
                sending = page.get("will_send", True)
                action = "本次发送" if sending else "本机复用"
                assets.append({
                    "asset_id": page_id, "sha256": page["sha256"],
                    "caption": f"{action} · {role} · {name} · 第 {page['page_number']} 页",
                    "source": f"{name} · 第 {page['page_number']} 页",
                    "purpose": (f"{role}；整页像素将交给模型识别，结果仍待核对" if sending
                                else f"{role}；复用已保存分片，本次不发送，结果仍待教师核对"),
                    "width": page["width"], "height": page["height"],
                    "content_type": page["mime_type"],
                })
            self._valid_plan = callable(image_loader)
        except (KeyError, TypeError, ValueError):
            assets = []
        disclosure = self._plan.get("confirmation_text")
        super().__init__(
            "题目导入 · 发送前核对",
            disclosure if isinstance(disclosure, str) else "发送清单不完整，请返回重新预览。",
            image_count=len(assets), image_assets=assets,
            image_loader=self._page_loader, parent=parent,
        )
        self.setObjectName("VisualImportEgressDialog")
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumWidth(0)
        if self._valid_plan:
            sending_count = sum(page.get("will_send", True) for page in self._plan["pages"])
            reused_count = len(assets) - sending_count
            policy = self._plan.get("request_policy")
            budget = ""
            if isinstance(policy, dict) and all(
                type(policy.get(key)) is int and policy[key] > 0
                for key in ("max_output_tokens", "timeout_seconds")
            ):
                budget = (
                    f"\n单次输出上限 {policy['max_output_tokens']} tokens（含推理）"
                    f" · 最长等待 {policy['timeout_seconds']} 秒"
                    + (f"\n输入预算 {policy['max_input_tokens']} tokens（估算）"
                       if policy.get("max_input_tokens") is not None else
                       "\n输入预算未设置（参考 64000）；可在模型设置调整输入与输出限额")
                )
            self.summary.setText(
                f"接收模型：{self._plan['model_label']}\n"
                f"{len(assets)} 页本次选定的源页 · 点击缩略图切换，放大检查内容与边界"
                f"\n{sending_count} 页本次发送 · {reused_count} 页从本机复用"
                + budget
                + (
                    f"\n提取后追加原页与实际裁片的图像复核，每组最多 {policy['crop_review_batch_limit']} 条裁片。"
                    "\n追加调用按裁片数分组计费，请求次数在提取后确定；复核失败不完成导入，不会自动重试。"
                    '\n当前预览包括本次选定的全部源页；识别后才会生成裁图，裁图仅来自这些页面。'
                    if isinstance(policy, dict) and policy.get("crop_review_required") is True
                    and type(policy.get("crop_review_batch_limit")) is int
                    and policy["crop_review_batch_limit"] > 0 else ""
                )
            )
        self.image_list.setAccessibleName("本批题目、答案与讲义页面；每页标明本次发送或本机复用")
        self.confirm_button.setText("确认发送并识别")
        self.cancel_button.setText("返回，不发送")
        if self._valid_plan and reused_count:
            self.confirm_button.setText("确认发送剩余页面" if sending_count else "在本机汇总候选")
            if not sending_count:
                self.heading.setText("本机恢复候选")
                self.reminder.setText("只汇总已保存的分片结果；不调用模型。恢复后的候选仍待教师核对。")
                self.tabs.setTabText(self.tabs.indexOf(self.disclosure), "本机恢复说明")
                self.summary.setText(
                    f"{reused_count} 页均可复用已保存的结果\n本次在本机汇总候选，不发送页面或调用模型。\n"
                    "可逐页查看原件；候选仍需教师核对。"
                )
                self.cancel_button.setText("返回")
        self._full_summary = self.summary.text()
        self._compact_summary = self._full_summary
        if self._valid_plan and sending_count:
            self._compact_summary = (
                f"接收模型：{self._plan['model_label']}\n"
                f"{sending_count} 页本次发送 · {reused_count} 页从本机复用\n"
                "提取后追加原页与裁片复核；追加调用会计费。\n"
                "完整预算和处理范围见“发送范围与费用”。"
            )
            resume = self._plan.get("resume", {})
            if isinstance(resume, dict) and resume.get("has_other_scopes") and not reused_count:
                notice = "已有记录与当前页面或配置不匹配，本次重新处理。\n"
                self._full_summary = notice + self._full_summary
                self._compact_summary = notice + self._compact_summary
        self._adapt_layout()

    def _adapt_layout(self):
        if not hasattr(self, "_full_summary"):
            return
        narrow = self.width() < 640
        self.layout().setContentsMargins(12 if narrow else 20, 12, 12 if narrow else 20, 12)
        self.summary.setText(self._compact_summary if narrow else self._full_summary)
        self.image_list.setMinimumWidth(0 if narrow else 150)
        self.image_list.setMaximumHeight(112 if narrow else 16777215)
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.gallery.orientation() != orientation:
            self.gallery.setOrientation(orientation)
            self.gallery.setSizes([112, 360] if narrow else [240, 580])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adapt_layout()

    def _normalize_assets(self, assets):
        if not self._valid_plan or not assets:
            raise ValueError("Incomplete frozen page snapshot")
        return deepcopy(assets)

    def _read_image(self, index):
        asset = self._assets[index]
        raw = self._page_loader(asset["asset_id"])
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != asset["sha256"]:
            raise ValueError("Frozen page changed")
        size = _image_dimensions(raw, asset["content_type"])
        if size != (asset["width"], asset["height"]):
            raise ValueError("Frozen page dimensions changed")
        return raw
