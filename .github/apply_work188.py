"""Apply the exact reviewed software delta once, then remove transport files."""
import base64
import hashlib
import lzma
import os
from pathlib import Path
import subprocess
import tempfile

BRANCH = 'feature/work-organization-0.1.88'
ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
assert os.environ.get('GITHUB_REF_NAME') == BRANCH
parts = [f'.github/work188-part{i}' for i in (1, 2, 3)]
patch = lzma.decompress(base64.b64decode(''.join(Path(p).read_text(encoding='utf-8') for p in parts), validate=True))
assert hashlib.sha256(patch).hexdigest() == '4a160a54204b55613c00667d80d9f4cfdac6668b56e549d1c6d9b84cba95e385'

def run(*args):
    return subprocess.check_output(args, text=True).strip()

assert run('git', 'ls-remote', 'origin', 'refs/heads/' + BRANCH).split()[0] == run('git', 'rev-parse', 'HEAD')
with tempfile.TemporaryDirectory() as name:
    path = Path(name) / 'software.patch'
    path.write_bytes(patch)
    run('git', 'apply', '--check', str(path))
    run('git', 'apply', '--index', str(path))
run('git', 'config', 'user.name', 'github-actions[bot]')
run('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
run('git', 'rm', *parts, '.github/apply_work188.py', '.github/workflows/stage-work188.yml')
print(run('git', 'commit', '-m', 'feat(work): names, archive and reversible trash for existing teacher works [skip ci]'))
print(run('git', 'push', 'origin', 'HEAD:refs/heads/' + BRANCH))
print('Source revision:', run('git', 'rev-parse', 'HEAD'))
