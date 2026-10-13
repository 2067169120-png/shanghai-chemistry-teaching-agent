"""Cancellable local book search with explicit, version-rechecked page jumps."""
from copy import deepcopy
from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QStyledItemDelegate, QVBoxLayout)


KIND_LABELS = {'original_text':'原文', 'section':'已保存目录', 'knowledge_note':'已保存知识笔记'}


def label(text=''):
    value = QLabel(text); value.setTextFormat(Qt.TextFormat.PlainText); value.setWordWrap(True)
    value.setMinimumWidth(0); return value


class _ResultDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        width = max(1, self.parent().viewport().width())
        height = option.fontMetrics.boundingRect(QRect(0,0,max(1,width-24),0),
            int(Qt.TextFlag.TextWordWrap), str(index.data(Qt.ItemDataRole.DisplayRole) or '')).height()
        return QSize(width, height+12)


class PersonalTextbookSearchDialog(QDialog):
    page_requested = Signal(dict)
    PAGE_SIZE = 25

    def __init__(self, facade, tasks, selection, view, parent=None, *, section_id=None, guard=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.selection, self.view = deepcopy(selection), deepcopy(view)
        self.guard = guard; self._closed = False; self._task = None; self._epoch = 0
        self.result_page = None; self.offset = 0; self._locating = False
        self.setWindowTitle('在书内查找'); self.resize(780,740); self.setMinimumSize(360,480)
        root = QVBoxLayout(self)
        root.addWidget(label(view['book']['source_name']))
        self.query = QLineEdit(); self.query.setMaxLength(10000)
        self.query.setPlaceholderText('输入关键词、化学式或一句原文（1—160字）')
        self.query.setAccessibleName('当前个人教材的查找文字'); root.addWidget(self.query)
        self.scope = QComboBox(); self.scope.addItem(f"整本书 · PDF 1—{view['book']['page_count']}", None)
        section = next((s for s in view['sections'] if s['section_id'] == section_id), None)
        if section:
            self.scope.addItem(f"当前章节 · {section['title']} · PDF {section['pdf_start']}—{section['pdf_end']}", section_id)
        self.scope.setAccessibleName('选择整本书或当前已保存章节的查找范围'); root.addWidget(self.scope)
        self.content = QComboBox()
        for name, kind in [('原文、目录与知识笔记','all'), ('仅原文文字','original_text'), ('已保存目录与知识笔记','organized')]:
            self.content.addItem(name,kind)
        self.content.setAccessibleName('选择原文或已保存整理的查找类型'); root.addWidget(self.content)
        actions = QHBoxLayout(); self.search_button = QPushButton('查找'); self.stop_button = QPushButton('停止查找')
        actions.addWidget(self.search_button); actions.addWidget(self.stop_button); root.addLayout(actions)
        self.summary = label('原文查找使用PDF已有文字；目录与知识笔记单独列出。'); root.addWidget(self.summary)
        self.list = QListWidget(); self.list.setWordWrap(True); self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setItemDelegate(_ResultDelegate(self.list)); self.list.setAccessibleName('书内查找结果，原文目录知识笔记分开标明')
        root.addWidget(self.list,1)
        navigation = QHBoxLayout(); self.previous = QPushButton('上一页'); self.next = QPushButton('下一页')
        self.page_label = label(''); navigation.addWidget(self.previous); navigation.addWidget(self.page_label,1)
        navigation.addWidget(self.next); root.addLayout(navigation)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setMaximumHeight(175)
        self.details.setAccessibleName('原文摘要或已保存知识笔记的完整内容'); root.addWidget(self.details)
        bottom = QHBoxLayout(); self.pages = QComboBox(); self.pages.setAccessibleName('所选结果的全部依据PDF原页')
        self.locate_button = QPushButton('定位原页'); close = QPushButton('关闭')
        bottom.addWidget(self.pages,1); bottom.addWidget(self.locate_button); bottom.addWidget(close); root.addLayout(bottom)
        self.status = label('输入查找文字后点击“查找”。'); root.addWidget(self.status)
        self.search_button.clicked.connect(self.search); self.query.returnPressed.connect(self.search)
        self.stop_button.clicked.connect(self.stop); close.clicked.connect(self.reject)
        self.query.textChanged.connect(self.edited); self.scope.currentIndexChanged.connect(self.edited)
        self.content.currentIndexChanged.connect(self.edited); self.list.currentItemChanged.connect(self.show_row)
        self.previous.clicked.connect(lambda:self.change_page(-1)); self.next.clicked.connect(lambda:self.change_page(1))
        self.locate_button.clicked.connect(self.locate); self._controls()

    def _valid(self):
        if self._closed: return False
        try:
            if self.guard is not None: self.guard()
        except (ValueError, RuntimeError): return False
        return True

    def _cancel(self):
        self._epoch += 1
        if self._task is not None and self._task != 'starting': self.tasks.cancel(self._task)
        self._task = None; self._locating = False

    def edited(self, *_):
        if self._closed: return
        self._cancel(); self.result_page = None; self.offset = 0
        self.list.clear(); self.details.clear(); self.pages.clear(); self.page_label.clear()
        self.summary.setText('当前范围：'+self.scope.currentText()+'。原文、目录与知识笔记分别显示。')
        self.status.setText('查找文字或范围已修改，点击“查找”。'); self._controls()

    def stop(self):
        if self._closed: return
        self._cancel(); self.status.setText('已停止查找，原页、笔记勾选与选材保留。'); self._controls()

    def _options(self, expected=None):
        return dict(section_id=self.scope.currentData(), study_revision=self.view['study_revision'],
                    content=self.content.currentData(), offset=self.offset, limit=self.PAGE_SIZE, expected=expected)

    def _submit(self, label_text, operation, ready, *, locating=False):
        if not self._valid():
            self.edited(); self.status.setText('当前教材或阅读状态已变化，请重新打开查找。'); return
        self._cancel(); epoch = self._epoch; self._task = 'starting'; self._locating = locating; self._controls()
        def current():
            if self._closed or epoch != self._epoch: return False
            if not self._valid():
                self.edited(); self.status.setText('当前教材或阅读状态已变化，请重新打开查找。'); return False
            return True
        def success(value):
            if not current(): return
            self._task = None; self._locating = False; ready(value); self._controls()
        def failed(message):
            if not current(): return
            if self._locating:
                self.result_page = None; self.list.clear(); self.details.clear(); self.pages.clear(); self.page_label.clear()
                self.summary.setText('原书或查找结果未能完成核对，请重新查找。')
            self._task = None; self._locating = False; self.status.setText(message); self._controls()
        def progress(value):
            if current(): self.status.setText(f"正在查找原文：{value['completed']}/{value['total']}页 · PDF第{value['pdf_page']}页")
        try:
            if callable(getattr(self.tasks,'submit_progress',None)):
                token = self.tasks.submit_progress(label_text, lambda report,_cancelled:operation(report),
                    on_progress=progress,on_success=success,on_failure=failed)
            else:
                token = self.tasks.submit(label_text,lambda:operation(None),on_success=success,on_failure=failed)
            if current() and self._task == 'starting': self._task = token
        except RuntimeError as exc: failed(str(exc))

    def search(self, *_ , expected=None):
        query = self.query.text(); options = self._options(expected)
        self.result_page = None; self.list.clear(); self.details.clear(); self.pages.clear(); self.page_label.clear()
        self.summary.setText('当前范围：'+self.scope.currentText()+'。正在核对原书与已保存整理。')
        self.status.setText('正在核对原书并查找…')
        def ready(result):
            self.result_page = deepcopy(result)
            for row in result['items']:
                positions = '、'.join(map(str,row['pdf_pages']))
                item = QListWidgetItem(KIND_LABELS[row['kind']]+' · '+row['title']+'\nPDF '+positions+'页\n'+row['excerpt'][:100].replace('\n',' '))
                item.setData(Qt.ItemDataRole.UserRole,deepcopy(row)); item.setToolTip(row['title']); self.list.addItem(item)
            bounds = result['pdf_pages']; scope = f"PDF {bounds[0]}—{bounds[-1]} · 共{len(bounds)}页"
            counts = result['counts']
            summary = f"查找范围：{scope}；共{result['total']}项匹配、{result['total_occurrences']}处命中。\n原文{counts['original_text']}项 · 目录{counts['section']}项 · 知识笔记{counts['knowledge_note']}项"
            if result['native_searched']:
                summary += f"\n{result['native_text_pages']}页有原生文字，{result['no_native_text_pages']}页无原生文字。"
            self.summary.setText(summary)
            first = min(result['offset']+1,result['total']); last = min(result['offset']+result['limit'],result['total'])
            self.page_label.setText(f"{first}–{last} / {result['total']}项")
            if result['unreadable_pages']:
                self.status.setText('部分原页文字读取失败：PDF '+ '、'.join(map(str,result['unreadable_pages']))
                    +'页。当前显示成功读取内容与已保存整理的匹配；失败页仍可阅读原页。')
            elif result['native_searched'] and not result['native_text_pages']:
                self.status.setText('该范围没有可检索的原生文字。可查看已保存目录与知识笔记的匹配，或阅读原页。')
            elif not result['total']: self.status.setText('没有找到匹配内容，可调整文字或查找范围。')
            else: self.status.setText('选择结果与依据PDF页，点击“定位原页”。查找不会自动加入备课。')
            if self.list.count(): self.list.setCurrentRow(0)
        self._submit('查找个人教材原文与已保存整理',
            lambda report:self.facade.personal_textbook_search(self.selection,query,**options,progress_callback=report),ready)

    def change_page(self, step):
        if self._task is not None or self.result_page is None: return
        result = self.result_page; offset = max(0,result['offset']+step*self.PAGE_SIZE)
        if offset == result['offset'] or offset >= result['total']: return
        self.offset = offset; self.search(expected=result['search_identity'])

    def show_row(self, item, *_):
        self.pages.clear()
        if item is None: self.details.clear(); self._controls(); return
        row = item.data(Qt.ItemDataRole.UserRole)
        chapters = '、'.join(c['title'] for c in row['chapters']) or '未登记章节'
        text = row.get('note_text',row['excerpt'])
        self.details.setPlainText(KIND_LABELS[row['kind']]+'：'+row['title']+'\n已知章节：'+chapters
            +'\n依据PDF页：'+'、'.join(map(str,row['pdf_pages']))+'\n印刷页码未登记\n\n'+text)
        for page in row['pdf_pages']: self.pages.addItem(f'PDF第{page}页',page)
        self._controls()

    def locate(self):
        item = self.list.currentItem()
        if item is None or self._task is not None or self.result_page is None: return
        selected = deepcopy(item.data(Qt.ItemDataRole.UserRole)); page = self.pages.currentData()
        result = deepcopy(self.result_page); options = self._options(result['search_identity']); query = self.query.text()
        self.status.setText('正在重新核对查找结果与原书版本…')
        def ready(fresh):
            current = self.list.currentItem()
            if (selected not in fresh['items'] or current is None or current.data(Qt.ItemDataRole.UserRole) != selected
                    or page not in selected['pdf_pages'] or self.pages.currentData() != page):
                self.status.setText('所选结果已变化，请重新查找。'); return
            self.page_requested.emit(dict(selection=deepcopy(self.selection),study_revision=fresh['study_revision'],
                search_identity=fresh['search_identity'],pdf_page=page,result=selected))
            self.status.setText(f'已定位PDF第{page}页，可回原页阅读、框选或继续查找。')
        self._submit('重新核对教材查找定位',
            lambda report:self.facade.personal_textbook_search(self.selection,query,**options,progress_callback=report),ready,locating=True)

    def _controls(self):
        busy = self._task is not None; result = self.result_page
        self.stop_button.setEnabled(busy); self.search_button.setEnabled(not self._closed)
        self.list.setEnabled(not busy); self.pages.setEnabled(not busy)
        self.locate_button.setEnabled(not busy and self.list.currentItem() is not None and result is not None)
        self.previous.setEnabled(not busy and result is not None and result['offset']>0)
        self.next.setEnabled(not busy and result is not None and result['has_more'])

    def done(self, result):
        if not self._closed: self._closed = True; self._cancel()
        super().done(result)
