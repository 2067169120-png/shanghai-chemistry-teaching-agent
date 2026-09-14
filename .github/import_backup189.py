"""One-time verified transfer of the locally tested patch; removed after use."""
from pathlib import Path
import hashlib
import lzma
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

base = subprocess.check_output(['git', 'rev-parse', 'HEAD^'], text=True).strip()
assert base == 'fe6b583e20f8a4a068d26d8f142d32e55738ae8d', base
parts = [Path(f'.github/backup189-{i}.bin') for i in range(1, 7)]
data = b''.join(p.read_bytes() for p in parts)
assert hashlib.sha256(data).hexdigest() == '03ab41385c86248f71e79bc27eca3319f836f57dd984e9e35a99f236019be9b5'
patch = lzma.decompress(data)
assert len(patch) == 145642
with tempfile.NamedTemporaryFile(suffix='.patch') as f:
    f.write(patch); f.flush()
    run('git', 'apply', '--index', '--3way', f.name)
workflow = Path('.github/workflows/lesson-backup.yml')
text = workflow.read_text(encoding='utf-8').replace('branches: ["feature/work-organization-0.1.89"]', 'branches: ["feature/work-organization-0.1.88"]')
workflow.write_text(text, encoding='utf-8')
run('git', 'add', '-f', str(workflow))
run('python', '-m', 'compileall', '-q', 'integrations', 'runtime/deeptutor_shchem')
run('git', 'rm', *map(str, parts), '.github/import_backup189.py', '.github/workflows/import-lesson-backup.yml')
run('git', 'config', 'user.name', 'github-actions[bot]')
run('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
run('git', 'commit', '-m', 'feat(backup): scoped lesson backup, independent restore and image reconnection [skip ci]')
run('git', 'push', 'origin', 'HEAD:refs/heads/feature/lesson-backup-0.1.89')
