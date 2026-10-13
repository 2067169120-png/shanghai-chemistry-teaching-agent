"""Process exclusion remains the same across temp roots and path aliases."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateError, _state_file_lock
from integrations.deeptutor_shchem_v1.desktop_state_locking import canonical_lock_identity

ROOT = Path(__file__).resolve().parents[4]
CHILD = """
import os, sys
from pathlib import Path
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateError, _state_file_lock
try:
    with _state_file_lock(Path(sys.argv[1])):
        if sys.argv[2] == 'crash':
            os._exit(17)
        print('acquired', flush=True)
except DesktopStateError:
    print('busy', flush=True)
"""


def run_child(path, temp, mode="read"):
    temp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", "-u", "-c", CHILD, str(path), mode],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=15)


def test_other_temp_process_cannot_enter_same_state_lock(tmp_path):
    target = tmp_path / "not-created" / "desktop-state.v1.json"
    with _state_file_lock(target):
        result = run_child(target, tmp_path / "different-child-temp")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "busy"
    assert not target.parent.exists()
    result = run_child(target, tmp_path / "different-child-temp")
    assert result.returncode == 0 and result.stdout.strip() == "acquired", result.stderr


def test_resolved_aliases_share_process_lock(tmp_path):
    target = tmp_path / "missing-state" / "desktop-state.v1.json"
    alias = target.parent / "not-created" / ".." / target.name
    assert canonical_lock_identity(alias) == canonical_lock_identity(target)
    with _state_file_lock(target):
        result = run_child(alias, tmp_path / "alias-child-temp")
        assert result.returncode == 0 and result.stdout.strip() == "busy", result.stderr
    assert not target.parent.exists()


def test_other_profile_is_independent(tmp_path):
    with _state_file_lock(tmp_path / "profile-A" / "desktop-state.v1.json"):
        result = run_child(tmp_path / "profile-B" / "desktop-state.v1.json", tmp_path / "other-temp")
        assert result.returncode == 0 and result.stdout.strip() == "acquired", result.stderr
    assert not (tmp_path / "profile-A").exists()
    assert not (tmp_path / "profile-B").exists()


def test_exception_and_crashed_owner_do_not_leave_live_lock(tmp_path):
    target = tmp_path / "profile" / "desktop-state.v1.json"
    with pytest.raises(ValueError, match="owned synthetic failure"):
        with _state_file_lock(target):
            raise ValueError("owned synthetic failure")
    result = run_child(target, tmp_path / "exception-child-temp")
    assert result.returncode == 0 and result.stdout.strip() == "acquired", result.stderr
    crashed = run_child(target, tmp_path / "crash-child-temp", "crash")
    assert crashed.returncode == 17, crashed.stderr
    result = run_child(target, tmp_path / "after-crash-temp")
    assert result.returncode == 0 and result.stdout.strip() == "acquired", result.stderr
    assert not target.parent.exists()
