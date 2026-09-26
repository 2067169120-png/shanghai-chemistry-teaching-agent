"""Optional, backwards-readable region metadata; never original question images."""
from copy import deepcopy
import json
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import pytest
from test_lesson_backup import seed, roundtrip, files
from integrations.deeptutor_shchem_v1.desktop_number_regions import NumberRegionStore, validated_region_library
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_backup import plan_backup, create_backup, inspect_backup, BackupError


def saved(state):
    image={'image_sha256': 'a'*64, 'width':200, 'height':100}
    NumberRegionStore(state).remember(image,[dict(image_sha256='a'*64,box=[4,4,50,30],number=7,punctuation='.')])
    return image


def test_regions_survive_real_archive_and_independent_restore(tmp_path):
    state, _, _ = seed(tmp_path/'source');image=saved(state)
    before=files(state.root)
    plan, archive, report, restored=roundtrip(state.root,tmp_path)
    assert report['summary']['number_regions']==1 and report['summary']['number_region_images']==1
    output=DesktopStateStore(restored)
    assert NumberRegionStore(output).load(image)==[{'box':[4,4,50,30],'punctuation':'.'}]
    assert 'number' not in str(output.snapshot()['scan_number_regions']['images'])
    assert files(state.root)==before
    with ZipFile(archive) as z:
        assert not any(n.endswith(('.png','.jpg')) for n in z.namelist())
        state_doc=json.loads(z.read('files/desktop-state.v1.json'))
        assert 'question_explorer' not in state_doc  # not a source data migration


def test_older_backups_without_regions_still_restore(tmp_path):
    state,_,_=seed(tmp_path/'source')
    _,_,report,restored=roundtrip(state.root,tmp_path)
    assert report['summary'].get('number_regions', 0)==0
    assert 'scan_number_regions' not in DesktopStateStore(restored).snapshot()


@pytest.mark.parametrize('corrupt', [
    lambda row: row['regions'][0].update(number=2),
    lambda row: row['regions'][0].update(box=[0,0,400,300]),
    lambda row: row.update(width=True),
    lambda row: row.update(path='private-source.docx'),
])
def test_bad_or_excess_region_data_is_not_silently_preserved(corrupt,tmp_path):
    state,_,_=seed(tmp_path/'source');saved(state)
    library=deepcopy(state.snapshot()['scan_number_regions'])
    corrupt(library['images']['a'*64])
    with pytest.raises(ValueError): validated_region_library(library)


def test_check_recomputes_region_counts_not_untrusted_summary(tmp_path):
    state,_,_=seed(tmp_path/'source');saved(state)
    plan=plan_backup(state.root); archive=tmp_path/'a.zip';create_backup(plan,archive)
    with ZipFile(archive) as z:data={n:z.read(n) for n in z.namelist()}
    manifest=json.loads(data['manifest.json']);manifest['summary']['number_regions']=999
    data['manifest.json']=json.dumps(manifest).encode()
    with ZipFile(archive,'w',ZIP_DEFLATED) as z:
        for n,b in data.items():z.writestr(n,b)
    assert inspect_backup(archive)['summary']['number_regions']==1


def test_modified_original_hash_does_not_reapply_saved_coordinates(tmp_path):
    state,_,_=seed(tmp_path/'source');image=saved(state)
    _,_,_,restored=roundtrip(state.root,tmp_path)
    image['image_sha256']='b'*64
    assert NumberRegionStore(DesktopStateStore(restored)).load(image)==[]
