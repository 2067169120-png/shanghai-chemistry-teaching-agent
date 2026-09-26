"""Compare real Word preparation input against the shipped 0.1.39 extractor.

Read-only sources, isolated UI state, no provider access and no model call.
The comparison proves native text/structure retention, not visual recognition.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from docx import Document
from PyInstaller.archive.readers import CArchiveReader

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    PreparationSourcesService,
    _body_blocks,
    _local,
    _table_rows,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import (
    PreparationSourcesDialog,
)


class NoProvider:
    def __getattr__(self, name):
        raise AssertionError("Source selection must not access provider settings")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)
    source = (
        ROOT
        / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-032/第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）.docx"
    )
    expected_hash = "d60317b8e533b957943e98b481305b85557d030d3056bf2eb0e9273f2811162d"
    assert digest(source) == expected_hash
    old_exe = (
        ROOT
        / "runtime/deeptutor_shchem/desktop_package_0.1.39/沪上化学智研台/沪上化学智研台.exe"
    )
    assert (
        digest(old_exe)
        == "c90e09c7af0c87c5febf3887649e4e043eb30c74cfb5ce675d59556b4c5f32c2"
    )
    archive = CArchiveReader(str(old_exe))
    pyz = archive.open_embedded_archive(
        next(key for key in archive.toc if key.endswith(".pyz"))
    )
    legacy = types.ModuleType("integrations.deeptutor_shchem_v1._qa_legacy_sources")
    legacy.__package__ = "integrations.deeptutor_shchem_v1"
    exec(  # noqa: S102 - hash-pinned local baseline, no entrypoint
        pyz.extract("integrations.deeptutor_shchem_v1.desktop_preparation_sources"),
        legacy.__dict__,
    )
    old_service = legacy.PreparationSourcesService(ROOT)
    new_service = PreparationSourcesService(ROOT)
    old = old_service.word_preview(source)
    new = new_service.word_preview(source)
    assert len(old["blocks"]) == len(new["blocks"])
    assert old["sections"] == new["sections"]
    document = Document(source)
    elements = list(_body_blocks(document._element.body))
    tables, paragraph_count = [], 0
    for old_block, new_block, element in zip(
        old["blocks"][38:123], new["blocks"][38:123], elements[38:123], strict=True
    ):
        assert old_block["index"] == new_block["index"]
        assert old_block["warnings"] == new_block["warnings"]
        if _local(element) != "tbl":
            assert old_block["text"] == new_block["text"]
            paragraph_count += 1
            continue
        old_rows = json.loads(old_block["text"].split("\n", 1)[1])
        new_rows = _table_rows(element, document, set())
        # Exact native cells and merge geometry, independently extracted by the
        # old frozen code and current source. Fail on changed/missing content.
        assert old_rows == new_rows
        for row in old_rows:
            for cell in row["cells"]:
                if cell["text"]:
                    assert cell["text"] in new_block["text"]
        tables.append(
            {
                "block": old_block["index"],
                "rows": len(old_rows),
                "cells": sum(len(row["cells"]) for row in old_rows),
                "before_characters": len(old_block["text"]),
                "after_characters": len(new_block["text"]),
                "native_cells_and_merge_geometry_exact": True,
            }
        )
    before = old_service.reference(source, expected_hash, 39, 123, [])
    after = new_service.reference(source, expected_hash, 39, 123, [])
    assert before["warnings"] == after["warnings"]
    for name, value in (("before", before), ("after", after)):
        (output / f"word-reference-{name}.txt").write_text(
            value["materials"], encoding="utf-8"
        )
    state = Path(tempfile.mkdtemp(prefix="shchem-readable-source-"))
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=state), provider_store=NoProvider()
    )
    app = create_application([])
    install_font_fallbacks()
    dialog = PreparationSourcesDialog(facade)
    dialog._load_word(str(source))
    dialog.block_start.setValue(39)
    dialog.block_end.setValue(123)
    dialog._compile_preview()
    assert dialog.reference == after
    assert dialog.preview.toPlainText() == after["materials"]
    dialog.show()
    images = []
    for width in (900, 420):
        dialog.resize(width, 800)
        app.processEvents()
        dialog.preview.moveCursor(dialog.preview.textCursor().MoveOperation.Start)
        assert dialog.preview.find("〔第1行·第1列〕")
        app.processEvents()
        image = output / f"source-preview-{width}.png"
        assert dialog.grab().save(str(image))
        images.append(str(image))
    dialog._confirm()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.reference == after
    assert digest(source) == expected_hash
    result = {
        "source_sha256": expected_hash,
        "baseline_exe_sha256": digest(old_exe),
        "word_blocks": [39, 123],
        "selected_blocks": 85,
        "unchanged_non_table_blocks": paragraph_count,
        "tables": tables,
        "before_characters": len(before["materials"]),
        "after_characters": len(after["materials"]),
        "warnings_retained_exactly": len(after["warnings"]),
        "ui_preview_and_confirm_match_compiled_reference": True,
        "source_unchanged": True,
        "network_calls": 0,
        "screenshots": images,
        "visual_review": "pending",
        "model_source_adoption_verified": False,
        "teaching_approved": False,
    }
    (output / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True))
    dialog.close()


if __name__ == "__main__":
    main()
