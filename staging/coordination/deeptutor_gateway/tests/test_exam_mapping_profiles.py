"""X01 header-based mapping reuse on synthetic data and isolated local state."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, build_exam, digest
from integrations.deeptutor_shchem_v1.desktop_exam_mapping_profiles import (
    ExamMappingProfileStore, resolve_profile, review_profile,
)


@pytest.fixture
def sample():
    # Distinctive private markers must never enter a mapping profile.
    book = {
        'source_name': 'PRIVATE_SOURCE_20261013.xlsx',
        'source_sha256': 'PRIVATE_SOURCE_HASH_20261013',
        'source_path': 'E:/PRIVATE_ORIGINAL_20261013.xlsx',
        'sheets': [{
            'name': 'PRIVATE_SHEET_20261013',
            'rows': [
                [' 学号 ', '班级', '状态', '总分', '实验得分', '原理得分'],
                ['PRIVATE_STUDENT_A', 'PRIVATE_CLASS_A', '', 81, 31, 50],
                ['PRIVATE_STUDENT_B', 'PRIVATE_CLASS_A', '', 65, 25, 40],
            ],
            'formula_cells': [], 'hidden_rows': [], 'hidden': False,
        }],
    }
    cfg = {
        'title': 'PRIVATE_TITLE_20261013', 'sheet': 'PRIVATE_SHEET_20261013',
        'header_row': 1, 'student_col': 0, 'class_col': 1, 'status_col': 2,
        'total_col': 3, 'max_score': 100, 'pass_score': 60, 'excellent_score': 85,
        'questions': [
            {'column': 4, 'question': '1(1)', 'max_score': 40,
             'knowledge': '实验推理', 'chapter': '实验方法', 'context': '共同材料甲：条件保持相同'},
            {'column': 5, 'question': '2', 'max_score': 60,
             'knowledge': '反应原理', 'chapter': '化学平衡', 'context': '共同材料乙：记录温度'},
        ],
    }
    return book, cfg


@pytest.fixture
def saved(sample, tmp_path):
    book, cfg = sample
    store = ExamMappingProfileStore(tmp_path / 'state')
    return store, store.save('实验与原理方案', book, cfg)


def rearrange(book, cfg, order, *, added=None):
    """Move complete columns; None denotes a new unrelated column."""
    book, cfg = deepcopy(book), deepcopy(cfg)
    sheet = next(s for s in book['sheets'] if s['name'] == cfg['sheet'])
    sheet['rows'] = [
        [row[column] if column is not None else (added if index == 0 else '新列内容')
         for column in order]
        for index, row in enumerate(sheet['rows'])
    ]
    for key in ('student_col', 'class_col', 'status_col', 'total_col'):
        old = cfg[key]
        cfg[key] = order.index(old) if old in order else None
    for q in cfg['questions']:
        q['column'] = order.index(q['column']) if q['column'] in order else None
    cfg['questions'] = [q for q in cfg['questions'] if q['column'] is not None]
    return book, cfg


def binding(review, key):
    return next(value for value in review['bindings'] if value['key'] == key)


def test_profile_reopens_with_metadata_but_no_source_or_student_data(sample, saved, tmp_path):
    book, cfg = sample
    store, profile = saved
    reopened = ExamMappingProfileStore(tmp_path / 'state')
    assert reopened.entries() == [(profile['id'], '实验与原理方案')]
    assert reopened.load(profile['id']) == profile
    assert profile['schema'] == 'shchem.exam-mapping-profile.v1'
    assert profile['headers'] == ['学号', '班级', '状态', '总分', '实验得分', '原理得分']
    assert profile['roles'] == {
        'student_col': '学号', 'class_col': '班级', 'status_col': '状态', 'total_col': '总分',
    }
    assert profile['max_score'] == 100 and profile['pass_score'] == 60 and profile['excellent_score'] == 85
    assert profile['questions'] == [
        {'header': book['sheets'][0]['rows'][0][q['column']],
         **{key: q[key] for key in ('question', 'max_score', 'knowledge', 'chapter', 'context')}}
        for q in cfg['questions']
    ]
    assert set(profile) == {
        'schema', 'id', 'name', 'headers', 'roles', 'questions',
        'max_score', 'pass_score', 'excellent_score',
    }
    stored = '\n'.join(path.read_text(encoding='utf-8') for path in store.root.glob('*.json'))
    for marker in ('PRIVATE_SOURCE', 'PRIVATE_ORIGINAL', 'PRIVATE_SHEET',
                   'PRIVATE_TITLE', 'PRIVATE_STUDENT', 'PRIVATE_CLASS'):
        assert marker not in stored
    assert all('column' not in q for q in profile['questions'])


def test_save_does_not_mutate_book_or_draft_and_load_is_independent(sample, tmp_path):
    book, cfg = sample
    original_book, original_cfg = deepcopy(book), deepcopy(cfg)
    store = ExamMappingProfileStore(tmp_path / 'state')
    profile = store.save('实验与原理方案', book, cfg)
    loaded = store.load(profile['id'])
    loaded['questions'][0]['context'] = '调用者修改'
    assert store.load(profile['id'])['questions'][0]['context'] == original_cfg['questions'][0]['context']
    assert book == original_book and cfg == original_cfg


def test_reordered_columns_match_headers_and_produce_original_scores(sample, saved):
    book, cfg = sample
    _, profile = saved
    moved, current = rearrange(book, cfg, [5, 3, 0, 4, 2, 1])
    current['title'] = '本次新考试'
    review = review_profile(profile, moved, current)
    assert binding(review, 'student_col')['column'] == 2
    assert binding(review, 'question:0')['column'] == 3
    assert binding(review, 'question:1')['column'] == 0
    assert any('位置变化' in value for value in review['changes'])
    resolved = resolve_profile(profile, moved, current)
    assert resolved['title'] == '本次新考试' and resolved['sheet'] == current['sheet']
    assert [q['column'] for q in resolved['questions']] == [3, 0]
    exam = build_exam(moved, resolved)
    assert [s['total'] for s in exam['students']] == [81, 65]
    assert exam['students'][0]['scores'] == {'1(1)': 31, '2': 50}


def test_added_column_is_reported_and_never_becomes_a_score(sample, saved):
    book, cfg = sample
    _, profile = saved
    moved, current = rearrange(book, cfg, [None, 0, 1, 2, 3, 4, 5], added='教师备注')
    review = review_profile(profile, moved, current)
    assert any('新增表头' in value and '教师备注' in value for value in review['changes'])
    resolved = resolve_profile(profile, moved, current)
    assert resolved['student_col'] == 1 and [q['column'] for q in resolved['questions']] == [5, 6]
    assert all(q['column'] != 0 for q in resolved['questions'])
    assert build_exam(moved, resolved)['students'][0]['total'] == 81


@pytest.mark.parametrize('removed,key', [(0, 'student_col'), (1, 'class_col'), (4, 'question:0')])
def test_removed_mapped_column_blocks_reuse_without_old_index_fallback(sample, saved, removed, key):
    book, cfg = sample
    _, profile = saved
    changed, current = rearrange(book, cfg, [i for i in range(6) if i != removed])
    review = review_profile(profile, changed, current)
    assert binding(review, key)['column'] is None
    assert any('缺少原表头' in value for value in review['changes'])
    with pytest.raises(ExamError, match='绑定'):
        resolve_profile(profile, changed, current)


def test_renamed_column_requires_explicit_teacher_rebinding(sample, saved):
    book, cfg = deepcopy(sample)
    _, profile = saved
    book['sheets'][0]['rows'][0][4] = '本次实验题得分'
    review = review_profile(profile, book, cfg)
    assert binding(review, 'question:0')['column'] is None
    with pytest.raises(ExamError, match='绑定'):
        resolve_profile(profile, book, cfg)
    resolved = resolve_profile(profile, book, cfg, {'question:0': 4})
    assert resolved['questions'][0]['column'] == 4
    assert build_exam(book, resolved)['students'][0]['scores']['1(1)'] == 31


def test_duplicate_headers_are_ambiguous_even_when_old_index_is_present(sample, saved, tmp_path):
    book, cfg = sample
    _, profile = saved
    duplicated, current = rearrange(book, cfg, [0, 1, 2, 3, 4, 5, None], added='实验得分')
    duplicated['sheets'][0]['rows'][1][6] = 0
    duplicated['sheets'][0]['rows'][2][6] = 0
    review = review_profile(profile, duplicated, current)
    assert binding(review, 'question:0')['column'] is None
    assert any('重复' in value and '旧列序号' in value for value in review['changes'])
    with pytest.raises(ExamError, match='绑定'):
        resolve_profile(profile, duplicated, current)
    explicit = resolve_profile(profile, duplicated, current, {'question:0': 4})
    assert build_exam(duplicated, explicit)['students'][0]['total'] == 81
    with pytest.raises(ExamError, match='唯一'):
        ExamMappingProfileStore(tmp_path / 'duplicate-state').save('重复表头不能保存', duplicated, current)


def test_review_shows_question_metadata_and_threshold_changes(sample, saved):
    book, cfg = deepcopy(sample)
    _, profile = saved
    cfg['pass_score'] = 55
    cfg['questions'][0].update(question='99', max_score=35, knowledge='本次知识点',
                               chapter='本次章节', context='本次共同材料')
    changes = '\n'.join(review_profile(profile, book, cfg)['changes'])
    for value in ('达标线', '题号=99', '满分=35', '本次知识点', '本次章节', '本次共同材料',
                  '题号=1(1)', '实验推理', '共同材料甲'):
        assert value in changes
    resolved = resolve_profile(profile, book, cfg)
    assert resolved['pass_score'] == 60
    assert resolved['questions'][0]['question'] == '1(1)'
    assert resolved['questions'][0]['context'] == profile['questions'][0]['context']


@pytest.mark.parametrize('key,value', [
    ('student_col', 4), ('question:0', 0), ('question:0', 5),
    ('student_col', None), ('question:0', -1), ('question:0', True),
])
def test_manual_selections_still_obey_column_and_overlap_constraints(sample, saved, key, value):
    book, cfg = sample
    _, profile = saved
    with pytest.raises(ExamError):
        resolve_profile(profile, book, cfg, {key: value})


def test_empty_header_cannot_be_explicitly_bound(sample, saved):
    book, cfg = deepcopy(sample)
    _, profile = saved
    book['sheets'][0]['rows'][0][4] = '  '
    with pytest.raises(ExamError, match='表头为空'):
        resolve_profile(profile, book, cfg, {'question:0': 4})


def test_explicit_exclusions_affect_only_this_import_and_keep_sum_constraint(sample, saved):
    book, cfg = sample
    store, profile = saved
    resolved = resolve_profile(profile, book, cfg, {'class_col': None, 'question:0': None})
    assert resolved['class_col'] is None
    assert [q['question'] for q in resolved['questions']] == ['2']
    assert build_exam(book, resolved)['students'][0]['total'] == 81
    assert store.load(profile['id']) == profile
    with pytest.raises(ExamError, match='全部非重叠'):
        resolve_profile(profile, book, cfg, {'total_col': None, 'question:0': None})


@pytest.mark.parametrize('alter', [
    lambda cfg: cfg.update(max_score=0),
    lambda cfg: cfg.update(pass_score=90, excellent_score=85),
    lambda cfg: cfg['questions'][0].update(max_score=0),
    lambda cfg: cfg['questions'][0].update(max_score=80),
    lambda cfg: cfg['questions'][0].update(question=''),
    lambda cfg: cfg['questions'][1].update(question='1(1)'),
    lambda cfg: cfg['questions'][1].update(column=4),
])
def test_invalid_exam_constraints_cannot_be_saved(sample, tmp_path, alter):
    book, cfg = deepcopy(sample)
    alter(cfg)
    store = ExamMappingProfileStore(tmp_path / 'state')
    with pytest.raises(ExamError):
        store.save('无效方案', book, cfg)
    assert store.entries() == []


def test_metadata_strings_are_normalized_and_optional_roles_preserved(sample, tmp_path):
    book, cfg = deepcopy(sample)
    cfg['class_col'] = cfg['status_col'] = None
    cfg['questions'][0].update(question=' 1(1) ', knowledge=' 实验推理 ', chapter=' 实验方法 ', context=' 公共材料 ')
    profile = ExamMappingProfileStore(tmp_path / 'state').save(' 规范化方案 ', book, cfg)
    assert profile['name'] == '规范化方案'
    assert profile['roles']['class_col'] is None and profile['roles']['status_col'] is None
    assert profile['questions'][0]['question'] == '1(1)'
    assert profile['questions'][0]['knowledge'] == '实验推理'
    assert profile['questions'][0]['chapter'] == '实验方法'
    assert profile['questions'][0]['context'] == '公共材料'
    resolved = resolve_profile(profile, book, cfg)
    assert resolved['class_col'] is None and resolved['status_col'] is None


def test_same_identity_updates_and_deletes_reopened_profile(sample, saved, tmp_path):
    book, cfg = deepcopy(sample)
    store, profile = saved
    cfg['questions'][0]['knowledge'] = '教师更新知识点'
    updated = store.save('更新后的名称', book, cfg, identity=profile['id'], expected_revision=digest(profile))
    assert updated['id'] == profile['id']
    assert store.entries() == [(profile['id'], '更新后的名称')]
    reopened = ExamMappingProfileStore(tmp_path / 'state')
    assert reopened.load(profile['id'])['questions'][0]['knowledge'] == '教师更新知识点'
    reopened.delete(profile['id'], expected_revision=digest(updated))
    assert store.entries() == []
    with pytest.raises(ExamError):
        store.load(profile['id'])


def test_stale_update_and_delete_preserve_newer_profile(sample, saved):
    book, cfg = sample
    store, profile = saved
    other_store = ExamMappingProfileStore(store.root.parent)
    latest = other_store.save('其他窗口更新', book, cfg, identity=profile['id'], expected_revision=digest(profile))
    with pytest.raises(ExamError, match='未覆盖'):
        store.save('旧窗口更新', book, cfg, identity=profile['id'], expected_revision=digest(profile))
    with pytest.raises(ExamError, match='未删除'):
        store.delete(profile['id'], expected_revision=digest(profile))
    assert store.load(profile['id']) == latest


def test_cross_process_lock_blocks_independent_store_save_and_delete(sample, saved, tmp_path):
    book, cfg = sample
    store, profile = saved
    path = store.root / (profile['id'] + '.json')
    original = path.read_bytes()
    script = tmp_path / 'hold-mapping-lock.py'
    ready = tmp_path / 'mapping-lock-ready.txt'
    workspace = Path(__file__).resolve().parents[4]
    script.write_text(
        'import sys\n'
        'from pathlib import Path\n'
        'sys.path.insert(0, sys.argv[3])\n'
        'from integrations.deeptutor_shchem_v1.desktop_state import _state_file_lock\n'
        'with _state_file_lock(Path(sys.argv[1]).resolve()):\n'
        '    Path(sys.argv[2]).write_text("locked", encoding="utf-8")\n'
        '    sys.stdin.readline()\n', encoding='utf-8',
    )
    env = os.environ.copy()
    env['TEMP'] = env['TMP'] = 'E:/临时文件/UserTemp/shchem-full-closure-20261013'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    child = subprocess.Popen(
        [sys.executable, '-B', str(script), str(store.root.resolve()), str(ready), str(workspace)],
        cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists() and child.poll() is None, '合成锁子进程未就绪'
        independent_writer = ExamMappingProfileStore(store.root.parent)
        independent_deleter = ExamMappingProfileStore(store.root.parent)
        with pytest.raises(ExamError):
            independent_writer.save('竞争写入', book, cfg, identity=profile['id'], expected_revision=digest(profile))
        assert path.read_bytes() == original
        with pytest.raises(ExamError):
            independent_deleter.delete(profile['id'], expected_revision=digest(profile))
        assert path.read_bytes() == original
    finally:
        try:
            child.communicate(input='release\n', timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=10)
    assert child.returncode == 0
    assert store.load(profile['id']) == profile


def test_corrupt_profile_cannot_be_overwritten_or_deleted_by_another_store(sample, saved):
    book, cfg = sample
    store, profile = saved
    path = store.root / (profile['id'] + '.json')
    corrupted = b'{"schema": "shchem.exam-mapping-profile.v1", "questions": '
    path.write_bytes(corrupted)
    writer = ExamMappingProfileStore(store.root.parent)
    deleter = ExamMappingProfileStore(store.root.parent)
    with pytest.raises(ExamError):
        writer.save('不覆盖损坏原件', book, cfg, identity=profile['id'])
    assert path.read_bytes() == corrupted
    with pytest.raises(ExamError):
        deleter.delete(profile['id'])
    assert path.read_bytes() == corrupted
    assert store.entries() == []


def test_duplicate_name_is_rejected_without_creating_another_entry(sample, saved):
    book, cfg = sample
    store, profile = saved
    with pytest.raises(ExamError):
        store.save(' 实验与原理方案 ', book, cfg)
    assert store.entries() == [(profile['id'], profile['name'])]
    assert store.load(profile['id']) == profile


@pytest.mark.parametrize('key,value', [
    ('max_score', '40'), ('question', 1), ('knowledge', ['实验推理']),
    ('chapter', None), ('context', {'text': '公共材料'}),
])
def test_raw_profile_cannot_bypass_numeric_or_metadata_types(sample, saved, key, value):
    book, cfg = sample
    _, profile = saved
    changed = deepcopy(profile)
    changed['questions'][0][key] = value
    with pytest.raises(ExamError):
        resolve_profile(changed, book, cfg)


def test_invalid_identity_and_extra_private_fields_are_rejected(sample, saved):
    book, cfg = sample
    store, profile = saved
    for operation in (lambda: store.load('../escape'), lambda: store.delete('../escape'),
                      lambda: store.save('越界', book, cfg, identity='../escape')):
        with pytest.raises(ExamError):
            operation()
    path = store.root / (profile['id'] + '.json')
    poisoned = dict(profile, students=[{'local_label': 'PRIVATE_STUDENT_A'}])
    path.write_text(json.dumps(poisoned, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ExamError):
        store.load(profile['id'])
    assert store.entries() == []


@pytest.fixture
def app():
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def import_dialog(app, sample, saved):
    from integrations.deeptutor_shchem_v1.desktop_workbench.exam_import_dialog import ExamImportDialog
    book, cfg = sample
    store, _ = saved
    dialog = ExamImportDialog(book, mapping_profiles=store)
    dialog.apply_config(cfg)
    yield dialog
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def choose_profile(dialog, profile):
    index = dialog.profile_choice.findData(profile['id'])
    assert index >= 0
    dialog.profile_choice.setCurrentIndex(index)


def test_review_dialog_requires_confirmation_before_accepting(app, sample, saved):
    from PySide6.QtWidgets import QDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.exam_import_dialog import MappingProfileReviewDialog
    book, cfg = sample
    _, profile = saved
    dialog = MappingProfileReviewDialog(profile, book, cfg)
    try:
        assert not dialog.confirm.isChecked()
        assert set(dialog.selectors) == {
            'student_col', 'class_col', 'total_col', 'status_col', 'question:0', 'question:1',
        }
        dialog.accept_mapping()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.resolved_config is None
        dialog.confirm.setChecked(True)
        dialog.accept_mapping()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert build_exam(book, dialog.resolved_config)['students'][0]['total'] == 81
    finally:
        dialog.deleteLater()


def test_review_dialog_duplicate_header_stays_unselected_until_teacher_choice(app, sample, saved):
    from PySide6.QtWidgets import QDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.exam_import_dialog import MappingProfileReviewDialog
    book, cfg = sample
    _, profile = saved
    changed, current = rearrange(book, cfg, [0, 1, 2, 3, 4, 5, None], added='实验得分')
    dialog = MappingProfileReviewDialog(profile, changed, current)
    try:
        assert dialog.selectors['question:0'].currentData() == 'unresolved'
        assert '重复' in dialog.changes.text()
        dialog.confirm.setChecked(True)
        dialog.accept_mapping()
        assert dialog.result() != QDialog.DialogCode.Accepted
        selector = dialog.selectors['question:0']
        selector.setCurrentIndex(selector.findData(4))
        dialog.accept_mapping()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.resolved_config['questions'][0]['column'] == 4
    finally:
        dialog.deleteLater()


def test_cancelling_review_preserves_complete_current_draft(import_dialog, saved, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.exam_import_dialog import MappingProfileReviewDialog
    dialog = import_dialog
    _, profile = saved
    choose_profile(dialog, profile)
    dialog.title.setText('当前草稿考试')
    dialog.passing.setValue(55)
    dialog.mapping.item(4, 4).setText('当前草稿知识点')
    dialog.mapping.item(4, 6).setText('当前草稿公共材料')
    before = deepcopy(dialog.config())
    seen = []
    def cancel(review):
        seen.append(review)
        review.confirm.setChecked(True)
        review.selectors['question:0'].setCurrentIndex(review.selectors['question:0'].findData(5))
        review.reject()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(MappingProfileReviewDialog, 'exec', cancel)
    dialog.profile_reuse.click()
    assert len(seen) == 1 and dialog.config() == before


def test_actual_review_reuse_and_commit_build_correct_exam_after_column_move(app, sample, saved, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.exam_import_dialog import (
        ExamImportDialog, MappingProfileReviewDialog,
    )
    book, cfg = sample
    store, profile = saved
    changed, current = rearrange(book, cfg, [5, 3, 0, 4, 2, 1])
    current['title'] = '本次复用考试'
    current['questions'] = []
    current['pass_score'] = 55
    current['excellent_score'] = 90
    dialog = ExamImportDialog(changed, mapping_profiles=store)
    dialog.apply_config(current)
    dialog.title.setText('本次复用考试')
    try:
        assert dialog.config()['questions'] == [] and dialog.passing.value() == 55
        choose_profile(dialog, profile)
        def confirm(review):
            assert not review.confirm.isChecked()
            assert review.selectors['question:0'].currentData() == 3
            review.confirm.setChecked(True)
            review.accept_mapping()
            return review.result()
        monkeypatch.setattr(MappingProfileReviewDialog, 'exec', confirm)
        dialog.profile_reuse.click()
        applied = dialog.config()
        assert {q['question']: q['column'] for q in applied['questions']} == {'1(1)': 3, '2': 0}
        assert applied['pass_score'] == 60 and applied['excellent_score'] == 85
        dialog.commit()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.exam['title'] == '本次复用考试'
        assert [s['total'] for s in dialog.exam['students']] == [81, 65]
        assert dialog.exam['students'][0]['scores'] == {'1(1)': 31, '2': 50}
        assert next(q for q in dialog.exam['questions'] if q['question'] == '1(1)')['context'] == '共同材料甲：条件保持相同'
    finally:
        dialog.deleteLater()


def test_save_button_only_saves_after_named_dialog_acceptance(import_dialog, saved, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    store, _ = saved
    before = store.entries()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('取消命名', False))
    import_dialog.profile_save.click()
    assert store.entries() == before
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('新命名方案', True))
    import_dialog.profile_save.click()
    assert len(store.entries()) == len(before) + 1
    assert '新命名方案' in [name for _, name in store.entries()]


def test_update_and_delete_buttons_require_explicit_confirmation(import_dialog, saved, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    store, profile = saved
    choose_profile(import_dialog, profile)
    import_dialog.mapping.item(4, 4).setText('明确更新的知识点')
    draft = deepcopy(import_dialog.config())
    prompts = []
    answer = [QMessageBox.StandardButton.No]
    def question(*args, **kwargs):
        prompts.append(args)
        return answer[0]
    monkeypatch.setattr(QMessageBox, 'question', question)
    import_dialog.profile_update.click()
    assert len(prompts) == 1 and store.load(profile['id']) == profile
    assert all(value in prompts[0][2] for value in ('原：', '新：', '实验推理', '明确更新的知识点'))
    assert prompts[0][-1] == QMessageBox.StandardButton.No
    assert import_dialog.config() == draft
    answer[0] = QMessageBox.StandardButton.Yes
    import_dialog.profile_update.click()
    updated = store.load(profile['id'])
    assert len(prompts) == 2 and updated['questions'][0]['knowledge'] == '明确更新的知识点'
    assert import_dialog.config() == draft
    answer[0] = QMessageBox.StandardButton.No
    import_dialog.profile_delete.click()
    assert len(prompts) == 3 and store.load(profile['id']) == updated
    answer[0] = QMessageBox.StandardButton.Yes
    import_dialog.profile_delete.click()
    assert len(prompts) == 4 and store.entries() == []
    assert import_dialog.config() == draft


def test_invalid_update_is_kept_local_before_confirmation(import_dialog, saved, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    store, profile = saved
    choose_profile(import_dialog, profile)
    import_dialog.mapping.item(4, 3).setText('0')
    prompts = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: prompts.append(a))
    before = deepcopy(import_dialog.config())
    import_dialog.profile_update.click()
    assert not prompts and store.load(profile['id']) == profile
    assert import_dialog.config() == before and import_dialog.message.text()


def test_dashboard_passes_local_mapping_store_to_import_dialog(app, sample, saved, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench import exam_dashboard
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    _, profile = saved
    facade = SimpleNamespace(paths=SimpleNamespace(state_root=tmp_path / 'state'), list_provider_profiles=lambda: ())
    tasks = DesktopTaskBridge()
    dashboard = exam_dashboard.ExamDashboard(facade, tasks)
    recorded = []
    class ImportSpy:
        def __init__(self, book, parent=None, *, mapping_profiles=None):
            recorded.append((book, parent, mapping_profiles))
        def exec(self):
            return QDialog.DialogCode.Rejected
        def deleteLater(self):
            pass
    monkeypatch.setattr(exam_dashboard, 'ExamImportDialog', ImportSpy)
    try:
        dashboard.map_excel(sample[0])
        assert len(recorded) == 1 and recorded[0][1] is dashboard
        assert isinstance(recorded[0][2], ExamMappingProfileStore)
        assert recorded[0][2].load(profile['id']) == profile
        assert dashboard.exam is None
    finally:
        dashboard.dirty = False
        dashboard.close()
        dashboard.deleteLater()
        tasks.shutdown()
        app.processEvents()
