"""Selected-page model requests use fake credentials and no network."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import base64
import json
from types import SimpleNamespace

import pytest

from test_personal_textbook_knowledge import native_fixture as native_fixture, decoded_items
from test_personal_textbook_study import chapter
from test_personal_textbook_ui import facade_for
from test_preparation_visual_provider import _context, _content
from test_desktop_preparation_provider import _Transport
from integrations.deeptutor_shchem_v1.desktop_textbook_knowledge_provider import generate_knowledge
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import _render_page
from integrations.deeptutor_shchem_v1.desktop_personal_textbook_knowledge import PersonalTextbookKnowledgeService
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, TaskCancellationEvent, read_cancel_scope


def prepared_for(f):
    view = chapter(f); service = PersonalTextbookKnowledgeService(f.workspace)
    return service.prepare(f.selection, view['study_revision'], view['sections'][0]['section_id'], 2, 3), view


@pytest.mark.parametrize('style', ['responses', 'chat_completions'])
def test_visual_request_contains_only_exact_selected_original_pages(native_fixture, style):
    f = native_fixture; prepared, _view = prepared_for(f)
    frozen = deepcopy(prepared); before = f.state.path.read_bytes()
    transport = _Transport(decoded_items()); rendered = []
    def render(data, page):
        assert data == f.original
        result = _render_page(data, page); rendered.append((page, result['page_data'])); return result
    result = generate_knowledge(_context(style), prepared, renderer=render, transport=transport)
    assert result == decoded_items() and prepared == frozen
    assert [page for page, _data in rendered] == [2, 3] and len(transport.requests) == 1
    body, prompt, urls = _content(transport.requests[0])
    assert len(urls) == 2
    for url, (_page, pixels) in zip(urls, rendered, strict=True):
        assert base64.b64decode(url.split(',', 1)[1], validate=True) == pixels
    assert '"pdf_pages": [2, 3]' in prompt and 'source_quote也不代表已经逐字核验' in prompt
    assert 'fixture-not-a-real-credential' not in transport.requests[0].body.decode()
    if style == 'responses':
        assert body['text']['format']['schema']['properties']['items']['maxItems'] == 20
        assert body['store'] is False and body['text']['format']['strict'] is True
    else:
        assert body['response_format']['json_schema']['strict'] is True
    assert f.state.path.read_bytes() == before and f.source.read_bytes() == f.original


@pytest.mark.parametrize('failure', ['http', 'not_invoked', 'incomplete', 'raw_error'])
def test_failed_model_is_safe_has_one_call_and_no_persisted_candidate(native_fixture, failure):
    f = native_fixture; prepared, _view = prepared_for(f); before = f.state.path.read_bytes()
    class Transport(_Transport):
        def send(self, request, **kwargs):
            response = super().send(request, **kwargs)
            if failure == 'http': return replace(response, http_status=503, body=b'raw server credential must not leak')
            if failure == 'not_invoked': return replace(response, model_invoked=False)
            if failure == 'incomplete': return replace(response, body=b'{"status":"incomplete","output_text":"{}"}')
            raise RuntimeError('fixture-not-a-real-credential raw server credential must not leak')
    transport = Transport(decoded_items())
    with pytest.raises(PreparationSourceError, match='本批未保存') as exc:
        generate_knowledge(_context(), prepared, transport=transport)
    assert 'credential' not in str(exc.value) and len(transport.requests) == 1
    assert f.state.path.read_bytes() == before


def test_cancel_before_render_and_after_response_prevents_candidate(native_fixture):
    f = native_fixture; prepared, _view = prepared_for(f); before = f.state.path.read_bytes()
    event = TaskCancellationEvent(); event.set(); transport = _Transport(decoded_items())
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        generate_knowledge(_context(), prepared, transport=transport)
    assert transport.requests == []
    event = TaskCancellationEvent()
    class CancelTransport(_Transport):
        def send(self, request, **kwargs):
            response = super().send(request, **kwargs); event.set(); return response
    transport = CancelTransport(decoded_items())
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        generate_knowledge(_context(), prepared, transport=transport)
    assert len(transport.requests) == 1 and f.state.path.read_bytes() == before


def configured_facade(f, *, vision=True, images_allowed=True):
    facade = facade_for(f); transport = _Transport(decoded_items())
    context = _context()
    class Providers:
        def list_metadata(self):
            return [{'profile_id': context.profile_id, 'allowed_data_classes': ['source_page_image'] if images_allowed else []}]
        @contextmanager
        def borrow_invocation_context(self, profile_id, expected_revision):
            if expected_revision != context.revision: raise PreparationSourceError('模型配置已变化')
            assert profile_id == context.profile_id
            yield context
    facade._providers = Providers(); facade._preparation_transport = transport
    facade._preparation_profile = lambda *_args: SimpleNamespace(capabilities=('vision',) if vision else ('text',))
    return facade, transport, {'profile_id': context.profile_id, 'profile_revision': context.revision}


@pytest.mark.parametrize('gate', ['unchecked', 'no_vision', 'images_blocked', 'stale'])
def test_facade_model_gates_prevent_all_network_and_state_writes(native_fixture, gate):
    f = native_fixture; _prepared, view = prepared_for(f)
    facade, transport, binding = configured_facade(f, vision=gate != 'no_vision', images_allowed=gate != 'images_blocked')
    if gate == 'stale': binding['profile_revision'] = 'STALE'
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError):
        facade.personal_textbook_knowledge_generate(f.selection, view['study_revision'], view['sections'][0]['section_id'],
            2, 3, **binding, send_confirmed=gate != 'unchecked')
    assert transport.requests == [] and f.state.path.read_bytes() == before


def test_facade_returns_candidate_without_automatically_saving_or_adopting(native_fixture):
    f = native_fixture; _prepared, view = prepared_for(f); facade, transport, binding = configured_facade(f)
    before = f.state.path.read_bytes()
    draft = facade.personal_textbook_knowledge_generate(f.selection, view['study_revision'], view['sections'][0]['section_id'],
        2, 3, **binding, send_confirmed=True)
    assert draft['method'] == 'model_visual' and draft['human_reviewed'] is False and len(transport.requests) == 1
    assert f.state.path.read_bytes() == before and f.study.book_study(f.selection)['notes'] == []
