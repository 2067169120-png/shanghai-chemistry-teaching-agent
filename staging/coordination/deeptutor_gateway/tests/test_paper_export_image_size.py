import hashlib

import pytest
from docx import Document
from PIL import Image

from integrations.deeptutor_shchem_v1.paper_export_renderer import _add_asset_block


@pytest.mark.parametrize(
    ("size", "width_mm", "height_mm"),
    [
        ((130, 165), 130 * 25.4 / 120, 165 * 25.4 / 120),
        ((1000, 300), 162, 48.6),
        ((1000, 3000), 70, 210),
    ],
)
def test_source_image_scale_is_bounded_without_modifying_pixels(
    tmp_path, size, width_mm, height_mm
):
    source = tmp_path / "fixture.png"
    Image.new("RGB", size, "white").save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    document = Document()
    _add_asset_block(document, {"asset_ref": source.name}, tmp_path, max_width_mm=162)
    shape = document.inline_shapes[0]
    assert shape.width.mm == pytest.approx(width_mm, abs=0.001)
    assert shape.height.mm == pytest.approx(height_mm, abs=0.001)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
