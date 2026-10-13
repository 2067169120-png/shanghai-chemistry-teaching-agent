"""Source cards, free paper positions and independent reading order in one draft."""
from copy import deepcopy
import json

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,QInputDialog,
    QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)
from ..desktop_lesson_composition import (FORMATS, block, resource, new_composition,
    enable_composition, edit_resource, place_resource, reorder_resources, resource_frames,
    suggested_resource_title, set_student_arrangement, set_click_reveal, TEACHER_ROLES, validate_composition)
from ..desktop_composition_render import arrange_composition, layout_composition, paint_elements
from ..desktop_lesson_design import digest
from ..desktop_composition_insertion import INSERTION_LABELS, insert_composition_batch
from ..desktop_composition_source_summary import source_summary, source_snapshot_text, raw_source_text
from .components import page_scroll
from .composition_page_navigator import CompositionPageNavigator, visible_fragments

KINDS = {'complete_question':'完整题','textbook_image':'教材图','knowledge_note':'知识卡','personal_text':'个人文字','lesson_activity':'教学环节','lesson_framework':'教案栏目','preparation_image':'教学图片','handout_image':'讲义原图与知识网','handout_knowledge':'讲义知识段'}
ROLES = {'shared_material':'公共条件','question':'题面','answer':'教师答案与解析','analysis':'教师解析',
    'rubric':'教师评分说明','teacher_note':'教师备注','material':'教材素材','knowledge':'知识内容','personal':'个人文字','student_task':'学生任务'}


def label(text):
    w=QLabel(text); w.setTextFormat(Qt.TextFormat.PlainText); w.setWordWrap(True); w.setMinimumWidth(0)
    return w


class MaterialLayoutError(ValueError):
    """A source-checked detached batch failed only its final visual layout."""
    def __init__(self, message, composition, assets, image_data):
        super().__init__(message)
        self.composition = deepcopy(composition)
        self.assets = deepcopy(list(assets))
        self.image_data = dict(image_data)


def readable_source(item, *, full=False):
    if item['original']['kind']=='lesson_activity':
        from .lesson_activity_dialogs import readable_snapshot
        return '原环节快照（含教师内容，供备课核对）\n\n'+readable_snapshot(item['original']['evidence']['activity_snapshot'])
    return source_snapshot_text(item) if full else source_summary(item)


class ResourcePicker(QDialog):
    def __init__(self, rows, title, row_label, parent, *, intro=None, visual_facade=None):
        super().__init__(parent); self.chosen=None; self.rows=rows; self.row_label=row_label
        self.setWindowTitle(title); self.resize(740,600)
        root=QVBoxLayout(self)
        if intro: root.addWidget(label(intro))
        self.search=QLineEdit(); self.search.setPlaceholderText('按标题、册名或知识内容查找')
        root.addWidget(self.search); self.list=QListWidget()
        self.detail=QPlainTextEdit(); self.detail.setReadOnly(True)
        self.page_preview=None
        if visual_facade is not None:
            from .textbook_asset_preview import TextbookAssetPreview
            self.resize(1000,720);self.setMinimumSize(360,550)
            self.visual_split=QSplitter();self.visual_split.addWidget(self.list)
            right=QWidget();layout=QVBoxLayout(right);layout.setContentsMargins(0,0,0,0)
            self.page_preview=TextbookAssetPreview(visual_facade,right)
            self.page_preview.bind_list(self.list)
            layout.addWidget(self.page_preview,3);layout.addWidget(self.detail,1)
            self.visual_split.addWidget(right);self.visual_split.setStretchFactor(0,1);self.visual_split.setStretchFactor(1,2)
            root.addWidget(self.visual_split,1)
        else:
            root.addWidget(self.list,1);root.addWidget(self.detail,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('选择并核对')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        root.addWidget(buttons); buttons.accepted.connect(self.choose); buttons.rejected.connect(self.reject)
        self.search.textChanged.connect(self.filter); self.list.currentItemChanged.connect(self.show_row); self.filter()

    def filter(self,*_):
        self.list.clear(); query=self.search.text().strip().casefold()
        for row in self.rows:
            if query and query not in json.dumps(row,ensure_ascii=False).casefold(): continue
            item=QListWidgetItem(self.row_label(row)); item.setData(Qt.ItemDataRole.UserRole,row); self.list.addItem(item)
        self.list.setCurrentRow(0)

    def show_row(self,item,*_):
        if item is None:self.detail.clear();return
        row=item.data(Qt.ItemDataRole.UserRole);lines=[]
        for key,title in (('title','标题'),('source_name','原书／来源'),('volume_title','册名'),('section_title','章节'),
                ('label','图表'),('summary','内容'),('classroom_check','课堂核对建议'),('limitation','适用范围与待核对处')):
            if row.get(key):lines.append(title+'：'+str(row[key]))
        pages=row.get('pdf_pages',[row['pdf_page']] if row.get('pdf_page') is not None else None)
        if pages:lines.append('PDF文件页：'+('、'.join(map(str,pages)) if isinstance(pages,list) else str(pages)))
        printed=row.get('printed_pages',row.get('printed_page'))
        if printed:lines.append('印刷页：'+('待核对' if printed=='unknown' else '、'.join(map(str,printed)) if isinstance(printed,list) else str(printed)))
        if row.get('page_count'):lines.append(f"全书 {row['page_count']} 页；选择后指定PDF页码。")
        lines.append('加入时重新核对来源版本；选用不会自动审核原书或笔记。')
        self.detail.setPlainText('\n\n'.join(lines))

    def choose(self):
        item=self.list.currentItem()
        if item:
            self.chosen=deepcopy(item.data(Qt.ItemDataRole.UserRole))
            if self.page_preview is not None:self.page_preview.release()
            self.accept()

    def reject(self):
        if self.page_preview is not None:self.page_preview.release()
        super().reject()

    def closeEvent(self,event):
        self.reject();event.accept()

    def resizeEvent(self,event):
        if hasattr(self,'visual_split'):
            self.visual_split.setOrientation(Qt.Orientation.Vertical if event.size().width()<800 else Qt.Orientation.Horizontal)
        super().resizeEvent(event)


class ResourceEditor(QDialog):
    """Edits are a cancellable work-copy transaction; the sealed source stays."""
    def __init__(self, composition, identity, parent=None, *, assets=(), image_data=None):
        from ..desktop_handout_working_tables import table_cell_model
        from .native_table_cell_editor import SourceTextEdit
        super().__init__(parent); self.value=deepcopy(composition); self.identity=identity; self.result_value=None
        self.item=next(r for r in self.value['resources'] if r['id']==identity)
        self.values={}; self.table_cells={}; self.current=None; self.loading=True
        self.assets=deepcopy(list(assets)); self.image_data=dict(image_data or {})
        self._models={}; self._table_editors={}; self.table_editor=None; self._image_locations={}
        for row in self.item['blocks']:
            if row['kind']!='text':continue
            model=table_cell_model(self.item,row['id'])
            if model is not None:
                self._models[row['id']]=model
                for occurrence in model['occurrences']:
                    self._image_locations[occurrence['image_block_id']]=(row['id'],occurrence['row'],occurrence['column'])
        self._initial_table_state=self._table_state(self.item)
        self.setWindowTitle('编辑作品中的素材副本'); self.resize(880,740); self.setMinimumSize(360,480)
        root=QVBoxLayout(self)
        root.addWidget(label('解锁后修改本作品的文字。表格可逐格编辑，原图保留在对应位置。'))
        self.locked=QCheckBox('锁定来源内容'); self.locked.setChecked(self.item['locked']); root.addWidget(self.locked)
        root.addWidget(label('导学问题／图题（显示在课页左上，也用于目录；可用“为什么”“怎样”等问题引导，正文锁定时也可修改）'))
        self.title=QLineEdit(self.item['title']); self.title.setMaxLength(2000)
        self.title.setPlaceholderText('例如：链结构怎样影响材料的性质与用途？')
        self.title.setAccessibleName('作品中的导学问题或图题'); root.addWidget(self.title)
        self.exposition=QComboBox();self.exposition.addItem('普通图文排版',None)
        visible=[b for b in self.item['blocks'] if b['role'] in {'personal','knowledge'}]
        if (self.item['original']['kind'] in {'personal_text','knowledge_note','handout_knowledge'} and visible
                and all(b['kind']=='text' and 'presentation' not in b for b in visible)):
            self.exposition.addItem('知识讲解与笔记（完整文字，正文24磅）','notes')
        if (self.item['original']['kind']=='personal_text' and 2<=len(self.item['blocks'])<=6
                and all(b['kind']=='text' and b['role']=='personal' for b in self.item['blocks'])):
            self.exposition.addItem('用箭头连接讲解步骤','process')
            self.exposition.addItem('沿竖向连接讲解步骤','process_vertical')
            if len(self.item['blocks'])<=3:self.exposition.addItem('按共同维度并列比较','comparison')
        self.exposition.setCurrentIndex(max(0,self.exposition.findData(self.item.get('exposition',{}).get('kind'))))
        root.addWidget(self.exposition)
        self.table_continuation=QCheckBox('内容增加时只为这项素材续页')
        self.table_continuation.setObjectName('NativeTableContinueResource')
        self.table_continuation.setChecked(True);self.table_continuation.setVisible(bool(self._models))
        self.table_continuation.setToolTip('应用单元格修改时，若现有页面放不下，只为当前素材续页。')
        root.addWidget(self.table_continuation)
        tabs=QTabWidget(); tabs.setMinimumSize(0,150); root.addWidget(tabs,1)
        edit=QWidget(); layout=QVBoxLayout(edit); self.blocks=QComboBox(); layout.addWidget(self.blocks)
        self.blocks.setObjectName('ResourceEditorBlocks')
        self.blocks.setMinimumWidth(0);self.blocks.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.blocks.setMinimumContentsLength(10)
        self.table_mode_note=label('');layout.addWidget(self.table_mode_note)
        self.table_mode_button=QPushButton('切换普通图文排版以编辑表格')
        self.table_mode_button.setObjectName('NativeTablePlainLayout');layout.addWidget(self.table_mode_button)
        self.table_mode_button.clicked.connect(lambda:self.exposition.setCurrentIndex(0))
        self.content_stack=QStackedWidget();self.content_stack.setMinimumSize(0,100);layout.addWidget(self.content_stack,1)
        self.body=SourceTextEdit();self.body.setMinimumSize(0,80);self.content_stack.addWidget(self.body)
        self.image_page=QWidget();self.image_layout=QVBoxLayout(self.image_page)
        self.image_description=label('');self.image_layout.addWidget(self.image_description)
        self.image_preview=None
        self.image_location_button=QPushButton('在表格中查看');self.image_layout.addWidget(self.image_location_button)
        self.image_location_button.clicked.connect(self.show_image_cell);self.content_stack.addWidget(self.image_page)
        tabs.addTab(edit,'当前作品内容')
        for index,row in enumerate(self.item['blocks'],1):
            kind='表格' if row['id'] in self._models else '图片' if row['kind']=='image' else '文字'
            self.blocks.addItem(f"{index} · {ROLES[row['role']]} · {kind}",row['id'])
        original_page=QWidget();source_layout=QVBoxLayout(original_page)
        original=QPlainTextEdit(); original.setReadOnly(True)
        original.setPlainText(readable_source(self.item,full=True));source_layout.addWidget(original,1)
        self.source_details_button=QPushButton('查看来源版本与记录细节');self.source_details_button.setCheckable(True)
        source_layout.addWidget(self.source_details_button)
        self.source_details=QPlainTextEdit();self.source_details.setReadOnly(True);self.source_details.setMaximumHeight(180)
        self.source_details.setPlainText(raw_source_text(self.item,full=True));self.source_details.hide();source_layout.addWidget(self.source_details)
        self.source_details_button.toggled.connect(self.source_details.setVisible)
        tabs.addTab(original_page,'原资源快照与来源')
        self.status=label(''); root.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.apply_button=self.buttons.button(QDialogButtonBox.StandardButton.Save);self.apply_button.setText('应用到作品')
        self.cancel_button=self.buttons.button(QDialogButtonBox.StandardButton.Cancel);self.cancel_button.setText('取消')
        self.apply_button.setObjectName('ResourceEditorApply');self.cancel_button.setObjectName('ResourceEditorCancel')
        self.buttons.accepted.connect(self.apply); self.buttons.rejected.connect(self.reject); root.addWidget(self.buttons)
        self.blocks.currentIndexChanged.connect(self.select_block); self.locked.toggled.connect(self.toggle_lock)
        self.exposition.currentIndexChanged.connect(self.exposition_changed)
        self.loading=False; self.select_block(); self.toggle_controls()

    @staticmethod
    def _cell_values(model):
        return [{key:deepcopy(cell[key]) for key in ('row','column','segments')} for cell in model['cells']]

    def _table_state(self, item):
        return (deepcopy(item.get('working_tables')), {
            row['id']:row['value'] for row in item['blocks'] if row['id'] in self._models})

    def store_block(self):
        if self.current and not self.item['locked']:
            row=next(r for r in self.item['blocks'] if r['id']==self.current)
            if row['kind']=='text' and self.current in self._table_editors:
                values=self._table_editors[self.current].cells_value()
                if values!=self._cell_values(self._models[self.current]):self.table_cells[self.current]=values
                else:self.table_cells.pop(self.current,None)
            elif row['kind']=='text':
                text=self.body.source_text()
                if text!=row['value']:self.values[self.current]=text
                else:self.values.pop(self.current,None)

    def select_block(self,*_):
        if self.loading:return
        self.store_block(); self.current=self.blocks.currentData()
        row=next(r for r in self.item['blocks'] if r['id']==self.current)
        self.table_editor=None
        if self.current in self._models:
            if self.current not in self._table_editors:
                from .native_table_cell_editor import NativeTableCellEditor
                editor=NativeTableCellEditor(self._models[self.current],self.assets,self.image_data,self)
                self._table_editors[self.current]=editor;self.content_stack.addWidget(editor)
            self.table_editor=self._table_editors[self.current]
            self.content_stack.setCurrentWidget(self.table_editor)
        elif row['kind']=='image':
            from .native_table_cell_editor import VerifiedImagePreview
            assets={a['asset_id']:a for a in self.assets}
            if self.image_preview is not None:
                self.image_layout.removeWidget(self.image_preview);self.image_preview.deleteLater()
            self.image_preview=VerifiedImagePreview(assets.get(row['value']),self.image_data.get(row['value']))
            self.image_layout.insertWidget(1,self.image_preview,1)
            position=self._image_locations.get(self.current)
            self.image_description.setText(f'原图 · 表格第{position[1]}行，第{position[2]}列' if position else '原图（只读）')
            self.image_location_button.setVisible(position is not None)
            self.content_stack.setCurrentWidget(self.image_page)
        else:
            self.body.set_source_text(self.values.get(self.current,row['value']));self.content_stack.setCurrentWidget(self.body)
        self.toggle_controls()

    def show_image_cell(self):
        position=self._image_locations.get(self.current)
        if position is None:return
        self.blocks.setCurrentIndex(self.blocks.findData(position[0]))
        self.table_editor.select_cell(position[1],position[2])

    def _has_table_changes(self):
        for identity,model in self._models.items():
            cells=(self._table_editors[identity].cells_value() if identity in self._table_editors else self._cell_values(model))
            if any(cell['segments']!=source['original_segments'] for cell,source in zip(cells,model['cells'])):return True
        return False

    def exposition_changed(self,*_):
        if self.loading:return
        self.store_block()
        if self.exposition.currentData() and self._has_table_changes():
            self.exposition.blockSignals(True);self.exposition.setCurrentIndex(0);self.exposition.blockSignals(False)
            self.toggle_controls()
            self.status.setText('已有单元格修改，请保留普通图文排版。还原这些文字后可切换讲解排版。')
            return
        self.toggle_controls()

    def toggle_controls(self):
        row=next(r for r in self.item['blocks'] if r['id']==self.current)
        self.title.setReadOnly(False); self.body.setReadOnly(self.item['locked'] or row['kind']=='image')
        blocked=self.table_editor is not None and bool(self.exposition.currentData())
        self.table_mode_note.setVisible(blocked);self.table_mode_button.setVisible(blocked)
        self.table_mode_note.setText('当前采用知识讲解排版。启用单元格编辑须先切换为普通图文排版。' if blocked else '')
        for editor in self._table_editors.values():editor.set_read_only(self.item['locked'] or bool(self.exposition.currentData()))
        self.status.setText('来源正文已锁定；作品标题可单独修改，原文与来源标题保持。' if self.item['locked'] else
            '已解锁作品副本。修改完成后应用到作品，或取消本次修改。')

    def _candidate(self, *, locked=None):
        # Layout and cell text are validated as one detached transaction. The
        # temporary absence of exposition is never assigned to the dialog.
        candidate=deepcopy(self.value)
        current=next(r for r in candidate['resources'] if r['id']==self.identity)
        current.pop('exposition',None)
        candidate=edit_resource(candidate,self.identity,title=self.title.text(),values=self.values,
            table_cells=self.table_cells,**({'locked':locked} if locked is not None else {}))
        current=next(r for r in candidate['resources'] if r['id']==self.identity)
        kind=self.exposition.currentData()
        if kind:
            from ..desktop_composition_exposition import SCHEMA
            current['exposition']={'schema':SCHEMA,'kind':kind}
        return validate_composition(candidate)

    def _adopt_work_copy(self, candidate):
        from ..desktop_handout_working_tables import table_cell_model
        self.value=candidate;self.item=next(r for r in self.value['resources'] if r['id']==self.identity)
        self.values={};self.table_cells={}
        for identity in self._models:self._models[identity]=table_cell_model(self.item,identity)

    def toggle_lock(self,value):
        if self.loading:return
        self.store_block()
        try:
            if value and not self.item['locked']:self._adopt_work_copy(self._candidate(locked=True))
            else:
                self.value=edit_resource(self.value,self.identity,locked=value)
                self.item=next(r for r in self.value['resources'] if r['id']==self.identity)
            self.toggle_controls()
        except ValueError as exc:
            self.locked.blockSignals(True);self.locked.setChecked(self.item['locked']);self.locked.blockSignals(False)
            self.toggle_controls();self.status.setText(str(exc))

    def apply(self):
        self.store_block()
        try:
            candidate=self._candidate()
            current=next(r for r in candidate['resources'] if r['id']==self.identity)
            if self._table_state(current)!=self._initial_table_state:
                from ..desktop_composition_resource_layout import refresh_resource_layout_if_needed, resource_layout_errors
                if self.table_continuation.isChecked():
                    candidate=refresh_resource_layout_if_needed(candidate,self.identity,self.assets,self.image_data)
                elif resource_layout_errors(candidate,self.identity,self.assets,self.image_data):
                    self.status.setText('现有页面放不下修改后的内容。请勾选“内容增加时只为这项素材续页”后应用。')
                    return
            self.value=candidate
            self.result_value=self.value; self.accept()
        except ValueError as exc:self.status.setText(str(exc))


class ResourceFrame(QGraphicsRectItem):
    def __init__(self,panel,fragment,page_origin):
        frame=fragment['frame']; super().__init__(0,0,frame['width'],frame['height'])
        self.panel,self.fragment,self.page_origin=panel,fragment,page_origin
        self.setPos(frame['x'],page_origin+frame['y'])
        flags=QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        if not panel.student_flow_active():flags|=QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        self.setFlags(flags)
        self.setToolTip('学生版按素材顺序单独排满。双击阅读／编辑完整内容。' if panel.student_flow_active() else
                        '拖动改变位置；阅读顺序由左侧列表决定。双击编辑完整内容。')

    def paint(self,painter,option,widget=None):
        painter.save(); painter.setClipRect(self.rect())
        frame=self.fragment['frame']; painter.translate(-frame['x'],-frame['y'])
        paint_elements(painter,self.fragment['elements']); painter.restore()
        painter.setPen(QColor('#387863') if self.isSelected() else QColor('#d4ded8'))
        painter.drawRect(self.rect())

    def mouseReleaseEvent(self,event):
        super().mouseReleaseEvent(event)
        if not self.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable:return
        panel=self.panel;identity=self.fragment['resource_id'];index=self.fragment['frame_index']
        x,y=self.pos().x(),self.pos().y()-self.page_origin
        # The commit rebuilds the scene; defer until its mouse dispatch ends.
        QTimer.singleShot(0,lambda:panel.move_frame(identity,index,x,y))

    def mouseDoubleClickEvent(self,event):
        self.panel.selected=self.fragment['resource_id'];self.panel.frame_index=self.fragment['frame_index']
        self.panel.edit_selected(); event.accept()


class LessonCompositionPanel(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.loading=False; self.selected=None; self.frame_index=0
        self.payload=None; self.layout_result=None; self._image_cache={};self._fitted=False;self._view_page_id=None
        self._insertion_epoch=0;self._material_selection_epoch=0
        self._transaction_scopes=set()
        self._handout_workspace_dialog=None;self._handout_materials_source=None
        self._handout_scope_event=None;self._handout_scope_closed=False
        # These signals retain an A -> B -> A change while a mixed selection or
        # its worker is pending. The worker itself reads only the Event.
        page=getattr(owner,'page',None)
        for name in ('topic','audience','objective','materials','learning_detail',
                     'template_detail','strategy_detail','output_kind','route',
                     'lesson_count','lesson_minutes'):
            widget=getattr(page,name,None)
            signal=next((getattr(widget,key) for key in
                ('textChanged','currentIndexChanged','valueChanged') if hasattr(widget,key)),None)
            if signal is not None:signal.connect(self._invalidate_handout_workspace)
        for source,name in ((page,'preparation_target_changed'),
                            (getattr(page,'image_assets_widget',None),'assets_changed'),
                            (getattr(page,'image_assets_widget',None),'mode_changed')):
            signal=getattr(source,name,None)
            if signal is not None:signal.connect(self._invalidate_handout_workspace)
        for name in ('finished','destroyed'):
            signal=getattr(owner,name,None)
            if signal is not None:signal.connect(self._close_handout_workspace)
        finished=getattr(owner,'finished',None)
        if finished is not None:finished.connect(self.invalidate_material_insertion)
        root=QVBoxLayout(self)
        root.addWidget(label('在同一作品中安排教学环节、完整题、教材图、知识卡与个人文字。拖动外框改变位置，左侧调整阅读顺序；所选环节与素材一起用于PDF、课件和学习单。'))
        purpose=QHBoxLayout();root.addLayout(purpose)
        self.print_button=QPushButton('打印学习单 · A4');self.print_button.clicked.connect(lambda:self.change_format('A4'))
        self.projection_button=QPushButton('课堂投影 · 16:9');self.projection_button.clicked.connect(lambda:self.change_format('16:9'))
        purpose.addWidget(self.print_button);purpose.addWidget(self.projection_button)
        self.pair_button=QPushButton('组成图文讲解页…');self.pair_button.setAccessibleName('组成图文讲解页')
        self.pair_button.setToolTip('选本课两项素材，预览原图、问题和讲解同页的位置；一次应用，可撤销。')
        self.pair_button.clicked.connect(self.pair_resources);purpose.addWidget(self.pair_button)
        purpose.addWidget(label('选择用途后完整续页；投影续页带素材标题，正文24磅。旧编排可重新排满，可撤销。'),1)
        student_layout=QHBoxLayout();root.addLayout(student_layout)
        self.student_arrangement=QComboBox()
        self.student_arrangement.addItems(['沿用教师位置','学生版单独排满 · 一栏','学生版单独排满 · 两栏'])
        self.student_arrangement.setAccessibleName('学生版页面编排')
        self.student_arrangement.setToolTip('单独排满仅按学生可见内容分页，保留教师位置。修改素材后自动更新，选择随草稿保存，可撤销。')
        self.student_arrangement.currentIndexChanged.connect(self.change_student_arrangement)
        student_layout.addWidget(label('学生版编排'));student_layout.addWidget(self.student_arrangement)
        student_layout.addWidget(label('单独排满后按素材顺序自动续页；教师位置仍可在教师视图调整。'),1)
        top=QHBoxLayout();root.addLayout(top)
        self.format=QComboBox();self.format.addItems(FORMATS);self.format.currentTextChanged.connect(self.change_format)
        self.student=QCheckBox('学生视图（隐藏教师内容及其空页）');self.student.setChecked(True);self.student.toggled.connect(self.audience_changed)
        top.addWidget(label('画幅'));top.addWidget(self.format);top.addWidget(self.student);top.addStretch(1)
        self.fit_button=QPushButton('适应页面');self.fit_button.clicked.connect(self.fit_view);top.addWidget(self.fit_button)
        reload_button=QPushButton('重新读取本地图');reload_button.clicked.connect(self.reload_images);top.addWidget(reload_button)
        for name,redo in (('撤销',False),('重做',True)):
            button=QPushButton(name);button.clicked.connect(lambda _=False,r=redo:self.owner.travel(r));top.addWidget(button)
        self.page_navigation=CompositionPageNavigator(self)
        self.page_navigation.pageRequested.connect(self.navigate_to_page)
        self.page_navigation.focus.toggled.connect(self.focus_pages)
        root.addWidget(self.page_navigation)
        split=QSplitter();root.addWidget(split,1)
        left=QWidget();a=QVBoxLayout(left);a.setContentsMargins(0,0,0,0)
        self.sources_panel=left
        self.source_tabs=QTabWidget();self.source_tabs.setMinimumHeight(160);a.addWidget(self.source_tabs,1)
        self.list=QListWidget();self.list.setAccessibleName('作品素材阅读顺序');self.list.currentItemChanged.connect(self.select_resource)
        self.source_tabs.addTab(self.list,'素材顺序');self.source_tabs.addTab(self.page_navigation.tree,'页面目录')
        insertion_row=QHBoxLayout();a.addLayout(insertion_row)
        insertion_row.addWidget(label('加入位置'))
        self.insertion_position=QComboBox()
        for mode,title in INSERTION_LABELS:self.insertion_position.addItem(title,mode)
        self.insertion_position.setAccessibleName('新素材加入位置')
        self.insertion_position.setToolTip('选材前确定位置；本批全部素材及续页一起加入，保留已有页面位置。')
        self.insertion_position.currentIndexChanged.connect(self.insertion_position_changed)
        insertion_row.addWidget(self.insertion_position,1)
        self.insertion_anchor=label('目标：请先选择已有素材');a.addWidget(self.insertion_anchor)
        # Keep source selection and its insertion target visible when the real
        # lesson dialog is short; the action rows must scroll, never compress.
        actions=QWidget();actions_layout=QVBoxLayout(actions);actions_layout.setContentsMargins(0,0,0,0)
        grid=QGridLayout();actions_layout.addLayout(grid)
        for i,(name,action) in enumerate((('教学环节…',self.add_activities),('完整题…',self.add_questions),('教材原页／切片…',self.add_textbook),('课外／本机配图…',self.add_images),
                ('知识卡…',self.add_note),('通读讲义并选材…',self.add_handout_materials),('讲义知识…',self.add_handout_knowledge),('讲义图…',self.add_handout_images),('个人文字…',self.add_personal),('分步讲解…',self.add_step_flow),('上移',lambda:self.reorder(-1)),
                ('下移',lambda:self.reorder(1)),('教师一栏排满并续页',lambda:self.arrange(1)),('教师两栏排满并续页',lambda:self.arrange(2)))):
            button=QPushButton(name);button.setObjectName('QuietButton');button.clicked.connect(action);grid.addWidget(button,i//2,i%2)
            if name=='通读讲义并选材…':
                self.handout_materials_button=button;button.setAccessibleName('通读讲义并选材')
                button.setToolTip('通读一份讲义，按授课顺序选择完整知识、知识网、原图与完整题，一批加入当前指定位置。')
            if name=='分步讲解…':
                self.step_flow_button=button;button.setAccessibleName('加入完整分步讲解流程')
                button.setToolTip('填写导学问题与2—6步完整讲解，预览横向或纵向流程，课堂可逐步点击显示。')
            if '排满并续页' in name:
                button.setToolTip('标题随正文开头或首图移动，能同页放下的短图注随图移动。长内容完整续页；可撤销。')
        remove=QPushButton('移除所选素材');remove.clicked.connect(self.remove_selected);actions_layout.addWidget(remove)
        self.source_actions=page_scroll(actions);self.source_actions.setAccessibleName('加入与编排素材操作')
        self.source_actions.setMinimumWidth(actions.minimumSizeHint().width()+self.source_actions.verticalScrollBar().sizeHint().width())
        self.source_actions.setMinimumHeight(2*remove.sizeHint().height()+grid.verticalSpacing())
        a.addWidget(self.source_actions,1)
        split.addWidget(left)
        self.scene=QGraphicsScene(self);self.scene.selectionChanged.connect(self.scene_selection)
        self.view=QGraphicsView(self.scene);self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setBackgroundBrush(QColor('#e8eeeb'));self.view.setAccessibleName('完整素材页面画布，位置与阅读顺序独立');split.addWidget(self.view)
        inspector=QWidget();f=QFormLayout(inspector);f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.details=label('请选择素材');f.addRow(self.details)
        self.rename_button=QPushButton('修改导学问题／图题…')
        self.rename_button.setToolTip('改写显示在课页左上的导学问题或图题，可用“为什么”“怎样”等提问；题干、答案与来源快照保持锁定。')
        self.rename_button.clicked.connect(self.rename_selected);f.addRow(self.rename_button)
        self.click_reveal=QCheckBox('课堂PPT中点击后显示')
        self.click_reveal.setToolTip('把答案、分析或讲解步骤放在题目旁；放映时按素材顺序点击出现。学习单与静态学生画布不显示这段内容。')
        self.click_reveal.toggled.connect(self.change_click_reveal);f.addRow(self.click_reveal)
        self.question_answer_button=QPushButton('题面同页／点击原答案…')
        self.question_answer_button.setAccessibleName('设置完整题面同页与明确选择原答案点击显示')
        self.question_answer_button.setToolTip('核对确切原答案区块，预览课堂点击前后；完整题面与原图保持，静态学生页隐藏答案。')
        self.question_answer_button.clicked.connect(self.edit_question_answer_reveal);f.addRow(self.question_answer_button)
        self.knowledge_origin=label('');f.addRow(self.knowledge_origin)
        self.knowledge_card_button=QPushButton('查看选图所用知识卡')
        self.knowledge_card_button.clicked.connect(self.open_selected_knowledge_card);f.addRow(self.knowledge_card_button)
        self.edit_button=QPushButton('阅读／编辑完整素材…');self.edit_button.clicked.connect(self.edit_selected);f.addRow(self.edit_button)
        self.image_display_button=QPushButton('图片显示与分页…')
        self.image_display_button.setAccessibleName('设置所选素材的原图显示大小与分页')
        self.image_display_button.setToolTip('按原图区块设置宽、高上限，预览完整所选素材；一次应用，可撤销。')
        self.image_display_button.clicked.connect(self.edit_image_display);f.addRow(self.image_display_button)
        self.knowledge_read_button=QPushButton('对照教材原页…')
        self.knowledge_read_button.clicked.connect(self.read_selected_textbook_original);f.addRow(self.knowledge_read_button)
        self.knowledge_textbook_button=QPushButton('从这张知识卡选教材图…')
        self.knowledge_textbook_button.clicked.connect(self.add_knowledge_textbook);f.addRow(self.knowledge_textbook_button)
        self.knowledge_question_button=QPushButton('从这张知识卡选例题…')
        self.knowledge_question_button.clicked.connect(self.add_knowledge_questions);f.addRow(self.knowledge_question_button)
        self.handout_source_button=QPushButton('从这份讲义继续选图…')
        self.handout_source_button.clicked.connect(self.add_handout_from_selected);f.addRow(self.handout_source_button)
        self.handout_knowledge_button=QPushButton('从这份讲义选知识段…')
        self.handout_knowledge_button.clicked.connect(self.add_handout_knowledge_from_selected);f.addRow(self.handout_knowledge_button)
        self.handout_question_button=QPushButton('从这份讲义选例题…')
        self.handout_question_button.clicked.connect(self.add_handout_questions);f.addRow(self.handout_question_button)
        self.knowledge_links_button=QPushButton('管理关联例题…')
        self.knowledge_links_button.setAccessibleName('为所选原知识管理可复用的具体例题关联')
        self.knowledge_links_button.clicked.connect(self.manage_knowledge_question_links);f.addRow(self.knowledge_links_button)
        self.question_knowledge_links_button=QPushButton('查看／维护关联知识…')
        self.question_knowledge_links_button.setAccessibleName('从所选完整题反查并维护关联的具体知识')
        self.question_knowledge_links_button.clicked.connect(self.manage_knowledge_question_links);f.addRow(self.question_knowledge_links_button)
        self.open_activity_button=QPushButton('回到关联教学环节');self.open_activity_button.clicked.connect(self.open_activity_source);f.addRow(self.open_activity_button)
        self.update_activity_button=QPushButton('核对／更新环节关联…');self.update_activity_button.clicked.connect(self.update_activity_source);f.addRow(self.update_activity_button)
        self.frames=QComboBox();self.frames.currentIndexChanged.connect(self.select_frame);f.addRow('首页／续页',self.frames)
        self.page_picker=QComboBox();f.addRow('放在第几页',self.page_picker)
        self.geometry={}
        for key,name in (('x','横向位置 mm'),('y','纵向位置 mm'),('width','外框宽度 mm'),('height','外框高度 mm')):
            spin=QDoubleSpinBox();spin.setDecimals(2);spin.setRange(0 if key in ('x','y') else .1,420)
            self.geometry[key]=spin;f.addRow(name,spin)
        self.geometry_apply=QPushButton('应用位置与尺寸');self.geometry_apply.clicked.connect(self.apply_geometry);f.addRow(self.geometry_apply)
        self.original=QPlainTextEdit();self.original.setReadOnly(True);self.original.setMaximumHeight(240);f.addRow('来源与内容',self.original)
        self.source_details_button=QPushButton('查看来源版本与记录细节');self.source_details_button.setCheckable(True)
        f.addRow(self.source_details_button)
        self.source_details=QPlainTextEdit();self.source_details.setReadOnly(True);self.source_details.setMaximumHeight(180)
        self.source_details.hide();f.addRow(self.source_details)
        self.source_details_button.toggled.connect(self.source_details.setVisible)
        self.inspector_panel=page_scroll(inspector)
        split.addWidget(self.inspector_panel);split.setSizes([220,620,250]);split.setChildrenCollapsible(False)
        self.status=label('加入素材后开始排版。旧草稿保持原格式；第一次加入才启用画布。');root.addWidget(self.status)
        self._resize_fit_timer=QTimer(self);self._resize_fit_timer.setSingleShot(True)
        self._resize_fit_timer.timeout.connect(self.fit_view)
        from .composition_image_batch_entry import CompositionImageBatchEntry
        self._image_batch_entry=CompositionImageBatchEntry(self)

    def composition(self):
        value=self.owner.history.value.get('composition')
        return deepcopy(value) if value is not None else new_composition()

    def insertion_position_changed(self,*_):
        self._insertion_epoch+=1

    def invalidate_material_insertion(self,*_):
        self._material_selection_epoch+=1
        self._invalidate_handout_workspace()
        for scope in tuple(self._transaction_scopes):scope.invalidate()

    def _invalidate_handout_workspace(self,*_):
        event=getattr(self,'_handout_scope_event',None)
        if event is not None:event.set()
        dialog=getattr(self,'_handout_workspace_dialog',None)
        if dialog is not None:
            try:dialog.reject()
            except RuntimeError:pass

    def _close_handout_workspace(self,*_):
        self._handout_scope_closed=True
        self._invalidate_handout_workspace()

    def freeze_insertion(self):
        """Capture the source action's destination before its picker opens."""
        self.invalidate_material_insertion()
        return {'mode':self.insertion_position.currentData(),
                'anchor_resource_id':self.selected,
                'epoch':self._insertion_epoch,'request_epoch':self._material_selection_epoch}

    def check_insertion(self,insertion):
        if (getattr(self.owner,'_stopping',False) or getattr(self.owner,'_close_when_finished',False)
                or getattr(getattr(self.owner,'tasks',None),'is_shutting_down',False)):
            raise ValueError('选材窗口已关闭或停止，本批未加入。')
        if insertion is not None and insertion.get('epoch',self._insertion_epoch)!=self._insertion_epoch:
            raise ValueError('加入位置在选材期间变化，本批未加入；请重新选择素材。')
        if insertion is not None and insertion.get('request_epoch',self._material_selection_epoch)!=self._material_selection_epoch:
            raise ValueError('本次选材已结束或被后续选材替代，本批未加入；请重新选择。')

    def student_flow_active(self):
        return self.student.isChecked() and 'student_arrangement' in self.composition()

    def resource_display_frames(self,identity):
        if self.student_flow_active():
            return [deepcopy(f['frame']) for f in (self.layout_result or {}).get('fragments',[])
                    if f['resource_id']==identity]
        placement=self.composition()['placements'].get(identity)
        return resource_frames(placement) if placement is not None else []

    def change_student_arrangement(self,index):
        if self.loading:return
        try:
            value=set_student_arrangement(self.composition(),index or None)
            result=layout_composition(value,self.payload.get('image_assets',[]),self.image_data(),student=True)
            if result['errors']:raise ValueError('\n'.join(result['errors']))
            self._view_page_id=None;self.frame_index=0;self.commit(value)
            self.student.setChecked(True);self.fit_view()
        except ValueError as exc:
            self.refresh();self.status.setText(str(exc))

    def set_payload(self,payload):
        for scope in tuple(self._transaction_scopes):scope.observe()
        self.payload=deepcopy(payload);self.refresh()

    def showEvent(self,event):
        super().showEvent(event)
        if not self._fitted:QTimer.singleShot(0,self.fit_view)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self._fitted and hasattr(self,'_resize_fit_timer'):self._resize_fit_timer.start(0)

    def reload_images(self):
        self._image_cache.clear();self.refresh()

    def image_data(self):
        used={b['value'] for r in self.composition()['resources'] for b in r['blocks'] if b['kind']=='image'}
        assets={a['asset_id']:a for a in self.payload.get('image_assets',[])};result={}
        for identity in used:
            if identity not in assets:raise ValueError('本地图片已从备课中移除，请重新关联完整素材。')
            asset=assets[identity];cache=self._image_cache.get(identity)
            if cache is None or cache[0]!=asset:
                cache=(deepcopy(asset),self.owner.facade.preparation_image_bytes(asset));self._image_cache[identity]=cache
            result[identity]=cache[1]
        # No unrelated image caches survive a changed workpiece.
        self._image_cache={k:v for k,v in self._image_cache.items() if k in used}
        return result

    def refresh(self,*_):
        if self.payload is None:return
        self.loading=True;value=self.composition();self.format.setCurrentText(value['format']);self.list.clear();self.scene.clear()
        self.pair_button.setEnabled(len(value['resources'])>=2)
        self.student_arrangement.setCurrentIndex(value.get('student_arrangement',{}).get('columns',0))
        for row in value['resources']:
            suffix=self.activity_label(row)
            item=QListWidgetItem(KINDS[row['original']['kind']]+' · '+row['title']+suffix);item.setData(Qt.ItemDataRole.UserRole,row['id']);self.list.addItem(item)
            if row['id']==self.selected:self.list.setCurrentItem(item)
        try:
            result=layout_composition(value,self.payload.get('image_assets',[]),self.image_data(),student=self.student.isChecked())
            self.layout_result=result;w,h=FORMATS[value['format']];origins={key:i*(h+12) for i,key in enumerate(result['pages'])}
            for key,y in origins.items():
                paper=self.scene.addRect(0,y,w,h);paper.setBrush(QColor('white'));paper.setPen(QColor('#bdc9c2'));paper.setZValue(-1)
            for fragment in result['fragments']:
                item=ResourceFrame(self,fragment,origins[fragment['frame']['page_id']]);self.scene.addItem(item)
                if fragment['resource_id']==self.selected and fragment['frame_index']==self.frame_index:item.setSelected(True)
            self.scene.setSceneRect(-5,-5,w+10,len(result['pages'])*(h+12))
            audience = ('学生版' if self.student.isChecked() else '教师版')+f" {len(result['pages'])}页"
            omitted = len(result['omitted_teacher_page_ids'])
            page_note = (f"；学生版独立{value['student_arrangement']['columns']}栏排满，教师编排{len(value['pages'])}页"
                         if result['independent_student_arrangement'] else
                         f"；已略过{omitted}页仅教师内容，教师编排{len(value['pages'])}页" if omitted else '')
            self.status.setText('\n'.join(result['errors']) if result['errors'] else
                f"{len(value['resources'])}项素材 · {audience}{page_note} · 完整内容已放入外框。PDF沿用画布排版；可编辑PPTX在Office中仍须核对。")
            stale=[r['title'] for r in value['resources'] if self.activity_label(r) in {' · 待更新关联',' · 原环节已移除'}]
            if stale:self.status.setText(self.status.text()+'\n环节关联待核对：'+'、'.join(stale)+'。更新关联或保留独立副本后再生成。')
        except ValueError as exc:self.layout_result=None;self.status.setText(str(exc))
        self.page_navigation.set_layout(self.layout_result,value['resources'],KINDS,student=self.student.isChecked())
        self._view_page_id=self.visible_page()
        self.page_navigation.set_current(self._view_page_id,self.selected,self.frame_index)
        self.loading=False;self.refresh_inspector()

    def visible_page(self):
        if self.layout_result is None:return None
        pages=list(self.layout_result['pages'])
        fragments=[f for f in visible_fragments(self.layout_result) if f['resource_id']==self.selected]
        preferred=next((f for f in fragments if f['frame_index']==self.frame_index),None)
        if preferred is not None:return preferred['frame']['page_id']
        if fragments:return fragments[0]['frame']['page_id']
        if self._view_page_id in pages:return self._view_page_id
        return pages[0] if pages else None

    def audience_changed(self,*_):
        self.refresh();self.fit_view()

    def fit_view(self,*_,page_id=None):
        if self.layout_result:
            value=self.composition();w,h=FORMATS[value['format']]
            pages=list(self.layout_result['pages'])
            if not pages:return
            page=page_id if page_id in pages else self.visible_page()
            self._view_page_id=page
            y=pages.index(page)*(h+12)
            from PySide6.QtCore import QRectF
            self.view.fitInView(QRectF(-5,y-5,w+10,h+10),Qt.AspectRatioMode.KeepAspectRatio)
            self.page_navigation.set_current(page,self.selected,self.frame_index)
            self._fitted=True

    def focus_pages(self,focused):
        self.sources_panel.setVisible(not focused);self.inspector_panel.setVisible(not focused)
        self.page_navigation.focus.setText('返回编排' if focused else '专注页面')
        QTimer.singleShot(0,self.fit_view)

    def navigate_to_page(self,page_id,resource_id=None,frame_index=None):
        if self.loading or self.layout_result is None or page_id not in self.layout_result['pages']:return
        fragments=[f for f in visible_fragments(self.layout_result) if f['frame']['page_id']==page_id]
        if resource_id is not None:
            chosen=next((f for f in fragments if f['resource_id']==resource_id and f['frame_index']==frame_index),None)
            if chosen is None:return
        else:
            chosen=next((f for f in fragments if f['resource_id']==self.selected),fragments[0] if fragments else None)
        self.invalidate_material_insertion()
        self.selected=chosen['resource_id'] if chosen is not None else None
        self.frame_index=chosen['frame_index'] if chosen is not None else 0
        self._view_page_id=page_id
        self.loading=True
        self.list.setCurrentRow(next((i for i in range(self.list.count())
            if self.list.item(i).data(Qt.ItemDataRole.UserRole)==self.selected),-1))
        for item in self.scene.items():
            if isinstance(item,ResourceFrame):
                item.setSelected(chosen is not None and item.fragment['resource_id']==self.selected
                    and item.fragment['frame_index']==self.frame_index)
        self.loading=False;self.refresh_inspector();self.fit_view(page_id=page_id)

    def current_frame(self):
        frames=self.resource_display_frames(self.selected)
        return frames[min(self.frame_index,len(frames)-1)] if frames else {}

    def select_resource(self,item,*_):
        if self.loading:return
        self.invalidate_material_insertion()
        self.selected=item.data(Qt.ItemDataRole.UserRole) if item else None;self.frame_index=0;self._view_page_id=None;self.refresh();self.fit_view()

    def scene_selection(self):
        if self.loading:return
        items=[i for i in self.scene.selectedItems() if isinstance(i,ResourceFrame)]
        if items:
            self.invalidate_material_insertion()
            self.selected=items[0].fragment['resource_id'];self.frame_index=items[0].fragment['frame_index'];self.refresh_inspector()
            self._view_page_id=items[0].fragment['frame']['page_id']
            self.page_navigation.set_current(self._view_page_id,self.selected,self.frame_index)
            self.loading=True
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole)==self.selected:self.list.setCurrentRow(i)
            self.loading=False

    def refresh_inspector(self):
        self.loading=True;value=self.composition();item=next((r for r in value['resources'] if r['id']==self.selected),None)
        self.insertion_anchor.setText('目标：'+str(next((i+1 for i,r in enumerate(value['resources']) if r['id']==self.selected),''))+
            ' · '+item['title'] if item is not None else '目标：请先选择已有素材')
        for index in (1,2):self.insertion_position.model().item(index).setEnabled(item is not None)
        if item is None:self.insertion_position.setCurrentIndex(0)
        self.frames.clear();self.page_picker.clear();self.edit_button.setEnabled(item is not None)
        self.rename_button.setEnabled(item is not None)
        self.image_display_button.setVisible(item is not None and any(b['kind']=='image' for b in item['blocks']))
        self.image_display_button.setEnabled(item is not None)
        complete=item is not None and item['original']['kind']=='complete_question'
        self.question_answer_button.setVisible(complete)
        self.question_answer_button.setEnabled(complete)
        self.click_reveal.setVisible(not complete)
        self.click_reveal.setEnabled(item is not None and not complete and value['format']=='16:9'
                                     and any(b['role'] not in TEACHER_ROLES and b['value'] for b in item['blocks']))
        self.click_reveal.setChecked(self.selected in value.get('click_reveals', []))
        automatic=self.student_flow_active()
        for control in [self.page_picker,self.geometry_apply,*self.geometry.values()]:control.setEnabled(item is not None and not automatic)
        if item:
            frames=self.resource_display_frames(item['id']);self.frame_index=min(self.frame_index,len(frames)-1) if frames else 0
            self.details.setText(item['title']+' · '+('来源已锁定' if item['locked'] else '作品副本可编辑')+self.activity_label(item))
            if automatic:self.details.setText(self.details.text()+(' · 学生版自动排满' if frames else ' · 仅教师版可见'))
            for i in range(len(frames)):self.frames.addItem('首页' if i==0 else f'续页{i}',i)
            self.frames.setCurrentIndex(self.frame_index)
            pages=list(self.layout_result['pages']) if automatic and self.layout_result else value['pages']
            for i,key in enumerate(pages):self.page_picker.addItem(f"{'学生编排' if automatic else '教师编排'}第{i+1}页",key)
            if frames:
                frame=frames[self.frame_index];self.page_picker.setCurrentIndex(self.page_picker.findData(frame['page_id']))
                for k,control in self.geometry.items():control.setValue(frame[k])
            self.original.setPlainText(readable_source(item))
            self.source_details.setPlainText(raw_source_text(item))
        else:self.details.setText('请选择素材');self.original.clear();self.source_details.clear()
        self.source_details_button.setEnabled(item is not None)
        choice=item['original'].get('selected_from_knowledge') if item is not None else None
        self.knowledge_origin.setVisible(choice is not None)
        self.knowledge_origin.setText('选图来源知识卡：'+choice['title'] if choice is not None else '')
        self.knowledge_card_button.setVisible(choice is not None)
        self.knowledge_card_button.setEnabled(choice is not None and any(
            r['id']==choice['resource_id'] and r['original']['kind']=='knowledge_note'
            and r['original_sha256']==choice['original_sha256'] for r in value['resources']))
        self.knowledge_textbook_button.setVisible(item is not None and item['original']['kind']=='knowledge_note')
        self.knowledge_read_button.setVisible(item is not None and item['original']['kind'] in {'knowledge_note','textbook_image'})
        self.knowledge_question_button.setVisible(item is not None and item['original']['kind']=='knowledge_note')
        self.knowledge_links_button.setVisible(item is not None and item['original']['kind'] in {'knowledge_note','handout_knowledge'})
        self.question_knowledge_links_button.setVisible(item is not None and item['original']['kind']=='complete_question')
        self.handout_source_button.setVisible(item is not None and item['original']['kind'] in {'handout_image','handout_knowledge'})
        self.handout_knowledge_button.setVisible(item is not None and item['original']['kind'] in {'handout_image','handout_knowledge'})
        self.handout_question_button.setVisible(item is not None and item['original']['kind'] in {'handout_image','handout_knowledge'})
        linked=item is not None and item['original']['kind']=='lesson_activity' and item['activity_link']['mode']=='linked'
        self.open_activity_button.setVisible(linked);self.update_activity_button.setVisible(linked)
        self.open_activity_button.setEnabled(linked and item['activity_link']['design_id']==self.owner.history.value['id']
            and any(n['id']==item['activity_link']['node_id'] for n in self.owner.history.value['nodes']))
        self.loading=False

    def activity_label(self,item):
        if item['original']['kind']!='lesson_activity':return ''
        from ..desktop_composition_activities import activity_status
        try:state=activity_status(self.owner.history.value,item,self.payload.get('image_assets',[]))
        except ValueError:return ' · 待更新关联'
        return {'current':' · 环节已关联','changed':' · 待更新关联','missing':' · 原环节已移除','snapshot':' · 独立副本'}[state]

    def open_selected_knowledge_card(self):
        value=self.composition();item=next((r for r in value['resources'] if r['id']==self.selected),None)
        choice=item['original'].get('selected_from_knowledge') if item is not None else None
        target=next((r for r in value['resources'] if choice is not None and r['id']==choice['resource_id']
                     and r['original']['kind']=='knowledge_note' and r['original_sha256']==choice['original_sha256']),None)
        if target is None:
            self.status.setText('选图所用知识卡已不在本作品中；选图来源快照仍保留。');return
        self.invalidate_material_insertion()
        self.selected=target['id'];self.frame_index=0;self._view_page_id=None;self.refresh();self.fit_view()

    def select_frame(self,index):
        if not self.loading and index>=0:
            self.invalidate_material_insertion()
            self.frame_index=index;self._view_page_id=None;self.refresh();self.fit_view()

    def commit(self,value,expected=None,*,receipt=None):
        if expected is not None and digest(self.owner.history.value)!=expected:raise ValueError('作品在处理期间变化，请重新选择。')
        self._check_question_pages(value)
        plan=enable_composition(self.owner.history.value);plan['composition']=value
        self.owner.history.put(plan)
        if receipt is not None:
            receipt(deepcopy(self.owner.history.value),deepcopy(self.owner.history.back),deepcopy(self.owner.history.forward))
        self.owner.sync_page()

    def _check_question_pages(self,value):
        """Manual moves and work-copy edits must retain all three full scopes."""
        from ..desktop_question_answer_reveal import question_answer_reveal_errors
        items=[r for r in value['resources'] if 'question_answer_reveal' in r]
        if value['format']!='16:9' or not items:return
        assets=self.owner.page.image_assets_widget.assets();by_id={a['asset_id']:a for a in assets}
        used={b['value'] for r in items for b in r['blocks'] if b['kind']=='image'}
        if not used<=set(by_id):raise ValueError('完整题的原图已移除，请重新关联后设置。')
        data={identity:self.owner.facade.preparation_image_bytes(by_id[identity]) for identity in used}
        for item in items:
            errors=question_answer_reveal_errors(value,item['id'],assets,data)
            if errors:raise ValueError('\n'.join(errors))

    def move_frame(self,identity,index,x,y):
        if self.student_flow_active():return
        try:
            value=self.composition();placement=deepcopy(value['placements'][identity]);frames=resource_frames(placement);frame=frames[index]
            w,h=FORMATS[value['format']];frame.update(x=max(0,min(x,w-frame['width'])),y=max(0,min(y,h-frame['height'])))
            changed={**frames[0],**({'continuations':frames[1:]} if len(frames)>1 else {})}
            self.selected=identity;self.frame_index=index;self.commit(place_resource(value,identity,changed))
        except (ValueError,OSError) as exc:
            self.refresh();self.status.setText(str(exc))

    def apply_geometry(self):
        if not self.selected or self.student_flow_active():return
        try:
            value=self.composition();frames=resource_frames(value['placements'][self.selected])
            frames[self.frame_index]={'page_id':self.page_picker.currentData(),**{k:e.value() for k,e in self.geometry.items()}}
            self.commit(place_resource(value,self.selected,{**frames[0],**({'continuations':frames[1:]} if len(frames)>1 else {})}))
        except ValueError as exc:self.status.setText(str(exc))

    def edit_selected(self):
        if not self.selected:return
        value=self.composition();item=next((r for r in value['resources'] if r['id']==self.selected),None)
        if item is None:return
        expected=digest(self.owner.history.value);dialog=None
        try:
            used={b['value'] for b in item['blocks'] if b['kind']=='image'}
            assets=[a for a in self.payload.get('image_assets',[]) if a['asset_id'] in used]
            if {a['asset_id'] for a in assets}!=used:raise ValueError('这项素材的本地原图已移除，请重新关联。')
            data={a['asset_id']:self.owner.facade.preparation_image_bytes(a) for a in assets}
            dialog=ResourceEditor(value,item['id'],self,assets=assets,image_data=data)
            if (dialog.exec()==QDialog.DialogCode.Accepted and dialog.result_value is not None
                    and dialog.result_value!=value):self.commit(dialog.result_value,expected)
        except (ValueError,OSError) as exc:self.status.setText(str(exc))
        finally:
            if dialog is not None:dialog.deleteLater()

    def edit_question_answer_reveal(self):
        from .question_answer_reveal_dialog import QuestionAnswerRevealDialog
        from .composition_image_batch_entry import FrozenCompositionScope
        value=self.composition();item=next((r for r in value['resources'] if r['id']==self.selected),None)
        if item is None or item['original']['kind']!='complete_question':return
        scope=FrozenCompositionScope(self);dialog=None
        try:
            scope.check()
            used={b['value'] for b in item['blocks'] if b['kind']=='image'}
            by_id={a['asset_id']:a for a in scope.assets}
            if not used<=set(by_id):raise ValueError('完整题的本地原图已移除，请重新关联。')
            data={identity:self.owner.facade.preparation_image_bytes(by_id[identity]) for identity in used}
            dialog=QuestionAnswerRevealDialog(value,item['id'],scope.assets,data,self,guard=scope.check)
            scope.dialog=dialog
            if dialog.exec()!=QDialog.DialogCode.Accepted or dialog.result_value is None:return
            scope.check()
            if dialog.result_value==value:return
            self.commit(dialog.result_value,scope.expected,receipt=scope.mark_committed)
            self.selected=item['id'];self.frame_index=0;self._view_page_id=None
            self.refresh();self.fit_view()
            self.status.setText('完整题的课堂同页与原答案设置已应用，可一次撤销。')
        except (ValueError,OSError,RuntimeError) as exc:
            if not scope.owner_closed:self.status.setText(str(exc))
        finally:
            scope.close()
            if dialog is not None:dialog.deleteLater()

    def rename_selected(self):
        value=self.composition();item=next((r for r in value['resources'] if r['id']==self.selected),None)
        if item is None:return
        expected=digest(self.owner.history.value)
        title,ok=QInputDialog.getText(self,'修改导学问题／图题',
            '课页左上的导学问题／图题（也用于目录，可用“为什么”“怎样”等提问）：',text=suggested_resource_title(item))
        if not ok:return
        if not title.strip():self.status.setText('请填写导学问题或图题。');return
        if title==item['title']:return
        try:
            self.commit(edit_resource(value,item['id'],title=title),expected)
            self.status.setText('导学问题／图题已更新；正文、答案及来源快照保留。请保存草稿并生成新版本。')
        except ValueError as exc:self.status.setText(str(exc))

    def edit_image_display(self):
        from .image_display_dialog import ImageDisplayDialog
        value=self.composition();item=next((r for r in value['resources'] if r['id']==self.selected),None)
        if item is None or not any(b['kind']=='image' for b in item['blocks']):return
        expected=digest(self.owner.history.value)
        try:
            assets=self.payload.get('image_assets',[]);by_id={a['asset_id']:a for a in assets}
            used={b['value'] for b in item['blocks'] if b['kind']=='image'}
            if not used<=set(by_id):raise ValueError('这项素材的本地原图已移除，请重新关联。')
            data={identity:self.owner.facade.preparation_image_bytes(by_id[identity]) for identity in used}
            dialog=ImageDisplayDialog(value,item['id'],assets,data,self)
            if dialog.exec()!=QDialog.DialogCode.Accepted or dialog.result_value is None:return
            self.commit(dialog.result_value,expected)
            self.selected=item['id'];self.frame_index=0;self._view_page_id=None
            self.refresh();self.fit_view()
            self.status.setText('图片显示与分页已应用；可继续编排，本次调整可一次撤销。')
        except (ValueError,OSError) as exc:self.status.setText(str(exc))

    def pair_resources(self):
        from .composition_pairing_dialog import CompositionPairingDialog
        expected=digest(self.owner.history.value)
        try:
            dialog=CompositionPairingDialog(self.composition(),self.payload.get('image_assets',[]),
                self.image_data(),self,selected=self.selected)
            if dialog.exec()!=QDialog.DialogCode.Accepted or dialog.result_value is None:return
            self.commit(dialog.result_value,expected)
            self.selected=dialog.first.currentData();self.frame_index=0;self._view_page_id=None
            self.student.setChecked(False);self.refresh();self.fit_view()
            self.status.setText('图文讲解页已应用；可继续调整课堂顺序、讲解与点击显示，再保存草稿。')
        except (ValueError,OSError) as exc:self.status.setText(str(exc))

    def reorder(self,direction):
        value=self.composition();ids=[r['id'] for r in value['resources']]
        if self.selected not in ids:return
        index=ids.index(self.selected);target=index+direction
        if 0<=target<len(ids):ids[index],ids[target]=ids[target],ids[index];self.commit(reorder_resources(value,ids))

    def change_click_reveal(self,enabled):
        if self.loading or self.selected is None:return
        item=next((r for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is not None and item['original']['kind']=='complete_question':
            self.refresh();self.status.setText('完整题请使用“题面同页／点击原答案”选择确切答案。');return
        try:self.commit(set_click_reveal(self.composition(),self.selected,enabled))
        except ValueError as exc:self.status.setText(str(exc));self.refresh()

    def remove_selected(self):
        value=self.composition()
        if self.selected not in value['placements']:return
        value=set_click_reveal(value,self.selected,False)
        value['resources']=[r for r in value['resources'] if r['id']!=self.selected]
        del value['placements'][self.selected];self.selected=None;self.frame_index=0;self.commit(value)

    def arrange(self,columns):
        try:self.commit(arrange_composition(self.composition(),self.payload.get('image_assets',[]),self.image_data(),columns=columns));self.fit_view()
        except ValueError as exc:self.status.setText(str(exc))

    def change_format(self,format):
        if self.loading:return
        value=self.composition()
        if format!='16:9':value.pop('click_reveals',None)
        value['format']=format;w,h=FORMATS[format]
        for identity in value['placements']:
            value['placements'][identity]={'page_id':value['pages'][0],'x':12,'y':12,'width':w-24,'height':h-24}
        try:self.commit(arrange_composition(value,self.payload.get('image_assets',[]),self.image_data()));self.fit_view()
        except ValueError as exc:self.status.setText(str(exc));self.refresh()

    def edit_pending_materials(self, fresh, assets, image_data, error, guard=None):
        """Native hook for an explicit uncommitted batch work-copy editor."""
        from .pending_material_editor import PendingMaterialEditor
        dialog = PendingMaterialEditor(fresh, assets, image_data, error, self, guard=guard)
        self.pending_material_editor = dialog
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_value is None:
                if dialog.guard_error: raise ValueError(dialog.guard_error)
                return None
            return deepcopy(dialog.result_value)
        finally:
            self.pending_material_editor = None
            dialog.deleteLater()

    def add_published_result(self, publication, result, expected, *, expected_assets=None, insertion=None, guard=None, on_recheck=None, commit_receipt=None):
        """Offer one explicit work-copy repair only after a layout failure."""
        from ..desktop_handout_material_handoff import checked_material_work_copy
        def current():
            if guard is not None: guard()
            self.check_insertion(insertion)
            if digest(self.owner.history.value) != expected:
                raise ValueError('作品已变化，本批未加入；请重新选择素材。')
            if expected_assets is not None and self.owner.page.image_assets_widget.assets() != expected_assets:
                raise ValueError('备课图片已变化，本批未加入；请重新选择素材。')
        current()
        try:
            self.add_result(result, expected, expected_assets=expected_assets,
                            insertion=insertion, commit_guard=current, commit_receipt=commit_receipt)
            return True
        except MaterialLayoutError as exc:
            current()
            candidate = self.edit_pending_materials(exc.composition, exc.assets, exc.image_data, str(exc), current)
        if candidate is None:
            raise ValueError('已取消这批课堂素材，原有课堂内容保留。')
        current()
        working = checked_material_work_copy(result, candidate['resources'])
        if on_recheck is not None:
            on_recheck(working)
            return False
        # This rechecks the original published result, never the edited text.
        publication.recheck_published(current)
        current()
        # A second layout failure propagates to the existing caller's discard;
        # neither partial additions nor automatic character replacement occur.
        self.add_result(working, expected, expected_assets=expected_assets,
                        insertion=insertion, commit_guard=current, commit_receipt=commit_receipt)
        return True

    def recheck_pending_materials(self, publication, working, expected, *, expected_assets=None,
                                  insertion=None, guard=None, worker_guard=None, commit_receipt=None, success, failure):
        """Rebuild Word evidence in a worker; lay out/commit only on the GUI."""
        from threading import Event
        from ..reader_cancellation import ReadCancelled
        from ..desktop_preparation import DesktopPreparationError
        owner = self.owner; revoked = Event(); connections = []; state = {'done': False, 'task_id': None}
        def revoke(*_): revoked.set()
        for name in ('finished', 'destroyed'):
            signal = getattr(owner, name, None)
            if signal is not None:
                signal.connect(revoke); connections.append((signal, revoke))
        def current():
            if revoked.is_set(): raise ValueError('选材窗口已关闭，本批未加入。')
            if guard is not None: guard()
            self.check_insertion(insertion)
        def work():
            def alive():
                if revoked.is_set(): raise ReadCancelled('选材窗口已关闭，本批未加入。')
                if worker_guard is not None: worker_guard()
            try:
                publication.recheck_published(alive)
                return working
            except (ValueError, OSError, TypeError, KeyError) as exc:
                publication.discard()
                raise DesktopPreparationError('material_work_copy_recheck', str(exc)) from exc
            except BaseException:
                publication.discard()
                raise
        def fail(message):
            try:
                if state['done']: return
                state['done'] = True
                # A destroyed/closed owner has already revoked this batch;
                # only its private publication needs releasing.
                if revoked.is_set(): return
                owner.set_busy(False)
                failure(str(message))
            finally: publication.discard()
        def ready(result):
            try:
                if state['done']: return
                current(); owner.set_busy(False)
                self.add_result(result, expected, expected_assets=expected_assets, insertion=insertion,
                    commit_guard=current, commit_receipt=commit_receipt)
                success(result)
                state['done'] = True
            except Exception as exc:
                fail(str(exc))
            finally: publication.discard()
        def finished(identity):
            if identity != state['task_id']: return
            try:
                if not state['done']: fail('本次来源核对已停止，本批未加入。')
            finally:
                for signal, callback in connections:
                    try: signal.disconnect(callback)
                    except (TypeError, RuntimeError): pass
        owner.tasks.task_finished.connect(finished); connections.append((owner.tasks.task_finished, finished))
        owner._cancellable = True; owner.set_busy(True)
        owner.status.setText('正在重新核对原来源与课堂副本…')
        try:
            state['task_id'] = owner.tasks.submit('正在重新核对原来源与课堂副本…', work, on_success=ready, on_failure=fail)
            owner._task = state['task_id']
            return state['task_id']
        except BaseException:
            for signal, callback in connections:
                try: signal.disconnect(callback)
                except (TypeError, RuntimeError): pass
            publication.discard()
            raise

    def add_result(self,result,expected,*,expected_assets=None,insertion=None,commit_guard=None,commit_receipt=None):
        self.check_insertion(insertion)
        if digest(self.owner.history.value)!=expected:raise ValueError('作品已变化，请重新选择素材。')
        if expected_assets is not None and self.owner.page.image_assets_widget.assets()!=expected_assets:
            raise ValueError('备课图片已变化，请重新选择素材。')
        value=self.composition()
        if not result['resources']:return
        fresh=new_composition(value['format']);w,h=FORMATS[value['format']]
        fresh['resources']=deepcopy(result['resources']);fresh['placements']={r['id']:{'page_id':fresh['pages'][0],'x':12,'y':12,'width':w-24,'height':h-24} for r in fresh['resources']}
        flow='step_flow_placement' in result
        if flow:
            from .step_flow_dialog import step_flow_composition
            if result['step_flow_placement']['format']!=value['format']:
                raise ValueError('作品画幅已变化，请重新填写分步讲解。')
            fresh=step_flow_composition(result)
        assets=result.get('image_assets',expected_assets if expected_assets is not None else self.payload.get('image_assets',[]))
        used={b['value'] for r in fresh['resources'] for b in r['blocks'] if b['kind']=='image'}
        data={a['asset_id']:self.owner.facade.preparation_image_bytes(a) for a in assets if a['asset_id'] in used}
        fresh=validate_composition(fresh)
        from ..desktop_preparation_images import verify_image_bytes
        if set(data)!=used:raise ValueError('本批素材的完整图片缺失，请重新选择来源。')
        for asset in assets:
            if asset['asset_id'] in used:verify_image_bytes(asset,data[asset['asset_id']])
        try:
            if flow:
                rendered=layout_composition(fresh,assets,data,student=False)
                if rendered['errors']:raise ValueError('\n'.join(rendered['errors']))
            else:fresh=arrange_composition(fresh,assets,data)
        except ValueError as exc:
            raise MaterialLayoutError(str(exc),fresh,assets,data) from exc
        reveals=result.get('click_reveals',[])
        if any(identity not in {r['id'] for r in fresh['resources']} for identity in reveals):
            raise ValueError('点击讲解须绑定本次加入的完整素材。')
        for identity in reveals:fresh=set_click_reveal(fresh,identity,True)
        position=insertion or {'mode':'end','anchor_resource_id':None}
        value=insert_composition_batch(value,fresh,position['mode'],position.get('anchor_resource_id'))
        # Validate before updating the existing image form and design together.
        validate_composition(value)
        self.check_insertion(insertion)
        if digest(self.owner.history.value)!=expected:raise ValueError('作品已变化，请重新选择素材。')
        if expected_assets is not None and self.owner.page.image_assets_widget.assets()!=expected_assets:
            raise ValueError('备课图片已变化，请重新选择素材。')
        if commit_guard is not None:commit_guard()
        if not flow or assets!=self.owner.page.image_assets_widget.assets():
            self.owner.page.image_assets_widget.set_assets(assets)
        self.selected=fresh['resources'][0]['id'];self.frame_index=0
        if commit_receipt is None:self.commit(value,expected)
        else:self.commit(value,expected,receipt=commit_receipt)
        self.fit_view()
        self.owner.status.setText(f"已加入 {len(fresh['resources'])} 项素材；可继续编排，完成后保存草稿。")

    def add_questions(self):
        insertion=self.freeze_insertion()
        from .word_question_dialog import WordQuestionDialog
        from ..desktop_composition_sources import merge_question_result
        expected=digest(self.owner.history.value)
        dialog=WordQuestionDialog(self.owner.facade,self.owner.tasks,self,
            lesson_topic=self.owner.page.topic.text(),handoff_mode='composition')
        try:
            if dialog.exec()!=QDialog.DialogCode.Accepted or not dialog.composition_result:return
            result=merge_question_result(dialog.composition_result,self.composition()['resources'],
                self.payload.get('image_assets',[]))
            if not result['resources']:
                self.owner.status.setText('这些版本的完整题已在当前课件中；原作品副本保持。');return
            self.add_result(result,expected,insertion=insertion)
        except (ValueError,OSError) as exc:
            self.owner.status.setText(str(exc)+'；当前课件与已有素材保留。')
        finally:
            dialog.deleteLater()

    def read_selected_textbook_original(self):
        item=next((r for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is not None and item['original']['kind']=='textbook_image':
            self.read_textbook_crop_original()
        else:self.read_knowledge_original()

    def read_textbook_crop_original(self):
        from shiboken6 import isValid
        from .textbook_crop_source_dialog import TextbookCropSourceDialog
        item=next((deepcopy(r) for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is None or item['original']['kind']!='textbook_image':return
        host=self.window()
        if isinstance(host,QDialog) and getattr(self,'_textbook_read_host',None) is not host:
            self._textbook_read_host=host
            host.finished.connect(self.invalidate_material_insertion)
        insertion=self.freeze_insertion()
        expected=digest(self.owner.history.value)
        assets=deepcopy(self.owner.page.image_assets_widget.assets())
        def current():
            if not isValid(self):
                raise ValueError('课件窗口已关闭，请重新打开教材裁片原页。')
            self.check_insertion(insertion)
            if (getattr(self.owner,'_stopping',False) or getattr(self.owner,'_close_when_finished',False)
                    or self.selected!=item['id'] or digest(self.owner.history.value)!=expected
                    or self.owner.page.image_assets_widget.assets()!=assets):
                raise ValueError('课件在阅读期间变化，请重新打开教材裁片原页。')
        def opened(context):
            if not isValid(self):return
            dialog=None
            try:
                current()
                if 'relocation_preview' in context:
                    from .textbook_source_location_dialog import TextbookSourceLocationDialog
                    dialog=TextbookSourceLocationDialog(self.owner.facade,item,assets,
                        context['relocation_preview'],self,tasks=self.owner.tasks,current=current)
                    dialog.exec()
                    current()
                    if dialog.linked:
                        self.owner.run('已关联当前原书位置，正在打开原页',
                            lambda:self.owner.facade.preparation_textbook_crop_original(item,assets),
                            opened,cancellable=True)
                    return
                dialog=TextbookCropSourceDialog(self.owner.facade,item,assets,context,self,current=current)
                dialog.exec()
                current()
                if getattr(dialog,'section_materials_requested',False):
                    self.choose_section_materials(item,assets,expected,insertion)
            except (ValueError,OSError) as exc:self.owner.status.setText(str(exc))
            finally:
                if dialog is not None:dialog.deleteLater()
        def read():
            from ..desktop_textbook_source_locations import (TextbookSourceRelocationRequired,
                preview_textbook_source_relocation)
            try:
                return self.owner.facade.preparation_textbook_crop_original(item,assets)
            except TextbookSourceRelocationRequired:
                return {'relocation_preview':preview_textbook_source_relocation(self.owner.facade,item,assets)}
        self.owner.run('正在核对教材裁片并打开完整原页…',read,opened,cancellable=True)

    def read_knowledge_original(self):
        from ..desktop_knowledge_reading import knowledge_card_reader, recheck_knowledge_card_reader
        from .knowledge_card_reader import KnowledgeCardReader
        item=next((deepcopy(r) for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is None or item['original']['kind']!='knowledge_note':return
        insertion=self.freeze_insertion()
        expected=digest(self.owner.history.value)
        assets=deepcopy(self.owner.page.image_assets_widget.assets())
        def current():
            self.check_insertion(insertion)
            if (getattr(self.owner,'_stopping',False) or getattr(self.owner,'_close_when_finished',False)
                    or self.selected!=item['id']
                    or digest(self.owner.history.value)!=expected
                    or self.owner.page.image_assets_widget.assets()!=assets):
                raise ValueError('备课在阅读期间变化，请重新打开知识卡原页。')
        def opened(context):
            dialog=None
            try:
                current()
                dialog=KnowledgeCardReader(self.owner.facade,item,context,self,current=current)
                if dialog.exec()!=QDialog.DialogCode.Accepted:return
                current()
                section_materials_requested=bool(getattr(dialog,'section_materials_requested',False))
                def checked():
                    return recheck_knowledge_card_reader(self.owner.facade,item,context)
                def select_images(_result):
                    current()
                    if section_materials_requested:
                        self.choose_section_materials(item,assets,expected,insertion)
                        return
                    self.selected=item['id'];self.refresh()
                    self.add_knowledge_textbook()
                self.owner.run('正在核对原页并返回教材选图…',checked,select_images,cancellable=True)
            except (ValueError,OSError) as exc:self.owner.status.setText(str(exc))
            finally:
                if dialog is not None:dialog.deleteLater()
        self.owner.run('正在打开知识卡对应的教材原页…',
            lambda:knowledge_card_reader(self.owner.facade,item),opened,cancellable=True)

    def choose_section_materials(self,item,assets,expected,insertion):
        """Keep the reading origin and classroom destination while browsing a chapter."""
        from ..desktop_section_materials import section_material_context
        from ..desktop_section_material_handoff import prepare_section_material_request
        from .section_materials_dialog import SectionMaterialsDialog
        resources=deepcopy(self.composition()['resources'])
        def current():
            self.check_insertion(insertion)
            if (self.selected!=item['id'] or digest(self.owner.history.value)!=expected
                    or self.owner.page.image_assets_widget.assets()!=assets):
                raise ValueError('当前课件或插入位置已经变化，请重新从原页选材。')
        def choose(context):
            dialog=None
            try:
                current()
                dialog=SectionMaterialsDialog(self.owner.facade,self.owner.tasks,context,self,
                    guard=current,course_resources=resources,image_assets=assets)
                if dialog.exec()!=QDialog.DialogCode.Accepted or dialog.request is None:return
                current();request=deepcopy(dialog.request)
                def prepare():
                    current()
                    return prepare_section_material_request(self.owner.facade,item,resources,assets,request)
                def ready(publication):
                    result=None; pending=False
                    def accepted(_result):
                        publication.accept()
                        self.owner.status.setText('本节知识与所选素材已加入指定位置，可一次撤销。')
                    def failed(message):
                        from ..desktop_handout_material_handoff import material_batch_is_referenced
                        # Preserve pictures already referenced if refresh fails
                        # after the single history commit.
                        referenced=material_batch_is_referenced(result or {},self.owner.history.value,
                            *self.owner.history.back,*self.owner.history.forward,
                            getattr(self.owner.page,'_lesson_design',None),existing_assets=assets)
                        if referenced:publication.accept()
                        elif (result is not None and 'image_assets' in result
                                and self.owner.page.image_assets_widget.assets()==result['image_assets']):
                            self.owner.page.image_assets_widget.set_assets(deepcopy(assets))
                        self.owner.status.setText(str(message))
                    def recheck(working):
                        nonlocal pending
                        self.recheck_pending_materials(publication,working,expected,expected_assets=assets,
                            insertion=insertion,guard=current,success=accepted,failure=failed)
                        pending=True
                    try:
                        current();result=publication.publish(current)
                        if not result['resources']:
                            publication.accept()
                            self.owner.status.setText('此版本知识与素材已在本课中，原有编辑保留。')
                            return
                        if not self.add_published_result(publication,result,expected,expected_assets=assets,
                            insertion=insertion,guard=current,on_recheck=recheck):return
                        accepted(result)
                    except Exception as exc:
                        failed(str(exc))
                    finally:
                        if not pending:publication.discard()
                self.owner.run('正在核对本节知识与素材并加入课件…',prepare,ready,cancellable=True)
            except (ValueError,OSError) as exc:self.owner.status.setText(str(exc))
            finally:
                if dialog is not None:dialog.deleteLater()
        self.owner.run('正在读取本节知识和选材范围…',
            lambda:section_material_context(self.owner.facade,item,resources,assets),choose,cancellable=True)

    def add_knowledge_questions(self):
        insertion=self.freeze_insertion()
        from ..desktop_knowledge_questions import knowledge_question_context, recheck_knowledge_question_context
        from ..desktop_composition_sources import merge_question_result
        from .word_question_dialog import WordQuestionDialog
        item=next((deepcopy(r) for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is None or item['original']['kind']!='knowledge_note':return
        expected=digest(self.owner.history.value)
        assets=deepcopy(self.owner.page.image_assets_widget.assets())
        resources=deepcopy(self.composition()['resources'])
        def current():
            self.check_insertion(insertion)
            if (getattr(self.owner,'_stopping',False) or getattr(self.owner,'_close_when_finished',False)
                    or digest(self.owner.history.value)!=expected
                    or self.owner.page.image_assets_widget.assets()!=assets):
                raise ValueError('备课或图片在选例题期间变化，请重新从知识卡选择。')
        def choose(context):
            dialog=None
            try:
                current()
                dialog=WordQuestionDialog(self.owner.facade,self.owner.tasks,self,
                    lesson_topic=context['original_title'],handoff_mode='composition',persist_selection=False,
                    initial_curriculum_context=context['curriculum_context'],
                    initial_search_query=context['search_query'])
                dialog.setWindowTitle('从这张知识卡选课堂例题')
                if dialog.exec()!=QDialog.DialogCode.Accepted or not dialog.composition_result:return
                current();incoming=deepcopy(dialog.composition_result)
                def checked():
                    recheck_knowledge_question_context(self.owner.facade,item,context)
                    return merge_question_result(incoming,resources,assets)
                def ready(result):
                    current()
                    if not result['resources']:
                        self.owner.status.setText('这些版本的完整题已在当前课件中；已有编辑保持。');return
                    self.add_result(result,expected,insertion=insertion)
                    fragment=next((f for f in (self.layout_result or {}).get('fragments',[])
                        if f['resource_id']==self.selected),None)
                    if fragment is not None:
                        self.navigate_to_page(fragment['frame']['page_id'],self.selected,fragment['frame_index'])
                self.owner.run('正在核对知识卡并加入本课例题…',checked,ready,cancellable=True)
            except (ValueError,OSError) as exc:self.owner.status.setText(str(exc))
            finally:
                if dialog is not None:dialog.deleteLater()
        self.owner.run('正在查找这张知识卡的教材章节与例题…',
            lambda:knowledge_question_context(self.owner.facade,item),choose,cancellable=True)

    def manage_knowledge_question_links(self):
        insertion=self.freeze_insertion()
        from .knowledge_question_links_dialog import KnowledgeQuestionLinksDialog
        from ..desktop_knowledge_question_links import KnowledgeQuestionLinks
        from ..desktop_composition_sources import import_questions, merge_question_result
        item=next((deepcopy(r) for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is None or item['original']['kind'] not in {'knowledge_note','handout_knowledge','complete_question'}:return
        knowledge=item['original']['kind'] in {'knowledge_note','handout_knowledge'}
        expected=digest(self.owner.history.value)
        assets=deepcopy(self.owner.page.image_assets_widget.assets())
        resources=deepcopy(self.composition()['resources'])
        def current():
            self.check_insertion(insertion)
            if (getattr(self.owner,'_stopping',False) or getattr(self.owner,'_close_when_finished',False)
                    or self.selected!=item['id'] or digest(self.owner.history.value)!=expected
                    or self.owner.page.image_assets_widget.assets()!=assets):
                raise ValueError('备课、图片或所选素材在关联期间变化，请重新打开。')
        service=KnowledgeQuestionLinks(self.owner.facade)
        dialog=KnowledgeQuestionLinksDialog(self.owner.facade,self.owner.tasks,self,
            knowledge_resource=item if knowledge else None,
            question_selection=None if knowledge else item,
            course_resources=resources,guard=current,allow_course_add=knowledge,service=service)
        try:
            if dialog.exec()!=QDialog.DialogCode.Accepted or dialog.course_request is None:return
            current();request=dialog.course_request
            def checked():
                current()
                if service.snapshot()!=request['snapshot']:
                    raise ValueError('例题关联在加入期间变化，请重新选择。')
                relation=service.evaluate(request['link_id'])
                if relation.get('status')!='current' or relation.get('removed',False):
                    raise ValueError('关联原件缺失或版本变化，请重新检查。')
                if service.knowledge_context(item)['endpoint']!=relation['knowledge']:
                    raise ValueError('本课知识原内容与所选关联版本不一致，请重新选择。')
                resolved=service.resolve_selection(relation)
                selection={**resolved['selection'],'points':request['selection']['points']}
                if selection!=request['selection']:
                    raise ValueError('关联题目范围已变化，请重新选择。')
                result=import_questions(resolved['scoped_facade'],[selection],assets,
                    expected_reference=request['reference'])
                latest=service.evaluate(request['link_id'])
                if service.snapshot()!=request['snapshot'] or latest.get('status')!='current' or latest.get('removed',False):
                    raise ValueError('知识或题目关联在读取期间变化，请重新选择。')
                return merge_question_result(result,resources,assets)
            def ready(result):
                current()
                if not result['resources']:
                    self.owner.status.setText('此版本例题已在本课中；已有标题与讲解保持。');return
                self.add_result(result,expected,insertion=insertion)
            self.owner.run('正在核对关联原题并加入本课…',checked,ready,cancellable=True)
        except (ValueError,OSError) as exc:self.owner.status.setText(str(exc))
        finally:dialog.deleteLater()

    def add_images(self):
        self._image_batch_entry.open('images')

    def add_activities(self):
        insertion=self.freeze_insertion()
        from ..desktop_composition_activities import activity_options
        from .lesson_activity_dialogs import ActivityPicker
        try:
            expected=digest(self.owner.history.value)
            rows=activity_options(self.owner.history.value,self.payload.get('image_assets',[]))
            if not rows:
                self.status.setText('暂无尚未加入的教学环节。可在“教学环节”填写任务，或选择已有环节核对关联。');return
            dialog=ActivityPicker(rows,self)
            if dialog.exec()==QDialog.DialogCode.Accepted:self.import_activity_nodes(dialog.chosen,expected,insertion=insertion)
            dialog.deleteLater()
        except ValueError as exc:self.status.setText(str(exc))

    def add_handout_materials(self):
        """Browse a detached mixed request without leaving the current editor."""
        from threading import Event
        from .handout_material_workspace import HandoutMaterialWorkspace
        from ..desktop_handout_material_handoff import prepare_handout_materials
        if self._handout_scope_closed:return
        if self._handout_workspace_dialog is not None:
            self._handout_workspace_dialog.raise_();self._handout_workspace_dialog.activateWindow();return
        if getattr(self.owner,'busy',False):
            self.owner.status.setText('请先完成当前操作，再通读讲义选材。');return
        insertion=self.freeze_insertion()
        revoked=Event();self._handout_scope_event=revoked
        history=deepcopy((self.owner.history.value,self.owner.history.back,self.owner.history.forward))
        expected=digest(history[0]);assets=deepcopy(self.owner.page.image_assets_widget.assets())
        resources=deepcopy(self.composition()['resources'])
        payload=getattr(self.owner.page,'_payload',None)
        expected_payload=deepcopy(payload()) if callable(payload) else None
        target=getattr(self.owner.page,'_editor_draft_id',None)
        def current():
            if revoked.is_set() or self._handout_scope_closed:
                raise ValueError('备课或选材窗口在读取期间变化，本批未加入；请重新选择。')
            self.check_insertion(insertion)
            if (history!=(self.owner.history.value,self.owner.history.back,self.owner.history.forward)
                    or self.owner.page.image_assets_widget.assets()!=assets
                    or getattr(self.owner.page,'_editor_draft_id',None)!=target
                    or (callable(payload) and payload()!=expected_payload)):
                revoked.set()
                raise ValueError('备课、图片或编辑历史在选材期间变化，本批未加入；请重新选择。')
        dialog=None
        try:
            current()
            source=deepcopy(self._handout_materials_source)
            options={'initial_source':source,'resume_source':True} if source is not None else {}
            dialog=HandoutMaterialWorkspace(self.owner.facade,self.owner.tasks,self,**options)
            self._handout_workspace_dialog=dialog
            outcome=dialog.exec()
            current()
            # Only the workspace's verified, fully read binding can resume.
            # A fresh workspace always starts with an empty selection list.
            self._handout_materials_source=deepcopy(dialog.continuation_source())
            if outcome!=QDialog.DialogCode.Accepted or not dialog.request:return
            request=deepcopy(dialog.request)
            self.queue_knowledge_publication(request,assets,resources,expected,insertion,current,
                prepare_handout_materials,title='正在核对讲义选材并加入本课…',
                added_message='讲义知识、原图与完整题已按所选顺序加入指定位置，可一次撤销。',
                duplicate_message='这些版本的讲义素材已在当前课件中；已有编辑保持。',
                revocation_event=revoked)
        except (ValueError,OSError,RuntimeError) as exc:
            if not self._handout_scope_closed:self.owner.status.setText(str(exc))
        finally:
            self._handout_workspace_dialog=None
            if dialog is not None:
                try:dialog.deleteLater()
                except RuntimeError:pass

    def add_handout_knowledge_from_selected(self):
        item=next((r for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is not None and item['original']['kind'] in {'handout_image','handout_knowledge'}:
            self.add_handout_knowledge(deepcopy(item['original']['evidence']['handout_source']))

    def add_handout_knowledge(self,initial_source=None):
        insertion=self.freeze_insertion()
        if isinstance(initial_source,bool):initial_source=None
        from .handout_knowledge_picker import HandoutKnowledgePicker
        from ..desktop_handout_material_handoff import prepare_handout_knowledge_publication
        expected=digest(self.owner.history.value)
        assets=deepcopy(self.owner.page.image_assets_widget.assets())
        resources=deepcopy(self.composition()['resources'])
        def current():
            self.check_insertion(insertion)
            if (getattr(self.owner,'_stopping',False) or getattr(self.owner,'_close_when_finished',False)
                    or digest(self.owner.history.value)!=expected
                    or self.owner.page.image_assets_widget.assets()!=assets):
                raise ValueError('备课在选择讲义知识期间变化，请重新选取。')
        picker=HandoutKnowledgePicker(self.owner.facade,self,initial_source=initial_source)
        try:
            if picker.exec()!=QDialog.DialogCode.Accepted or not picker.requests:return
            current();requests=deepcopy(picker.requests)
            self.queue_knowledge_publication(requests,assets,resources,expected,insertion,current,
                prepare_handout_knowledge_publication)
        except (ValueError,OSError) as exc:self.owner.status.setText(str(exc))
        finally:
            release=getattr(picker,'release',None)
            if callable(release):release()
            picker.deleteLater()

    def queue_knowledge_publication(self,requests,assets,resources,expected,insertion,current,prepare,*,
                                    title='正在核对并加入完整讲义知识…',
                                    added_message='完整讲义知识已加入指定位置，可一次撤销。',
                                    duplicate_message='这些版本的讲义知识段已在当前课件中；已有编辑保持。',
                                    revocation_event=None):
        """Publish a detached lecture batch; legacy knowledge defaults stay intact."""
        from threading import Event
        from ..reader_cancellation import ReadCancelled
        from ..desktop_handout_material_handoff import material_batch_is_referenced
        revoked=revocation_event if revocation_event is not None else Event()
        box={'publication':None,'phase':'reading','task_id':None};connections=[]
        def revoke(*_):revoked.set()
        for name in ('finished','destroyed'):
            signal=getattr(self.owner,name,None)
            if signal is not None:signal.connect(revoke);connections.append((signal,revoke))
        def disconnect():
            for signal,callback in connections:
                try:signal.disconnect(callback)
                except (RuntimeError,TypeError):pass
            connections.clear()
        def gui_guard():
            if revoked.is_set():raise ValueError('选材窗口已关闭，本批未加入。')
            current()
        def worker_guard():
            if revoked.is_set():raise ReadCancelled('选材窗口已关闭，本批未加入。')
        def work():
            publication=None
            try:
                worker_guard();publication=prepare(self.owner.facade,requests,assets,resources)
                box['publication']=publication
                return publication.publish(worker_guard)
            except BaseException:
                if publication is not None:publication.discard()
                raise
        def ready(result):
            publication=box['publication'];box['phase']='editing_or_layout';pending=False
            def accepted(_result):
                publication.accept()
                self.owner.status.setText(added_message)
            def failed(message):
                if material_batch_is_referenced(result,self.owner.history.value,*self.owner.history.back,
                        *self.owner.history.forward,getattr(self.owner.page,'_lesson_design',None),existing_assets=assets):
                    publication.accept()
                elif self.owner.page.image_assets_widget.assets()==result['image_assets']:
                    self.owner.page.image_assets_widget.set_assets(deepcopy(assets))
                if not self._handout_scope_closed:self.owner.status.setText(str(message))
            def recheck(working):
                nonlocal pending
                self.recheck_pending_materials(publication,working,expected,expected_assets=assets,
                    insertion=insertion,guard=gui_guard,worker_guard=worker_guard,success=accepted,failure=failed)
                pending=True;box['phase']='rechecking'
            try:
                gui_guard()
                if not result['resources']:
                    publication.accept()
                    self.owner.status.setText(duplicate_message);return
                if self.add_published_result(publication,result,expected,expected_assets=assets,
                        insertion=insertion,guard=gui_guard,on_recheck=recheck):accepted(result)
            except Exception as exc:failed(str(exc))
            finally:
                disconnect()
                if not pending:box['phase']='finished';publication.discard()
        def finished(identity):
            if identity!=box['task_id'] or box['phase']!='reading':return
            publication=box['publication']
            if publication is not None:publication.discard()
            disconnect()
        self.owner.tasks.task_finished.connect(finished);connections.append((self.owner.tasks.task_finished,finished))
        try:
            self.owner.run(title,work,ready,cancellable=True)
            box['task_id']=self.owner._task
        except BaseException:
            disconnect()
            if box['publication'] is not None:box['publication'].discard()
            raise

    def add_handout_from_selected(self):
        item = next((r for r in self.composition()['resources'] if r['id'] == self.selected), None)
        if item is not None and item['original']['kind'] in {'handout_image','handout_knowledge'}:
            self.add_handout_images(deepcopy(item['original']['evidence']['handout_source']))

    def add_handout_questions(self):
        insertion=self.freeze_insertion()
        from ..desktop_handout_questions import handout_question_facade
        from ..desktop_handout_sources import validate_handout_source
        from ..desktop_composition_sources import merge_question_result
        from .word_question_dialog import WordQuestionDialog
        item = next((r for r in self.composition()['resources'] if r['id'] == self.selected), None)
        if item is None or item['original']['kind'] not in {'handout_image','handout_knowledge'}: return
        try:
            binding = validate_handout_source(item['original']['evidence']['handout_source'])
        except ValueError as exc:
            self.owner.status.setText(str(exc)); return
        expected = digest(self.owner.history.value)
        assets = deepcopy(self.owner.page.image_assets_widget.assets())
        resources = deepcopy(self.composition()['resources'])
        def current():
            self.check_insertion(insertion)
            if (getattr(self.owner, '_stopping', False) or getattr(self.owner, '_close_when_finished', False)
                    or digest(self.owner.history.value) != expected
                    or self.owner.page.image_assets_widget.assets() != assets):
                raise ValueError('备课或图片在选例题期间变化，请重新选择。')
        def choose(scoped):
            dialog = None
            try:
                current()
                dialog = WordQuestionDialog(scoped, self.owner.tasks, self,
                    required_source_id=binding['source_sha256'],
                    lesson_topic=self.owner.page.topic.text(), handoff_mode='composition',
                    persist_selection=False)
                dialog.setWindowTitle('从这份讲义选课堂例题')
                if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.composition_result: return
                current()
                result = merge_question_result(dialog.composition_result, resources, assets)
                if not result['resources']:
                    self.owner.status.setText('这些版本的完整题已在当前课件中；已有编辑保持。'); return
                self.add_result(result, expected,insertion=insertion)
                fragment = next((f for f in (self.layout_result or {}).get('fragments', [])
                    if f['resource_id'] == self.selected), None)
                if fragment is not None:
                    self.navigate_to_page(fragment['frame']['page_id'], self.selected, fragment['frame_index'])
            except (ValueError, OSError) as exc:
                self.owner.status.setText(str(exc))
            finally:
                if dialog is not None: dialog.deleteLater()
        self.owner.run('正在读取这份讲义的例题…',
            lambda: handout_question_facade(self.owner.facade, binding), choose, cancellable=True)

    def add_handout_images(self, source=None):
        insertion=self.freeze_insertion()
        from ..desktop_handout_images import handout_image_options, handout_image_context, import_handout_image
        from ..desktop_handout_sources import handout_source_context, validate_handout_source
        from .handout_image_picker import HandoutImagePicker
        source = deepcopy(source) if isinstance(source, dict) else None
        expected = digest(self.owner.history.value)
        assets = deepcopy(self.owner.page.image_assets_widget.assets())
        resources = deepcopy(self.composition()['resources'])
        def current():
            self.check_insertion(insertion)
            if (getattr(self.owner, '_stopping', False) or getattr(self.owner, '_close_when_finished', False)
                    or digest(self.owner.history.value) != expected
                    or self.owner.page.image_assets_widget.assets() != assets):
                raise ValueError('备课或图片在选择讲义图期间变化，请重新选择。')
        def context():
            if source is None: return handout_image_options(self.owner.facade)
            checked = validate_handout_source(source)
            if 'asset_id' in checked: return handout_image_context(self.owner.facade, checked)
            return handout_source_context(self.owner.facade, checked)
        def choose(snapshot):
            picker = None
            try:
                current()
                picker = HandoutImagePicker(self.owner.facade, snapshot, resources, self)
                if picker.exec() != QDialog.DialogCode.Accepted: return
                current(); selection = deepcopy(picker.selection)
                def done(result):
                    current()
                    if not result['resources']:
                        self.owner.status.setText('相同原图、图题和用途已在本课；已有编辑保持。'); return
                    self.add_result(result, expected,insertion=insertion)
                    fragment = next((f for f in (self.layout_result or {}).get('fragments', [])
                        if f['resource_id'] == self.selected), None)
                    if fragment is not None:
                        self.navigate_to_page(fragment['frame']['page_id'], self.selected, fragment['frame_index'])
                self.owner.run('正在加入讲义原图…',
                    lambda: import_handout_image(self.owner.facade, assets, resources, selection), done, cancellable=True)
            except (ValueError, OSError) as exc: self.owner.status.setText(str(exc))
            finally:
                if picker is not None: picker.deleteLater()
        self.owner.run('正在读取讲义原图目录…',
            context,
            choose, cancellable=True)

    def import_activity_nodes(self,identities,expected=None,*,insertion=None):
        insertion=insertion or self.freeze_insertion()
        from ..desktop_composition_activities import import_activities
        if expected is not None and digest(self.owner.history.value)!=expected:
            raise ValueError('教学设计在选择期间变化，请重新选择环节。')
        expected=digest(self.owner.history.value)
        result=import_activities(self.owner.history.value,identities,self.payload.get('image_assets',[]))
        self.add_result(result,expected,insertion=insertion)

    def open_activity_source(self):
        item=next((r for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is None or item['original']['kind']!='lesson_activity':return
        index=next((i for i,n in enumerate(self.owner.history.value['nodes']) if n['id']==item['activity_link']['node_id']),None)
        if index is not None and item['activity_link']['design_id']==self.owner.history.value['id']:
            self.owner.tabs.setCurrentWidget(self.owner.main_splitter);self.owner.nodes.setCurrentRow(index)
            self.owner.select_node(index)

    def update_activity_source(self):
        from .lesson_activity_dialogs import ActivityUpdateDialog
        item=next((r for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is None or item['original']['kind']!='lesson_activity' or item['activity_link']['mode']!='linked':return
        expected=digest(self.owner.history.value)
        dialog=ActivityUpdateDialog(self.composition(),self.selected,self.owner.history.value,
            self.payload.get('image_assets',[]),self)
        try:
            if dialog.exec()==QDialog.DialogCode.Accepted:self.commit(dialog.result_value,expected)
        except ValueError as exc:self.status.setText(str(exc))
        dialog.deleteLater()

    def add_note(self):
        insertion=self.freeze_insertion()
        from ..desktop_composition_sources import knowledge_options,merge_knowledge_result
        from .knowledge_note_picker import KnowledgeNotePicker
        expected=digest(self.owner.history.value)
        assets=deepcopy(self.owner.page.image_assets_widget.assets())
        def current():
            self.check_insertion(insertion)
            if digest(self.owner.history.value)!=expected or self.owner.page.image_assets_widget.assets()!=assets:
                raise ValueError('备课或图片在选择知识卡期间变化，请重新选择。')
        def choose(snapshot):
            picker=None
            try:
                current()
                picker=KnowledgeNotePicker(self.owner.facade,snapshot.get('concepts',[])+snapshot['reading_notes'],self,batch=True)
                if picker.exec()!=QDialog.DialogCode.Accepted or not picker.composition_result:return
                current();result=merge_knowledge_result(picker.composition_result,self.composition()['resources'])
                if not result['resources']:
                    self.owner.status.setText('所选版本的知识卡已在课件中，已有编辑保持。');return
                self.add_result(result,expected,insertion=insertion)
                fragment=next((f for f in (self.layout_result or {}).get('fragments',[]) if f['resource_id']==self.selected),None)
                if fragment is not None:self.navigate_to_page(fragment['frame']['page_id'],self.selected,fragment['frame_index'])
            except (ValueError,OSError) as exc:self.owner.status.setText(str(exc))
            finally:
                if picker is not None:picker.deleteLater()
        self.owner.run('正在读取本地知识卡…',lambda:knowledge_options(self.owner.facade),choose,cancellable=True)

    def add_textbook(self):
        self._image_batch_entry.open('textbook')

    def add_knowledge_textbook(self):
        insertion=self.freeze_insertion()
        from .knowledge_textbook_picker import KnowledgeTextbookPicker
        from .textbook_batch_crop_dialog import TextbookBatchCropDialog
        from ..desktop_composition_sources import knowledge_textbook_options,import_knowledge_textbook_crops
        item=next((deepcopy(r) for r in self.composition()['resources'] if r['id']==self.selected),None)
        if item is None or item['original']['kind']!='knowledge_note':return
        expected=digest(self.owner.history.value)
        assets=deepcopy(self.owner.page.image_assets_widget.assets())
        def current():
            try:self.check_insertion(insertion)
            except ValueError as exc:self.status.setText(str(exc));return False
            if digest(self.owner.history.value)==expected and self.owner.page.image_assets_widget.assets()==assets:return True
            self.status.setText('作品在选图期间变化，请重新从知识卡选图。');return False
        def choose(snapshot):
            if not current():return
            intro='从这张知识卡的原页和章节选取讲解图。勾选多张，逐张框选、填写问题图题与讲解用途，再一起加入本课。'
            if snapshot['notices']:intro+='\n'+'；'.join(snapshot['notices'])
            picker=dialog=None
            try:
                picker=KnowledgeTextbookPicker(self.owner.facade,snapshot['rows'],self,intro=intro)
                if picker.exec()!=QDialog.DialogCode.Accepted or not picker.chosen_rows or not current():return
                dialog=TextbookBatchCropDialog(self.owner.facade,self.owner.tasks,picker.chosen_rows,self)
                if dialog.exec()!=QDialog.DialogCode.Accepted or not dialog.selection_requests or not current():return
                requests=deepcopy(dialog.selection_requests)
                def ready(result):
                    if current():self.add_result(result,expected,insertion=insertion)
                self.owner.run('正在核对知识卡原页并带入本批裁片…',
                    lambda:import_knowledge_textbook_crops(self.owner.facade,item,snapshot['binding'],requests,assets),
                    ready,cancellable=True)
            except (ValueError,OSError) as exc:self.status.setText(str(exc))
            finally:
                if picker is not None:picker.deleteLater()
                if dialog is not None:dialog.deleteLater()
        self.owner.run('正在查找这张知识卡的教材原页与图表…',
            lambda:knowledge_textbook_options(self.owner.facade,item),choose,cancellable=True)

    def add_step_flow(self):
        insertion=self.freeze_insertion()
        from .step_flow_dialog import StepFlowDialog
        expected=digest(self.owner.history.value)
        assets=deepcopy(self.owner.page.image_assets_widget.assets())
        def current():
            self.check_insertion(insertion)
            if digest(self.owner.history.value)!=expected:
                raise ValueError('作品在填写分步讲解期间变化，请重新打开。')
            if self.owner.page.image_assets_widget.assets()!=assets:
                raise ValueError('备课图片在填写分步讲解期间变化，请重新打开。')
        dialog=None
        try:
            current()
            dialog=StepFlowDialog(self.composition()['format'],self,guard=current)
            if dialog.exec()!=QDialog.DialogCode.Accepted or dialog.result_value is None:return
            current()
            self.add_result(dialog.result_value,expected,expected_assets=assets,insertion=insertion)
            self.student.setChecked(False);self.refresh();self.fit_view()
            self.status.setText('完整分步讲解已加入；课堂按步骤点击显示，本次加入可一次撤销。'
                if dialog.click_steps.isChecked() else '完整分步讲解已加入，本次加入可一次撤销。')
        except (ValueError,OSError) as exc:self.status.setText(str(exc))
        finally:
            if dialog is not None:dialog.deleteLater()

    def add_personal(self):
        insertion=self.freeze_insertion();expected=digest(self.owner.history.value)
        dialog=QDialog(self);dialog.setWindowTitle('加入个人文字');dialog.resize(650,450);layout=QVBoxLayout(dialog)
        title=QLineEdit();title.setPlaceholderText('素材标题');title.setMaxLength(2000);body=QPlainTextEdit()
        teacher=QCheckBox('仅教师可见');layout.addWidget(title);layout.addWidget(body,1);layout.addWidget(teacher)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        item=resource('personal_text',title.text() or '个人文字',[block('text','teacher_note' if teacher.isChecked() else 'personal',body.toPlainText())],
                      {'source_path':'教师本次手工输入','source_sha256':'unknown','source_revision':'unknown'})
        try:self.add_result({'resources':[item]},expected,insertion=insertion)
        except ValueError as exc:self.status.setText(str(exc))
