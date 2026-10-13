"""Explicit local knowledge/example relations, independent of lesson selections."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .components import page_scroll


def _label(text=''):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    return widget


def _link_id(row):
    return row.get('id', row.get('link_id')) if isinstance(row, dict) else None


def _state(row):
    status = row.get('status', 'unknown')
    return {'current': '可用', 'missing': '原件缺失', 'stale': '版本已变化',
            'unknown': '尚未检查'}.get(status, '尚未检查')


def _endpoint_title(endpoint):
    if not isinstance(endpoint, dict):
        return ''
    title = endpoint.get('title') or endpoint.get('source_label')
    if title:
        return str(title)
    title = str(endpoint.get('source_name') or endpoint.get('record_id') or '')
    ranges = endpoint.get('ranges', {})
    if ranges.get('block_start'):
        title += f" · 原文区块{ranges['block_start']}—{ranges.get('block_end', ranges['block_start'])}"
    return title


def _card_text(card):
    original = card.get('original', {}) if isinstance(card, dict) else {}
    return '\n\n'.join(row['value'] for row in original.get('blocks', [])
                       if row.get('kind') == 'text')


class _KnowledgeRelationPicker(QDialog):
    """Explicit original selections, including this lesson's handout sections."""
    def __init__(self, rows, parent):
        super().__init__(parent)
        self.chosen = None
        self.setWindowTitle('选择要关联的原知识')
        self.resize(760, 650)
        self.setMinimumSize(360, 500)
        root = QVBoxLayout(self)
        root.addWidget(_label('选择具体知识。课件中的讲义知识段按原文范围识别；作品中的自拟标题不会改变知识身份。'))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAccessibleName('原教材知识与本课讲义知识段候选')
        root.addWidget(self.list, 2)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName('所选原知识内容预览')
        root.addWidget(self.detail, 1)
        for row in rows:
            item = QListWidgetItem(str(row.get('title', '原知识')))
            item.setData(Qt.ItemDataRole.UserRole, deepcopy(row))
            self.list.addItem(item)
        self.list.currentItemChanged.connect(self.show_row)
        self.confirm = QPushButton('选择后阅读原知识并填写理由')
        self.confirm.clicked.connect(self.choose)
        root.addWidget(self.confirm)
        close = QPushButton('取消')
        close.clicked.connect(self.reject)
        root.addWidget(close)
        self.list.setCurrentRow(0)
        self.show_row(self.list.currentItem())

    def show_row(self, item, *_):
        row = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.confirm.setEnabled(row is not None)
        self.detail.setPlainText('' if row is None else '\n\n'.join(str(row.get(key) or '')
                                 for key in ('title', 'volume_title', 'section_title', 'summary')))

    def choose(self):
        item = self.list.currentItem()
        if item is None:
            return
        from ..desktop_composition_sources import knowledge_selection
        row = item.data(Qt.ItemDataRole.UserRole)
        self.chosen = deepcopy(row['card']) if row.get('kind') == 'handout_section' else (
            deepcopy(row['selection']) if 'selection' in row else knowledge_selection(row))
        self.accept()


def _handout_reader(facade, card, parent):
    from .handout_knowledge_picker import HandoutKnowledgePicker
    evidence = card['original']['evidence']
    expected_section = evidence['section_snapshot']['section']
    class Reader(HandoutKnowledgePicker):
        def _outline_ready(self, epoch, source, value):
            super()._outline_ready(epoch, source, value)
            if self._outline is None:
                return
            if expected_section not in self._outline['sections']:
                self.reader.set_source(None)
                self.status.setText('原知识栏目已变化，请返回后重新检查关联。')
                return
            self._sections = [deepcopy(expected_section)]
            self.filter_sections()
            self.sections.setEnabled(False)
            self.reader.scroll_to_block(expected_section['start'])
            self.status.setText('正在阅读关联的原知识段、表格和图片。')

        def choose(self):
            self.reject()
    reader = Reader(facade, parent, initial_source=evidence['handout_source'])
    reader.setWindowTitle('阅读关联的原讲义知识段')
    reader.navigation.hide()
    for index in range(reader.reading_panel.layout().count()):
        form = reader.reading_panel.layout().itemAt(index).layout()
        if isinstance(form, QFormLayout):
            for i in range(form.count()):
                if form.itemAt(i).widget() is not None:
                    form.itemAt(i).widget().hide()
    reader.confirm.hide()
    reader.details_button.hide()
    intro = reader.layout().itemAt(0).widget()
    if isinstance(intro, QLabel):
        intro.setText('阅读关联的原讲义知识段；原文、表格与图片保持完整。')
    reader.split.setSizes([0, 1000])
    return reader


class KnowledgeQuestionLinksDialog(QDialog):
    """Browse, inspect and explicitly save relations; return one course request.

    The relationship collection is a separate transaction. Closing this dialog
    never imports course resources. Only the dedicated course button returns a
    checked selection, which the lesson owner rechecks before its own commit.
    """

    def __init__(self, facade, tasks, parent=None, *, knowledge_resource=None,
                 question_selection=None, guard=None, allow_course_add=False,
                 service=None, course_resources=None):
        super().__init__(parent)
        if (knowledge_resource is None) == (question_selection is None):
            raise ValueError('请选择一张知识卡或一道完整题。')
        from ..desktop_knowledge_question_links import KnowledgeQuestionLinks
        self.facade, self.tasks = facade, tasks
        self.service = service or KnowledgeQuestionLinks(facade)
        self.knowledge = deepcopy(knowledge_resource)
        self.question = deepcopy(question_selection)
        self._course_question_resource = (deepcopy(question_selection) if isinstance(question_selection, dict)
                                          and question_selection.get('original', {}).get('kind') == 'complete_question' else None)
        self.guard = guard
        self.course_resources = deepcopy(course_resources or [])
        self.allow_course_add = bool(allow_course_add and self.knowledge is not None)
        self.course_request = None
        self._snapshot = None
        self._rows = []
        self._selected = None
        self._assessed = None
        self._prepared = None
        self._new_knowledge = None
        self._new_question = None
        self._context = None
        self._closed = False
        self._active = None
        self._epoch = 0
        self._serial = 0
        self._plan = None
        self._restore_path = None
        self.setWindowTitle('知识卡与课堂例题的关联')
        self.resize(1120, 790)
        self.setMinimumSize(360, 550)
        root = QVBoxLayout(self)
        root.addWidget(_label('为具体知识选择合适的例题，并写明联系。保存关联后可在以后备课中复用；加入当前课件需另行选择。'))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        relation = QWidget()
        body = QVBoxLayout(relation)
        body.setContentsMargins(0, 0, 0, 0)
        self.heading = _label('正在读取关联…')
        body.addWidget(self.heading)
        self.split = QSplitter()
        self.split.setChildrenCollapsible(False)
        body.addWidget(self.split, 1)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAccessibleName('已保存的知识卡与题目关联，含原件缺失或版本变化的记录')
        lay.addWidget(self.list, 1)
        self.new_button = QPushButton('选择例题并建立关联…' if self.knowledge is not None else '选择知识卡并建立关联…')
        self.new_button.setAccessibleName('浏览原知识和完整题后建立新关联')
        self.new_button.clicked.connect(self.choose_endpoint)
        lay.addWidget(self.new_button)
        self.refresh_button = QPushButton('刷新关联')
        self.refresh_button.clicked.connect(self.refresh)
        lay.addWidget(self.refresh_button)
        self.location_button = QPushButton('明确原题的归档位置…')
        self.location_button.setAccessibleName('有多个同字节 Word 归档时明确选择原批次和文件')
        self.location_button.setVisible(self._course_question_resource is not None)
        self.location_button.clicked.connect(self.choose_question_location)
        lay.addWidget(self.location_button)
        self.split.addWidget(left)
        content = QWidget()
        right = QVBoxLayout(content)
        right.setContentsMargins(6, 0, 6, 0)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName('原知识、完整题的关联依据及来源状态')
        right.addWidget(self.detail, 2)
        readers = QHBoxLayout()
        self.read_knowledge = QPushButton('阅读原知识…')
        self.read_knowledge.clicked.connect(self.open_knowledge)
        self.read_question = QPushButton('阅读完整题与答案…')
        self.read_question.clicked.connect(self.open_question)
        readers.addWidget(self.read_knowledge)
        readers.addWidget(self.read_question)
        right.addLayout(readers)
        right.addWidget(_label('这道题如何体现这个知识点？'))
        self.reason = QPlainTextEdit()
        self.reason.setAccessibleName('具体关联理由，明确保存后生效')
        self.reason.setPlaceholderText('例如：题目要求用氢键解释水的性质，适合在氢键条件和影响讲解后讨论。')
        right.addWidget(self.reason, 1)
        self.save_button = QPushButton('保存关联理由')
        self.save_button.setAccessibleName('明确保存知识卡与题目的关联及理由')
        self.save_button.clicked.connect(self.save_reason)
        right.addWidget(self.save_button)
        self.remove_button = QPushButton('解除所选关联')
        self.remove_button.clicked.connect(self.remove_relation)
        right.addWidget(self.remove_button)
        self.split.addWidget(page_scroll(content))
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 2)
        self.tabs.addTab(relation, '知识与例题')
        transfer = QWidget()
        layout = QVBoxLayout(transfer)
        layout.addWidget(_label('关联可单独保存为 JSON 文件，包含知识、原题版本和理由。恢复前查看差异；原件缺失时保留关联，补齐相同原件后再检查。'))
        self.transfer_detail = QPlainTextEdit()
        self.transfer_detail.setReadOnly(True)
        self.transfer_detail.setAccessibleName('关联文件检查结果与冲突预览')
        layout.addWidget(self.transfer_detail, 1)
        self.export_button = QPushButton('导出关联 JSON…')
        self.export_button.clicked.connect(self.export_relations)
        layout.addWidget(self.export_button)
        self.inspect_button = QPushButton('选择文件并检查…')
        self.inspect_button.clicked.connect(self.inspect_relations)
        layout.addWidget(self.inspect_button)
        self.restore_button = QPushButton('确认恢复新关联')
        self.restore_button.clicked.connect(self.restore_relations)
        layout.addWidget(self.restore_button)
        self.tabs.addTab(transfer, '导出与恢复')
        self.status = _label('')
        self.status.setAccessibleName('知识卡与例题关联操作状态')
        root.addWidget(self.status)
        footer = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.footer_layout = footer
        self.course_button = QPushButton('将所选关联题加入本课')
        self.course_button.setObjectName('PrimaryAction')
        self.course_button.setAccessibleName('把来源与版本有效的关联完整题加入当前课件')
        self.course_button.setVisible(self.allow_course_add)
        self.course_button.clicked.connect(self.choose_for_course)
        footer.addWidget(self.course_button, 1)
        self.cancel_button = QPushButton('取消当前读取')
        self.cancel_button.clicked.connect(self.cancel_operation)
        footer.addWidget(self.cancel_button)
        self.close_button = QPushButton('返回课件' if allow_course_add else '关闭')
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        root.addLayout(footer)
        self.list.currentItemChanged.connect(self.selected_relation)
        self.reason.textChanged.connect(self._buttons)
        if hasattr(tasks, 'task_finished'):
            tasks.task_finished.connect(self._task_finished)
        self._buttons()
        QTimer.singleShot(0, self.refresh)

    def _check(self):
        if self._closed or getattr(self.tasks, 'is_shutting_down', False):
            raise ValueError('关联窗口已关闭。')
        if self.guard is not None:
            self.guard()

    def _run(self, title, operation, done):
        if self._closed or self._active is not None:
            return
        try:
            self._check()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self._serial += 1
        serial, epoch = self._serial, self._epoch
        self._active = 'starting'
        self.status.setText(title + '…')
        self._buttons()
        def success(value):
            if self._closed or serial != self._serial or epoch != self._epoch:
                return
            self._active = None
            try:
                self._check()
                done(value)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.status.setText(str(exc))
            self._buttons()
        def failure(message):
            if self._closed or serial != self._serial or epoch != self._epoch:
                return
            self._active = None
            self.status.setText(message)
            self._buttons()
        def checked():
            self._check()
            return operation()
        try:
            task_id = self.tasks.submit(title, checked, on_success=success, on_failure=failure)
            if self._active == 'starting' and serial == self._serial:
                self._active = task_id
        except (RuntimeError, TypeError) as exc:
            failure(str(exc))

    def _task_finished(self, task_id):
        if self._active == task_id:
            self._active = None
            self._buttons()

    def _buttons(self):
        idle = not self._closed and self._active is None
        existing = self._selected is not None and self._prepared is None
        draft = self._prepared is not None
        current = (existing and self._assessed is not None and self._assessed.get('status') == 'current'
                   and not self._assessed.get('removed', False))
        if self.knowledge is not None:
            current = (current and self._context is not None and not self._context.get('error')
                       and self._context.get('endpoint') == self._assessed.get('knowledge'))
        for button in (self.new_button, self.refresh_button, self.export_button, self.inspect_button):
            button.setEnabled(idle)
        self.location_button.setEnabled(idle)
        self.list.setEnabled(idle)
        self.reason.setReadOnly(not idle or not (existing or draft))
        self.save_button.setEnabled(idle and bool(self.reason.toPlainText().strip()) and (existing or draft))
        self.save_button.setText('明确保存新关联' if draft else '保存关联理由')
        self.remove_button.setEnabled(idle and existing)
        self.read_knowledge.setEnabled(idle and (existing or draft or self.knowledge is not None))
        self.read_question.setEnabled(idle and (existing or draft or self.question is not None))
        self.course_button.setEnabled(idle and bool(current))
        self.cancel_button.setEnabled(not self._closed and self._active is not None)
        self.restore_button.setEnabled(idle and self._plan is not None)
        self.export_button.setEnabled(idle and self._snapshot is not None)

    def refresh(self):
        if self._closed or self._active is not None:
            return
        chosen = self._selected
        def read():
            rows = (self.service.list(knowledge=self.knowledge, assess=False) if self.knowledge is not None
                    else self.service.reverse(self.question, assess=False))
            try:
                context = (self.service.knowledge_context(self.knowledge) if self.knowledge is not None
                           else self.service.resolve_selection(self.question))
            except (ValueError, OSError, KeyError) as exc:
                subject = self.knowledge if self.knowledge is not None else self.question
                original = subject.get('original', {})
                context = {'endpoint': {'title': original.get('title') or _endpoint_title(subject)},
                           'error': str(exc)}
            return self.service.snapshot(), context, rows
        def ready(value):
            self._snapshot, self._context, self._rows = value
            self._prepared = None
            self._assessed = None
            self._selected = None
            self._new_knowledge = self._new_question = None
            self.heading.setText(('知识卡：' if self.knowledge is not None else '完整题：') +
                                 _endpoint_title(self._context.get('endpoint', {})))
            self.list.blockSignals(True)
            self.list.clear()
            index = 0
            for row in self._rows:
                if row.get('removed', False):
                    continue
                other = row.get('question' if self.knowledge is not None else 'knowledge', {})
                item = QListWidgetItem(_endpoint_title(other) + ' · ' + _state(row))
                item.setData(Qt.ItemDataRole.UserRole, deepcopy(row))
                self.list.addItem(item)
                if _link_id(row) == chosen:
                    index = self.list.count() - 1
            self.list.setCurrentRow(index if self.list.count() else -1)
            self.list.blockSignals(False)
            self.reason.clear()
            self.detail.setPlainText('还没有保存关联。选择具体知识和完整题，阅读后填写理由。')
            self.status.setText(self._context.get('error') or
                               f'已保存 {self.list.count()} 条关联；浏览和本次备课勾选独立。')
            self.selected_relation(self.list.currentItem())
        self._run('读取知识与例题关联', read, ready)

    def selected_relation(self, item, *_):
        row = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._prepared = None
        self._new_knowledge = self._new_question = None
        self._assessed = None
        self._selected = _link_id(row)
        if row is None:
            self._buttons()
            return
        self.reason.setPlainText(row.get('reason', ''))
        self._show_relation(row)
        identity = self._selected
        def checked(value):
            if self._selected != identity:
                return
            self._assessed = value
            self._show_relation(value)
            item = self.list.currentItem()
            if item is not None and _link_id(item.data(Qt.ItemDataRole.UserRole)) == identity:
                other = value.get('question' if self.knowledge is not None else 'knowledge', {})
                item.setText(_endpoint_title(other) + ' · ' + _state(value))
                item.setData(Qt.ItemDataRole.UserRole, deepcopy(value))
            self.status.setText('所选关联：' + _state(value))
        self._run('核对所选关联的原件与版本', lambda: self.service.evaluate(identity), checked)

    def _show_relation(self, row):
        lines = ['知识：' + _endpoint_title(row.get('knowledge', {})),
                 '完整题：' + _endpoint_title(row.get('question', {})),
                 '使用状态：' + _state(row), '关联理由：' + row.get('reason', '')]
        for key, title in (('knowledge_status', '知识原件'), ('question_status', '题目原件')):
            if row.get(key):
                lines.append(title + '：' + {'current': '当前来源已核验', 'unknown': '尚未核验',
                    'missing': '来源缺失', 'stale': '内容或范围已变化'}.get(row[key], '尚未核验'))
        original = row.get('knowledge', {}).get('original')
        if original:
            lines.append('保存时的原知识：\n' + _card_text({'original': original}))
        self.detail.setPlainText('\n\n'.join(lines))

    def choose_endpoint(self):
        if self._active is not None or self._closed:
            return
        if self.knowledge is not None:
            self._browse_question()
        else:
            def ready(value):
                picker = _KnowledgeRelationPicker(value['items'], self)
                try:
                    if picker.exec() != QDialog.DialogCode.Accepted or picker.chosen is None:
                        return
                    self._check()
                    self._prepare_relation(picker.chosen, self.question)
                finally:
                    picker.deleteLater()
            self._run('读取可关联的原知识卡', lambda: self.service.choices(resources=self.course_resources), ready)

    def choose_question_location(self):
        if self._course_question_resource is None:
            return
        def ready(rows):
            if not rows:
                self.status.setText('本地没有这道题的原 Word 归档，请在资料导入中补齐相同原件。')
                return
            picker = QDialog(self)
            picker.setWindowTitle('明确原题的原批次与文件')
            picker.resize(700, 520)
            layout = QVBoxLayout(picker)
            layout.addWidget(_label('这些归档的文件内容相同。请选择本次使用的原批次和文件。'))
            choices = QListWidget()
            choices.setAccessibleName('相同字节原题的全部归档候选，需明确选择')
            choices.setWordWrap(True)
            for row in rows:
                item = QListWidgetItem(row['label'])
                item.setData(Qt.ItemDataRole.UserRole, row)
                choices.addItem(item)
            layout.addWidget(choices, 1)
            confirm = QPushButton('核对所选归档')
            confirm.clicked.connect(picker.accept)
            layout.addWidget(confirm)
            close = QPushButton('取消')
            close.clicked.connect(picker.reject)
            layout.addWidget(close)
            choices.setCurrentRow(0)
            try:
                if picker.exec() != QDialog.DialogCode.Accepted or choices.currentItem() is None:
                    return
                row = choices.currentItem().data(Qt.ItemDataRole.UserRole)
                location = {key: row[key] for key in ('batch_id', 'archive_source_id')}
                def resolved(value):
                    self.question = deepcopy(value['endpoint'])
                    self._context = value
                    self.refresh()
                self._run('核对所选原题归档', lambda: self.service.resolve_selection(
                    self._course_question_resource, location=location), resolved)
            finally:
                picker.deleteLater()
        self._run('读取原题归档候选',
                  lambda: self.service.question_locations(self._course_question_resource), ready)

    def _browse_question(self):
        from .word_question_dialog import WordQuestionDialog
        context = (self._context or {}).get('browser_context') or {}
        dialog = WordQuestionDialog(self.facade, self.tasks, self, handoff_mode='reference',
            persist_selection=False, allow_knowledge_links=False,
            initial_curriculum_context=context.get('curriculum_context'),
            initial_search_query=context.get('search_query', ''))
        dialog.setWindowTitle('选择一题建立知识关联')
        dialog.import_button.setText('选择此题并填写关联理由')
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.reference is None:
                return
            self._check()
            selections = dialog.reference.get('selections', [])
            if len(selections) != 1:
                self.status.setText('建立关联时每次选择一道完整题。')
                return
            row = dialog._items.get(selections[0]['key'])
            if row is None or row.get('revision') != selections[0]['revision']:
                raise ValueError('所选题目在阅读期间变化，请重新选择。')
            self._prepare_relation(self.knowledge, deepcopy(row))
        finally:
            dialog.deleteLater()

    def _prepare_relation(self, knowledge, question):
        def prepare():
            return self.service.snapshot(), self.service.prepare(knowledge, question)
        def ready(value):
            self._snapshot, self._prepared = value
            self._selected = None
            self._assessed = None
            self._new_knowledge, self._new_question = deepcopy(knowledge), deepcopy(question)
            self.list.clearSelection()
            self.reason.clear()
            self.detail.setPlainText('知识：' + _endpoint_title(self._prepared.get('knowledge', {})) +
                '\n\n完整题：' + _endpoint_title(self._prepared.get('question', {})) +
                '\n\n' + _card_text(self._prepared.get('knowledge_card', {})) +
                '\n\n阅读两端原内容后，填写理由并明确保存。')
            self.status.setText('已核对两个原件；尚未保存关联，也未加入课件。')
        self._run('核对原知识与完整题', prepare, ready)

    def save_reason(self):
        reason = self.reason.toPlainText().strip()
        if not reason or self._snapshot is None:
            return
        expected = deepcopy(self._snapshot)
        if self._prepared is not None:
            knowledge, question = deepcopy(self._new_knowledge), deepcopy(self._new_question)
            shown = deepcopy(self._prepared)
            def save():
                prepared = self.service.prepare(knowledge, question, reason)
                if (prepared.get('knowledge') != shown.get('knowledge') or
                        prepared.get('question') != shown.get('question')):
                    raise ValueError('原知识或题目在填写理由期间已变化，请重新选择。')
                return self.service.save(prepared, expected=expected, guard=self._check)
        elif self._selected is not None:
            identity = self._selected
            def save():
                return self.service.edit_reason(identity, reason, expected=expected, guard=self._check)
        else:
            return
        def ready(value):
            self._snapshot = value
            self._plan = None
            self.refresh()
        self._run('保存明确的知识例题关联', save, ready)

    def remove_relation(self):
        if self._selected is None or self._snapshot is None:
            return
        identity, expected = self._selected, deepcopy(self._snapshot)
        def ready(value):
            self._snapshot = value
            self._plan = None
            self.refresh()
        self._run('解除关联并保留修改历史',
                  lambda: self.service.remove(identity, expected=expected, guard=self._check), ready)

    def _current_row(self):
        return next((row for row in self._rows if _link_id(row) == self._selected), None)

    def open_knowledge(self):
        row = self._current_row()
        knowledge = self._new_knowledge if self._prepared is not None else (
            row['knowledge'] if row else self.knowledge)
        if knowledge is None:
            return
        def read():
            context = self.service.knowledge_context(knowledge)
            if context['card']['original']['kind'] == 'handout_knowledge':
                return context, None
            from ..desktop_knowledge_reading import knowledge_card_reader
            return context, knowledge_card_reader(self.facade, context['card'])
        def ready(value):
            from .knowledge_card_reader import KnowledgeCardReader
            context, reading = value
            if reading is None:
                dialog = _handout_reader(self.facade, context['card'], self)
            else:
                dialog = KnowledgeCardReader(self.facade, context['card'], reading, self)
                dialog.pick.hide()
                dialog.close_button.setText('返回关联')
            try:
                dialog.exec()
                self._check()
            finally:
                dialog.deleteLater()
        self._run('打开关联知识的原内容与教材页', read, ready)

    def open_question(self):
        row = self._current_row()
        question = self._new_question if self._prepared is not None else (row if row else self.question)
        if question is None:
            return
        def ready(value):
            from .word_question_dialog import WordQuestionDialog
            selection, source = value['selection'], value['row']
            dialog = WordQuestionDialog(value['scoped_facade'], self.tasks, self,
                initial_source_id=source['source_id'], required_source_id=source['source_id'],
                initial_question_key=selection['key'], persist_selection=False,
                allow_knowledge_links=False)
            dialog.setWindowTitle('阅读关联的完整题与原答案')
            dialog.preparation_controls.hide()
            dialog.preparation_toggle.hide()
            dialog.select_current_button.hide()
            try:
                dialog.exec()
                self._check()
            finally:
                dialog.deleteLater()
        self._run('核对并打开关联完整题', lambda: self.service.resolve_selection(question), ready)

    def choose_for_course(self):
        if not self.allow_course_add or self._selected is None or not self.course_button.isEnabled():
            return
        identity, expected = self._selected, deepcopy(self._snapshot)
        def checked():
            row = self.service.evaluate(identity)
            if row.get('status') != 'current' or row.get('removed', False):
                raise ValueError('关联原件已变化或缺失，请补齐后重新检查。')
            if self.service.knowledge_context(self.knowledge)['endpoint'] != row['knowledge']:
                raise ValueError('所选课件知识与关联的原知识版本不一致，请重新加入当前知识卡。')
            resolved = self.service.resolve_selection(row)
            selection = {**resolved['selection'], 'points': 2}
            reference = resolved['scoped_facade'].word_question_reference([selection], include_images=True)
            if self.service.snapshot() != expected:
                raise ValueError('关联在选择期间变化，请刷新后重新加入。')
            return {'link_id': identity, 'snapshot': expected, 'selection': selection,
                    'reference': reference, 'scoped_facade': resolved['scoped_facade']}
        def ready(value):
            self.course_request = value
            self.accept()
        self._run('核对关联题并准备加入本课', checked, ready)

    def export_relations(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存知识例题关联', '知识例题关联.json', 'JSON 文件 (*.json)')
        if not path:
            return
        from ..desktop_knowledge_question_links_transfer import export_knowledge_question_links
        def ready(value):
            self.transfer_detail.setPlainText('关联已导出：' + path)
            self.status.setText('已保存独立关联文件。')
        self._run('导出知识例题关联',
                  lambda: export_knowledge_question_links(self.service, path, expected_revision=self._snapshot['revision']), ready)

    def inspect_relations(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择知识例题关联', '', 'JSON 文件 (*.json)')
        if not path:
            return
        self._plan = None
        self._restore_path = None
        from ..desktop_knowledge_question_links_transfer import inspect_knowledge_question_links
        def ready(value):
            self._plan = deepcopy(value)
            self._restore_path = path
            counts = value.get('counts', {})
            lines = ['文件：' + path, f"新增 {counts.get('new', 0)} 条，重复 {counts.get('duplicates', 0)} 条，冲突 {counts.get('conflicts', 0)} 条。",
                     '原件和版本将在恢复后单独检查；原件缺失的关联仍会保留。']
            for row in value.get('conflict_details', []):
                lines.append('冲突理由：\n当前：' + row.get('current_reason', '') +
                             '\n文件：' + row.get('incoming_reason', ''))
            self.transfer_detail.setPlainText('\n\n'.join(lines))
            self.restore_button.setText('保留当前冲突记录，只恢复新关联' if value.get('conflicts') else '确认恢复新关联')
            self.status.setText('检查完成。请查看差异后明确恢复；不会静默覆盖当前关联。')
        self._run('检查关联文件与恢复差异', lambda: inspect_knowledge_question_links(self.service, path), ready)

    def restore_relations(self):
        if self._plan is None or self._restore_path is None:
            return
        from ..desktop_knowledge_question_links_transfer import restore_knowledge_question_links
        plan, path = deepcopy(self._plan), self._restore_path
        policy = 'keep_current' if plan.get('conflicts') else 'block'
        def ready(value):
            self._plan = None
            self._restore_path = None
            self.transfer_detail.appendPlainText('\n已完成显式恢复；当前冲突记录保持。')
            self.refresh()
        self._run('按已检查的计划恢复关联',
                  lambda: restore_knowledge_question_links(self.service, path, plan,
                      conflict_policy=policy, guard=self._check), ready)

    def cancel_operation(self):
        if self._active is None:
            return
        self._serial += 1
        self._epoch += 1
        task_id, self._active = self._active, None
        if task_id != 'starting':
            self.tasks.cancel(task_id)
        self.status.setText('已请求取消，请刷新查看已保存的关联。')
        self._buttons()

    def resizeEvent(self, event):
        if hasattr(self, 'split'):
            narrow = event.size().width() < 800
            self.split.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
            self.footer_layout.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)

    def done(self, result):
        if not self._closed:
            self.cancel_operation()
            self._closed = True
            self._epoch += 1
        super().done(result)

    def closeEvent(self, event):
        self.reject()
        event.accept()
