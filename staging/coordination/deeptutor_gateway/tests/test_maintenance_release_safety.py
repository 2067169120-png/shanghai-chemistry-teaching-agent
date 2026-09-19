"""Regression contracts for the active maintenance lane, not a universal YAML audit."""
from fnmatch import fnmatchcase
from pathlib import Path
import json
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
BRANCH = 'feature/lesson-source-0.1.101'
WORKFLOWS = ROOT / '.github/workflows'


def load(name):
    # Preserve the GitHub key `on`, which YAML 1.1 treats as True.
    return yaml.load((WORKFLOWS / name).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def matches(patterns, branch):
    selected = False
    for pattern in patterns:
        negative = pattern.startswith('!')
        if fnmatchcase(branch, pattern.lstrip('!')):
            selected = not negative
    return selected


def active_on_branch(workflow):
    events = workflow.get('on', {})
    if isinstance(events, str):
        events = {events: {}}
    if isinstance(events, list):
        events = dict.fromkeys(events, {})
    for event in ('push', 'pull_request', 'pull_request_target'):
        if event not in events:
            continue
        config = events[event] or {}
        # Ignore path filters: any possible maintenance event must remain safe.
        branches = config.get('branches', ['**'])
        if matches(branches, BRANCH) and not matches(config.get('branches-ignore', []), BRANCH):
            return True
    return False


def assert_read_only(workflow):
    assert workflow.get('permissions') == {'contents': 'read'}
    for job in workflow.get('jobs', {}).values():
        permissions = job.get('permissions', {})
        assert isinstance(permissions, dict)
        assert all(value == 'read' or value == 'none' for value in permissions.values())
        assert 'secrets' not in job
        if 'uses' in job:
            assert job['uses'] == './.github/workflows/basket-audit.yml'
        for step in job.get('steps', []):
            if step.get('uses', '').startswith('actions/checkout@'):
                assert step.get('with', {}).get('persist-credentials') == 'false'
    body = json.dumps(workflow, ensure_ascii=False)
    for forbidden in ('prepare_verified_release.py', 'gh release ', 'git push', 'secrets.'):
        assert forbidden not in body


def test_all_active_maintenance_workflows_are_read_only():
    active = {p.name: load(p.name) for p in WORKFLOWS.glob('*.yml') if active_on_branch(load(p.name))}
    assert 'basket-audit.yml' in active
    for workflow in active.values():
        assert_read_only(workflow)


def test_full_windows_verification_is_explicit_not_a_publisher():
    workflow = load('lesson-source.yml')
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    assert set(workflow['jobs']) == {'verify'}
    assert_read_only(workflow)
    body = json.dumps(workflow)
    for required in ('verify_packaged_trial.py', 'desktop_build.ps1', '--verify-lesson-import',
                     '--verify-closure', '--verify-scan-numbers', 'explorer-performance.yml'):
        assert required in body


def test_normal_push_and_pr_checks_do_not_build_or_publish():
    workflow = load('basket-audit.yml')
    assert BRANCH in workflow['on']['push']['branches']
    assert BRANCH in workflow['on']['pull_request']['branches']
    assert_read_only(workflow)
    body = json.dumps(workflow)
    assert 'desktop_build.ps1' not in body
    assert 'test_maintenance_release_safety.py' in body
    assert 'test_desktop_state_corruption.py' in body
    assert 'test_explorer_basket_recovery_ui.py' in body
    assert 'reviewed-source.zip' in body


@pytest.mark.parametrize('permissions', [{'contents': 'write'}, {'actions': 'write'}, 'write-all'])
def test_job_permission_escalation_is_rejected(permissions):
    workflow = {'permissions': {'contents': 'read'}, 'jobs': {'verify': {'permissions': permissions}}}
    with pytest.raises(AssertionError):
        assert_read_only(workflow)


@pytest.mark.parametrize('command', ['python prepare_verified_release.py', 'gh release create v0.1.101', 'git push origin HEAD'])
def test_publication_commands_are_rejected(command):
    workflow = {'permissions': {'contents': 'read'}, 'jobs': {'verify': {'steps': [{'run': command}]}}}
    with pytest.raises(AssertionError):
        assert_read_only(workflow)


def test_branch_filters_do_not_hide_a_future_maintenance_publisher():
    assert active_on_branch({'on': {'push': None}})
    assert active_on_branch({'on': {'pull_request': {'branches': ['feature/**']}}})
    assert not active_on_branch({'on': {'push': {'branches': ['feature/lesson-design-0.1.100']}}})
    assert not active_on_branch({'on': {'workflow_dispatch': None}})
    assert not matches(['**', '!feature/**'], BRANCH)
