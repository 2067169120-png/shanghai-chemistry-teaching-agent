"""Keep navigation on the preparation page, outside saved lesson content."""
from .composition_page_navigator import visible_fragments


def _tab_text(tabs):
    return tabs.tabText(tabs.currentIndex())


def _select_tab(tabs, text):
    index = next((i for i in range(tabs.count()) if tabs.tabText(i) == text), None)
    if index is not None:
        tabs.setCurrentIndex(index)


def remember_editor(dialog):
    panel = dialog.composition_panel
    dialog.page._lesson_editor_session = {
        'design_id': dialog.history.value['id'],
        'tab': _tab_text(dialog.tabs),
        'node_id': dialog.current,
        'properties_tab': _tab_text(dialog.properties),
        'source_tab': _tab_text(panel.source_tabs),
        'student': panel.student.isChecked(),
        'resource_id': panel.selected,
        'frame_index': panel.frame_index,
        'page_id': panel._view_page_id,
        'focused': panel.page_navigation.focus.isChecked(),
        'width': dialog.width(),
        'height': dialog.height(),
    }


def restore_editor(dialog):
    state = getattr(dialog.page, '_lesson_editor_session', None)
    if not isinstance(state, dict) or state.get('design_id') != dialog.history.value['id']:
        return
    width, height = state.get('width'), state.get('height')
    if type(width) is int and type(height) is int and width >= 320 and height >= 400:
        dialog.resize(width, height)
    node_id = state.get('node_id')
    if any(node['id'] == node_id for node in dialog.history.value['nodes']):
        dialog.refresh_lists(node_id)
    _select_tab(dialog.properties, state.get('properties_tab'))
    panel = dialog.composition_panel
    if type(state.get('student')) is bool:
        panel.student.setChecked(state['student'])
    result = panel.layout_result
    if result is not None:
        fragments = visible_fragments(result)
        identity, frame = state.get('resource_id'), state.get('frame_index')
        chosen = next((f for f in fragments if f['resource_id'] == identity
                       and type(frame) is int and f['frame_index'] == frame), None)
        if chosen is None:
            chosen = next((f for f in fragments if f['resource_id'] == identity), None)
        if chosen is not None:
            panel.navigate_to_page(chosen['frame']['page_id'], identity, chosen['frame_index'])
        elif state.get('page_id') in result['pages']:
            panel.navigate_to_page(state['page_id'])
        elif result['pages']:
            panel.navigate_to_page(next(iter(result['pages'])))
    _select_tab(panel.source_tabs, state.get('source_tab'))
    if type(state.get('focused')) is bool:
        panel.page_navigation.focus.setChecked(state['focused'])
    _select_tab(dialog.tabs, state.get('tab'))
