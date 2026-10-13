"""Distinct crop recipes keep their source identity even with identical pixels."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import desktop_backup as backup
from integrations.deeptutor_shchem_v1 import curriculum_workbench as curriculum
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import digest, validate_resource
from integrations.deeptutor_shchem_v1.desktop_textbook_crop_reading import textbook_crop_reader

CASE = Path('E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/image-batch/'
            'tmp/service-after02/test_same_page_same_pixels_dif0/same-pixel-case.json')


@pytest.fixture
def sample(tmp_path, monkeypatch):
    source = json.loads(CASE.read_text(encoding='utf-8'))
    profile = tmp_path / 'profile'
    task = profile / 'tasks'
    shutil.copytree(source['task_root'], task)
    facade = SimpleNamespace(paths=SimpleNamespace(
        workspace_root=Path(source['workspace_root']), task_root=task))
    # The fixture has a complete synthetic 5/19/60 directory, not the shipped
    # active directory. Pin its own immutable bytes; keep the protocol checks.
    directory = facade.paths.workspace_root / 'sh-chem-db' / curriculum.DIRECTORY_RELATIVE
    monkeypatch.setattr(curriculum, 'EXPECTED_DIRECTORY_FILE_SHA256',
                        hashlib.sha256(directory.read_bytes()).hexdigest())
    result = deepcopy(source['result'])
    assert len(result['resources']) == 2 and len(result['image_assets']) == 1
    return facade, profile, result


def legacy(card):
    value = deepcopy(card)
    value['original']['evidence'].pop('crop_id')
    value['original_sha256'] = digest(value['original'])
    return validate_resource(value)


def test_each_same_pixel_crop_opens_its_exact_original_recipe(sample):
    facade, _, result = sample
    contexts = [textbook_crop_reader(facade, row, result['image_assets']) for row in result['resources']]
    ids = [row['original']['evidence']['crop_id'] for row in result['resources']]
    assert len(set(ids)) == 2
    assert [context['binding']['crop_id'] for context in contexts] == ids
    assert all(context['crop_pixels_verified'] for context in contexts)
    receipts = [json.loads((facade.paths.task_root / 'preparation-v1/textbook-crops' /
                           (crop_id + '.json')).read_text(encoding='utf-8')) for crop_id in ids]
    assert receipts[0]['bounds'] != receipts[1]['bounds']
    assert result['resources'][0]['original']['blocks'][0]['value'] == result['resources'][1]['original']['blocks'][0]['value']


def test_missing_exact_receipt_never_falls_back_to_same_pixel_recipe(sample):
    facade, _, result = sample
    card = result['resources'][0]
    crop_id = card['original']['evidence']['crop_id']
    (facade.paths.task_root / 'preparation-v1/textbook-crops' / (crop_id + '.json')).unlink()
    with pytest.raises(ValueError, match='缺失|变化|重复'):
        textbook_crop_reader(facade, card, result['image_assets'])
    other = result['resources'][1]
    assert textbook_crop_reader(facade, other, result['image_assets'])['binding']['crop_id'] == other['original']['evidence']['crop_id']


def test_unknown_well_formed_crop_binding_does_not_pick_any_matching_image(sample):
    facade, _, result = sample
    card = deepcopy(result['resources'][0])
    card['original']['evidence']['crop_id'] = 'f' * 64
    card['original_sha256'] = digest(card['original'])
    with pytest.raises(ValueError, match='缺失|变化|重复'):
        textbook_crop_reader(facade, card, result['image_assets'])


def test_legacy_ambiguous_same_pixel_recipes_stay_rejected(sample):
    facade, _, result = sample
    with pytest.raises(ValueError, match='缺失|变化|重复'):
        textbook_crop_reader(facade, legacy(result['resources'][0]), result['image_assets'])


def test_legacy_single_recipe_stays_readable(sample):
    facade, _, result = sample
    other_id = result['resources'][1]['original']['evidence']['crop_id']
    (facade.paths.task_root / 'preparation-v1/textbook-crops' / (other_id + '.json')).unlink()
    context = textbook_crop_reader(facade, legacy(result['resources'][0]), result['image_assets'])
    assert context['binding']['crop_id'] == result['resources'][0]['original']['evidence']['crop_id']


def test_backup_and_restore_closure_keep_two_receipts_with_one_image(sample):
    _, profile, result = sample
    selected = backup._plan_crop_receipts(profile, result, None)
    assert len(selected) == 2
    image = result['image_assets'][0]
    image_name = 'tasks/preparation-v1/images/' + image['sha256'] + '.image'
    contents = {name: (profile / name).read_bytes() for name in selected}
    contents[image_name] = (profile / image_name).read_bytes()
    references, warnings = [], []
    closure = backup._crop_closure(result, set(contents), contents.__getitem__, references, warnings)
    assert closure == {'textbook_crops_referenced': 2, 'textbook_crop_receipts': 2, 'textbook_crop_receipts_missing': 0}
    assert references == []
    assert len(warnings) == 1


def test_backup_does_not_guess_a_legacy_ambiguous_recipe(sample):
    _, profile, result = sample
    result['resources'] = [legacy(result['resources'][0])]
    with pytest.raises(backup.BackupError, match='多个裁选凭据'):
        backup._plan_crop_receipts(profile, result, None)


def test_packaged_wrong_recipe_is_not_accepted_for_exact_binding(sample):
    _, profile, result = sample
    card = result['resources'][0]
    wrong_id = result['resources'][1]['original']['evidence']['crop_id']
    name = 'tasks/preparation-v1/textbook-crops/' + wrong_id + '.json'
    contents = {name: (profile / name).read_bytes()}
    only = {'resources': [card], 'image_assets': result['image_assets']}
    with pytest.raises(backup.BackupError, match='未被已选教材资源引用'):
        backup._crop_closure(only, set(contents), contents.__getitem__, [], [])


def test_missing_exact_receipt_is_a_specific_backup_gap(sample):
    facade, profile, result = sample
    card = result['resources'][0]
    crop_id = card['original']['evidence']['crop_id']
    (facade.paths.task_root / 'preparation-v1/textbook-crops' / (crop_id + '.json')).unlink()
    selected = backup._plan_crop_receipts(profile, result, None)
    assert len(selected) == 1
    contents = {name: (profile / name).read_bytes() for name in selected}
    references = []
    closure = backup._crop_closure(result, set(contents), contents.__getitem__, references, [])
    assert closure['textbook_crop_receipts_missing'] == 1
    assert len(references) == 1 and references[0]['binding']['crop_id'] == crop_id
