"""Complete questions from the exact archived lecture behind a course resource."""
from copy import deepcopy
from hashlib import sha256

from .desktop_handout_sources import handout_source_context, validate_handout_source
from .desktop_word_questions import WordQuestionError
from .reader_cancellation import check_read_cancelled, read_cancel_scope


class HandoutQuestionFacade:
    """Pin every question operation under the existing source service's RLock.

    Question keys identify bytes, so a catalogue for another archived copy can
    replace the shared lookup locations. This adapter restores the selected
    lecture's archive location for each operation without replacing the facade.
    """
    def __init__(self, facade, binding):
        self._facade = facade
        self.binding = validate_handout_source(binding)
        self._words = facade._word_questions()
        self._scoped_words = _ScopedWords(self)

    def _catalog_locked(self):
        check_read_cancelled()
        binding = self.binding
        handout_source_context(self._facade, binding)
        catalog, inventory = self._words._catalog(
            {binding['batch_id']: {binding['source_id']}}, read_only=True)
        found = inventory.get(binding['source_sha256'])
        if found is None or len(inventory) != 1:
            raise WordQuestionError('这份讲义的原文件已变化，请返回重新选择。')
        source, batch_id, preview, _ = found
        if (batch_id != binding['batch_id']
                or source.effective_source_file_id != binding['source_id']
                or source.source_sha256 != binding['source_sha256']
                or sha256(source.content).hexdigest() != binding['source_sha256']
                or source.filename != binding['source_name']
                or ('source_role' in binding and source.role != binding['source_role'])
                or preview['source_sha256'] != binding['source_sha256']
                or preview['source_name'] != binding['source_name']
                or preview['revision'] != binding['source_revision']):
            raise WordQuestionError('来源与题目不属于同一份讲义版本，请重新选择。')
        if any(s.get('source_id') != binding['source_sha256']
               or s.get('batch_id') != binding['batch_id'] for s in catalog['sources']):
            raise WordQuestionError('讲义题目来源位置已变化，请重新选择。')
        for row in catalog['items']:
            if (row.get('batch_id') != binding['batch_id']
                    or row.get('archive_source_id') != binding['source_id']
                    or row.get('source_id') != binding['source_sha256']
                    or row.get('source_sha256') != binding['source_sha256']
                    or row.get('source_revision') != binding['source_revision']):
                raise WordQuestionError('讲义题目来源版本已变化，请重新选择。')
        check_read_cancelled()
        return catalog, inventory

    def _validate_choices(self, catalog, selections):
        if not isinstance(selections, list):
            raise WordQuestionError('请选择本份讲义中的完整题。')
        rows = {row['key']: row for row in catalog['items']}
        for chosen in selections:
            if (not isinstance(chosen, dict) or chosen.get('key') not in rows
                    or rows[chosen['key']]['revision'] != chosen.get('revision')):
                raise WordQuestionError('题目不在这份讲义的当前版本中，请重新选择。')

    def _call(self, operation, selections=None):
        with self._words._lock:
            catalog, _ = self._catalog_locked()
            if selections is not None:
                self._validate_choices(catalog, selections)
            result = operation()
            # Do not return pixels or a reference read during a source change.
            self._catalog_locked()
            return result

    def word_question_catalog(self):
        with self._words._lock:
            catalog, _ = self._catalog_locked()
            catalog = deepcopy(catalog)
            try:
                catalog['attribute_catalog'] = self._words._read_attribute_catalog()
            except (OSError, ValueError, TypeError, KeyError):
                catalog['attribute_catalog'] = None
            self._catalog_locked()
            return catalog

    def word_question_catalog_progress(self, progress, cancelled):
        with read_cancel_scope(cancelled):
            if progress:
                progress({'message': '正在读取当前讲义的完整题…'})
            return self.word_question_catalog()

    def _word_questions(self):
        return self._scoped_words

    def __getattr__(self, name):
        # The scoped picker has its own choices and no cross-source basket.
        if name in {'word_question_saved_selection', 'word_question_save_selection',
                    'add_word_questions_to_basket'} or name.startswith('word_semantic_'):
            raise AttributeError(name)
        value = getattr(self._facade, name)
        if not callable(value) or not name.startswith('word_question_'):
            return value
        def scoped(*args, **kwargs):
            if name == 'word_question_reference':
                selections = args[0] if args else kwargs.get('selections')
            elif name == 'word_question_export':
                selections = args[1] if len(args) > 1 else kwargs.get('selections')
            else:
                selections = [{'key': args[0] if args else kwargs.get('key'),
                    'revision': args[1] if len(args) > 1 else kwargs.get('revision')}]
            return self._call(lambda: value(*args, **kwargs), selections)
        return scoped


class _ScopedWords:
    """The same lock and native validators used by complete-question import."""
    def __init__(self, owner):
        self.owner = owner
        self._lock = owner._words._lock

    def _resolve(self, selections, **kwargs):
        return self.owner._call(
            lambda: self.owner._words._resolve(selections, **kwargs), selections)

    def reference(self, selections, **kwargs):
        return self.owner._call(
            lambda: self.owner._words.reference(selections, **kwargs), selections)

    def __getattr__(self, name):
        return getattr(self.owner._words, name)


def handout_question_facade(facade, binding):
    """Validate the bound lecture evidence and exact Word archive before UI."""
    scoped = HandoutQuestionFacade(facade, binding)
    scoped.word_question_catalog()
    return scoped
