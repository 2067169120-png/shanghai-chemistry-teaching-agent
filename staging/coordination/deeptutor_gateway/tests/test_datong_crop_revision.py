from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from itertools import pairwise
from pathlib import Path

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1.datong_crop_revision import (
    RECROPS,
    REVISION_ID,
    SOURCE_PATH,
    SOURCE_SHA256,
    project_datong_descriptor,
    recrop_datong_view,
    visible_evidence,
)
from integrations.deeptutor_shchem_v1.desktop_library import image_descriptors
from integrations.deeptutor_shchem_v1.paper_export_workbench import _descriptor_rows
from integrations.deeptutor_shchem_v1.source_crop_revision import (
    SourceCropRevisionError,
)

ROOT = Path(__file__).resolve().parents[4] / "sh-chem-db"
MANIFEST = (
    ROOT
    / "kb/formal/candidates/intake_round_2026-08-02/datong_high1_2025_fall_midterm_complete_paper/crop_manifest.jsonl"
)


def test_unknown_crop_does_not_touch_the_filesystem(tmp_path):
    assert recrop_datong_view(tmp_path, "other", b"unchanged") == b"unchanged"
    assert project_datong_descriptor(tmp_path, {"crop_id": "other"}) == {
        "crop_id": "other"
    }


def test_changed_crop_or_descriptor_is_not_silently_repaired(tmp_path):
    crop_id = next(iter(RECROPS))
    with pytest.raises(SourceCropRevisionError, match="原裁片"):
        recrop_datong_view(tmp_path, crop_id, b"wrong")
    with pytest.raises(SourceCropRevisionError, match="原归档"):
        project_datong_descriptor(tmp_path, {"crop_id": crop_id, "sha256": "0" * 64})


def test_changed_source_is_rejected_even_with_an_archived_descriptor(tmp_path):
    source = tmp_path / SOURCE_PATH
    source.parent.mkdir(parents=True)
    source.write_bytes(b"changed source fixture")
    crop_id = next(iter(RECROPS))
    with pytest.raises(SourceCropRevisionError, match="原页已变化"):
        project_datong_descriptor(
            tmp_path,
            {
                "crop_id": crop_id,
                "sha256": RECROPS[crop_id][0],
                "evidence_role": "question",
            },
        )


@pytest.mark.skipif(not MANIFEST.is_file(), reason="requires local source pages")
@pytest.mark.parametrize("crop_id", RECROPS)
def test_repaired_views_are_exact_source_pixels_with_unchanged_originals(crop_id):
    rows = [
        json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines()
    ]
    original_path = ROOT / next(
        row["crop_asset"] for row in rows if row["crop_id"] == crop_id
    )
    original = original_path.read_bytes()
    sha, box = RECROPS[crop_id]
    assert hashlib.sha256(original).hexdigest() == sha
    revised = recrop_datong_view(ROOT, crop_id, original)
    role = "shared_material" if "SHARED" in crop_id else "question"
    descriptor = {
        "crop_id": crop_id,
        "sha256": sha,
        "evidence_role": role,
        "width": 1,
        "height": 1,
        "bytes": 1,
        "visual_inspection_status": "actually_viewed_by_primary_model",
    }
    before = deepcopy(descriptor)
    projected = project_datong_descriptor(ROOT, descriptor)
    assert descriptor == before
    assert projected["sha256"] == hashlib.sha256(revised).hexdigest()
    assert projected["archived_crop_sha256"] == sha
    assert projected["presentation_revision_id"] == REVISION_ID
    assert projected["bytes"] == len(revised)
    assert len(revised) < 1024 * 1024
    source = ROOT / SOURCE_PATH
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    with Image.open(source) as page, Image.open(io.BytesIO(revised)) as actual:
        expected = page.crop(box)
        assert actual.size == (projected["width"], projected["height"])
        assert actual.tobytes() == expected.tobytes()
    assert original_path.read_bytes() == original


@pytest.mark.parametrize(
    "question,figure",
    [
        ("DT2025-H1-Q01-E1", "DT2025-H1-SHARED-VIS_Q01_APPARATUS"),
        ("DT2025-H1-Q08-E1", "DT2025-H1-SHARED-VIS_Q08_APPARATUS_SET"),
    ],
)
def test_embedded_figure_is_suppressed_only_when_complete_question_is_present(
    question, figure
):
    def descriptor(crop_id, role):
        return {
            "crop_id": crop_id,
            "evidence_role": role,
            "sha256": "a" * 64,
            "presentation_revision_id": REVISION_ID,
        }

    q = descriptor(question, "question")
    f = descriptor(figure, "shared_material")
    rows = [q, f]
    assert visible_evidence(rows) == [q]
    assert visible_evidence([f]) == [f]
    assert _descriptor_rows({"evidence_descriptors": rows}) == [q]
    assert [
        x.crop_id
        for x in image_descriptors("wave1", "node", {"evidence_descriptors": rows})
    ] == [question]
    f["presentation_revision_id"] = "unverified"
    assert visible_evidence(rows) == rows
    outer = RECROPS[question][1]
    inner = RECROPS[figure][1]
    assert outer[0] <= inner[0] < inner[2] <= outer[2]
    assert outer[1] <= inner[1] < inner[3] <= outer[3]


def test_adjacent_printed_questions_do_not_overlap():
    boxes = sorted(
        {box for crop_id, (_, box) in RECROPS.items() if "SHARED" not in crop_id},
        key=lambda box: box[1],
    )
    assert all(left[3] <= right[1] for left, right in pairwise(boxes))


@pytest.mark.skipif(not MANIFEST.is_file(), reason="requires local source pages")
def test_q33_restores_parent_conditions_and_deduplicates_identical_parent_view():
    rows = {
        row["crop_id"]: row
        for row in (
            json.loads(line)
            for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        )
    }
    images = []
    for part in ("P01", "P02", "P03"):
        crop_id = f"DT2025-H1-Q33-{part}-E1"
        assert "DT2025-H1-Q33" in rows[crop_id]["bindings"]
        original = (ROOT / rows[crop_id]["crop_asset"]).read_bytes()
        images.append(recrop_datong_view(ROOT, crop_id, original))
    assert images[0] == images[1] == images[2]
    box = RECROPS["DT2025-H1-Q33-P01-E1"][1]
    assert box[1] < 6215 and box[3] > 6490  # A-E conditions through final response line
