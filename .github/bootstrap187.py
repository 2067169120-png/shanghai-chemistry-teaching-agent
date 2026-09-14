"""One-time transport of previously delivered source deltas; removed after commit."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
branch = 'feature/desktop-onboarding-0.1.87'
def git(*args):
    return subprocess.check_output(['git', *args], cwd=root)

entries = json.loads((root / '.github/onboarding-edits.json').read_text(encoding='utf-8'))
outputs = []
for item in entries:
    old = git('show', 'HEAD:' + item['path']).decode('utf-8').replace('\r\n', '\n')
    assert hashlib.sha256(old.encode()).hexdigest() == item['before'], item['path']
    lines = old.splitlines(keepends=True)
    for start, end, text in reversed(item['edits']):
        lines[start:end] = [text]
    content = ''.join(lines)
    assert hashlib.sha256(content.encode()).hexdigest() == item['after'], item['path']
    outputs.append((item['path'], content))
for name, text in outputs:
    (root / name).write_bytes(text.encode('utf-8'))
# Correct the transport-only import typo before compilation or test execution.
test = root / 'staging/coordination/deeptutor_gateway/tests/test_desktop_environment.py'
text = test.read_text(encoding='utf-8').replace('from integrations.deeptutor_shchem_v1/desktop_paths', 'from integrations.deeptutor_shchem_v1.desktop_paths')
test.write_bytes(text.encode('utf-8'))
git('add', '-f', *(name for name, _ in outputs), str(test.relative_to(root)))
git('rm', '.github/bootstrap187.py', '.github/onboarding-edits.json')
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('commit', '-m', 'feat(desktop): integrate 0.1.87 settings and local readiness candidate [skip ci]')
git('push', 'origin', 'HEAD:refs/heads/' + branch)
print('Applied the five checked source deltas and removed temporary transport files.')
