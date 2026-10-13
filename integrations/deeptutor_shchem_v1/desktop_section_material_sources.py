"""Direct section materials and Word browsing without an invented knowledge card."""
from copy import deepcopy
from hashlib import sha256

from .desktop_lesson_composition import digest
from .desktop_section_materials import recheck_textbook_section_material_context
from .desktop_section_page_crops import SectionTextbookPageCatalog
from .reader_cancellation import check_read_cancelled


def _checked(facade, context):
    check_read_cancelled()
    if (not isinstance(context, dict) or context.get('entry_kind') != 'textbook_section'
            or context.get('status') != 'ready' or context.get('resource_id') is not None):
        raise ValueError('请先明确选择真实教材原书和完整章节。')
    recheck_textbook_section_material_context(facade, context)
    return deepcopy(context)


def _source(facade, context):
    chosen = context['section_selection']
    if chosen['mode'] == 'personal':
        source = facade.personal_textbook_study().read(chosen['selection'], section_id=chosen['section_id'],
                                                       study_revision=chosen['study_revision'])
    else:
        source = SectionTextbookPageCatalog(facade.paths.workspace_root).read_scope(chosen)[0]
    metadata = {key: value for key, value in source.items() if key != 'pdf_bytes'}
    if (sha256(source['pdf_bytes']).hexdigest() != context['source_binding']['source_sha256']
            or source['source_sha256'] != context['source_binding']['source_sha256']
            or source['pdf_pages'] != context['source_binding']['pdf_pages']
            or digest(metadata) != context['source_binding']['source_snapshot_sha256']):
        raise ValueError('当前原书、完整章节或来源元数据与选材锚点不同。')
    return source


def _options_native(facade, context):
    chosen = _checked(facade, context)
    source = _source(facade, chosen)
    rows, notices = [], []
    if chosen['section_selection']['mode'] != 'personal':
        assets = source['visual_assets']
        notices = deepcopy(assets.get('notices', []))
        for asset in assets['assets']:
            if (asset['source_sha256'] != chosen['source_binding']['source_sha256']
                    or asset['pdf_page'] not in chosen['source_binding']['pdf_pages']):
                raise ValueError('教材图目录候选越过当前原书或完整章节。')
            rows.append({**deepcopy(asset), 'origin': 'textbook_catalog_asset',
                'caption': asset.get('label', chosen['section_label']),
                'label': asset.get('label', chosen['section_label']), 'relation': '同节目录图表',
                'selection': {key: asset[key] for key in ('visual_asset_id', 'revision')}})
        rows.extend(SectionTextbookPageCatalog(facade.paths.workspace_root).rows(chosen['section_selection']))
    else:
        from .desktop_personal_textbook_crops import book_page_selection
        selected = chosen['section_selection']['selection']
        book = facade.personal_textbook_study().book_study(selected)['book']
        if book['source_sha256'] != source['source_sha256']:
            raise ValueError('个人教材原书已变化，请重新选择章节原页。')
        for page in chosen['source_binding']['pdf_pages']:
            rows.append({'origin': 'personal_textbook_page', 'pdf_page': page, 'printed_page': None,
                'caption': chosen['section_label'] + f' · PDF第{page}页',
                'label': f'打开本节完整原页并框选 · PDF {page}', 'relation': '本节完整原页',
                'source_name': book['source_name'], 'section_title': chosen['section_label'],
                'selection': book_page_selection(book, page)})
    if len({digest(row['selection']) for row in rows}) != len(rows):
        raise ValueError('当前节教材候选身份重复，请刷新后重新选择。')
    _checked(facade, context)
    result = {'schema_version': 'shchem.section-textbook-options.v1',
        'section_binding_sha256': chosen['binding_sha256'], 'rows': rows, 'notices': notices,
        'candidate_only': True, 'human_reviewed': False}
    result['binding'] = digest({'section_context': chosen, 'projection': result})
    return result


def _question_native(facade, context):
    chosen = _checked(facade, context)
    request = chosen['section_selection']
    if request['mode'] == 'supplement':
        curriculum, query, mode = None, chosen['section_label'], 'text_search'
    else:
        curriculum = facade.textbook_question_context(deepcopy(request))
        if (facade.validate_textbook_question_context(curriculum) is not True
                or curriculum['source_sha256'] != chosen['source_binding']['source_sha256']
                or curriculum['pdf_pages'] != chosen['source_binding']['pdf_pages']):
            raise ValueError('本地题浏览范围与当前原书或完整章节不同。')
        query, mode = '', curriculum['mode']
    _checked(facade, context)
    result = {'schema_version': 'shchem.section-question-context.v1', 'entry_kind': 'textbook_section',
        'original_title': chosen['section_label'], 'curriculum_context': deepcopy(curriculum),
        'search_query': query, 'mode': mode, 'section_binding_sha256': chosen['binding_sha256'],
        'section_context': chosen, 'candidate_only': True, 'human_reviewed': False}
    result['binding_sha256'] = digest(result)
    return result


def _read(facade, context, domain, native):
    stage = getattr(facade, '_section_source_stage', None)
    if stage is None:
        return native(facade, context)
    chosen = deepcopy(context)
    scope = {'section_context': chosen, 'read_port': id(facade),
             'workspace_root': str(facade.paths.workspace_root)}
    return stage.read_memo(domain, scope, lambda: native(facade, deepcopy(chosen)))


def section_textbook_options(facade, context):
    return _read(facade, context, 'section_textbook_options', _options_native)


def section_question_context(facade, context):
    return _read(facade, context, 'section_question_context', _question_native)


def recheck_section_question_context(facade, context, browser):
    if not isinstance(browser, dict) or section_question_context(facade, context) != browser:
        raise ValueError('教材原书、完整章节或本地题浏览范围已变化，请重新打开。')
    return True
