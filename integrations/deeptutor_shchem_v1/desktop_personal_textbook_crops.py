"""Resolve a selected page of an archived personal book for lesson cropping."""
from __future__ import annotations

import re

from .desktop_textbook_preparation_crops import TextbookPreparationCropError

PAGE_ID = re.compile(r"BOOKPAGE-(BOOK-[0-9a-f]{64})-([1-9][0-9]{0,3})")


def book_page_selection(book, pdf_page):
    """Make a page identity from an existing book; never accept a file path."""
    if (not isinstance(book, dict)
            or not re.fullmatch(r"BOOK-[0-9a-f]{64}", str(book.get("source_id", "")))
            or book.get("revision") != book["source_id"][5:]
            or type(book.get("page_count")) is not int
            or type(pdf_page) is not int or not 1 <= pdf_page <= book["page_count"]):
        raise TextbookPreparationCropError("个人教材或PDF页位置已变化，请刷新后重选原页。")
    return {"visual_asset_id": f"BOOKPAGE-{book['source_id']}-{pdf_page}",
            "revision": book["revision"]}


class PersonalTextbookPageCatalog:
    """Adapt verified personal-book bytes to the existing crop/export pipeline."""

    def __init__(self, workspace):
        self.workspace = workspace

    def source(self, visual_asset_id, revision):
        match = PAGE_ID.fullmatch(visual_asset_id) if isinstance(visual_asset_id, str) else None
        if match is None or not isinstance(revision, str) or revision != match[1][5:]:
            raise TextbookPreparationCropError("个人教材原页身份无法核对，请从已导入整书重新选择。")
        source_id, page = match[1], int(match[2])
        book = self.workspace.read_book(source_id, revision)
        if (book["source_id"] != source_id or book["source_sha256"] != revision
                or not 1 <= page <= book["page_count"]):
            raise TextbookPreparationCropError("个人教材归档或PDF页位置无法核对，未使用旧裁片。")
        return {"visual_asset_id": visual_asset_id, "revision": revision, "volume_id": None,
                "source_id": source_id, "source_kind": "personal_textbook_page",
                "source_title": book["source_name"], "source_sha256": book["source_sha256"],
                "pdf_page": page, "printed_page": None, "pdf_bytes": book["pdf_bytes"],
                "label": f"{book['source_name']} · PDF第{page}页"}
