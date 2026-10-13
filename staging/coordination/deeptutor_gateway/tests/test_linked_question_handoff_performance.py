"""Fresh linked guards reuse the compiled card, never recopy a full gallery."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from test_handout_material_handoff import (linked_request, images, linked,
    scoped_lecture, desktop_paths, notes, section_fixture, asset_fixture, shelf_fixture)
from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as backend
from integrations.deeptutor_shchem_v1 import desktop_knowledge_question_links as links
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionService


def gallery(facade, count=29):
    store = PreparationImageStore(facade.paths.task_root / 'preparation-v1' / 'images')
    assets = []
    for number in range(count):
        data = BytesIO(); Image.new('RGB', (28, 20), (number + 1, 92, 182)).save(data, 'PNG')
        assets.append(store.import_bytes(data.getvalue(), f'已有原图{number}', '合成原图', '已有课堂用途'))
    return assets


def test_one_native_import_one_29_picture_temporary_gallery_and_fresh_readonly_checks(linked, monkeypatch):
    facade = linked.facade; request = linked_request(linked); assets = gallery(facade)
    original_files = images(facade); original_state = facade.state_store.snapshot()
    hashes = {asset['sha256'] for asset in assets}; copies = []; imports = []; loads = []
    raw_resolves = []
    import_bytes = PreparationImageStore.import_bytes
    load = PreparationImageStore.load
    import_questions = backend.import_questions
    resolve = linked.service.words._resolve
    normal_root = facade.paths.task_root / 'preparation-v1' / 'images'
    def copied(store, data, *args, **kwargs):
        if sha256(data).hexdigest() in hashes:
            copies.append(str(store.root))
        return import_bytes(store, data, *args, **kwargs)
    def loaded(store, asset):
        if store.root == normal_root and asset['sha256'] in hashes: loads.append(asset['asset_id'])
        return load(store, asset)
    def imported(*args, **kwargs):
        imports.append(True); return import_questions(*args, **kwargs)
    def resolved(*args, **kwargs):
        raw_resolves.append(kwargs.get('read_only', False)); return resolve(*args, **kwargs)
    monkeypatch.setattr(PreparationImageStore, 'import_bytes', copied)
    monkeypatch.setattr(PreparationImageStore, 'load', loaded)
    monkeypatch.setattr(backend, 'import_questions', imported)
    monkeypatch.setattr(linked.service.words, '_resolve', resolved)
    publication = backend.prepare_linked_question(facade, request, assets, [])
    assert len(imports) == 1 and len(copies) == 29 and len(set(copies)) == 1
    assert len(loads) == 29 and images(facade) == original_files
    raw_resolves.clear()
    result = publication.publish()
    assert result['resources'][0]['original']['kind'] == 'complete_question'
    assert len(imports) == 1 and len(copies) == 29 and len(set(copies)) == 1
    assert len(loads) == 29 * 3  # prepare, actual publication before and after.
    assert raw_resolves and all(raw_resolves)  # fresh reference resolves cannot save preview caches.
    assert facade.state_store.snapshot() == original_state
    for name, original in original_files.items(): assert images(facade)[name] == original
    publication.discard(); assert images(facade) == original_files


@pytest.mark.parametrize('change', ['source', 'role', 'range', 'association', 'scope', 'reference', 'knowledge'])
def test_readonly_recheck_still_blocks_changed_source_scope_role_and_reference(linked, monkeypatch, change):
    facade = linked.facade; request = linked_request(linked)
    assets = gallery(facade, 1); old = images(facade)
    publication = backend.prepare_linked_question(facade, request, assets, [])
    if change in {'source', 'role'}:
        source = facade._imported_word_source
        def changed(*args, **kwargs):
            row = source(*args, **kwargs)
            return replace(row, content=row.content + b'changed') if change == 'source' else replace(row, role='answer')
        monkeypatch.setattr(facade, '_imported_word_source', changed)
    elif change == 'range':
        catalog = linked.service.words._catalog
        def changed(*args, **kwargs):
            rows, inventory = catalog(*args, **kwargs)
            rows = deepcopy(rows)
            next(row for row in rows['items'] if row['key'] == request['selection']['key'])['answer_start'] += 1
            return rows, inventory
        monkeypatch.setattr(linked.service.words, '_catalog', changed)
    elif change == 'association':
        linked.service.edit_reason(request['link_id'], '确认之前修改理由', expected=request['snapshot'])
    elif change == 'scope':
        scope = links._scope
        def changed(*args):
            _, native, payloads = scope(*args)
            return 'f' * 64, native, payloads
        monkeypatch.setattr(links, '_scope', changed)
    elif change == 'reference':
        compile_reference = WordQuestionService._compile_reference
        def changed(*args, **kwargs):
            result, payloads = compile_reference(*args, **kwargs)
            result['materials'] += '\n读取期间改变的参考。'
            return result, payloads
        monkeypatch.setattr(WordQuestionService, '_compile_reference', changed)
    else:
        context = links.KnowledgeQuestionLinks.knowledge_context
        def changed(*args, **kwargs):
            value = context(*args, **kwargs)
            value['endpoint']['title'] += '改变'
            return value
        monkeypatch.setattr(links.KnowledgeQuestionLinks, 'knowledge_context', changed)
    with pytest.raises(ValueError): publication.publish()
    assert images(facade) == old


@pytest.mark.parametrize('when', ['before', 'during'])
def test_old_gallery_damage_still_blocks_and_new_owned_pictures_roll_back(linked, monkeypatch, when):
    facade = linked.facade; request = linked_request(linked); assets = gallery(facade, 1)
    publication = backend.prepare_linked_question(facade, request, assets, [])
    root = facade.paths.task_root / 'preparation-v1' / 'images'
    old_path = root / (assets[0]['sha256'] + '.image')
    if when == 'before': old_path.write_bytes(b'external damage')
    else:
        native_link = backend.os.link
        def damage(source, target):
            value = native_link(source, target)
            if Path(target).parent == root: old_path.write_bytes(b'external damage')
            return value
        monkeypatch.setattr(backend.os, 'link', damage)
    with pytest.raises(ValueError): publication.publish()
    assert old_path.read_bytes() == b'external damage'
    assert set(images(facade)) == {old_path.name}


def test_linked_image_rollback_keeps_another_successful_owner(linked, monkeypatch):
    facade = linked.facade; request = linked_request(linked); assets = gallery(facade, 1)
    old = images(facade); publication = backend.prepare_linked_question(facade, request, assets, [])
    root = facade.paths.task_root / 'preparation-v1' / 'images'
    native_link = backend.os.link; concurrent = []
    def other_wins(source, target):
        if Path(target).parent == root and not concurrent:
            Path(target).write_bytes(Path(source).read_bytes()); concurrent.append(Path(target))
            raise FileExistsError('another successful image import won')
        return native_link(source, target)
    monkeypatch.setattr(backend.os, 'link', other_wins)
    publication.publish(); publication.discard()
    assert set(images(facade)) == set(old) | {concurrent[0].name}
    for name, original in old.items(): assert images(facade)[name] == original


def test_final_readonly_reference_guard_rolls_back_after_copy(linked, monkeypatch):
    facade = linked.facade; request = linked_request(linked); assets = gallery(facade, 1)
    old = images(facade); publication = backend.prepare_linked_question(facade, request, assets, [])
    compile_reference = WordQuestionService._compile_reference; calls = []
    def changed(*args, **kwargs):
        result, payloads = compile_reference(*args, **kwargs); calls.append(True)
        if len(calls) == 2: result['materials'] += '\n末端改变参考。'
        return result, payloads
    monkeypatch.setattr(WordQuestionService, '_compile_reference', changed)
    with pytest.raises(ValueError): publication.publish()
    assert len(calls) == 2 and images(facade) == old


def test_knowledge_change_during_last_reference_read_is_not_published(linked, monkeypatch):
    facade = linked.facade; request = linked_request(linked); assets = gallery(facade, 1)
    old = images(facade); publication = backend.prepare_linked_question(facade, request, assets, [])
    reference = backend._ReadOnlyReferenceWords.reference; context = links.KnowledgeQuestionLinks.knowledge_context
    calls = []; changed = []
    def reference_changed(*args, **kwargs):
        result = reference(*args, **kwargs); calls.append(True)
        if len(calls) == 2: changed.append(True)
        return result
    def knowledge_changed(*args, **kwargs):
        result = context(*args, **kwargs)
        if changed: result['endpoint']['title'] += '原件变化'
        return result
    monkeypatch.setattr(backend._ReadOnlyReferenceWords, 'reference', reference_changed)
    monkeypatch.setattr(links.KnowledgeQuestionLinks, 'knowledge_context', knowledge_changed)
    with pytest.raises(ValueError): publication.publish()
    assert len(calls) == 2 and images(facade) == old


@pytest.mark.parametrize('change', ['source', 'role', 'range'])
def test_question_change_during_final_knowledge_read_is_not_published(linked, monkeypatch, change):
    facade = linked.facade; request = linked_request(linked); assets = gallery(facade, 1)
    old = images(facade); publication = backend.prepare_linked_question(facade, request, assets, [])
    context = links.KnowledgeQuestionLinks.knowledge_context; source = facade._imported_word_source
    calls = []
    def changed(*args, **kwargs):
        result = context(*args, **kwargs); calls.append(True)
        if len(calls) == 4:  # Final publication guard: knowledge after fresh Word reference.
            if change == 'range':
                from integrations.deeptutor_shchem_v1.desktop_word_questions import RANGES_DRAFT
                facade.state_store.save_draft(RANGES_DRAFT, {'ranges': {
                    request['selection']['key']: {'source_revision': 'f' * 64, 'range': {}}}})
            else:
                def altered(*a, **kw):
                    row = source(*a, **kw)
                    return replace(row, content=row.content + b'changed') if change == 'source' else replace(row, role='answer')
                monkeypatch.setattr(facade, '_imported_word_source', altered)
        return result
    monkeypatch.setattr(links.KnowledgeQuestionLinks, 'knowledge_context', changed)
    with pytest.raises(ValueError): publication.publish()
    assert len(calls) == 4 and images(facade) == old
