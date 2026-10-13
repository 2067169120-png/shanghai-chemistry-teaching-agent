"""Explicit saved-course image selection and late read-only callbacks."""
from copy import deepcopy
from types import SimpleNamespace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from test_question_explorer_ui import window
from test_composition_ui import desk
from test_desktop_studio_ui import settle
from test_composition_teaching_images import picture
from integrations.deeptutor_shchem_v1.desktop_workbench.teaching_image_picker import TeachingImagePicker
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import ResourcePicker
from integrations.deeptutor_shchem_v1 import desktop_saved_preparation_images as saved


class DeferredReads:
    def __init__(self): self.pending = []; self.cancelled = []
    def submit(self, label, operation, **callbacks):
        key = str(len(self.pending)); self.pending.append((operation, callbacks)); return key
    def finish(self, index):
        operation, callbacks = self.pending[index]; callbacks['on_success'](operation())
    def cancel(self, key): self.cancelled.append(key)


def setup_picker(desk, monkeypatch, *, restricted=False, question=False):
    d, panel, app, page = desk; data, asset = picture('green'); reads = DeferredReads()
    courses = [{'draft_id':'prep-example', 'revision':'a'*64, 'title':'分子间作用力',
                'created_at':'2026-10-06T12:00:00Z', 'image_count':1}]
    binding = {'draft_id':'prep-example', 'draft_revision':'a'*64, 'payload_sha256':'b'*64,
               'source_asset':deepcopy(asset), 'source_roles':['teacher_note'] if restricted else [], 'source_resources':[]}
    rows = [{'asset':deepcopy(asset), 'source_roles':binding['source_roles'], 'teacher_only':restricted,
             'via_question':question, 'title':'浮冰为什么浮在水面？', 'purpose':'比较水与冰的结构',
             'saved_title':courses[0]['title'], 'saved_preparation':binding}]
    calls = []
    facade = SimpleNamespace(preparation_draft_options=lambda:[], load_preparation_draft=lambda *a:None,
        preparation_image_bytes=lambda _:data, import_preparation_image=lambda *a:calls.append(a))
    monkeypatch.setattr(saved, 'saved_preparation_image_options', lambda _:deepcopy(courses))
    monkeypatch.setattr(saved, 'saved_preparation_images', lambda *a:deepcopy(rows))
    picker = TeachingImagePicker(facade, [], [], panel, tasks=reads); picker.show(); settle(app)
    return picker, reads, courses, rows, calls, app, page


def choose_course(monkeypatch):
    def choose(dialog):
        assert '1图' in dialog.list.item(0).text() and '2026-10-06' in dialog.list.item(0).text()
        assert '保存时间' in dialog.detail.toPlainText()
        dialog.list.setCurrentRow(0); dialog.choose(); return dialog.result()
    monkeypatch.setattr(ResourcePicker, 'exec', choose)


def test_explicit_course_then_full_image_preview_and_classroom_question(desk, monkeypatch):
    picker, reads, courses, rows, calls, app, page = setup_picker(desk, monkeypatch)
    original = deepcopy(page._payload()); choose_course(monkeypatch)
    picker.choose_saved(); assert not picker.saved_button.isEnabled()
    reads.finish(0); assert len(reads.pending)==2 and picker.choices==[]
    reads.finish(1); settle(app)
    assert picker.preview.has_image and picker.confirm_button.isEnabled()
    assert '已有备课' in picker.list.item(0).text()
    picker.title.setText('浮冰怎样影响水下生物？'); picker.purpose.setPlainText('先解释冰的密度，再联系水下环境')
    picker.resize(420, 850); settle(app)
    assert picker.split.orientation()==Qt.Orientation.Vertical and picker.confirm_button.isVisible()
    picker.accept_choice(); assert picker.result()==QDialog.DialogCode.Accepted
    assert picker.selection['saved_preparation']==rows[0]['saved_preparation']
    assert picker.selection['title']=='浮冰怎样影响水下生物？'
    assert calls==[] and page._payload()==original
    picker.deleteLater()


def test_course_cancel_leaves_current_image_and_draft(desk, monkeypatch):
    picker, reads, courses, rows, calls, app, page = setup_picker(desk, monkeypatch)
    before = deepcopy(page._payload())
    monkeypatch.setattr(ResourcePicker, 'exec', lambda dialog:QDialog.DialogCode.Rejected)
    picker.choose_saved(); reads.finish(0); settle(app)
    assert len(reads.pending)==1 and picker.choices==[] and picker.saved_button.isEnabled()
    picker.reject(); assert picker.selection is None and calls==[] and page._payload()==before
    picker.deleteLater()


def test_parent_cancel_during_either_read_discards_late_results(desk, monkeypatch):
    for during_images in (False, True):
        picker, reads, courses, rows, calls, app, page = setup_picker(desk, monkeypatch)
        before = deepcopy(page._payload()); choose_course(monkeypatch); picker.choose_saved()
        if during_images: reads.finish(0)
        pending = len(reads.pending)-1; picker.reject(); reads.finish(pending); settle(app)
        assert reads.cancelled==[str(pending)] and picker.choices==[] and picker.selection is None
        assert calls==[] and page._payload()==before
        picker.deleteLater()


def test_saved_answer_scope_and_complete_question_route_survive_preview(desk, monkeypatch):
    for restricted, question in ((True, False), (False, True)):
        picker, reads, courses, rows, calls, app, page = setup_picker(desk, monkeypatch, restricted=restricted, question=question)
        choose_course(monkeypatch); picker.choose_saved(); reads.finish(0); reads.finish(1); settle(app)
        assert picker.preview.has_image
        if restricted:
            assert picker.teacher_only.isChecked() and not picker.teacher_only.isEnabled()
            picker.accept_choice(); assert picker.selection['teacher_only']
        else:
            assert not picker.confirm_button.isEnabled() and '完整题' in picker.status.text()
            picker.accept_choice(); assert picker.selection is None
        assert calls==[]; picker.reject(); picker.deleteLater()


def test_saved_course_title_search_preserves_question_edits(desk, monkeypatch):
    picker, reads, courses, rows, calls, app, page = setup_picker(desk, monkeypatch)
    choose_course(monkeypatch); picker.choose_saved(); reads.finish(0); reads.finish(1)
    picker.search.setText('浮冰为什么浮在水面'); assert picker.list.count()==1
    picker.title.setText('冰与水的微观结构有什么区别？')
    picker.purpose.setPlainText('对比氢键网络，再解释密度')
    picker.search.setText('水的微观结构'); assert picker.list.count()==1
    assert picker.list.item(0).text().splitlines()[0]=='冰与水的微观结构有什么区别？'
    picker.search.setText('对比氢键网络'); assert picker.list.count()==1
    picker.search.setText('分子间作用力'); assert picker.list.count()==1
    picker.search.setText('不存在'); assert picker.list.count()==0 and not picker.confirm_button.isEnabled()
    picker.search.clear(); settle(app)
    assert picker.title.text()=='冰与水的微观结构有什么区别？'
    assert picker.purpose.toPlainText()=='对比氢键网络，再解释密度'
    assert rows[0]['title']=='浮冰为什么浮在水面？' and rows[0]['purpose']=='比较水与冰的结构'
    picker.reject(); assert calls==[]; picker.deleteLater()


def test_six_saved_images_show_distinct_questions_before_the_same_long_course(desk, monkeypatch):
    picker, reads, courses, rows, calls, app, page = setup_picker(desk, monkeypatch)
    original = deepcopy(page._payload())
    course_title = '分子间作用力与课堂问题讨论的完整备课课程名称' * 8
    courses[0].update(title=course_title, image_count=6)
    questions = ['水和冰的结构有什么区别？', '氢键如何影响沸点？', '乙醇和水怎样形成氢键？',
                 '两种晶体有什么不同？', '分子极性如何影响溶解？', '微观结构怎样解释密度？']
    template = deepcopy(rows[0]); rows.clear(); image_bytes = {}
    for color, question in zip(('green', 'red', 'blue', 'yellow', 'purple', 'cyan'), questions):
        data, asset = picture(color); image_bytes[asset['asset_id']] = data
        binding = {**deepcopy(template['saved_preparation']), 'source_asset':deepcopy(asset)}
        rows.append({**deepcopy(template), 'asset':asset, 'title':question,
                     'saved_title':course_title, 'saved_preparation':binding})
    picker.facade.preparation_image_bytes = lambda asset:image_bytes[asset['asset_id']]

    def choose(dialog):
        assert '6图' in dialog.list.item(0).text()
        dialog.list.setCurrentRow(0); dialog.choose(); return dialog.result()

    monkeypatch.setattr(ResourcePicker, 'exec', choose)
    picker.choose_saved(); reads.finish(0); reads.finish(1)
    picker.resize(1050, 900); settle(app)
    assert picker.list.count()==6 and picker.preview.has_image and picker.confirm_button.isEnabled()
    assert [picker.list.item(index).text().splitlines()[0] for index in range(6)]==questions
    for index in range(6):
        item = picker.list.item(index); bounds = picker.list.visualItemRect(item)
        assert course_title in item.toolTip() and course_title not in item.text()
        assert 0<=bounds.top() and bounds.bottom()<picker.list.viewport().height()
        assert bounds.width()<=picker.list.viewport().width()
    assert not picker.list.horizontalScrollBar().isVisible()
    picker.reject(); assert calls==[] and page._payload()==original
    picker.deleteLater()
