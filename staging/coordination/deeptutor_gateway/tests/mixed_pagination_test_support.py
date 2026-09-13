"""Synthetic service-test pagination; never evidence of real Word rendering."""
from io import BytesIO


def synthetic_docx(text):
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


def synthetic_pagination(paths, output):
    from test_mixed_paper_pagination import SyntheticRenderer

    from integrations.deeptutor_shchem_v1.desktop_mixed_paper_pagination import (
        prepare_pages,
    )

    return prepare_pages(paths, output, renderer=SyntheticRenderer({"student": 1, "teacher": 1}))


def paginate_and_read(service, preview):
    service._pagination_builder = synthetic_pagination
    preview = service.prepare_pagination(preview.preview_id, preview.preview_hash)
    for document in preview.preview_model["pagination"]["documents"].values():
        for page in document["pages"]:
            service.image(preview.preview_id, page["image_id"])
    return preview
