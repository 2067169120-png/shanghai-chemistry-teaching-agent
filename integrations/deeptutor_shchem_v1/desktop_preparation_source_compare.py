"""Pure offline text comparison for preparation source material and slide text.

This module deliberately provides retrieval clues only.  It does not read
files, parse HTML, consult a model, or decide that a source was adopted or
that a lesson is ready for teaching.
"""

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

__all__ = ["compare_source_text"]


_BRACED_SCRIPT = re.compile(r"[_^]\s*\{\s*([^{}]*?)\s*\}")
_WHITESPACE = re.compile(r"\s+")
_SOURCE_SEPARATOR = "\n\n【另一处资料上下文】\n\n"
_MINUS_VARIANTS = str.maketrans({"−": "-", "﹣": "-", "－": "-"})


def _search_normalize(value: str) -> str:
    """Normalize only for matching; callers retain the original strings."""

    normalized = unicodedata.normalize("NFKC", value)
    while True:
        unbraced = _BRACED_SCRIPT.sub(r"\1", normalized)
        if unbraced == normalized:
            break
        normalized = unbraced
    # Removing whitespace lets native Word extraction such as ``NH_{4}Cl``
    # match copied Unicode text such as ``NH₄Cl`` without rewriting output.
    # Chemical symbols are case-sensitive: CO and Co must not be conflated.
    return _WHITESPACE.sub("", normalized).translate(_MINUS_VARIANTS)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def _source_excerpt(materials: str, query: str, has_source: bool) -> tuple[str, bool]:
    if not has_source:
        return "", False

    needle = _search_normalize(query)
    if not needle:
        return materials, True

    lines = materials.splitlines()
    matching_lines = [
        index
        for index, line in enumerate(lines)
        if needle in _search_normalize(line)
    ]
    if not matching_lines:
        return "", False

    selected = {
        line_number
        for match in matching_lines
        for line_number in range(
            max(0, match - 1), min(len(lines), match + 2)
        )
    }
    ordered = sorted(selected)
    blocks: list[list[str]] = []
    previous_line = None
    for line_number in ordered:
        if previous_line is None or line_number != previous_line + 1:
            blocks.append([])
        blocks[-1].append(lines[line_number])
        previous_line = line_number
    return _SOURCE_SEPARATOR.join("\n".join(block) for block in blocks), True


def _student_page_text(page: Mapping[str, Any]) -> str:
    page_number = page.get("page", "")
    title = _text(page.get("title"))
    lines = [f"第 {page_number} 页：{title}"]
    lines.extend(_text_list(page.get("visible_text")))
    return "\n".join(lines)


def compare_source_text(report: Mapping[str, Any], query: str) -> dict[str, Any]:
    """Compare a classroom-review report with its optional source text.

    Matching uses only Unicode NFKC, whitespace removal, and conversion of
    braced sub/superscripts (for example ``_{4}`` and ``^{+}``).  All returned
    source and student text remains exactly as supplied by ``report``.
    """

    source_reference = report.get("source_reference")
    materials = ""
    if isinstance(source_reference, Mapping):
        materials = _text(source_reference.get("materials"))
    has_source = bool(materials.strip())
    source_text, source_found = _source_excerpt(materials, query, has_source)

    pages = report.get("pages", [])
    if not isinstance(pages, (list, tuple)):
        pages = []

    needle = _search_normalize(query)
    selected_pages: list[Mapping[str, Any]] = []
    visible_pages: list[int] = []
    notes_only_pages: list[int] = []

    for page in pages:
        if not isinstance(page, Mapping):
            continue
        page_number = page.get("page")
        title = _text(page.get("title"))
        visible_text = _text_list(page.get("visible_text"))
        notes = _text(page.get("teacher_notes"))

        if not needle:
            selected = True
        else:
            selected = needle in _search_normalize(title) or any(
                needle in _search_normalize(line) for line in visible_text
            )
            notes_match = needle in _search_normalize(notes)
            if notes_match and not selected and isinstance(page_number, int):
                notes_only_pages.append(page_number)

        if selected:
            selected_pages.append(page)
            if isinstance(page_number, int):
                visible_pages.append(page_number)

    return {
        "source_text": source_text,
        "student_text": "\n\n".join(
            _student_page_text(page) for page in selected_pages
        ),
        "source_found": source_found,
        "visible_pages": visible_pages,
        "notes_only_pages": notes_only_pages if needle else [],
        "query": query,
        "has_source": has_source,
    }
