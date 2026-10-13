"""Read-only, source-bound inline Word question display for the current tab."""
from copy import deepcopy
from hashlib import sha256

from .desktop_composition_inline import NativeParagraphDisplay, display_tokens
from .desktop_word_question_attributes import suggest_attributes, validate_attributes
from .desktop_word_questions import WordQuestionError
from .offline_word_image_label_review import _native_scope


def question_reading_layout(words, key, revision, *, scope='question'):
    if scope not in {'question', 'answer'}:
        raise WordQuestionError('请选择题面或答案。')
    with words._lock:
        rows, inventory = words._resolve([{'key': key, 'revision': revision}], read_only=True)
        row = rows[0]
        source, _batch, preview, _index = inventory[row['source_id']]
        stored = words.attribute_store.get(key, source_sha256=row['source_sha256'], question_revision=revision)
        attributes = validate_attributes(stored if stored is not None else
            suggest_attributes(row, {'source_name': row['source_name']}))
        # Use the same complete native closure as lesson import. Object order,
        # OLE same-object previews and original bytes get no relaxed admission.
        native, payloads = _native_scope(words, row, attributes, preview, source)
        reader = NativeParagraphDisplay(source.content)
        groups = ('answer_blocks',) if scope == 'answer' else ('context_blocks', 'question_blocks')
        paragraphs, images = [], {}
        for group in groups:
            for original in native['blocks'][group]:
                index = original['index']
                text = {'value': original['text'], 'source_index': index}
                objects = [o for o in native['objects'] if o['block_index'] == index]
                blocks = []
                for occurrence, obj in enumerate(objects):
                    asset_id = obj['asset_id']
                    identity = 'CB-' + sha256(f'{key}:{index}:{occurrence}'.encode()).hexdigest()[:32]
                    blocks.append({'id': identity, 'value': asset_id})
                    metadata = next(a for a in native['images'] if a['asset_id'] == asset_id)
                    images[asset_id] = {'bytes': payloads[asset_id], **deepcopy(metadata)}
                reader.bind(text, blocks, objects)
                paragraphs.append({'source_index': index, 'role': 'answer' if scope == 'answer' else
                    ('shared_material' if group == 'context_blocks' else 'question'),
                    'text': original['text'], 'warnings': deepcopy(original['warnings']),
                    'tokens': display_tokens(text, {b['id']: b for b in blocks})})
        # Only this tab's images/text are returned. Answer pixels are never
        # included in a question result even if another role reuses the bytes.
        return {'schema': 'shchem.word-question-reading.v1', 'key': key, 'revision': revision,
            'scope': scope, 'source_sha256': row['source_sha256'], 'source_revision': row['source_revision'],
            'paragraphs': paragraphs, 'images': images, 'candidate_only': True, 'human_reviewed': False}
