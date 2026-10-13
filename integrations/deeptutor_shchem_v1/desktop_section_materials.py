"""Read-only, original-bound section material choices for classroom resources.

Section membership supplies candidates, never a saved knowledge relationship.
The context contains no PDF payload and grants no review or publication gate.
"""
from copy import deepcopy
from hashlib import sha256

from .desktop_composition_sources import (
    import_knowledge_note, knowledge_options, knowledge_reader_selection,
    knowledge_selection, knowledge_textbook_options)
from .desktop_knowledge_questions import _selection_and_request, _source_content
from .desktop_knowledge_reading import knowledge_card_reader
from .desktop_lesson_composition import FLAGS, digest, validate_resource
from .desktop_preparation_images import normalize_image_assets
from .desktop_textbook_bookshelf import TextbookBookshelfService
from .reader_cancellation import check_read_cancelled

SCHEMA = 'shchem.section-material-context.v1'


def _unique(rows, message):
    if len(rows) != 1:
        raise ValueError(message)
    return rows[0]


def _registered_scopes(facade, volume_id, source_sha256, directory_revision=None):
    shelf = TextbookBookshelfService(facade.paths.workspace_root)
    registry, revision, _parents, sections = shelf._directory()
    volume = _unique([row for row in registry['volumes'] if row['volume_id'] == volume_id
                      and row['source_sha256'] == source_sha256],
                     '教材原书身份或版本无法核对，请重新选择。')
    if directory_revision is not None and directory_revision != revision:
        raise ValueError('教材目录版本已变化，请重新选择。')
    book_key = 'registered:' + volume_id
    common = {'book_key': book_key, 'book_label': volume['volume_title']}
    scopes = [{**common, 'section_filter_key': row['section_key'],
               'section_label': row['title'], 'section_kind': 'registered',
               'selection': {'mode': 'registered', 'section_key': row['section_key'],
                             'revision': revision}}
              for row in sections if row['volume_id'] == volume_id]
    snapshot = shelf.snapshot()
    scopes.extend({**common, 'section_filter_key': row['supplement_node_key'],
                   'section_label': row['title'], 'section_kind': 'supplement',
                   'selection': {'mode': 'supplement',
                                 'supplement_node_key': row['supplement_node_key'],
                                 'revision': row['revision']}}
                  for row in snapshot.get('supplements', [])
                  if row['volume_id'] == volume_id and row['source_sha256'] == source_sha256)
    if len({row['section_filter_key'] for row in scopes}) != len(scopes):
        raise ValueError('教材章节身份重复，请刷新后重新选择。')
    return common, scopes, {'directory_revision': revision}, snapshot


def _personal_scopes(facade, book_selection, source_sha256):
    study = facade.personal_textbook_study()
    book = _unique([row for row in study.books()
                    if {key: row[key] for key in ('source_id', 'revision')} == book_selection
                    and row['source_sha256'] == source_sha256],
                   '个人教材身份或原书版本无法核对，请重新选择。')
    view = study.book_study(book_selection)
    if (view['book']['source_id'] != book['source_id']
            or view['book']['source_sha256'] != source_sha256
            or view['book']['revision'] != book_selection['revision']):
        raise ValueError('个人教材章节与原书身份不一致，请刷新后重新选择。')
    common = {'book_key': 'personal:' + book['source_id'], 'book_label': book['source_name']}
    scopes = []
    for row in view['sections']:
        if row['source_id'] != book['source_id'] or row['source_sha256'] != source_sha256:
            raise ValueError('个人教材章节与原书版本不一致，请刷新后重新选择。')
        scopes.append({**common,
            'section_filter_key': common['book_key'] + ':' + row['section_id'],
            'section_label': row['title'], 'section_kind': 'personal',
            'selection': {'mode': 'personal', 'selection': deepcopy(book_selection),
                          'section_id': row['section_id'], 'study_revision': view['study_revision']}})
    if len({row['section_filter_key'] for row in scopes}) != len(scopes):
        raise ValueError('个人教材章节身份重复，请刷新后重新选择。')
    return common, scopes, {'study_revision': view['study_revision']}, view


def _option_identity(row):
    """Use typed source selections, never titles, filenames or filter labels."""
    selection = knowledge_selection(row)
    kind = selection.get('kind')
    if kind == 'personal':
        return ('personal:' + selection['book']['source_id'],
                'personal:' + selection['book']['source_id'] + ':' + row['section_id'],
                selection['book']['revision'], selection['study_revision'])
    if kind == 'textbook_concept':
        location = selection['location']
        return ('registered:' + (location['volume_id'] or 'unknown'),
                location['section_key'] or location['supplement_node_key'],
                row['source_sha256'], selection['directory_revision'])
    return ('registered:' + row['volume_id'], selection['section_key'],
            row['textbook_sha256'], selection['directory_revision'])


def _selection_scope(facade, row, source_sha256):
    selected = knowledge_selection(row)
    book_key, scope_key, row_sha256, revision = _option_identity(row)
    if (row_sha256 != source_sha256 or row['book_key'] != book_key
            or scope_key is not None and row['section_filter_key'] != scope_key):
        raise ValueError('知识点的原书或章节身份不一致，请刷新后重选。')
    if selected.get('kind') == 'personal':
        common, scopes, versions, _view = _personal_scopes(facade, selected['book'], source_sha256)
        if versions['study_revision'] != revision:
            raise ValueError('个人教材笔记或章节版本已变化，请刷新后重选。')
    else:
        volume_id = (selected['location']['volume_id'] if selected.get('kind') == 'textbook_concept'
                     else row['volume_id'])
        common, scopes, versions, _snapshot = _registered_scopes(
            facade, volume_id, source_sha256, revision)
    if scope_key is not None:
        current = _unique([r for r in scopes if r['section_filter_key'] == scope_key],
                          '知识点对应的章节或补充栏目已缺失，请刷新后重选。')
        if (selected.get('kind') == 'textbook_concept'
                and selected['location']['scope_revision'] != current['selection']['revision']):
            raise ValueError('知识点对应的章节或补充栏目版本已变化，请刷新后重选。')
    return common, scopes, versions, scope_key


def _all_options(facade):
    options = knowledge_options(facade)
    # The public concept projection omits its original PDF hash. Load that
    # literal field from the revision-checked record rather than inventing it
    # from a display label or registered volume.
    concepts = []
    if options['concepts']:
        from .desktop_preparation_sources import PreparationSourcesService, _digest
        records = PreparationSourcesService(facade.paths.workspace_root)._concepts()
        for row in options['concepts']:
            record = records.get(row['concept_id'])
            if record is None or _digest(record) != row['revision']:
                raise ValueError('教材知识摘要的原记录已变化，请刷新后重新选择。')
            concepts.append({**deepcopy(row), 'source_sha256': record['source_sha256']})
    return concepts + options['reading_notes']


def _knowledge_origin(facade, card, options):
    evidence = card['original']['evidence']
    personal = [row for row in options if row.get('origin') == 'personal'
                and row['selection']['note']['note_id'] == evidence['record_id']
                and row['selection']['note']['revision'] == evidence['source_revision']
                and row['selection']['book']['revision'] == evidence['source_sha256']]
    if personal:
        # The registered bookshelf may be unavailable while a complete personal
        # original is readable. Use the reader's same source/content primitives
        # without manufacturing a registered-book metadata fallback.
        row = _unique(personal, '个人知识卡的当前来源身份重复，请重新加入知识卡。')
        selection = knowledge_selection(row)
        current = import_knowledge_note(facade, selection)
        if _source_content(current['original']) != _source_content(card['original']):
            raise ValueError('个人知识卡原内容与当前来源不一致，请重新加入知识卡。')
        reader = knowledge_reader_selection(facade, row)
        reader_binding = digest({'resource_id': card['id'], 'original_sha256': card['original_sha256'],
                                 'knowledge_selection': selection, 'reader': reader})
    else:
        reader = knowledge_card_reader(facade, card)
        selection, _request = _selection_and_request(facade, card)
        row = _unique([row for row in options if knowledge_selection(row) == selection],
                      '知识卡对应的当前知识点缺失或身份重复，请重新加入知识卡。')
        reader_binding = reader['binding_sha256']
    scope = _selection_scope(facade, row, evidence['source_sha256'])
    return scope, selection, {'knowledge_resource_id': card['id'],
        'knowledge_original_sha256': card['original_sha256'],
        'knowledge_reader_binding': reader_binding}


def _checked_crop(facade, card, image_assets):
    """Defend the source boundary even if an adapter returns inconsistent data."""
    result = facade.preparation_textbook_crop_original(card, image_assets)
    source, binding = result['source'], result['binding']
    evidence = card['original']['evidence']
    assets = normalize_image_assets(image_assets)
    identities = [row['value'] for row in card['original']['blocks'] if row['kind'] == 'image']
    asset = _unique([row for row in assets if row['asset_id'] in identities],
                    '教材裁片的图片记录缺失或重复，请恢复后重选。')
    original_source = binding['source']
    printed = [source['printed_page']] if type(source.get('printed_page')) is int else 'unknown'
    if (len(identities) != 1
            or result.get('crop_pixels_verified') is not True or result.get('reading_mode') != 'asset'
            or binding['resource_id'] != card['id']
            or binding['original_sha256'] != card['original_sha256']
            or binding['image_sha256'] != asset['sha256']
            or result['binding_sha256'] != digest(binding)
            or not isinstance(source['pdf_bytes'], bytes) or not source['pdf_bytes']
            or sha256(source['pdf_bytes']).hexdigest() != evidence['source_sha256']
            or source['source_sha256'] != evidence['source_sha256']
            or source['revision'] != evidence['source_revision']
            or type(source['pdf_page']) is not int or [source['pdf_page']] != evidence['pdf_pages']
            or source['pdf_pages'] != evidence['pdf_pages'] or printed != evidence['printed_pages']
            or any(source.get(key) != value for key, value in original_source.items())
            or result['selection'] != {key: source[key] for key in ('visual_asset_id', 'revision')}):
        raise ValueError('教材裁片原页、原书版本或来源绑定不一致，请重新打开原页。')
    return source, {'crop_binding': deepcopy(binding), 'crop_reader_binding': result['binding_sha256']}, result['selection']


def _crop_origin(facade, card, course_resources, image_assets, options):
    source, binding, crop_selection = _checked_crop(facade, card, image_assets)
    linked = card['original'].get('selected_from_knowledge')
    if linked is not None:
        matches = [row for row in course_resources if isinstance(row, dict)
                   and row.get('id') == linked['resource_id']]
        note = validate_resource(_unique(matches, '选图所用的课堂知识卡缺失或身份重复，请恢复原知识卡。'))
        evidence = note['original']['evidence']
        if (note['original']['kind'] != 'knowledge_note'
                or note['original_sha256'] != linked['original_sha256']
                or any(evidence[key] != linked[key] for key in ('record_id', 'source_sha256', 'source_revision'))):
            raise ValueError('选图所用知识卡的封存原内容已变化，请重新从原知识卡选择。')
        scope, selection, note_binding = _knowledge_origin(facade, note, options)
        if selection.get('kind') == 'personal':
            from .desktop_personal_textbook_crops import book_page_selection
            view = facade.personal_textbook_study().book_study(selection['book'])
            allowed = [book_page_selection(view['book'], page) for page in evidence['pdf_pages']]
        else:
            allowed = [row['selection'] for row in knowledge_textbook_options(facade, note)['rows']]
        if crop_selection not in allowed:
            raise ValueError('裁片原页不属于这张知识卡的来源候选，请重新选择。')
        return scope, selection, {**binding, **note_binding}

    source_kind = source.get('source_kind')
    if source_kind == 'section_textbook_page':
        from .desktop_section_page_crops import SectionTextbookPageCatalog, _scope
        chosen, key = _scope(source['section_selection'])
        common, scopes, versions, _snapshot = _registered_scopes(
            facade, source['volume_id'], source['source_sha256'])
        current = _unique([row for row in scopes if row['section_filter_key'] == chosen[key]],
                          '裁片的真实原节或补充栏目已缺失，请重新选择。')
        if current['selection'] != chosen or versions['directory_revision'] != source['directory_revision']:
            # The fresh original-page reader has verified the exact receipt,
            # pixels and explicitly confirmed current physical location. Only
            # that relocation may bridge a raw directory revision; native
            # logical scope and complete registry semantics must still match.
            location = binding['crop_binding'].get('source_location')
            current_source, _registry, identity = SectionTextbookPageCatalog(
                facade.paths.workspace_root).read_scope(current['selection'])
            if (not isinstance(location, dict) or not location.get('revision')
                    or current['selection']['mode'] != chosen['mode']
                    or current['selection'][key] != chosen[key]
                    or current_source['volume_id'] != source['volume_id']
                    or current_source['source_sha256'] != source['source_sha256']
                    or any(identity[field] != source[field] for field in (
                        'section_pdf_pages', 'section_printed_pages', 'section_content_sha256',
                        'section_registry_logical_sha256'))):
                raise ValueError('教材裁片的原节、版本或明确原书位置已变化，请重新选择。')
            chosen = deepcopy(current['selection'])
        if source['pdf_page'] not in source['section_pdf_pages']:
            raise ValueError('教材裁片的完整原节或逐页列表已变化，请重新选择。')
        return (common, scopes, versions, chosen[key]), None, binding
    if source_kind == 'personal_textbook_page':
        book = {'source_id': source['source_id'], 'revision': source['source_sha256']}
        common, scopes, versions, view = _personal_scopes(facade, book, source['source_sha256'])
        matches = [row for row in view['sections']
                   if row['pdf_start'] <= source['pdf_page'] <= row['pdf_end']]
        scope_key = (common['book_key'] + ':' + matches[0]['section_id']) if len(matches) == 1 else None
        return (common, scopes, versions, scope_key), None, binding

    if source_kind in {'knowledge_note_page', 'textbook_concept_page'}:
        if source_kind == 'knowledge_note_page':
            selection = {'note_id': source['record_id'], 'section_key': source['section_key'],
                         'revision': source['note_revision'], 'directory_revision': source['directory_revision']}
        else:
            selection = {'kind': 'textbook_concept', 'concept_id': source['record_id'],
                         'revision': source['concept_revision'], 'directory_revision': source['directory_revision'],
                         'location': deepcopy(source['concept_location'])}
        row = _unique([row for row in options if knowledge_selection(row) == selection],
                      '教材裁片对应的知识点已变化、缺失或身份重复，请重新选择。')
        scope = _selection_scope(facade, row, source['source_sha256'])
        if scope[0]['book_key'] != 'registered:' + source['volume_id']:
            raise ValueError('教材裁片与知识点的原书身份不一致，请重新选择。')
        return scope, selection, binding

    if source_kind is not None:
        raise ValueError('教材裁片来源类型无法核对，请重新选择。')
    common, scopes, versions, snapshot = _registered_scopes(
        facade, source['volume_id'], source['source_sha256'])
    asset = _unique([row for row in snapshot['assets']
                     if row['visual_asset_id'] == source['visual_asset_id']
                     and row['revision'] == source['revision']],
                    '教材图表的当前来源身份缺失或重复，请重新选择。')
    if any(asset[key] != source[key] for key in ('volume_id', 'source_sha256', 'pdf_page')):
        raise ValueError('教材图表与原书页码不一致，请重新选择。')
    scope_key = asset['section_key'] or asset.get('resolved_supplement_node_key')
    if asset.get('supplement_node_key') is not None and scope_key is None:
        raise ValueError('教材图表的补充栏目尚未核对，不能改作正式章节。')
    if scope_key is not None and not any(row['section_filter_key'] == scope_key for row in scopes):
        raise ValueError('教材图表所属章节已变化，请重新选择。')
    return (common, scopes, versions, scope_key), None, binding


def _resolve(facade, item, course_resources, image_assets, requested):
    check_read_cancelled()
    card = validate_resource(item)
    if not isinstance(course_resources, list):
        raise ValueError('当前课堂资源列表无法核对，请重新打开。')
    kind = card['original']['kind']
    if kind not in {'knowledge_note', 'textbook_image'}:
        raise ValueError('请从知识卡或教材裁片打开本节选材。')
    options = _all_options(facade)
    if kind == 'knowledge_note':
        scope, selected, binding = _knowledge_origin(facade, card, options)
    else:
        scope, selected, binding = _crop_origin(facade, card, course_resources, image_assets, options)
    common, scopes, versions, scope_key = scope
    if requested is not None:
        if (not isinstance(requested, str) or not requested
                or scope_key is not None and requested != scope_key
                or not any(row['section_filter_key'] == requested for row in scopes)):
            raise ValueError('请选择已核验原书中的当前章节；来源已定位时不能替换章节。')
        scope_key = requested
    current = next((row for row in scopes if row['section_filter_key'] == scope_key), None)
    evidence = card['original']['evidence']
    candidates = []
    if current is not None:
        expected_revision = versions.get('directory_revision', versions.get('study_revision'))
        for row in options:
            book_key, key, source_sha256, revision = _option_identity(row)
            if book_key != common['book_key'] or key != scope_key:
                continue
            if (row['book_key'] != book_key or row['section_filter_key'] != key
                    or source_sha256 != evidence['source_sha256'] or revision != expected_revision):
                raise ValueError('同节知识点的原书或章节版本失配，请刷新后重新选择。')
            choice = knowledge_selection(row)
            if (choice.get('kind') == 'textbook_concept'
                    and choice['location']['scope_revision'] != current['selection']['revision']):
                raise ValueError('同节知识点的章节或补充栏目范围版本失配，请刷新后重新选择。')
            candidates.append(deepcopy(row))
        identities = [digest(knowledge_selection(row)) for row in candidates]
        if len(set(identities)) != len(identities):
            raise ValueError('本节知识点身份重复，请刷新后重新选择。')
    # An unmapped source can suggest its real book's sections, but its own
    # knowledge point must not become a confirmed member of the teacher's choice.
    if selected is not None and selected not in [knowledge_selection(row) for row in candidates]:
        selected = None
    source_binding = {'resource_id': card['id'], 'original_sha256': card['original_sha256'],
        'source_sha256': evidence['source_sha256'], 'source_revision': evidence['source_revision'],
        'pdf_pages': deepcopy(evidence['pdf_pages']), 'printed_pages': deepcopy(evidence['printed_pages']),
        'requested_section_filter_key': requested, **versions, **binding}
    value = {'schema_version': SCHEMA,
        'status': 'ready' if current else 'section_selection_required' if scopes else 'no_section',
        'source_kind': kind, 'resource_id': card['id'], 'original_title': card['original']['title'],
        **common, 'section_filter_key': scope_key,
        'section_label': current['section_label'] if current else None,
        'section_kind': current['section_kind'] if current else None,
        'section_choices': deepcopy(scopes), 'knowledge_options': candidates,
        'knowledge_selection': deepcopy(selected), 'source_binding': source_binding, **FLAGS}
    value['binding_sha256'] = digest(value)
    check_read_cancelled()
    latest = validate_resource(item)
    if latest['id'] != card['id'] or latest['original'] != card['original']:
        raise ValueError('课堂素材的封存原内容在核对期间变化，请重新选择。')
    return value


def section_material_context(facade, item, course_resources, image_assets, *, section_filter_key=None):
    """Resolve current same-section options without importing or saving anything.

    A missing, stale or unreadable original fails closed. When a checked book
    has no unique section, only its own declared choices are returned. An
    explicit section choice is transient and never rewrites the source card.
    """
    try:
        value = _resolve(facade, item, course_resources, image_assets, section_filter_key)
        if _resolve(facade, item, course_resources, image_assets, section_filter_key) != value:
            raise ValueError('课堂素材或本节知识点在核对期间变化，请重新选择。')
        return value
    except (KeyError, TypeError, StopIteration, OSError, AttributeError) as exc:
        raise ValueError('课堂素材来源或教材章节无法核对，请恢复完整来源后重新选择。') from exc


def recheck_section_material_context(facade, item, course_resources, image_assets, context):
    """Re-resolve the complete transient binding before a material handoff."""
    if isinstance(context, dict) and context.get('entry_kind') == 'textbook_section':
        if item is not None:
            raise ValueError('教材节入口不能替换成另一张课堂素材。')
        return recheck_textbook_section_material_context(facade, context)
    if not isinstance(context, dict) or not isinstance(context.get('source_binding'), dict):
        raise ValueError('本节选材上下文不完整，请从课堂素材重新打开。')
    requested = context['source_binding'].get('requested_section_filter_key')
    if section_material_context(facade, item, course_resources, image_assets,
                                section_filter_key=requested) != context:
        raise ValueError('课堂素材或本节选材范围已变化，请从原素材重新打开。')
    return True


def _textbook_section_context(facade, book_key, section_filter_key, selection):
    """Read the explicitly selected original even when the section has no notes."""
    check_read_cancelled()
    if (not isinstance(book_key, str) or not book_key
            or not isinstance(section_filter_key, str) or not section_filter_key
            or not isinstance(selection, dict)):
        raise ValueError('请在教材页明确选择一本原书和一个完整章节。')
    chosen = deepcopy(selection)
    mode = chosen.get('mode')
    if mode == 'personal':
        if set(chosen) != {'mode', 'selection', 'section_id', 'study_revision'}:
            raise ValueError('个人教材节选择缺少完整的原书与整理版本。')
        study = facade.personal_textbook_study()
        source = study.read(chosen['selection'], section_id=chosen['section_id'],
                            study_revision=chosen['study_revision'])
        source_sha256 = source['source_sha256']
        common, scopes, versions, _view = _personal_scopes(
            facade, chosen['selection'], source_sha256)
        if (source['source_id'] != chosen['selection']['source_id']
                or source['section_id'] != chosen['section_id']
                or source['study_revision'] != chosen['study_revision']):
            raise ValueError('个人教材节与已选原书版本不一致，请刷新后重选。')
    elif mode in {'registered', 'supplement'}:
        identity = 'section_key' if mode == 'registered' else 'supplement_node_key'
        if set(chosen) != {'mode', identity, 'revision'}:
            raise ValueError('教材节选择缺少完整的章节与目录版本。')
        shelf = TextbookBookshelfService(facade.paths.workspace_root)
        source = (shelf.section_source(chosen[identity], chosen['revision'])
                  if mode == 'registered' else
                  shelf.supplement_source(chosen[identity], chosen['revision']))
        source_sha256 = source['source_sha256']
        common, scopes, versions, _snapshot = _registered_scopes(
            facade, source['volume_id'], source_sha256,
            chosen['revision'] if mode == 'registered' else None)
    else:
        raise ValueError('请使用当前教材页中已明确选择的真实章节。')
    current = _unique([row for row in scopes if row['section_filter_key'] == section_filter_key],
                      '所选章节不属于当前原书，请刷新后重选。')
    if common['book_key'] != book_key or current['selection'] != chosen:
        raise ValueError('教材原书、章节或版本与当前选择不一致，请刷新后重选。')
    raw = source['pdf_bytes']
    pages = source['pdf_pages']
    if (not isinstance(raw, bytes) or not raw
            or sha256(raw).hexdigest() != source_sha256
            or not isinstance(pages, list) or not pages
            or any(type(page) is not int or page < 1 for page in pages)):
        raise ValueError('教材原文件、完整节页范围或版本无法核对。')
    candidates = []
    expected_revision = versions.get('directory_revision', versions.get('study_revision'))
    for row in _all_options(facade):
        row_book, row_scope, row_sha, revision = _option_identity(row)
        if row_book != book_key or row_scope != section_filter_key:
            continue
        if (row['book_key'] != row_book or row['section_filter_key'] != row_scope
                or row_sha != source_sha256 or revision != expected_revision):
            raise ValueError('同节知识候选的原书或目录版本不一致，请刷新后重选。')
        choice = knowledge_selection(row)
        if (choice.get('kind') == 'textbook_concept'
                and choice['location']['scope_revision'] != chosen['revision']):
            raise ValueError('知识候选的章节或补充栏目版本已变化，请刷新后重选。')
        candidates.append(deepcopy(row))
    identities = [digest(knowledge_selection(row)) for row in candidates]
    if len(identities) != len(set(identities)):
        raise ValueError('本节知识候选身份重复，请刷新后重选。')
    # Retain original/TOC/asset revisions, never PDF payloads or an invented card.
    metadata = {key: value for key, value in source.items() if key != 'pdf_bytes'}
    binding = {'resource_id': None, 'book_key': book_key,
        'section_filter_key': section_filter_key, 'selection': deepcopy(chosen),
        'source_sha256': source_sha256,
        'source_revision': chosen.get('revision', chosen.get('study_revision')),
        'pdf_pages': deepcopy(pages),
        'printed_pages': deepcopy(source.get('printed_pages')) or 'unknown',
        'source_snapshot_sha256': digest(metadata), **versions}
    value = {'schema_version': SCHEMA, 'entry_kind': 'textbook_section', 'status': 'ready',
        'source_kind': 'textbook_section', 'resource_id': None,
        'original_title': current['section_label'], **common,
        'section_filter_key': section_filter_key, 'section_label': current['section_label'],
        'section_kind': current['section_kind'], 'section_selection': deepcopy(chosen),
        'section_choices': [deepcopy(current)], 'knowledge_options': candidates,
        'knowledge_selection': None, 'source_binding': binding, **FLAGS}
    value['binding_sha256'] = digest(value)
    check_read_cancelled()
    return value


def textbook_section_material_context(facade, book_key, section_filter_key, selection):
    """Resolve one real textbook section without adding any classroom resource."""
    try:
        value = _textbook_section_context(facade, book_key, section_filter_key, selection)
        if _textbook_section_context(facade, book_key, section_filter_key, selection) != value:
            raise ValueError('教材原书、目录或候选在读取期间变化，请重新选择。')
        return value
    except (KeyError, TypeError, StopIteration, OSError, AttributeError) as exc:
        raise ValueError('教材原书或完整章节无法核对，请刷新教材页后重新选择。') from exc


def recheck_textbook_section_material_context(facade, context):
    """Recheck the exact book, section, source bytes and asset/note versions."""
    if (not isinstance(context, dict) or context.get('entry_kind') != 'textbook_section'
            or context.get('resource_id') is not None
            or not {'book_key', 'section_filter_key', 'section_selection'} <= context.keys()):
        raise ValueError('教材节选材上下文不完整，请从教材页重新打开。')
    if textbook_section_material_context(facade, context['book_key'],
            context['section_filter_key'], context['section_selection']) != context:
        raise ValueError('教材原书、章节或选材候选已变化，请从教材页重新打开。')
    return True
