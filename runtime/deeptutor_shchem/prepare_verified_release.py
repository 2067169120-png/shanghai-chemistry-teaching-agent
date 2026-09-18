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
from documentation_policy import archived_readmes, check_documentation

ROOT = Path(__file__).resolve().parents[2]
VERSION = '0.1.101'
BRANCH = 'feature/lesson-source-0.1.101'
TAG = 'v' + VERSION


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    assert not archived_readmes(ROOT), "Keep history in Git; remove versioned README copies before release"
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
    ready['work_batch'] = read('readiness-qa/work-batch/work-batch-probe.json')
    ready['exam_analysis'] = read('readiness-qa/exam/exam-probe.json')
    ready['teaching_closure'] = read('readiness-qa/closure/closure-probe.json')
    ready['lesson_design'] = read('readiness-qa/lesson-design/lesson-design-probe.json')
    ready['lesson_import'] = read('readiness-qa/lesson-import/lesson-import-probe.json')
    for report in (ready,packaged):
        imported = report['lesson_import']
        assert imported['version'] == VERSION and imported['source_commit'] == sha
        assert all(imported['checks'].values()) and not imported['uncaught_errors']
        assert imported['network_requests'] == 0 and imported['import_model_calls'] == 0
        assert imported['actual_restored_pptx'] is True
        lesson = report['lesson_design']
        assert lesson['version'] == VERSION and lesson['source_commit'] == sha
        assert all(lesson['checks'].values()) and not lesson['uncaught_errors']
        assert lesson['model_calls'] == 0 and lesson['slides'] == 3
        closure = report['teaching_closure']
        assert closure['version'] == VERSION and closure['source_commit'] == sha
        assert all(closure['checks'].values()) and not closure['uncaught_errors']
        assert closure['network_requests'] == 0
        exam = report['exam_analysis']
        assert exam['version'] == VERSION and exam['source_commit'] == sha
        assert all(exam['checks'].values()) and not exam['uncaught_errors']
        assert exam['network_requests'] == 0 and exam['mean'] == 69 and exam['valid_totals'] == 10
        assert report['version'] == VERSION and report['source_commit'] == sha
        assert not report['uncaught_errors'] and len(report['routes_opened']) == 8
        batch = report['work_batch']
        assert batch['version'] == VERSION and batch['source_commit'] == sha
        assert all(batch['checks'].values()) and not batch['uncaught_errors']
        assert batch['network_requests'] == 0 and batch['review_transport_requests'] == 0
        assert len(batch['geometry']) == 2 and all(g['actions_visible'] for g in batch['geometry'])
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
    assert tests['tests'] >= 1279 and not any(tests[k] for k in ('failures','errors','skipped'))
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
                          (packaged['student_review'],source/'package-qa/student-review'),
                          (packaged['work_batch'],source/'package-qa/work-batch'),
                          (packaged['exam_analysis'],source/'package-qa/exam'),
                          (packaged['teaching_closure'],source/'package-qa/closure'),
                          (packaged['lesson_design'],source/'package-qa/lesson-design'),
                          (packaged['lesson_import'],source/'package-qa/lesson-import')):
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
        '源码与同一发行EXE均验证从已有初稿选择转换、保留教师原环节、撤销重做、锁定材料、本地三类成品生成、草稿及恢复保存、ZIP独立恢复后三文件读取与实际PPTX重新预览。原教学目标与输出版本、评分统计、题库复练、复测和考试流程继续回归。'
        '原有选题、中文/化学符号及Qt倍率、组卷、作品整理、备课备份和文档输出继续回归。本轮没有真实学生数据与网络模型请求，固定传输夹具只用来准备样例。')
    for image in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text):
        assert (ROOT/image).is_file(),image
    readme.write_text(text,encoding='utf-8')
    doc_errors = check_documentation(ROOT)
    assert not doc_errors, '\n'.join(doc_errors)
    command('git','config','user.name','github-actions[bot]')
    command('git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    command('git','add','-f','README.md',str(target.relative_to(ROOT)),
            f'docs/qa/{VERSION}-readiness.json',f'docs/qa/{VERSION}-package.json')
    command('git','commit','-m','docs: record verified 0.1.101 existing-result handoff, restored outputs and full functional guide [skip ci]')
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
    import sys
    sys.path.insert(0, str(ROOT))
    from integrations.deeptutor_shchem_v1.desktop_exam_template import template_bytes
    (output/f'ShanghaiChem-{VERSION}-Score-Import-Example.xlsx').write_bytes(template_bytes())
    shutil.copyfile(source/'package-qa/exam/synthetic-exam-report.html',output/f'ShanghaiChem-{VERSION}-Synthetic-Exam-Report.html')
    notes_text = (ROOT/f'docs/ux/{VERSION}-lesson-design.md').read_text(encoding='utf-8')
    notes_html = markdown.markdown(notes_text, extensions=['tables','fenced_code'])
    (output/f'ShanghaiChem-{VERSION}-Lesson-Design-Notes.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>教学环节与同源输出</title>'
        '<style>body{max-width:1100px;margin:36px auto;padding:0 24px;font:17px/1.8 system-ui}'
        'table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:10px}</style>'
        + notes_html + '</html>', encoding='utf-8')
    with ZipFile(output/f'ShanghaiChem-{VERSION}-Synthetic-Lesson.zip','w',ZIP_DEFLATED) as archive:
        folder=source/'package-qa/lesson-design'
        for name in ('lesson_presentation.pptx','lesson_plan.docx','student_worksheet.docx',
                     'actual-pptx.pdf','lesson_plan.pdf','student_worksheet.pdf','lesson-design.json'):
            assert (folder/name).is_file(),name
            archive.write(folder/name,name)
    assets=[{'file':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(output.iterdir()) if p.is_file()]
    write_json(output/'RELEASE-MANIFEST.json',{'version':VERSION,'tag':TAG,'tested_source_commit':sha,
               'release_commit':delivery_sha,'workflow_run':os.environ['GITHUB_RUN_ID'],'tests':tests,'unsigned_trial':True,'assets':assets})
    (output/'SHA256SUMS.txt').write_text('\n'.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name
        for p in sorted(output.iterdir()) if p.is_file())+'\n',encoding='utf-8')
    command('git','tag','-a',TAG,'-m',VERSION+' verified Windows trial; code '+sha)
    command('git','push','origin','refs/tags/'+TAG)
    # Use the ID returned by creation. Draft lookup by tag was unreliable in
    # previous releases; upload_url is the documented authoritative endpoint.
    import mimetypes, time, urllib.request, urllib.parse
    payload={'tag_name':TAG,'target_commitish':delivery_sha,'name':VERSION+' · 教学环节与同源教案/PPT/学习单',
             'draft':True,'prerelease':True,'make_latest':'false',
             'body':(ROOT/f'docs/releases/{VERSION}.md').read_text(encoding='utf-8')}
    created=subprocess.check_output(['gh','api',f'repos/{repo}/releases','-X','POST','--input','-'],
                                   input=json.dumps(payload,ensure_ascii=False), text=True)
    release=json.loads(created); release_id=release['id']
    write_json(output/'RELEASE-STATUS.json', {'release_id':release_id,'tag':TAG,'draft':True})
    upload=release['upload_url'].split('{')[0]
    assert upload==f'https://uploads.github.com/repos/{repo}/releases/{release_id}/assets'
    for path in sorted(output.iterdir()):
        if not path.is_file() or path.name=='RELEASE-STATUS.json':continue
        expected=hashlib.sha256(path.read_bytes()).hexdigest()
        for attempt in range(3):
            info=json.loads(command('gh','api',f'repos/{repo}/releases/{release_id}'))
            assert info['draft'] and info['tag_name']==TAG
            asset=next((a for a in info['assets'] if a['name']==path.name),None)
            if asset and asset['state']=='uploaded':
                assert asset['size']==path.stat().st_size and asset.get('digest')=='sha256:'+expected,path.name
                break
            if asset:
                assert asset['state']=='starter' and not asset.get('digest'),path.name
                command('gh','api',f"repos/{repo}/releases/assets/{asset['id']}",'-X','DELETE')
            try:
                request=urllib.request.Request(upload+'?name='+urllib.parse.quote(path.name), data=path.read_bytes(),
                    headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],
                             'Accept':'application/vnd.github+json',
                             'Content-Type':mimetypes.guess_type(path.name)[0] or 'application/octet-stream'},method='POST')
                with urllib.request.urlopen(request,timeout=300) as response:
                    uploaded=json.load(response)
                assert uploaded['size']==path.stat().st_size and uploaded.get('digest')=='sha256:'+expected,path.name
                break
            except (OSError, ValueError):
                if attempt==2:raise
                time.sleep(5*(attempt+1))
        else:raise RuntimeError('Asset upload not verified: '+path.name)
    command('gh','api',f'repos/{repo}/releases/{release_id}','-X','PATCH','-F','draft=false','-f','make_latest=false')
    write_json(output/'RELEASE-STATUS.json', {'release_id':release_id,'tag':TAG,'draft':False})
    print('Published',TAG,'tested',sha,'delivery',delivery_sha)


if __name__=='__main__': main()
