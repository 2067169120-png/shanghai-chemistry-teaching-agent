"""Lecture context is original text, read-only, and pinned to one Word version."""
from copy import deepcopy
import pytest
from test_handout_images import lecture
from integrations.deeptutor_shchem_v1 import desktop_handout_images as handout


@pytest.fixture
def contextual_lecture(lecture):
    f = lecture
    second = deepcopy(f.value['assets'][0]); second['asset_id'] = 'word-b24-image1'; second['block_index'] = 24
    f.value['assets'].append(second)
    f.value['blocks'] = [
        {'index': 19, 'text': '知识结构网', 'label': '知识结构网'},
        {'index': 20, 'text': '', 'label': '原图'},
        {'index': 21, 'text': '分子间作用力与物质性质', 'label': '原文'},
        {'index': 23, 'text': '氢键的形成条件与溶解性', 'label': '讲解'},
        {'index': 24, 'text': '', 'label': '第二张原图'},
        {'index': 26, 'text': '原题答案保留在原文中', 'label': '答案'},
    ]
    f.value['sections'] = [{'start': 19, 'end': 26, 'title': '分子间作用力'},
                           {'start': 23, 'end': 24, 'title': '氢键与性质'}]
    return f


def test_catalog_search_labels_are_only_original_excerpts_and_specific_sections(contextual_lecture):
    f = contextual_lecture; report = handout.handout_image_options(f.facade)
    first, second = report['images']
    assert set(first) == set(f.row)
    assert report['contexts'][handout.image_key(first)]['excerpt'] == '知识结构网\n分子间作用力与物质性质'
    assert report['contexts'][handout.image_key(second)] == {
        'section': '氢键与性质', 'excerpt': '分子间作用力与物质性质\n氢键的形成条件与溶解性'}
    assert all(f.calls) and not f.facade.paths.task_root.exists()


def test_whole_lecture_context_contains_complete_text_and_exact_source_image_occurrences(contextual_lecture):
    f = contextual_lecture; result = handout.handout_image_context(f.facade, f.row)
    assert result['source'] == f.row and result['preview'] == f.value
    assert len(result['images']) == 2
    assert result['images'][1]['block_index'] == 24
    assert result['preview']['blocks'][-1]['text'] == '原题答案保留在原文中'
    result['preview']['blocks'][0]['text'] = '作品新文字'
    assert f.value['blocks'][0]['text'] == '知识结构网'
    assert not f.facade.paths.task_root.exists()


@pytest.mark.parametrize('key', ['source_sha256', 'revision'])
def test_stale_lecture_context_cannot_be_opened(contextual_lecture, key):
    f = contextual_lecture; f.value[key] = 'f' * 64
    with pytest.raises(ValueError, match='版本已变化'):
        handout.handout_image_context(f.facade, f.row)
    assert not f.facade.paths.task_root.exists()


def test_restored_location_reads_exact_version_without_rewriting_original_provenance(contextual_lecture):
    f = contextual_lecture; original = deepcopy(f.row)
    f.facade.imported_word_path = lambda *args: 'D:/恢复副本/同一原始讲义.docx'
    result = handout.handout_image_context(f.facade, original)
    assert result['source'] == original and f.row == original
    assert handout.handout_image_preview(f.facade, original)['bytes'] == f.data
    f.value['source_sha256'] = 'd' * 64
    with pytest.raises(ValueError, match='版本已变化'):
        handout.handout_image_context(f.facade, original)


def test_navigation_excerpts_omit_extractor_markers_and_large_tables_but_reader_keeps_everything(contextual_lecture):
    f = contextual_lecture
    f.value['blocks'][0]['text'] = '【待查看原文：图片或图形】知识结构网'
    f.value['blocks'][2]['text'] = '【表格开始：按原行列顺序；未标合并即未合并】\n〔第1行·第1列〕\n考点要求'
    result = handout.handout_image_context(f.facade, f.row)
    assert result['contexts'][handout.image_key(f.row)]['excerpt'] == '知识结构网'
    assert result['preview'] == f.value
    assert '【表格开始：' in result['preview']['blocks'][2]['text']


def test_restored_path_deduplicates_same_classroom_use_without_editing_old_source(contextual_lecture):
    f = contextual_lecture; result = handout.import_handout_image(f.facade, [], [], f.selection)
    old = deepcopy(result['resources'][0]); selected = deepcopy(f.selection)
    selected['source']['source_path'] = 'D:/恢复/原始讲义.docx'
    again = handout.import_handout_image(f.facade, result['image_assets'], [old], selected)
    assert again['duplicate'] and not again['resources'] and result['resources'][0] == old
