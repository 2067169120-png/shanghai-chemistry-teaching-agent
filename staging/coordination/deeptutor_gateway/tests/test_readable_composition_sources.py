"""Source reading stays complete, separate from work-copy edits and technical records."""
from copy import deepcopy
import json
import pytest
from integrations.deeptutor_shchem_v1.desktop_composition_source_summary import source_summary,source_snapshot_text,raw_source_text
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource,block,new_composition


def material():
    return resource('knowledge_note','原资料标题',[block('text','knowledge','首句\n'+'全文'*3000+'末句')],
                    {'record_id':'synthetic-note','pdf_pages':[1,2,4],'printed_pages':'unknown',
                     'source_sha256':'1'*64,'source_revision':'2'*64,'source_path':'本地证据'})


def test_explicit_pages_and_unknown_printed_pages_are_not_fabricated():
    item=material();frozen=deepcopy(item)
    text=source_summary(item)
    assert 'PDF文件页：1—2、4' in text and '印刷页：待核对' in text
    assert 'source_sha256' not in text and '本地证据' not in text
    assert item==frozen and json.loads(raw_source_text(item,full=True))==item['original']


def test_original_full_content_and_title_survive_work_copy_edit():
    item=material();item['title']='当前作品标题';item['blocks'][0]['value']='当前作品改文'
    text=source_snapshot_text(item)
    assert '原资料标题：原资料标题' in text and item['original']['blocks'][0]['value'] in text
    assert '当前作品改文' not in text and '末句' in text


def test_question_block_positions_do_not_invent_printed_hierarchy():
    item={'original':{'kind':'complete_question','title':'本地讲义',
        'evidence':{'formal_atomic_hierarchy':'unknown'},'blocks':[
        {'kind':'text','role':'question','source_index':225,'value':'题面全文'},
        {'kind':'text','role':'answer','source_index':226,'value':'仅教师答案全文'}]}}
    text=source_summary(item)
    assert '题面：原文区块225' in text and '教师答案与解析：原文区块226' in text
    assert '不是印刷题号' in text and '仅教师答案全文' in source_snapshot_text(item)


def test_native_collapsed_details_and_cancel_leave_draft_unchanged():
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication,QDialogButtonBox,QPlainTextEdit,QTabWidget
    from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import ResourceEditor,ResourcePicker
    app=QApplication.instance() or QApplication([])
    comp=new_composition();comp['resources']=[material()];before=deepcopy(comp)
    dialog=ResourceEditor(comp,comp['resources'][0]['id'],None);dialog.show()
    tabs=dialog.findChild(QTabWidget);tabs.setCurrentIndex(1);app.processEvents()
    assert not dialog.source_details.isVisible()
    original=tabs.widget(1).findChildren(QPlainTextEdit)[0]
    assert original.isReadOnly() and comp['resources'][0]['original']['blocks'][0]['value'] in original.toPlainText()
    dialog.source_details_button.click();app.processEvents()
    assert dialog.source_details.isVisible() and json.loads(dialog.source_details.toPlainText())==comp['resources'][0]['original']
    buttons=dialog.findChild(QDialogButtonBox);assert buttons.button(QDialogButtonBox.StandardButton.Cancel).text()=='取消'
    buttons.button(QDialogButtonBox.StandardButton.Cancel).click();assert dialog.result_value is None and comp==before
    picker=ResourcePicker([],'来源',str,None);assert picker.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel).text()=='取消'
    picker.reject();dialog.deleteLater();picker.deleteLater();app.processEvents()
