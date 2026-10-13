"""One occurrence-preserving native table projection for every output path."""
from .desktop_handout_working_tables import _sealed_table_context, _table_projection


def source_table_projection(item, row):
    """Project verified cell edits, or unchanged legacy work text, without I/O.

    A present sidecar must validate even when a requested block is ineligible;
    corrupt sidecars cannot masquerade as the ordinary free-text fallback. Old
    resources with edited free text still retain all independent image blocks.
    """
    from .desktop_lesson_composition import validate_resource
    validated = 'working_tables' in item
    if validated:
        validate_resource(item)
    original = item.get('original', {})
    if original.get('kind') != 'handout_knowledge' or row.get('kind') != 'text' or 'presentation' in row:
        return None
    working = [b for b in item.get('blocks', []) if b.get('id') == row.get('id')]
    native = [b for b in original.get('blocks', []) if b.get('id') == row.get('id')]
    if len(working) != 1 or working[0] != row or len(native) != 1 or native[0].get('kind') != 'text':
        return None
    table = item.get('working_tables', {}).get('tables', {}).get(row['id'])
    if table is None and row.get('value') != native[0].get('value'):
        return None
    matches = [b for b in original.get('evidence', {}).get('section_snapshot', {}).get('blocks', [])
        if b.get('index') == row.get('source_index')]
    if len(matches) != 1 or 'table_rows' not in matches[0]:
        return None
    if matches[0].get('text') != native[0].get('value'):
        raise ValueError('讲义表格封存原文与Original区块不一致。')
    if not validated:
        validate_resource(item)
    context = _sealed_table_context(item, row['id'])
    if context is None:
        return None
    return _table_projection(context, table['cells'] if table is not None else None)
