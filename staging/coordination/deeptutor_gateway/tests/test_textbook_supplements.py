"""Declared supplement identity, version changes and unchanged concept scope."""
from copy import deepcopy
import hashlib
import json

import pytest

from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, disk_bytes
from integrations.deeptutor_shchem_v1 import desktop_textbook_asset_catalog as catalog
from integrations.deeptutor_shchem_v1 import desktop_textbook_supplements as supplements
from integrations.deeptutor_shchem_v1.desktop_textbook_bookshelf import TextbookBookshelfError


@pytest.fixture
def supplement_fixture(shelf_fixture):
    f = shelf_fixture
    gate = {'candidate_only': True, 'counted_in_60': False,
            **{name: False for name in supplements.metadata._CLOSED_FLAGS}}
    node = {**gate, 'node_key': 'declared-project', 'node_type': 'project_activity',
            'title': '合成项目：测量示例', 'volume_id': f.row['volume_id'],
            'chapter_id': f.row['chapter_id'], 'parent_node_id': f.row['chapter_id'],
            'source_path': f.row['source_path'], 'source_sha256': f.row['source_sha256'],
            'pdf_pages': [43, 45], 'pdf_page_list': [43, 44, 45], 'printed_pages': [38, 40]}
    appendix = {**node, 'node_key': 'declared-appendix', 'node_type': 'appendix',
                'title': '合成附录', 'chapter_id': None, 'parent_node_id': f.row['volume_id'],
                'pdf_pages': [128, 128], 'pdf_page_list': [128], 'printed_pages': []}
    f.product = {**gate, 'volume_count': 5, 'chapter_count': 19, 'section_count': 60,
                 'source_registry_sha256': hashlib.sha256(f.registry_path.read_bytes()).hexdigest(),
                 'supplement_nodes': [node, appendix]}
    f.supplement_path = f.root / supplements.PRODUCT_PATH
    f.write_product = lambda: f.supplement_path.write_text(json.dumps(f.product), encoding='utf-8')
    f.write_product()
    f.row.update(section_key=None, supplement_node_key=node['node_key'], pdf_pages=[43, 45])
    f.catalog.write_text(json.dumps(f.row), encoding='utf-8')
    f.asset.update(section_key=None, supplement_node_key=node['node_key'], pdf_page=44, printed_page=None)
    f.write_assets([f.asset])
    return f


def selected(f, key='declared-project'):
    return {field: row[field] for row in f.shelf.snapshot()['supplements']
            if row['supplement_node_key'] == key for field in ('supplement_node_key', 'revision')}


def test_grouping_listing_never_opens_pdf_or_writes_concept(supplement_fixture, monkeypatch):
    f = supplement_fixture
    before = disk_bytes(f.root)
    monkeypatch.setattr(catalog, '_read_pdf', lambda *_: pytest.fail('listing opened PDF'))
    result = f.shelf.snapshot()
    assert len(result['sections']) == 60 and len(result['supplements']) == 2
    assert result['unmapped_concept_count'] == 0 and result['supplement_concept_count'] == 1
    concept = result['concepts'][0]
    assert concept['section_key'] is None and concept['pdf_pages'] == [43, 45]
    assert concept['revision'] == f.service.concept_options()[0]['revision']
    assert concept['resolved_supplement_node_key'] == 'declared-project'
    assert result['assets'][0]['printed_page'] is None
    assert result['assets'][0]['resolved_supplement_node_key'] == 'declared-project'
    assert disk_bytes(f.root) == before


def test_open_project_keeps_concept_two_pages_and_uses_three_declared_pages(supplement_fixture):
    f = supplement_fixture
    before = disk_bytes(f.root)
    identity = {'concept_id': f.row['concept_id'], 'revision': f.service.concept_options()[0]['revision']}
    result = f.shelf.supplement_source(**selected(f))
    assert result['reading_mode'] == 'supplement' and result['pdf_pages'] == [43, 44, 45]
    assert result['printed_pages'] == [38, 39, 40] and result['pdf_bytes'] == f.book.read_bytes()
    assert all(result[name] is False for name in supplements.metadata._CLOSED_FLAGS)
    assert result['visual_assets']['assets'][0]['printed_page'] is None
    assert f.service.textbook_source(**identity)['pdf_pages'] == [43, 45]
    reference = f.service.reference(None, None, 1, 1, [identity])
    assert 'PDF文件页序：[43, 45]' in reference['materials']
    assert disk_bytes(f.root) == before


def test_volume_appendix_unknown_printed_page_and_no_parent_guess(supplement_fixture):
    f = supplement_fixture
    f.asset.update(supplement_node_key='declared-appendix', chapter_id=None, pdf_page=128)
    f.write_assets([f.asset])
    result = f.shelf.supplement_source(**selected(f, 'declared-appendix'))
    assert result['chapter_id'] is None and result['printed_pages'] == []
    assert result['pdf_pages'] == [128] and len(result['visual_assets']['assets']) == 1
    assert result['visual_assets']['assets'][0]['chapter_id'] is None


@pytest.mark.parametrize('field,value', [
    ('source_sha256', '0'*64), ('source_path', '../outside.pdf'), ('parent_node_id', 'TB-M1'),
    ('chapter_id', 'TB-M1-C1'), ('pdf_page_list', [43, 43]), ('pdf_page_list', [True]),
    ('pdf_page_list', [45, 43]), ('printed_pages', [38, 39]), ('counted_in_60', True),
    ('human_reviewed', True), ('generation_allowed', True), ('publication_allowed', True),
    ('node_key', '@unmapped'), ('node_key', '@supplement'), ('pdf_pages', [43, 44]),
])
def test_bad_node_quarantined_without_losing_formal_sections(supplement_fixture, field, value):
    f = supplement_fixture
    old = selected(f)
    f.product['supplement_nodes'][0][field] = value
    f.write_product()
    result = f.shelf.snapshot()
    assert len(result['sections']) == 60 and len(result['supplements']) == 1
    assert result['unmapped_concept_count'] == 1 and result['notices']
    with pytest.raises(TextbookBookshelfError):
        f.shelf.supplement_source(**old)


def test_duplicate_nodes_both_rejected(supplement_fixture):
    f = supplement_fixture
    f.product['supplement_nodes'].append(deepcopy(f.product['supplement_nodes'][0]))
    f.write_product()
    assert [row['supplement_node_key'] for row in f.shelf.snapshot()['supplements']] == ['declared-appendix']


@pytest.mark.parametrize('change', ['product', 'active', 'pdf', 'missing_product'])
def test_saved_selection_rechecked_for_each_request(supplement_fixture, change):
    f = supplement_fixture
    identity = selected(f)
    if change == 'product':
        f.supplement_path.write_bytes(f.supplement_path.read_bytes() + b' ')
    elif change == 'active':
        f.registry['nodes'][0]['section_title'] += ' edited'
        f.activate()
        f.product['source_registry_sha256'] = hashlib.sha256(f.registry_path.read_bytes()).hexdigest()
        f.write_product()
    elif change == 'pdf':
        f.book.write_bytes(f.book.read_bytes() + b' ')
    else:
        f.supplement_path.unlink()
    with pytest.raises(TextbookBookshelfError):
        f.shelf.supplement_source(**identity)


def test_product_change_during_pdf_read_is_rejected(supplement_fixture, monkeypatch):
    f = supplement_fixture
    identity = selected(f)
    original = catalog._read_pdf
    def changed(*args):
        data = original(*args)
        f.supplement_path.write_bytes(f.supplement_path.read_bytes() + b' ')
        return data
    monkeypatch.setattr(catalog, '_read_pdf', changed)
    with pytest.raises(TextbookBookshelfError):
        f.shelf.supplement_source(**identity)


@pytest.mark.parametrize('field,value', [('source_sha256', '0'*64), ('chapter_id', 'TB-M1-C1'),
                                      ('pdf_pages', [42]), ('section_key', 'not-a-section')])
def test_conflicting_concept_stays_unmapped(supplement_fixture, field, value):
    f = supplement_fixture
    f.row[field] = value
    f.catalog.write_text(json.dumps(f.row), encoding='utf-8')
    snapshot = f.shelf.snapshot()
    assert snapshot['unmapped_concept_count'] == 1 and snapshot['supplement_concept_count'] == 0


def test_unbound_product_keeps_all_concepts_and_sections(supplement_fixture):
    f = supplement_fixture
    f.product['source_registry_sha256'] = '0'*64
    f.write_product()
    result = f.shelf.snapshot()
    assert len(result['sections']) == 60 and not result['supplements'] and result['concepts']
    assert result['unmapped_concept_count'] == 1 and result['notices']
