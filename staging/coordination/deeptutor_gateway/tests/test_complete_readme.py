"""One complete current guide; previous full guides live in Git, not root copies."""
from pathlib import Path
import re
import shutil
import importlib.util

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location('documentation_policy', ROOT/'runtime/deeptutor_shchem/documentation_policy.py')
POLICY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(POLICY)


def test_full_guide_keeps_all_daily_pages_and_operating_instructions():
    text = (ROOT/'README.md').read_text(encoding='utf-8')
    for anchor in POLICY.REQUIRED_ANCHORS:
        assert f'<a id="{anchor}"></a>' in text
        assert f'(#{anchor})' in text
    assert len(text) > 8000
    assert '学生作答/批次/批改暂存' in text
    assert '原Word/公众号完整题库' in text


def test_every_screenshot_exists_no_future_placeholder_exemption():
    text = (ROOT/'README.md').read_text(encoding='utf-8')
    images = POLICY.IMAGE.findall(text)
    assert len(set(images)) >= 16
    for image in images:
        assert (ROOT/image).is_file(), image


def test_only_one_current_root_guide_and_module_readmes_survive():
    assert POLICY.archived_readmes(ROOT) == []
    assert (ROOT/'integrations/deeptutor_shchem_v1/README.md').is_file()
    assert (ROOT/'knowledge/README.md').is_file()


def test_all_current_documentation_links_and_navigation_resolve():
    assert POLICY.check_documentation(ROOT) == []


def test_closure_status_preserves_all_original_task_ids():
    text = (ROOT/'docs/WORKFLOW_STATUS.md').read_text(encoding='utf-8')
    assert '尚未形成全业务闭环' in text
    assert '本地候选' in text
    progress = (ROOT/'docs/roadmaps/audit-followup.md').read_text(encoding='utf-8')
    expected = {f'{group}{i:02}' for group,n in [('A',8),('B',9),('C',9),('D',9),('E',7),('F',5),('G',7),('H',5)] for i in range(1,n+1)}
    assert set(re.findall(r'^\| ([A-H][0-9]{2}) \|',progress,re.M)) == expected
    assert set(re.findall(r'^\| (X[0-9]{2}) \|',progress,re.M)) == {f'X{i:02}' for i in range(1,11)}


def test_prevent_new_archive_even_when_ignored_by_git(tmp_path):
    (tmp_path/'README-0.1.999-archive.md').write_text('not needed',encoding='utf-8')
    assert len(POLICY.archived_readmes(tmp_path)) == 1
    assert any('Git history' in error for error in POLICY.check_documentation(tmp_path))


def test_history_url_is_allowed_but_local_archive_reference_is_not(tmp_path):
    owner = tmp_path/'README.md'
    assert POLICY.local_target(owner,'https://github.com/org/repo/blob/v1/README.md') is None
    path = POLICY.local_target(owner,'README-1.2.3-archive.md')
    assert path == tmp_path/'README-1.2.3-archive.md'
    assert POLICY.SNAPSHOT.fullmatch(path.name)


def test_module_documentation_is_not_treated_as_version_snapshot(tmp_path):
    (tmp_path/'README.md').write_text('main')
    (tmp_path/'README-setup.md').write_text('distinct instructions')
    sub=tmp_path/'module';sub.mkdir();(sub/'README.md').write_text('module')
    assert POLICY.archived_readmes(tmp_path) == []


def test_checker_detects_missing_image_instead_of_accepting_planned_name(tmp_path):
    shutil.copy(ROOT/'README.md',tmp_path/'README.md')
    errors=POLICY.check_documentation(tmp_path)
    assert any('Unresolved guide link: docs/screenshots/' in e for e in errors)


def test_checker_detects_duplicate_anchor(tmp_path):
    text=(ROOT/'README.md').read_text(encoding='utf-8')
    (tmp_path/'README.md').write_text(text+'\n<a id="home"></a>\n',encoding='utf-8')
    assert any('Duplicate guide anchor: home' == e for e in POLICY.check_documentation(tmp_path))


def test_release_gate_checks_final_guide_and_does_not_create_archives():
    source=(ROOT/'runtime/deeptutor_shchem/prepare_verified_release.py').read_text(encoding='utf-8')
    assert 'check_documentation(ROOT)' in source
    assert 'archived_readmes(ROOT)' in source
    assert 'README-' not in source or '-archive.md' not in source


def test_moving_old_readmes_to_docs_is_not_a_cleanup(tmp_path):
    history=tmp_path/'docs'/'archive';history.mkdir(parents=True)
    (history/'README-0.1.999-archive.md').write_text('still duplicate')
    assert len(POLICY.archived_readmes(tmp_path)) == 1
