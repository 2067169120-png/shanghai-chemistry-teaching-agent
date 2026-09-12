"""Mechanically merge reviewed, source-bound note additions into lecture indexes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from validate_lecture_knowledge import validate

from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import WordPreviewCache


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--extensions", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bind-source-revisions", action="store_true")
    args = parser.parse_args()
    validate(args.state_root)
    extension = json.loads(args.extensions.read_text(encoding="utf-8"))
    assert extension["schema_version"] == "shchem.lecture-source-extension.v1"
    assert extension["human_reviewed"] is False
    inventory = (
        ROOT / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/archive_inventory.csv"
    )
    with inventory.open(encoding="utf-8-sig") as stream:
        sources = {
            r["package_id"]: r
            for r in csv.DictReader(stream)
            if r["document_role"] == "解析版"
        }
    directory = json.loads(
        (
            ROOT
            / "sh-chem-db/kb/classification/supplemental_wechat_textbook_tagging_v1_2026-08-27/textbook_directory_nodes.json"
        ).read_text(encoding="utf-8")
    )
    nodes = {n["node_key"]: n for n in directory["nodes"]}
    cache = WordPreviewCache(args.state_root / "word-question-previews")
    files = {}
    for name in ("part-a.jsonl", "part-b.jsonl", "part-c.jsonl"):
        path = ROOT / "knowledge/lectures" / name
        files[path] = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    additions = 0
    changed = set()
    bound = 0
    if args.bind_source_revisions:
        # Source hashes and all claim locators were checked by validate above.
        # Freeze the current extraction identity, never silently replace an old
        # identity after the parser or source changes.
        for target, rows in files.items():
            for row in rows:
                source = sources[row["package_id"]]
                raw = (inventory.parent / source["output_relative_path"]).read_bytes()
                preview = cache.load(raw, row["source_name"])
                if preview is None:
                    raise ValueError("Missing source preview for revision binding")
                previous = row.get("source_preview_revision")
                if previous not in (None, preview["revision"]):
                    raise ValueError("Source block revision changed; review before rebinding")
                if previous is None:
                    row["source_preview_revision"] = preview["revision"]
                    bound += 1
                    changed.add(target)
    seen = set()
    for entry in extension["entries"]:
        package = entry["package_id"]
        assert package not in seen
        seen.add(package)
        matches = [
            (path, row)
            for path, rows in files.items()
            for row in rows
            if row["package_id"] == package
        ]
        assert len(matches) == 1
        target, row = matches[0]
        source = sources[package]
        raw = (inventory.parent / source["output_relative_path"]).read_bytes()
        assert (
            hashlib.sha256(raw).hexdigest()
            == source["sha256"]
            == row["source_sha256"]
            == entry["source_sha256"]
        )
        preview = cache.load(raw, row["source_name"])
        assert preview is not None
        positions = {b["index"] for b in preview["blocks"]}
        for group in ("methods", "pitfalls"):
            existing = {c["summary"]: c for c in row[group]}
            for claim in entry[group]:
                assert set(claim) == {"summary", "block_indices"}
                assert (
                    isinstance(claim["summary"], str)
                    and 0 < len(claim["summary"]) <= 300
                )
                assert claim["block_indices"] and all(
                    type(i) is int and i in positions for i in claim["block_indices"]
                )
                if claim["summary"] in existing:
                    assert claim == existing[claim["summary"]]
                else:
                    row[group].append(claim)
                    existing[claim["summary"]] = claim
                    additions += 1
                    changed.add(target)
        for link in entry["textbook_links"]:
            node = nodes[link["section_key"]]
            assert node["volume_id"] == link["volume_id"]
            assert link["human_reviewed"] is False
            pdf = ROOT / node["source_path"]
            assert (
                hashlib.sha256(pdf.read_bytes()).hexdigest()
                == node["source_sha256"]
                == link["source_sha256"]
            )
            assert all(
                node["content_pdf_pages"][0] <= p <= node["content_pdf_pages"][1]
                for p in link["pdf_pages"]
            )
            assert len(link["pdf_pages"]) == len(link["printed_pages"])
            if "lecture_block_indices" in link:
                assert link["lecture_block_indices"] and all(
                    type(i) is int and i in positions for i in link["lecture_block_indices"]
                )
        if row.get("textbook_links") != entry["textbook_links"]:
            row["textbook_links"] = entry["textbook_links"]
            changed.add(target)
    if args.apply:
        # Bulk mechanical JSONL rewrite only after all origins and additions
        # have been checked. No original Word, PDF or cache is written.
        for path in sorted(changed):
            path.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                    for row in files[path]
                )
                + "\n",
                encoding="utf-8",
            )
        coverage = validate(args.state_root)
        (ROOT / "knowledge/lectures/coverage.json").write_text(
            json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "preview",
                "lecture_sources": len(seen),
                "new_note_count": additions,
                "source_revisions_bound": bound,
                "textbook_pages_referenced": sum(
                    len(r["pdf_pages"])
                    for e in extension["entries"]
                    for r in e["textbook_links"]
                ),
                "originals_written": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
