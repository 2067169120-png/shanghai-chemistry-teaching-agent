from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1.source_crop_revision import (
    FENGXIAN_RECROPS,
    SourceCropRevisionError,
    recrop_fengxian_view,
)

ROOT = Path(__file__).resolve().parents[4] / "sh-chem-db"
PRODUCT = (
    ROOT
    / "kb/classification/question_visual_scan_fengxian_2025_second_mock_theme2_disinfectant_v2_2026-09-08"
)


def test_unknown_crop_does_not_read_files(tmp_path):
    assert recrop_fengxian_view(tmp_path, "other", b"unchanged") == b"unchanged"


def test_revision_requires_the_original_crop_hash(tmp_path):
    with pytest.raises(SourceCropRevisionError, match="原裁图版本"):
        recrop_fengxian_view(tmp_path, "paper-p03-q2", b"wrong")


@pytest.mark.skipif(not PRODUCT.is_dir(), reason="requires local source pages")
@pytest.mark.parametrize("crop_id", FENGXIAN_RECROPS)
def test_revised_pixels_are_exact_original_page_rectangle_and_archive_unchanged(
    crop_id,
):
    original_path = PRODUCT / "evidence" / (crop_id + ".png")
    original = original_path.read_bytes()
    expected_sha, page, box = FENGXIAN_RECROPS[crop_id]
    revised = recrop_fengxian_view(ROOT, crop_id, original)
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == expected_sha
    assert revised != original
    source = (
        ROOT
        / "03_各区一二模/奉贤区/2025-奉贤区-二模-化学试卷与参考答案"
        / f"试卷-page-0{page}.png"
    )
    with Image.open(source) as image, Image.open(io.BytesIO(revised)) as actual:
        expected = image.crop(box)
        assert actual.size == expected.size
        assert actual.tobytes() == expected.tobytes()


def test_adjacent_question_rectangles_do_not_overlap():
    previous = {}
    for crop_id, (_, page, box) in FENGXIAN_RECROPS.items():
        if "-q" not in crop_id:
            continue
        assert box[1] >= previous.get(page, 0)
        previous[page] = box[3]
