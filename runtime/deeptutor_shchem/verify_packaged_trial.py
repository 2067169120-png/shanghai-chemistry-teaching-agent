"""Run the shipping binary in a copied directory with no developer Python path."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
APP = '沪上化学智研台'


def main():
    package = Path(sys.argv[1]).resolve()
    qa = ROOT/'package-qa'
    qa.mkdir(exist_ok=True)
    # Copy dependency notices, not fonts or arbitrary installed data.
    notices = package/'THIRD_PARTY_LICENSES'
    notices.mkdir(exist_ok=True)
    names = ('PySide6','PySide6_Essentials','PySide6_Addons','shiboken6','Pillow','python-docx',
             'python-pptx','pypdf','pypdfium2','jsonschema','lxml','attrs','referencing','rpds-py','XlsxWriter','typing_extensions')
    versions = []
    for name in names:
        dist = metadata.distribution(name)
        versions.append(dict(name=name, version=dist.version))
        for file in dist.files or ():
            if (file.name.lower().startswith(('license','copying','notice','copyright'))
                    and file.suffix.lower() not in ('.py','.pyc','.dll','.pyd')):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    destination = notices/name/file.name
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(source,destination)
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.is_file(): shutil.copyfile(python_license,notices/'Python-LICENSE.txt')
    (package/'COMPONENTS.json').write_text(json.dumps(versions,indent=2),encoding='utf-8')
    fonts=[str(p.relative_to(package)) for p in package.rglob('*') if p.suffix.lower() in ('.ttf','.ttc','.otf','.woff','.woff2')]
    assert not fonts, 'Do not redistribute system/font files: '+str(fonts)
    (package/'使用前请读.txt').write_text('沪上化学智研台 0.1.89 Windows x64 试用版\n请先完整解压，再双击“沪上化学智研台.exe”。不要单独移动EXE或_internal。\n无需另装Python。真实DOCX分页需要本机LibreOffice或Microsoft Word。\n不含教材、题库、模型密钥。首次打开空库正常；设置中的本机检查按功能说明缺项。\n未进行数字签名，不承诺SmartScreen信誉；请核对GitHub Release来源与SHA256，勿关闭系统防护。\n旧VBS可能打开旧版。已有源码旁题库不会自动复制进此程序；可继续使用旧源码环境或明确配置原工作区。\n',encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='shchem-detached-') as temporary:
        temp=Path(temporary)
        detached=temp/'app'
        shutil.copytree(package,detached)
        exe=detached/(APP+'.exe')
        env=os.environ.copy()
        for key in ('PYTHONHOME','PYTHONPATH','SHCHEM_WORKSPACE_ROOT','QT_QPA_PLATFORM','QT_PLUGIN_PATH','QML2_IMPORT_PATH'):
            env.pop(key,None)
        system_root = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
        if not system_root:
            raise RuntimeError('Windows system directory is unavailable')
        env['PATH']=os.pathsep.join((str(Path(system_root)/'System32'),system_root))
        env['LOCALAPPDATA']=str(temp/'profile')
        normal=subprocess.Popen([str(exe)],cwd=temp,env=env)
        found=[]
        user32=ctypes.WinDLL('user32',use_last_error=True)
        callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        user32.IsWindowVisible.argtypes=[wintypes.HWND]
        user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        user32.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        @callback
        def visit(hwnd, _):
            pid=wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value==normal.pid and user32.IsWindowVisible(hwnd): found.append(hwnd)
            return True
        started=time.monotonic()
        try:
            while not found and time.monotonic()-started<45:
                assert normal.poll() is None, 'Normal executable exited before opening'
                user32.EnumWindows(visit,0)
                time.sleep(.1)
            assert found, 'Normal native window did not open'
            time.sleep(1)
            user32.PostMessageW(found[0],0x0010,0,0)
            assert normal.wait(timeout=20)==0
        finally:
            if normal.poll() is None: normal.kill();normal.wait()
            for file in (temp/'profile').rglob('startup-error.log'):
                shutil.copyfile(file,qa/'normal-startup-error.log')
        errors=list((temp/'profile').rglob('startup-error.log'))
        for file in errors: shutil.copyfile(file,qa/'normal-startup-error.log')
        assert not errors, 'Normal executable wrote startup error'
        probe=temp/'probe'
        env['QT_QPA_PLATFORM']='offscreen'
        run=subprocess.run([str(exe),'--verify-package',str(probe)],cwd=temp,env=env,timeout=240)
        if probe.exists(): shutil.copytree(probe,qa/'probe',dirs_exist_ok=True)
        for file in (temp/'profile').rglob('startup-error.log'):
            shutil.copyfile(file,qa/'probe-startup-error.log')
        assert run.returncode==0, 'Packaged smoke failed; see collected diagnostic'
        report=json.loads((probe/'package-probe.json').read_text(encoding='utf-8'))
        assert report['frozen'] and report['module_origin']=='bundle'
        assert report['pdf_pages'] and not report['uncaught_errors']
        restored_name=(probe/'backup-demo/restored-directory.txt').read_text(encoding='utf-8').strip()
        restored=probe/'backup-demo'/restored_name
        assert restored.is_dir() and Path(restored_name).name==restored_name
        found.clear()
        native_env=env.copy();native_env.pop('QT_QPA_PLATFORM',None)
        normal=subprocess.Popen([str(exe),'--personal-state',str(restored)],cwd=temp,env=native_env)
        started=time.monotonic()
        try:
            while not found and time.monotonic()-started<45:
                assert normal.poll() is None, 'Restored-profile executable exited early'
                user32.EnumWindows(visit,0);time.sleep(.1)
            assert found, 'Restored-profile window did not open'
            title=ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
            user32.GetWindowTextW(found[0],title,512)
            assert '独立恢复副本' in title.value, 'Wrong personal profile was opened'
            user32.PostMessageW(found[0],0x0010,0,0)
            assert normal.wait(timeout=20)==0
        finally:
            if normal.poll() is None:normal.kill();normal.wait()
        report['lesson_backup']['native_restored_cli_start_and_close']=True
        report.update(source_commit=os.environ['GITHUB_SHA'], workflow_run=os.environ['GITHUB_RUN_ID'],
            executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
            normal_native_start_and_close=True, detached_directory=True,
            cleared_python_and_workspace_environment=True, system_fonts_bundled=False)
        (qa/'package-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (package/'BUILD.json').write_text(json.dumps({'version':'0.1.89','source_commit':os.environ['GITHUB_SHA'],
        'workflow_run':os.environ['GITHUB_RUN_ID'],'trial':True},indent=2),encoding='utf-8')
    release=ROOT/'release-assets';release.mkdir(exist_ok=True)
    shutil.make_archive(str(release/'ShanghaiChem-0.1.89-Windows-x64'),'zip',package.parent,package.name)


if __name__=='__main__': main()
