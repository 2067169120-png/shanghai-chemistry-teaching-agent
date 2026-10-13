"""Work titles stay useful without unlocking or relabeling source content."""
from copy import deepcopy
import pytest
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    resource,block,new_composition,add_resource,edit_resource,suggested_resource_title,visible_blocks)
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_composition_source_summary import source_summary


def question(stem='14．比较各气体装置的目的与条件。'):
    return resource('complete_question','同一讲义.docx · 原文区块94—101',[
        block('text','shared_material','公共材料，不能作为题目标题。',92),
        block('text','question',stem,94),
        block('text','answer','【答案】仅教师标记',100)],
        {'source_path':'同一讲义.docx','source_sha256':'a'*64,'source_revision':'b'*64,
         'question_key':'synthetic-title-question','scope_review_sha256':'c'*64,'scope_status':'complete_native_scope',
         'hierarchy':dict.fromkeys(('paper_id','theme_big_question_id','printed_question_id','atomic_part_id'),'unknown')})


def composition(item):
    value=new_composition()
    return add_resource(value,item,{'page_id':value['pages'][0],'x':12,'y':12,'width':186,'height':273})


def test_title_can_change_while_source_body_and_answer_stay_locked():
    item=question();value=composition(item);before=deepcopy(value)
    changed=edit_resource(value,item['id'],title='课堂比较：装置目的')
    current=changed['resources'][0]
    assert current['locked'] and current['title']=='课堂比较：装置目的'
    assert current['blocks']==item['blocks'] and current['original']==item['original']
    assert current['original_sha256']==item['original_sha256'] and value==before
    assert '原文区块94' in source_summary(current) and '同一讲义.docx' in source_summary(current)
    assert all('仅教师标记' not in r['value'] for r in visible_blocks(current))
    with pytest.raises(ValueError,match='锁定'):
        edit_resource(changed,item['id'],values={item['blocks'][-1]['id']:'改动答案'})
    with pytest.raises(ValueError,match='版本'):
        edit_resource(changed,item['id'],title='过期修改',expected=digest(value))


def test_source_question_navigation_does_not_use_answers_context_or_guessed_ids():
    item=question();before=deepcopy(item)
    assert suggested_resource_title(item)=='例题14'
    assert item==before and set(item['original']['evidence']['hierarchy'].values())=={'unknown'}
    item['title']='教师已经写好的标题'
    assert suggested_resource_title(item)==item['title']
    image_only=question('3．【待查看原文：图片或图形】')
    assert suggested_resource_title(image_only)=='例题3'


def test_long_opening_has_neutral_navigation_and_complete_source_remains():
    item=question('19．'+'原文条件与限制；'*30+'不能删去的尾部条件')
    before=deepcopy(item);title=suggested_resource_title(item)
    assert title == '例题19' and '原文条件与限制' not in title
    assert item==before and '不能删去的尾部条件' in item['original']['blocks'][1]['value']


def test_new_question_import_names_the_work_and_seals_full_original_source(tmp_path,monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from threading import RLock
    from hashlib import sha256
    from docx import Document
    from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_questions
    from integrations.deeptutor_shchem_v1 import desktop_word_question_attributes as attrs
    from integrations.deeptutor_shchem_v1 import offline_word_label_review as review
    doc=Document();texts=['3．比较气体的制备和收集。','末句条件，全文须保留。','【答案】仅教师标记']
    for text in texts:doc.add_paragraph(text)
    stream=BytesIO();doc.save(stream);raw=stream.getvalue()
    row={'key':'synthetic-new-import','revision':'f'*64,'source_sha256':sha256(raw).hexdigest(),
        'source_revision':'e'*64,'source_id':'synthetic-source','batch_id':'synthetic-batch','archive_source_id':'synthetic-archive',
        'source_name':'完整来源文件.docx','source_label':'完整来源文件.docx','block_start':1,'block_end':3,
        'context_blocks':[],'question_blocks':[{'text':t,'index':i,'assets':[]} for i,t in enumerate(texts[:2],1)],
        'answer_blocks':[{'text':texts[2],'index':3,'assets':[]}]}
    def resolve(selections,*,read_only):
        assert read_only
        return [row],{'synthetic-source':(SimpleNamespace(content=raw),None,None,None)}
    words=SimpleNamespace(_lock=RLock(),_resolve=resolve,attribute_store=SimpleNamespace(get=lambda _,**_binding:{}))
    facade=SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path/'tasks'),_word_questions=lambda:words,
        imported_word_path=lambda *args:tmp_path/'完整来源文件.docx')
    monkeypatch.setattr(attrs,'validate_attributes',lambda a:a)
    monkeypatch.setattr(review,'_source_blocks',lambda *a,**kw:None)
    imported=import_questions(facade,[{'key':row['key']}],[]);item=imported['resources'][0]
    assert item['title']=='例题3' and item['locked']
    assert item['original']['title']=='完整来源文件 · 原文区块1—3'
    assert [r['value'] for r in item['original']['blocks']]==texts
    assert item['blocks']==item['original']['blocks'] and item['original_sha256']==digest(item['original'])
    assert item['original']['evidence']['source_sha256']==sha256(raw).hexdigest()
    assert set(item['original']['evidence']['hierarchy'].values())=={'unknown'}
    assert not item['human_reviewed'] and not item['publication_allowed']


@pytest.mark.usefixtures('app')
def test_resource_editor_accepts_title_without_unlocking_body():
    from PySide6.QtWidgets import QApplication,QDialogButtonBox
    from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import ResourceEditor
    value=composition(question());before=deepcopy(value)
    dialog=ResourceEditor(value,value['resources'][0]['id'],None)
    assert dialog.locked.isChecked() and dialog.body.isReadOnly() and not dialog.title.isReadOnly()
    dialog.title.setText('教师的短标题')
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    assert dialog.result_value['resources'][0]['title']=='教师的短标题'
    assert dialog.result_value['resources'][0]['locked'] and value==before
    assert dialog.result_value['resources'][0]['blocks']==before['resources'][0]['blocks']
    dialog.deleteLater();QApplication.instance().processEvents()


def test_quick_rename_cancel_commit_undo_and_stale_dialog_preserve_sources(desk,monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_desktop_studio_ui import settle
    from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_composition_panel as ui
    dialog,panel,app,page=desk;item=question()
    panel.add_result({'resources':[item]},digest(dialog.history.value));panel.selected=item['id'];panel.refresh();settle(app)
    before=deepcopy(dialog.history.value);payload=deepcopy(page._payload());seen=[]
    def cancel(*args,**kwargs):seen.append(kwargs['text']);return ('cancelled',False)
    monkeypatch.setattr(ui.QInputDialog,'getText',cancel)
    QTest.mouseClick(panel.rename_button,Qt.MouseButton.LeftButton);settle(app)
    assert seen==[suggested_resource_title(item)] and dialog.history.value==before and page._payload()==payload
    monkeypatch.setattr(ui.QInputDialog,'getText',lambda *a,**kw:('装置目的对照',True))
    QTest.mouseClick(panel.rename_button,Qt.MouseButton.LeftButton);settle(app)
    renamed=panel.composition()['resources'][0]
    assert renamed['title']=='装置目的对照' and renamed['locked'] and renamed['original']==item['original']
    assert renamed['blocks']==item['blocks'] and '装置目的对照' in panel.list.item(0).text()
    assert any('装置目的对照' in panel.page_navigation.pages.itemText(i) for i in range(panel.page_navigation.pages.count()))
    from PySide6.QtWidgets import QPushButton
    undo=next(b for b in panel.findChildren(QPushButton) if b.text()=='撤销')
    QTest.mouseClick(undo,Qt.MouseButton.LeftButton);settle(app)
    assert dialog.history.value==before
    def stale(*a,**kw):
        value=deepcopy(dialog.history.value);value['composition']['resources'][0]['title']='并发保存的新标题';dialog.history.put(value)
        return ('过期标题',True)
    monkeypatch.setattr(ui.QInputDialog,'getText',stale)
    QTest.mouseClick(panel.rename_button,Qt.MouseButton.LeftButton);settle(app)
    current=panel.composition()['resources'][0]
    assert current['title']=='并发保存的新标题' and current['original']==item['original']
    assert current['blocks']==item['blocks'] and '作品在处理期间变化' in panel.status.text()


from test_composition_ui import desk,window
from test_composition_render import app
from test_composition_inline_personal import inline


def test_native_title_is_neutral_and_keeps_authored_scripts_inline_source(inline):
    value,_assets,_data,_facade,_native=inline;item=value['resources'][0];before=deepcopy(item)
    title=suggested_resource_title(item)
    assert title == '例题'
    assert '图前H_{2}O' in item['blocks'][0]['value'] and '图后Na^{+}' in item['blocks'][0]['value']
    assert '待查看原文' not in title and '仅教师答案' not in title and item==before
