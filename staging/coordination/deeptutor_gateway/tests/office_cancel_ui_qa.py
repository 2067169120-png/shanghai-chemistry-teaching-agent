"""Explicitly non-Office Qt screenshots, geometry and test evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from .no_office_plugin import block_office_activation
    import pytest
    from PySide6.QtCore import qVersion
    previous=os.environ.get('SHCHEM_OFFICE_CANCEL_UI_OUTPUT')
    os.environ['SHCHEM_OFFICE_CANCEL_UI_OUTPUT']=str(output)
    arguments=[str(Path(__file__).with_name('test_office_cancel_ui.py')),'-q','-p','no:cacheprovider',
               '-p','staging.coordination.deeptutor_gateway.tests.no_office_plugin',
               '--junitxml='+str(output/'tests.xml')]
    try:
        # The direct script itself installs the guard, independent of pytest.
        with block_office_activation():code=pytest.main(arguments)
    finally:
        if previous is None:os.environ.pop('SHCHEM_OFFICE_CANCEL_UI_OUTPUT',None)
        else:os.environ['SHCHEM_OFFICE_CANCEL_UI_OUTPUT']=previous
    images=[json.loads(line) for line in (output/'screenshots.jsonl').read_text(encoding='utf-8').splitlines()] if (output/'screenshots.jsonl').exists() else []
    manifest={'schema':'shchem.office-cancel-ui-qa.v1','office':False,'synthetic_only':True,
        'provider_calls':0,'personal_state_accessed':False,'qt_version':qVersion(),'pytest_exit_code':int(code),
        'arguments':arguments,'images':images,'junit_sha256':hashlib.sha256((output/'tests.xml').read_bytes()).hexdigest(),
        'limitations':['Converter surrogate is a real owned Python process, not Office.',
                      'Exam dashboard narrow size is 800x700; its existing minimum remains 720x570.',
                      'After stop, backend keeps the old approved pagination; editor requires preview/confirmation again.']}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'manifest':str(output/'manifest.json'),'images':len(images),'office':False}))
    return int(code)


if __name__=='__main__':raise SystemExit(main())
