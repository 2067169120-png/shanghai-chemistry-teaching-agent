"""An optional exact crop identity never replaces the existing book/page evidence."""
from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    block, resource, validate_resource)


def textbook(crop_id=...):
    evidence = dict(source_path='教材原文.pdf', source_sha256='a'*64,
        source_revision='b'*64, pdf_pages=[12], printed_pages='unknown')
    if crop_id is not ...: evidence['crop_id'] = crop_id
    return resource('textbook_image', '原图', [block('image', 'material', 'IMG-'+'c'*64)], evidence)


def test_legacy_and_exact_crop_identity_preserve_book_page_evidence():
    legacy, explicit = textbook(), textbook('d'*64)
    before = deepcopy(explicit)
    assert validate_resource(explicit) == before
    assert {k: v for k, v in explicit['original']['evidence'].items() if k != 'crop_id'} == legacy['original']['evidence']
    assert explicit['candidate_only'] and not explicit['publication_allowed']


@pytest.mark.parametrize('identity', ['', 'd'*63, 'd'*65, 'D'*64, 'g'*64, None, True, 17, ['d'*64]])
def test_invalid_crop_identity_is_rejected(identity):
    with pytest.raises(ValueError, match='裁图标识'):
        textbook(identity)


@pytest.mark.parametrize('required', ['source_path', 'source_sha256', 'source_revision', 'pdf_pages', 'printed_pages'])
def test_crop_identity_cannot_replace_required_book_or_page_evidence(required):
    value = textbook('d'*64)
    evidence = dict(value['original']['evidence']); evidence.pop(required)
    with pytest.raises(ValueError, match='来源字段不完整'):
        resource('textbook_image', value['title'], value['blocks'], evidence)
