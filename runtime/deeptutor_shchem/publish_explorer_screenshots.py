"""Copy only the verified synthetic selection screenshots into versioned docs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
NAMES=('selection-all','selection-filtered','selection-basket','selection-pagination-student','selection-pagination-teacher','selection-compact')

def main():
    folder=ROOT/'explorer-qa'
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    expected=os.environ.get('SHCHEM_SOURCE_SHA',os.environ['GITHUB_SHA'])
    if report['source_commit']!=expected or report['uncaught_errors'] or report['tests']['failures'] or report['tests']['errors']:
        raise RuntimeError('Screenshot run is not successful or bound to this source')
    target=ROOT/'docs/screenshots/v0.1.85';target.mkdir(parents=True,exist_ok=True)
    records={r['file']:r for r in report['screenshots']}
    paths=[]
    for name in NAMES:
        filename=name+'.png';source=folder/filename
        if hashlib.sha256(source.read_bytes()).hexdigest()!=records[filename]['sha256']:
            raise RuntimeError('Screenshot differs from report')
        destination=target/filename;shutil.copy2(source,destination);paths.append(str(destination.relative_to(ROOT)))
    destination=ROOT/'docs/qa/0.1.85-selection.json'
    shutil.copy2(folder/'report.json',destination);paths.append(str(destination.relative_to(ROOT)))
    subprocess.run(['git','add','-f','--',*paths],cwd=ROOT,check=True)
    result=subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT)
    if result.returncode:
        subprocess.run(['git','commit','-m','docs: update verified 0.1.85 selection and pagination screenshots [skip ci]'],cwd=ROOT,check=True)

if __name__=='__main__':main()
