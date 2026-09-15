"""Publish only same-revision Windows verification artifacts, never personal data."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
VERSION = '0.1.96'
BRANCH = 'feature/student-review-desk-0.1.96'
TAG = 'v' + VERSION


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    if os.environ.get('GITHUB_EVENT_NAME') != 'push' or os.environ.get('GITHUB_REF_NAME') != BRANCH:
        raise RuntimeError('Publishing is limited to a push on the version branch')
    sha, repo = os.environ['GITHUB_SHA'], os.environ['GITHUB_REPOSITORY']
    assert command('git','rev-parse','HEAD') == sha
    assert command('git','ls-remote','origin','refs/heads/'+BRANCH).split()[0] == sha, 'Branch advanced'
    assert not command('git','ls-remote','--tags','origin','refs/tags/'+TAG), 'Never move an existing tag'
    source = ROOT / '.release-input'
    def read(relative):
        return json.loads((source/relative).read_text(encoding='utf-8'))
    ready = read('readiness-qa/onboarding-smoke.json')
    packaged = read('package-qa/package-verification.json')
    ready['scan_numbering'] = read('readiness-qa/scan/scan-probe.json')
    ready['font_scaling'] = read('readiness-qa/font-scaling/scaling-summary.json')
    ready['explorer_performance'] = read('readiness-qa/explorer/explorer-probe.json')
    ready['performance_benchmark'] = read('readiness-qa/explorer-performance.json')
    ready['student_review'] = read('readiness-qa/student-review/student-review-probe.json')
    for report in (ready,packaged):
        assert report['version'] == VERSION and report['source_commit'] == sha
        assert not report['uncaught_errors'] and len(report['routes_opened']) == 8
        review = report['student_review']
        assert review['version'] == VERSION and review['source_commit'] == sha
        assert all(review['checks'].values()) and not review['uncaught_errors']
        assert review['network_requests'] == 0 and review['review_transport_requests'] == 0
        assert review['teacher_score_records'] == 2 and review['teacher_diagnosis_records'] == 1
        assert len(review['geometry']) == 2 and all(row['actions_visible'] for row in review['geometry'])
        explorer = report['explorer_performance']
        assert explorer['source_commit'] == sha and explorer['version'] == VERSION
        assert all(explorer['checks'].values()) and not explorer['uncaught_errors'] and explorer['model_calls'] == 0
        fonts = report['typography']
        assert fonts['chinese_sample_supported'] and fonts['editor_unchanged']
        assert not fonts['han']['missing_glyphs'] and not fonts['chemistry']['missing_glyphs']
        assert fonts['actions_visible_800x700'] and fonts['model_calls'] == 0
        scan = report['scan_numbering']
        assert scan['source_commit'] == sha and scan['version'] == VERSION and scan['model_calls'] == 0
        assert scan['edits'] == 4 and all(scan[k] for k in ('raw_word_import','native_entry_opened',
            'undo_exercised','two_audiences_exported','outside_regions_unchanged','source_unchanged'))
        assert all(scan['pages'][role] > 0 for role in ('student','teacher'))
        assert all(scan['region_reuse'].values()), 'Region reuse or answer pagination not verified'
        scales = report['font_scaling']
        assert [r['requested_scale'] for r in scales] == ['1','1.25','1.5']
        assert all(r['qt_platform'] == 'windows' and not r['han']['missing_glyphs'] and
            not r['chemistry']['missing_glyphs'] and r['actions_visible_800x700'] for r in scales)
        assert report['lesson_backup']['restored_production_export'] and report['work_organization']['task_lifecycle']
        assert report['paper_numbering']['source_bytes_unchanged'] and report['teacher_desk']['resume_preserved_editor']
    assert packaged['frozen'] and packaged['detached_directory'] and packaged['normal_native_start_and_close']
    assert packaged['cleared_python_and_workspace_environment'] and not packaged['system_fonts_bundled']
    assert packaged['lesson_backup']['native_restored_cli_start_and_close']
    assert packaged['editable_pptx_slides'] == 5 and packaged['pdf_pages'] == 2
    suites = list(ET.parse(source/'readiness-qa/pytest.xml').getroot().iter('testsuite'))
    tests = {key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    assert tests['tests'] >= 1089 and not any(tests[k] for k in ('failures','errors','skipped'))
    target = ROOT/'docs/screenshots'/TAG
    target.mkdir(parents=True,exist_ok=True)
    names=set()
    def copy_image(folder, record, name=None):
        source_name=record['file'];name=name or source_name
        assert Path(source_name).name == source_name and Path(name).name == name and name.endswith('.png')
        assert name not in names
        data=(folder/source_name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record['sha256']
        (target/name).write_bytes(data);names.add(name)
    for report,folder in ((ready,source/'readiness-qa'),(packaged,source/'package-qa/probe'),
                          (packaged['scan_numbering'],source/'package-qa/scan'),
                          (packaged['explorer_performance'],source/'package-qa/explorer'),
                          (packaged['student_review'],source/'package-qa/student-review')):
        for record in report['screenshots']:
            copy_image(folder,record)
    for scale in ('1','1.25','1.5'):
        prefix='scale-'+scale.replace('.','_')
        folder=source/'package-qa/font-scaling'/prefix
        report=next(r for r in packaged['font_scaling'] if r['requested_scale']==scale)
        for name in ('typography-sample.png','typography-compact.png'):
            copy_image(folder,next(r for r in report['screenshots'] if r['file']==name),prefix+'-'+name)
    ready.update(tests=tests,workflow_run=os.environ['GITHUB_RUN_ID'])
    write_json(ROOT/f'docs/qa/{VERSION}-readiness.json',ready)
    write_json(ROOT/f'docs/qa/{VERSION}-package.json',packaged)
    readme=ROOT/'README.md';text=readme.read_text(encoding='utf-8')
    assert '{{VERIFICATION_SUMMARY}}' in text
    text=text.replace('源码候选（等待本版验收）', '原生试用版')
    text=text.replace('{{VERIFICATION_SUMMARY}}',
        f"同一提交完成 **{tests['tests']}项Windows定向测试，0失败、0错误、0跳过**。"
        '源码与同一发行EXE均从学生分析实际入口打开同屏工作区，核对原页、证据框、独立教师改分、切题未记录输入、诊断、重开和800×700操作布局；另一名合成学生和AI原稿不变。'
        '原有选题、中文/化学符号及Qt倍率、组卷、作品整理、备课备份和文档输出继续回归。本轮没有真实学生数据与网络模型请求，固定传输夹具只用来准备样例。')
    for image in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text):
        assert (ROOT/image).is_file(),image
    readme.write_text(text,encoding='utf-8')
    command('git','config','user.name','github-actions[bot]')
    command('git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    command('git','add','-f','README.md',str(target.relative_to(ROOT)),
            f'docs/qa/{VERSION}-readiness.json',f'docs/qa/{VERSION}-package.json')
    command('git','commit','-m','docs: record 0.1.96 verified student original-page and teacher-scoring workspace [skip ci]')
    delivery_sha=command('git','rev-parse','HEAD')
    command('git','push','origin','HEAD:refs/heads/'+BRANCH)
    output=ROOT/'release-delivery';output.mkdir(exist_ok=True)
    windows_name=f'ShanghaiChem-{VERSION}-Windows-x64.zip'
    windows_zip=source/'release-assets'/windows_name
    with ZipFile(windows_zip) as bundle:
        assert not any(Path(n).suffix.lower() in ('.ttf','.ttc','.otf','.woff','.woff2') for n in bundle.namelist())
        builds=[n for n in bundle.namelist() if n.endswith('/BUILD.json')]
        assert len(builds)==1 and json.loads(bundle.read(builds[0]))['source_commit']==sha
    shutil.copyfile(windows_zip,output/windows_name)
    command('git','archive','--format=zip','--output='+str(output/f'ShanghaiChem-{VERSION}-source.zip'),'HEAD')
    with ZipFile(output/f'ShanghaiChem-{VERSION}-Windows-QA.zip','w',ZIP_DEFLATED) as z:
        for folder in ('readiness-qa','package-qa'):
            for path in sorted((source/folder).rglob('*')):
                if path.is_file(): z.write(path,str(path.relative_to(source)))
    import markdown
    from markdown.extensions.toc import slugify_unicode
    html=markdown.markdown(text,extensions=['tables','fenced_code','toc'],extension_configs={'toc':{'slugify':slugify_unicode}})
    def embed(match):
        path=ROOT/match[1]
        return ('src="data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()+'"'
                if path.is_file() and path.suffix=='.png' else match[0])
    html=re.sub(r'src="([^"]+)"',embed,html)
    def link(match):
        href=match[1]
        return match[0] if href.startswith(('#','https:','http:','mailto:')) else 'href="https://github.com/'+repo+'/blob/'+TAG+'/'+href+'"'
    html=re.sub(r'href="([^"]+)"',link,html)
    ids=set(re.findall(r'id="([^"]+)"',html))
    assert all(href[1:] in ids for href in re.findall(r'href="([^"]+)"',html) if href.startswith('#'))
    (output/f'ShanghaiChem-{VERSION}-Guide.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>沪上化学智研台'+VERSION+'</title><style>body{max-width:1050px;margin:40px auto;padding:0 24px;font:17px/1.8 system-ui}img{max-width:100%}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px}pre{overflow:auto;background:#f5f5f5;padding:16px}</style>'+html+'</html>',encoding='utf-8')
    assets=[{'file':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(output.iterdir()) if p.is_file()]
    write_json(output/'RELEASE-MANIFEST.json',{'version':VERSION,'tag':TAG,'tested_source_commit':sha,
               'release_commit':delivery_sha,'workflow_run':os.environ['GITHUB_RUN_ID'],'tests':tests,'unsigned_trial':True,'assets':assets})
    (output/'SHA256SUMS.txt').write_text('\n'.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name
        for p in sorted(output.iterdir()) if p.is_file())+'\n',encoding='utf-8')
    command('git','tag','-a',TAG,'-m',VERSION+' verified Windows trial; code '+sha)
    command('git','push','origin','refs/tags/'+TAG)
    command('gh','release','create',TAG,'--repo',repo,'--verify-tag','--draft','--prerelease',
        '--title',VERSION+' · 原作答与评分同屏','--notes-file',f'docs/releases/{VERSION}.md',
        *[str(p) for p in sorted(output.iterdir()) if p.is_file()])
    command('gh','release','edit',TAG,'--repo',repo,'--draft=false','--latest=false')
    print('Published',TAG,'tested',sha,'delivery',delivery_sha)


if __name__=='__main__': main()
