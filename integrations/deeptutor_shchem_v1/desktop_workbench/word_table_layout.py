"""Source-cell table display; no text inference, layout repair or external assets."""

from __future__ import annotations

from html import escape

from ..desktop_preparation_sources import _readable_table


def word_table_html(rows: object, expected_text: str) -> str | None:
    """Return a bounded source grid, or retain the existing ordered text view.

    A continuation containing text, ambiguous grid or nested table must not be
    silently collapsed. Column widths are reading aids, not original Word layout.
    """
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        return None
    width = None
    display_rows = []
    active_merges = {}
    cell_count = 0
    for row in rows:
        if not isinstance(row, dict):
            return None
        before, after, cells = (
            row.get("grid_before"), row.get("grid_after"), row.get("cells")
        )
        if (
            type(before) is not int or not 0 <= before <= 12
            or type(after) is not int or not 0 <= after <= 12
            or not isinstance(cells, list) or not cells
        ):
            return None
        output = []
        next_merges = {}
        column = before
        if before:
            output.append({"span": before, "rows": 1, "text": "（原行省略）"})
        for cell in cells:
            cell_count += 1
            if not isinstance(cell, dict) or cell_count > 2400:
                return None
            span, merge, text = (
                cell.get("column_span"), cell.get("vertical_merge"), cell.get("text")
            )
            if (
                type(span) is not int or not 1 <= span <= 12
                or merge not in ("none", "restart", "continue")
                or not isinstance(text, str)
                or "【表格开始：" in text
            ):
                return None
            key = (column, span)
            if merge == "continue":
                origin = active_merges.get(key)
                if origin is None or text.strip():
                    return None
                origin["rows"] += 1
                next_merges[key] = origin
            else:
                visible = {"span": span, "rows": 1, "text": text}
                output.append(visible)
                if merge == "restart":
                    next_merges[key] = visible
            column += span
        if after:
            output.append({"span": after, "rows": 1, "text": "（原行省略）"})
        column += after
        if not 1 <= column <= 12 or (width is not None and column != width):
            return None
        width = column
        active_merges = next_merges
        display_rows.append(output)
    # The source-text view is the authority. Geometry cannot substitute content.
    if width > 6 or _readable_table(rows) != expected_text:
        return None
    parts = [
        (
            '<table border="1" cellspacing="0" cellpadding="7" width="100%" '
            'style="border-color:#d9dfd3; border-collapse:collapse;">'
        )
    ]
    for row in display_rows:
        parts.append("<tr>")
        for cell in row:
            parts.append(
                f'<td colspan="{cell["span"]}" rowspan="{cell["rows"]}" '
                f'width="{100 * cell["span"] / width:.3f}%" valign="middle" '
                'style="white-space:pre-wrap; border:1px solid #cbd7c9;">'
                + escape(cell["text"]) + "</td>"
            )
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)
