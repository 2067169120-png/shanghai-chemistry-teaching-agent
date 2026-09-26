"""Preview the exact frozen import pages before a provider sees any pixels."""

import hashlib
import re
from copy import deepcopy

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
                assets.append({
                    "asset_id": page_id, "sha256": page["sha256"],
                    "caption": f"{role} · {name} · 第 {page['page_number']} 页",
                    "source": f"{name} · 第 {page['page_number']} 页",
                    "purpose": f"{role}；整页像素将交给模型识别，结果仍待核对",
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
        if self._valid_plan:
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
        self.image_list.setAccessibleName("本次实际发送的题目、答案与讲义页面")
        self.confirm_button.setText("确认发送并识别")
        self.cancel_button.setText("返回，不发送")

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
