"""X05 explicit local roster import, using only synthetic E: test files.

The acceptance runner supplies an E: basetemp and TEMP/TMP. No spreadsheet
application, network endpoint, real student record or source fixture is used.
"""
from copy import deepcopy
import csv
import hashlib
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, digest
from integrations.deeptutor_shchem_v1.desktop_student_roster import (
    DesktopStudentRosterStore, attach_roster, new_member, new_roster,
)
from integrations.deeptutor_shchem_v1.desktop_student_roster_import import (
    build_roster_import, commit_roster_import, preview_roster_import, read_roster_file,
)


PROFILE_A = 'a' * 32
PROFILE_B = 'b' * 32
UNKNOWN_ID = 'f' * 32
HEADERS = ['本地别名', '稳定成员ID', '匿名档案ID', '学校学号']


@pytest.fixture
def importer(tmp_path):
    profiles = [{'student_id': PROFILE_A}, {'student_id': PROFILE_B}]
    store = DesktopStudentRosterStore(tmp_path / 'private-roster', profiles=lambda: profiles)
    draft = new_roster('合成导入班级')
    draft['members'] = [new_member('已有同名成员'), new_member('已有另一成员')]
    draft['members'][0]['profile_student_id'] = PROFILE_A
    return store, store.save(draft), profiles


def csv_file(tmp_path, rows, *, name='synthetic-roster.csv', encoding='utf-8-sig', delimiter=','):
    path = tmp_path / name
    with path.open('w', encoding=encoding, newline='') as stream:
        csv.writer(stream, delimiter=delimiter).writerows(rows)
    return path


def xlsx_file(path, sheets):
    """Minimal valid XLSX text cells; formulas have no cached result."""
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    package = 'http://schemas.openxmlformats.org/package/2006/relationships'
    names, relations, content = [], [], []
    with ZipFile(path, 'w') as archive:
        for number, (name, rows) in enumerate(sheets, 1):
            names.append(f'<sheet name="{escape(name)}" sheetId="{number}" r:id="rId{number}"/>')
            relations.append(f'<Relationship Id="rId{number}" Type="{rel}/worksheet" Target="worksheets/sheet{number}.xml"/>')
            content.append(f'<Override PartName="/xl/worksheets/sheet{number}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            xml_rows = []
            for row_number, row in enumerate(rows, 1):
                cells = []
                for column, value in enumerate(row):
                    coordinate = chr(65 + column) + str(row_number)
                    if isinstance(value, str) and value.startswith('='):
                        cells.append(f'<c r="{coordinate}"><f>{escape(value[1:])}</f></c>')
                    else:
                        cells.append(f'<c r="{coordinate}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
                xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
            archive.writestr(f'xl/worksheets/sheet{number}.xml', f'<worksheet xmlns="{main}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{"".join(names)}</sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{package}">{"".join(relations)}</Relationships>')
        archive.writestr('_rels/.rels', f'<Relationships xmlns="{package}"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                         + ''.join(content) + '</Types>')
    return path


def mapping(book, **changes):
    config = {'sheet': book['sheets'][0]['name'], 'header_row': 1, 'alias_col': 0,
              'member_col': 1, 'profile_col': 2, 'external_col': 3}
    config.update(changes)
    return config


def planned(path, roster, profiles, *, config=None, encoding='auto'):
    book = read_roster_file(path, encoding=encoding)
    return preview_roster_import(book, config or mapping(book), roster, profiles=profiles)


def choices(plan, action='new'):
    return {row['source_row']: {'action': action} for row in plan['rows']}


def build(plan, decisions, **changes):
    options = {'confirmed': True, 'acknowledge_warnings': True,
               'reason': '合成教师逐行确认稳定身份与导入范围'}
    options.update(changes)
    return build_roster_import(plan, decisions, **options)


def commit(store, path, plan, decisions, **changes):
    options = {'confirmed': True, 'acknowledge_warnings': True,
               'reason': '合成教师逐行确认稳定身份与导入范围'}
    options.update(changes)
    return commit_roster_import(store, path, plan, decisions, **options)


def test_utf8_bom_csv_is_read_as_text_with_hash_and_source_coordinates(tmp_path, importer):
    _, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['合成新成员', '', '', '00123']])
    original = path.read_bytes()
    book = read_roster_file(path)
    assert book['source_name'] == path.name
    assert book['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert book['sheets'][0]['rows'][0][0] == '本地别名'
    plan = preview_roster_import(book, mapping(book), roster, profiles=profiles)
    assert plan['rows'][0]['source_row'] == 2
    assert plan['rows'][0]['alias'] == '合成新成员'
    assert plan['rows'][0]['external_id'] == '00123'
    assert path.read_bytes() == original


def test_legacy_encoding_requires_explicit_choice_and_tsv_preserves_text(tmp_path, importer):
    _, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['中文合成成员', '', '', '00017']], encoding='gb18030')
    with pytest.raises(ExamError):
        read_roster_file(path)
    plan = planned(path, roster, profiles, encoding='gb18030')
    assert plan['rows'][0]['alias'] == '中文合成成员'
    tsv = csv_file(tmp_path, [HEADERS, ['制表符合成成员', '', '', '00009']],
                   name='synthetic-roster.tsv', delimiter='\t')
    assert planned(tsv, roster, profiles)['rows'][0]['external_id'] == '00009'


def test_optional_identity_columns_and_external_school_id_never_infer_members(tmp_path, importer):
    _, roster, profiles = importer
    path = csv_file(tmp_path, [['别名', '学校记录'], ['已有同名成员', roster['members'][0]['id']]])
    book = read_roster_file(path)
    config = mapping(book, member_col=None, profile_col=None, external_col=1)
    plan = preview_roster_import(book, config, roster, profiles=profiles)
    assert plan['rows'][0]['member_id'] is None
    assert plan['rows'][0]['profile_student_id'] is None
    assert plan['rows'][0]['external_id'] == roster['members'][0]['id']
    result = build(plan, choices(plan))['roster']
    assert result['members'][:2] == roster['members']
    assert result['members'][-1]['id'] != roster['members'][0]['id']


def test_csv_multiline_records_keep_physical_lines_and_empty_rows_are_reported(tmp_path, importer):
    _, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, [], ['有效合成成员', '', '', '外部\n多行记录'],
                               ['下一条合成成员', '', '', '']])
    plan = planned(path, roster, profiles)
    assert plan['blank_rows'] == [2]
    assert [row['source_row'] for row in plan['rows']] == [3, 4]
    assert plan['rows'][0]['source_lines'] == {'start': 3, 'end': 4}
    assert plan['rows'][1]['source_lines'] == {'start': 5, 'end': 5}
    assert plan['rows'][0]['errors']


def test_malformed_csv_is_rejected_without_silently_dropping_bad_records(tmp_path):
    path = tmp_path / 'synthetic-broken.csv'
    path.write_text('别名,成员ID\n"未结束引号,合成内容\n', encoding='utf-8')
    original = path.read_bytes()
    with pytest.raises(ExamError):
        read_roster_file(path)
    assert path.read_bytes() == original


def test_xlsx_sheet_and_header_are_teacher_selected_without_first_sheet_fallback(tmp_path, importer):
    _, roster, profiles = importer
    path = tmp_path / 'synthetic-roster.xlsx'
    xlsx_file(path, [('说明页', [['该页不是名单']]),
                     ('明确名单页', [['合成标题行'], HEADERS, ['明确工作表成员', '', '', '00105']])])
    book = read_roster_file(path)
    config = mapping(book, sheet='明确名单页', header_row=2)
    plan = preview_roster_import(book, config, roster, profiles=profiles)
    assert len(plan['rows']) == 1 and plan['rows'][0]['source_row'] == 3
    assert plan['rows'][0]['alias'] == '明确工作表成员'
    with pytest.raises(ExamError):
        preview_roster_import(book, {**config, 'sheet': '不存在的工作表'}, roster, profiles=profiles)


@pytest.mark.parametrize('location', ['alias', 'member_id', 'profile_student_id'])
def test_csv_formula_in_identity_columns_is_an_error_and_is_never_evaluated(tmp_path, importer, location):
    store, roster, profiles = importer
    values = ['安全合成成员', '', '', '学校记录']
    values[{'alias': 0, 'member_id': 1, 'profile_student_id': 2}[location]] = '=HYPERLINK("synthetic.invalid","合成")'
    path = csv_file(tmp_path, [HEADERS, values])
    plan = planned(path, roster, profiles)
    assert plan['rows'][0]['errors']
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan))
    assert store.path.read_bytes() == before


def test_xlsx_formula_alias_remains_invalid_without_cached_value_execution(tmp_path, importer):
    store, roster, profiles = importer
    path = tmp_path / 'synthetic-formula.xlsx'
    xlsx_file(path, [('名单', [HEADERS, ['="公式不能生成成员"', '', '', '00001']])])
    plan = planned(path, roster, profiles)
    assert plan['rows'][0]['errors']
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan))
    assert store.path.read_bytes() == before


def test_preview_has_no_write_and_empty_member_id_needs_explicit_new_action(tmp_path, importer):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['空ID合成新成员', '', '', '']])
    before_roster, before_disk = deepcopy(roster), store.path.read_bytes()
    plan = planned(path, roster, profiles)
    assert not plan['rows'][0]['errors'] and not plan['rows'][0]['member_id']
    assert roster == before_roster and store.path.read_bytes() == before_disk
    with pytest.raises(ExamError):
        build(plan, {})
    result = build(plan, choices(plan))['roster']
    assert result['members'][:2] == roster['members']
    assert len(result['members']) == 3
    assert result['members'][-1]['id'] not in {m['id'] for m in roster['members']}
    assert result['members'][-1]['profile_student_id'] is None
    assert roster == before_roster and store.path.read_bytes() == before_disk


@pytest.mark.parametrize('alias, member_id, profile_id', [
    ('', '', ''), ('   ', '', ''), ('未知稳定身份', UNKNOWN_ID, ''),
    ('未知匿名档案', '', UNKNOWN_ID), ('非法稳定身份', '../outside', ''),
])
def test_invalid_row_cannot_be_selected_new_but_can_be_explicitly_excluded(tmp_path, importer, alias, member_id, profile_id):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, [alias, member_id, profile_id, '非空来源记录'],
                               ['明确保留的有效合成成员', '', '', '']])
    plan = planned(path, roster, profiles)
    assert len(plan['rows']) == 2 and plan['rows'][0]['errors']
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan))
    assert store.path.read_bytes() == before
    result = build(plan, {2: {'action': 'exclude'}, 3: {'action': 'new'}})['roster']
    assert result['members'][:2] == roster['members']
    assert len(result['members']) == 3


def test_all_excluded_rows_have_no_saveable_change(tmp_path, importer):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['排除合成成员', '', '', '']])
    plan = planned(path, roster, profiles)
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan, 'exclude'))
    assert store.path.read_bytes() == before


def test_same_alias_creates_a_new_id_only_after_warning_acknowledgement(tmp_path, importer):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['已有同名成员', '', '', ''], ['已有同名成员', '', '', '']])
    plan = planned(path, roster, profiles)
    assert len(plan['rows']) == 2
    assert all(row['warnings'] for row in plan['rows'])
    with pytest.raises(ExamError):
        build(plan, choices(plan), acknowledge_warnings=False)
    result = commit(store, path, plan, choices(plan))
    aliases = [member for member in result['members'] if member['local_alias'] == '已有同名成员']
    assert len(aliases) == 3 and len({member['id'] for member in aliases}) == 3
    assert result['members'][0] == roster['members'][0]


def test_duplicate_existing_ids_require_exclusion_and_never_last_row_wins(tmp_path, importer):
    store, roster, profiles = importer
    identity = roster['members'][1]['id']
    path = csv_file(tmp_path, [HEADERS, ['明确更新甲', identity, '', ''], ['明确更新乙', identity, '', '']])
    plan = planned(path, roster, profiles)
    assert all(row['conflicts'] for row in plan['rows'])
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan, 'update'))
    assert store.path.read_bytes() == before
    decisions = {2: {'action': 'update'}, 3: {'action': 'exclude'}}
    result = commit(store, path, plan, decisions)
    assert result['members'][1]['id'] == identity
    assert result['members'][1]['local_alias'] == '明确更新甲'
    assert len(result['members']) == len(roster['members'])


def test_explicit_update_without_source_id_uses_only_chosen_target(tmp_path, importer):
    _, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['已有同名成员', '', '', '合成学校号']])
    plan = planned(path, roster, profiles)
    with pytest.raises(ExamError):
        build(plan, choices(plan, 'update'))
    target = roster['members'][1]['id']
    result = build(plan, {2: {'action': 'update', 'member_id': target}})['roster']
    assert result['members'][0] == roster['members'][0]
    assert result['members'][1]['id'] == target
    assert result['members'][1]['local_alias'] == '已有同名成员'
    assert len(result['members']) == 2


def test_existing_id_cannot_be_redirected_or_replaced_by_new_identity(tmp_path, importer):
    _, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['合成明确更新', roster['members'][0]['id'], '', '']])
    plan = planned(path, roster, profiles)
    for decision in ({'action': 'new'}, {'action': 'update', 'member_id': roster['members'][1]['id']}):
        with pytest.raises(ExamError):
            build(plan, {2: decision})


def test_duplicate_known_profiles_need_one_explicitly_retained_member(tmp_path, importer):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['新档案归属甲', '', PROFILE_B, ''],
                               ['新档案归属乙', '', PROFILE_B, '']])
    plan = planned(path, roster, profiles)
    assert all(row['conflicts'] for row in plan['rows'])
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan))
    assert store.path.read_bytes() == before
    result = commit(store, path, plan, {2: {'action': 'new'}, 3: {'action': 'exclude'}})
    assert result['members'][:2] == roster['members']
    assert result['members'][-1]['profile_student_id'] == PROFILE_B
    assert sum(member['profile_student_id'] == PROFILE_B for member in result['members']) == 1


def test_profile_already_owned_by_existing_member_can_only_update_its_exact_owner(tmp_path, importer):
    _, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['已有档案不按新别名换归属', '', PROFILE_A, '']])
    plan = planned(path, roster, profiles)
    assert plan['rows'][0]['conflicts']
    with pytest.raises(ExamError):
        build(plan, choices(plan))
    with pytest.raises(ExamError):
        build(plan, {2: {'action': 'update', 'member_id': roster['members'][1]['id']}})
    result = build(plan, {2: {'action': 'update', 'member_id': roster['members'][0]['id']}})['roster']
    assert result['members'][0]['id'] == roster['members'][0]['id']
    assert result['members'][0]['profile_student_id'] == PROFILE_A
    assert result['members'][1] == roster['members'][1]


@pytest.mark.parametrize('replacement', ['', PROFILE_B])
def test_existing_profile_ownership_is_retained_or_conflicting_replacement_rejected(tmp_path, importer, replacement):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['更新后合成别名', roster['members'][0]['id'], replacement, '']])
    plan = planned(path, roster, profiles)
    before = store.path.read_bytes()
    if replacement:
        with pytest.raises(ExamError):
            commit(store, path, plan, choices(plan, 'update'))
        assert store.path.read_bytes() == before
    else:
        result = commit(store, path, plan, choices(plan, 'update'))
        assert result['members'][0]['id'] == roster['members'][0]['id']
        assert result['members'][0]['profile_student_id'] == PROFILE_A
        assert result['members'][0]['local_alias'] == '更新后合成别名'
        assert result['created_at'] == roster['created_at']


@pytest.mark.parametrize('changes', [
    {'confirmed': False}, {'confirmed': None}, {'confirmed': 'yes'}, {'reason': ''},
    {'reason': True}, {'reason': ' '},
])
def test_teacher_confirmation_and_valid_reason_are_required(tmp_path, importer, changes):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['确认测试合成成员', '', '', '']])
    plan = planned(path, roster, profiles)
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan), **changes)
    assert store.path.read_bytes() == before


def test_source_edit_after_preview_blocks_save_and_preserves_inputs(tmp_path, importer):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['预览时合成成员', '', '', '']])
    plan = planned(path, roster, profiles)
    decisions = choices(plan)
    original_plan, original_decisions = deepcopy(plan), deepcopy(decisions)
    before = store.path.read_bytes()
    csv_file(tmp_path, [HEADERS, ['原表已经被修改', '', '', '']])
    with pytest.raises(ExamError):
        commit(store, path, plan, decisions)
    assert store.path.read_bytes() == before
    assert plan == original_plan and decisions == original_decisions


def test_preview_flags_cannot_hide_unknown_identity_or_profile(tmp_path, importer):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['污染预览合成成员', UNKNOWN_ID, UNKNOWN_ID, '']])
    plan = planned(path, roster, profiles)
    assert plan['rows'][0]['errors']
    poisoned = deepcopy(plan)
    for row in poisoned['rows']:
        row['errors'] = []
        row['warnings'] = []
        row['conflicts'] = []
        row['member_id'] = ''
        row['profile_student_id'] = None
    # A recomputed client-side digest cannot turn preview flags into evidence.
    poisoned['plan_sha256'] = digest({key: value for key, value in poisoned.items() if key != 'plan_sha256'})
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, poisoned, choices(poisoned))
    assert store.path.read_bytes() == before


def test_profile_removed_after_preview_cannot_be_bound_at_commit(tmp_path, importer):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['曾可用的合成匿名档案', '', PROFILE_B, '']])
    plan = planned(path, roster, profiles)
    assert not plan['rows'][0]['errors']
    profiles[:] = [{'student_id': PROFILE_A}]
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan))
    assert store.path.read_bytes() == before


def test_concurrent_roster_change_blocks_cas_and_keeps_latest_library(tmp_path, importer):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['旧预览输入', '', '', '']])
    plan = planned(path, roster, profiles)
    latest = deepcopy(roster)
    latest['label'] = '另一窗口已修改'
    latest = store.save(latest, expected_revision=roster['revision'])
    before = store.path.read_bytes()
    with pytest.raises(ExamError):
        commit(store, path, plan, choices(plan))
    assert store.path.read_bytes() == before and store.load(roster['id']) == latest


def test_change_between_revalidation_and_save_is_still_rejected_by_cas(tmp_path, importer, monkeypatch):
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['最终保存前竞态输入', '', '', '']])
    plan = planned(path, roster, profiles)
    original_save = store.save
    latest = []
    def race_save(candidate, *, expected_revision=None):
        changed = deepcopy(roster)
        changed['label'] = '最终保存前另一窗口已写入'
        latest.append(original_save(changed, expected_revision=roster['revision']))
        assert expected_revision == roster['revision']
        return original_save(candidate, expected_revision=expected_revision)
    monkeypatch.setattr(store, 'save', race_save)
    with pytest.raises(ExamError, match='另一窗口'):
        commit(store, path, plan, choices(plan))
    assert len(latest) == 1 and store.load(roster['id']) == latest[0]


def test_failed_atomic_save_preserves_original_roster_and_import_decisions(tmp_path, importer, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_student_roster as roster_module
    store, roster, profiles = importer
    path = csv_file(tmp_path, [HEADERS, ['保存失败输入', '', '', '']])
    plan = planned(path, roster, profiles)
    decisions = choices(plan)
    original_plan, original_decisions = deepcopy(plan), deepcopy(decisions)
    before = store.path.read_bytes()
    calls = []
    def fail_replace(*args):
        calls.append(args)
        raise OSError('synthetic roster import replacement failure')
    monkeypatch.setattr(roster_module.os, 'replace', fail_replace)
    with pytest.raises(ExamError):
        commit(store, path, plan, decisions)
    assert store.path.read_bytes() == before and store.load(roster['id']) == roster
    assert plan == original_plan and decisions == original_decisions
    assert len(calls) == 1, 'the real atomic replacement must be reached before its synthetic failure'
    assert not list(store.root.glob('.roster-*'))


def test_import_update_preserves_existing_exam_roster_snapshot(tmp_path, importer):
    store, roster, profiles = importer
    exam = {'students': [], 'questions': [], 'source': {'source_name': '合成历史考试'}}
    attached = attach_roster(exam, roster, reason='合成历史明确确认名册')
    frozen = deepcopy(attached)
    path = csv_file(tmp_path, [HEADERS, ['历史后改名', roster['members'][0]['id'], '', ''],
                               ['历史后新增', '', '', '']])
    plan = planned(path, roster, profiles)
    updated = commit(store, path, plan, {2: {'action': 'update'}, 3: {'action': 'new'}})
    assert updated['revision'] != roster['revision']
    assert updated['members'][0]['id'] == roster['members'][0]['id']
    assert updated['members'][0]['profile_student_id'] == PROFILE_A
    assert attached == frozen
    assert attached['roster_snapshots'][0]['roster'] == roster


def test_repeated_import_retains_original_receipt_and_stable_member_identity(tmp_path, importer):
    store, roster, profiles = importer
    first_path = csv_file(tmp_path, [HEADERS, ['首轮合成新成员', '', PROFILE_B, '00109']], name='synthetic-first.csv')
    first_plan = planned(first_path, roster, profiles)
    first = commit(store, first_path, first_plan, choices(first_plan))
    first_receipt = deepcopy(first['import_history'][0])
    member = first['members'][-1]
    second_path = csv_file(tmp_path, [HEADERS, ['二轮合成改名', member['id'], '', '00109']], name='synthetic-second.csv')
    second_plan = planned(second_path, first, profiles)
    second = commit(store, second_path, second_plan, choices(second_plan, 'update'))
    assert len(second['import_history']) == 2
    assert second['import_history'][0] == first_receipt
    assert first_receipt['source']['source_sha256'] == hashlib.sha256(first_path.read_bytes()).hexdigest()
    assert second['members'][-1]['id'] == member['id']
    assert second['members'][-1]['profile_student_id'] == PROFILE_B
    assert second['members'][-1]['local_alias'] == '二轮合成改名'
    assert store.load(second['id']) == second


@pytest.mark.parametrize('change',['drop','edit','unknown_schema','unknown_field','bad_hash','not_list','duplicate'])
def test_unknown_or_modified_import_receipts_do_not_overwrite_history(tmp_path,importer,change):
    store,roster,profiles=importer
    path=csv_file(tmp_path,[HEADERS,['合成凭据成员','','','0001']])
    plan=planned(path,roster,profiles);saved=commit(store,path,plan,choices(plan))
    candidate=deepcopy(saved);before=store.path.read_bytes()
    if change=='drop':candidate.pop('import_history')
    elif change=='edit':candidate['import_history'][0]['reason']='变更旧核对依据'
    elif change=='unknown_schema':candidate['import_history'][0]['schema']='unknown.receipt.v9'
    elif change=='unknown_field':candidate['import_history'][0]['unrecognised']='preserve'
    elif change=='bad_hash':candidate['import_history'][0]['receipt_sha256']='f'*64
    elif change=='not_list':candidate['import_history']=None
    else:candidate['import_history'].append(deepcopy(candidate['import_history'][0]))
    with pytest.raises(ExamError):store.save(candidate,expected_revision=saved['revision'])
    assert store.path.read_bytes()==before and store.load(saved['id'])==saved


def test_corrupt_durable_receipt_blocks_import_without_repairing_original_disk(tmp_path,importer):
    import json
    store,roster,profiles=importer
    path=csv_file(tmp_path,[HEADERS,['合成首轮成员','','','0001']])
    plan=planned(path,roster,profiles);saved=commit(store,path,plan,choices(plan))
    second_path=csv_file(tmp_path,[HEADERS,['合成次轮成员','','','0002']],name='second.csv')
    second_plan=planned(second_path,saved,profiles);decisions=choices(second_plan)
    library=json.loads(store.path.read_text(encoding='utf-8'))
    library['rosters'][saved['id']]['import_history'][0]['schema']='unknown.receipt.v9'
    store.path.write_text(json.dumps(library,ensure_ascii=False),encoding='utf-8')
    before=store.path.read_bytes();teacher_plan=deepcopy(second_plan);teacher_choices=deepcopy(decisions)
    with pytest.raises(ExamError):commit(store,second_path,second_plan,decisions)
    assert store.path.read_bytes()==before and second_plan==teacher_plan and decisions==teacher_choices


def test_explicit_exclusion_retains_multiline_physical_source_location(tmp_path,importer):
    store,roster,profiles=importer
    path=csv_file(tmp_path,[HEADERS,['保留合成记录','','','001'],['排除\n含控制字符记录','','','002']])
    plan=planned(path,roster,profiles)
    saved=commit(store,path,plan,{2:{'action':'new'},3:{'action':'exclude'}})
    excluded=saved['import_history'][-1]['rows'][-1]
    assert excluded['source_row']==3 and excluded['source_lines']=={'start':3,'end':4}
