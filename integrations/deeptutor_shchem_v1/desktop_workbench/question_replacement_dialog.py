"""Explicit, source-bound replacement of one complete saved-paper section."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QTabWidget,
    QVBoxLayout, QWidget)

from .components import page_scroll
from .explorer_reader import PersonalQuestionReader, SourceImage, text_label
from .library_detail import LibraryDetailDialog
from .word_question_filter_panel import _Flow
from ..desktop_word_metafile_preview import can_attempt_metafile


LANES = ("master", "wave1", "supplemental", "word_native", "visual_native")
LANE_LABELS = ("正式主库", "首批补充", "扩展资料", "Word 原题", "图片原题")
_TEXT_FIELDS = {"text", "text_zh", "question_text", "question_text_zh", "shared_text",
    "answer_text", "explanation", "explanation_zh", "context", "context_zh", "title",
    "title_zh", "instruction", "instruction_zh", "requirement_zh", "dependency_zh",
    "question_number", "printed_number", "label", "label_zh", "stem", "content", "response_requirements"}
_HEADINGS = {"shared_context": "共同材料", "shared_materials": "共同材料",
    "context_blocks": "共同材料", "question_blocks": "题面", "answer_blocks": "来源答案",
    "printed_questions": "主题内印刷小题", "atomic_parts": "作答单元",
    "atomic_chain": "完整作答链", "dependencies": "材料与前序结论依赖",
    "answer": "来源答案", "reference_answer": "来源参考答案"}


def _content_lines(value, lines, depth=0):
    """Display source text recursively without converting metadata into prose."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in _TEXT_FIELDS and isinstance(child, (str, int, float)) and str(child).strip():
                lines.append(str(child))
            elif key in _TEXT_FIELDS and isinstance(child, (list, tuple)):
                for text in child:
                    if isinstance(text, str) and text.strip():
                        lines.append(text)
                    elif isinstance(text, (Mapping, list, tuple)):
                        _content_lines(text, lines, depth + 1)
            elif isinstance(child, (Mapping, list, tuple)):
                if key in _HEADINGS and child:
                    lines.append("\n" + _HEADINGS[key])
                _content_lines(child, lines, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _content_lines(child, lines, depth + 1)


def _load_available_answers(reader):
    """The general readers defer answer pictures; replacement reads all of them."""
    if isinstance(reader, PersonalQuestionReader):
        reader.render(1)
        supported = True
        if reader.entry["lane"] == "word_native":
            for block in reader.entry["payload"].get("answer_blocks", []):
                assets = block.get("assets", [])
                if any(not asset.get("asset_id") or not (
                        asset.get("preview_supported") is True or can_attempt_metafile(asset)) for asset in assets):
                    supported = False
                if not assets and any(any(word in str(warning) for word in ("图片", "对象", "图形", "嵌入", "公式"))
                        for warning in block.get("warnings", [])):
                    supported = False
        reader._replacement_answer_supported = supported
    else:
        reader._replacement_answer_expected = sum(len(part.answer_images) for part in reader.detail.parts)
        for button in reader.findChildren(QPushButton, "LibraryShowAnswerImage"):
            button.click()


def _native_reader_complete(reader):
    if isinstance(reader, PersonalQuestionReader):
        return (reader.ready_to_select and getattr(reader, "_replacement_answer_supported", False)
            and all(image.loaded for image in reader.images))
    expected = getattr(reader, "_replacement_answer_expected", 0)
    answers = [image for image in reader._image_widgets if image.objectName() == "LibraryReferenceAnswerImage"]
    return (reader.preview_readiness[0] and len(answers) == expected
        and all(image.has_image for image in reader._image_widgets))


class ReplacementSectionReader(QWidget):
    readiness_changed = Signal(bool)

    def __init__(self, item, facade, tasks, plan, side, parent=None):
        super().__init__(parent)
        self.images = []
        self.native = None
        self._closed = False
        self._native_task = None
        self._native_loaded = item.get("kind") == "personal_visual_theme"
        self.valid = (isinstance(item, Mapping) and isinstance(item.get("content"), Mapping)
            and item.get("kind") in {"core_theme", "word_question", "personal_visual_theme"})
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(text_label(item.get("title_zh", "完整来源段"), "CardTitle"))
        layout.addWidget(text_label(item.get("source_zh", "来源待核对"), "MutedLabel"))
        lines = []
        _content_lines(item.get("content", {}), lines)
        layout.addWidget(text_label("\n".join(lines) or "本段以来源原图呈现，请核对下方全部图片。"))
        if item.get("kind") == "personal_visual_theme":
            for descriptor in item.get("content", {}).get("images", []):
                if not isinstance(descriptor, Mapping) or not descriptor.get("asset_id"):
                    self.valid = False
                    continue
                caption = descriptor.get("caption") or "来源图"
                role = "教师答案" if descriptor.get("teacher_only") or descriptor.get("role") == "answer" else "共同材料" if descriptor.get("role") == "shared_material" else "题面"
                layout.addWidget(text_label(f"{role} · {caption}", "CardTitle"))
                image = SourceImage(tasks, lambda asset_id=descriptor["asset_id"]:
                    facade.question_replacement_image(plan["plan_id"], plan["plan_sha256"], asset_id, side=side))
                self.images.append(image)
                image.finished.connect(lambda: self.readiness_changed.emit(self.ready))
                layout.addWidget(image)
        layout.addStretch(1)
        root.addWidget(page_scroll(body))
        if item.get("kind") in {"core_theme", "word_question"}:
            self._load_native(item, facade, tasks, root)

    def _native_readiness(self):
        self.readiness_changed.emit(self.ready)

    def _load_native(self, item, facade, tasks, root):
        source = item.get("source_ref", {})
        def ready(detail):
            if self._closed:
                return
            try:
                if item["kind"] == "word_question":
                    if detail.get("key") != source.get("key") or detail.get("revision") != source.get("revision"):
                        raise ValueError("source changed")
                    reader = PersonalQuestionReader({"lane": "word_native", "payload": detail}, facade, tasks, self)
                    _load_available_answers(reader)
                    reader.readiness_changed.connect(lambda _ready: self._native_readiness())
                else:
                    if detail.key != item["key"] or detail.scope != source.get("scope"):
                        raise ValueError("source changed")
                    reader = LibraryDetailDialog(detail, tasks, facade.library_image, self,
                        answer_image_loader=getattr(facade, "library_answer_image", None), embedded=True)
                    _load_available_answers(reader)
                    reader.preview_readiness_changed.connect(lambda _ready, _message: self._native_readiness())
                    for image in reader._image_widgets:
                        image.load_state_changed.connect(self._native_readiness)
                self.native = reader
                self._native_loaded = True
                root.addWidget(reader, 1)
                self._native_readiness()
            except Exception:
                failed("")
        def failed(_message):
            if not self._closed:
                self.valid = False
                root.addWidget(text_label("本段完整原件暂不能读取，请重新核对；不能保存替换。", "StatusAttention"))
                self._native_readiness()
        try:
            if item["kind"] == "word_question":
                operation = lambda: facade.word_question_detail(source["key"], source["revision"])
            else:
                from ..desktop_facade import ThemeCard
                # Every action binding comes from the resolved full source item.
                card = ThemeCard(key=item["key"], scope=source["scope"],
                    title_zh=item.get("title_zh", ""), paper_title_zh=item.get("source_zh", ""),
                    source_zh=item.get("source_zh", ""), atomic_total=0, atomic_matched=0,
                    shared_context_zh="", page_zh="", source_identity_sha256=source["source_identity_sha256"],
                    data_snapshot_id=source["data_snapshot_id"])
                operation = lambda: facade.library_theme_detail(card)
            self._native_task = tasks.submit("读取替换前后完整来源段", operation, on_success=ready, on_failure=failed)
            self._tasks = tasks
        except Exception:
            failed("")

    @property
    def ready(self):
        if not self.valid or not self._native_loaded or not all(image.loaded for image in self.images):
            return False
        if isinstance(self.native, PersonalQuestionReader):
            return _native_reader_complete(self.native)
        if isinstance(self.native, LibraryDetailDialog):
            return _native_reader_complete(self.native)
        return self.native is None

    def closeEvent(self, event):
        self._closed = True
        if self._native_task is not None:
            self._tasks.cancel(self._native_task)
        if self.native is not None:
            self.native.close()
        for image in self.images:
            image.cancel()
        super().closeEvent(event)


class QuestionReplacementDialog(QDialog):
    saved = Signal(dict)
    save_unconfirmed = Signal(str)
    PAGE_SIZE = 8

    def __init__(self, facade, tasks, paper_id, target_key, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.paper_id, self.target_key = paper_id, target_key
        self._epoch = 0
        self._closed = False
        self._busy = False
        self._committing = False
        self._uncertain = False
        self._task_ids = set()
        self._commit_task = None
        self._reader = None
        self._candidate = None
        self._candidate_ready = False
        self._plan = None
        self._section_readers = []
        self._search_result = None
        self._cursor = None
        self.setWindowTitle("替换当前卷完整题目段")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(0)
        self.resize(800, 720)
        outer = QVBoxLayout(self)
        body = QWidget()
        body.setMinimumWidth(0)
        root = QVBoxLayout(body)
        root.addWidget(text_label("说明本次目标，检索并完整阅读候选，再核对替换前后内容。", "CardTitle"))
        self.goal = QPlainTextEdit()
        self.goal.setPlaceholderText("本次替题目标，例如：增加对平衡移动原因的解释练习。")
        self.goal.setAccessibleName("本次替换目标")
        self.goal.setMaximumHeight(82)
        root.addWidget(self.goal)
        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("检索题面与共同材料关键词")
        self.query.setAccessibleName("替题候选关键词")
        self.knowledge = QLineEdit()
        self.knowledge.setPlaceholderText("已核验 K 标签（可选，逗号分隔）")
        self.knowledge.setAccessibleName("替题已核验知识点标签")
        search_row.addWidget(self.query, 2)
        search_row.addWidget(self.knowledge, 1)
        root.addLayout(search_row)
        source_host = QWidget()
        sources = _Flow(source_host)
        self.sources = {}
        for lane, label in zip(LANES, LANE_LABELS):
            check = QCheckBox(label)
            check.setChecked(True)
            check.toggled.connect(self._search_changed)
            self.sources[lane] = check
            sources.addWidget(check)
        root.addWidget(source_host)
        buttons = QVBoxLayout()
        self.search_button = QPushButton("检索真实候选")
        self.more_button = QPushButton("下一页候选")
        self.read_button = QPushButton("完整阅读所选候选")
        self.prepare_button = QPushButton("核对替换前后完整段")
        for button in (self.search_button, self.more_button, self.read_button, self.prepare_button):
            buttons.addWidget(button)
            button.setAutoDefault(False)
        root.addLayout(buttons)
        self.results = QListWidget()
        self.results.setAccessibleName("跨来源真实替题候选")
        self.results.setWordWrap(True)
        self.results.setMinimumHeight(88)
        self.results.setMaximumHeight(150)
        root.addWidget(self.results)
        self.reading = QTabWidget()
        self.reading.setMinimumHeight(400)
        self.reading.setAccessibleName("候选及替换前后完整段阅读")
        self.reading.setUsesScrollButtons(True)
        self.reading.tabBar().setExpanding(False)
        root.addWidget(self.reading, 1)
        self.evidence = text_label("检索只提供实际命中依据，不能据此推断难度或已确认学情。", "MutedLabel")
        root.addWidget(self.evidence)
        self.read_confirm = QCheckBox("已完整阅读")
        self.goal_confirm = QCheckBox("已核对目标")
        self.replace_confirm = QCheckBox("确认保存替换")
        confirmations = ((self.read_confirm, "我已阅读候选及替换前后完整段、共同材料和可用答案"),
            (self.goal_confirm, "我确认实际命中依据符合本次目标；未据此推断难度或学情"),
            (self.replace_confirm, "确认替换所选完整段，保留其他题目；保存后重新核对预览"))
        for check, description in confirmations:
            check.toggled.connect(self._actions)
            check.setAccessibleName(description)
            confirmation = QHBoxLayout()
            confirmation.addWidget(check)
            confirmation.addWidget(text_label(description), 1)
            root.addLayout(confirmation)
        self.scroll = page_scroll(body)
        outer.addWidget(self.scroll, 1)
        self.status = text_label("当前卷保持原样。先说明目标并检索候选。", "MutedLabel")
        outer.addWidget(self.status)
        footer = QVBoxLayout()
        self.cancel_button = QPushButton("返回，保留当前卷")
        self.commit_button = QPushButton("确认替换并保存当前卷")
        self.commit_button.setObjectName("PrimaryAction")
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.commit_button)
        outer.addLayout(footer)
        self.search_button.clicked.connect(self.search)
        self.more_button.clicked.connect(lambda: self.search(next_page=True))
        self.query.returnPressed.connect(self.search)
        self.read_button.clicked.connect(self.read_selected)
        self.prepare_button.clicked.connect(self.prepare)
        self.commit_button.clicked.connect(self.commit)
        self.cancel_button.clicked.connect(self.reject)
        self.query.textChanged.connect(self._search_changed)
        self.knowledge.textChanged.connect(self._search_changed)
        self.goal.textChanged.connect(self._goal_changed)
        self.results.currentRowChanged.connect(self._selection_changed)
        if hasattr(tasks, "task_cancelled"):
            tasks.task_cancelled.connect(self._task_cancelled)
        if hasattr(tasks, "task_finished"):
            tasks.task_finished.connect(self._task_finished)
        for button in (self.cancel_button, self.commit_button):
            button.setAutoDefault(False)
        self._actions()

    def _goal_value(self):
        values = [value for value in re.split(r"[\s,，;；]+", self.knowledge.text().strip()) if value]
        return {"text": self.goal.toPlainText().strip(), "query": self.query.text().strip(), "knowledge": values}

    def _submit(self, label, operation, ready, failed=None):
        epoch = self._epoch
        def success(value):
            if not self._closed and epoch == self._epoch:
                self._busy = False
                try:
                    ready(value)
                except Exception:
                    self._failed("来源阅读或完整计划未能显示，请重新核对；当前卷未自动重试。")
                self._actions()
        def failure(message):
            if not self._closed and epoch == self._epoch:
                self._busy = False
                (failed or self._failed)(message)
                self._actions()
        try:
            identity = self.tasks.submit(label, operation, on_success=success, on_failure=failure)
            self._task_ids.add(identity)
            return identity
        except Exception as exc:
            failure(str(exc))
            return None

    def _task_finished(self, identity):
        self._task_ids.discard(identity)

    def _task_cancelled(self, identity, _label):
        if identity == self._commit_task and not self._closed:
            self._commit_task = None
            self._committing = self._busy = False
            self._plan = None
            self.status.setText("替换已停止，当前卷未改动。重新核对后可继续。")
            self._actions()

    def _failed(self, message):
        self.status.setText(str(message) or "来源暂不能核对，当前卷保持原样。")

    def _clear_plan(self):
        self._plan = None
        for reader in self._section_readers:
            index = self.reading.indexOf(reader)
            if index >= 0:
                self.reading.removeTab(index)
            reader.close()
            reader.deleteLater()
        self._section_readers = []
        for check in (self.read_confirm, self.goal_confirm, self.replace_confirm):
            check.setChecked(False)
        self.evidence.setText("检索只提供实际命中依据，不能据此推断难度或已确认学情。")

    def _clear_candidate(self):
        self._candidate = None
        self._candidate_ready = False
        self._clear_plan()
        if self._reader is not None:
            self.reading.removeTab(self.reading.indexOf(self._reader))
            self._reader.close()
            self._reader.deleteLater()
            self._reader = None

    def _cancel_reading(self):
        self._epoch += 1
        for identity in tuple(self._task_ids):
            if identity != self._commit_task:
                self.tasks.cancel(identity)
        self._busy = False

    def _search_changed(self, *_args):
        if self._committing:
            return
        self._cancel_reading()
        self._clear_candidate()
        self._search_result = None
        self._cursor = None
        self.results.clear()
        self.status.setText("条件已变化，请重新检索；当前卷保持原样。")
        self._actions()

    def _goal_changed(self):
        if not self._committing:
            # A pending preparation must not bind a former teacher goal.
            if self._busy:
                self._cancel_reading()
            self._clear_plan()
            self._actions()

    def _selection_changed(self, *_args):
        if not self._committing:
            self._cancel_reading()
            self._clear_candidate()
            self._actions()

    def search(self, *_args, next_page=False):
        if self._committing or self._uncertain or self._closed:
            return
        goal = self._goal_value()
        lanes = [lane for lane, check in self.sources.items() if check.isChecked()]
        if not goal["text"] or not (goal["query"] or goal["knowledge"]) or not lanes:
            self.status.setText("请填写明确目标，并提供关键词或已核验知识点及至少一个来源。")
            return
        cursor = self._cursor if next_page else None
        self._cancel_reading()
        self._clear_candidate()
        self.results.clear()
        self._busy = True
        self.status.setText("正在检索真实来源候选…")
        self._actions()
        def ready(result):
            if not isinstance(result, Mapping) or not isinstance(result.get("entries"), list):
                raise ValueError("候选结果不完整，请重新检索。")
            self._search_result = result
            self._cursor = result.get("next_cursor")
            self.results.blockSignals(True)
            for entry in result["entries"]:
                if not isinstance(entry, Mapping) or entry.get("lane") not in LANES or not entry.get("entry_id"):
                    continue
                row = QListWidgetItem(f"{entry.get('title', '完整来源候选')}\n{entry.get('subtitle', '')}")
                row.setData(Qt.ItemDataRole.UserRole, deepcopy(dict(entry)))
                self.results.addItem(row)
            self.results.setCurrentRow(0 if self.results.count() else -1)
            self.results.blockSignals(False)
            notices = []
            for lane, state in result.get("source_status", {}).items():
                if isinstance(state, Mapping) and state.get("status") != "ok":
                    label = LANE_LABELS[LANES.index(lane)] if lane in LANES else "来源"
                    notices.append(f"{label}：{state.get('message') or '尚未完整核对，总数未知'}")
            notices.extend(str(value) for value in result.get("warnings", []))
            message = "选择一个真实候选，先完整阅读题面与材料。" if self.results.count() else "没有已核对且符合当前条件的真实候选，当前卷保持原样。"
            self.status.setText("\n".join([message, *notices]))
        self._submit("检索完整段替题候选", lambda: self.facade.search_unified_questions(
            lanes=lanes, query=goal["query"], filters={"knowledge": goal["knowledge"], "knowledge_mode": "all"},
            cursor=cursor, limit=self.PAGE_SIZE), ready)

    def _source_current(self):
        checker = getattr(self.facade, "unified_question_search_is_current", None)
        if callable(checker) and self._search_result is not None:
            try:
                current = checker(self._search_result.get("version_vector", {}))
            except Exception:
                current = False
            if not current:
                self._search_changed()
                self.status.setText("候选来源已变化，请重新检索；当前卷保持原样。")
                return False
        return True

    def read_selected(self, *_args):
        if self._busy or self._committing or self._uncertain or not self._source_current():
            return
        selected = self.results.currentItem()
        if selected is None:
            return
        self._cancel_reading()
        self._clear_candidate()
        entry = deepcopy(selected.data(Qt.ItemDataRole.UserRole))
        self._candidate = entry
        epoch = self._epoch
        row, lane = entry["payload"], entry["lane"]
        self._busy = True
        self.status.setText("正在按原来源读取完整题面、材料和可用答案…")
        self._actions()
        def attach(detail):
            if lane in {"word_native", "visual_native"}:
                if not isinstance(detail, Mapping) or detail.get("key") != row.get("key") or detail.get("revision") != row.get("revision"):
                    self._failed("候选来源或版本已变化，请重新检索。")
                    return
                reader = PersonalQuestionReader({**entry, "payload": detail}, self.facade, self.tasks, self.reading)
                # Read available teacher answers as well as student material.
                _load_available_answers(reader)
                reader.readiness_changed.connect(lambda _ready: self._reader_readiness(epoch))
            else:
                if detail.key != entry["key"] or detail.scope != lane:
                    self._failed("候选主题身份已变化，请重新检索。")
                    return
                reader = LibraryDetailDialog(detail, self.tasks, self.facade.library_image, self.reading,
                    answer_image_loader=getattr(self.facade, "library_answer_image", None), embedded=True)
                _load_available_answers(reader)
                reader.preview_readiness_changed.connect(lambda _ok, _message: self._reader_readiness(epoch))
                for image in reader._image_widgets:
                    image.load_state_changed.connect(lambda: self._reader_readiness(epoch))
            self._reader = reader
            self.reading.addTab(reader, "候选原题与来源")
            self.reading.setCurrentWidget(reader)
            self._reader_readiness(epoch)
        if lane == "word_native":
            operation = lambda: self.facade.word_question_detail(row["key"], row["revision"])
        elif lane == "visual_native":
            operation = lambda: self.facade.personal_visual_question_detail(row["batch_id"], row["key"], row["revision"])
        else:
            operation = lambda: self.facade.library_theme_detail(row)
        self._submit("读取替题候选原来源", operation, attach)

    def _reader_readiness(self, epoch):
        if self._closed or epoch != self._epoch or self._reader is None:
            return
        reader = self._reader
        self._candidate_ready = _native_reader_complete(reader)
        self.status.setText("候选原题与材料已加载；继续核对替换前后完整段。" if self._candidate_ready else "题面、材料或可用答案图片尚未完整加载，不能替换。")
        self._actions()

    def prepare(self, *_args):
        goal = self._goal_value()
        if self._busy or self._committing or self._uncertain or not self._candidate_ready or not goal["text"] or not self._source_current():
            return
        self._clear_plan()
        self._busy = True
        self.status.setText("正在核对目标实际命中与完整来源段…")
        self._actions()
        entry = deepcopy(self._candidate)
        def ready(plan):
            required = ("plan_id", "plan_sha256", "before_projection", "after_projection", "before_item", "after_item")
            if (not isinstance(plan, Mapping) or any(key not in plan for key in required)
                    or plan.get("paper_id") != self.paper_id or plan.get("target_key") != self.target_key):
                self._failed("完整替换计划未能核对，当前卷保持原样。")
                return
            self._plan = dict(plan)
            for side, title in (("before", "替换前完整段"), ("after", "替换后完整段")):
                reader = ReplacementSectionReader(plan[side + "_item"], self.facade, self.tasks, plan, side, self.reading)
                self._section_readers.append(reader)
                reader.readiness_changed.connect(lambda _ready: self._actions())
                self.reading.addTab(reader, title)
            self.reading.setCurrentWidget(self._section_readers[-1])
            evidence = plan.get("match_evidence", {})
            lines = []
            _content_lines(evidence, lines)
            # Keep the literal matches visible beside the backend's explanation.
            terms = evidence.get("terms", evidence.get("matched_terms", [])) if isinstance(evidence, Mapping) else []
            if terms:
                lines.append("实际题面或共同材料命中：" + "、".join(str(term) for term in terms))
            knowledge = evidence.get("knowledge", []) if isinstance(evidence, Mapping) else []
            if knowledge:
                lines.append("实际知识点命中：" + "、".join(str(value) for value in knowledge))
            if isinstance(evidence, Mapping):
                for match in evidence.get("knowledge_evidence", []):
                    if not isinstance(match, Mapping):
                        continue
                    parts = []
                    if match.get("unit_id"):
                        parts.append("命中单元：" + str(match["unit_id"]))
                    if match.get("field"):
                        parts.append("实际字段：" + str(match["field"]))
                    if isinstance(match.get("values"), list) and match["values"]:
                        parts.append("命中值：" + "、".join(str(value) for value in match["values"]))
                    if match.get("status") not in (None, "", "unknown"):
                        parts.append("来源状态：" + str(match["status"]))
                    if match.get("origin") and match["origin"] != "unknown":
                        parts.append("来源记录：" + str(match["origin"]))
                    if parts:
                        lines.append(" · ".join(parts))
                if evidence.get("knowledge_notice"):
                    lines.append(str(evidence["knowledge_notice"]))
            lines.extend(str(value) for value in plan.get("warnings", []))
            if plan.get("settings_notice"):
                lines.append(str(plan["settings_notice"]))
            self.evidence.setText("\n".join(lines) or "来源与目标已按实际检索命中核对；文字命中不能证明难度或学情。")
            self.status.setText("请阅读三个页签，核对完整材料、可用答案与目标依据，再逐项确认。")
        self._submit("核对完整段替换计划", lambda: self.facade.prepare_question_replacement(
            self.paper_id, self.target_key, goal=goal, candidate_entry=entry), ready)

    def _actions(self, *_args):
        idle = not self._busy and not self._committing and not self._closed and not self._uncertain
        self.search_button.setEnabled(idle)
        self.more_button.setEnabled(idle and bool(self._cursor))
        self.results.setEnabled(idle)
        self.read_button.setEnabled(idle and self.results.currentItem() is not None)
        self.prepare_button.setEnabled(idle and self._candidate_ready and bool(self._goal_value()["text"]))
        complete = idle and self._candidate_ready and self._plan is not None and len(self._section_readers) == 2 and all(reader.ready for reader in self._section_readers)
        for check in (self.read_confirm, self.goal_confirm, self.replace_confirm):
            check.setEnabled(complete)
        self.commit_button.setEnabled(complete and all(check.isChecked() for check in (self.read_confirm, self.goal_confirm, self.replace_confirm)))
        for field in (self.goal, self.query, self.knowledge, *self.sources.values()):
            field.setEnabled(not self._committing and not self._uncertain)
        self.cancel_button.setText("停止本次替换并保留当前卷" if self._committing else "返回，保留当前卷")

    def commit(self, *_args):
        if not self.commit_button.isEnabled() or not self._source_current():
            return
        plan = deepcopy(self._plan)
        self._busy = self._committing = True
        self.status.setText("正在重新核验来源并保存替换；请等待保存结果。")
        self._actions()
        def ready(result):
            if self._closed:
                return
            self._committing = self._busy = False
            self._commit_task = None
            if (not isinstance(result, Mapping) or result.get("status") != "saved"
                    or result.get("paper_id") != self.paper_id):
                failed("保存未能确认，请重读当前卷核对；本次替换不会自动重试。")
                return
            self._closed = True
            self._cancel_reading()
            self._clear_candidate()
            self.saved.emit(dict(result))
            super(QuestionReplacementDialog, self).accept()
        def failed(message):
            if self._closed:
                return
            self._committing = self._busy = False
            self._commit_task = None
            self._uncertain = True
            self.status.setText(str(message) + "\n请关闭并重读当前卷核对；本次替换不会自动重试。")
            self.save_unconfirmed.emit(str(message))
            self._actions()
        try:
            self._commit_task = self.tasks.submit("保存当前卷完整段替换", lambda:
                self.facade.commit_question_replacement(plan["plan_id"], plan["plan_sha256"]),
                on_success=ready, on_failure=failed)
            self._task_ids.add(self._commit_task)
        except Exception as exc:
            failed(str(exc))

    def reject(self):
        if self._committing:
            if self._commit_task is not None:
                stopped = self.tasks.cancel(self._commit_task)
                self.status.setText("正在停止替换，等待保存结果核对。" if stopped is not False else "替换已完成保存核对，正在显示结果。")
            return
        self._closed = True
        self._cancel_reading()
        self._clear_candidate()
        super().reject()

    def closeEvent(self, event):
        if self._committing:
            self.reject()
            event.ignore()
            return
        self._closed = True
        self._cancel_reading()
        self._clear_candidate()
        super().closeEvent(event)


class QuestionReplacementVersionsDialog(QDialog):
    saved = Signal(dict)
    save_unconfirmed = Signal(str)

    def __init__(self, facade, tasks, paper_id, parent=None):
        super().__init__(parent)
        self.facade, self.tasks, self.paper_id = facade, tasks, paper_id
        self._closed, self._busy, self._revision = False, False, None
        self.setWindowTitle("当前卷替题版本")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(620, 460)
        root = QVBoxLayout(self)
        root.addWidget(text_label("恢复前重新核验版本原件；保存成功后重新打开当前卷并核对预览。", "MutedLabel"))
        self.versions = QListWidget()
        self.versions.setWordWrap(True)
        self.versions.setAccessibleName("当前卷完整来源版本")
        root.addWidget(self.versions, 1)
        self.confirm = QCheckBox("确认恢复所选完整版本")
        self.confirm.setAccessibleName("确认恢复所选完整版本的来源、顺序及卷面设置")
        root.addWidget(self.confirm)
        self.status = text_label("正在读取当前卷来源版本…", "MutedLabel")
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("重新读取版本")
        self.restore_button = QPushButton("核验并恢复所选版本")
        self.close_button = QPushButton("返回当前卷")
        for button in (self.refresh_button, self.restore_button, self.close_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        root.addLayout(actions)
        self.refresh_button.clicked.connect(self.load)
        self.restore_button.clicked.connect(self.restore)
        self.close_button.clicked.connect(self.reject)
        self.confirm.toggled.connect(self._actions)
        self.versions.currentRowChanged.connect(lambda _row: self.confirm.setChecked(False))
        self.versions.currentRowChanged.connect(self._actions)
        self.load()

    def _actions(self, *_args):
        idle = not self._busy and not self._closed
        self.restore_button.setEnabled(idle and self._revision is not None and self.versions.currentItem() is not None and self.confirm.isChecked())
        for control in (self.refresh_button, self.confirm, self.versions, self.close_button):
            control.setEnabled(idle)

    def load(self, *_args):
        if self._busy or self._closed:
            return
        self._busy = True
        self.confirm.setChecked(False)
        self._actions()
        def ready(result):
            if self._closed:
                return
            self._busy = False
            self.versions.clear()
            if not isinstance(result, Mapping) or "revision" not in result:
                failed("来源版本列表不完整，请重新读取。")
                return
            self._revision = result["revision"]
            for number, version in enumerate(result.get("versions", []), 1):
                goal = version.get("goal", {})
                goal_text = goal.get("text", "") if isinstance(goal, Mapping) else str(goal)
                row = QListWidgetItem(f"{number}　{version.get('title') or '保存来源版本'} · {version.get('created_at', '')}\n{version.get('action') or '完整来源版本'} · {goal_text}")
                row.setData(Qt.ItemDataRole.UserRole, version["version_id"])
                self.versions.addItem(row)
            self.status.setText(str(result.get("history_notice") or "选择版本并明确确认后恢复。") if self.versions.count() else "尚无替题来源版本；当前卷保持原样。")
            self._actions()
        def failed(message):
            if not self._closed:
                self._busy = False
                self._revision = None
                self.status.setText(str(message))
                self._actions()
        try:
            self.tasks.submit("读取当前卷替题版本", lambda: self.facade.list_question_replacement_versions(self.paper_id), on_success=ready, on_failure=failed)
        except Exception as exc:
            failed(str(exc))

    def restore(self, *_args):
        if not self.restore_button.isEnabled():
            return
        version_id = self.versions.currentItem().data(Qt.ItemDataRole.UserRole)
        revision = self._revision
        self._busy = True
        self.status.setText("正在核验版本原件并恢复当前卷…")
        self._actions()
        def ready(result):
            if self._closed:
                return
            self._busy = False
            if (not isinstance(result, Mapping) or result.get("status") != "saved"
                    or result.get("paper_id") != self.paper_id):
                failed("恢复保存未能确认，请重读当前卷核对。")
                return
            self._closed = True
            self.saved.emit(dict(result))
            super(QuestionReplacementVersionsDialog, self).accept()
        def failed(message):
            if not self._closed:
                self._busy = False
                self._revision = None
                self.status.setText(str(message) + "\n请重读当前卷核对；本次恢复不会自动重试。")
                self.save_unconfirmed.emit(str(message))
                self._actions()
        try:
            self.tasks.submit("恢复当前卷替题版本", lambda: self.facade.restore_question_replacement_version(
                self.paper_id, version_id, expected_revision=revision), on_success=ready, on_failure=failed)
        except Exception as exc:
            failed(str(exc))

    def reject(self):
        if self._busy:
            return
        self._closed = True
        super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
            return
        self._closed = True
        super().closeEvent(event)
