"""Extract five lossless, separately versioned source-bound crop repairs.

No OCR, generative image model, retouching, content replacement, registry update,
or source write is performed. An existing output directory is always refused.
Pixel equality is a machine check, not human or semantic visual approval.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import stat
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

QA_RELATIVE = Path(
    "staging/coordination/deeptutor_gateway/private_qa/"
    "archived_wechat_native_0_1_54_r1/crop-defects.json"
)
OUTPUT_RELATIVE = Path("runtime/deeptutor_shchem/crop_repairs_0.1.55_r1")
EXPECTED_QA_SHA256 = "fa34a6c4cdc70b63858e7eb48eb199cffd721af0e82beb21b58fb5f29933c3c5"
EXPECTED_ATOMIC_IDS = (
    "SJ2025-EM-S2-Q8-P1",
    "SHEAST2025-M05-B-T5-Q1-P1",
    "SHEAST2025-M05-B-T5-Q2-P1",
    "SHEAST2025-M05-B-T5-Q3-P1",
    "SHEAST2025-M05-B-T5-Q4-P1",
)
AUTHORITY = {
    "candidate_only": True,
    "local_derivative_only": True,
    "human_review_complete": False,
    "chemistry_correctness_verified": False,
    "formal_promotion_allowed": False,
    "retrieval_ready": False,
    "teaching_use_approved": False,
    "source_pixel_reuse_allowed": False,
    "publication_allowed": False,
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def safe_path(root: Path, relative: str) -> Path:
    require(isinstance(relative, str) and bool(relative), "empty relative path")
    parts = PurePosixPath(relative)
    require(
        not parts.is_absolute()
        and "\\" not in relative
        and ":" not in relative
        and not set(parts.parts) & {".", ".."},
        "unsafe relative path",
    )
    cursor = root
    for component in parts.parts:
        cursor /= component
        require(not cursor.is_symlink(), "symlink path denied")
        if cursor.exists():
            attrs = getattr(cursor.stat(), "st_file_attributes", 0)
            require(
                not attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse path denied"
            )
    require(cursor.resolve().is_relative_to(root.resolve()), "path leaves root")
    return cursor


def fingerprint(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "sha256": sha256(raw),
        "bytes": len(raw),
        "mtime_ns": path.stat().st_mtime_ns,
    }


def bound_read(
    root: Path,
    relative: str,
    digest: str,
    bindings: dict[str, dict[str, Any]],
) -> bytes:
    path = safe_path(root, relative)
    raw = path.read_bytes()
    current = fingerprint(path)
    require(sha256(raw) == digest == current["sha256"], f"binding mismatch: {relative}")
    require(
        relative not in bindings or bindings[relative] == current,
        "source changed during read",
    )
    bindings[relative] = current
    return raw


def encode_png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False, compress_level=9)
    return stream.getvalue()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def prepare(repo_root: Path) -> tuple[dict, dict, dict[str, bytes]]:
    repo_root = repo_root.resolve(strict=True)
    db = safe_path(repo_root, "sh-chem-db").resolve(strict=True)
    qa_path = safe_path(repo_root, QA_RELATIVE.as_posix())
    qa_raw = qa_path.read_bytes()
    require(sha256(qa_raw) == EXPECTED_QA_SHA256, "frozen defect QA changed")
    qa = json.loads(qa_raw)
    require(
        tuple(row["master_atomic_part_id"] for row in qa["records"])
        == EXPECTED_ATOMIC_IDS,
        "unexpected repair scope",
    )
    before: dict[str, dict[str, Any]] = {}
    before["catalog.csv"] = fingerprint(safe_path(db, "catalog.csv"))
    records, png_outputs = [], {}
    for index, item in enumerate(qa["records"], 1):
        require(
            item["source_role"] == "question"
            and item["repair_status"] == "not_repaired",
            "source role or repair baseline changed",
        )
        manifest_raw = bound_read(
            db, item["manifest_asset"], item["manifest_sha256"], before
        )
        manifest = json.loads(manifest_raw)
        old_raw = bound_read(db, item["crop_asset"], item["crop_sha256"], before)
        source_raw = bound_read(db, item["source_asset"], item["source_sha256"], before)
        candidate_raw = bound_read(
            db, manifest["candidate_file"], manifest["candidate_file_sha256"], before
        )
        bound_read(
            db, manifest["evidence_map_file"], manifest["evidence_map_sha256"], before
        )
        crop_binding = next(
            crop
            for crop in manifest["local_crops"]
            if crop["asset"] == item["crop_asset"]
        )
        for source_key, qa_key in (
            ("sha256", "crop_sha256"),
            ("source_asset", "source_asset"),
            ("source_sha256", "source_sha256"),
            ("page_number", "source_page_number"),
            ("crop_box", "crop_box"),
        ):
            require(
                crop_binding[source_key] == item[qa_key],
                "original crop binding drifted",
            )
        candidates = [json.loads(line) for line in candidate_raw.splitlines()]
        candidate = next(
            row
            for row in candidates
            if row["question_id"] == item["internal_question_id"]
        )
        part = next(
            part
            for part in candidate["parts"]
            if part["part_id"] == item["master_atomic_part_id"]
        )
        require(
            part["printed_number"]
            == candidate["source_locator"]["printed_question_number"]
            == item["printed_question_number"],
            "explicit printed number mismatch",
        )
        ref = next(
            ref
            for ref in candidate["source_locator"]["page_refs"]
            if ref["crop_asset"] == item["crop_asset"]
        )
        require(
            ref["role"] == "question"
            and ref["asset"] == item["source_asset"]
            and ref["source_sha256"] == item["source_sha256"]
            and ref["crop_sha256"] == item["crop_sha256"]
            and ref["crop_box"] == item["crop_box"],
            "explicit question/source edge mismatch",
        )
        bbox = item["proposal"]["candidate_crop_box"]
        require(
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(type(value) is int for value in bbox),
            "invalid proposed bbox",
        )
        x, y, width, height = bbox
        with Image.open(io.BytesIO(source_raw)) as source:
            source.load()
            require(
                list(source.size) == item["source_size"], "source dimensions changed"
            )
            require(
                source.mode in {"RGB", "RGBA", "L"},
                "unsupported source mode; do not silently convert",
            )
            require(
                0 <= x < x + width <= source.width
                and 0 <= y < y + height <= source.height,
                "proposed bbox outside source",
            )
            ox, oy, ow, oh = item["crop_box"]
            with Image.open(io.BytesIO(old_raw)) as old:
                old.load()
                original_region = source.crop((ox, oy, ox + ow, oy + oh))
                require(
                    old.mode == original_region.mode
                    and old.size == original_region.size
                    and old.tobytes() == original_region.tobytes(),
                    "old crop does not match explicit original pixels",
                )
            derivative = source.crop((x, y, x + width, y + height))
            raw = encode_png(derivative)
            with Image.open(io.BytesIO(raw)) as decoded:
                decoded.load()
                require(
                    decoded.mode == derivative.mode
                    and decoded.size == derivative.size
                    and decoded.tobytes() == derivative.tobytes(),
                    "lossless PNG roundtrip failed",
                )
            mode = source.mode
        crop_path = f"crops/{item['master_atomic_part_id']}-crop-r1.png"
        png_outputs[crop_path] = raw
        records.append(
            {
                "repair_id": f"ARCHIVED-WECHAT-CROP-REPAIR-0.1.55-R1-{index:03d}",
                "crop_id": f"ARCHIVED-WECHAT-CROP-REPAIR-0.1.55-R1-{index:03d}",
                "defect_id": item["defect_id"],
                "master_node_id": item["master_atomic_part_id"],
                "printed_question_id": item["internal_question_id"],
                "printed_question_number": item["printed_question_number"],
                "printed_theme_title": item["printed_theme_title_from_source_record"],
                "public_location_zh": item["public_location_zh"],
                "role": "question",
                "evidence_role": "question",
                "source_asset": item["source_asset"],
                "source_sha256": item["source_sha256"],
                "source_page_number": item["source_page_number"],
                "source_manifest_asset": item["manifest_asset"],
                "source_manifest_sha256": item["manifest_sha256"],
                "supersedes_crop_asset": item["crop_asset"],
                "supersedes_crop_sha256": item["crop_sha256"],
                "original_crop_box": item["crop_box"],
                "crop_path": crop_path,
                "crop_box": bbox,
                "sha256": sha256(raw),
                "bytes": len(raw),
                "width": width,
                "height": height,
                "image_mode": mode,
                "source_pixels_equal": True,
                "repair_kind": "deterministic_source_rectangle_only",
                "repair_status": "derived_pixels_verified_model_visual_review_separate",
                "human_visual_reviewed": False,
                "authority": dict(AUTHORITY),
            }
        )
    require(len(records) == len(png_outputs) == 5, "repair count mismatch")
    after = {relative: fingerprint(safe_path(db, relative)) for relative in before}
    require(before == after, "source file changed during derivation")
    created = datetime.now(UTC).isoformat()
    manifest = {
        "schema_version": "archived-wechat-crop-repairs-v1",
        "batch_id": "ARCHIVED-WECHAT-CROP-REPAIRS-0.1.55-R1",
        "created_at_utc": created,
        "scope": "local_candidate_derivative_source_crop_repairs_only",
        "status": "derived_pixels_verified_model_visual_review_separate",
        "source_path_basis": "source paths are relative to sh-chem-db; crop_path is relative to this manifest directory",
        "bbox_convention": "[x,y,width,height], original source pixels, right/bottom exclusive",
        "upstream_defect_qa": {
            "path": QA_RELATIVE.as_posix(),
            "sha256": EXPECTED_QA_SHA256,
        },
        "count": len(records),
        "records": records,
        "source_bindings": [
            {
                "path": relative,
                **before[relative],
                "after_sha256": after[relative]["sha256"],
                "after_mtime_ns": after[relative]["mtime_ns"],
                "unchanged": True,
            }
            for relative in sorted(before)
        ],
        "authority": dict(AUTHORITY),
    }
    report = {
        "schema_version": "archived-wechat-crop-repair-machine-validation-v1",
        "created_at_utc": created,
        "status": "PASS_MACHINE_PIXELS_AND_SOURCE_BINDINGS_ONLY",
        "record_count": 5,
        "png_roundtrip_source_pixel_equal_count": 5,
        "source_binding_count": len(before),
        "source_sha_and_mtime_unchanged": before == after,
        "original_files_overwritten": 0,
        "ocr_used": False,
        "generative_model_used": False,
        "retouching_or_text_insertion_used": False,
        "semantic_visual_review": "separate report after actual original-size image viewing",
        "human_review_performed": False,
        "authority": dict(AUTHORITY),
    }
    return manifest, report, png_outputs


def build(repo_root: Path, *, dry_run: bool = False) -> dict:
    repo_root = repo_root.resolve(strict=True)
    output = safe_path(repo_root, OUTPUT_RELATIVE.as_posix())
    require(
        not output.exists(),
        "output directory already exists; never overwrite an existing repair version",
    )
    manifest, report, png_outputs = prepare(repo_root)
    if dry_run:
        return {"dry_run": True, "count": len(png_outputs), "validation": report}
    output.mkdir(parents=True, exist_ok=False)
    (output / "crops").mkdir(exist_ok=False)
    for relative, raw in png_outputs.items():
        with safe_path(output, relative).open("xb") as stream:
            stream.write(raw)
    db = safe_path(repo_root, "sh-chem-db")
    for binding in manifest["source_bindings"]:
        current = fingerprint(safe_path(db, binding["path"]))
        require(
            current["sha256"] == binding["sha256"]
            and current["mtime_ns"] == binding["mtime_ns"],
            "source changed after output write; do not publish partial output",
        )
    for filename, value in (
        ("crop_repair_manifest.json", manifest),
        ("validation_report.json", report),
    ):
        with (output / filename).open("xb") as stream:
            stream.write(json_bytes(value))
    return {
        "output": OUTPUT_RELATIVE.as_posix(),
        "count": len(png_outputs),
        "source_files_unchanged": len(manifest["source_bindings"]),
        "manifest_sha256": sha256((output / "crop_repair_manifest.json").read_bytes()),
        "human_review_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[4]
    )
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            build(arguments.repo_root, dry_run=arguments.dry_run), ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
