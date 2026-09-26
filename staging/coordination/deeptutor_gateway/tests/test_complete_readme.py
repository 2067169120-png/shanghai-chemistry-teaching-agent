"""Teacher-oriented guide contracts replace page-length and screenshot quotas."""
import importlib.util
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location('documentation_policy', ROOT/'runtime/deeptutor_shchem/documentation_policy.py')
policy = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(policy)


def test_current_guide_covers_teacher_tasks_without_engineering_changelog():
    text=(ROOT/'README.md').read_text(encoding='utf-8')
    assert len(text)<9000 and len(text.splitlines())<=180
    assert text.count('## 场景') == 6
    for anchor in policy.REQUIRED_ANCHORS: assert f'id="{anchor}"' in text
    for anchor in policy.ENTRY_ANCHORS: assert '(#'+anchor+')' in text
    for token in ('维护源码','学生作答','完整题库','完成标志','准备'):
        assert token in text
    assert not re.search(r'\b[0-9a-f]{40}\b',text)
    assert 'docs/maintainer/README.md' in text


def test_screenshots_are_contextual_not_a_readme_quota():
    text=(ROOT/'README.md').read_text(encoding='utf-8')
    assert len(policy.IMAGE.findall(text))<=2
    images=[]
    for name in policy.TEACHER_CHAPTERS:
        owner=ROOT/name; body=owner.read_text(encoding='utf-8')
        for target in policy.IMAGE.findall(body):
            local=policy.local_target(owner,target)
            assert local is not None and local.is_file()
            assert local.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            images.append(local)
    assert len(set(images))>=3


def test_only_one_current_root_guide_without_removing_module_readmes():
    assert policy.archived_readmes(ROOT)==[]
    assert len(list(ROOT.glob('README.md')))==1
    assert any(p.name=='README.md' for p in (ROOT/'integrations').rglob('README.md'))


def test_guide_links_anchors_and_workflow_docs_are_valid():
    assert policy.check_documentation(ROOT)==[]


def test_original_work_items_are_retained_not_marked_all_complete():
    text=(ROOT/'docs/roadmaps/audit-followup.md').read_text(encoding='utf-8')
    for letter,count in [('A',8),('B',9),('C',9),('D',9),('E',7),('F',5),('G',7),('H',5)]:
        for number in range(1,count+1): assert re.search(r'\b'+letter+f'{number:02}'+r'\b',text)
    for number in range(1,11): assert f'X{number:02}' in text
    status=(ROOT/'docs/WORKFLOW_STATUS.md').read_text(encoding='utf-8')
    assert '尚未形成全业务闭环' in status and '本地候选' in status


def test_ignored_root_snapshot_is_still_rejected(tmp_path):
    (tmp_path/'README-0.1.999-archive.md').write_text('duplicate',encoding='utf-8')
    assert len(policy.archived_readmes(tmp_path))==1


def test_hosted_history_link_is_not_mistaken_for_a_local_snapshot(tmp_path):
    owner=tmp_path/'README.md'
    assert policy.local_target(owner,'https://github.com/example/repo/blob/v0.1.1/README.md') is None
    assert policy.local_target(owner,'README-0.1.1-archive.md')==tmp_path/'README-0.1.1-archive.md'


def test_module_readme_and_setup_notes_are_not_version_archives(tmp_path):
    (tmp_path/'README.md').write_text('current')
    (tmp_path/'README-setup.md').write_text('module')
    assert policy.archived_readmes(tmp_path)==[]


def test_missing_teacher_chapters_are_not_hidden_by_short_readme(tmp_path):
    shutil.copyfile(ROOT/'README.md',tmp_path/'README.md')
    errors=policy.check_documentation(tmp_path)
    assert any('Unresolved guide link: docs/teacher/' in error for error in errors)
    assert any('Missing teacher chapter:' in error for error in errors)


def test_duplicate_legacy_bookmark_is_rejected(tmp_path):
    text=(ROOT/'README.md').read_text(encoding='utf-8')+'\n<a id="home"></a>\n'
    (tmp_path/'README.md').write_text(text,encoding='utf-8')
    assert 'Duplicate guide anchor: home' in policy.check_documentation(tmp_path)


def test_release_checks_current_guide_without_creating_a_snapshot():
    text=(ROOT/'runtime/deeptutor_shchem/prepare_verified_release.py').read_text(encoding='utf-8')
    assert 'check_documentation(ROOT)' in text and 'archived_readmes(ROOT)' in text
    assert 'README-{VERSION}-archive.md' not in text


def test_moving_duplicate_root_guides_to_docs_is_not_a_solution(tmp_path):
    folder=tmp_path/'docs/archive';folder.mkdir(parents=True)
    (folder/'README-0.1.101-archive.md').write_text('duplicate')
    assert len(policy.archived_readmes(tmp_path))==1


def test_fragment_validation_is_not_lost_when_details_move_to_chapters(tmp_path):
    (tmp_path/'README.md').write_text('[bad](guide.md#missing)')
    (tmp_path/'guide.md').write_text('<a id="present"></a>\n# Chapter')
    errors=policy.check_documentation(tmp_path)
    assert 'Unresolved guide fragment: guide.md#missing' in errors


def test_excess_length_and_screenshot_dump_are_rejected(tmp_path):
    (tmp_path/'README.md').write_text('word '*2000+'\n![a](a.png)\n![b](b.png)\n![c](c.png)')
    errors=policy.check_documentation(tmp_path)
    assert any('compact entry-point budget' in error for error in errors)
    assert any('Move detailed screenshots' in error for error in errors)
