"""Reproducible synthetic timings: search only vs batch source/image read.

No speed thresholds on shared runners, no model requests and no private data.
The index build/cold read is reported separately from subsequent searches.
"""
from __future__ import annotations
from pathlib import Path
import json
import os
import platform
import statistics
import sys
import tempfile
from time import perf_counter
ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT),str(ROOT/'staging/coordination/deeptutor_gateway/tests')]
from test_explorer_speed import source_catalog, make_images_fixture
from integrations.deeptutor_shchem_v1.desktop_explorer_index import PersonalSearchIndex
from integrations.deeptutor_shchem_v1.desktop_question_explorer import personal_results
from integrations.deeptutor_shchem_v1.desktop_word_image_batch import load_word_image_batch
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_version import DESKTOP_VERSION


def timed(fn):
    start=perf_counter();result=fn();return (perf_counter()-start)*1000,result


def stats(samples):
    return {'samples_ms':[round(v,3) for v in samples], 'median_ms':round(statistics.median(samples),3),
            'max_ms':round(max(samples),3)}


def main():
    output=Path(sys.argv[1]);output.parent.mkdir(parents=True,exist_ok=True)
    result={'version':DESKTOP_VERSION,'source_commit':os.environ.get('GITHUB_SHA','local'),
            'platform':platform.platform(),'python':platform.python_version(),'search':[],
            'scope':'Synthetic search adapter and source image reads, not whole-window timing; no API/real library.'}
    for size in (1000,5000,10000):
        catalog=source_catalog(size)
        # Includes 383 uniquely identified chapter/section nodes, not purported textbooks.
        catalog['attribute_catalog']['nodes'] += [
            {'node_key':f'extra{i}','volume_id':'V1','chapter_id':f'C{i}',
             'volume_title':'合成册','chapter_title':f'合成章{i}','section_title':'合成节'} for i in range(380)]
        build_ms,index=timed(lambda:PersonalSearchIndex(catalog,'word_native'))
        old,new,paging=[],[],[]
        for i in range(5):
            query=('ALPHA','CONTEXT','原题','知识','')[i]
            elapsed,a=timed(lambda:personal_results(catalog,'word_native',{},query,0,8));old.append(elapsed)
            elapsed,b=timed(lambda:index.search({},query,0,8));new.append(elapsed)
            assert {k:v for k,v in b.items() if k!='performance'}==a
            elapsed,c=timed(lambda:index.search({},query,1,8));paging.append(elapsed)
            assert c['performance']['query_cache_hit']
        result['search'].append({'items':size,'directory_nodes':383,'index_build_ms':round(build_ms,3),
                                 'old_search':stats(old),'indexed_new_query':stats(new),'indexed_next_page':stats(paging)})
    with tempfile.TemporaryDirectory(prefix='shchem-perf195-') as temp:
        root=Path(temp);ws=root/'workspace';(ws/'integrations/deeptutor_shchem_v1').mkdir(parents=True)
        paths=DesktopPaths.from_workspace(ws,state_root=root/'personal')
        paths.ensure_mutable_roots()
        facade,row,ids=make_images_fixture(paths,root,count=8,questions=80)
        try:
            cold,cat=timed(facade.word_question_catalog)
            old,new=[],[]
            for _ in range(5):
                elapsed,expected=timed(lambda:[facade.word_question_image(row['key'],row['revision'],a) for a in ids]);old.append(elapsed)
                outputs=[]
                elapsed,report=timed(lambda:load_word_image_batch(facade,row['key'],row['revision'],ids,progress=outputs.append));new.append(elapsed)
                assert [v['result'] for v in outputs]==expected
            result['word_images']={'questions_in_source':80,'visible_images':8,'warm_catalog_read_ms':round(cold,3),
                'old_per_image':stats(old),'new_one_source_parse':stats(new),'pixel_bytes_equal':True,
                'source_resolutions_before':8,'source_resolutions_after':1}
        finally:facade.shutdown()
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
