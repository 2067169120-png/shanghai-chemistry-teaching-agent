"""Synthetic integrity negatives, with source and prior outputs preserved."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from test_visual_mixed_paper_integration import (
    imported_visual_batch, _service, _prepare, _read_all, _request, _protected,
)
from integrations.deeptutor_shchem_v1 import desktop_state
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import MixedPaperError, _ACTIVE
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateError
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_pagination import MixedPaperPaginationError


def _approved(context):
    service, rows = _service(context)
    _, first = _prepare(service, rows)
    _read_all(service, first)
    service.approve(first.preview_id, first.preview_hash)
    result = service.export(first.preview_id, first.preview_hash)
    artifacts = {row['path']: Path(row['path']).read_bytes() for row in result['artifacts']}
    return service, first, artifacts


@pytest.mark.parametrize('stage', ['pagination', 'approval'])
def test_state_replace_failure_has_no_partial_preview_commit(imported_visual_batch, monkeypatch, stage):
    context = imported_visual_batch
    service, first, artifacts = _approved(context)
    protected = _protected(context)
    candidate = service.create_preview(_request(service, title='合成状态写入失败候选'))
    if stage == 'approval':
        candidate = service.prepare_pagination(candidate.preview_id, candidate.preview_hash)
        _read_all(service, candidate)
    before = service.state.path.read_bytes()
    drafts_before = deepcopy(service.state.snapshot()['drafts'])
    actual_replace = desktop_state.os.replace

    def fail_owned_state_replace(src, dst):
        if Path(dst).resolve() == service.state.path.resolve():
            raise OSError('synthetic owned state replacement failure')
        return actual_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(desktop_state.os, 'replace', fail_owned_state_replace)
        with pytest.raises(DesktopStateError):
            if stage == 'pagination':
                service.prepare_pagination(candidate.preview_id, candidate.preview_hash)
            else:
                service.approve(candidate.preview_id, candidate.preview_hash)
    assert service.state.path.read_bytes() == before
    assert service.state.snapshot()['drafts'] == drafts_before
    assert service._load(candidate.preview_id)[1]['approval'] is None
    assert not list(service.state.root.glob('.desktop-state-*.tmp'))
    if stage == 'pagination':
        assert service.state.snapshot()['drafts'][_ACTIVE]['preview_id'] == first.preview_id
        assert not service._load(candidate.preview_id)[2].get('pagination_binding')
        assert service.export(first.preview_id, first.preview_hash)['status'] == 'completed'
    else:
        with pytest.raises(MixedPaperError):
            service.export(candidate.preview_id, candidate.preview_hash)
    assert artifacts == {path: Path(path).read_bytes() for path in artifacts}
    assert _protected(context) == protected


@pytest.mark.parametrize('target', ['snapshot', 'canonical_model', 'source_image', 'frozen_page'])
def test_owned_preview_tamper_cannot_return_completed_export_or_replace_prior_outputs(imported_visual_batch, target):
    context = imported_visual_batch
    service, first, artifacts = _approved(context)
    candidate = service.create_preview(_request(service, title='合成篡改候选'))
    candidate = service.prepare_pagination(candidate.preview_id, candidate.preview_hash)
    _read_all(service, candidate)
    folder, record, snapshot = service._load(candidate.preview_id)
    if target in {'snapshot', 'canonical_model'}:
        path = folder / record['snapshot_file']
    elif target == 'source_image':
        image_id = next(image for image in snapshot['images'] if image.startswith('image-'))
        path = folder / snapshot['images'][image_id]['path']
    else:
        image_id = next(iter(service._page_ids(snapshot)))
        path = folder / snapshot['images'][image_id]['path']
    if target == 'canonical_model':
        snapshot['preview_model']['title'] = 'synthetic canonical model mismatch'
        raw = json.dumps(snapshot, ensure_ascii=False).encode('utf-8')
        path.write_bytes(raw)
        # Rebasing just the outer snapshot checksum must not bypass the
        # canonical preview-model hash independently checked by _load.
        record['snapshot_sha256'] = hashlib.sha256(raw).hexdigest()
        service.state.save_draft(candidate.preview_id, record)
    else:
        path.write_bytes(path.read_bytes() + b'\nsynthetic owned tamper')
    protected = _protected(context)
    before = service.state.path.read_bytes()
    # Source image verification also runs at export; confirmation explicitly
    # verifies source bindings and prepared page files without reusing old flags.
    if target == 'source_image':
        # An unused frozen copy cannot alter frozen artifacts; direct reading
        # must reject it and export must reject before returning completion.
        with pytest.raises(MixedPaperError):
            service.image(candidate.preview_id, image_id)
        service.approve(candidate.preview_id, candidate.preview_hash)
        with pytest.raises(MixedPaperError):
            service.export(candidate.preview_id, candidate.preview_hash)
    else:
        with pytest.raises((MixedPaperError, MixedPaperPaginationError)):
            service.approve(candidate.preview_id, candidate.preview_hash)
        with pytest.raises((MixedPaperError, MixedPaperPaginationError)):
            service.export(candidate.preview_id, candidate.preview_hash)
        assert service.state.path.read_bytes() == before
    assert artifacts == {path: Path(path).read_bytes() for path in artifacts}
    assert _protected(context) == protected


def test_changed_visual_selection_is_rejected_before_old_confirmation_commit(imported_visual_batch):
    context = imported_visual_batch
    service, first, artifacts = _approved(context)
    rows = context['service'].catalog('DESKTOPBATCH-' + 'a' * 32)['items']
    assert service.add_visual_questions([rows[1]]) == 1
    before = service.state.path.read_bytes()
    protected = _protected(context)
    with pytest.raises(MixedPaperError):
        service.approve(first.preview_id, first.preview_hash)
    with pytest.raises(MixedPaperError):
        service.export(first.preview_id, first.preview_hash)
    assert service.state.path.read_bytes() == before
    assert artifacts == {path: Path(path).read_bytes() for path in artifacts}
    assert _protected(context) == protected
