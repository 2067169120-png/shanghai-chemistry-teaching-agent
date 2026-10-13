"""Memory-only Qt handoffs of verified display pixels and raw vector bindings.

The PNG is a synthetic display payload, not proof of real WMF/EMF decoding,
font accuracy, chemistry content, or the source lecture's visual quality.
"""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from test_desktop_library_ui import _ManualBridge, qt_app
from test_desktop_studio_ui import settle
from test_handout_knowledge_picker_ui import _SyntheticLibrary

from integrations.deeptutor_shchem_v1.desktop_workbench import handout_image_reader as reader_module
from integrations.deeptutor_shchem_v1.desktop_workbench import handout_image_picker as picker_module
from integrations.deeptutor_shchem_v1.desktop_workbench import handout_knowledge_picker as knowledge_module
from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as workspace_module


class _DisplayLibrary(_SyntheticLibrary):
    def __init__(self, *, mime='image/wmf', role='handout'):
        super().__init__(role=role)
        for outline in self.outlines.values():
            original = deepcopy(outline['preview']['assets'][0])
            first = {**original, 'mime_type': mime, 'preview_supported': mime == 'image/png'}
            if mime != 'image/png':
                first.update(sha256=sha256(b'synthetic-original-vector').hexdigest(), bytes_count=1200)
            second = {**first, 'asset_id': 'word-b66-image1', 'block_index': 66,
                      'mime_type': 'image/emf', 'preview_supported': False}
            second.update(sha256=sha256(b'another-synthetic-original-vector').hexdigest(), bytes_count=900)
            small_raster = {**original, 'asset_id': 'word-b67-image1', 'block_index': 67, 'bytes_count': 200}
            outline['preview']['assets'] = [first, second, small_raster]

    @property
    def current_outline(self):
        return self.outlines[self.sources[0]['source_id']]

    def rows(self):
        bound = self.current_outline['source']
        return [{'schema': 'shchem.handout-picture.v1',
                 **{key: bound[key] for key in ('batch_id', 'source_id', 'source_name', 'source_path',
                                              'source_sha256', 'source_revision')},
                 **{key: asset[key] for key in ('asset_id', 'block_index', 'mime_type', 'sha256', 'bytes_count')}}
                for asset in self.current_outline['preview']['assets']]

    def image(self, _facade, row):
        self.image_calls.append(deepcopy(row))
        derived = row['mime_type'] != 'image/png'
        return {'bytes': self.image_bytes, 'info': {'width': 1200, 'height': 800, 'content_type': 'image/png'},
                'source': deepcopy(row), 'derived_preview': derived,
                'renderer_revision': 'synthetic-verified-display-v1' if derived else None,
                'original_sha256': row['sha256'], 'original_mime_type': row['mime_type'],
                'preview_sha256': sha256(self.image_bytes).hexdigest(),
                'conversion_note': '不应显示的防御性转换说明'}

    def source_context(self, _facade, binding):
        outline = self.outlines[binding['source_id']]
        return {'document_source': deepcopy(outline['source']), 'preview': deepcopy(outline['preview']),
                'images': self.rows(), 'contexts': {}}


@pytest.fixture
def make_ui(qt_app, monkeypatch):
    created = []

    def make(kind, *, mime='image/wmf', role='handout', resources=None):
        library = _DisplayLibrary(mime=mime, role=role)
        bridge = _ManualBridge()
        bridge.shutdown = lambda _timeout: None
        facade = SimpleNamespace()  # No import, asset-store or profile-write API.
        modules = (reader_module, picker_module, knowledge_module, workspace_module)
        for module in modules:
            monkeypatch.setattr(module, 'handout_image_preview', library.image)
        monkeypatch.setattr(knowledge_module, 'DesktopTaskBridge', lambda _parent: bridge)
        for module in (knowledge_module, workspace_module):
            monkeypatch.setattr(module, 'handout_knowledge_sources', library.catalog)
            monkeypatch.setattr(module, 'handout_knowledge_outline', library.outline)
        monkeypatch.setattr(workspace_module, 'handout_source_context', library.source_context)
        original = deepcopy(library.outlines)
        row = library.rows()[0]
        outline = library.current_outline
        if kind == 'reader':
            snapshot = {'source': row, 'images': library.rows(), 'preview': outline['preview'], 'contexts': {}}
            dialog = reader_module.HandoutImageReader(facade, snapshot, tasks=bridge)
            image_label = '在讲义原文中读取原图'
        elif kind == 'picker':
            snapshot = {'source': row, 'images': library.rows(), 'contexts': {}}
            dialog = picker_module.HandoutImagePicker(facade, snapshot, resources or [], tasks=bridge)
            image_label = '读取讲义原图'
        elif kind == 'knowledge':
            dialog = knowledge_module.HandoutKnowledgePicker(facade)
            bridge.succeed(bridge.take('读取讲义知识资料目录'), run=True)
            dialog.source.setCurrentIndex(1)
            bridge.succeed(bridge.take('读取讲义知识栏目'), run=True)
            dialog.reader.image_requested.emit(row['asset_id'])
            image_label = '读取讲义知识原图'
        elif kind == 'workspace':
            dialog = workspace_module.HandoutMaterialWorkspace(facade, bridge)
            bridge.succeed(bridge.take('读取讲义选材资料目录'), run=True)
            dialog.source.setCurrentIndex(1)
            bridge.succeed(bridge.take('读取当前讲义选材原文'), run=True)
            dialog.reader.image_requested.emit(row['asset_id'])
            image_label = '读取当前讲义选材原图'
        else:
            raise AssertionError(kind)
        dialog.show()
        settle(qt_app)
        created.append(dialog)
        pending = bridge.take(image_label)
        while pending.task_id in bridge.cancelled:
            pending = bridge.take(image_label)
        return SimpleNamespace(kind=kind, dialog=dialog, bridge=bridge, library=library, original=original,
                               row=row, pending=pending, image_label=image_label, app=qt_app)

    yield make
    for dialog in created:
        dialog.reject()
        dialog.deleteLater()
    settle(qt_app)


@pytest.mark.parametrize('kind', ['reader', 'picker', 'knowledge', 'workspace'])
@pytest.mark.parametrize('mime', ['image/wmf', 'image/png'])
def test_verified_display_is_visible_and_preserves_raw_binding_without_conversion_reminders(make_ui, kind, mime):
    case = make_ui(kind, mime=mime)
    dialog = case.dialog
    case.bridge.succeed(case.pending, run=True)
    derived = mime == 'image/wmf'
    if kind == 'picker':
        assert dialog.preview.has_image and dialog.confirm.isEnabled()
        assert ('本地转换显示' in dialog.dimensions.text()) is derived
        assert dialog._previewed == case.row
        displayed = dialog.dimensions.text() + dialog.status.text()
        dialog.preview.zoom_button.click()
        assert dialog.preview._zoom_dialog.image.pixmap() is not None
    else:
        assert dialog._pixmap is not None and not dialog._pixmap.isNull()
        assert dialog.reader._image_derived is derived
        expected = '本地转换预览 · 放大查看' if derived else '来源原图 · 放大查看'
        assert expected in dialog.reader.browser.toPlainText()
        displayed = dialog.reader.browser.toPlainText() + dialog.status.text()
        dialog.reader.image_zoom_requested.emit()
        assert dialog._zoom.image.pixmap() is not None
    assert '不应显示的防御性转换说明' not in displayed
    assert case.library.image_calls[-1] == case.row
    assert case.library.outlines == case.original
    if kind == 'reader':
        dialog.confirm.click()
        assert dialog.selection == case.row
    elif kind == 'picker':
        dialog.title.setText('这幅原图怎样帮助解释概念？')
        dialog.purpose.setPlainText('先观察再串起知识结构。')
        dialog.confirm.click()
        assert dialog.selection == {'source': case.row, 'title': dialog.title.text(),
                                    'purpose': dialog.purpose.toPlainText(), 'teacher_only': False}
    elif kind == 'knowledge':
        assert dialog.requests is None and not dialog.confirm.isEnabled()
    else:
        assert dialog.request is None and not dialog._selections and not dialog.confirm.isEnabled()


@pytest.mark.parametrize('mime', ['image/wmf', 'image/x-wmf', 'image/emf', 'image/x-emf'])
def test_small_vector_original_is_visible_without_enabling_small_raster_filter(make_ui, mime):
    case = make_ui('picker', mime=mime)
    dialog = case.dialog
    assert not dialog.small.isChecked()
    assert [dialog.list.item(i).data(Qt.ItemDataRole.UserRole)['mime_type'] for i in range(dialog.list.count())] == [mime, 'image/emf']
    dialog.small.setChecked(True)
    assert dialog.list.count() == 3
    dialog.small.setChecked(False)
    dialog.focus_image(case.row)
    assert not dialog.small.isChecked() and dialog.current == case.row


@pytest.mark.parametrize('kind', ['reader', 'picker', 'knowledge', 'workspace'])
def test_cancelled_vector_preview_ignores_late_display_and_has_no_handoff(make_ui, kind):
    case = make_ui(kind)
    payload = case.pending.operation()
    case.dialog.reject()
    case.bridge.succeed(case.pending, payload)
    assert case.pending.task_id in case.bridge.cancelled
    assert case.dialog.result() == QDialog.DialogCode.Rejected
    assert not case.dialog.confirm.isEnabled()
    if kind == 'picker':
        assert not case.dialog.preview.has_image and case.dialog.selection is None
    else:
        assert case.dialog._pixmap is None
        assert case.dialog.reader._image_pixmap is None
    assert case.library.outlines == case.original


@pytest.mark.parametrize('kind', ['reader', 'picker', 'knowledge', 'workspace'])
def test_failed_vector_read_clears_prior_pixels_and_cannot_reuse_old_image(make_ui, kind):
    case = make_ui(kind)
    dialog = case.dialog
    case.bridge.succeed(case.pending, run=True)
    second = case.library.rows()[1]
    if kind == 'picker':
        dialog.list.setCurrentRow(1)
    else:
        dialog.reader.image_requested.emit(second['asset_id'])
    pending = case.bridge.take(case.image_label)
    case.bridge.fail(pending, '本地图片读取失败')
    if kind == 'picker':
        assert not dialog.preview.has_image and dialog._previewed is None
    else:
        assert dialog._pixmap is None and dialog.reader._image_pixmap is None
    assert not dialog.confirm.isEnabled()
    assert '本地图片读取失败' in dialog.status.text()
    assert case.library.outlines == case.original


def test_reader_keeps_two_vector_occurrences_distinct_while_rejecting_late_first_pixels(make_ui):
    case = make_ui('reader')
    dialog = case.dialog
    first_payload = case.pending.operation()
    second = case.library.rows()[1]
    dialog.reader.image_requested.emit(second['asset_id'])
    second_pending = case.bridge.take(case.image_label)
    case.bridge.succeed(case.pending, first_payload)
    assert dialog._pixmap is None and not dialog.confirm.isEnabled()
    case.bridge.succeed(second_pending, run=True)
    dialog.confirm.click()
    assert dialog.selection == second
    assert dialog.selection['mime_type'] == 'image/emf' and dialog.selection['block_index'] == 66


@pytest.mark.parametrize('resource_kind', ['handout_image', 'handout_knowledge'])
def test_known_teacher_scope_survives_original_vector_to_prepared_png_mapping(make_ui, resource_kind):
    library = _DisplayLibrary()
    row = library.rows()[0]
    prepared_id = 'IMG-' + sha256(library.image_bytes).hexdigest()
    evidence = ({'handout_source': row, 'image_asset': {'asset_id': prepared_id}}
                if resource_kind == 'handout_image' else
                {'section_snapshot': {'blocks': [{'index': row['block_index'], 'images': [
                    {'original_sha256': row['sha256'], 'asset_id': prepared_id}]}]}})
    resources = [{'original': {'kind': resource_kind, 'evidence': evidence,
                              'blocks': [{'kind': 'image', 'value': prepared_id, 'role': 'teacher_note'}]}}]
    original = deepcopy(resources)
    case = make_ui('picker', resources=resources)
    case.bridge.succeed(case.pending, run=True)
    dialog = case.dialog
    assert dialog.teacher_only.isChecked() and not dialog.teacher_only.isEnabled()
    dialog.confirm.click()
    assert dialog.selection['source'] == row and dialog.selection['teacher_only'] is True
    assert resources == original


@pytest.mark.parametrize('kind', ['reader', 'picker', 'knowledge', 'workspace'])
def test_converted_display_and_confirmation_remain_reachable_at_420px(make_ui, kind):
    case = make_ui(kind)
    dialog = case.dialog
    case.bridge.succeed(case.pending, run=True)
    dialog.resize(420, 840)
    settle(case.app)
    for widget in (dialog.confirm, dialog.status):
        position = widget.mapTo(dialog, widget.rect().topLeft())
        assert position.x() >= 0 and position.y() >= 0
        assert position.x() + widget.width() <= dialog.width()
        assert position.y() + widget.height() <= dialog.height()
    if kind == 'picker':
        assert dialog.preview.has_image
    else:
        assert dialog.reader.browser.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize('role', ['handout', 'answer'])
def test_actual_child_picker_handoff_keeps_raw_vector_and_whole_section_in_one_request(make_ui, role):
    case = make_ui('workspace', role=role)
    dialog = case.dialog
    case.bridge.succeed(case.pending, run=True)
    dialog.images_button.click()
    case.bridge.succeed(case.bridge.take('读取当前讲义选材原图目录'), run=True)
    child = dialog._child
    pending = case.bridge.take('读取讲义原图')
    while pending.task_id in case.bridge.cancelled:
        pending = case.bridge.take('读取讲义原图')
    case.bridge.succeed(pending, run=True)
    assert child.preview.has_image and not child.small.isChecked()
    assert child.teacher_only.isChecked() is (role == 'answer')
    if role == 'answer':
        assert not child.teacher_only.isEnabled()
    child.title.setText('这张图怎样串起讲义的知识？')
    child.confirm.click()
    assert dialog._child is None and dialog.selected.count() == 1
    knowledge = {'source': deepcopy(dialog._outline['source']), 'outline_revision': dialog._outline['outline_revision'],
                 'section': deepcopy(dialog._outline['sections'][0]), 'title': '如何联系这整段知识？',
                 'teacher_only': role == 'answer'}
    dialog._add_selections('knowledge', [knowledge])
    dialog.confirm.click()
    case.bridge.succeed(case.bridge.take('核对本次讲义选材来源'), run=True)
    request = dialog.request
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert request['source'] == case.library.current_outline['source']
    assert request['image_selections'][0]['source'] == case.row
    assert request['image_selections'][0]['teacher_only'] is (role == 'answer')
    assert request['knowledge_requests'] == [knowledge]
    assert request['image_selections'][0]['source']['sha256'] != sha256(case.library.image_bytes).hexdigest()
    assert request['image_selections'][0]['source']['mime_type'] == 'image/wmf'
    assert case.library.outlines == case.original
