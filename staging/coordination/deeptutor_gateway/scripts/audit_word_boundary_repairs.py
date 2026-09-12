"""Reindex frozen Word sources without accessing application state or providers.

Only a fresh, explicitly selected private QA directory is written. The public
script contains source identities and block coordinates, never question text.
PNG/JPEG evidence copies retain their original bytes; metafiles are not rendered.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from docx import Document
from PIL import Image

from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    _block_text,
    _body_blocks,
    _word_images,
)
from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import (
    CACHE_REVISION,
    WordPreviewCache,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
    BOUNDARY_REVIEW_REVISION,
    QUESTION_INDEX_REVISION,
    apply_question_range,
    index_word_questions,
)
from integrations.deeptutor_shchem_v1.word_handout_import import _validate_container
from integrations.deeptutor_shchem_v1.word_native_text import WordNativeTextReader

BASELINE_REVISION = "20260910-word-answer-boundary-index-v4"
RANGE_FIELDS = ("origin_block_start", "block_start", "question_end", "answer_start", "block_end")
COMPARE_FIELDS = (*RANGE_FIELDS, "export_ready")
REVIEWS = {
    "PKG-040": (265, 266, 267, 268, "unmarked_answer"),
    "PKG-069": (293, 295, 296, 297, "self_contained_reference"),
    "PKG-092": (79, 84, 85, 87, "nonstandard_label"),
}
NATIVE_RANGES = {"PKG-022": (314, 354), "PKG-040": (263, 270),
                 "PKG-069": (291, 299), "PKG-092": (77, 89)}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def regular_file(path, root):
    require(not path.is_symlink() and not path.is_junction(), "Linked audit input is not allowed")
    resolved = path.resolve(strict=True)
    require(resolved.is_relative_to(root.resolve(strict=True)) and resolved.is_file(),
            "Audit input escapes its selected root")
    return resolved


def projection(item):
    return {name: item.get(name) for name in ("key", *COMPARE_FIELDS)}


def source_fidelity(preview, items):
    original = {block["index"]: block for block in preview["blocks"]}
    assets = defaultdict(list)
    for asset in preview["assets"]:
        assets[asset["block_index"]].append(asset)
    checked = 0
    for item in items:
        for role in ("question_blocks", "answer_blocks", "context_blocks"):
            for block in item[role]:
                source = original[block["index"]]
                require(block["assets"] == assets[block["index"]], "Source block asset binding changed")
                expected = dict(source)
                if block.get("display_only_split"):
                    start, end = block["text_range"]
                    require(block["source_text"] == source["text"] and
                            block["text"] == source["text"][start:end], "Source text slice changed")
                    expected.update(text=source["text"][start:end], source_text=source["text"],
                                    text_range=[start, end], display_only_split=True)
                actual = {key: value for key, value in block.items() if key != "assets"}
                require(actual == expected, "Original block metadata or text changed")
                checked += 1
    return checked


def ownership(items, assets):
    """Compare question/answer ownership; old report omitted context coordinates."""
    result = defaultdict(list)
    for item in items:
        for asset in assets:
            index = asset["block_index"]
            role = None
            if item["block_start"] <= index <= item["question_end"]:
                role = "question"
            if item["answer_start"] is not None and item["answer_start"] <= index <= item["block_end"]:
                role = "answer" if role is None else "question_and_answer_display_slice"
            if role:
                result[asset["asset_id"]].append({"question_key": item["key"], "role": role})
    return {key: sorted(value, key=lambda row: (row["question_key"], row["role"]))
            for key, value in result.items()}


def native_evidence(data, preview, package_id, selected_items):
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        _validate_container(package)
    document = Document(io.BytesIO(data))
    elements = list(_body_blocks(document._element.body))
    reader = WordNativeTextReader(document.styles.element)
    originals = {block["index"]: block for block in preview["blocks"]}
    start, end = NATIVE_RANGES[package_id]
    for index in range(start, end + 1):
        require(_block_text(elements[index - 1], document, set(), reader) == originals[index]["text"],
                f"Native source projection differs: {package_id} block {index}")
    selected_assets = {}
    for item in selected_items:
        for role in ("question_blocks", "answer_blocks", "context_blocks"):
            for block in item[role]:
                for asset in block["assets"]:
                    selected_assets.setdefault(asset["asset_id"], {**asset, "owners": []})["owners"].append(
                        {"question_key": item["key"], "role": role.removesuffix("_blocks")})
    actual = {asset["asset_id"]: asset for asset in _word_images(elements, document, include_bytes=True)}
    records, copies = [], {}
    for asset_id, expected in selected_assets.items():
        source = actual[asset_id]
        require({key: value for key, value in source.items() if key != "bytes"} ==
                {key: value for key, value in expected.items() if key != "owners"},
                "Native image metadata differs from frozen cache")
        raw = source["bytes"]
        require(hashlib.sha256(raw).hexdigest() == expected["sha256"], "Native image bytes changed")
        record = {key: expected[key] for key in
                  ("asset_id", "block_index", "sha256", "mime_type", "bytes_count", "preview_supported", "owners")}
        record.update(package_id=package_id, source_sha256=preview["source_sha256"],
                      source_revision=preview["revision"], original_bytes_verified=True)
        if source["mime_type"] in {"image/png", "image/jpeg"}:
            with Image.open(io.BytesIO(raw)) as image:
                require(image.width * image.height <= 50_000_000, "Image exceeds decode safety limit")
                require(image.format in {"PNG", "JPEG"}, "Image decoder format differs")
                record["pixel_size"] = [image.width, image.height]
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
            extension = ".png" if source["mime_type"] == "image/png" else ".jpg"
            name = preview["source_sha256"] + "--" + asset_id + extension
            copies[name] = raw
            record.update(export_relative_path="images/" + name, export_status="original_bytes_copy")
        else:
            record.update(export_relative_path=None, export_status="not_exported_not_converted")
        records.append(record)
    return {"range": [start, end], "verified_block_count": end - start + 1}, records, copies


def audit(inventory, cache_root, archive_root, baseline_path):
    inventory_hash = file_hash(inventory)
    baseline_hash = file_hash(baseline_path)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    require(baseline["index_revision"] == BASELINE_REVISION and baseline["source_count"] == 98,
            "Expected the frozen 98-source boundary report")
    old_sources = {row["package_id"]: row for row in baseline["sources"]}
    with inventory.open(encoding="utf-8-sig", newline="") as stream:
        rows = sorted((row for row in csv.DictReader(stream) if row["document_role"] == "解析版"),
                      key=lambda row: row["package_id"])
    require(len(rows) == 98 and len({row["sha256"] for row in rows}) == 98 and
            {row["package_id"] for row in rows} == set(old_sources), "Unexpected source inventory")
    cache = WordPreviewCache(cache_root)
    counts, differences = Counter(), {"added": [], "removed": [], "changed": []}
    sources, reviews, ownership_changes, image_records, copies, preserved = [], [], [], [], {}, []
    label_impact = []
    for order, row in enumerate(rows, 1):
        package_id = row["package_id"]
        old_source = old_sources[package_id]
        source_path = regular_file(inventory.parent / row["output_relative_path"], inventory.parent)
        archived = regular_file(archive_root / (row["sha256"] + ".docx"), archive_root)
        data = source_path.read_bytes()
        require(len(data) == int(row["bytes"]) and hashlib.sha256(data).hexdigest() == row["sha256"] and
                file_hash(archived) == row["sha256"] == old_source["source_sha256"],
                "Source SHA mismatch: " + package_id)
        cache_path = regular_file(cache._path(row["sha256"], source_path.name), cache_root)
        cache_hash = file_hash(cache_path)
        preview = cache.load(data, source_path.name)
        require(preview is not None and preview["revision"] == old_source["preview_revision"],
                "Frozen native preview unavailable or changed: " + package_id)
        require(preview["source_name"] == old_source["source_name"], "Frozen source name changed")
        before = digest(preview)
        items = index_word_questions(preview)
        counts["verified_indexed_block_occurrences"] += source_fidelity(preview, items)
        current = {item["key"]: item for item in items}
        old = {item["key"]: item for item in old_source["items"]}
        require(len(current) == len(items) and len(old) == len(old_source["items"]), "Duplicate question key")
        for key in sorted(current.keys() - old.keys()):
            differences["added"].append({"package_id": package_id, **projection(current[key])})
        for key in sorted(old.keys() - current.keys()):
            differences["removed"].append({"package_id": package_id, **projection(old[key])})
            label_impact.append({"package_id": package_id, "question_key": key, "reason": "removed_question_key"})
        for key in sorted(old.keys() & current.keys()):
            changed = [name for name in COMPARE_FIELDS if old[key].get(name) != current[key].get(name)]
            if changed:
                differences["changed"].append({"package_id": package_id, "key": key, "changed_fields": changed,
                                                "before": projection(old[key]), "after": projection(current[key])})
                label_impact.append({"package_id": package_id, "question_key": key, "reason": "automatic_boundary_or_status_changed"})
        old_owners = ownership(old_source["items"], preview["assets"])
        new_owners = ownership(items, preview["assets"])
        for asset in preview["assets"]:
            asset_id = asset["asset_id"]
            if old_owners.get(asset_id, []) != new_owners.get(asset_id, []):
                ownership_changes.append({"package_id": package_id, "asset_id": asset_id,
                                          "block_index": asset["block_index"], "sha256": asset["sha256"],
                                          "before": old_owners.get(asset_id, []), "after": new_owners.get(asset_id, [])})
        selected = []
        if package_id == "PKG-022":
            selected = [item for item in items if item["origin_block_start"] in {316, 340}]
            require([(item["block_start"], item["question_end"], item["answer_start"], item["block_end"])
                     for item in selected] == [(316, 328, 329, 338), (340, 346, 347, 352)], "Split boundaries differ")
            require(all(item["export_ready"] and not item["context_blocks"] for item in selected), "Split still held")
            require(not preview["blocks"][338]["text"].strip(), "Excluded separator is not empty")
            expected_assets = {316: {317, 333, 338}, 340: {340, 341}}
            for item in selected:
                require({asset["block_index"] for role in ("question_blocks", "answer_blocks")
                         for block in item[role] for asset in block["assets"]} == expected_assets[item["origin_block_start"]],
                        "Split image ownership differs")
        if package_id in REVIEWS:
            start, qend, answer, end, issue = REVIEWS[package_id]
            original = next(item for item in items if item["origin_block_start"] == start)
            require(not original["export_ready"], "Expected an explicit review hold")
            boundaries = {"block_start": start, "question_end": qend, "answer_start": answer, "block_end": end}
            original_digest = digest(original)
            unconfirmed = apply_question_range(preview, original, **boundaries)
            require(not unconfirmed["export_ready"], "Range alone silently clears an explicit hold")
            reviewed = apply_question_range(preview, original, **boundaries, reviewed_issues=[issue])
            require(reviewed["export_ready"] and reviewed["key"] == original["key"] and
                    reviewed["revision"] != original["revision"] and not reviewed["context_blocks"],
                    "Explicit in-memory review failed")
            for role in ("question_blocks", "answer_blocks", "context_blocks"):
                require(reviewed[role] == original[role], "Review changed source text or assets")
            require(digest(original) == original_digest, "Review mutated the input candidate")
            source_fidelity(preview, [reviewed])
            reviews.append({"package_id": package_id, **projection(reviewed),
                            "reviewed_issues": [issue], "source_revision": preview["revision"],
                            "before_question_revision": original["revision"], "after_question_revision": reviewed["revision"],
                            "source_blocks_and_assets_unchanged": True, "unconfirmed_range_still_held": True,
                            "personal_state_persisted": False})
            label_impact.append({"package_id": package_id, "question_key": reviewed["key"],
                                 "reason": "question_revision_changes_only_if_explicit_review_is_persisted"})
            selected = [reviewed]
        native = None
        if selected:
            native, assets, exports = native_evidence(data, preview, package_id, selected)
            image_records.extend(assets)
            copies.update(exports)
        require(digest(preview) == before, "Indexing or review mutated the native preview")
        source_counts = {"questions": len(items), "export_ready": sum(item["export_ready"] for item in items),
                         "without_answers": sum(not item["answer_blocks"] for item in items),
                         "source_blocks": len(preview["blocks"]), "source_asset_refs": len(preview["assets"])}
        counts.update(source_counts)
        sources.append({"package_id": package_id, "source_name": source_path.name,
                        "source_id_from_verified_import_order": f"SRC-HANDOUT-{order:03d}-{row['sha256'][:16]}",
                        "source_sha256": row["sha256"], "preview_revision": preview["revision"],
                        "source_path": str(source_path), "archive_path": str(archived), "cache_path": str(cache_path),
                        "cache_sha256": cache_hash, "original_blocks_sha256": digest(preview["blocks"]),
                        "original_assets_sha256": digest(preview["assets"]), "counts": source_counts,
                        "native_projection_check": native, "items": [projection(item) for item in items]})
        preserved.extend(((source_path, row["sha256"]), (archived, row["sha256"]), (cache_path, cache_hash)))
    require(len(reviews) == 3, "Expected exactly three in-memory reviews")
    require([(item["package_id"], item["origin_block_start"]) for item in differences["added"]] ==
            [("PKG-022", 340)] and not differences["removed"] and
            [(item["package_id"], item["before"]["origin_block_start"], item["changed_fields"])
             for item in differences["changed"]] == [("PKG-022", 316, ["block_end", "export_ready"])],
            "Unexpected changes outside the scoped split repair")
    require([(item["package_id"], item["block_index"]) for item in ownership_changes] ==
            [("PKG-022", 340), ("PKG-022", 341)], "Unexpected image ownership changes")
    require(counts["questions"] == baseline["counts"]["questions"] + 1 and
            counts["without_answers"] == 0 and counts["questions"] - counts["export_ready"] == 3,
            "Unexpected final boundary denominator")
    require(file_hash(inventory) == inventory_hash, "Source inventory changed during audit")
    require(file_hash(baseline_path) == baseline_hash, "Old frozen report changed during audit")
    for path, sha in preserved:
        require(file_hash(path) == sha, "Source or cache file changed during audit")
    return {
        "schema_version": "word-boundary-repairs-audit-v1", "index_revision": QUESTION_INDEX_REVISION,
        "boundary_review_revision": BOUNDARY_REVIEW_REVISION, "cache_revision": CACHE_REVISION,
        "script_sha256": file_hash(Path(__file__)),
        "index_source_sha256": file_hash(ROOT / "integrations/deeptutor_shchem_v1/desktop_word_question_index.py"),
        "inventory_sha256": inventory_hash,
        "baseline_path": str(baseline_path.resolve()), "baseline_sha256": baseline_hash,
        "baseline_counts": baseline["counts"], "automatic_counts": dict(counts),
        "simulated_explicit_review_counts": {"review_count": len(reviews), "export_ready": counts["export_ready"] + len(reviews)},
        "source_count": len(sources), "source_archive_and_cache_hashes_unchanged": True,
        "all_current_blocks_and_asset_refs_match_frozen_preview": True,
        "old_to_new_comparison_fields": list(COMPARE_FIELDS),
        "difference_counts": {kind: len(values) for kind, values in differences.items()},
        "expected_scoped_delta_only": True,
        "differences": differences, "question_answer_asset_ownership_changes": ownership_changes,
        "asset_ownership_comparison_scope": "question_and_answer_ranges_only_old_report_omits_context_ranges",
        "source_block_image_binding_unchanged": True, "in_memory_reviews": reviews,
        "label_impact": {"potentially_stale_existing_keys": label_impact,
                         "new_keys_without_inherited_labels": [item["key"] for item in differences["added"]],
                         "actual_attribute_store_read": False,
                         "limitation": "The old report omits full question revisions; revision-only drift and actual saved labels were not audited."},
        "selected_question_assets": image_records, "exported_original_image_count": len(copies),
        "provider_calls": 0, "personal_state_reads": 0, "personal_state_writes": 0,
        "attribute_store_reads": 0, "attribute_store_writes": 0, "source_writes": 0, "cache_writes": 0,
        "chemical_answer_review": False, "rendered_page_review": False, "teaching_approval": False,
        "review_persistence": "simulation_only_no_application_state_changes", "sources": sources,
    }, copies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    allowed = ROOT / "runtime/deeptutor_shchem/qa"
    require(output.is_relative_to(allowed.resolve()) and output != allowed.resolve(), "Report must remain in private runtime QA")
    require(not output.exists(), "Use a fresh output directory; prior reports are never overwritten")
    result, copies = audit(args.inventory, args.cache_root, args.archive_root, args.baseline)
    output.mkdir(parents=True, exist_ok=False)
    (output / "images").mkdir()
    for name, raw in copies.items():
        with (output / "images" / name).open("xb") as stream:
            stream.write(raw)
    with (output / "audit.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("source_count", "automatic_counts", "difference_counts",
                     "simulated_explicit_review_counts", "exported_original_image_count", "personal_state_writes")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
