"""Source-bound Word table rows for a local reader, separate from its cache.

This adds a display projection only. It never rewrites native text, interprets
Chinese table markers, updates source files, or claims Word layout fidelity.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

from docx import Document

from .desktop_preparation_sources import (
    PreparationSourceError,
    _body_blocks,
    _digest,
    _local,
    _readable_table,
    _table_rows,
)
from .word_handout_import import _validate_container
from .word_native_text import NATIVE_WORD_TEXT_REVISION, WordNativeTextReader


class WordTablePreviewService:
    """Read one selected original and bind its table grid to the current text."""

    def __init__(self, facade):
        self.facade = facade

    def preview(self, batch_id, source_id, source_sha256, expected_revision):
        if (
            not isinstance(batch_id, str)
            or not batch_id.strip()
            or not isinstance(source_id, str)
            or not source_id.strip()
            or not isinstance(source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
            or not isinstance(expected_revision, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_revision) is None
        ):
            raise PreparationSourceError("原教案表格选择信息不完整，请重新打开原文。")

        # The facade checks the archived source descriptor and source bytes.
        # Keep this returned immutable byte string even if a later read changes.
        source = self.facade._imported_word_source(batch_id, source_id)
        data = source.content
        if (
            not isinstance(data, bytes)
            or not 0 < len(data) <= 40 * 1024 * 1024
            or hashlib.sha256(data).hexdigest() != source_sha256
        ):
            raise PreparationSourceError("原教案已变化，请重新打开后查看表格。")

        value = self.facade.imported_word_preview(batch_id, source_id)
        if (
            not isinstance(value, dict)
            or value.get("source_sha256") != source_sha256
            or value.get("source_name") != source.filename
            or value.get("extraction_revision") != NATIVE_WORD_TEXT_REVISION
            or value.get("revision") != expected_revision
            or _digest({key: item for key, item in value.items() if key != "revision"})
            != expected_revision
        ):
            raise PreparationSourceError("原教案预览已变化，请刷新后再查看表格。")

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                _validate_container(package)
            document = Document(io.BytesIO(data))
            elements = list(_body_blocks(document._element.body))
            blocks = value.get("blocks")
            if (
                not isinstance(blocks, list)
                or len(blocks) != len(elements)
                or any(
                    not isinstance(block, dict)
                    or type(block.get("index")) is not int
                    or block["index"] != index
                    or not isinstance(block.get("text"), str)
                    for index, block in enumerate(blocks, 1)
                )
            ):
                raise PreparationSourceError("原教案区块位置已变化，请重新打开原文。")

            text_reader = WordNativeTextReader(document.styles.element)
            tables = {}
            for index, element in enumerate(elements, 1):
                if _local(element) != "tbl":
                    continue
                rows = _table_rows(element, document, set(), text_reader)
                if _readable_table(rows) != blocks[index - 1]["text"]:
                    raise PreparationSourceError(
                        "原表格内容与当前原文预览不一致，请刷新或用 Word 核对。"
                    )
                tables[index] = rows
            return {
                "source_sha256": source_sha256,
                "source_revision": expected_revision,
                "tables": tables,
            }
        except PreparationSourceError:
            raise
        except Exception as exc:
            raise PreparationSourceError(
                "原表格暂时无法读取；原文未改变，请用 Word 核对原表。"
            ) from exc


__all__ = ["WordTablePreviewService"]
