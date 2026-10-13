"""Teacher/student scope, editing and recovery in the existing lesson editor."""
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect
from test_question_explorer_ui import window
from test_lesson_design_ui import desk
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_recovery import apply_editor_payload
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_node_preview import LessonNodePreview
from integrations.deeptutor_shchem_v1.desktop_lesson_design import new_design, new_node


def test_live_student_scope_excludes_teacher_fields_and_literal_html(desk):
    d, app, page = desk
    p = d.content_preview
    d.student_material.setChecked(False)
    d.fields['teacher_answer'].setPlainText('只给教师的答案标记')
    d.fields['notes'].setPlainText('私人课堂备注标记')
    d.fields['teacher_action'].setPlainText('教师活动标记')
    d.fields['material_text'].setPlainText('<img src="file:///private.png">原题全部条件')
    d.fields['student_task'].setPlainText('学生任务末尾标记')
    settle(app)
    text = p.browser.toPlainText()
    assert '学生任务末尾标记' in text
    assert all(s not in text for s in ('只给教师', '私人课堂', '教师活动标记', '原题全部条件'))
    d.student_material.setChecked(True)
    assert '<img src="file:///private.png">原题全部条件' in p.browser.toPlainText()
    assert 'src="file:///private.png"' not in p.browser.toHtml()
    p.role.setCurrentIndex(p.role.findData(False))
    assert all(s in p.browser.toPlainText() for s in ('只给教师的答案标记', '私人课堂备注标记', '教师活动标记'))
    p.role.setCurrentIndex(p.role.findData(True))
    assert '只给教师的答案标记' not in p.browser.toPlainText()
    assert not p.browser.images


def test_full_material_reorder_undo_and_save_reopen(desk):
    d, app, page = desk
    original = page._payload()
    first = d.current
    text = '完整公共条件\n' + '题面条件；' * 150 + '\n完整末尾标记'
    d.fields['material_text'].setPlainText(text)
    d.student_material.setChecked(True)
    assert '完整末尾标记' in d.content_preview.browser.toPlainText()
    d.add_node()
    second = d.current
    d.fields['student_task'].setPlainText('第二环节独立任务')
    d.move_node(-1)
    assert d.content_preview.browser.toPlainText().startswith('1.')
    d.travel(False)
    assert [n['id'] for n in d.history.value['nodes']][-1] == second
    d.nodes.setCurrentRow(0)
    assert d.current == first and '完整末尾标记' in d.content_preview.browser.toPlainText()
    d.save.click()
    settle(app, lambda: not d.busy)
    assert '已保存' in d.status.text()
    saved = page._payload()
    assert saved['materials'] == original['materials']
    assert page.recovery.flush()
    recovered = page.recovery.store.load()['payload']
    assert recovered['lesson_design'] == saved['lesson_design']
    d.reject()
    apply_editor_payload(page, recovered)
    reopened = LessonDesignDialog(page)
    reopened.show()
    settle(app)
    try:
        assert reopened.history.value == saved['lesson_design']
        assert '完整末尾标记' in reopened.content_preview.browser.toPlainText()
    finally:
        reopened.reject()
        reopened.deleteLater()


def test_preview_moves_between_same_screen_and_compact_tab(desk):
    d, app, page = desk
    p = d.content_preview
    p.role.setCurrentIndex(p.role.findData(False))
    baseline = page._payload()
    d.resize(1500, 900)
    settle(app)
    assert d.main_splitter.count() == 3 and p.isVisible()
    assert d.tabs.indexOf(d.preview_page) < 0
    d.resize(780, 800)
    settle(app)
    index = d.tabs.indexOf(d.preview_page)
    assert index >= 0 and d.main_splitter.count() == 2
    d.tabs.setCurrentIndex(index)
    settle(app)
    assert p.isVisible() and p.browser.height() > 300
    assert p.role.currentData() is False
    for widget in (d.save, d.generate, d.preview, d.return_button, p.browser):
        assert d.rect().contains(QRect(widget.mapTo(d, widget.rect().topLeft()), widget.size()))
    d.resize(1500, 900)
    d.tabs.setCurrentIndex(0)
    settle(app)
    assert d.content_preview is p and p.isVisible()
    assert page._payload() == baseline


def test_student_hidden_image_is_not_read_and_failure_replaces_old_preview(desk):
    from io import BytesIO
    from PIL import Image
    d, app, page = desk
    stream = BytesIO()
    Image.new('RGB', (80, 60), 'green').save(stream, format='PNG')
    raw = stream.getvalue()
    from hashlib import sha256
    digest = sha256(raw).hexdigest()
    asset = {'asset_id': 'IMG-' + digest, 'sha256': digest, 'caption': '本地图片',
             'source': '测试原创图片', 'purpose': '观察', 'width': 80, 'height': 60,
             'content_type': 'image/png'}
    calls = []
    class Images:
        failed = False
        def preparation_image_bytes(self, a):
            calls.append(a['asset_id'])
            if self.failed:
                raise ValueError('原图摘要失配')
            return raw
    facade = Images()
    p = LessonNodePreview(facade)
    n = new_node()
    n.update(image_ids=[asset['asset_id']], student_task='观察任务')
    plan = new_design({})
    plan['nodes'].append(n)
    payload = {'lesson_design': plan, 'image_assets': [asset]}
    p.set_payload(payload, n['id'])
    assert calls == [] and not p.browser.images
    p.role.setCurrentIndex(p.role.findData(False))
    assert len(calls) == 1 and len(p.browser.images) == 1
    facade.failed = True
    p.reload_images()
    assert '原图摘要失配' in p.notice.text() and not p.browser.images
    p.role.setCurrentIndex(p.role.findData(True))
    assert '本地图片' not in p.browser.toPlainText() and not p.browser.images
    p.clear()
    assert not p._cache
    p.deleteLater()


def test_empty_and_removed_node_does_not_retain_previous_contents(desk):
    d, app, page = desk
    d.fields['student_task'].setPlainText('待移除环节标记')
    d.delete_node()
    settle(app)
    assert '待移除环节标记' not in d.content_preview.browser.toPlainText()
    assert d.save.isEnabled()


def test_visible_text_matches_actual_docx_scope(desk, tmp_path):
    from docx import Document
    from integrations.deeptutor_shchem_v1.desktop_lesson_output import write_documents
    from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_node_preview import node_content
    d, app, page = desk
    d.student_material.setChecked(True)
    d.fields['material_text'].setPlainText('共同题目条件\n材料末尾标记')
    d.fields['teacher_answer'].setPlainText('教师专用解答标记')
    d.fields['notes'].setPlainText('教师专用备注标记')
    payload = page._payload()
    write_documents(payload, tmp_path, {})
    for student, name in ((True, 'student_worksheet.docx'), (False, 'lesson_plan.docx')):
        paragraphs = '\n'.join(p.text for p in Document(tmp_path / name).paragraphs)
        content = node_content(payload, d.current, student)
        assert all(block['text'] in paragraphs for block in content['blocks'])
        assert ('教师专用解答标记' in paragraphs) is (not student)
        assert ('教师专用备注标记' in paragraphs) is (not student)
        assert '材料末尾标记' in paragraphs
