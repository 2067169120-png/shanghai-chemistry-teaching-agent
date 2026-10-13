"""Author complete personal steps, measure their actual classroom flow, then add once."""
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGraphicsScene, QGraphicsView, QGridLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter,
    QVBoxLayout, QWidget)

from ..desktop_composition_exposition import SCHEMA
from ..desktop_composition_render import layout_composition, page_image
from ..desktop_lesson_composition import (FORMATS, add_resource, block, new_composition,
    resource, set_click_reveal, validate_composition)
from .components import page_scroll


def _label(text):
    label=QLabel(text);label.setWordWrap(True);label.setMinimumWidth(0)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def step_flow_resource(title, steps, kind):
    """Preserve authored strings exactly; reject capacity instead of truncating."""
    if not title.strip():raise ValueError('请填写左上角的导学问题。')
    if len(title)>2000:raise ValueError('导学问题超出2000字容量，请修改；已填写文字保留。')
    if kind not in {'process','process_vertical'} or not 2<=len(steps)<=6:
        raise ValueError('请选择横向或纵向流程，并保留2—6个步骤。')
    rows=[]
    for index, step in enumerate(steps):
        heading,description=step['title'],step['description']
        if not heading.strip():raise ValueError(f'请填写第{index+1}步标题。')
        if '\n' in heading:raise ValueError(f'第{index+1}步标题请填写为一行；长说明放入说明框。')
        text=heading+('\n'+description if description else '')
        if len(text)>120000:raise ValueError(f'第{index+1}步超出文字容量；已填写文字保留。')
        rows.append(block('text','personal',text))
    item=resource('personal_text',title,rows,
        {'source_path':'教师本次手工填写的分步讲解','source_sha256':'unknown','source_revision':'unknown'})
    item['exposition']={'schema':SCHEMA,'kind':kind}
    return item


def step_flow_result(title, steps, kind='process_vertical', *, format='16:9', click_steps=None):
    """Production handoff: a personal flow plus its complete, measured-width frame."""
    if format not in FORMATS:raise ValueError('流程画幅无法识别。')
    if click_steps is None:click_steps=format=='16:9'
    if type(click_steps) is not bool or (click_steps and format!='16:9'):
        raise ValueError('逐步点击用于课堂投影。')
    item=step_flow_resource(title,steps,kind);w,h=FORMATS[format];margin=10 if format=='16:9' else 12
    result={'resources':[item],'step_flow_placement':{'format':format,
        'frame':{'x':margin,'y':margin,'width':w-2*margin,'height':h-2*margin}}}
    if click_steps:result['click_reveals']=[item['id']]
    return result


def step_flow_composition(result):
    """Rebuild only this entry's frame; the existing lesson and its source roles stay."""
    spec=result['step_flow_placement'];format=spec['format']
    if set(spec)!={'format','frame'} or format not in FORMATS or len(result['resources'])!=1:
        raise ValueError('分步讲解的完整外框无效。')
    item=result['resources'][0]
    if item['original']['kind']!='personal_text' or item.get('exposition',{}).get('kind') not in {'process','process_vertical'}:
        raise ValueError('分步讲解只能使用教师填写的完整文字步骤。')
    w,h=FORMATS[format];margin=10 if format=='16:9' else 12
    expected={'x':margin,'y':margin,'width':w-2*margin,'height':h-2*margin}
    if spec['frame']!=expected:raise ValueError('分步讲解外框须保留当前画幅的完整可读尺寸。')
    value=new_composition(format);value=add_resource(value,item,{'page_id':value['pages'][0],**deepcopy(expected)})
    if result.get('click_reveals'):
        if result['click_reveals']!=[item['id']]:raise ValueError('逐步点击须绑定当前完整流程。')
        value=set_click_reveal(value,item['id'],True)
    return validate_composition(value)


class StepFlowDialog(QDialog):
    def __init__(self, format='16:9', parent=None, *, guard=None):
        super().__init__(parent)
        self.setWindowTitle('加入分步讲解');self.resize(900,800);self.setMinimumSize(380,500)
        self.format=format;self.guard=guard;self.result_value=None
        self.steps=[{'title':'','description':''} for _ in range(2)]
        self.current=0;self._loading=True;self._closed=False;self._candidate=None;self._layout=None
        root=QVBoxLayout(self)
        root.addWidget(_label('用问题引导学生思考，再逐步呈现完整的推理过程。标题与说明按填写内容保留。'))
        content=QWidget();controls=QVBoxLayout(content);controls.setContentsMargins(0,0,0,0)
        self.question_title=QLineEdit();self.question_title.setMaxLength(120000)
        self.question_title.setPlaceholderText('例如：怎样设计实验来验证这个假设？')
        self.question_title.setAccessibleName('分步讲解左上导学问题')
        controls.addWidget(_label('左上导学问题'));controls.addWidget(self.question_title)
        self.split=QSplitter()
        left=QWidget();order=QVBoxLayout(left);order.setContentsMargins(0,0,0,0)
        self.step_list=QListWidget();self.step_list.setMinimumWidth(0);self.step_list.setMinimumHeight(100)
        self.step_list.setWordWrap(True);self.step_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.step_list.setAccessibleName('分步讲解步骤顺序');order.addWidget(self.step_list)
        actions=QGridLayout();order.addLayout(actions)
        self.add_button=QPushButton('新增步骤');self.remove_button=QPushButton('删除步骤')
        self.up_button=QPushButton('上移');self.down_button=QPushButton('下移')
        for index,button in enumerate((self.add_button,self.remove_button,self.up_button,self.down_button)):
            actions.addWidget(button,index//2,index%2)
        self.split.addWidget(left)
        right=QWidget();form=QFormLayout(right);form.setContentsMargins(0,0,0,0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.step_title=QLineEdit();self.step_title.setMaxLength(120000)
        self.step_title.setAccessibleName('当前步骤标题')
        self.description=QPlainTextEdit();self.description.setMinimumHeight(100)
        self.description.setAccessibleName('当前步骤完整说明')
        form.addRow('步骤标题',self.step_title);form.addRow('说明（可留空）',self.description)
        self.split.addWidget(right);self.split.setSizes([260,500]);controls.addWidget(self.split)
        form=QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows);controls.addLayout(form)
        self.direction=QComboBox();self.direction.addItem('纵向流程','process_vertical');self.direction.addItem('横向流程','process')
        self.direction.setAccessibleName('分步讲解流程方向');form.addRow('流程方向',self.direction)
        self.click_steps=QCheckBox('课堂PPT逐步点击显示');self.click_steps.setChecked(format=='16:9')
        self.click_steps.setEnabled(format=='16:9');form.addRow(self.click_steps)
        self.format_note=_label('当前为课堂投影；流程说明保持27磅。' if format=='16:9' else
            '当前打印画幅保留完整流程文字；课堂投影可逐步点击显示。')
        form.addRow(self.format_note)
        self.controls_scroll=page_scroll(content);self.controls_scroll.setMinimumHeight(180)
        self.controls_scroll.setMaximumHeight(410);root.addWidget(self.controls_scroll,2)
        self.scene=QGraphicsScene(self);self.view=QGraphicsView(self.scene)
        self.view.setMinimumHeight(120);self.view.setBackgroundBrush(QColor('#e8eeeb'))
        self.view.setAccessibleName('分步讲解实际课堂页预览');root.addWidget(self.view,1)
        self.status=_label('');root.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.apply_button=self.buttons.button(QDialogButtonBox.StandardButton.Ok);self.apply_button.setText('加入分步讲解')
        self.cancel_button=self.buttons.button(QDialogButtonBox.StandardButton.Cancel);self.cancel_button.setText('取消')
        root.addWidget(self.buttons)
        self.buttons.accepted.connect(self.apply);self.buttons.rejected.connect(self.reject)
        self._preview_timer=QTimer(self);self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.update_preview)
        self.step_list.currentRowChanged.connect(self.select_step)
        self.step_title.textChanged.connect(self.edited);self.description.textChanged.connect(self.edited)
        self.question_title.textChanged.connect(self.schedule_preview)
        self.direction.currentIndexChanged.connect(self.schedule_preview);self.click_steps.toggled.connect(self.schedule_preview)
        self.add_button.clicked.connect(self.add_step);self.remove_button.clicked.connect(self.remove_step)
        self.up_button.clicked.connect(lambda:self.move_step(-1));self.down_button.clicked.connect(lambda:self.move_step(1))
        self._loading=False;self.reload_steps();self.update_preview()

    def _check(self):
        if self._closed:raise ValueError('分步讲解窗口已关闭。')
        if self.guard is not None:self.guard()

    def store_step(self):
        if not self._loading and 0<=self.current<len(self.steps):
            self.steps[self.current]={'title':self.step_title.text(),'description':self.description.toPlainText()}

    def reload_steps(self):
        self._loading=True;self.step_list.clear()
        for index,step in enumerate(self.steps):
            item=QListWidgetItem(f"{index+1}. {step['title'] or '填写步骤标题'}")
            item.setToolTip(step['title']);self.step_list.addItem(item)
        self.step_list.setCurrentRow(self.current)
        self.step_title.setText(self.steps[self.current]['title'])
        self.description.setPlainText(self.steps[self.current]['description'])
        self.add_button.setEnabled(len(self.steps)<6);self.remove_button.setEnabled(len(self.steps)>2)
        self.up_button.setEnabled(self.current>0);self.down_button.setEnabled(self.current<len(self.steps)-1)
        self._loading=False

    def select_step(self,index):
        if self._loading or index<0:return
        self.store_step();self.current=index;self.reload_steps()

    def edited(self,*_):
        if self._loading:return
        self.store_step()
        item=self.step_list.item(self.current)
        if item is not None:
            item.setText(f"{self.current+1}. {self.steps[self.current]['title'] or '填写步骤标题'}")
            item.setToolTip(self.steps[self.current]['title'])
        self.schedule_preview()

    def add_step(self):
        if len(self.steps)>=6:return
        self.store_step();self.current+=1;self.steps.insert(self.current,{'title':'','description':''})
        self.reload_steps();self.schedule_preview()

    def remove_step(self):
        if len(self.steps)<=2:return
        self.store_step();self.steps.pop(self.current);self.current=min(self.current,len(self.steps)-1)
        self.reload_steps();self.schedule_preview()

    def move_step(self,direction):
        target=self.current+direction
        if not 0<=target<len(self.steps):return
        self.store_step();self.steps[self.current],self.steps[target]=self.steps[target],self.steps[self.current]
        self.current=target;self.reload_steps();self.schedule_preview()

    def schedule_preview(self,*_):
        if self._loading or self._closed:return
        self.apply_button.setEnabled(False);self._preview_timer.start(150)

    def update_preview(self):
        self._preview_timer.stop();self._candidate=None;self._layout=None;self.scene.clear()
        try:
            self._check();self.store_step()
            if self.click_steps.isChecked() and self.format!='16:9':
                raise ValueError('逐步点击用于课堂投影。请先切换课堂投影，或取消点击选项。')
            candidate=step_flow_result(self.question_title.text(),self.steps,self.direction.currentData(),
                format=self.format,click_steps=self.click_steps.isChecked())
            value=step_flow_composition(candidate)
            result=layout_composition(value,[],{},student=False)
            if result['errors']:
                raise ValueError('当前方向放不下完整流程，请选择另一方向或调整文字；已填写的完整内容和字号保留。')
            if self.format=='16:9' and len(result['pages'])!=1:
                raise ValueError('完整流程放不下一张课堂页。请调整流程方向或文字；所有步骤和原字号保留。')
            self._check()
            self._candidate=candidate
            self._layout=result;self.apply_button.setEnabled(True)
            self.status.setText(f'{len(self.steps)}个完整步骤 · {len(result["pages"])}页 · 按填写顺序呈现。')
            image=page_image(result,next(iter(result['pages'])));self.scene.addPixmap(QPixmap.fromImage(image))
            self.scene.setSceneRect(0,0,image.width(),image.height());self.fit_preview()
        except (ValueError,OSError,KeyError) as exc:
            self.apply_button.setEnabled(False);self.status.setText(str(exc))

    def fit_preview(self):
        if self._layout:self.view.fitInView(self.scene.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self,event):
        if hasattr(self,'split'):
            self.split.setOrientation(Qt.Orientation.Vertical if event.size().width()<650 else Qt.Orientation.Horizontal)
        super().resizeEvent(event)
        QTimer.singleShot(0,self.fit_preview)

    def apply(self):
        self.update_preview()
        if self._candidate is None:return
        try:self._check()
        except ValueError as exc:self.apply_button.setEnabled(False);self.status.setText(str(exc));return
        self.result_value=deepcopy(self._candidate);self._closed=True;self._preview_timer.stop();self.accept()

    def reject(self):
        self._closed=True;self._preview_timer.stop();self.result_value=None;super().reject()

    def closeEvent(self,event):
        self.reject();event.accept()
