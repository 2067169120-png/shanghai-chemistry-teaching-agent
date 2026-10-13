"""Explicit local resource handoff. Full ranges and images are checked first."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from .desktop_lesson_composition import block, resource, suggested_resource_title, validate_resource
from .desktop_preparation_images import PreparationImageStore, image_info, normalize_image_assets, verify_image_bytes
from .reader_cancellation import check_read_cancelled


def import_questions(facade, selections, existing_assets, *, expected_reference=None):
    """Retain full source blocks, every picture occurrence and answer scope.

    Structural completeness is checked through the existing native scope
    validator. This grants no chemical, human-review or publication status.
    All selected sources and merged image capacity are checked before copying.
    """
    from .offline_word_image_label_review import _native_scope
    from .offline_word_label_review import _source_blocks, _digest
    from .desktop_word_question_attributes import validate_attributes, suggest_attributes
    from .desktop_composition_inline import NativeParagraphDisplay
    from .desktop_question_classroom_copy import classroom_question_copy
    if not isinstance(selections,list) or not 1 <= len(selections) <= 20:
        raise ValueError('每次请选择1至20道完整题。')
    if expected_reference is not None and (not isinstance(expected_reference,dict)
            or expected_reference.get('selections') != selections
            or expected_reference.get('include_images') is not True):
        raise ValueError('完整题接入须先预览题目和全部原图，请重新预览。')
    words = facade._word_questions(); items = []; assets = {}; payloads = {}
    with words._lock:
        rows, inventory = words._resolve(selections, read_only=True)
        for row in rows:
            check_read_cancelled()
            source, _batch, preview, _index = inventory[row['source_id']]
            if sha256(source.content).hexdigest() != row['source_sha256']:
                raise ValueError('题目来源已变化，请刷新后重选。')
            stored_attributes = words.attribute_store.get(row['key'],
                source_sha256=row['source_sha256'], question_revision=row['revision'])
            # Unsaved teaching labels are not a prerequisite for reading a
            # complete native scope. Use source-bound material counts in memory
            # only; no labels, difficulty claims or review status are written.
            # A saved row for an earlier range stays in its attribute history;
            # its old image counts must not describe this current source scope.
            attributes = validate_attributes(stored_attributes if stored_attributes is not None
                else suggest_attributes(row, {'source_name': row['source_name']}))
            has_images = any(b.get('assets') for name in ('context_blocks','question_blocks','answer_blocks') for b in row[name])
            if has_images:
                scope, image_bytes = _native_scope(words,row,attributes,preview,source)
                groups = scope['blocks']
            else:
                _source_blocks(row,attributes,preview,mode='recheck_automatic')
                groups = {name: deepcopy(row[name]) for name in ('context_blocks','question_blocks','answer_blocks')}
                scope = {'identity':{key:row[key] for key in ('key','revision','source_sha256','source_revision')},'blocks':groups}
                image_bytes = {}
            blocks = []
            paragraph_display=NativeParagraphDisplay(source.content)
            for group, role in (('context_blocks','shared_material'),('question_blocks','question'),('answer_blocks','answer')):
                for native in groups[group]:
                    text_block=block('text',role,native['text'],native['index']);blocks.append(text_block)
                    paragraph_images=[]
                    objects=[o for o in scope.get('objects',[]) if o['block_index']==native['index']]
                    pictures=([next(a for a in native['assets'] if a['asset_id']==o['asset_id']) for o in objects]
                              if objects else native.get('assets',[]))
                    for picture in pictures:
                        data = image_bytes[picture['asset_id']]; digest = sha256(data).hexdigest()
                        identity = 'IMG-' + digest
                        image_block=block('image',role,identity,native['index']);blocks.append(image_block);paragraph_images.append(image_block)
                        asset = normalize_image_assets([{'asset_id':identity,'sha256':digest,
                            'caption':f"{row['source_label']} · 区块{native['index']}",
                            'source':row['source_name']+'；原图SHA-256：'+picture['sha256'],
                            'purpose':'完整题原图；按每次题面或教师答案引用的角色排版。',**image_info(data)}])[0]
                        # Same bytes can occur in multiple roles. Metadata is
                        # shared, while every ordered role occurrence is kept.
                        assets.setdefault(identity,asset); payloads[identity]=data
                    paragraph_display.bind(text_block,paragraph_images,objects)
            evidence = {'source_path':str(facade.imported_word_path(row['batch_id'],row['archive_source_id'])),
                'source_sha256':row['source_sha256'],'source_revision':row['source_revision'],
                'question_key':row['key'],'scope_review_sha256':_digest(scope),'scope_status':'complete_native_scope',
                'hierarchy':dict.fromkeys(('paper_id','theme_big_question_id','printed_question_id','atomic_part_id'),'unknown')}
            source_title = row['source_label']
            if source_title == row['source_name']:
                source_title = Path(source_title).stem
            else: source_title += ' · ' + Path(row['source_name']).stem
            source_title += f" · 原文区块{row['block_start']}—{row['block_end']}"
            item = resource('complete_question',source_title,blocks,evidence)
            item = classroom_question_copy(item)
            item['title'] = suggested_resource_title(item)
            items.append(validate_resource(item))
        # Compare the displayed reference after all native scopes are checked,
        # while holding the same service lock and before copying any image.
        if expected_reference is not None and words.reference(selections,include_images=True) != expected_reference:
            raise ValueError('选题内容已变化，请刷新目录并重新预览后加入课件。')
        merged = normalize_image_assets(existing_assets)
        known = {a['asset_id'] for a in merged}; additions = [a for key,a in assets.items() if key not in known]
        merged = normalize_image_assets(merged+additions)
        for asset in assets.values(): verify_image_bytes(asset,payloads[asset['asset_id']])
        store = PreparationImageStore(Path(facade.paths.task_root)/'preparation-v1/images')
        for asset in existing_assets: store.load(asset)
        for asset in additions:
            check_read_cancelled()
            if store.import_bytes(payloads[asset['asset_id']],asset['caption'],asset['source'],asset['purpose']) != asset:
                raise ValueError('本地图片保存与完整源图不一致。')
    return {'resources':items,'image_assets':merged}


def _same_question_source(first, second):
    """A verified source copy keeps its identity when its storage path moves."""
    import re
    hashes = ('source_sha256', 'source_revision', 'scope_review_sha256')
    if (all(isinstance(row.get(key), str) and re.fullmatch(r'[a-f0-9]{64}', row[key])
            for row in (first, second) for key in hashes)
            and all(isinstance(row.get('question_key'), str)
                    and row['question_key'] not in {'', 'unknown'} for row in (first, second))):
        first = {k:v for k,v in first.items() if k != 'source_path'}
        second = {k:v for k,v in second.items() if k != 'source_path'}
    return first == second


def merge_question_result(result, existing_resources, existing_assets):
    """Append explicit full questions once; preserve current work copies/assets."""
    if (not isinstance(result,dict) or set(result)!={'resources','image_assets'}
            or not isinstance(result['resources'],list) or not 1<=len(result['resources'])<=20):
        raise ValueError('完整题接入内容不完整，请返回资料库重新预览。')
    rows=[validate_resource(r) for r in result['resources']]
    if any(r['original']['kind']!='complete_question' for r in rows):
        raise ValueError('此入口只接收已核对原文范围的完整题。')
    current=normalize_image_assets(existing_assets)
    incoming=normalize_image_assets(result['image_assets'])
    kept=[]
    for row in rows:
        if not any(old['original']['kind']=='complete_question'
                and _same_question_source(old['original']['evidence'], row['original']['evidence'])
                for old in [*existing_resources,*kept]):
            kept.append(row)
    used={b['value'] for r in kept for b in r['blocks'] if b['kind']=='image'}
    known={a['asset_id'] for a in current}
    additions=[a for a in incoming if a['asset_id'] in used and a['asset_id'] not in known]
    merged=normalize_image_assets(current+additions)
    if used-{a['asset_id'] for a in merged}:
        raise ValueError('完整题的原图记录缺失，请重新选择题目。')
    return {'resources':kept,'image_assets':merged}


def import_knowledge_note(facade, selection):
    if isinstance(selection,dict) and selection.get('kind')=='textbook_concept':
        from .desktop_textbook_concept_cards import import_concept_card
        return import_concept_card(facade,selection)
    if isinstance(selection,dict) and selection.get('kind')=='personal':
        return import_personal_knowledge_note(facade,selection)
    from .desktop_textbook_bookshelf import TextbookBookshelfService
    facade.textbook_reading_reference([selection])
    rows = facade.textbook_bookshelf()['reading_notes']
    current = next((row for row in rows if all(row.get(k)==v for k,v in selection.items())),None)
    if current is None: raise ValueError('知识卡已变化，请重新选择。')
    registry, revision, _parents, _sections = TextbookBookshelfService(facade.paths.workspace_root)._directory()
    if revision != selection['directory_revision']: raise ValueError('教材目录已变化，请刷新。')
    node = next(row for row in registry['nodes'] if row['node_key']==current['section_key'])
    evidence = {'source_path':str(Path(facade.paths.workspace_root)/node['source_path']),
        'source_sha256':current['textbook_sha256'],'source_revision':current['revision'],
        'record_id':current['note_id'],'pdf_pages':current['pdf_pages'],'printed_pages':current['printed_pages']}
    return resource('knowledge_note',current['title'],[
        block('text','knowledge',current['summary']),
        block('text','knowledge','适用范围与待核对处：'+current['limitation']),
        block('text','teacher_note','课堂核对建议：'+current['classroom_check'])],evidence)


def _checked_textbook_crop_resource(facade, request):
    """Rebuild a source-bound crop resource without publishing image copies."""
    from .desktop_textbook_bookshelf import TextbookBookshelfService
    preview = facade.preparation_textbook_crop_preview(request['selection'],request['page_revision'],
        request['bounds'],request['caption'],request['purpose'])
    if (preview['preview_revision'] != request['expected_preview_revision']
            or preview['image_asset']['sha256'] != request['expected_image_sha256']):
        raise ValueError('教材裁片已变化，请重新核对。')
    origin = preview['provenance']
    if origin.get('source_kind')=='personal_textbook_page':
        workspace=facade.textbook_workspace()
        workspace.read_book(origin['source_id'],origin['source_sha256'])
        path=workspace.root/(origin['source_sha256']+'.pdf')
    else:
        registry, _revision, _parents, _sections = TextbookBookshelfService(facade.paths.workspace_root)._directory()
        volume = next((r for r in registry['volumes'] if r['volume_id']==origin['volume_id']
                       and r['source_sha256']==origin['source_sha256']),None)
        if volume is None: raise ValueError('已登记教材原图的来源已变化，请刷新后重选。')
        path=Path(facade.paths.workspace_root)/volume['source_path']
    evidence = {'source_path':str(path),
        'source_sha256':origin['source_sha256'],'source_revision':origin['revision'],
        'pdf_pages':[origin['pdf_page']],
        'printed_pages':[origin['printed_page']] if type(origin['printed_page']) is int else 'unknown',
        'crop_id':origin['crop_id']}
    item = resource('textbook_image',request['caption'],[
        block('image','material',preview['image_asset']['asset_id']),
        block('text','teacher_note',request['purpose'])],evidence)
    return item


def import_textbook_crop(facade, request, existing_assets):
    item = _checked_textbook_crop_resource(facade, request)
    result = facade.import_textbook_preparation_crops([request],existing_assets)
    return {'resources':[item],'image_assets':result['image_assets']}


def validate_textbook_crop_requests(requests):
    """The destination is UI state, never part of the sealed crop request."""
    from .desktop_preparation_images import MAX_IMAGES
    from .desktop_textbook_preparation_crops import CROP_REQUEST_FIELDS
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_IMAGES:
        raise ValueError(f'每次请选择1至{MAX_IMAGES}张教材裁片。')
    if any(not isinstance(row, dict) or set(row) != CROP_REQUEST_FIELDS
            or not isinstance(row['selection'], dict)
            or set(row['selection']) != {'visual_asset_id', 'revision'}
            or row['selection_confirmed'] is not True for row in requests):
        raise ValueError('某张教材裁片尚未完整确认，请重新预览。')
    return deepcopy(requests)


def _textbook_crop_use(original):
    # Reconstruction generates fresh block UUIDs; compare source content,
    # ordered roles and teacher purpose rather than those transient IDs.
    return {'kind': original['kind'], 'title': original['title'], 'evidence': original['evidence'],
            'blocks': [{key: value for key, value in row.items() if key != 'id'}
                       for row in original['blocks']]}


def merge_textbook_crop_result(result, existing_resources, existing_assets):
    """Keep one use of each original crop, title and teaching purpose."""
    if not isinstance(result, dict) or set(result) != {'resources', 'image_assets'}:
        raise ValueError('教材裁片交接内容不完整，请重新选图。')
    rows = [validate_resource(row) for row in result['resources']]
    if any(row['original']['kind'] != 'textbook_image' for row in rows):
        raise ValueError('教材裁片入口只接收完整裁选的原图。')
    current = normalize_image_assets(existing_assets)
    incoming = normalize_image_assets(result['image_assets'])
    kept = []
    for row in rows:
        # Original includes its title and ordered purpose block. A second
        # pedagogical use of the same pixels must remain a separate resource.
        if not any(_textbook_crop_use(old['original']) == _textbook_crop_use(row['original'])
                   for old in [*existing_resources, *kept]):
            kept.append(row)
    used = {b['value'] for row in kept for b in row['blocks'] if b['kind'] == 'image'}
    known = {asset['asset_id'] for asset in current}
    merged = normalize_image_assets(current + [asset for asset in incoming
        if asset['asset_id'] in used and asset['asset_id'] not in known])
    if used - {asset['asset_id'] for asset in merged}:
        raise ValueError('教材裁片的图片记录缺失，请重新选图。')
    return {'resources': kept, 'image_assets': merged}


def import_textbook_crops_for_composition(facade, requests, existing_assets, existing_resources, *, guard=None):
    """Recheck every page and hand one complete native batch to the canvas.

    Cached image bytes and immutable receipts can survive cancellation; this
    function neither edits the lesson nor grants source publication rights.
    """
    choices = validate_textbook_crop_requests(requests)

    def current():
        check_read_cancelled()
        if guard is not None: guard()

    current()
    if len(choices) == 1:
        class CheckedFacade:
            def __getattr__(self, name): return getattr(facade, name)
            def import_textbook_preparation_crops(self, selected, assets):
                current()
                value = facade.import_textbook_preparation_crops(selected, assets)
                if type(value.get('requested_count')) is not int or value['requested_count'] != len(selected):
                    raise ValueError('教材裁片未能完整加入，请重新选图。')
                return value
        result = import_textbook_crop(CheckedFacade(), choices[0], existing_assets)
    else:
        rows = []
        for request in choices:
            current(); rows.append(_checked_textbook_crop_resource(facade, request))
        current()
        batch = facade.import_textbook_preparation_crops(choices, existing_assets)
        if type(batch.get('requested_count')) is not int or batch['requested_count'] != len(choices):
            raise ValueError('教材裁片未能整批加入，请重新选图。')
        result = {'resources': rows, 'image_assets': batch['image_assets']}
    # A later page read or local copy must not conceal an earlier source change.
    for request, row in zip(choices, result['resources'], strict=True):
        current()
        if _textbook_crop_use(_checked_textbook_crop_resource(facade, request)['original']) != _textbook_crop_use(row['original']):
            raise ValueError('教材原页在读取期间变化，请重新选图。')
    current()
    return merge_textbook_crop_result(result, existing_resources, existing_assets)


def knowledge_options(facade):
    """List local metadata only; original PDF is read when explicitly selected."""
    snapshot=facade.textbook_bookshelf()
    sections={s['section_key']:s for s in snapshot.get('sections',[])}
    rows=[]
    for note in snapshot.get('reading_notes',[]):
        section=sections.get(note['section_key'],{})
        rows.append({**note,'origin':'registered',
            'book_key':'registered:'+note['volume_id'], 'section_filter_key':note['section_key'],
            'volume_title':section.get('volume_title','册名待核对'),
            'chapter_title':section.get('chapter_title',''),
            'section_title':section.get('title','章节待核对')})
    for book in facade.personal_textbook_study().books():
        selection={k:book[k] for k in ('source_id','revision')}
        view=facade.personal_textbook_study().book_study(selection)
        sections={s['section_id']:s for s in view['sections']}
        for note in view['notes']:
            evidence=note.get('source_evidence',{})
            rows.append({'origin':'personal','title':note['title'],'summary':note['text'],
                'book_key':'personal:'+book['source_id'],
                'volume_title':book['source_name'], 'section_id':note['section_id'],
                'section_filter_key':'personal:'+book['source_id']+':'+note['section_id'],
                'source_name':book['source_name'],'section_title':sections[note['section_id']]['title'],
                'pdf_pages':evidence.get('pdf_pages',[note['pdf_page']]),'printed_pages':'unknown',
                'limitation':evidence.get('limitation','个人整理笔记；印刷页码与化学内容仍须对照原书核对。'),
                'selection':{'kind':'personal','book':selection,'study_revision':view['study_revision'],
                             'note':{k:note[k] for k in ('note_id','revision')}}})
    from .desktop_textbook_concept_cards import concept_options
    concepts=concept_options(facade.paths.workspace_root,snapshot) if snapshot.get('concepts') else []
    return {'reading_notes':rows,'concepts':concepts}


def knowledge_selection(row):
    return deepcopy(row['selection']) if 'selection' in row else {
        k:row[k] for k in ('note_id','section_key','revision','directory_revision')}


def import_knowledge_cards(facade, selections):
    """Recheck the whole explicit batch before returning any classroom cards."""
    import json
    if not isinstance(selections,list) or not 1<=len(selections)<=30:
        raise ValueError('每次请选择1至30张知识卡。')
    identities=[json.dumps(s,ensure_ascii=False,sort_keys=True) for s in selections]
    if len(set(identities))!=len(identities):
        raise ValueError('本批知识卡重复，请重新选择。')
    cards=[import_knowledge_note(facade,s) for s in selections]
    options=knowledge_options(facade)
    current=[knowledge_selection(r) for r in options['concepts']+options['reading_notes']]
    if any(s not in current for s in selections):
        raise ValueError('本批知识卡在核对期间变化，请重新选择。')
    return {'resources':cards}


def merge_knowledge_result(result, existing_resources):
    """Keep each source-bound note version once, retaining edited work copies."""
    if (not isinstance(result, dict) or set(result) != {'resources'}
            or not isinstance(result['resources'], list) or not 1 <= len(result['resources']) <= 30
            or not isinstance(existing_resources, list)):
        raise ValueError('知识卡接入内容不完整，请返回教材重新预览。')
    rows = [validate_resource(row) for row in result['resources']]
    existing = [validate_resource(row) for row in existing_resources]
    if any(row['original']['kind'] != 'knowledge_note' for row in rows):
        raise ValueError('此入口只接收已选择的教材知识卡。')
    def same(first, second):
        # Validated hashes bind bytes and note revisions. A backup can move the
        # file path; unknown record identities cannot establish equivalence.
        if all(row['record_id'] not in {'', 'unknown'} for row in (first, second)):
            first = {key: value for key, value in first.items() if key != 'source_path'}
            second = {key: value for key, value in second.items() if key != 'source_path'}
        return first == second
    kept = []
    for row in rows:
        if not any(old['original']['kind'] == 'knowledge_note'
                   and same(old['original']['evidence'], row['original']['evidence'])
                   for old in [*existing, *kept]):
            kept.append(row)
    return {'resources': kept}


def knowledge_reader_selection(facade, row):
    """Recheck the note as well as the original before opening a read-only page.

    Filter labels are display metadata. Only version-bound selection fields
    identify a note, and neither this handoff nor the viewer imports material.
    """
    if row.get('origin')=='textbook_concept':
        from .desktop_textbook_concept_cards import checked_concept
        current, _raw, _source=checked_concept(facade.paths.workspace_root,row['selection'],read_source=True)
        return {'reading_mode':'concept_card','selection':deepcopy(current['selection'])}
    if row.get('origin')=='personal':
        chosen=row['selection']
        facade.personal_textbook_reference(chosen['book'],chosen['study_revision'],[chosen['note']])
        view=facade.personal_textbook_study().book_study(chosen['book'])
        if view['study_revision']!=chosen['study_revision']:
            raise ValueError('个人教材笔记已变化，请刷新后重选。')
        note=next((n for n in view['notes'] if all(n.get(k)==v for k,v in chosen['note'].items())),None)
        if note is None:raise ValueError('个人教材笔记已变化，请刷新后重选。')
        pages=note.get('source_evidence',{}).get('pdf_pages',[note['pdf_page']])
        return {'reading_mode':'book','selection':{**chosen['book'],
            'section_id':note['section_id'],'study_revision':chosen['study_revision'],
            'initial_pdf_page':pages[0]}}
    chosen={k:row[k] for k in ('note_id','section_key','revision','directory_revision')}
    facade.textbook_reading_reference([chosen])
    current=next((n for n in facade.textbook_bookshelf()['reading_notes']
                  if all(n.get(k)==v for k,v in chosen.items())),None)
    if current is None:raise ValueError('知识卡已变化，请刷新后重选。')
    return {'reading_mode':'section','selection':{'section_key':chosen['section_key'],
        'revision':chosen['directory_revision'],'initial_pdf_page':current['pdf_pages'][0]}}


def import_personal_knowledge_note(facade, selection):
    if set(selection)!={'kind','book','study_revision','note'}:
        raise ValueError('个人教材笔记选择记录不完整，请刷新后重选。')
    facade.personal_textbook_reference(selection['book'],selection['study_revision'],[selection['note']])
    view=facade.personal_textbook_study().book_study(selection['book'])
    if view['study_revision']!=selection['study_revision']:
        raise ValueError('个人教材笔记已变化，请重新选择。')
    note=next((n for n in view['notes'] if all(n.get(k)==v for k,v in selection['note'].items())),None)
    if note is None:raise ValueError('个人教材笔记已变化或缺失，请重新选择。')
    workspace=facade.textbook_workspace()
    workspace.read_book(selection['book']['source_id'],selection['book']['revision'])
    origin=note.get('source_evidence',{})
    evidence={'source_path':str(workspace.root/(view['book']['source_sha256']+'.pdf')),
        'source_sha256':view['book']['source_sha256'],'source_revision':note['revision'],'record_id':note['note_id'],
        'pdf_pages':origin.get('pdf_pages',[note['pdf_page']]),'printed_pages':'unknown'}
    rows=[block('text','knowledge',note['text']),
          block('text','knowledge','适用范围与待核对处：'+origin.get('limitation','个人整理笔记；印刷页码与化学内容仍须对照原书核对。'))]
    if origin.get('source_quote'):
        rows.append(block('text','teacher_note','原页依据（待核对）：'+origin['source_quote']))
    return resource('knowledge_note',note['title'],rows,evidence)


def textbook_image_options(facade):
    return {**facade.preparation_textbook_asset_options(),
            'local_books':facade.personal_textbook_study().books()}


def knowledge_textbook_options(facade, item):
    """Keep membership checks per crop while reusing one sealed projection."""
    check_read_cancelled()
    card = validate_resource(item)
    stage = getattr(facade, '_section_source_stage', None)
    if stage is None:
        return _knowledge_textbook_options_native(facade, card)
    selected = deepcopy(card)
    scope = {key: selected[key] for key in ('id', 'original_sha256', 'original')}
    scope['read_port'] = {'facade': id(facade),
                         'workspace_root': str(getattr(getattr(facade, 'paths', None), 'workspace_root', ''))}
    return stage.read_memo('knowledge_textbook_options', scope,
        lambda: _knowledge_textbook_options_native(facade, deepcopy(selected)))


def _knowledge_textbook_options_native(facade, item):
    """Same-section candidates and explicit note pages; metadata is not a relevance verdict."""
    import json
    from .desktop_note_page_crops import note_page_selection
    from .desktop_personal_textbook_crops import book_page_selection
    original = item['original']
    if original['kind'] != 'knowledge_note':
        raise ValueError('请先选择一张知识卡。')
    evidence = original['evidence']
    if 'concept_source' in evidence:
        return _concept_textbook_options(facade,item)
    snapshot = facade.textbook_bookshelf()
    matches = [r for r in snapshot['reading_notes'] if r['note_id'] == evidence['record_id']]
    rows = []
    if matches:
        if len(matches) != 1:
            raise ValueError('知识卡身份重复，请核对本地笔记。')
        note = matches[0]
        if (note['revision'] != evidence['source_revision']
                or note['textbook_sha256'] != evidence['source_sha256']
                or note['pdf_pages'] != evidence['pdf_pages']
                or note['printed_pages'] != evidence['printed_pages']):
            raise ValueError('这张知识卡的来源版本已变化，请重新加入知识卡后选图。')
        section = next(s for s in snapshot['sections'] if s['section_key'] == note['section_key'])
        catalog = facade.preparation_textbook_asset_options()
        assets = [a for a in catalog['assets'] if a['section_key'] == note['section_key']
                  and a['source_sha256'] == note['textbook_sha256']]
        assets.sort(key=lambda a: (a['pdf_page'] not in note['pdf_pages'], a['pdf_page'], a['visual_asset_id']))
        for a in assets:
            rows.append({**a, 'relation': '同页图表' if a['pdf_page'] in note['pdf_pages'] else '本节其他图表',
                         'selection': {k: a[k] for k in ('visual_asset_id', 'revision')}})
        for page in note['pdf_pages']:
            printed = note['printed_pages']
            rows.append({'origin': 'knowledge_note_page', 'relation': '知识卡原页',
                         'label': f'打开原页并框选 · PDF {page}', 'pdf_page': page,
                         'printed_page': printed[note['pdf_pages'].index(page)] if isinstance(printed, list) else None,
                         'volume_title': section['volume_title'], 'section_title': section['title'],
                         'selection': note_page_selection(note, page)})
        binding = {k: note[k] for k in ('note_id', 'revision', 'directory_revision', 'section_key')}
        notices = catalog['notices']
    else:
        books = [b for b in facade.personal_textbook_study().books()
                 if b['source_sha256'] == evidence['source_sha256']]
        if len(books) != 1:
            raise ValueError('这张知识卡对应的教材或笔记已缺失，请重新加入知识卡后选图。')
        book = books[0]; selected = {k: book[k] for k in ('source_id', 'revision')}
        view = facade.personal_textbook_study().book_study(selected)
        notes = [n for n in view['notes'] if n['note_id'] == evidence['record_id']
                 and n['revision'] == evidence['source_revision']]
        if len(notes) != 1:
            raise ValueError('个人教材知识卡已变化，请重新加入知识卡后选图。')
        note = notes[0]; pages = note.get('source_evidence', {}).get('pdf_pages', [note['pdf_page']])
        if pages != evidence['pdf_pages']:
            raise ValueError('个人知识卡的原页范围已变化，请重新选择。')
        binding = {'book': selected, 'study_revision': view['study_revision'],
                   'note': {k: note[k] for k in ('note_id', 'revision')}}
        for page in pages:
            rows.append({'origin': 'knowledge_note_page', 'relation': '知识卡原页',
                         'source_name': book['source_name'], 'label': f'打开原页并框选 · PDF {page}',
                         'pdf_page': page, 'printed_page': None, 'selection': book_page_selection(book, page)})
        notices = []
    binding = sha256(json.dumps({'original': original, 'binding': binding}, ensure_ascii=False,
                                sort_keys=True, separators=(',', ':')).encode('utf8')).hexdigest()
    return {'rows': rows, 'binding': binding, 'notices': notices, 'candidate_only': True}


def _concept_textbook_options(facade,item):
    import json
    from .desktop_textbook_concept_cards import checked_concept, concept_page_selection
    validate_resource(item)
    evidence=item['original']['evidence'];source=evidence['concept_source']
    selected={'kind':'textbook_concept','concept_id':evidence['record_id'],
        'revision':evidence['source_revision'],'directory_revision':source['directory_revision'],
        'location':source['location']}
    row,raw,_pdf=checked_concept(facade.paths.workspace_root,selected)
    if (raw!=source['record'] or raw['source_sha256']!=evidence['source_sha256']
            or row['pdf_pages']!=evidence['pdf_pages'] or row['printed_pages']!=evidence['printed_pages']):
        raise ValueError('知识摘要的原书或页码已变化，请重新加入知识卡后选图。')
    snapshot=facade.textbook_bookshelf()
    location=selected['location'];rows=[]
    for asset in sorted(snapshot['assets'],key=lambda a:(a['pdf_page'] not in row['pdf_pages'],a['pdf_page'],a['visual_asset_id'])):
        same_scope=(location['section_key'] is not None and asset.get('section_key')==location['section_key']
            or location['supplement_node_key'] is not None
            and asset.get('resolved_supplement_node_key')==location['supplement_node_key'])
        if same_scope and asset['source_sha256']==raw['source_sha256']:
            rows.append({**asset,'relation':'同页图表' if asset['pdf_page'] in row['pdf_pages'] else '本栏目其他图表',
                         'selection':{k:asset[k] for k in ('visual_asset_id','revision')}})
    if row['volume_id'] is not None:
        for index,page in enumerate(row['pdf_pages']):
            rows.append({'origin':'textbook_concept_page','relation':'知识摘要原页',
                'label':f'打开原页并框选 · PDF {page}','pdf_page':page,
                'printed_page':row['printed_pages'][index] if isinstance(row['printed_pages'],list) else None,
                'volume_title':row['volume_title'],'section_title':row['section_title'],
                'selection':concept_page_selection(row,page)})
    binding=sha256(json.dumps({'original':item['original'],'binding':selected},ensure_ascii=False,
                             sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
    return {'rows':rows,'binding':binding,'notices':snapshot['notices'],'candidate_only':True}


def import_knowledge_textbook_crop(facade, item, binding, request, existing_assets):
    return import_knowledge_textbook_crops(facade, item, binding, [request], existing_assets)


def import_knowledge_textbook_crops(facade, item, binding, requests, existing_assets):
    """Publish one complete crop batch selected from the same sealed card."""
    from .desktop_lesson_composition import bind_knowledge_selection
    from .desktop_preparation_images import MAX_IMAGES
    from .desktop_textbook_preparation_crops import CROP_REQUEST_FIELDS
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_IMAGES:
        raise ValueError(f'每次请选择1至{MAX_IMAGES}张教材裁片。')
    if any(not isinstance(request, dict) or set(request) != CROP_REQUEST_FIELDS
            or not isinstance(request['selection'], dict) or request['selection_confirmed'] is not True
            for request in requests):
        raise ValueError('某张教材裁片的确认内容不完整，请重新预览。')
    card = validate_resource(item)
    choices = deepcopy(requests)

    def recheck():
        check_read_cancelled()
        current_card = validate_resource(item)
        if current_card['id'] != card['id'] or current_card['original'] != card['original']:
            raise ValueError('知识卡原内容已变化，请重新从知识卡选图。')
        current = knowledge_textbook_options(facade, item)
        if (current['binding'] != binding
                or any(request['selection'] not in [r['selection'] for r in current['rows']] for request in choices)):
            raise ValueError('知识卡或图表候选已变化，请重新从知识卡选图。')

    recheck()
    if len(choices) == 1:
        # Keep the ordinary single-crop handoff and its public entry point.
        # It delegates once to the same facade batch/cache transaction.
        class CheckedSingleCropFacade:
            def __getattr__(self, name):
                return getattr(facade, name)

            def import_textbook_preparation_crops(self, selected, assets):
                recheck()
                return facade.import_textbook_preparation_crops(selected, assets)

        result = import_textbook_crop(CheckedSingleCropFacade(), choices[0], existing_assets)
    else:
        rows = []
        for request in choices:
            check_read_cancelled()
            rows.append(_checked_textbook_crop_resource(facade, request))
        # Knowledge may have changed while later original pages were read.
        recheck()
        batch = facade.import_textbook_preparation_crops(choices, existing_assets)
        if type(batch.get('requested_count')) is not int or batch['requested_count'] != len(choices):
            raise ValueError('教材裁片未能整批核对，请重新选图。')
        result = {'resources': rows, 'image_assets': batch['image_assets']}
    # Normalize and bind in memory, then recheck the card once at the final
    # return gate. Late changes can retain closed cache receipts, but release
    # no partial cards.
    assets = normalize_image_assets(result['image_assets'])
    if (len(result['resources']) != len(choices)
            or {b['value'] for row in result['resources'] for b in row['blocks'] if b['kind'] == 'image'}
            - {asset['asset_id'] for asset in assets}):
        raise ValueError('教材裁片的整批图片记录缺失，请重新选图。')
    rows = []
    for image in result['resources']:
        check_read_cancelled()
        rows.append(bind_knowledge_selection(image, card))
    recheck()
    return {'resources': rows, 'image_assets': assets}
