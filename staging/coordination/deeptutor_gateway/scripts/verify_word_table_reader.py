"""Read-only selected-source QA; private widget grabs, no app state or model."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_word_table_preview import (
    WordTablePreviewService,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_lesson_reader import (
    WordLessonReader,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--block", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a fresh private QA output directory")
    data, cache_bytes = args.source.read_bytes(), args.cache.read_bytes()
    value = json.loads(gzip.decompress(cache_bytes))["preview"]
    source_sha = hashlib.sha256(data).hexdigest()
    facade = SimpleNamespace(
        _imported_word_source=lambda *_: SimpleNamespace(
            content=data, filename=value["source_name"]
        ),
        imported_word_preview=lambda *_: deepcopy(value),
    )
    tables = WordTablePreviewService(facade).preview(
        "selected-source-qa", "selected-docx", source_sha, value["revision"]
    )
    if args.block not in tables["tables"]:
        raise ValueError("The selected block is not an original table")
    app = QApplication.instance() or QApplication([])
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft YaHei", 10))
    reader = WordLessonReader()
    reader.set_source(value)
    assert reader.set_table_previews(tables)
    assert args.block in reader._table_grids, "Selected table fell back to source text"
    args.output.mkdir(parents=True)
    shots = []
    for width in (420, 900):
        reader.resize(width, 1000)
        reader.show()
        for _ in range(4):
            app.processEvents()
        reader.scroll_to_block(args.block)
        app.processEvents()
        image_path = args.output / f"source-table-{width}.png"
        assert reader.grab().save(str(image_path))
        shots.append({
            "width": width,
            "horizontal_scroll_maximum": reader.browser.horizontalScrollBar().maximum(),
            "path": str(image_path.resolve()),
        })
    report = {
        "source_sha256": source_sha,
        "source_revision": value["revision"],
        "selected_block": args.block,
        "source_tables_read": len(tables["tables"]),
        "grid_tables": len(reader._table_grids),
        "fallback_tables": len(reader._table_fallbacks),
        "source_bytes_unchanged": args.source.read_bytes() == data,
        "cache_bytes_unchanged": args.cache.read_bytes() == cache_bytes,
        "screenshots": shots,
        "boundary": "One source and one selected table; not all 98 documents or Word layout approval.",
    }
    assert report["source_bytes_unchanged"] and report["cache_bytes_unchanged"]
    assert all(item["horizontal_scroll_maximum"] == 0 for item in shots)
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    reader.close()


if __name__ == "__main__":
    main()
