from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

QA_ROOT = os.environ.get("SHCHEM_LIBRARY_QA_OUTPUT")
pytestmark = pytest.mark.skipif(
    not QA_ROOT,
    reason="set SHCHEM_LIBRARY_QA_OUTPUT to a completed isolated QA directory",
)


def test_real_library_visual_qa_manifest_and_screenshots() -> None:
    root = Path(str(QA_ROOT)).resolve()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "shchem.native-library-detail-visual-qa.v1"
    assert manifest["network_used"] is False
    assert manifest["model_used"] is False
    assert manifest["credential_store_accessed"] is False
    assert manifest["central_database_mutated"] is False
    assert manifest["summary"] == {
        "scope_count": 3,
        "screenshot_count": 24,
        "all_horizontal_maximum_zero": True,
        "all_zoom_toolbars_fit": True,
        "all_zoom_images_horizontally_pannable": True,
        "all_screenshot_hashes_unique": True,
    }
    assert [item["scope"] for item in manifest["scopes"]] == [
        "master",
        "wave1",
        "supplemental",
    ]
    screenshots = []
    for scope in manifest["scopes"]:
        assert scope["part_count"] > 0
        assert scope["question_image_count"] > 0
        assert [item["dialog_width"] for item in scope["widths"]] == [900, 420]
        for width in scope["widths"]:
            assert width["layout_stability"]["stable"] is True
            assert width["layout_stability"]["stable_rounds"] >= 3
            assert width["layout_stability"]["heights_match_pixmaps"] is True
            assert all(tab["horizontal_maximum"] == 0 for tab in width["tabs"])
            assert width["zoom"]["percent"] == 150
            assert width["zoom"]["toolbar_fits"] is True
            assert width["zoom"]["horizontal_maximum"] > 0
            assert width["zoom"]["horizontal_bar_height"] > 0
            screenshots.extend(width["screenshots"])
            screenshots.append(width["zoom"]["screenshot"])
    assert len(screenshots) == 24
    for record in screenshots:
        path = Path(record["path"])
        assert path.parent == root
        payload = path.read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(payload) == record["bytes"]
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
