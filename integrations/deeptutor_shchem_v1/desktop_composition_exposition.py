"""Editable teaching diagrams from the work copy's complete text blocks.

This is a layout choice, never a chemistry solver or a source rewrite. Arrows
express the teacher-authored reasoning order rather than molecular geometry.
"""
SCHEMA = 'shchem.classroom-exposition.v1'


def validate_exposition(item):
    spec = item['exposition']
    rows = item['blocks']
    if isinstance(spec,dict) and spec=={'schema':SCHEMA,'kind':'notes'}:
        visible=[r for r in rows if r['role'] in {'personal','knowledge'}]
        if (item['original']['kind'] not in {'personal_text','knowledge_note','handout_knowledge'} or not 1<=len(visible)<=12
                or any(r['kind']!='text' or 'presentation' in r for r in visible)):
            raise ValueError('知识讲解与笔记须使用完整文字内容，原图与题目仍按原排版呈现。')
        return
    if (item['original']['kind'] != 'personal_text' or not isinstance(spec, dict)
            or set(spec) != {'schema', 'kind'} or spec['schema'] != SCHEMA
            or spec['kind'] not in {'process', 'process_vertical', 'comparison'}
            or not 2 <= len(rows) <= (3 if spec['kind'] == 'comparison' else 6)
            or any(r['kind'] != 'text' or r['role'] != 'personal' or 'presentation' in r for r in rows)):
        raise ValueError('讲解图须使用2—6个完整个人文字块；比较图最多3栏。')


def notes_chunks(blocks, width, lines, *, body_px):
    """Complete note paragraphs, with a stable heading and readable body."""
    result=[]
    for block in blocks:
        for paragraph in block['value'].split('\n\n'):
            if not paragraph.strip():continue
            parts=paragraph.split('\n',1)
            if len(parts)==2:
                result+=lines(parts[0],width,block_id=block['id'],font_px=body_px,bold=True)
                result+=lines(parts[1],width,block_id=block['id'],font_px=body_px)
            else:result+=lines(paragraph,width,block_id=block['id'],font_px=body_px)
            result.append({'kind':'space','height':3,'width':width,'block_id':block['id']})
    return result


def diagram_chunks(item, width, lines):
    """Measure all text first; return one indivisible diagram, never shrink it."""
    validate_exposition(item)
    count = len(item['blocks']); vertical=item['exposition']['kind']=='process_vertical'
    process = item['exposition']['kind'] != 'comparison'
    gap = (9 if vertical else 13) if process else 6
    column = width if vertical else (width-gap*(count-1))/count
    if column < 42:
        raise ValueError('讲解图太窄，请加宽外框或减少节点。')
    rows=[]
    for block in item['blocks']:
        paragraphs=block['value'].split('\n')
        row=lines(paragraphs[0],column-10,block_id=block['id'],font_px=40,bold=True)
        if len(paragraphs)>1:row+=lines('\n'.join(paragraphs[1:]),column-10,block_id=block['id'],font_px=36)
        rows.append(row)
    padding=3 if vertical else 8
    height = max(sum(r['height'] for r in row) for row in rows)+2*padding
    parts = []
    offset=0
    for index, (block, chunks) in enumerate(zip(item['blocks'], rows)):
        x=0 if vertical else index*(column+gap)
        node_height=sum(r['height'] for r in chunks)+2*padding if vertical else height
        parts.append({'kind':'diagram_box','offset_x':x,'offset_y':offset,'width':column,'height':node_height,
                      'fill':'#edf5fb','stroke':'#246b9a','block_id':block['id']})
        y=offset+padding
        for chunk in chunks:
            parts.append({**chunk,'offset_x':x+5,'offset_y':y});y+=chunk['height']
        if process and index<count-1:
            parts.append({'kind':'diagram_arrow','offset_x':column/2-3 if vertical else x+column+2,
                'offset_y':offset+node_height+2 if vertical else height/2-3,
                'width':6 if vertical else gap-4,'height':gap-4 if vertical else 6,
                'vertical':vertical,'fill':'#246b9a','block_id':item['blocks'][index+1]['id']})
        if vertical:offset+=node_height+(gap if index<count-1 else 0)
    return [{'kind':'diagram','width':width,'height':(offset if vertical else height)+3,'block_id':None,'_parts':parts}]
