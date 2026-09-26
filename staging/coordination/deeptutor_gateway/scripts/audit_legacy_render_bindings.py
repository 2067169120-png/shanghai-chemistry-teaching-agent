"""Read-only source audit for three legacy native handout render bindings."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PRODUCT = ROOT / "sh-chem-db/kb/teaching_handout_question_slices_v1_2026-08-28"
NATIVE = PRODUCT / "native_ooxml_v2"
TARGETS = (
    "NATIVE-BATCH-001-PKG038-039",
    "NATIVE-BATCH-002-PKG050-051",
    "NATIVE-BATCH-NV2W2-PKG096-A01",
)


def pipeline():
    spec = importlib.util.spec_from_file_location(
        "_legacy_binding_audit", NATIVE / "scripts/native_fastlane_pipeline.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def audit():
    module = pipeline()
    # validate_batch normally writes its report; this audit explicitly disables it.
    module.write_json = lambda *_args, **_kwargs: None
    _, inventory = module.load_input_documents()
    result = []
    for batch_id in TARGETS:
        root = NATIVE / "batches" / batch_id
        summary = module.load_json(root / "batch_summary.json")
        candidates = module.load_jsonl(root / "question_candidates.jsonl")
        batch = {
            "batch_id": batch_id,
            "before": module.validate_batch(batch_id),
            "packages": [],
        }
        for package_id in summary["package_ids"]:
            rows = [c for c in candidates if c["package_id"] == package_id]
            anchors = [
                a
                for c in rows
                for group in (
                    c["page_binding"],
                    c["answer_alignment"]["solution_page_binding"],
                )
                for a in group["pages"]
            ]
            ids = {Path(a["path"]).parts[1] for a in anchors}
            if not anchors:
                ids = {
                    b["render_batch_id"]
                    for b in summary.get("render_bindings", [])
                    if b["package_id"] == package_id
                }
            assert len(ids) == 1, (package_id, ids)
            render_id = ids.pop()
            expected = {
                d["document_role"]: d
                for d in inventory
                if d["package_id"] == package_id
            }
            item = {
                "package_id": package_id,
                "render_batch_id": render_id,
                "candidate_count": len(rows),
                "anchor_occurrences": len(anchors),
            }
            try:
                render = module._validate_render_batch(package_id, render_id, expected)
                for role in ("question", "solution"):
                    document = module.load_json(
                        root / "documents" / f"{package_id}-{role}.json"
                    )
                    assert document["source"]["sha256"] == expected[role]["sha256"]
                    metadata = next(
                        d
                        for d in summary["documents"]
                        if d["package_id"] == package_id and d["document_role"] == role
                    )
                    assert metadata["source_sha256"] == expected[role]["sha256"]
                    assert (
                        metadata["render_pages"]
                        == render["documents"][role]["render"]["page_count"]
                    )
                    assert metadata["word_layout_pages"] == metadata["render_pages"]
                for anchor in anchors:
                    original = next(
                        a
                        for a in render["documents"][anchor["document_role"]]["pages"]
                        if a["page"] == anchor["page"]
                    )
                    for key in ("path", "sha256", "width", "height"):
                        assert anchor[key] == original[key], (
                            package_id,
                            anchor["page"],
                            key,
                        )
                binding = {
                    "package_id": package_id,
                    "render_batch_id": render_id,
                    "render_manifest_path": f"batches/{render_id}/render_manifest.json",
                    "render_manifest_sha256": module.sha256_file(
                        PRODUCT / "batches" / render_id / "render_manifest.json"
                    ),
                    "render_output_manifest_sha256": module.sha256_file(
                        PRODUCT / "batches" / render_id / "output_manifest.json"
                    ),
                }
                item.update(
                    valid=True,
                    binding=binding,
                    source_hashes={role: d["sha256"] for role, d in expected.items()},
                )
            except (module.FastlaneError, AssertionError) as exc:
                item.update(valid=False, error=str(exc))
            batch["packages"].append(item)
        result.append(batch)
    return {"read_only": True, "batches": result}


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
