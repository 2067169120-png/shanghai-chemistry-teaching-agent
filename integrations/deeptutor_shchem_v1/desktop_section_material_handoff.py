"""Recheck explicit section/knowledge selection and publish one classroom batch."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from .desktop_composition_sources import (
    import_knowledge_note, import_knowledge_textbook_crops, import_questions,
    import_textbook_crops_for_composition,
    knowledge_selection, knowledge_textbook_options, merge_knowledge_result,
    merge_question_result, merge_textbook_crop_result,
)
from .desktop_knowledge_questions import _source_content, recheck_knowledge_question_context
from .desktop_lesson_composition import validate_resource
from .desktop_section_materials import recheck_section_material_context
from .reader_cancellation import check_read_cancelled


def _request_selection_scope(request, item):
    """Validate explicit scope before source reads; old requests are knowledge-led."""
    kinds = {'knowledge', 'textbook_images', 'questions', 'handout_materials'}
    knowledge = {'knowledge_selection', 'knowledge_card', 'knowledge_original'}
    if (not isinstance(request, dict) or request.get('kind') not in kinds
            or not isinstance(request.get('context'), dict)):
        raise ValueError('本节选材请求不完整，请重新打开。')
    scope = request.get('selection_scope', 'knowledge')
    if not isinstance(scope, str) or scope not in {'section', 'knowledge'}:
        raise ValueError('选材范围须明确为教材节或真实知识卡。')
    present = knowledge & request.keys()
    common = {'kind', 'context'} | ({'selection_scope'} if 'selection_scope' in request else set())
    if scope == 'knowledge':
        if present != knowledge or any(not isinstance(request[name], dict) for name in knowledge):
            raise ValueError('知识选材须保留完整的真实知识卡与原内容。')
        common |= knowledge
    else:
        context = request['context']
        if present:
            raise ValueError('教材节选材不能携带知识卡或伪造知识证据。')
        if (item is not None or context.get('entry_kind') != 'textbook_section'
                or context.get('status') != 'ready' or 'resource_id' not in context
                or context['resource_id'] is not None
                or not isinstance(context.get('book_key'), str) or not context['book_key']
                or not isinstance(context.get('section_filter_key'), str) or not context['section_filter_key']
                or not isinstance(context.get('section_selection'), dict)
                or context['section_selection'].get('mode') not in {'registered', 'supplement', 'personal'}):
            raise ValueError('请从原教材中明确选择一个已完整读取的真实教材节。')
        if request['kind'] == 'knowledge' or (request['kind'] == 'questions'
                and request.get('question_mode') == 'linked'):
            raise ValueError('知识讲解与已关联例题仍须选择真实知识卡。')
    extra = {'knowledge': set(), 'textbook_images': {'crop_binding', 'crop_requests'},
        'handout_materials': {'course_handout_source', 'material_request'}}
    if request['kind'] == 'questions':
        if request.get('question_mode') == 'browse':
            fields = {'question_mode', 'question_context', 'question_selections', 'reference'}
        elif request.get('question_mode') == 'linked':
            fields = {'question_mode', 'linked_question'}
        else:
            raise ValueError('请选择已关联例题或讲义中的完整题。')
    else:
        fields = extra[request['kind']]
    if set(request) != common | fields:
        raise ValueError('本节选材请求字段不完整或与选材范围不对应，请重新选择。')
    return scope


def _checked_knowledge(facade, request, context):
    chosen = request['knowledge_selection']
    if chosen not in [knowledge_selection(row) for row in context['knowledge_options']]:
        raise ValueError('所选知识不在当前教材章节中，请重新选择。')
    card = deepcopy(validate_resource(request['knowledge_card']))
    if card['original']['kind'] != 'knowledge_note':
        raise ValueError('请选择本节的教材知识。')
    if card['original'] != request['knowledge_original']:
        raise ValueError('知识原内容在选材期间变化，请重新选择。')
    stage = getattr(facade, '_section_source_stage', None)
    fresh = (stage.import_knowledge(chosen, card['original']) if stage is not None
             else import_knowledge_note(facade, chosen))
    if _source_content(fresh['original']) != _source_content(card['original']):
        raise ValueError('知识原文或教材版本已经变化，请重新选择。')
    if card['candidate_only'] is not True or any(card[key] is not False for key in (
            'human_reviewed', 'generation_allowed', 'publication_allowed', 'official')):
        raise ValueError('知识来源状态与当前候选不一致，请重新选择。')
    return card


def _classroom_target(card, existing):
    additions = merge_knowledge_result({'resources': [card]}, existing)['resources']
    if additions:
        return card, additions
    matches = [row for row in existing if row['original']['kind'] == 'knowledge_note'
               and _source_content(row['original']) == _source_content(card['original'])]
    if len(matches) != 1:
        raise ValueError('本课知识版本重复或无法核对，请重新选择。')
    # A new picture must point to the retained course card, including its edits.
    return deepcopy(matches[0]), []


class _SectionPublication:
    def __init__(self, check, build, additions, *, inner=None):
        self._check, self._build = check, build
        self._additions, self._inner = deepcopy(additions), inner
        self._published = self._accepted = False

    def publish(self, guard=None):
        if self._published:
            raise ValueError('本次选材已交接，请勿重复加入。')
        try:
            if guard: guard()
            self._check();check_read_cancelled()
            result = self._inner.publish(guard) if self._inner else self._build()
            self._check()
            if guard: guard()
            result = deepcopy(result)
            result['resources'] = deepcopy(self._additions) + result['resources']
            self._published = True
            return result
        except BaseException:
            self.discard()
            raise

    def accept(self):
        if not self._published:
            raise ValueError('本次选材尚未完成交接。')
        if self._inner: self._inner.accept()
        self._accepted = True

    def discard(self):
        if not self._accepted and self._inner: self._inner.discard()
        # Textbook services may retain completed crop receipts as candidate
        # caches. They are not classroom state and are not deleted by this flow.

    def stage_result(self, staged_facade):
        """Compile into a caller-owned temporary store without a final commit.

        Batch callers must supply a staging facade: no inner publication is
        accepted and no cancellation event is marked durably published here.
        """
        from .desktop_forward_section_materials import _SectionStagingFacade
        from .desktop_preparation_images import PreparationImageStore
        from .desktop_handout_material_handoff import _stamp
        if not isinstance(staged_facade, _SectionStagingFacade):
            raise ValueError('本批选材必须先在独立暂存区核对。')
        self._check(); check_read_cancelled()
        if self._inner:
            result = deepcopy(self._inner.result)
            if self._inner._recheck:
                current, _ = self._inner._recheck()
                if _stamp(current) != _stamp(result):
                    raise ValueError('选材来源在整批暂存期间变化，请重新选择。')
            if result['image_assets']:
                store = PreparationImageStore(staged_facade.paths.task_root / 'preparation-v1' / 'images')
                for asset in result['image_assets']:
                    check_read_cancelled()
                    raw = self._inner._payloads[asset['sha256']]
                    if store.import_bytes(raw, asset['caption'], asset['source'], asset['purpose']) != asset:
                        raise ValueError('选材原图与暂存副本不一致。')
        else:
            result = self._build()
        self._check(); check_read_cancelled()
        return {**deepcopy(result), 'resources': deepcopy(self._additions) + result['resources']}


def prepare_section_material_request(facade, item, course_resources, image_assets, request):
    """Keep the entry scope distinct from the selected knowledge and destination."""
    scope = _request_selection_scope(request, item)
    if request['kind'] == 'handout_materials':
        from .desktop_handout_material_handoff import validate_material_request
        if ('course_handout_source' not in request
                or not isinstance(request.get('material_request'), dict)
                or (request['course_handout_source'] is not None
                    and not isinstance(request['course_handout_source'], dict))):
            raise ValueError('讲义选材请求不完整，请重新选择。')
        validate_material_request(request['material_request'])
    if request['kind'] == 'textbook_images' and (
            not isinstance(request.get('crop_binding'), str)
            or len(request['crop_binding']) != 64
            or any(char not in '0123456789abcdef' for char in request['crop_binding'])
            or not isinstance(request.get('crop_requests'), list)
            or not request['crop_requests']
            or any(not isinstance(row, dict) or 'selection' not in row
                   for row in request['crop_requests'])):
        raise ValueError('教材图选材请求不完整，请重新框选。')
    if request['kind'] == 'questions':
        mode = request.get('question_mode')
        if mode == 'linked':
            linked = request.get('linked_question')
            required = {'link_id', 'snapshot', 'reference', 'selection', 'knowledge_endpoint'}
            if not isinstance(linked, dict) or set(linked) != required:
                raise ValueError('关联例题请求不完整，请重新选择。')
        elif mode == 'browse':
            selected, reference = request.get('question_selections'), request.get('reference')
            if (not isinstance(request.get('question_context'), dict)
                    or not isinstance(selected, list) or not 1 <= len(selected) <= 20
                    or any(not isinstance(row, dict) for row in selected)
                    or not isinstance(reference, dict)
                    or reference.get('selections') != selected):
                raise ValueError('讲义完整题请求不完整，请重新选择。')
        else:
            raise ValueError('请选择已关联例题或讲义中的完整题。')
    frozen = deepcopy(request)
    context = frozen['context'];existing = deepcopy(course_resources);assets = deepcopy(image_assets)
    def check():
        check_read_cancelled()
        stage = getattr(facade, '_section_source_stage', None)
        if stage is None:
            recheck_section_material_context(facade, item, existing, assets, context)
        else:
            stage.context(context, item=item, resources=existing, assets=assets)
        if scope == 'knowledge':
            _checked_knowledge(facade, frozen, context)
        if frozen['kind'] == 'handout_materials' and frozen['course_handout_source'] is not None:
            from .desktop_section_handout_sources import recheck_course_handout_source
            candidate = frozen['course_handout_source']
            recheck_course_handout_source(facade, existing, candidate)
            if frozen['material_request']['source'] != candidate['source']:
                raise ValueError('所选讲义与本课封存来源不是同一版本，请重新选择。')
    check()
    if scope == 'knowledge':
        card = _checked_knowledge(facade, frozen, context)
        target, additions = _classroom_target(card, existing)
    else:
        card = target = None; additions = []
    if frozen['kind'] == 'knowledge':
        return _SectionPublication(check, lambda: {'resources': [], 'image_assets': deepcopy(assets)}, additions)

    if frozen['kind'] == 'handout_materials':
        from .desktop_handout_material_handoff import prepare_handout_materials
        # The lecture source is an explicit teacher choice. This batch does not
        # create a semantic association with the selected textbook knowledge.
        inner = prepare_handout_materials(facade, frozen['material_request'], assets, existing + additions)
        return _SectionPublication(check, None, additions, inner=inner)

    if frozen['kind'] == 'textbook_images':
        choices = deepcopy(frozen['crop_requests'])
        if scope == 'section':
            from .desktop_section_material_sources import section_textbook_options
            preview = section_textbook_options(facade, context)
            if preview['section_binding_sha256'] != context['binding_sha256']:
                raise ValueError('教材图候选与当前原书章节不是同一范围。')
        else:
            preview = knowledge_textbook_options(facade, card)
        if preview['binding'] != frozen['crop_binding']:
            raise ValueError('教材图候选已经变化，请重新框选。')
        allowed = [row['selection'] for row in preview['rows']]
        if not choices or any(row['selection'] not in allowed for row in choices):
            label = '教材节' if scope == 'section' else '知识'
            raise ValueError(f'所选教材图不属于当前{label}候选，请重新选择。')
        def build():
            check()
            current = (section_textbook_options(facade, context) if scope == 'section'
                       else knowledge_textbook_options(facade, card))
            if (current['binding'] != frozen['crop_binding']
                    or any(row['selection'] not in [entry['selection'] for entry in current['rows']] for row in choices)):
                raise ValueError('教材原页或图候选已经变化，请重新选择。')
            if scope == 'section':
                def check_crops():
                    check()
                    latest = section_textbook_options(facade, context)
                    if (latest['binding'] != frozen['crop_binding']
                            or latest['section_binding_sha256'] != context['binding_sha256']
                            or any(row['selection'] not in [entry['selection'] for entry in latest['rows']] for row in choices)):
                        raise ValueError('教材节或图候选已经变化，请重新选择。')
                result = import_textbook_crops_for_composition(facade, choices, assets, existing, guard=check_crops)
            else:
                binding = knowledge_textbook_options(facade, target)['binding']
                result = import_knowledge_textbook_crops(facade, target, binding, choices, assets)
            check()
            return merge_textbook_crop_result(result, existing + additions, assets)
        return _SectionPublication(check, build, additions)

    from .desktop_handout_material_handoff import (
        MaterialPublication, _ReadOnlyReferenceWords, _StagingFacade,
        prepare_linked_question,
    )
    if frozen.get('question_mode') == 'linked':
        from .desktop_knowledge_question_links import KnowledgeQuestionLinks
        linked = frozen['linked_question'];service = KnowledgeQuestionLinks(facade)
        if service.knowledge_context(card)['endpoint'] != linked['knowledge_endpoint']:
            raise ValueError('所选例题与当前知识不是同一关联版本。')
        relation = service.evaluate(linked['link_id']);resolved = service.resolve_selection(relation)
        inner = prepare_linked_question(facade, {**linked, 'scoped_facade': resolved['scoped_facade']}, assets, existing)
        return _SectionPublication(check, None, additions, inner=inner)
    if frozen.get('question_mode') != 'browse':
        raise ValueError('请选择已关联例题或讲义中的完整题。')

    selections = deepcopy(frozen['question_selections']);reference = deepcopy(frozen['reference'])
    if reference.get('include_images') is not True:
        raise ValueError('请保留完整题及原图，再确认加入本课。')
    def check_questions():
        check()
        if scope == 'section':
            from .desktop_section_material_sources import recheck_section_question_context
            recheck_section_question_context(facade, context, frozen['question_context'])
        else:
            recheck_knowledge_question_context(facade, card, frozen['question_context'])
        latest = _ReadOnlyReferenceWords(facade._word_questions()).reference(selections)
        if latest != reference:
            raise ValueError('讲义题原文、原图或范围已经变化，请重新选择。')
        check_read_cancelled()
    check_questions()
    from .desktop_preparation_images import PreparationImageStore
    with TemporaryDirectory(prefix='shchem-section-question-') as temporary:
        staged = _StagingFacade(facade, temporary)
        store = PreparationImageStore(Path(temporary) / 'preparation-v1' / 'images')
        for asset in assets:
            store.import_bytes(facade.preparation_image_bytes(asset), asset['caption'], asset['source'], asset['purpose'])
        result = merge_question_result(import_questions(staged, selections, assets, expected_reference=reference), existing, assets)
        payloads = {asset['sha256']: store.load(asset) for asset in result['image_assets']}
    sealed = deepcopy(result)
    def recheck():
        check_questions();return deepcopy(sealed), {}
    inner = MaterialPublication(facade, frozen, assets, existing, result, payloads, recheck=recheck)
    return _SectionPublication(check_questions, None, additions, inner=inner)


def prepare_section_material_requests(facade, item, course_resources, image_assets, requests):
    """Prepare an ordered mixed selection for one guarded classroom transaction."""
    from .desktop_forward_section_materials import prepare_section_material_batch
    return prepare_section_material_batch(facade, item, course_resources, image_assets, requests)
