"""Inspect the frozen student-analysis pixels before either consent is given."""

import hashlib
import re
from collections.abc import Mapping
from copy import deepcopy

from PySide6.QtWidgets import QCheckBox, QDialog, QSizePolicy

from ..desktop_visual_import_v2 import _image_dimensions
from .preparation_egress_dialog import PreparationEgressDialog

_ROLES = {
    "question_pages": "题目页面",
    "reference_answer_pages": "参考答案页面",
    "student_work_pages": "学生作答页面",
}


def _field(value, key, default=None):
    return (
        value.get(key, default)
        if isinstance(value, Mapping)
        else getattr(value, key, default)
    )


class AnalysisConfirmationDialog(PreparationEgressDialog):
    """Use the consent snapshot, never the mutable student-page selection."""

    def __init__(self, confirmation, parent=None, *, image_loader=None):
        self._page_loader = image_loader
        self._valid_pages = False
        assets = []
        try:
            pages = _field(confirmation, "pages", ())
            hashes = _field(confirmation, "page_sha256", ())
            counts = {role: 0 for role in _ROLES}
            if not isinstance(pages, (list, tuple)) or not 1 <= len(pages) <= 60:
                raise ValueError("Incomplete pages")
            if tuple(_field(page, "sha256") for page in pages) != tuple(hashes):
                raise ValueError("Page order changed")
            if len(set(hashes)) != len(pages):
                raise ValueError("Duplicate pages")
            for page in pages:
                sha = _field(page, "sha256")
                role = _field(page, "role")
                width, height = _field(page, "width"), _field(page, "height")
                mime = _field(page, "mime_type")
                page_number = _field(page, "page_number")
                if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                    raise ValueError("Invalid hash")
                if role not in _ROLES or not _field(page, "file_id"):
                    raise ValueError("Invalid source")
                if any(
                    type(size) is not int or not 1 <= size <= 40_000
                    for size in (width, height)
                ):
                    raise ValueError("Invalid dimensions")
                if type(page_number) is not int or page_number < 1:
                    raise ValueError("Invalid page number")
                if mime not in {"image/png", "image/jpeg", "image/webp"}:
                    raise ValueError("Invalid image format")
                counts[role] += 1
                label = f"{_ROLES[role]} · 第 {counts[role]} 页"
                assets.append(
                    {
                        "asset_id": sha,
                        "sha256": sha,
                        "caption": label,
                        "source": f"{label}（文件内第 {page_number} 页）",
                        "purpose": "整页图像用于学生作答分析，请核对题目、作答及直接身份信息",
                        "width": width,
                        "height": height,
                        "content_type": mime,
                    }
                )
            if (
                counts != dict(_field(confirmation, "page_counts_by_role", {}))
                or _field(confirmation, "total_page_count") != len(pages)
                or not counts["question_pages"]
                or not counts["student_work_pages"]
            ):
                raise ValueError("Incomplete sending scope")
            self._valid_pages = callable(image_loader)
        except (KeyError, TypeError, ValueError):
            assets = []
        details = "\n".join(
            (
                f"匿名学生：{_field(confirmation, 'student_label_zh', '匿名学生')}",
                f"接收模型：{_field(confirmation, 'provider_label_zh', '所选视觉模型')}",
                f"发送范围：本窗口全部 {len(assets)} 页实际图片，包含图内可见内容和文件元数据。",
                f"保留期限记录：{_field(confirmation, 'retention_days', 0)} 天（不会到期自动删除文件）。",
                "页面将离开本机，可能产生模型服务费用。返回结果仅作为教师复核候选。",
                str(_field(confirmation, "message_zh", "")),
            )
        )
        super().__init__(
            "确认发送学生页面",
            details,
            image_count=len(assets),
            image_assets=assets,
            image_loader=image_loader,
            parent=parent,
        )
        self.setObjectName("StudentAnalysisEgressDialog")
        self.start_button = self.confirm_button
        self.start_button.setText("确认并开始分析")
        self.cancel_button.setText("暂不发送")
        self.summary.setText(
            f"接收模型：{_field(confirmation, 'provider_label_zh', '所选视觉模型')}\n"
            f"{len(assets)} 页实际图片 · 请逐页检查题面、参考答案与学生作答"
        )
        self.identifiers_clear = QCheckBox(
            "这些页面不含姓名、学号等直接身份标识，或已经完成脱敏。"
        )
        self.egress_confirmed = QCheckBox(
            "我确认把上述页面发送给所选模型，并知晓可能产生费用。"
        )
        for checkbox in (self.identifiers_clear, self.egress_confirmed):
            checkbox.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            checkbox.setAccessibleName(checkbox.text())
            self.layout().insertWidget(self.layout().count() - 1, checkbox)
            checkbox.toggled.connect(self._update_start_enabled)
        self._update_start_enabled()

    def _normalize_assets(self, assets):
        if not self._valid_pages or not assets:
            raise ValueError("Incomplete frozen student pages")
        return deepcopy(assets)

    def _read_image(self, index):
        asset = self._assets[index]
        data, mime = self._page_loader(asset["asset_id"])
        if (
            not isinstance(data, bytes)
            or len(data) > 40 * 1024 * 1024
            or mime != asset["content_type"]
            or hashlib.sha256(data).hexdigest() != asset["sha256"]
        ):
            raise ValueError("Frozen student page changed")
        if _image_dimensions(data, mime) != (asset["width"], asset["height"]):
            raise ValueError("Frozen student page dimensions changed")
        return data

    def _both_confirmed(self):
        return (
            hasattr(self, "identifiers_clear")
            and self.identifiers_clear.isChecked()
            and self.egress_confirmed.isChecked()
        )

    def _update_start_enabled(self):
        self.confirm_button.setEnabled(self._ready and self._both_confirmed())

    def _show_status(self):
        super()._show_status()
        self._update_start_enabled()

    def accept(self):
        if self._both_confirmed():
            super().accept()

    def done(self, result):
        if result == QDialog.DialogCode.Accepted and not self._both_confirmed():
            self._rechecking = False
            self._update_start_enabled()
            return
        super().done(result)
