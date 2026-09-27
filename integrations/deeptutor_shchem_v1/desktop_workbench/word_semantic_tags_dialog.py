"""Read the exact native Word input, call the chosen API, then select new tags."""

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_word_question_attributes import automatic_tags_protected
from .components import page_scroll, set_status


def _label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


def _question_identity(number, unit):
    return f"本次第{number}题 · {unit['source_name']}"


def _tags(value):
    lines = ["主考点：" + value["primary_knowledge"]["label"]]
    for row in [value["primary_knowledge"], *value["curriculum_candidates"]]:
        if row.get("section_key"):
            lines.append("教材：" + row["label"])
        for evidence in row["evidence"]:
            kind = {
                "question_text": "题干",
                "shared_context": "公共材料",
                "source_chapter": "来源章节",
                "source_metadata": "来源信息",
                "teacher_note": "教师备注",
                "model_image_observation": "图像观察",
            }.get(evidence.get("kind"), "来源依据")
            block = evidence.get("block_index")
            position = f" · 原文区块{block}" if block is not None else ""
            lines.append(f"依据：{evidence['quote']}（{kind}{position}）")
    if not value["curriculum_candidates"]:
        lines.append("教材：待映射")
    return "\n".join(lines)


def _changes(old, new):
    """Describe the service's merged proposal, never raw model suggestions."""
    before, after = old["primary_knowledge"], new["primary_knowledge"]
    primary_action = (
        "保留" if before["id"] == after["id"]
        else "补充" if before["id"] == "unknown" else "替换"
    )
    previous = {row["section_key"]: row for row in old["curriculum_candidates"]}
    proposed = {row["section_key"]: row for row in new["curriculum_candidates"]}
    added, removed = proposed.keys() - previous.keys(), previous.keys() - proposed.keys()
    retained = previous.keys() & proposed.keys()
    def names(rows):
        return "、".join(row["label"] for row in rows) or "待映射"

    return [
        {"field": "主考点", "action": primary_action,
         "before": before["label"], "after": after["label"], "details": []},
        {"field": "教材映射", "action": "替换" if removed else "补充" if added else "保留",
         "before": names(previous.values()), "after": names(proposed.values()),
         "details": [
             label + "：" + names(row for key, row in rows.items() if key in keys)
             for label, rows, keys in (
                 ("保留", previous, retained), ("补充", proposed, added), ("移除", previous, removed)
             ) if keys
         ]},
    ]


def _change_lines(changes, *, changed_only=False):
    lines = []
    for change in changes:
        if changed_only and change["action"] == "保留":
            continue
        lines.append(
            f"{change['field']}【{change['action']}】：{change['before']} → {change['after']}"
        )
        lines.extend("  " + detail for detail in change["details"])
    return lines


class WordSemanticTagsDialog(QDialog):
    def __init__(self, facade, tasks, selections, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.selections = deepcopy(selections)
        self.plan, self.analysis_result = None, None
        self._image_labels = []
        self._image_preview_failed = False
        self._job, self._phase = None, ""
        self._closed = False
        self._request_token = None
        self._cancel_requested = False
        self._failure_message = ""
        self._applying_keys = ()
        self._saved_keys = set()
        self._save_complete = False
        self.saved = []
        self.setWindowTitle("AI标签整理 · 先预览再采用")
        self.resize(900, 820)
        self.setMinimumSize(360, 520)
        outer = QVBoxLayout(self)
        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(4, 4, 4, 4)
        self.body_scroll = page_scroll(self.content)
        outer.addWidget(self.body_scroll, 1)
        heading = _label("逐题核对 · 标签整理")
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)
        self.introduction = _label(
            "默认只补缺失主考点与教材映射，已有非空标签保留。分析完整题干、公共材料及可读取原图。"
        )
        layout.addWidget(self.introduction)
        self.recheck = QCheckBox("重新核对自动标签")
        self.recheck.setAccessibleName("重新核对自动标签")
        self.recheck.setToolTip(
            "核对模式不跳过标签齐全的自动标注题；教师修改、教师确认及固定修订仍受保护。"
        )
        layout.addWidget(self.recheck)
        self.profile = QComboBox()
        self.profile.setMinimumContentsLength(10)
        self.profile.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.profile.setAccessibleName("选择题目标签分析模型")
        layout.addWidget(self.profile)
        self.prepare = QPushButton("预览发送内容")
        self.prepare.setObjectName("QuietButton")
        self.prepare.clicked.connect(self._prepare)
        layout.addWidget(self.prepare)
        self.disclosure = _label(
            "本地预览不调用模型。带图题必须使用允许图片发送的多模态配置；不可读图片不会被悄悄省略。"
        )
        layout.addWidget(self.disclosure)
        self.allow_send = QCheckBox("同意发送并调用模型（可能计费）")
        self.allow_send.toggled.connect(self._actions)
        layout.addWidget(self.allow_send)
        self.questions = QListWidget()
        self.questions.setAccessibleName("本次分析题目及待采用标签")
        self.questions.setWordWrap(True)
        self.questions.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.questions.setMaximumHeight(120)
        self.questions.setMinimumHeight(90)
        self.questions.currentRowChanged.connect(self._show_question)
        self.questions.itemChanged.connect(self._actions)
        layout.addWidget(self.questions)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(0)
        self.tabs.setMinimumHeight(300)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.paper = QWidget()
        self.paper_layout = QVBoxLayout(self.paper)
        self.paper_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tabs.addTab(page_scroll(self.paper), "原题与发送预览")
        self.tags = QPlainTextEdit()
        self.tags.setMinimumWidth(0)
        self.tags.setReadOnly(True)
        self.tags.setAccessibleName("当前标签与AI建议对照")
        self.tabs.addTab(self.tags, "标签建议与依据")
        layout.addWidget(self.tabs, 1)
        self.selection_summary = _label("尚未分析；没有待保存的标签变化。")
        self.selection_summary.setAccessibleName("仅勾选标签变化的保存摘要")
        layout.addWidget(self.selection_summary)
        self.status = _label("正在读取可用模型设置…")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.run_button = QPushButton("确认发送并分析")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self._run)
        self.stop = QPushButton("停止分析")
        self.stop.clicked.connect(self._stop)
        actions.addWidget(self.run_button)
        actions.addWidget(self.stop)
        outer.addLayout(actions)
        self.apply_button = QPushButton("保存勾选的标签建议")
        self.apply_button.clicked.connect(self._apply)
        save_actions = QHBoxLayout()
        save_actions.addWidget(self.apply_button, 1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.reject)
        save_actions.addWidget(self.close_button)
        outer.addLayout(save_actions)
        self.profile.currentIndexChanged.connect(self._invalidate)
        self.recheck.toggled.connect(self._invalidate)
        self.tasks.task_finished.connect(self._finished)
        self._submit("profiles", self.facade.preparation_profiles, self._profiles)

    def _submit(self, phase, operation, success, *, progress=False):
        self._phase = phase
        self._job = "starting"
        self._failure_message = ""
        self._cancel_requested = False
        token = self._request_token = object()

        def current():
            return not self._closed and self._request_token is token

        def succeeded(value):
            if current():
                success(value)

        def failed(message):
            if current():
                self._failed(message)

        def progressed(value):
            if current() and not self._cancel_requested:
                set_status(self.status, "info", value.get("message_zh", "正在分析…"))

        set_status(self.status, "info", {
            "profiles": "正在读取可用模型设置…",
            "preview": "正在准备本次原题与发送预览；尚未调用模型。",
            "run": "正在分析；标签尚未保存。",
            "apply": f"正在保存已勾选的{len(self._applying_keys)}题，请等待保存回执。",
        }[phase])
        self._actions()
        if phase != "profiles":
            self.body_scroll.ensureWidgetVisible(self.status)
        try:
            if progress:
                self._job = self.tasks.submit_progress(
                    "AI题目标签分析",
                    operation,
                    on_success=succeeded,
                    on_failure=failed,
                    on_progress=progressed,
                )
            else:
                self._job = self.tasks.submit(
                    "题目标签预览",
                    operation,
                    on_success=succeeded,
                    on_failure=failed,
                )
        except (RuntimeError, TypeError):
            self._job = None
            self._failed("标签任务未能启动，请重试。")

    def _profiles(self, profiles):
        self.profile.blockSignals(True)
        self.profile.clear()
        for profile in profiles:
            self.profile.addItem(
                profile.provider_name + " / " + profile.model_id, profile
            )
        self.profile.blockSignals(False)
        set_status(
            self.status,
            "info" if profiles else "attention",
            "请选择模型并预览本次选题。"
            if profiles
            else "请先在模型设置保存Key，并允许结构化文字输出及题目文字发送。",
        )

    def _invalidate(self):
        if self._job or self.analysis_result:
            return
        self._discard()
        self.plan, self.analysis_result = None, None
        self.allow_send.setChecked(False)
        self._image_preview_failed = False
        self.introduction.setText(
            "核对已有自动分类，先对照修改再逐题勾选保存。教师修改、教师确认、固定修订和原考试出处均保留。"
            if self.recheck.isChecked()
            else "默认只补缺失主考点与教材映射，已有非空标签保留。分析完整题干、公共材料及可读取原图。"
        )
        self.disclosure.setText(
            "模式或模型已变，请重新预览。预览不调用API；结果出来后关闭窗口可重新选择模式。"
        )
        self.questions.clear()
        self.tags.clear()
        self._show_question(-1)
        self._actions()

    def _prepare(self):
        profile = self.profile.currentData()
        if not profile or self._job:
            return
        self.allow_send.setChecked(False)
        options = {"mode": "recheck_automatic"} if self.recheck.isChecked() else {}
        self._submit(
            "preview",
            lambda: self.facade.word_semantic_tag_preview(
                self.selections, profile.profile_id, profile.revision, **options
            ),
            self._prepared,
        )

    def _prepared(self, value):
        self._discard()
        self.plan, self.analysis_result = value, None
        self._image_preview_failed = False
        ready = [u for u in value["units"] if u["status"] == "ready"]
        images = sum(len(u["images"]) for u in ready)
        mode_label = (
            "重新核对自动标签 · 允许建议替换"
            if value.get("mode") == "recheck_automatic"
            else "只补缺失 · 已有非空标签保留"
        )
        self.disclosure.setText(
            f"本次模式：{mode_label}\n接收模型：{value['model_label']}\n将发送{len(ready)}题的原生文字、公共材料及{images}张图片像素（包含图内可见内容和文件元数据）。"
            f"另有{len(value['units']) - len(ready)}题不发送，原因见题目列表。\n"
            f"最多调用{value['request_count']}次，每题一次；可能产生费用。失败即停止，重试可能再次计费。\n"
            f"单题输出上限{value['request_policy']['max_output_tokens']} token（部分模型包含推理），"
            f"最长等待{value['request_policy']['timeout_seconds']}秒；保留模型默认推理设置。"
        )
        self._populate()
        set_status(
            self.status,
            "info",
            "逐题核对发送内容后，再确认调用模型。未调用API、未修改标签。",
        )

    def _populate(self, *, preserve_checks=False):
        self.questions.blockSignals(True)
        current = self.questions.currentRow()
        previous_checks = {
            self.questions.item(i).data(Qt.ItemDataRole.UserRole): self.questions.item(i).checkState()
            for i in range(self.questions.count())
        } if preserve_checks else {}
        self.questions.clear()
        candidates = {
            i["key"]: i for i in (self.analysis_result or {}).get("items", [])
        }
        recheck = self.plan.get("mode") == "recheck_automatic"
        for number, unit in enumerate(self.plan["units"], 1):
            candidate = candidates.get(unit["key"])
            state = unit["reason"] or (
                "待发送" if not self.analysis_result else "尚未分析"
            )
            if unit["key"] in self._saved_keys:
                state = "已保存标签建议"
            elif candidate and unit["status"] == "ready":
                state = (
                    (
                        ("标签修改待勾选" if recheck else "新增标签可采用")
                        if self._eligible(unit, candidate)
                        else ("没有标签变更" if recheck else "没有新增标签")
                    )
                    if candidate["status"] == "ready"
                    else candidate["note"]
                )
                if candidate.get("changed") and candidate["status"] == "ready" and not self._eligible(unit, candidate):
                    state = "建议不可采用，请重新核对来源与模式"
            item = QListWidgetItem(_question_identity(number, unit) + " · " + state)
            item.setData(Qt.ItemDataRole.UserRole, unit["key"])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            if self._eligible(unit, candidate):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    previous_checks.get(unit["key"], Qt.CheckState.Unchecked if recheck else Qt.CheckState.Checked)
                )
            self.questions.addItem(item)
        self.questions.blockSignals(False)
        self.questions.setCurrentRow(max(0, min(current, self.questions.count() - 1)))
        self._show_question(self.questions.currentRow())

    def _candidate(self, key):
        return next((row for row in (self.analysis_result or {}).get("items", [])
                     if row["key"] == key), None)

    def _eligible(self, unit, candidate):
        if (not candidate or unit["status"] != "ready"
                or candidate.get("status") != "ready" or candidate.get("changed") is not True
                or unit["key"] in self._saved_keys
                or automatic_tags_protected(unit["attributes"])):
            return False
        old, new = unit["attributes"], candidate["proposed"]
        if any(old.get(field) != new.get(field) for field in (
            "key", "source_sha256", "source_revision", "question_revision",
            "index_revision", "extraction_revision",
        )):
            return False
        if self.plan.get("mode") != "recheck_automatic" and (
            (old["primary_knowledge"]["id"] != "unknown" and old["primary_knowledge"] != new["primary_knowledge"])
            or (old["curriculum_candidates"] and old["curriculum_candidates"] != new["curriculum_candidates"])
        ):
            return False
        return any(change["action"] != "保留" for change in _changes(old, new))

    def _selected_units(self):
        if not self.plan or not self.analysis_result or not self.analysis_result.get("finished"):
            return []
        checked = {
            self.questions.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.questions.count())
            if self.questions.item(i).checkState() == Qt.CheckState.Checked
        }
        return [unit for unit in self.plan["units"] if unit["key"] in checked
                and self._eligible(unit, self._candidate(unit["key"]))]

    def _selection_text(self, units):
        if not units:
            return "未勾选可保存的变化；保留项、无变更、失败和不发送的题目不会计入保存。"
        lines = [f"已勾选{len(units)}题 · 仅保存以下变化（尚未写入）"]
        for unit in units:
            number = self.plan["units"].index(unit) + 1
            changes = _changes(unit["attributes"], self._candidate(unit["key"])["proposed"])
            lines.append("\n" + _question_identity(number, unit))
            lines.extend(_change_lines(changes, changed_only=True))
        return "\n".join(lines)

    def _show_question(self, index):
        self._image_labels = []
        while self.paper_layout.count():
            item = self.paper_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self.plan or not 0 <= index < len(self.plan["units"]):
            self.tags.clear()
            return
        unit = self.plan["units"][index]
        self.paper_layout.addWidget(_label(_question_identity(index + 1, unit)))
        if unit["reason"]:
            self.paper_layout.addWidget(_label("本题不发送：" + unit["reason"]))
        for block in unit["input"]["blocks"]:
            self.paper_layout.addWidget(
                _label(
                    ("公共材料\n" if block["kind"] == "shared_context" else "")
                    + block["text"]
                )
            )
            for sha in block["image_sha256s"]:
                label = _label("原图")
                try:
                    raw = self.facade.word_semantic_tag_image(self.plan["plan_id"], sha)
                    pixmap = QPixmap()
                    if not pixmap.loadFromData(raw):
                        raise ValueError("image invalid")
                    self._image_labels.append((label, pixmap))
                except (ValueError, RuntimeError, KeyError, TypeError, OSError):
                    label.setText("本次原图预览失败，请重新预览后再发送。")
                    self._image_preview_failed = True
                    self.allow_send.setChecked(False)
                self.paper_layout.addWidget(label)
        self._resize_images()
        self.tabs.widget(0).verticalScrollBar().setValue(0)
        text = _question_identity(index + 1, unit) + "\n\n当前标签\n" + _tags(unit["attributes"])
        candidate = self._candidate(unit["key"])
        if unit["status"] != "ready" or automatic_tags_protected(unit["attributes"]):
            text = "保留原标签 · 本题不保存建议\n" + unit["reason"] + "\n\n" + text
        elif candidate and candidate.get("changed") and candidate["status"] == "ready" and unit["key"] not in self._saved_keys and not self._eligible(unit, candidate):
            text = "建议不可采用 · 来源、题目范围或当前模式需重新核对\n尚未保存本题建议。\n\n" + text
        elif candidate:
            recheck = self.plan.get("mode") == "recheck_automatic"
            if candidate["status"] == "ready":
                old, new = unit["attributes"], candidate["proposed"]
                changes = _changes(old, new)
                state = "已保存建议" if unit["key"] in self._saved_keys else "尚未保存"
                text = (
                    f"修改对照 · {state}\n"
                    + "\n".join(_change_lines(changes))
                    + "\n\n"
                    + text
                )
            text += "\n\nAI建议合并后的标签（未作教师审核）\n" + (
                _tags(candidate["proposed"])
                if candidate["status"] == "ready"
                else "本题分析未完成"
            )
            if candidate["status"] == "ready":
                changed = [row["field"] for row in changes if row["action"] != "保留"]
                text += ("\n本次修改：" if recheck else "\n本次补全：") + (
                    "、".join(changed) if changed else "无字段变化"
                )
                text += (
                    "\n仅核对自动标签；证据不足的字段保留原值。原考试出处与教师标记不变。"
                    if recheck
                    else "\n已有非空字段保持原值。"
                )
                text += "\n题面选项引文是定位依据，不代表选项陈述正确。"
                text += "\n\n模型原始说明（可能包含未采用的建议）：\n"
            else:
                text += "\n"
            text += candidate["note"]
        self.tags.setPlainText(text)
        self.tags.verticalScrollBar().setValue(0)

    def _run(self):
        if (
            self._job
            or not self.plan
            or self.analysis_result
            or self._image_preview_failed
            or not self.allow_send.isChecked()
        ):
            return
        plan = deepcopy(self.plan)
        self._submit(
            "run",
            lambda progress, cancelled: self.facade.word_semantic_tag_run(
                plan["plan_id"],
                plan["revision"],
                confirmed=True,
                progress=progress,
                cancelled=cancelled,
            ),
            self._result_ready,
            progress=True,
        )

    def _result_ready(self, value):
        if not self.plan or value.get("plan_id") != self.plan["plan_id"]:
            self._failed("结果与本次发送预览不一致，未采用任何标签建议。")
            return
        self.analysis_result = value
        self._populate()
        self.tabs.setCurrentIndex(1)
        if value.get("finished") is not True:
            self._failed("分析结果尚未完成，当前建议不能保存；请等待结束或关闭后重新核对。")
            return
        items = {row["key"]: row for row in value["items"]}
        units = [unit for unit in self.plan["units"] if unit["status"] == "ready"]
        changed = sum(self._eligible(unit, items.get(unit["key"])) for unit in units)
        failed = sum(row.get("status") == "failed" for row in value["items"])
        unchanged = sum(row.get("status") == "ready" and not row.get("changed") for row in value["items"])
        remaining = sum(unit["key"] not in items for unit in units)
        unusable = sum(
            bool(items.get(unit["key"], {}).get("changed"))
            and items[unit["key"]].get("status") == "ready"
            and not self._eligible(unit, items[unit["key"]]) for unit in units
        )
        lead = (
            "分析已停止。" if self._cancel_requested
            else "分析未全部完成。" if failed or self._failure_message or remaining or unusable
            else "分析已完成。"
        )
        summary = (
            f"可采用变更{changed}题；无变更{unchanged}题；失败{failed}题；"
            f"尚未分析{remaining}题；不可采用{unusable}题；不发送{len(self.plan['units']) - len(units)}题。"
        )
        set_status(
            self.status,
            "attention" if self._cancel_requested or failed or remaining or unusable or self._failure_message else "info",
            lead + summary + ("\n" + self._failure_message if self._failure_message else "") + "\n" + (
                "修改默认不勾选；逐题对照原标签、建议与依据，再勾选保存。尚未写入标签。"
                if self.plan.get("mode") == "recheck_automatic"
                else "可逐题查看建议与依据，取消不合适的勾选，再保存；尚未写入标签。"
            ),
        )
        self.body_scroll.ensureWidgetVisible(self.tabs)

    def _stop(self):
        if self._job and self._phase == "run" and not self._cancel_requested:
            self._cancel_requested = True
            self.tasks.cancel(self._job)
            set_status(self.status, "attention", "正在停止分析，请等待当前请求结束。已完成的建议会保留供核对；标签尚未保存。")
            self._actions()
            self.body_scroll.ensureWidgetVisible(self.status)

    def _apply(self):
        selected = self._selected_units()
        keys = [unit["key"] for unit in selected]
        if self._job or not keys or not self.plan:
            return
        if (
            self.plan.get("mode") == "recheck_automatic"
            and QMessageBox.question(
                self,
                "保存核对后的自动标签？",
                f"将保存已勾选的{len(keys)}题标签修改，具体变化见窗口中的勾选摘要。\n"
                "原题、答案、原考试出处和教师标记不变；修改保留本机历史，采用后仍标为AI建议。是否保存？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._applying_keys = tuple(keys)
        self._save_complete = False
        plan_id = self.plan["plan_id"]
        self._submit(
            "apply",
            lambda: self.facade.word_semantic_tag_apply(plan_id, keys),
            self._saved,
        )

    def _saved(self, values):
        if (not isinstance(values, list) or any(not isinstance(row, dict) or not isinstance(row.get("key"), str) for row in values)
                or len({row["key"] for row in values}) != len(values)
                or not {row["key"] for row in values}.issubset(self._applying_keys)):
            self._failed("保存回执与勾选题目不一致，无法确认保存范围。请关闭后刷新核对。")
            return
        keys = {row["key"] for row in values}
        self.saved.extend(values)
        self._saved_keys.update(keys)
        self._save_complete = keys == set(self._applying_keys)
        self._populate(preserve_checks=True)
        set_status(
            self.status,
            "success" if self._save_complete and values else "attention",
            f"已保存{len(values)}题的标签建议，原题、出处和教师标记保持不变。"
            if self._save_complete and values else
            f"本次仅收到{len(values)}题保存回执（勾选{len(self._applying_keys)}题）；其余未确认保存，请核对后再操作。",
        )

    def _failed(self, message):
        self._failure_message = message
        set_status(self.status, "error", message)
        self._actions()
        self.body_scroll.ensureWidgetVisible(self.status)

    def _finished(self, task_id):
        if self._closed or task_id != self._job:
            return
        phase, self._job = self._phase, None
        if phase == "run" and self.plan and self.analysis_result is None:
            try:
                result = self.facade.word_semantic_tag_result(self.plan["plan_id"])
            except (RuntimeError, ValueError, KeyError, TypeError, OSError):
                self.analysis_result = {"plan_id": self.plan["plan_id"], "finished": False, "items": []}
                self._populate()
                self._failed("分析已结束，但结果暂不能读取；未确认任何标签保存，请重新核对。")
            else:
                if result["finished"]:
                    self._result_ready(result)
                elif self._cancel_requested:
                    set_status(self.status, "attention", "分析已停止，尚无可采用的结果；标签未保存。")
        self._request_token = None
        self._actions()
        if phase == "apply" and self._save_complete:
            self.accept()

    def _actions(self, *_):
        if self._closed:
            return
        busy = bool(self._job)
        self.profile.setEnabled(not busy and not self.analysis_result)
        self.recheck.setEnabled(not busy and not self.analysis_result)
        self.prepare.setEnabled(
            not busy and bool(self.profile.currentData()) and not self.analysis_result
        )
        self.allow_send.setEnabled(
            not busy and bool(self.plan) and not self.analysis_result
        )
        self.run_button.setEnabled(
            not busy
            and not self._image_preview_failed
            and bool(self.plan and self.plan["request_count"])
            and self.allow_send.isChecked()
            and not self.analysis_result
        )
        self.stop.setEnabled(busy and self._phase == "run" and not self._cancel_requested)
        self.questions.setEnabled(not (busy and self._phase == "apply"))
        selected = self._selected_units()
        self.selection_summary.setText(self._selection_text(selected))
        self.apply_button.setText(f"保存勾选的标签建议（{len(selected)}题）")
        self.apply_button.setEnabled(
            not busy and bool(selected)
        )
        self.close_button.setEnabled(not busy)

    def reject(self):
        if self._job:
            set_status(
                self.status, "attention", "任务执行中，请先停止分析并等待当前请求结束。"
            )
            return
        super().reject()

    def _discard(self):
        if self.plan:
            self.facade.word_semantic_tag_discard(self.plan["plan_id"])

    def done(self, result):
        if self._closed:
            return super().done(result)
        if self._job:
            return
        if (
            result == QDialog.DialogCode.Rejected
            and any(
                i.get("changed") and i["key"] not in self._saved_keys
                for i in (self.analysis_result or {}).get("items", [])
            )
            and QMessageBox.question(
                self,
                "保留本次标签建议？",
                "还有标签建议没有保存。关闭后将丢弃本次建议，重新分析可能再次计费。确定关闭吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._discard()
        self.plan = None
        self._closed = True
        self._request_token = None
        self.tasks.task_finished.disconnect(self._finished)
        super().done(result)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.plan:
            self._resize_images()

    def _resize_images(self):
        for label, pixmap in self._image_labels:
            margins = self.paper_layout.contentsMargins()
            available = self.tabs.widget(0).viewport().width() - margins.left() - margins.right()
            scaled = pixmap.scaledToWidth(
                min(pixmap.width(), max(80, min(self.width() - 96, available))),
                Qt.TransformationMode.SmoothTransformation,
            )
            label.setWordWrap(False)
            label.setPixmap(scaled)
            label.setFixedHeight(scaled.height() + 8)

    def closeEvent(self, event):
        if self._job:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)
