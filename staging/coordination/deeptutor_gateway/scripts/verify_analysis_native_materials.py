"""Audit all imported original lesson documents and their cached text/image refs.

No model, rewrite, rasterization, personal setting read or duplicate import.
Image references are counted separately from unique image bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from import_analysis_teaching_pack import verified_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    from docx import Document
    from PIL import Image

    from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
        _body_blocks,
        _word_images,
    )
    from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import (
        WordPreviewCache,
    )

    cache = WordPreviewCache(args.state_root / "word-question-previews")
    inventory = ROOT / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/archive_inventory.csv"
    rows, decoded, unique_images = [], {}, set()
    started = time.monotonic()
    for source, metadata in verified_sources(inventory):
        data = source.read_bytes()
        archived = args.state_root / "visual-import-v2/sources" / (metadata["sha256"] + ".docx")
        if hashlib.sha256(archived.read_bytes()).hexdigest() != metadata["sha256"]:
            raise RuntimeError("Imported original no longer matches inventory")
        preview = cache.load(data, source.name)
        if preview is None:
            raise RuntimeError("Missing source-bound full native preview")
        document = Document(io.BytesIO(data))
        elements = list(_body_blocks(document._element.body))
        if len(elements) != len(preview["blocks"]):
            raise RuntimeError("Incomplete native body range")
        assets = _word_images(elements, document, include_bytes=True)
        if [{k: v for k, v in a.items() if k != "bytes"} for a in assets] != preview["assets"]:
            raise RuntimeError("Cached image references differ from original package")
        failures, formats = [], Counter()
        supported = 0
        for asset in assets:
            digest = hashlib.sha256(asset["bytes"]).hexdigest()
            if digest != asset["sha256"]:
                raise RuntimeError("Original image digest mismatch")
            unique_images.add(digest)
            formats[asset["mime_type"]] += 1
            if not asset["preview_supported"]:
                continue
            if digest not in decoded:
                try:
                    with Image.open(io.BytesIO(asset["bytes"])) as original:
                        if original.width * original.height > 50_000_000:
                            raise ValueError("image_dimensions_exceed_preview_limit")
                        original.verify()
                    decoded[digest] = True
                except (OSError, ValueError, SyntaxError):
                    decoded[digest] = False
            if decoded[digest]:
                supported += 1
            else:
                failures.append(asset["asset_id"])
        row = {"package_id": metadata["package_id"], "source_name": source.name,
               "source_sha256": metadata["sha256"], "archived_original_verified": True,
               "native_blocks": len(preview["blocks"]),
               "native_text_characters": sum(len(b["text"]) for b in preview["blocks"]),
               "native_tables": sum(e.tag.endswith("}tbl") for e in elements),
               "image_references": len(assets), "image_formats": dict(formats),
               "raster_references_decoded": supported,
               "non_raster_or_oversize_references_preserved_in_original": sum(not a["preview_supported"] for a in assets),
               "raster_decode_failures": failures,
               "blocks_with_original_object_warnings": sum(bool(b["warnings"]) for b in preview["blocks"])}
        rows.append(row)
        print(json.dumps({"package_id": row["package_id"], "blocks": row["native_blocks"], "image_refs": len(assets)}), flush=True)
    report = {"source_count": len(rows), "originals_verified": True,
              "text_is_full_native_extraction_not_summary": True,
              "semantic_or_all_page_visual_review_complete": False,
              "provider_calls": 0, "new_original_copies_created": 0,
              "native_blocks": sum(r["native_blocks"] for r in rows),
              "native_text_characters": sum(r["native_text_characters"] for r in rows),
              "native_tables": sum(r["native_tables"] for r in rows),
              "image_references": sum(r["image_references"] for r in rows),
              "unique_image_digests": len(unique_images),
              "raster_references_decoded": sum(r["raster_references_decoded"] for r in rows),
              "non_raster_or_oversize_references_preserved_in_original": sum(r["non_raster_or_oversize_references_preserved_in_original"] for r in rows),
              "raster_decode_failures": sum(len(r["raster_decode_failures"]) for r in rows),
              "sources": rows, "seconds": round(time.monotonic() - started, 2)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "sources"}), flush=True)


if __name__ == "__main__":
    main()
