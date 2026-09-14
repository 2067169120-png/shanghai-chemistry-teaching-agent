"""One-time source patch transport, removed after exact before/after checks."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANCH = 'feature/desktop-reliability-0.1.86'
EXPECTED = {
  'integrations/deeptutor_shchem_v1/desktop_editor_recovery.py': '48feb2c314492ccb66c063b7a1f094ca94357ec2845548da5cd61e5b95b93413',
  'integrations/deeptutor_shchem_v1/desktop_work_search.py': '2ae32c9bf2c89681c8cdc23e984eb7394284b662ad06dcc216ba8568c7466227',
  'integrations/deeptutor_shchem_v1/desktop_workbench/preparation_recovery.py': '3171f5a4ff14de4507be9c71fd089fe3265d7d2e4599c23e39b0186ad927172b',
  'runtime/deeptutor_shchem/benchmark_phase_a.py': '17509d93469100254f28d9f54429659568457b1d6a8dcc49cecbb815c6dc68de',
  'runtime/deeptutor_shchem/phase_a_ci_smoke.py': '426e3fc346daf2125a1bc42fe57b2a8008a0b8222f09bbb3e5ef881a26237325',
  'runtime/deeptutor_shchem/publish_phase_a_evidence.py': '2dc882901389fee820a200d3a54de1e95aa629c48cfddaa0ef11d61be6d0d513',
  'staging/coordination/deeptutor_gateway/tests/test_phase_a_core.py': '93c8605ad6031b68123e85949769f0a7ed33371140b653e5d44bc85a1f1fc434',
  'staging/coordination/deeptutor_gateway/tests/test_phase_a_ui.py': 'd7a48ca1020e64d06ccf6c8b09fc259c5d768dd0a597a21cb590dc672c566fec',
}


def main():
    bundles = sorted((ROOT / '.github').glob('phase-a-edits-*.json'))
    writes = {}
    # Normalize one known transport typo; expected hash below fixes exact content.
    ui = 'staging/coordination/deeptutor_gateway/tests/test_phase_a_ui.py'
    lines = (ROOT / ui).read_bytes().decode('utf-8').splitlines(True)
    for i, line in enumerate(lines):
        if line.startswith('from integrations') and line.rstrip().endswith(' import MyWorkPage'):
            lines[i] = 'from integrations.deeptutor_shchem_v1.desktop_workbench.my_work_page import MyWorkPage\n'
    writes[ui] = ''.join(lines).encode('utf-8')
    for name, expected in EXPECTED.items():
        value = writes.get(name, (ROOT / name).read_bytes())
        assert hashlib.sha256(value).hexdigest() == expected, name + ': source transport mismatch'
    for bundle in bundles:
        data = json.loads(bundle.read_text(encoding='utf-8'))
        assert data['schema'] == 1
        for entry in data['entries']:
            name = entry['path']
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
            assert name.startswith('integrations/') or name in ('.gitignore', 'README.md', 'CHANGELOG.md')
            old = (ROOT / name).read_bytes()
            assert hashlib.sha256(old).hexdigest() == entry['before'], name + ': original mismatch'
            lines = old.decode('utf-8').splitlines(True)
            for begin, end, text in sorted(entry['edits'], reverse=True):
                lines[begin:end] = [text]
            new = ''.join(lines).encode('utf-8')
            assert hashlib.sha256(new).hexdigest() == entry['after'], name + ': result mismatch'
            writes[name] = new
    for name, value in writes.items():
        (ROOT / name).write_bytes(value)
    subprocess.run(['git', 'add', '-f', '--', *writes], cwd=ROOT, check=True)
    subprocess.run(['git', 'rm', '--', '.github/apply_phase_a.py', *[p.relative_to(ROOT).as_posix() for p in bundles]], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m', 'feat(desktop): connect audited recovery, full-history search and compiled filters [skip ci]'], cwd=ROOT, check=True)
    subprocess.run(['git', 'push', 'origin', 'HEAD:' + BRANCH], cwd=ROOT, check=True)
    print('Applied', len(writes), 'verified source files.')


if __name__ == '__main__':
    main()
