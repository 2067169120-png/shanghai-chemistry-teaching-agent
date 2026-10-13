"""Minimal native composition owner tests the section material commit boundary.

No MainWindow, recovery service or state store is constructed. Source services,
modal choices and publication are controlled; the Panel, history, arrangement,
image verification and frozen insertion are the product implementations.
"""
import os
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QVBoxLayout

from test_composition_insertion import fixture_compositions
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    block, enable_composition, resource, resource_frames,
)
from integrations.deeptutor_shchem_v1.desktop_lesson_design import DesignHistory, digest, new_design
from integrations.deeptutor_shchem_v1.desktop_preparation_images import image_info, normalize_image_assets
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import LessonCompositionPanel


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


class Assets:
    def __init__(self):
        self.value = []

    def assets(self):
        return deepcopy(self.value)

    def set_assets(self, rows):
        self.value = deepcopy(rows)


class RunQueue:
    def __init__(self):
        self.jobs = []

    def run(self, title, operation, ready, **kwargs):
        self.jobs.append({'title': title, 'operation': operation, 'ready': ready, 'options': kwargs})

    def calculate(self, index):
        return self.jobs[index]['operation']()

    def deliver(self, index, result):
        return self.jobs[index]['ready'](result)

    def finish(self, index):
        return self.deliver(index, self.calculate(index))


def knowledge(title, record, *, long=False):
    return resource('knowledge_note', title,
        [block('text', 'knowledge', ('完整定义、全部条件与最后结论。\n' * 42) if long else '原有完整知识讲解。')],
        {'source_path': '合成教材.pdf', 'source_sha256': 'a' * 64, 'source_revision': 'b' * 64,
         'record_id': record, 'pdf_pages': [8], 'printed_pages': [4]})


def picture():
    stream = BytesIO()
    Image.new('RGB', (260, 130), '#4f83a4').save(stream, format='PNG')
    data = stream.getvalue()
    hashed = sha256(data).hexdigest()
    asset = normalize_image_assets([{'asset_id': 'IMG-' + hashed, 'sha256': hashed,
        'caption': '合成教材裁片', 'source': '合成原书', 'purpose': '对照完整知识讲解',
        **image_info(data)}])[0]
    item = resource('textbook_image', asset['caption'], [block('image', 'material', asset['asset_id'])],
        {'source_path': '合成教材.pdf', 'source_sha256': 'a' * 64, 'source_revision': 'c' * 64,
         'pdf_pages': [8], 'printed_pages': [4]})
    return item, asset, data


class Publication:
    """A discard really removes an unaccepted image, making ownership observable."""
    def __init__(self, folder, source_state):
        crop, self.asset, self.data = picture()
        self.result = {'resources': [knowledge('新选知识全文', 'NEW-KNOWLEDGE', long=True), crop],
                       'image_assets': [self.asset]}
        self.path = folder / 'published-section-image.png'
        self.source_state = source_state
        self.events = []
        self.accepted = self.published = False
        self.destructive_discards = 0

    def publish(self, guard):
        self.events.append('publish')
        guard()
        if not self.source_state['current']:
            raise ValueError('原教材来源版本已变化')
        self.path.write_bytes(self.data)
        self.published = True
        return deepcopy(self.result)

    def accept(self):
        self.events.append('accept')
        self.accepted = True

    def discard(self):
        self.events.append('discard')
        if not self.accepted and self.path.exists():
            self.path.unlink()
            self.destructive_discards += 1


@pytest.fixture
def panel_desk(app, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as context_api
    from integrations.deeptutor_shchem_v1 import desktop_section_material_handoff as handoff_api
    from integrations.deeptutor_shchem_v1.desktop_workbench import section_materials_dialog as dialog_api
    old, _, previous_anchor = fixture_compositions()
    source = knowledge('已读原知识', 'SOURCE-KNOWLEDGE')
    old['resources'][1] = source
    old['placements'][source['id']] = old['placements'].pop(previous_anchor)
    placement = old['placements'][source['id']]
    old['placements'][source['id']] = {**placement['continuations'][0],
        'continuations': [{key: value for key, value in placement.items() if key != 'continuations'}]}
    old['placements'][source['id']]['width'] = old['placements'][source['id']]['continuations'][0]['width']
    plan = enable_composition(new_design({'objective': '完整课堂知识与原图对照'}))
    plan['composition'] = old
    state = {'current': True, 'cancel_dialog': False, 'opened': [], 'prepared': [], 'contexts': []}
    publication = Publication(tmp_path, state)
    queue = RunQueue()
    owner = SimpleNamespace(history=DesignHistory(plan), status=QLabel(), run=queue.run,
        tasks=SimpleNamespace(is_shutting_down=False), _stopping=False, _close_when_finished=False,
        facade=SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path / 'tasks')),
        page=SimpleNamespace(image_assets_widget=Assets(), topic=QLineEdit('原有课堂主题')))
    owner.facade.preparation_image_bytes = lambda asset: publication.path.read_bytes()
    window = QDialog()
    window.resize(1360, 900)
    layout = QVBoxLayout(window)
    panel = LessonCompositionPanel(owner)
    layout.addWidget(panel)
    def sync():
        panel.set_payload({'lesson_design': owner.history.value,
                           'image_assets': owner.page.image_assets_widget.assets()})
    owner.sync_page = sync
    owner.travel = lambda redo=False: (owner.history.travel(redo), sync())
    sync()
    panel.selected = source['id']
    panel.refresh()
    window.show()
    app.processEvents()
    context = {'resource_id': source['id'], 'binding_sha256': 'd' * 64,
               'book_label': '真实来源替身', 'section_label': '来源绑定章节'}
    request = {'kind': 'textbook_images', 'context': deepcopy(context),
               'knowledge_selection': {'note_id': 'NEW-KNOWLEDGE'}}
    def read_context(facade, item, rows, assets):
        state['contexts'].append((deepcopy(item), deepcopy(rows), deepcopy(assets)))
        if not state['current']:
            raise ValueError('原教材来源版本已变化')
        return deepcopy(context)
    def prepare(facade, item, rows, assets, selected):
        state['prepared'].append((deepcopy(item), deepcopy(rows), deepcopy(assets), deepcopy(selected)))
        if not state['current']:
            raise ValueError('原教材来源版本已变化')
        return publication
    class Picker(QDialog):
        def __init__(self, facade, tasks, current_context, parent, *, guard, course_resources, image_assets):
            super().__init__(parent)
            guard()
            state['opened'].append({'context': deepcopy(current_context), 'rows': deepcopy(course_resources),
                                    'assets': deepcopy(image_assets), 'guard': guard})
            self.request = deepcopy(request)
        def exec(self):
            return self.DialogCode.Rejected if state['cancel_dialog'] else self.DialogCode.Accepted
    monkeypatch.setattr(context_api, 'section_material_context', read_context)
    monkeypatch.setattr(handoff_api, 'prepare_section_material_request', prepare)
    monkeypatch.setattr(dialog_api, 'SectionMaterialsDialog', Picker)
    yield SimpleNamespace(owner=owner, panel=panel, window=window, app=app, queue=queue,
        source=source, state=state, publication=publication, context=context, request=request,
        monkeypatch=monkeypatch, sync=sync)
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def start(s, mode='after'):
    s.panel.insertion_position.setCurrentIndex(s.panel.insertion_position.findData(mode))
    insertion = s.panel.freeze_insertion()
    before = deepcopy(s.owner.history.value)
    s.panel.choose_section_materials(deepcopy(s.source), s.owner.page.image_assets_widget.assets(),
                                    digest(before), insertion)
    return before, insertion


def assert_preserved(before, after):
    old, new = before['composition'], after['composition']
    old_ids = {row['id'] for row in old['resources']}
    assert [row for row in new['resources'] if row['id'] in old_ids] == old['resources']
    assert {key: new['placements'][key] for key in old['placements']} == old['placements']
    assert [page for page in new['pages'] if page in old['pages']] == old['pages']
    assert {key: value for key, value in after.items() if key != 'composition'} == {
        key: value for key, value in before.items() if key != 'composition'}


@pytest.mark.parametrize('mode,resource_index,page_index', [('before', 1, 1), ('after', 2, 3), ('end', 5, 5)])
def test_confirmed_section_batch_uses_frozen_position_and_one_undo(panel_desk, mode, resource_index, page_index):
    s = panel_desk
    before, insertion = start(s, mode)
    assert not s.publication.events
    s.queue.finish(0)
    assert len(s.queue.jobs) == 2
    assert s.state['opened'][0]['context'] == s.context
    assert s.state['opened'][0]['rows'] == before['composition']['resources']
    assert s.owner.history.value == before
    s.queue.finish(1)
    after = deepcopy(s.owner.history.value)
    assert_preserved(before, after)
    incoming = s.publication.result['resources']
    assert after['composition']['resources'][resource_index:resource_index + 2] == incoming
    added_pages = [p for p in after['composition']['pages'] if p not in before['composition']['pages']]
    assert after['composition']['pages'][page_index:page_index + len(added_pages)] == added_pages
    assert len(resource_frames(after['composition']['placements'][incoming[0]['id']])) > 1
    assert insertion['anchor_resource_id'] == s.source['id']
    assert len(s.owner.history.back) == 1
    assert not s.panel.layout_result['errors']
    assert s.publication.accepted and not s.publication.destructive_discards
    assert s.publication.path.read_bytes() == s.publication.data
    assert all(job['options']['cancellable'] for job in s.queue.jobs)
    s.owner.travel(False)
    assert s.owner.history.value == before
    s.owner.travel(True)
    assert s.owner.history.value == after


def test_cancelled_section_picker_does_not_prepare_publish_or_change_history(panel_desk):
    s = panel_desk
    s.state['cancel_dialog'] = True
    before, _ = start(s)
    s.queue.finish(0)
    assert len(s.queue.jobs) == 1
    assert not s.state['prepared'] and not s.publication.events
    assert s.owner.history.value == before and not s.owner.history.back
    assert s.owner.page.image_assets_widget.assets() == []


@pytest.mark.parametrize('phase', ['context', 'prepare', 'publish'])
def test_changed_original_source_never_inserts_a_section_batch(panel_desk, phase):
    s = panel_desk
    before, _ = start(s)
    if phase == 'context':
        s.state['current'] = False
        with pytest.raises(ValueError, match='来源版本已变化'):
            s.queue.calculate(0)
        assert not s.state['opened']
    else:
        s.queue.finish(0)
        publication = s.queue.calculate(1) if phase == 'publish' else None
        s.state['current'] = False
        if phase == 'prepare':
            with pytest.raises(ValueError, match='来源版本已变化'):
                s.queue.calculate(1)
        else:
            s.queue.deliver(1, publication)
            assert '来源版本已变化' in s.owner.status.text()
    assert s.owner.history.value == before and not s.owner.history.back
    assert s.owner.page.image_assets_widget.assets() == []
    assert not s.publication.path.exists() and not s.publication.accepted


@pytest.mark.parametrize('change', ['position', 'selection', 'history', 'assets', 'new_action', 'closing', 'shutdown'])
def test_late_prepared_publication_cannot_overwrite_changed_destination(panel_desk, change):
    s = panel_desk
    start(s)
    s.queue.finish(0)
    publication = s.queue.calculate(1)
    if change == 'position':
        s.panel.insertion_position.setCurrentIndex(s.panel.insertion_position.findData('before'))
    elif change == 'selection':
        s.panel.selected = s.panel.composition()['resources'][-1]['id']
    elif change == 'history':
        changed = deepcopy(s.owner.history.value)
        changed['objectives'][0]['text'] += '；阅读期间新编辑'
        s.owner.history.put(changed)
    elif change == 'assets':
        s.owner.page.image_assets_widget.set_assets([s.publication.asset])
    elif change == 'new_action':
        s.panel.freeze_insertion()
    elif change == 'closing':
        s.owner._close_when_finished = True
    else:
        s.owner.tasks.is_shutting_down = True
    before, back = deepcopy(s.owner.history.value), deepcopy(s.owner.history.back)
    assets = s.owner.page.image_assets_widget.assets()
    s.queue.deliver(1, publication)
    assert s.owner.history.value == before and s.owner.history.back == back
    assert s.owner.page.image_assets_widget.assets() == assets
    assert not publication.published and not publication.accepted
    assert not publication.path.exists()


def test_runtime_refresh_error_after_history_commit_accepts_referenced_image_before_discard(panel_desk):
    s = panel_desk
    before, _ = start(s)
    s.queue.finish(0)
    publication = s.queue.calculate(1)
    def failed_sync():
        # This boundary is after DesignHistory.put inside the real Panel.commit.
        assert len(s.owner.history.back) == 1
        raise RuntimeError('页面同步失败，但历史已经完成写入')
    s.owner.sync_page = failed_sync
    callback_error = None
    try:
        s.queue.deliver(1, publication)
    except RuntimeError as exc:
        callback_error = exc
    after = deepcopy(s.owner.history.value)
    assert_preserved(before, after)
    incoming = publication.result['resources']
    assert after['composition']['resources'][2:4] == incoming
    assert s.owner.page.image_assets_widget.assets() == [publication.asset]
    assert publication.accepted, 'Committed resources must own their image even when UI refresh raises RuntimeError.'
    assert publication.path.read_bytes() == publication.data
    assert publication.destructive_discards == 0
    if 'discard' in publication.events:
        assert publication.events.index('accept') < publication.events.index('discard')
    assert callback_error is None
    assert '页面同步失败' in s.owner.status.text()
    assert len(s.owner.history.back) == 1
    s.owner.history.travel(False)
    assert s.owner.history.value == before
    assert publication.path.exists(), 'Undo keeps the accepted image available for redo.'


def test_runtime_error_before_history_commit_discards_only_unreferenced_published_image(panel_desk):
    s = panel_desk
    before, _ = start(s)
    s.queue.finish(0)
    publication = s.queue.calculate(1)
    def fail_before_commit(*args, **kwargs):
        raise RuntimeError('插入在历史写入前失败')
    s.monkeypatch.setattr(s.panel, 'add_result', fail_before_commit)
    s.queue.deliver(1, publication)
    assert publication.published and not publication.accepted
    assert publication.destructive_discards == 1 and not publication.path.exists()
    assert s.owner.history.value == before and not s.owner.history.back
    assert s.owner.page.image_assets_widget.assets() == []
    assert '历史写入前失败' in s.owner.status.text()


@pytest.mark.parametrize('intent', ['section', 'old_pick', 'close', 'changed_position'])
def test_knowledge_reader_to_section_picker_keeps_the_original_frozen_intent(panel_desk, intent):
    s = panel_desk
    from integrations.deeptutor_shchem_v1 import desktop_knowledge_reading as reading_api
    from integrations.deeptutor_shchem_v1.desktop_workbench import knowledge_card_reader as reader_api
    calls, old_picks, frozen = [], [], []
    freeze = s.panel.freeze_insertion
    def capture():
        value = freeze()
        frozen.append(deepcopy(value))
        return value
    s.monkeypatch.setattr(s.panel, 'freeze_insertion', capture)
    s.monkeypatch.setattr(s.panel, 'choose_section_materials', lambda *args: calls.append(deepcopy(args)))
    s.monkeypatch.setattr(s.panel, 'add_knowledge_textbook', lambda: old_picks.append(True))
    s.monkeypatch.setattr(reading_api, 'knowledge_card_reader', lambda facade, item: {'selection': {'real': True}})
    s.monkeypatch.setattr(reading_api, 'recheck_knowledge_card_reader', lambda *args: True)
    class Reader(QDialog):
        def __init__(self, facade, card, context, parent, *, current):
            super().__init__(parent)
            current()
            self.section_materials_requested = intent in {'section', 'changed_position'}
        def exec(self):
            if intent == 'changed_position':
                s.panel.insertion_position.setCurrentIndex(s.panel.insertion_position.findData('before'))
            return self.DialogCode.Rejected if intent == 'close' else self.DialogCode.Accepted
    s.monkeypatch.setattr(reader_api, 'KnowledgeCardReader', Reader)
    s.panel.insertion_position.setCurrentIndex(s.panel.insertion_position.findData('after'))
    before = deepcopy(s.owner.history.value)
    s.panel.read_knowledge_original()
    assert len(frozen) == 1 and frozen[0]['mode'] == 'after'
    s.queue.finish(0)
    if len(s.queue.jobs) > 1:
        s.queue.finish(1)
    assert s.owner.history.value == before and not s.owner.history.back
    if intent == 'section':
        assert calls == [(s.source, [], digest(before), frozen[0])]
        assert len(frozen) == 1 and not old_picks
    elif intent == 'old_pick':
        assert old_picks == [True] and not calls
    else:
        assert not calls and not old_picks


@pytest.mark.parametrize('intent', ['section', 'close', 'changed_position'])
def test_unbound_crop_reader_to_section_picker_keeps_frozen_source_and_destination(panel_desk, intent):
    s = panel_desk
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_crop_source_dialog as reader_api
    crop, asset, data = picture()
    original = deepcopy(s.owner.history.value)
    original['composition']['resources'][1] = crop
    original['composition']['placements'][crop['id']] = original['composition']['placements'].pop(s.source['id'])
    s.owner.history = DesignHistory(original)
    s.owner.page.image_assets_widget.set_assets([asset])
    s.owner.facade.preparation_image_bytes = lambda _: data
    context = {'selection': {'visual_asset_id': 'EXACT-PAGE', 'revision': 'e' * 64},
               'binding_sha256': 'f' * 64}
    reads, calls, frozen = [], [], []
    def read_source(item, assets):
        reads.append((deepcopy(item), deepcopy(assets)))
        return deepcopy(context)
    s.owner.facade.preparation_textbook_crop_original = read_source
    s.sync()
    s.panel.selected = crop['id']
    s.panel.refresh()
    freeze = s.panel.freeze_insertion
    def capture():
        value = freeze()
        frozen.append(deepcopy(value))
        return value
    s.monkeypatch.setattr(s.panel, 'freeze_insertion', capture)
    s.monkeypatch.setattr(s.panel, 'choose_section_materials', lambda *args: calls.append(deepcopy(args)))
    class Reader(QDialog):
        def __init__(self, facade, item, assets, shown, parent, *, current):
            super().__init__(parent)
            current()
            assert item == crop and assets == [asset] and shown == context
            self.section_materials_requested = intent != 'close'
        def exec(self):
            if intent == 'changed_position':
                s.panel.insertion_position.setCurrentIndex(s.panel.insertion_position.findData('before'))
            return self.DialogCode.Rejected if intent == 'close' else self.DialogCode.Accepted
    s.monkeypatch.setattr(reader_api, 'TextbookCropSourceDialog', Reader)
    s.panel.insertion_position.setCurrentIndex(s.panel.insertion_position.findData('after'))
    s.panel.read_textbook_crop_original()
    assert len(frozen) == 1
    s.queue.finish(0)
    assert reads == [(crop, [asset])]
    assert s.owner.history.value == original and not s.owner.history.back
    if intent == 'section':
        assert calls == [(crop, [asset], digest(original), frozen[0])]
        assert frozen[0]['anchor_resource_id'] == crop['id'] and frozen[0]['mode'] == 'after'
    else:
        assert not calls
