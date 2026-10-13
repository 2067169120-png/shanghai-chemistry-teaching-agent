"""Complete lecture selections use the asynchronous owned-publication contract.

The in-memory Qt owner and private image store are shared with the pending-batch
tests. No MainWindow, Recovery, StateStore or Facade constructor is used.
"""
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QDialog, QPushButton

from test_pending_material_edit import app, desk as safe_workspace, SafeDesignDialog, png
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource
from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as backend


def personal(text):
    return resource('personal_text', text, [block('text', 'personal', text)],
        {'source_path': '测试教师文字', 'source_sha256': 'unknown', 'source_revision': 'unknown'})


@pytest.fixture
def desk(safe_workspace):
    s = safe_workspace
    dialog = SafeDesignDialog(s.page); s.page._material_handoff_dialog = dialog
    dialog.resize(1200, 850); dialog.show(); s.app.processEvents()
    return dialog, dialog.composition_panel, s.app, s.page


class Picker(QDialog):
    made = []
    outcome = QDialog.DialogCode.Accepted
    mutate = None
    def __init__(self, facade, parent=None, initial_source=None):
        super().__init__(parent)
        self.initial_source = deepcopy(initial_source)
        self.requests = [{'source': {'source_id': 'SYNTHETIC'}, 'title': '这些概念如何联系起来？'}]
        self.made.append(self)
    def exec(self):
        if self.mutate is not None: self.mutate()
        return self.outcome


class Publication:
    def __init__(self, resources, assets):
        self.result = {'resources': deepcopy(resources), 'image_assets': deepcopy(assets)}
        self.events = []; self.accepted = self.discarded = False
    def publish(self, guard):
        assert 'publish' not in self.events
        self.events.append('publish'); guard(); return deepcopy(self.result)
    def recheck_published(self, guard):
        assert 'publish' in self.events
        self.events.append('recheck'); guard()
    def accept(self): self.events.append('accept'); self.accepted = True
    def discard(self):
        self.events.append('discard')
        if not self.accepted: self.discarded = True


def setup(desk, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import handout_knowledge_picker as ui
    d, panel, app, page = desk
    Picker.made = []; Picker.mutate = None; Picker.outcome = QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui, 'HandoutKnowledgePicker', Picker)
    incoming = [personal('第一段完整原文'), personal('第二段完整原文')]
    calls = []; publications = []
    def prepared(facade, requests, assets, resources):
        calls.append((deepcopy(requests), deepcopy(assets), deepcopy(resources)))
        value = Publication(incoming, assets); publications.append(value); return value
    monkeypatch.setattr(backend, 'prepare_handout_knowledge_publication', prepared)
    d.test_publications = publications
    return d, panel, app, page, incoming, calls


def finish(d):
    identity = d._task
    assert identity in d.tasks.jobs
    d.tasks.finish(identity)


def test_toolbar_accepts_one_batch_and_undo_keeps_original_work(desk, monkeypatch):
    d, panel, app, page, incoming, calls = setup(desk, monkeypatch)
    panel.add_result({'resources': [personal('既有课堂导语')]}, digest(d.history.value))
    before = deepcopy(d.history.value); assets = page.image_assets_widget.assets()
    button = next(b for b in panel.findChildren(QPushButton) if b.text() == '讲义知识…')
    button.click()
    assert Picker.made[0].initial_source is None  # clicked(bool) is not a source.
    assert not calls and d.history.value == before
    finish(d)
    assert len(calls) == 1 and calls[0][1] == assets
    assert d.test_publications[0].accepted and not d.test_publications[0].discarded
    assert d.test_publications[0].events.count('publish') == 1
    assert panel.composition()['resources'] == before['composition']['resources'] + incoming
    after = deepcopy(d.history.value)
    d.travel(False); assert d.history.value == before
    d.travel(True); assert d.history.value == after


def test_cancel_does_not_call_import_or_change_draft(desk, monkeypatch):
    d, panel, app, page, incoming, calls = setup(desk, monkeypatch)
    Picker.outcome = QDialog.DialogCode.Rejected
    before = deepcopy(d.history.value); assets = page.image_assets_widget.assets()
    panel.add_handout_knowledge()
    assert not calls and not d.tasks.jobs and d.history.value == before
    assert page.image_assets_widget.assets() == assets


def change_destination(d, panel, page, change):
    if change == 'draft': panel.add_result({'resources': [personal('选择期间后续编辑')]}, digest(d.history.value))
    elif change == 'images': page.image_assets_widget.set_assets([png('red')[0]])
    elif change == 'closing': d._close_when_finished = True
    else: d._stopping = True


@pytest.mark.parametrize('change', ['draft', 'images', 'closing', 'stopping'])
def test_changes_during_selection_do_not_import(desk, monkeypatch, change):
    d, panel, app, page, incoming, calls = setup(desk, monkeypatch)
    monkeypatch.setattr(Picker, 'mutate', staticmethod(lambda: change_destination(d, panel, page, change)))
    panel.add_handout_knowledge()
    assert not calls and not d.tasks.jobs and not d.test_publications
    assert any(word in d.status.text() for word in ('变化', '关闭', '停止'))
    d._close_when_finished = d._stopping = False


@pytest.mark.parametrize('change', ['draft', 'images', 'closing', 'stopping'])
def test_late_import_result_cannot_replace_current_work(desk, monkeypatch, change):
    d, panel, app, page, incoming, calls = setup(desk, monkeypatch)
    panel.add_handout_knowledge(); identity = d._task
    result = d.tasks.calculate(identity)
    change_destination(d, panel, page, change)
    before = deepcopy(d.history.value); assets = page.image_assets_widget.assets()
    d.tasks.deliver(identity, result)
    assert d.history.value == before and page.image_assets_widget.assets() == assets
    assert len(calls) == 1 and d.test_publications[0].discarded
    assert not d.test_publications[0].accepted
    assert not {r['id'] for r in incoming} & {r['id'] for r in panel.composition()['resources']}
    d._close_when_finished = d._stopping = False


@pytest.mark.parametrize('outcome', ['source_failure', 'duplicate'])
def test_source_failure_and_duplicate_keep_current_work(desk, monkeypatch, outcome):
    d, panel, app, page, incoming, calls = setup(desk, monkeypatch)
    before = deepcopy(d.history.value); assets = page.image_assets_widget.assets()
    publication = Publication([], assets)
    def prepared(*_args):
        if outcome == 'source_failure': raise ValueError('原文版本已变化')
        return publication
    monkeypatch.setattr(backend, 'prepare_handout_knowledge_publication', prepared)
    panel.add_handout_knowledge(); finish(d)
    assert d.history.value == before and page.image_assets_widget.assets() == assets
    assert not d.history.back
    if outcome == 'source_failure':
        assert '原文版本已变化' in d.status.text() and not publication.accepted
    else:
        assert '已有编辑保持' in d.status.text() and publication.accepted


def test_original_picture_opens_same_lecture_knowledge_without_rewriting_binding(desk, monkeypatch):
    d, panel, app, page, incoming, calls = setup(desk, monkeypatch)
    asset = page.image_assets_widget.assets()[0]
    data = d.facade.preparation_image_bytes(asset)
    source = {'schema': 'shchem.handout-picture.v1', 'batch_id': 'PRIVATE-BATCH',
        'source_id': 'PRIVATE-SOURCE', 'source_name': '合成讲义.docx',
        'source_path': str(d.facade.paths.task_root / '合成讲义.docx'),
        'source_sha256': 'a' * 64, 'source_revision': 'b' * 64,
        'asset_id': 'word-b20-image1', 'block_index': 20,
        'mime_type': asset['content_type'], 'sha256': asset['sha256'], 'bytes_count': len(data)}
    picture = resource('handout_image', '原知识图', [
        block('image', 'material', asset['asset_id'], 20), block('text', 'material', '完整讲解。', 20)],
        {**{key: source[key] for key in ('source_path', 'source_sha256', 'source_revision')},
         'handout_source': source, 'image_asset': asset, 'source_roles': []})
    panel.add_result({'resources': [picture], 'image_assets': [asset]}, digest(d.history.value))
    card = deepcopy(panel.composition()['resources'][0])
    assert panel.handout_knowledge_button.isVisible()
    Picker.outcome = QDialog.DialogCode.Rejected; before = deepcopy(d.history.value)
    panel.handout_knowledge_button.click()
    assert Picker.made[-1].initial_source == card['original']['evidence']['handout_source']
    assert d.history.value == before and not calls and not d.tasks.jobs
