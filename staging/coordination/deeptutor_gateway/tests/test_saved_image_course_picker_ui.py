"""Paged course choice and visible-only thumbnails on isolated synthetic data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialog

from test_desktop_studio_ui import settle
from test_composition_teaching_images import picture, source_question
from test_saved_preparation_images_ui import DeferredReads
from test_saved_preparation_image_page import SearchFacade
from test_saved_preparation_images import source_payload, image_card, snapshot
from integrations.deeptutor_shchem_v1.desktop_composition_images import import_image_result
from integrations.deeptutor_shchem_v1.desktop_workbench import saved_image_course_picker as course_ui
from integrations.deeptutor_shchem_v1.desktop_workbench import teaching_image_picker as image_ui
from integrations.deeptutor_shchem_v1.desktop_workbench.saved_image_course_picker import SavedImageCoursePicker
from integrations.deeptutor_shchem_v1.desktop_workbench.teaching_image_picker import TeachingImagePicker, _read_thumbnail
from integrations.deeptutor_shchem_v1.desktop_workbench.preview_navigation import PixmapCache
from integrations.deeptutor_shchem_v1 import desktop_saved_preparation_images as saved


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def option(index, count=8):
    return dict(draft_id=f'prep-{index:02}', revision=f'{index+1:064x}',
        title=f'已保存课程{index:02}', original_title=f'原课题{index:02}',
        created_at='2026-10-08T12:00:00Z', image_count=count)


def setup_course(app, monkeypatch):
    reads = DeferredReads(); rows = [option(i) for i in range(52)]; calls = []
    def page(facade, **request):
        calls.append(request); query = request['query']; offset = request['offset']; limit = request['limit']
        selected = [r for r in rows if query in r['title'] or query in r['original_title']]
        return dict(items=deepcopy(selected[offset:offset+limit]), total=len(selected), offset=offset,
                    limit=limit, has_more=offset+limit<len(selected), unavailable_count=0)
    monkeypatch.setattr(course_ui, 'saved_preparation_image_page', page)
    dialog = SavedImageCoursePicker(SimpleNamespace(), tasks=reads); dialog.show(); settle(app)
    return dialog, reads, rows, calls


def test_all_history_pages_title_topic_and_explicit_course_choice(app, monkeypatch):
    dialog, reads, rows, calls = setup_course(app, monkeypatch)
    assert len(reads.pending) == 1 and dialog.list.count() == 0
    reads.finish(0); assert dialog.list.count() == 25 and '52份' in dialog.page_label.text()
    assert not dialog.previous.isEnabled() and dialog.next.isEnabled()
    dialog.change_page(1); reads.finish(1); assert dialog.offset == 25
    dialog.change_page(1); reads.finish(2)
    assert dialog.offset == 50 and dialog.list.count() == 2 and not dialog.next.isEnabled()
    assert dialog.list.item(0).data(Qt.ItemDataRole.UserRole)['draft_id'] == 'prep-50'
    dialog.search.setText('原课题51'); dialog.refresh(); reads.finish(3)
    assert dialog.offset == 0 and dialog.list.count() == 1
    assert '原课题51' in dialog.detail.toPlainText() and '图片：8张' in dialog.detail.toPlainText()
    dialog.choose(); assert dialog.chosen == rows[51] and dialog.result() == QDialog.DialogCode.Accepted
    assert calls[-1] == dict(query='原课题51', offset=0, limit=25)
    dialog.deleteLater()


def test_empty_course_is_visible_but_not_selectable(app, monkeypatch):
    dialog, reads, rows, calls = setup_course(app, monkeypatch); rows[0]['image_count'] = 0
    reads.finish(0); assert '0图' in dialog.list.item(0).text() and not dialog.confirm_button.isEnabled()
    dialog.choose(); assert dialog.chosen is None
    dialog.list.setCurrentRow(1); assert dialog.confirm_button.isEnabled()
    dialog.reject(); dialog.deleteLater()


def test_search_and_close_discard_late_page_results(app, monkeypatch):
    dialog, reads, rows, calls = setup_course(app, monkeypatch)
    dialog.search.setText('课程51'); dialog.refresh()
    reads.finish(0); assert dialog.list.count() == 0 and reads.cancelled == ['0']
    reads.finish(1); assert dialog.list.count() == 1 and '课程51' in dialog.list.item(0).text()
    dialog.search.setText('课程50'); dialog.refresh(); dialog.reject(); reads.finish(2)
    assert dialog.list.count() == 0 and dialog.chosen is None and reads.cancelled == ['0', '2']
    dialog.deleteLater()


def test_failed_page_has_no_stale_selection_and_can_be_searched_again(app, monkeypatch):
    dialog, reads, rows, calls = setup_course(app, monkeypatch); reads.finish(0)
    dialog.change_page(1); reads.pending[1][1]['on_failure']('读取未完成')
    assert dialog.list.count() == 0 and not dialog.confirm_button.isEnabled() and dialog._task is None
    assert dialog.status.text() == '读取未完成'
    dialog.refresh(); reads.finish(2); assert dialog.list.count() == 25
    dialog.reject(); dialog.deleteLater()


def test_new_course_entry_works_without_legacy_options_and_preserves_binding(app, monkeypatch):
    data, asset = picture(); reads = DeferredReads(); thumbs = DeferredReads(); source = option(51)
    binding = dict(draft_id=source['draft_id'], draft_revision=source['revision'], payload_sha256='b'*64,
        source_asset=deepcopy(asset), source_roles=['material'], source_resources=[])
    row = dict(asset=asset, source_roles=['material'], teacher_only=False, via_question=False,
        title='原图引导问题', purpose='观察图中关系', saved_title=source['title'], saved_preparation=binding)
    facade = SimpleNamespace(search_preparation_drafts=lambda **kw:None, load_preparation_draft=lambda *a:None,
                             preparation_image_bytes=lambda _:data)
    monkeypatch.setattr(saved, 'saved_preparation_image_catalog', lambda *a:[deepcopy(row)])
    def choose(dialog): dialog.chosen = deepcopy(source); dialog.accept(); return dialog.result()
    monkeypatch.setattr(SavedImageCoursePicker, 'exec', choose)
    picker = TeachingImagePicker(facade, [], [], tasks=reads, thumbnail_tasks=thumbs); picker.show(); settle(app)
    assert picker.saved_button.isEnabled() and '已有备课' in picker.saved_button.text()
    picker.choose_saved(); assert len(reads.pending) == 1 and picker.choices == []
    reads.finish(0); settle(app); assert picker.preview.has_image
    picker.title.setText('本课比较哪些证据？'); picker.accept_choice()
    assert picker.selection == dict(title='本课比较哪些证据？', purpose=row['purpose'], teacher_only=False,
                                    saved_preparation=binding)
    picker.deleteLater()


def setup_images(app, *, count=32, resources=()):
    images = {}; assets = []
    for i in range(count):
        data, asset = picture(f'#{i+20:02x}6080'); asset['caption'] = f'课堂问题{i:02}：怎样解释图中关系？'
        assets.append(asset); images[asset['asset_id']] = data
    calls = []; thumbs = DeferredReads()
    def read(asset): calls.append(asset['asset_id']); return images[asset['asset_id']]
    facade = SimpleNamespace(preparation_image_bytes=read)
    picker = TeachingImagePicker(facade, assets, list(resources), thumbnail_tasks=thumbs)
    picker.resize(1000, 760); picker.show(); settle(app)
    return picker, thumbs, calls, assets, images


def test_visible_thumbnails_read_in_background_one_at_a_time_and_keep_full_preview(app):
    picker, reads, calls, assets, images = setup_images(app)
    assert calls == [assets[0]['asset_id']]  # Only the explicitly selected full preview was read synchronously.
    visible = {i.data(Qt.ItemDataRole.UserRole) for i in picker._visible_thumbnail_items()}
    assert 0 < len(visible) < len(assets) and len(reads.pending) == 1
    visited = set()
    index = 0
    while index < len(reads.pending):
        reads.finish(index); settle(app)
        visited.add(calls[-1])
        index += 1; assert index < 32
    current_visible = {i.data(Qt.ItemDataRole.UserRole) for i in picker._visible_thumbnail_items()}
    assert visited <= visible | current_visible and len(reads.pending) < len(assets)
    assert len(picker._thumbnails) == len(visited) and picker._thumbnails.nbytes <= image_ui.THUMBNAIL_CACHE_BYTES
    assert picker.preview.has_image and picker.confirm_button.isEnabled()
    for pixmap in picker._thumbnails.values(): assert pixmap.width() <= 96 and pixmap.height() <= 64
    picker.reject(); assert len(picker._thumbnails) == 0; picker.deleteLater()


def test_scroll_filter_and_close_drop_obsolete_thumbnail_callbacks(app):
    picker, reads, calls, assets, images = setup_images(app)
    picker.search.setText('问题31'); settle(app)
    assert reads.cancelled == ['0'] and len(reads.pending) == 2
    reads.finish(0); assert len(picker._thumbnails) == 0
    reads.finish(1); settle(app); assert len(picker._thumbnails) == 1
    assert assets[31]['sha256'] in picker._thumbnails
    picker.search.clear(); settle(app); last = len(reads.pending)-1
    picker.reject(); reads.finish(last); settle(app)
    assert len(picker._thumbnails) == 0 and picker.selection is None
    assert str(last) in reads.cancelled; picker.deleteLater()


def test_decode_result_for_picture_scrolled_out_of_view_is_discarded(app):
    picker, reads, calls, assets, images = setup_images(app)
    picker.list.scrollToBottom(); settle(app); reads.finish(0); settle(app)
    assert assets[0]['sha256'] not in picker._thumbnails
    reads.finish(1); settle(app)
    assert calls[-1] in {i.data(Qt.ItemDataRole.UserRole) for i in picker._visible_thumbnail_items()}
    picker.reject(); picker.deleteLater()


def test_corrupt_thumbnail_does_not_change_scope_or_handoff(app):
    data, asset = picture(); rows = [source_question(asset, 'answer')]; thumbs = DeferredReads()
    picker = TeachingImagePicker(SimpleNamespace(preparation_image_bytes=lambda _:data), [asset], rows, thumbnail_tasks=thumbs)
    picker.show(); settle(app); before = deepcopy(picker.choices)
    picker.facade.preparation_image_bytes = lambda _:b'corrupted bytes'
    with pytest.raises(ValueError): thumbs.pending[0][0]()
    thumbs.pending[0][1]['on_failure']('读取未完成'); settle(app)
    assert picker.choices == before and picker.teacher_only.isChecked() and not picker.teacher_only.isEnabled()
    assert len(picker._thumbnails) == 0 and len(thumbs.pending) == 1
    # Existing current-course teacher-only choices can still be used as
    # teacher notes. Thumbnail failure must not alter that prior route.
    assert picker.confirm_button.isEnabled()
    picker.reject(); picker.deleteLater()


def test_thumbnail_cache_bounds_release_list_icons_and_share_only_pixel_sha(app):
    picker, reads, calls, assets, images = setup_images(app, count=3)
    picker._thumbnails = PixmapCache(96*64*4*2, 2)
    for index in range(3): reads.finish(index); settle(app)
    assert len(picker._thumbnails) == 2 and picker._thumbnails.nbytes <= 96*64*4*2
    assert assets[0]['sha256'] not in picker._thumbnails and len(reads.pending) == 3
    assert picker.list.item(0).icon().pixmap(96,64).toImage().pixelColor(0,0).alpha() == 0
    assert picker.list.item(1).icon().pixmap(96,64).toImage().pixelColor(0,0).alpha() > 0
    picker.reject(); picker.deleteLater()


def test_scaled_decoder_verifies_original_and_keeps_aspect_ratio(app):
    data, asset = picture(); row = dict(asset=asset)
    image = _read_thumbnail(SimpleNamespace(preparation_image_bytes=lambda _:data), row)
    assert (image.width(), image.height()) == (96, 54)
    with pytest.raises(ValueError): _read_thumbnail(SimpleNamespace(preparation_image_bytes=lambda _:picture('red')[0]), row)


def test_unreadable_local_alias_does_not_suppress_valid_stored_thumbnail(app, tmp_path):
    picker, reads, calls, assets, images = setup_images(app, count=1)
    picker._invalidate_thumbnail_read(); source = deepcopy(picker.choices[0])
    local = {**deepcopy(source), 'identity':'LOCAL-'+assets[0]['sha256'], 'local':dict(file_path=str(tmp_path/'missing.png'))}
    picker.choices = [local, source]; picker.filter_rows(); settle(app)
    bad_index = len(reads.pending)-1
    with pytest.raises(ValueError): reads.pending[bad_index][0]()
    reads.pending[bad_index][1]['on_failure']('读取未完成'); settle(app)
    good_index = len(reads.pending)-1; assert good_index > bad_index
    reads.finish(good_index); settle(app)
    assert local['identity'] in picker._thumbnail_failed and source['identity'] not in picker._thumbnail_failed
    assert assets[0]['sha256'] in picker._thumbnails
    picker.list.setCurrentRow(1); assert picker.preview.has_image and picker.confirm_button.isEnabled()
    picker.accept_choice(); assert picker.selection['asset'] == assets[0]
    picker.deleteLater()


def test_real_course_catalog_handoff_reads_selected_and_visible_pixels_only(app, monkeypatch, tmp_path):
    facade = SearchFacade(tmp_path); assets = []
    for i in range(24):
        data, metadata = picture(f'#{i+50:02x}8040')
        metadata['caption'] = f'原图{i:02}：观察与解释'
        assets.append(facade.store.import_bytes(data, metadata['caption'], metadata['source'], metadata['purpose']))
    cards = [image_card(a) for a in assets]
    course = facade.put(source_payload(assets, cards))
    before = snapshot(facade); reads = DeferredReads(); thumbs = DeferredReads(); facade.byte_reads = 0
    def choose(dialog): dialog.chosen = deepcopy(course); dialog.accept(); return dialog.result()
    monkeypatch.setattr(SavedImageCoursePicker, 'exec', choose)
    picker = TeachingImagePicker(facade, [], [], tasks=reads, thumbnail_tasks=thumbs)
    picker.show(); settle(app); picker.choose_saved()
    # Execute the real catalog separately to show that sealing source metadata
    # reads no original pixels; applying its result previews only the first row.
    operation, callbacks = reads.pending[0]; rows = operation()
    assert len(rows) == 24 and facade.byte_reads == 0
    callbacks['on_success'](rows); settle(app)
    assert facade.byte_reads == 1 and len(thumbs.pending) == 1
    visible = picker._visible_thumbnail_items(); assert 0 < len(visible) < 24
    index = 0
    while index < len(thumbs.pending):
        thumbs.finish(index); settle(app); index += 1; assert index < 24
    assert facade.byte_reads == 1+index
    picker.title.setText('本课如何解释这张原图？'); picker.accept_choice()
    binding = picker.selection['saved_preparation']
    assert binding == rows[0]['saved_preparation'] and binding['source_resources'][0]['original'] == cards[0]['original']
    result = import_image_result(facade, [], [], picker.selection)
    assert result['resources'][0]['original']['selected_from_preparation'] == binding
    assert snapshot(facade) == before and facade.import_calls == []
    # Legacy readers still deliberately check all pixels, preserving old tests.
    facade.byte_reads = 0; assert len(saved.saved_preparation_images(facade, course['draft_id'], course['revision'])) == 24
    assert facade.byte_reads == 24
    picker.deleteLater()


def test_thumbnail_bridge_ownership_and_late_close_are_bounded(app, monkeypatch):
    class OwnedReads(DeferredReads):
        def __init__(self): super().__init__(); self.shutdown_calls = []
        def shutdown(self, milliseconds): self.shutdown_calls.append(milliseconds)
    bridges = []
    def bridge(parent):
        value = OwnedReads(); bridges.append(value); return value
    monkeypatch.setattr(image_ui, 'DesktopTaskBridge', bridge)
    data, asset = picture(); main = DeferredReads()
    picker = TeachingImagePicker(SimpleNamespace(preparation_image_bytes=lambda _:data), [asset], [], tasks=main)
    picker.show(); settle(app); assert len(bridges) == 1 and len(bridges[0].pending) == 1
    picker.reject(); bridges[0].finish(0)
    assert bridges[0].shutdown_calls == [1000] and bridges[0].cancelled == ['0']
    assert len(picker._thumbnails) == 0 and not main.cancelled
    picker.deleteLater()
    shared = OwnedReads()
    picker = TeachingImagePicker(SimpleNamespace(preparation_image_bytes=lambda _:data), [asset], [], thumbnail_tasks=shared)
    picker.show(); settle(app); picker.reject(); shared.finish(0)
    assert shared.cancelled == ['0'] and shared.shutdown_calls == [] and len(picker._thumbnails) == 0
    picker.deleteLater()


def test_paged_course_bridge_owned_close_cancels_only_its_page(app, monkeypatch):
    class OwnedReads(DeferredReads):
        def __init__(self): super().__init__(); self.shutdown_calls = []
        def shutdown(self, milliseconds): self.shutdown_calls.append(milliseconds)
    owned = OwnedReads(); monkeypatch.setattr(course_ui, 'DesktopTaskBridge', lambda _:owned)
    monkeypatch.setattr(course_ui, 'saved_preparation_image_page', lambda *a,**kw:
        dict(items=[option(0)],total=1,offset=0,limit=25,has_more=False,unavailable_count=0))
    dialog = SavedImageCoursePicker(SimpleNamespace()); dialog.show(); settle(app); dialog.reject(); owned.finish(0)
    assert owned.shutdown_calls == [1000] and owned.cancelled == ['0'] and dialog.list.count() == 0
    dialog.deleteLater()
