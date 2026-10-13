"""Book and section navigation beside a cancellable, read-only original page."""
from copy import deepcopy
import json

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ..desktop_composition_sources import knowledge_reader_selection, knowledge_selection
from .tasks import DesktopTaskBridge
from .textbook_source_dialog import TextbookSourceDialog


def _label(text=''):
    value=QLabel(text);value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True);value.setMinimumWidth(0)
    return value


def _identity(row):
    if row is None:return None
    value=knowledge_selection(row)
    return json.dumps(value,ensure_ascii=False,sort_keys=True)


def _pages(value):
    return '、'.join(map(str,value)) if isinstance(value,list) and value else '待核对'


class KnowledgeNotePicker(QDialog):
    def __init__(self, facade, rows, parent=None, *, tasks=None, batch=False):
        super().__init__(parent)
        self.facade=facade;self.rows=deepcopy(rows);self.chosen=None
        self.batch=batch;self.chosen_rows=[];self.composition_result=None
        self._checked={};self._prepared=None;self._batch_task=None;self._batch_epoch=0
        self.tasks=tasks or DesktopTaskBridge(self);self._own_tasks=tasks is None
        self._reader=None;self._task_id=None;self._epoch=0;self._closed=False;self._current=None
        self.setWindowTitle('选择本地教材知识卡');self.resize(1080,780)
        self.setMinimumSize(360 if batch else 480,600)
        root=QVBoxLayout(self)
        root.addWidget(_label('按教材和章节找知识卡。勾选可跨章节保留，按勾选顺序预览后一次加入当前课件。' if batch else
            '按教材和章节找知识卡，查看内容或原页，再选择加入当前作品。'))
        self.split=QSplitter();root.addWidget(self.split,1)
        navigation=QWidget();left=QVBoxLayout(navigation);left.setContentsMargins(0,0,0,0)
        form=QFormLayout();self.book=QComboBox();self.section=QComboBox();self.content_type=QComboBox()
        for title,key in (('全部内容',None),('知识摘要','textbook_concept'),('研读提示','registered'),('个人笔记','personal')):
            self.content_type.addItem(title,key)
        self.book.setMinimumWidth(0);self.section.setMinimumWidth(0)
        self.book.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.section.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        form.addRow('内容',self.content_type);form.addRow('教材',self.book);form.addRow('章节',self.section);left.addLayout(form)
        self.search=QLineEdit();self.search.setPlaceholderText('搜索标题或知识内容')
        left.addWidget(self.search);self.count=_label();left.addWidget(self.count)
        self.list=QListWidget();self.list.setWordWrap(True);left.addWidget(self.list,1)
        self.split.addWidget(navigation)
        right=QWidget();right_layout=QVBoxLayout(right);right_layout.setContentsMargins(0,0,0,0)
        self.heading=_label();right_layout.addWidget(self.heading)
        self.tabs=QTabWidget();right_layout.addWidget(self.tabs,1)
        self.detail=QPlainTextEdit();self.detail.setReadOnly(True);self.tabs.addTab(self.detail,'知识卡内容')
        self.source=QWidget();self.source_layout=QVBoxLayout(self.source)
        self.source_hint=_label('点击“查看原页”，核对所选知识卡绑定的本地教材。')
        self.source_layout.addWidget(self.source_hint);self.tabs.addTab(self.source,'教材原页')
        if batch:
            basket=QWidget();basket_layout=QVBoxLayout(basket);basket_layout.setContentsMargins(0,0,0,0)
            self.basket=QListWidget();self.basket.setWordWrap(True)
            self.basket.setAccessibleName('跨章节勾选的知识卡，按勾选顺序')
            basket_layout.addWidget(self.basket,1)
            self.remove_checked=QPushButton('移除所选勾选');self.remove_checked.clicked.connect(self.remove_choice)
            self.clear_checked=QPushButton('清空勾选');self.clear_checked.clicked.connect(self.clear_choices)
            basket_layout.addWidget(self.remove_checked);basket_layout.addWidget(self.clear_checked)
            self.basket_tab=self.tabs.addTab(basket,'已勾选（0）')
            self.batch_detail=QPlainTextEdit();self.batch_detail.setReadOnly(True)
            self.batch_detail.setAccessibleName('按勾选顺序完整预览本次加入的课堂知识卡')
            self.batch_tab=self.tabs.addTab(self.batch_detail,'本次加入预览')
        self.read_button=QPushButton('查看原页');right_layout.addWidget(self.read_button)
        self.split.addWidget(right);self.split.setStretchFactor(0,1);self.split.setStretchFactor(1,2)
        if batch:
            self.preview_button=QPushButton('预览勾选知识卡（0）');self.preview_button.clicked.connect(self.preview_choices)
            root.addWidget(self.preview_button)
        self.status=_label('勾选所需知识，再预览本次课堂内容。' if batch else '选用时重新核对来源版本；候选内容仍需教师核对。');root.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.confirm=buttons.button(QDialogButtonBox.StandardButton.Ok);self.confirm.setText('加入勾选知识卡' if batch else '选择并核对')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消');root.addWidget(buttons)
        buttons.accepted.connect(self.choose);buttons.rejected.connect(self.reject)
        self.book.addItem('全部教材',None)
        books={r['book_key']:r.get('volume_title') or '册名待核对' for r in self.rows}
        for key,title in books.items():
            n=sum(r['book_key']==key for r in self.rows)
            self.book.addItem(f'{title}（{n}张）',key)
        self.book.currentIndexChanged.connect(self._book_changed)
        self.section.currentIndexChanged.connect(self.filter)
        self.content_type.currentIndexChanged.connect(self.filter)
        self.search.textChanged.connect(self.filter);self.list.currentItemChanged.connect(self.show_row)
        if batch:self.list.itemChanged.connect(self.check_changed)
        self.read_button.clicked.connect(self.read_original);self._book_changed()

    def _book_changed(self,*_):
        book=self.book.currentData();old=self.section.currentData()
        with QSignalBlocker(self.section):
            self.section.clear();self.section.addItem('全部章节',None)
            sections={}
            for row in self.rows:
                if book is None or row['book_key']==book:
                    key=row['section_filter_key']
                    title=row.get('section_title') or '章节待核对'
                    if book is None:title=(row.get('volume_title') or '册名待核对')+' · '+title
                    sections.setdefault(key,[title,0])[1]+=1
            for key,(title,n) in sections.items():self.section.addItem(f'{title}（{n}张）',key)
            index=self.section.findData(old)
            self.section.setCurrentIndex(max(0,index))
        self.filter()

    def filter(self,*_):
        book=self.book.currentData();section=self.section.currentData();query=self.search.text().strip().casefold()
        content_type=self.content_type.currentData()
        old=self._current;selected=0
        with QSignalBlocker(self.list):
            self.list.clear()
            for row in self.rows:
                if content_type is not None and row.get('origin')!=content_type:continue
                if book is not None and row['book_key']!=book:continue
                if section is not None and row['section_filter_key']!=section:continue
                if query and query not in json.dumps(row,ensure_ascii=False).casefold():continue
                kind={'textbook_concept':'知识摘要','registered':'研读提示','personal':'个人笔记'}.get(row.get('origin'),'知识卡')
                text=kind+' · '+row['title']+'\n'+(row.get('section_title') or '章节待核对')
                if book is None:text+=' · '+(row.get('volume_title') or '册名待核对')
                item=QListWidgetItem(text);item.setData(Qt.ItemDataRole.UserRole,row)
                if self.batch:
                    item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if _identity(row) in self._checked else Qt.CheckState.Unchecked)
                self.list.addItem(item)
                if _identity(row)==old:selected=self.list.count()-1
            self.list.setCurrentRow(selected if self.list.count() else -1)
        self.count.setText(f'显示 {self.list.count()} / {len(self.rows)} 张知识卡')
        self.show_row(self.list.currentItem())

    def _clear_reader(self):
        if self._task_id is not None:self.tasks.cancel(self._task_id);self._task_id=None
        if self._reader is not None:
            self.source_layout.removeWidget(self._reader);self._reader.reject();self._reader.deleteLater();self._reader=None
        self.source_hint.show()

    def show_row(self,item,*_):
        row=item.data(Qt.ItemDataRole.UserRole) if item else None
        identity=_identity(row)
        if identity!=self._current:
            self._epoch+=1;self._clear_reader();self._current=identity
            self.tabs.setCurrentWidget(self.detail)
        if self.batch:self.batch_controls()
        else:self.confirm.setEnabled(row is not None)
        self.read_button.setEnabled(row is not None and self._task_id is None)
        if row is None:
            self.heading.setText('没有匹配的知识卡');self.detail.clear();return
        location=(row.get('volume_title') or '册名待核对')+' · '+(row.get('section_title') or '章节待核对')
        pages='PDF文件页：'+_pages(row.get('pdf_pages'))+'；印刷页：'+_pages(row.get('printed_pages'))
        self.heading.setText(row['title']+'\n'+location+'\n'+pages)
        lines=[location,pages,'原书：'+row.get('source_name','来源待核对'),row.get('summary','')]
        for key,title in (('classroom_check','课堂核对建议'),('limitation','适用范围与待核对处')):
            if row.get(key):lines.append(title+'：'+row[key])
        self.detail.setPlainText('\n\n'.join(lines))

    def read_original(self):
        item=self.list.currentItem()
        if self._closed or item is None or self._task_id is not None:return
        if self._reader is not None:self.tabs.setCurrentWidget(self.source);return
        row=deepcopy(item.data(Qt.ItemDataRole.UserRole));epoch=self._epoch
        self.read_button.setEnabled(False);self.status.setText('正在核对知识卡版本与教材来源…')
        self._task_id=self.tasks.submit('核对知识卡原页',lambda:knowledge_reader_selection(self.facade,row),
            on_success=lambda value:self._reader_ready(epoch,value),on_failure=lambda message:self._failed(epoch,message))

    def _reader_ready(self,epoch,value):
        if self._closed or epoch!=self._epoch:return
        self._task_id=None;self.read_button.setEnabled(True)
        reader=TextbookSourceDialog(self.facade,value['selection'],self.source,
            reading_mode=value['reading_mode'],embedded=True)
        self._reader=reader;self.source_hint.hide();self.source_layout.addWidget(reader,1)
        self.tabs.setCurrentWidget(self.source)
        self.status.setText('正在只读显示教材原页。核对后仍需点击“选择并核对”加入作品。')

    def _failed(self,epoch,message):
        if self._closed or epoch!=self._epoch:return
        self._task_id=None;self.read_button.setEnabled(True);self.status.setText(message)

    def choose(self):
        if self.batch:
            if self._prepared is None or self._batch_task is not None:return
            from ..desktop_knowledge_batch_preview import recheck_batch
            choices=deepcopy(list(self._checked.values()));selections=[knowledge_selection(r) for r in choices]
            key=self.selection_key();epoch=self._batch_epoch;expected=self._prepared['signature']
            self._batch_task=self.tasks.submit('确认本批知识卡',lambda:recheck_batch(self.facade,selections,expected),
                on_success=lambda result:self.batch_accepted(epoch,key,choices,result),
                on_failure=lambda message:self.batch_failed(epoch,key,message))
            self.status.setText('正在确认本批知识卡内容与来源版本…');self.batch_controls();return
        item=self.list.currentItem()
        if item is not None:
            self.chosen=deepcopy(item.data(Qt.ItemDataRole.UserRole));self.accept()

    def selection_key(self):
        return tuple(self._checked)

    def invalidate_batch(self):
        self._batch_epoch+=1;self._prepared=None;self.composition_result=None
        if self._batch_task is not None:self.tasks.cancel(self._batch_task);self._batch_task=None
        self.batch_detail.clear();self.batch_controls()

    def refresh_basket(self):
        self.basket.clear()
        for index,(key,row) in enumerate(self._checked.items(),1):
            item=QListWidgetItem(f"{index}. {row['title']}\n"+(row.get('volume_title') or '册名待核对')+' · '+(row.get('section_title') or '章节待核对'))
            item.setData(Qt.ItemDataRole.UserRole,key);self.basket.addItem(item)
        if self.basket.count():self.basket.setCurrentRow(0)
        self.tabs.setTabText(self.basket_tab,f'已勾选（{len(self._checked)}）')
        self.preview_button.setText(f'预览勾选知识卡（{len(self._checked)}）')
        self.batch_controls()

    def batch_controls(self):
        active=bool(self._checked) and self._batch_task is None and not self._closed
        self.preview_button.setEnabled(active)
        self.confirm.setEnabled(active and self._prepared is not None)
        self.remove_checked.setEnabled(bool(self._checked));self.clear_checked.setEnabled(bool(self._checked))

    def check_changed(self,item):
        row=item.data(Qt.ItemDataRole.UserRole);key=_identity(row)
        if item.checkState()==Qt.CheckState.Checked:
            if key not in self._checked and len(self._checked)>=30:
                with QSignalBlocker(self.list):item.setCheckState(Qt.CheckState.Unchecked)
                self.status.setText('每次最多选择30张知识卡，请先移除不用的勾选。');return
            self._checked[key]=deepcopy(row)
        else:self._checked.pop(key,None)
        self.invalidate_batch();self.refresh_basket()
        self.status.setText('勾选已保留；预览后可一次加入课件。')

    def remove_choice(self):
        item=self.basket.currentItem()
        if item is None:return
        self._checked.pop(item.data(Qt.ItemDataRole.UserRole),None)
        self.invalidate_batch();self.refresh_basket();self.filter()

    def clear_choices(self):
        self._checked.clear();self.invalidate_batch();self.refresh_basket();self.filter()

    def preview_choices(self):
        if not self._checked or self._batch_task is not None:return
        from ..desktop_knowledge_batch_preview import preview_batch
        self.invalidate_batch();key=self.selection_key();epoch=self._batch_epoch
        selections=[knowledge_selection(r) for r in self._checked.values()]
        self._batch_task=self.tasks.submit('预览本批知识卡',lambda:preview_batch(self.facade,selections),
            on_success=lambda value:self.batch_ready(epoch,key,value),
            on_failure=lambda message:self.batch_failed(epoch,key,message))
        self.status.setText('正在读取本批知识卡并核对教材原页…');self.batch_controls()

    def batch_ready(self,epoch,key,value):
        if self._closed or epoch!=self._batch_epoch or key!=self.selection_key():return
        from ..desktop_knowledge_batch_preview import preview_text
        self._batch_task=None;self._prepared=deepcopy(value)
        self.batch_detail.setPlainText(preview_text(value['result']))
        self.tabs.setCurrentWidget(self.batch_detail);self.batch_controls()
        self.status.setText(f'已完整预览{len(self._checked)}张知识卡，按勾选顺序加入。')

    def batch_failed(self,epoch,key,message):
        if self._closed or epoch!=self._batch_epoch or key!=self.selection_key():return
        self._batch_task=None;self._prepared=None;self.batch_detail.clear();self.batch_controls();self.status.setText(message)

    def batch_accepted(self,epoch,key,choices,result):
        if self._closed or epoch!=self._batch_epoch or key!=self.selection_key():return
        self._batch_task=None;self.chosen_rows=deepcopy(choices);self.composition_result=deepcopy(result);self.accept()

    def resizeEvent(self,event):
        self.split.setOrientation(Qt.Orientation.Vertical if self.width()<850 else Qt.Orientation.Horizontal)
        super().resizeEvent(event)

    def done(self,result):
        if not self._closed:
            self._closed=True;self._epoch+=1;self._clear_reader()
            self._batch_epoch+=1
            if self._batch_task is not None:self.tasks.cancel(self._batch_task);self._batch_task=None
            if self._own_tasks:self.tasks.shutdown(1000)
        super().done(result)
