"""A synthetic vector remains source-bound across Word view and preparation."""

import hashlib
import io
import struct
import zipfile

import pytest
from test_desktop_visual_import_facade import FakeProviderStore, _facade
from test_desktop_visual_import_facade import (
    desktop_paths as desktop_paths,  # noqa: PLC0414
)
from test_word_metafile_preview import _emf, _fake_raster, _wmf

from integrations.deeptutor_shchem_v1 import desktop_word_metafile_preview as renderer
from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    PreparationImageStore,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    PreparationSourceError,
    PreparationSourcesService,
)


def _document(data, extension):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr(
            "word/document.xml",
            """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:v="urn:schemas-microsoft-com:vml"><w:body>
        <w:p><w:r><w:t>【例1】观察图示，写出线段两个端点。</w:t></w:r></w:p>
        <w:p><w:r><w:pict><v:shape><v:imagedata r:id="rId1"/></v:shape></w:pict></w:r></w:p>
        <w:p><w:r><w:t>【答案】图中左上、右下两个端点。</w:t></w:r></w:p>
        </w:body></w:document>""",
        )
        package.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.{extension}"/></Relationships>',
        )
        package.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="{extension}" ContentType="image/{extension}"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        package.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        package.writestr(f"word/media/image1.{extension}", data)
    return output.getvalue()


@pytest.fixture(autouse=True)
def mock_raster(monkeypatch):
    monkeypatch.setattr(renderer, "_native_rasterize", _fake_raster)
    monkeypatch.setattr(renderer, "_gdiplus_rasterize", lambda data, size: _fake_raster(data, size, None))
    with renderer._LOCK:
        renderer._CACHE.clear()
        renderer._cache_bytes = 0


def test_converted_preview_keeps_original_metadata_and_hash_guard(desktop_paths):
    reader = PreparationSourcesService(desktop_paths.workspace_root)
    original = _emf()
    data = _document(original, "emf")
    before = reader.word_preview_bytes(data, "合成测试.docx")
    asset = before["assets"][0]
    assert asset["mime_type"] == "image/emf" and not asset["preview_supported"]
    with pytest.raises(PreparationSourceError):
        reader.word_asset_bytes(data, asset["asset_id"])
    result = reader.word_asset_bytes(
        data, asset["asset_id"], render_metafiles=True, expected_sha256=asset["sha256"]
    )
    assert result["derived_preview"] and result["bytes"].startswith(b"\x89PNG")
    assert result["original_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["preview_sha256"] == hashlib.sha256(result["bytes"]).hexdigest()
    assert reader.word_preview_bytes(data, "合成测试.docx") == before
    with pytest.raises(PreparationSourceError):
        reader.word_asset_bytes(
            data, asset["asset_id"], render_metafiles=True, expected_sha256="0" * 64
        )


@pytest.mark.parametrize("extension", ["emf", "wmf"])
def test_derived_picture_can_be_previewed_and_imported_with_original_provenance(
    desktop_paths, tmp_path, extension
):
    raw = _emf() if extension == "emf" else _wmf()
    source = tmp_path / "矢量测试.docx"
    document = _document(raw, extension)
    source.write_bytes(document)
    provider = FakeProviderStore(configured=False)
    facade = _facade(desktop_paths, provider)
    receipt = facade.save_visual_import_batch(
        handout_files=(source,), source_type="教师讲义"
    )
    item = facade.word_question_catalog()["items"][0]
    asset = item["question_blocks"][1]["assets"][0]
    shown = facade.word_question_image(item["key"], item["revision"], asset["asset_id"])
    assert shown["derived_preview"]
    full = facade.imported_word_asset(
        receipt.batch_id, item["archive_source_id"], asset["asset_id"]
    )
    assert full["bytes"] == shown["bytes"]
    selection = [{"key": item["key"], "revision": item["revision"]}]
    reference = facade.word_question_reference(selection)
    assert not reference["image_issues"] and len(reference["image_assets"]) == 1
    assert hashlib.sha256(raw).hexdigest() in reference["materials"]
    assert "本地转换预览" in reference["image_assets"][0]["caption"]
    imported = facade.import_word_question_reference(reference, [])
    stored = PreparationImageStore(
        desktop_paths.task_root / "preparation-v1" / "images"
    )
    assert stored.load(imported["image_assets"][0]) == shown["bytes"]
    assert source.read_bytes() == document and provider.borrow_calls == 0


def test_unreliable_wmf_does_not_silently_become_a_preparation_picture(
    desktop_paths, tmp_path
):
    source = tmp_path / "旧公式测试.docx"
    document = _document(_wmf(extra_record=struct.pack("<IHH", 4, 0x7777, 0)), "wmf")
    source.write_bytes(document)
    facade = _facade(desktop_paths, FakeProviderStore(configured=False))
    facade.save_visual_import_batch(handout_files=(source,), source_type="教师讲义")
    item = facade.word_question_catalog()["items"][0]
    reference = facade.word_question_reference(
        [{"key": item["key"], "revision": item["revision"]}]
    )
    assert reference["image_issues"] and reference["image_assets"] == []
    assert source.read_bytes() == document
