"""The selected output is an immutable source; teacher copies are independently editable."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_lesson_design import sample
from integrations.deeptutor_shchem_v1.desktop_lesson_output import (
    FILES, checked_file, copy_output_bundle, export_design,
)


@pytest.fixture
def bundle(tmp_path):
    payload = sample()
    facade = SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path / 'state'))
    record = export_design(facade, payload)
    payload['lesson_design']['exports'].append(record)
    destination = tmp_path / 'teacher'
    destination.mkdir()
    return facade, payload, record, destination


def test_copy_all_three_files_and_record_exact_version(bundle):
    f, p, r, parent = bundle
    before = deepcopy(p)
    folder = Path(copy_output_bundle(f, p['lesson_design'], r['id'], parent))
    assert folder.parent == parent
    for name in FILES:
        assert (folder / name).read_bytes() == checked_file(f, p['lesson_design'], r['id'], name).read_bytes()
    assert json.loads((folder / 'OUTPUT-MANIFEST.json').read_text()) == r
    assert '教师答案' in (folder / '使用说明.txt').read_text(encoding='utf-8')
    assert p == before


def test_reexport_never_overwrites_previous_teacher_copy(bundle):
    f, p, r, parent = bundle
    first = Path(copy_output_bundle(f, p['lesson_design'], r['id'], parent))
    (first / FILES[0]).write_bytes(b'teacher-edited-copy')
    second = Path(copy_output_bundle(f, p['lesson_design'], r['id'], parent))
    assert second != first and (first / FILES[0]).read_bytes() == b'teacher-edited-copy'
    assert sha256((second / FILES[0]).read_bytes()).hexdigest() == r['files'][0]['sha256']
    assert checked_file(f, p['lesson_design'], r['id'], FILES[0]).is_file()


def test_source_modification_rejected_before_any_copy(bundle):
    f, p, r, parent = bundle
    checked_file(f, p['lesson_design'], r['id'], FILES[2]).write_bytes(b'changed')
    with pytest.raises(ValueError, match='外部修改'):
        copy_output_bundle(f, p['lesson_design'], r['id'], parent)
    assert list(parent.iterdir()) == []


def test_failed_copy_cleans_only_its_new_folder(bundle, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_lesson_output as module
    f, p, r, parent = bundle
    marker = parent / 'old-notes.txt'; marker.write_text('keep')
    monkeypatch.setattr(module.shutil, 'copyfile', lambda *_: (_ for _ in ()).throw(OSError('full disk')))
    with pytest.raises(OSError):
        copy_output_bundle(f, p['lesson_design'], r['id'], parent)
    assert list(parent.iterdir()) == [marker] and marker.read_text() == 'keep'
    assert checked_file(f, p['lesson_design'], r['id'], FILES[0]).is_file()


def test_history_copy_keeps_chosen_old_version(bundle):
    f, p, old, parent = bundle
    p['lesson_design']['nodes'][0]['student_task'] = 'Different new task'
    new = export_design(f, p); p['lesson_design']['exports'].append(new)
    folder = Path(copy_output_bundle(f, p['lesson_design'], old['id'], parent))
    assert json.loads((folder / 'OUTPUT-MANIFEST.json').read_text())['id'] == old['id']
    assert (folder / FILES[0]).read_bytes() == checked_file(f, p['lesson_design'], old['id'], FILES[0]).read_bytes()
    assert (folder / FILES[0]).read_bytes() != checked_file(f, p['lesson_design'], new['id'], FILES[0]).read_bytes()
