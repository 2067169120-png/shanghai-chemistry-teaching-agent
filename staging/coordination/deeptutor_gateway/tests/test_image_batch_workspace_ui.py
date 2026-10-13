"""Native batch dialog tests using only isolated facades and task bridges."""
from copy import deepcopy
from uuid import uuid4
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QDialog

from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture, disk_bytes
from test_textbook_preparation_crops import request
from test_composition_image_batch import photo, facade, crop_fixture
from integrations.deeptutor_shchem_v1.desktop_composition_image_batch import prepare_image_batch
from integrations.deeptutor_shchem_v1.desktop_workbench import image_batch_workspace as ui
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


class Tasks(QObject):
    task_cancelled = Signal(str, str)
    task_finished = Signal(str)
    def __init__(self): super().__init__(); self.pending = {}; self.stopped = set()
    def submit(self, label, operation, *, on_success, on_failure):
        key = uuid4().hex; self.pending[key] = (label, operation, on_success, on_failure); return key
    def cancel(self, key): self.stopped.add(key); return True
    def succeed(self, key):
        label, operation, done, failed = self.pending.pop(key)
        try: value = operation()
        except Exception as exc: failed(str(exc))
        else: done(value)
        self.task_finished.emit(key)


@pytest.fixture
def app():
    value = QApplication.instance() or QApplication([])
    value.setStyle('Fusion'); install_ui_font(value)
    yield value
    value.processEvents()


def close(dialog, app):
    dialog.reject(); dialog.deleteLater(); app.processEvents()


def test_multi_file_queue_edits_teaching_order_without_writes(app, tmp_path):
    f = facade(tmp_path); first = photo(tmp_path/'first.png'); photo(tmp_path/'second.png', 'red')
    before = disk_bytes(tmp_path); assets = []; resources = []
    dialog = ui.ImageBatchWorkspace(f, Tasks(), assets, resources)
    dialog.add_local_paths([str(tmp_path/'first.png'), str(tmp_path/'second.png')])
    dialog.show(); app.processEvents()
    assert dialog.list.count() == 2 and dialog.preview.has_image and not dialog.confirm.isEnabled()
    dialog.list.setCurrentRow(1); dialog.title.setText('为什么会出现红色？'); dialog.purpose.setPlainText('先观察，再比较。')
    dialog.move_current(-1)
    assert dialog.requests()[0]['title'] == '为什么会出现红色？'
    assert dialog.requests()[0]['purpose'] == '先观察，再比较。'
    dialog.list.setCurrentRow(1); dialog.list.setCurrentRow(0)
    assert dialog.title.text() == '为什么会出现红色？' and dialog.preview.has_image
    dialog.resize(430, 850); app.processEvents(); assert dialog.split.orientation() == Qt.Orientation.Vertical
    close(dialog, app)
    assert assets == [] and resources == [] and disk_bytes(tmp_path) == before
    assert not f.paths.task_root.exists()


def test_same_page_two_crops_survive_revisiting_and_sorting(app, crop_fixture):
    f = crop_fixture; first = request(f); second = deepcopy(first)
    second['bounds']['right'] += 30
    p1 = f.crops.preview(first['selection'], first['page_revision'], first['bounds'], first['caption'], first['purpose'])
    p2 = f.crops.preview(second['selection'], second['page_revision'], second['bounds'], second['caption'], second['purpose'])
    second.update(expected_preview_revision=p2['preview_revision'], expected_image_sha256=p2['image_asset']['sha256'])
    dialog = ui.ImageBatchWorkspace(f.facade, Tasks(), [], [], initial_source='textbook')
    dialog.enqueue_textbook(first, p1); dialog.enqueue_textbook(second, p2)
    originals = [deepcopy(row['request']) for row in dialog.requests()]
    dialog.list.setCurrentRow(0); dialog.title.setText('如何从第一裁片判断？'); dialog.move_current(1)
    dialog.list.setCurrentRow(0); dialog.list.setCurrentRow(1)
    assert len(dialog.entries) == 2
    assert [row['request'] for row in dialog.requests()] == list(reversed(originals))
    assert all(row['request']['selection_confirmed'] for row in dialog.requests())
    assert dialog.same_page.isEnabled() and not f.task_root.exists()
    close(dialog, app)


def test_complete_page_previews_fit_and_require_every_page(app, tmp_path):
    f = facade(tmp_path); one = photo(tmp_path/'one.png'); two = photo(tmp_path/'two.png', 'red', '怎样比较两种颜色？')
    publication = prepare_image_batch(f, [], [], [one, two])
    preview = ui.ImageBatchPreview(publication.result, publication.image_data)
    preview.show(); app.processEvents()
    assert preview.format.currentText() == '16:9' and preview.pages.count() >= 2
    assert abs(preview.page.original.width()/preview.page.original.height()-16/9) < .01
    pix = preview.page.pixmap(); assert pix.width() <= preview.page.width() and pix.height() <= preview.page.height()
    assert not preview.confirm.isEnabled()
    for index in range(preview.pages.count()): preview.pages.setCurrentIndex(index)
    assert preview.confirm.isEnabled()
    preview.format.setCurrentText('A4'); app.processEvents()
    assert abs(preview.page.original.width()/preview.page.original.height()-210/297) < .01
    assert not f.paths.task_root.exists()
    close(preview, app); publication.discard()


def test_only_current_fully_previewed_batch_can_accept(app, tmp_path, monkeypatch):
    f = facade(tmp_path); photo(tmp_path/'one.png'); photo(tmp_path/'two.png', 'red')
    tasks = Tasks(); dialog = ui.ImageBatchWorkspace(f, tasks, [], [])
    dialog.add_local_paths([str(tmp_path/'one.png'), str(tmp_path/'two.png')])
    def view(preview):
        for index in range(preview.pages.count()): preview.pages.setCurrentIndex(index)
        preview._accept_preview(); return preview.result()
    monkeypatch.setattr(ui.ImageBatchPreview, 'exec', view)
    dialog.preview_batch(); tasks.succeed(dialog._task_id)
    assert dialog.confirm.isEnabled() and not f.paths.task_root.exists()
    dialog.title.setText('修改后的导学问题'); assert not dialog.confirm.isEnabled()
    dialog.preview_batch(); tasks.succeed(dialog._task_id)
    dialog.accept_batch()
    assert dialog.result() == QDialog.DialogCode.Accepted and len(dialog.selection_requests) == 2
    assert not f.paths.task_root.exists()
    dialog.deleteLater(); app.processEvents()


def test_cancelled_or_closed_preview_never_releases_a_batch(app, tmp_path):
    f = facade(tmp_path); photo(tmp_path/'one.png'); tasks = Tasks()
    dialog = ui.ImageBatchWorkspace(f, tasks, [], []); dialog.add_local_paths([str(tmp_path/'one.png')])
    dialog.preview_batch(); key = dialog._task_id
    dialog.reject(); assert key in tasks.stopped and dialog.selection_requests is None
    tasks.succeed(key)
    assert not f.paths.task_root.exists()
    dialog.deleteLater(); app.processEvents()


def test_failed_later_local_file_keeps_previous_queue_unchanged(app, tmp_path):
    f = facade(tmp_path); photo(tmp_path/'one.png'); photo(tmp_path/'two.png','red')
    dialog = ui.ImageBatchWorkspace(f, Tasks(), [], [])
    dialog.add_local_paths([str(tmp_path/'one.png')]); before = dialog.requests()
    with pytest.raises(ValueError): dialog.add_local_paths([str(tmp_path/'two.png'), str(tmp_path/'missing.png')])
    assert dialog.requests() == before and not f.paths.task_root.exists()
    close(dialog, app)


def test_shared_pixels_use_one_memory_budget_and_keep_both_sources(app, tmp_path, monkeypatch):
    f = facade(tmp_path); photo(tmp_path/'one.png'); photo(tmp_path/'two.png')
    monkeypatch.setattr(ui, 'MAX_BATCH_IMAGE_BYTES', len((tmp_path/'one.png').read_bytes()))
    dialog = ui.ImageBatchWorkspace(f, Tasks(), [], [])
    dialog.add_local_paths([str(tmp_path/'one.png'), str(tmp_path/'two.png')])
    assert len(dialog.requests()) == 2 and dialog.entries[0]['data'] is dialog.entries[1]['data']
    assert dialog.requests()[0]['selection']['local']['file_path'] != dialog.requests()[1]['selection']['local']['file_path']
    close(dialog, app)


def test_later_teacher_alias_is_visible_when_returning_to_local_picture(app, tmp_path):
    from test_saved_preparation_images import source_payload, image_card
    from integrations.deeptutor_shchem_v1 import desktop_saved_preparation_images as saved
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import image_info
    from integrations.deeptutor_shchem_v1.desktop_lesson_composition import digest
    f = facade(tmp_path); local = photo(tmp_path/'photo.png')['selection']; details = local['local']
    data = Path(details['file_path']).read_bytes()
    asset = {'asset_id': 'IMG-'+details['sha256'], **{k: details[k] for k in ('sha256', 'caption', 'purpose')},
        'source': '已有备课原教师图', **image_info(data)}
    payload = source_payload([asset], [image_card(asset, 'teacher_note')]); revision = digest(payload)
    f.load_preparation_draft = lambda identity, expected: {'draft_id': identity, 'revision': expected, 'payload': deepcopy(payload)}
    folder = f.paths.task_root/'preparation-v1/images'; folder.mkdir(parents=True)
    (folder/(asset['sha256']+'.image')).write_bytes(data)
    binding = saved._binding('source', revision, payload, asset, saved._references(payload, [asset]))
    dialog = ui.ImageBatchWorkspace(f, Tasks(), [], [])
    dialog.enqueue_image(local)
    dialog.enqueue_image({'saved_preparation': binding, 'title': '教师核对', 'purpose': '教师备注', 'teacher_only': False})
    dialog.list.setCurrentRow(0)
    assert dialog.teacher_only.isChecked() and not dialog.teacher_only.isEnabled()
    assert '仅教师' in dialog.context.text()
    assert local['teacher_only'] is False
    close(dialog, app)
