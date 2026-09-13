"""Synthetic temporary artifacts only; no Office, credentials, or model API."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from threading import Event

from docx import Document
from PIL import Image
from pypdf import PdfWriter
import pytest

from integrations.deeptutor_shchem_v1 import (
    desktop_mixed_paper_pagination as pagination,
)
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled,
    read_cancel_scope,
)


def docx(path, text):
    document = Document()
    document.add_paragraph(text)
    document.save(path)
    return path


@pytest.fixture
def inputs(tmp_path):
    return {audience: docx(tmp_path / (audience + ".docx"), "合成" + audience)
            for audience in ("student", "teacher")}


class SyntheticRenderer:
    def __init__(self, counts=None):
        self.calls = []
        self.counts = counts or {"student": 2, "teacher": 3}

    def __call__(self, source, output, *, toolchain):
        self.calls.append((source, output, toolchain))
        audience = source.stem
        output.mkdir(parents=True)
        writer = PdfWriter()
        images = []
        for number in range(1, self.counts[audience] + 1):
            writer.add_blank_page(width=144, height=216)
            image = output / f"page-{number}.png"
            Image.new("RGB", (300, 450), (number * 20, 80 if audience == "student" else 160, 90)).save(image)
            images.append(image)
        pdf = output / (source.stem + ".pdf")
        with pdf.open("wb") as stream:
            writer.write(stream)
        return images, pdf, {"tool": "synthetic-test-renderer", "invocation_mode": "test-double", "dpi": 150}


def prepare(inputs, tmp_path, renderer=None, **kwargs):
    renderer = renderer or SyntheticRenderer()
    root = tmp_path / "frozen"
    value = pagination.prepare_pages(inputs, root, renderer=renderer, **kwargs)
    return root, value, renderer


def diagnostic(root):
    return json.loads((root / pagination.FAILURE_FILENAME).read_text(encoding="utf-8"))


def test_prepare_freezes_both_docx_pdf_and_every_real_png_before_return(inputs, tmp_path):
    originals = {audience: path.read_bytes() for audience, path in inputs.items()}
    root, manifest, renderer = prepare(inputs, tmp_path)
    assert [call[0].stem for call in renderer.calls] == ["student", "teacher"]
    assert manifest["status"] == "rendered_pending_review"
    assert all(value is False for value in manifest["gates"].values())
    assert manifest["manifest_sha256"] == pagination._manifest_hash(manifest)
    assert (root / pagination.MANIFEST_FILENAME).is_file()
    assert not (root / pagination.FAILURE_FILENAME).exists()
    assert [manifest["versions"][audience]["page_count"] for audience in ("student", "teacher")] == [2, 3]
    for audience, version in manifest["versions"].items():
        assert (root / version["docx"]["path"]).read_bytes() == originals[audience]
        assert inputs[audience].read_bytes() == originals[audience]
        assert (root / version["pdf"]["path"]).read_bytes().startswith(b"%PDF-")
        for number, page in enumerate(version["pages"], 1):
            raw = (root / page["path"]).read_bytes()
            assert page["page_number"] == number
            assert page["sha256"] == hashlib.sha256(raw).hexdigest()
            assert (page["width_px"], page["height_px"]) == (300, 450)
            assert (page["pdf_width_pt"], page["pdf_height_pt"]) == (144, 216)


def test_readers_verify_exact_frozen_bytes_without_renderer_or_mutation(inputs, tmp_path, monkeypatch):
    root, manifest, renderer = prepare(inputs, tmp_path)
    expected = manifest["manifest_sha256"]
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    monkeypatch.setattr(pagination, "prepare_pages", lambda *args, **kwargs: pytest.fail("must not render on read"))
    assert pagination.load_prepared_pages(root, expected_manifest_sha256=expected) == manifest
    assert pagination.verify_prepared_pages(root, manifest, expected_manifest_sha256=expected) == manifest
    for audience, version in manifest["versions"].items():
        for number, page in enumerate(version["pages"], 1):
            assert pagination.read_frozen_page(root, manifest, audience, number, expected_manifest_sha256=expected) == (root / page["path"]).read_bytes()
        for fmt in ("docx", "pdf"):
            assert pagination.read_frozen_artifact(root, manifest, audience, fmt, expected_manifest_sha256=expected) == (root / version[fmt]["path"]).read_bytes()
    assert len(renderer.calls) == 2
    assert before == {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("artifact", ["docx", "pdf", "page"])
def test_any_artifact_hash_change_blocks_all_frozen_readers(inputs, tmp_path, artifact):
    root, manifest, _ = prepare(inputs, tmp_path)
    version = manifest["versions"]["teacher"]
    record = version["pages"][0] if artifact == "page" else version[artifact]
    target = root / record["path"]
    target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises(pagination.MixedPaperPaginationError, match="已经变化"):
        pagination.read_frozen_page(root, manifest, "student", 1)
    with pytest.raises(pagination.MixedPaperPaginationError):
        pagination.read_frozen_artifact(root, manifest, "student", "docx")


def test_expected_manifest_hash_blocks_even_resealed_changed_contract(inputs, tmp_path):
    root, manifest, _ = prepare(inputs, tmp_path)
    changed = deepcopy(manifest)
    changed["versions"]["student"]["renderer"]["tool"] = "changed-renderer"
    changed["manifest_sha256"] = pagination._manifest_hash(changed)
    with pytest.raises(pagination.MixedPaperPaginationError, match="确认绑定"):
        pagination.verify_prepared_pages(root, changed, expected_manifest_sha256=manifest["manifest_sha256"])


@pytest.mark.parametrize("mutation", ["fake_pdf", "no_pdf", "empty_png", "wrong_size", "missing_page", "extra_page", "reordered", "duplicate"])
def test_renderer_incomplete_or_invalid_output_never_becomes_ready(inputs, tmp_path, mutation):
    base = SyntheticRenderer()

    def renderer(source, output, *, toolchain):
        pages, pdf, metadata = base(source, output, toolchain=toolchain)
        if mutation == "fake_pdf":
            pdf.write_bytes(b"%PDF-FAKE-NOT-A-REAL-PDF")
        elif mutation == "no_pdf":
            pdf.unlink()
        elif mutation == "empty_png":
            pages[0].write_bytes(b"")
        elif mutation == "wrong_size":
            Image.new("RGB", (299, 444), "white").save(pages[0])
        elif mutation == "missing_page":
            pages = pages[:-1]
        elif mutation == "extra_page":
            Image.new("RGB", (300, 450), "white").save(output / "page-99.png")
        elif mutation == "reordered":
            pages.reverse()
        elif mutation == "duplicate":
            pages[1] = pages[0]
        return pages, pdf, metadata

    root = tmp_path / "frozen"
    with pytest.raises(pagination.MixedPaperPaginationError):
        pagination.prepare_pages(inputs, root, renderer=renderer)
    assert diagnostic(root)["status"] == "failed" and diagnostic(root)["ready"] is False
    assert diagnostic(root)["automatic_retry"] is False
    assert not (root / pagination.MANIFEST_FILENAME).exists()


def test_teacher_failure_retains_student_outputs_and_safe_diagnostics_without_retry(inputs, tmp_path):
    base = SyntheticRenderer()

    def renderer(source, output, *, toolchain):
        if source.stem == "teacher":
            raise RuntimeError("sensitive synthetic detail must not enter the diagnostic")
        return base(source, output, toolchain=toolchain)

    root = tmp_path / "frozen"
    with pytest.raises(pagination.MixedPaperPaginationError):
        pagination.prepare_pages(inputs, root, renderer=renderer)
    assert len(base.calls) == 1
    assert (root / "student/render/student.pdf").is_file()
    assert diagnostic(root)["stage"] == "render_teacher"
    assert "sensitive" not in (root / pagination.FAILURE_FILENAME).read_text(encoding="utf-8")
    assert not (root / pagination.MANIFEST_FILENAME).exists()


def test_source_docx_change_during_render_invalidates_prepare(inputs, tmp_path):
    base = SyntheticRenderer()

    def renderer(source, output, *, toolchain):
        result = base(source, output, toolchain=toolchain)
        docx(inputs[source.stem], "changed source while converting")
        return result

    root = tmp_path / "frozen"
    with pytest.raises(pagination.MixedPaperPaginationError) as error:
        pagination.prepare_pages(inputs, root, renderer=renderer)
    assert error.value.code == "pagination_source_changed"
    assert len(base.calls) == 1
    assert not (root / pagination.MANIFEST_FILENAME).exists()


def test_modified_frozen_docx_is_rejected_even_if_input_stays_unchanged(inputs, tmp_path):
    base = SyntheticRenderer()

    def renderer(source, output, *, toolchain):
        result = base(source, output, toolchain=toolchain)
        docx(source, "renderer must not rewrite DOCX")
        return result

    with pytest.raises(pagination.MixedPaperPaginationError) as error:
        prepare(inputs, tmp_path, renderer)
    assert error.value.code == "pagination_file_changed"


def test_cancel_after_student_renderer_returns_does_not_start_teacher_or_publish(inputs, tmp_path):
    event, base = Event(), SyntheticRenderer()

    def renderer(source, output, *, toolchain):
        result = base(source, output, toolchain=toolchain)
        event.set()
        return result

    root = tmp_path / "frozen"
    with pytest.raises(ReadCancelled):
        with read_cancel_scope(event):
            pagination.prepare_pages(inputs, root, renderer=renderer)
    assert len(base.calls) == 1
    assert diagnostic(root)["status"] == "cancelled"
    assert diagnostic(root)["ready"] is False
    assert not (root / pagination.MANIFEST_FILENAME).exists()


def test_page_limit_and_new_output_directory_fail_closed(inputs, tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("keep existing diagnostics", encoding="utf-8")
    with pytest.raises(pagination.MixedPaperPaginationError) as error:
        pagination.prepare_pages(inputs, root, renderer=SyntheticRenderer())
    assert error.value.code == "pagination_output_exists"
    assert marker.read_text(encoding="utf-8") == "keep existing diagnostics"
    with pytest.raises(pagination.MixedPaperPaginationError):
        prepare(inputs, tmp_path, max_pages=1)


def test_renderer_cannot_register_external_pages_or_cross_audience_artifacts(inputs, tmp_path):
    base = SyntheticRenderer()
    outside = tmp_path / "page-1.png"
    Image.new("RGB", (300, 450), "white").save(outside)

    def renderer(source, output, *, toolchain):
        pages, pdf, metadata = base(source, output, toolchain=toolchain)
        pages[0] = outside
        return pages, pdf, metadata

    with pytest.raises(pagination.MixedPaperPaginationError):
        prepare(inputs, tmp_path, renderer)
    assert outside.is_file()


@pytest.mark.parametrize("bad_path", ["../student.docx", "/student.docx", "student\\student.docx", "teacher/render/teacher.pdf"])
def test_manifest_path_escape_or_role_swap_is_rejected(inputs, tmp_path, bad_path):
    root, manifest, _ = prepare(inputs, tmp_path)
    manifest["versions"]["student"]["docx"]["path"] = bad_path
    manifest["manifest_sha256"] = pagination._manifest_hash(manifest)
    with pytest.raises(pagination.MixedPaperPaginationError):
        pagination.verify_prepared_pages(root, manifest)


@pytest.mark.parametrize("mutation", ["page_number", "dimension", "gate", "missing_version", "extra_field"])
def test_resealed_invalid_manifest_contract_is_rejected(inputs, tmp_path, mutation):
    root, manifest, _ = prepare(inputs, tmp_path)
    if mutation == "page_number":
        manifest["versions"]["student"]["pages"][0]["page_number"] = 2
    elif mutation == "dimension":
        manifest["versions"]["student"]["pages"][0]["width_px"] = 500
    elif mutation == "gate":
        manifest["gates"]["human_reviewed"] = True
    elif mutation == "missing_version":
        del manifest["versions"]["teacher"]
    else:
        manifest["unapproved_extra"] = True
    manifest["manifest_sha256"] = pagination._manifest_hash(manifest)
    with pytest.raises(pagination.MixedPaperPaginationError):
        pagination.verify_prepared_pages(root, manifest)


def test_invalid_docx_or_duplicate_source_rejected_before_render(inputs, tmp_path):
    inputs["student"].write_bytes(b"not a docx")
    renderer = SyntheticRenderer()
    with pytest.raises(pagination.MixedPaperPaginationError):
        prepare(inputs, tmp_path, renderer)
    assert not renderer.calls


def test_same_source_path_cannot_stand_in_for_two_audience_versions(inputs, tmp_path):
    inputs["teacher"] = inputs["student"]
    renderer = SyntheticRenderer()
    with pytest.raises(pagination.MixedPaperPaginationError, match="各自明确"):
        prepare(inputs, tmp_path, renderer)
    assert not renderer.calls


def test_success_looking_manifest_with_failure_marker_is_not_reusable(inputs, tmp_path):
    root, manifest, _ = prepare(inputs, tmp_path)
    (root / pagination.FAILURE_FILENAME).write_text('{"status":"failed"}', encoding="utf-8")
    with pytest.raises(pagination.MixedPaperPaginationError, match="记录过失败"):
        pagination.load_prepared_pages(root, expected_manifest_sha256=manifest["manifest_sha256"])


def test_default_renderer_reuses_existing_engine_and_validated_toolchain(inputs, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import (
        paper_export_renderer,
        paper_export_workbench,
    )

    base = SyntheticRenderer()

    class Toolchain:
        def validated(self):
            return self

    chain = Toolchain()
    monkeypatch.setattr(paper_export_workbench, "_locate_toolchain", lambda: chain)
    monkeypatch.setattr(paper_export_renderer, "_render_with_canonical_docx_tool", base)
    pagination.prepare_pages(inputs, tmp_path / "frozen")
    assert len(base.calls) == 2 and all(call[2] is chain for call in base.calls)


def test_missing_default_tools_report_chinese_failure_not_ready(inputs, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import paper_export_workbench
    from integrations.deeptutor_shchem_v1.paper_export_renderer import (
        PaperExportRendererError,
    )

    class MissingTools:
        def validated(self):
            raise PaperExportRendererError("renderer_tool_missing", "Synthetic missing tools")

    monkeypatch.setattr(paper_export_workbench, "_locate_toolchain", lambda: MissingTools())
    root = tmp_path / "frozen"
    with pytest.raises(pagination.MixedPaperPaginationError, match="缺少本地分页工具"):
        pagination.prepare_pages(inputs, root)
    assert diagnostic(root)["error_code"] == "renderer_tool_missing"
    assert not (root / pagination.MANIFEST_FILENAME).exists()


def test_invalid_page_or_artifact_selector_is_rejected(inputs, tmp_path):
    root, manifest, _ = prepare(inputs, tmp_path)
    for audience, number in (("unknown", 1), ("student", 0), ("student", True), ("teacher", 4)):
        with pytest.raises(pagination.MixedPaperPaginationError):
            pagination.read_frozen_page(root, manifest, audience, number)
    with pytest.raises(pagination.MixedPaperPaginationError):
        pagination.read_frozen_artifact(root, manifest, "teacher", "html")


def test_corrupt_manifest_json_fails_closed_on_load(inputs, tmp_path):
    root, manifest, _ = prepare(inputs, tmp_path)
    path = root / pagination.MANIFEST_FILENAME
    path.write_text('{"status":"failed","status":"rendered_pending_review"}', encoding="utf-8")
    with pytest.raises(pagination.MixedPaperPaginationError):
        pagination.load_prepared_pages(root, expected_manifest_sha256=manifest["manifest_sha256"])
