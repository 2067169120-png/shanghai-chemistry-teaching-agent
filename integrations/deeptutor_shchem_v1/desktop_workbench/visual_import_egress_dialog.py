"""Preview the exact frozen import pages before a provider sees any pixels."""

import hashlib
import re
from copy import deepcopy

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QFrame, QLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget

from ..desktop_visual_import_v2 import _image_dimensions
from .preparation_egress_dialog import PreparationEgressDialog

_ROLES = {"question": "题目 / 共同材料", "answer": "参考答案", "handout": "教师讲义"}
_MIMES = {"image/png", "image/jpeg", "image/webp"}


class _ShardList(QListWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(80, self.viewport().width() - 60)
        for index in range(self.count()):
            item = self.item(index)
            height = self.fontMetrics().boundingRect(0, 0, width, 10000, Qt.TextFlag.TextWordWrap, item.text()).height()
            item.setSizeHint(QSize(0, height + 24))


class VisualImportEgressDialog(PreparationEgressDialog):
    """Share the gallery UX, not the preparation workflow's 48-picture limit.

    A visual import can have up to 500 raster pages of at most 32 MiB each.
    The facade owns its frozen snapshot; this dialog keeps metadata, thumbnails
    and the currently selected full raster only. No source path is accepted.
    """

    REVISE_PREVIEW = 2
    REVISE_REPAIR = 3

    def __init__(self, plan, image_loader, parent=None):
        self._plan = deepcopy(plan) if isinstance(plan, dict) else {}
        self._page_loader = image_loader
        self._valid_plan = False
        self._selection_dirty = False
        self._selected_shards = ()
        self._repair_mode = "repair" in self._plan
        self._selected_options = ()
        self._can_confirm = self._plan.get("can_confirm", True) is True
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
                blocked = page.get("checkpoint_state") == "blocked"
                action = ("本机恢复核对" if self._repair_mode else
                          "需选择重做" if blocked else "本次发送" if sending else "本机复用")
                assets.append({
                    "asset_id": page_id, "sha256": page["sha256"],
                    "caption": f"{action} · {role} · {name} · 第 {page['page_number']} 页",
                    "source": f"{name} · 第 {page['page_number']} 页",
                    "purpose": (f"{role}；核对既有记录对应的原页，本次修复不会发送" if self._repair_mode else
                                f"{role}；已存结果无法核验，选择重做并更新预览后才能继续" if blocked else
                                f"{role}；整页像素将交给模型识别，结果仍待核对" if sending
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
            reused_count = sum(not page.get("will_send", True) and page.get("checkpoint_state") != "blocked"
                               for page in self._plan["pages"])
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
        if self._valid_plan and reused_count and self._can_confirm:
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
        if not self._can_confirm:
            self.heading.setText("先检查无法核验的结果")
            self.summary.setText("已有页组的保存记录无法核验。请在“处理进度”中选择重做，再更新发送预览。")
            self.reminder.setText("当前不能发送或汇总。原页与旧记录保留；选择重做后仍需确认发送范围。")
            self.confirm_button.setEnabled(False)
        if self._repair_mode:
            self.setWindowTitle("题目导入 · 本机目录修复")
            self.heading.setText("修复本机处理目录")
            self.summary.setText("目录损坏，尚未恢复。先选择已有记录并预览，再确认本机修复。")
            self.reminder.setText("保留旧记录；修复不调用模型。修复后返回普通预览，发送须再次确认。")
            self.tabs.setTabText(self.tabs.indexOf(self.disclosure), "修复说明")
            self.confirm_button.setText("确认本机修复")
            self.cancel_button.setText("返回")
            self.image_list.setAccessibleName("本机修复核对的原页；不会发送或调用模型")
            self._install_repair_tab()
            if not self._manifest_error:
                if self._original_selection:
                    self.summary.setText(
                        f"本次将恢复 {len(self._original_selection)} 组，其余 {len(self._plan['shards']) - len(self._original_selection)} 组保持待处理。"
                        "请核对所选记录与原页，再确认本机修复。"
                    )
                elif not self._repair_options:
                    self.summary.setText("未找到可安全恢复的记录。请先核对来源和保存记录；当前不能本机修复。")
        else:
            self._install_progress_tab()
        shards = self._plan.get("shards")
        reprocess_count = sum(s.get("reprocess") is True for s in shards if isinstance(s, dict)) if isinstance(shards, list) else 0
        if self._valid_plan and reprocess_count and self._can_confirm:
            self.confirm_button.setText("确认发送选定页面")
        self._full_summary = self.summary.text()
        self._compact_summary = self._full_summary
        if self._valid_plan and sending_count and self._can_confirm and not self._repair_mode:
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
        if self._repair_mode:
            self._install_repair_scroll()
        self._adapt_layout()

    def _install_repair_scroll(self):
        """Keep consent buttons reachable even on a short window or large font."""
        root = self.layout()
        buttons = root.takeAt(root.count() - 1).widget()
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        while root.count():
            widget = root.takeAt(0).widget()
            body.addWidget(widget, 1 if widget is self.tabs else 0)
        self.repair_scroll = QScrollArea()
        self.repair_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.repair_scroll.setWidgetResizable(True)
        self.repair_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.repair_scroll.setAccessibleName("本机修复内容；可上下滚动，确认与返回按钮固定在窗口底部")
        self.repair_scroll.setWidget(content)
        root.addWidget(self.repair_scroll, 1)
        root.addWidget(buttons)

    def _install_repair_tab(self):
        try:
            repair = self._plan["repair"]
            options, shards = repair["options"], self._plan["shards"]
            if not isinstance(options, list) or not isinstance(shards, list) or not shards:
                raise ValueError("repair list")
            pages = {p["page_id"]: p for p in self._plan["pages"]}
            by_shard = {s["shard_id"]: s for s in shards}
            members = [p for s in shards for p in s["page_ids"]]
            if len(by_shard) != len(shards) or len(members) != len(pages) or set(members) != set(pages):
                raise ValueError("repair page membership")
            self._repair_options = {o["option_id"]: o for o in options}
            if len(self._repair_options) != len(options):
                raise ValueError("duplicate option")
            for option in options:
                if (not isinstance(option["option_id"], str) or not option["option_id"]
                        or option["shard_id"] not in by_shard or type(option["version"]) is not int or option["version"] < 1
                        or not isinstance(option["summary"], str)
                        or option["shard_index"] != by_shard[option["shard_id"]]["shard_index"]):
                    raise ValueError("repair option")
            self._original_selection = tuple(sorted(repair["selected_option_ids"]))
            if (not set(self._original_selection) <= self._repair_options.keys()
                    or len({self._repair_options[o]["shard_id"] for o in self._original_selection}) != len(self._original_selection)
                    or bool(self._original_selection) != self._can_confirm):
                raise ValueError("repair selection")
        except (KeyError, TypeError, ValueError):
            self._manifest_error = True
            self._can_confirm = False
            self.confirm_button.setEnabled(False)
            self.validation_status.setText("恢复记录清单不完整，请返回重新预览。")
            return
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        note = QLabel("选择可恢复记录，每组只选一个。点选记录可看摘录；原页在“图片预览”。")
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        self.repair_list = _ShardList()
        self.repair_list.setWordWrap(True)
        self.repair_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.repair_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.repair_list.setMinimumHeight(80)
        self.repair_list.setAccessibleName("选择经独立核验的旧页组记录，每组一个版本")
        for shard in shards:
            names = "、".join(f"{pages[p]['source_name']} 第{pages[p]['page_number']}页" for p in shard["page_ids"])
            role = _ROLES[pages[shard["page_ids"][0]]["source_role"]]
            choices = [o for o in options if o["shard_id"] == shard["shard_id"]]
            for option in choices or [None]:
                title = f"候选 {option['version']} · 可恢复" if option else "无可安全恢复的记录"
                item = QListWidgetItem(f"第 {shard['shard_index']} 组 · {role} · {title}\n{names}")
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if option:
                    item.setData(Qt.ItemDataRole.UserRole, option["option_id"])
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if option["option_id"] in self._original_selection else Qt.CheckState.Unchecked)
                else:
                    item.setData(Qt.ItemDataRole.UserRole + 1, shard.get("unavailable_reason", "未找到这组的可用旧记录。"))
                self.repair_list.addItem(item)
        self.repair_details = QPlainTextEdit()
        self.repair_details.setReadOnly(True)
        self.repair_details.setMinimumHeight(70)
        self.repair_details.setAccessibleName("损坏原因与所选记录摘录，仍需教师核对")
        self.repair_details.setPlainText("\n".join(repair["issues"]) + "\n选择记录查看摘录。")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.repair_list)
        splitter.addWidget(self.repair_details)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([180, 120])
        layout.addWidget(splitter, 1)
        self.selection_note = QLabel()
        self.selection_note.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_note.setWordWrap(True)
        layout.addWidget(self.selection_note)
        self.revise_button = QPushButton("预览所选恢复范围")
        self.revise_button.setAutoDefault(False)
        self.revise_button.clicked.connect(lambda: self.done(self.REVISE_REPAIR))
        layout.addWidget(self.revise_button)
        self.repair_list.itemChanged.connect(self._repair_selection_changed)
        self.repair_list.currentItemChanged.connect(self._repair_item_changed)
        self.tabs.addTab(panel, "处理进度")
        self.tabs.setCurrentWidget(panel)
        self._repair_selection_changed()

    def _repair_item_changed(self, item, _previous=None):
        option = self._repair_options.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        reason = "\n".join(self._plan["repair"]["issues"])
        self.repair_details.setPlainText(reason + "\n\n" + (
            option["summary"] + "\n\n已核对来源与处理条件；内容仍待教师核对。" if option else
            str((item.data(Qt.ItemDataRole.UserRole + 1) if item else None) or "未找到这组的可用旧记录。")
            + "\n本次不处理；修复后如需识别，须另行确认发送。"
        ))

    def _repair_selection_changed(self, changed=None):
        if changed is not None and changed.checkState() == Qt.CheckState.Checked:
            selected = self._repair_options[changed.data(Qt.ItemDataRole.UserRole)]
            self.repair_list.blockSignals(True)
            for index in range(self.repair_list.count()):
                item = self.repair_list.item(index)
                option = self._repair_options.get(item.data(Qt.ItemDataRole.UserRole))
                if item is not changed and option and option["shard_id"] == selected["shard_id"]:
                    item.setCheckState(Qt.CheckState.Unchecked)
            self.repair_list.blockSignals(False)
        self._selected_options = tuple(sorted(
            self.repair_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.repair_list.count())
            if self.repair_list.item(i).checkState() == Qt.CheckState.Checked
        ))
        self._selection_dirty = self._selected_options != self._original_selection
        self.revise_button.setEnabled(bool(self._selected_options) and self._selection_dirty and not self._manifest_error)
        count = len(self._selected_options)
        self.selection_note.setText(
            f"已选 {count} 组，请先预览范围。" if self._selection_dirty else
            f"将恢复 {count} 组，其余保持待处理。" if count else
            "尚未选择；每组可选一个版本。"
        )
        self._show_status()

    def selected_repair_records(self):
        return self._selected_options

    def _install_progress_tab(self):
        shards = self._plan.get("shards")
        if "shards" not in self._plan:
            return
        try:
            if not isinstance(shards, list) or not shards:
                raise ValueError("shards")
            pages = {page["page_id"]: page for page in self._plan["pages"]}
            seen, page_ids = set(), []
            for shard in shards:
                if (not isinstance(shard["shard_id"], str) or shard["shard_id"] in seen
                        or shard["stored_state"] not in {"saved", "invalid", "pending"}
                        or type(shard["shard_index"]) is not int or shard["shard_index"] < 1
                        or type(shard["reprocess"]) is not bool or not isinstance(shard["page_ids"], list)
                        or not shard["page_ids"] or (shard["reprocess"] and shard["stored_state"] == "pending")
                        or any(page_id not in pages for page_id in shard["page_ids"])):
                    raise ValueError("shard state")
                seen.add(shard["shard_id"])
                page_ids.extend(shard["page_ids"])
            if len(page_ids) != len(pages) or set(page_ids) != set(pages):
                raise ValueError("page membership")
        except (KeyError, TypeError, ValueError):
            self._manifest_error = True
            self._can_confirm = False
            self.confirm_button.setEnabled(False)
            self.validation_status.setText("页组清单不完整，请返回重新预览。")
            return
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        note = QLabel("按组查看已保存与待处理的页面。勾选需要重做的组，再更新发送预览；旧记录保留。")
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        self.shard_list = _ShardList()
        self.shard_list.setWordWrap(True)
        self.shard_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.shard_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shard_list.setAccessibleName("页组处理进度与重新识别选择")
        states = {"saved": "已保存", "invalid": "无法核验 · 需选择重做", "pending": "待识别"}
        self._original_selection = tuple(sorted(s["shard_id"] for s in shards if s["reprocess"]))
        for shard in shards:
            source_pages = {}
            for page_id in shard["page_ids"]:
                page = pages[page_id]
                source_pages.setdefault(page["source_name"], []).append(str(page["page_number"]))
            names = "\n".join(f"{name} 第{'、'.join(numbers)}页" for name, numbers in source_pages.items())
            state = "已选择重做" if shard["reprocess"] else states[shard["stored_state"]]
            item = QListWidgetItem(f"第 {shard['shard_index']} 组 · {state}\n{names}")
            item.setData(Qt.ItemDataRole.UserRole, shard["shard_id"])
            item.setToolTip(item.text())
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if shard["stored_state"] in {"saved", "invalid"}:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if shard["reprocess"] else Qt.CheckState.Unchecked)
            self.shard_list.addItem(item)
        layout.addWidget(self.shard_list, 1)
        self.selection_note = QLabel()
        self.selection_note.setWordWrap(True)
        layout.addWidget(self.selection_note)
        self.revise_button = QPushButton("更新发送预览")
        self.revise_button.setAutoDefault(False)
        self.revise_button.clicked.connect(lambda: self.done(self.REVISE_PREVIEW))
        layout.addWidget(self.revise_button)
        self.shard_list.itemChanged.connect(self._selection_changed)
        self.tabs.addTab(panel, "处理进度")
        self._selection_changed()
        if not self._can_confirm:
            self.tabs.setCurrentWidget(panel)

    def _selection_changed(self, _item=None):
        self._selected_shards = tuple(sorted(
            self.shard_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.shard_list.count())
            if self.shard_list.item(i).checkState() == Qt.CheckState.Checked
        ))
        self._selection_dirty = self._selected_shards != self._original_selection
        self.revise_button.setEnabled(self._selection_dirty and not self._manifest_error)
        self.selection_note.setText(
            f"已勾选 {len(self._selected_shards)} 组重做。更新预览后核对实际发送范围；调用可能再次计费。"
            if self._selection_dirty else
            f"当前预览包含 {len(self._selected_shards)} 组重做。可修改勾选后更新预览。"
        )
        self._show_status()

    def selected_reprocess_shards(self):
        return self._selected_shards

    def _show_status(self):
        super()._show_status()
        self.confirm_button.setEnabled(self._ready and not self._failed and not self._manifest_error
                                       and not self._rechecking and not self._selection_dirty and self._can_confirm)
        if self._repair_mode:
            if self._manifest_error:
                message = "恢复清单不完整，请返回重试。"
            elif self._failed:
                message = "原页无法核对，不能本机修复。"
            elif self._selection_dirty:
                message = "选择已变化，请先预览范围。"
            elif not self._can_confirm:
                message = "尚未修复，请先选择并预览。"
            elif self._rechecking:
                message = "正在再次核对原页；未调用模型。"
            elif self._ready:
                message = "原页已载入；确认时再次核验。"
            else:
                message = f"正在读取原页（{self._cursor}/{len(self._assets)}）；未发送。"
            self.validation_status.setText(message)
            return
        if self._selection_dirty or not self._can_confirm:
            self.confirm_button.setEnabled(False)
            self.validation_status.setText("重做选择已变化，请先更新发送预览。" if self._selection_dirty else
                                           "保存记录无法核验，请选择重做并更新预览；尚未发送。")

    def accept(self):
        if self._selection_dirty or not self._can_confirm:
            return
        super().accept()

    def done(self, result):
        if result == QDialog.DialogCode.Accepted and (self._selection_dirty or not self._can_confirm):
            return
        if result == self.REVISE_PREVIEW and (not self._selection_dirty or self._manifest_error):
            return
        if result == self.REVISE_REPAIR and (not self._repair_mode or not self._selected_options
                                           or not self._selection_dirty or self._manifest_error):
            return
        super().done(result)

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
