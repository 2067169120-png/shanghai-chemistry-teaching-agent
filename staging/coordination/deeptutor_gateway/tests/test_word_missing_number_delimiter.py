"""Damaged printed numbers remain reviewable source-bound complete questions."""
from copy import deepcopy
import os
import pytest

from integrations.deeptutor_shchem_v1.desktop_word_question_index import apply_question_range, index_word_questions


def preview(*texts):
    return {'source_name':'合成边界.docx','source_sha256':'a'*64,
        'revision':'synthetic-preview','extraction_revision':'synthetic',
        'blocks':[{'index':i,'text':text,'warnings':[]} for i,text in enumerate(texts,1)],'assets':[]}


def damaged_source():
    return preview('12．下列正确的是________。','A．示例甲 B．示例乙','【答案】A','【解析】上一题完整说明。',
        '13下列叙述与分子间作用力无关的是________。','A．示例气体 B．示例分子',
        'C．示例物理性质 D．示例离子晶体','【答案】D','【解析】本题完整说明。',
        '14．下列错误的是________。','A．后题选项','【答案】A')


def test_missing_delimiter_cannot_swallow_or_export_adjacent_answers():
    data=damaged_source();before=deepcopy(data)
    rows=index_word_questions(data)
    assert [r['origin_block_start'] for r in rows]==[1,5,10]
    assert rows[0]['block_end']==4 and rows[1]['block_end']==9
    assert [b['index'] for b in rows[1]['question_blocks']]==[5,6,7]
    assert [b['index'] for b in rows[1]['answer_blocks']]==[8,9]
    assert rows[1]['title'].startswith('13下列')
    assert rows[1]['boundary_status']=='needs_review' and not rows[1]['export_ready']
    assert data==before


def test_explicit_current_range_review_is_needed_and_keeps_exact_original():
    data=damaged_source();row=index_word_questions(data)[1]
    ranges={'block_start':5,'question_end':7,'answer_start':8,'block_end':9}
    unchecked=apply_question_range(data,row,**ranges)
    assert not unchecked['export_ready']
    checked=apply_question_range(data,row,**ranges,reviewed_issues=['missing_number_delimiter'])
    assert checked['export_ready'] and checked['key']==row['key']
    assert checked['boundary_review']['scope']=='selected_source_ranges_only'
    assert checked['question_blocks']==row['question_blocks'] and checked['answer_blocks']==row['answer_blocks']
    with pytest.raises(ValueError,match='确认项'):
        apply_question_range(data,index_word_questions(data)[0],block_start=1,question_end=2,
            answer_start=3,block_end=4,reviewed_issues=['missing_number_delimiter'])


@pytest.mark.parametrize('stem,following',[
    ('13 ℃下列记录用于说明条件。',['A．记录甲','【答案】A']),
    ('2025下列说法正确的是________。',['A．记录甲','【答案】A']),
    ('13下列资料是分子间作用力的分类。',['A．记录甲','【答案】A']),
    ('13下列说法正确的是________。',['普通讲解','【答案】A']),
    ('13下列说法正确的是________。',['A．记录甲','【解析】只是分析，无独立答案标记。']),
    ('13下列说法正确的是________。',['14．下列正确的是________。','A．后题选项','【答案】A']),
    ('13下列说法正确的是________。',['知识点02','A．知识列表','【答案】A']),
])
def test_missing_delimiter_requires_local_question_options_and_key(stem,following):
    assert not any(r['origin_block_start']==1 for r in index_word_questions(preview(stem,*following)))


def test_damaged_next_marker_does_not_supply_options_to_a_numbered_note():
    data=preview('1．分类说明。','13下列说法正确的是________。','A．示例甲','【答案】A')
    assert [r['origin_block_start'] for r in index_word_questions(data)]==[2]


def test_legacy_well_formed_row_revision_remains_unchanged():
    data=damaged_source();new=index_word_questions(data)[2]
    old_data=deepcopy(data);old_data['blocks'][4]['text']='原文说明，不是编号题。'
    old=index_word_questions(old_data)[1]
    assert new==old


def test_native_review_controls_require_preview_and_clear_on_range_change():
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionRangeDialog
    app=QApplication.instance() or QApplication([])
    data=damaged_source();dialog=WordQuestionRangeDialog(index_word_questions(data)[1],data)
    try:
        box=dialog.review_checks['missing_number_delimiter']
        assert not box.isEnabled() and not box.isChecked() and not dialog.save_button.isEnabled()
        dialog.preview_button.click();assert box.isEnabled() and not box.isChecked()
        box.setChecked(True);dialog.fields['block_end'].setValue(8)
        assert not box.isChecked() and not box.isEnabled() and not dialog.save_button.isEnabled()
        dialog.fields['block_end'].setValue(9);dialog.preview_button.click();box.setChecked(True)
        dialog.save_button.click()
        assert dialog.ranges['reviewed_issues']==['missing_number_delimiter']
    finally:
        dialog.deleteLater();app.processEvents()
