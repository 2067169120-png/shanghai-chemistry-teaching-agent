"""Private native export QA for two complete archived WeChat themes.

Readers run in the application environment. Only the bundled artifact Python
authors DOCX and invokes the normal Word COM/canonical page-rendering chain.
No provider, real personal state, archive edit, or approval promotion is used.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1 import paper_export_workbench
from integrations.deeptutor_shchem_v1 import (
    shanghai_high_east2025_theme45_direct_visual_scan as east,
)
from integrations.deeptutor_shchem_v1 import (
    songjiang2025_theme2_direct_visual_scan as songjiang,
)
from integrations.deeptutor_shchem_v1.archived_wechat_crop_revision import (
    MARGIN_RECIPES,
    SHARED_RECIPES,
)
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopWorkbenchFacade,
    _canonical_digest,
)
from integrations.deeptutor_shchem_v1.desktop_library_session import (
    snapshot_reader_graph,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
    PaperComposerModel,
)

BUNDLED_PYTHON = Path(
    "C:/Users/20671/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
)
OUTPUT_RELATIVE = "runtime/deeptutor_shchem/qa_0.1.56_repairs_export_20260910_r1"
REPAIRS_RELATIVE = (
    "runtime/deeptutor_shchem/crop_repairs_0.1.55_r1/crop_repair_manifest.json"
)
THEMES = (
    (songjiang.PAPER_ID, songjiang.THEME_ID, 9),
    (east.PAPER_ID, "THEME-aa5b660ca407ced6230f", 5),
)
EXPECTED_SHARED = {
    songjiang.THEME_ID: {
        "SJ25T2-C-783aedf98189899baf2c2909",
        "SJ25T2-C-377ff6ffad094904d6cf5ea4",
        "SJ25T2-C-bc033f159e7aba3c44d5af29",
    },
    "THEME-aa5b660ca407ced6230f": {"SHEAST2025-CROP-b39d0d33cdac04884a42c934"},
}
ARCHIVE_ONLY_SHARED = {
    "SJ25T2-C-cad3a95bc7739857cfe7515e",
    "SJ25T2-C-c4702d34468809ed194d0065",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require(value, message: str) -> None:
    if not value:
        raise AssertionError(message)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def file_state(path: Path) -> dict:
    return {
        "sha256": sha(path.read_bytes()),
        "bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
    }


def read_source_parts(db: Path, module) -> dict:
    manifest = json.loads(
        (db / module.PRODUCT_RELATIVE / "candidate_manifest.json").read_text(
            encoding="utf-8-sig"
        )
    )
    source = (db / manifest["candidate_file"]).resolve()
    require(source.is_relative_to(db), "candidate path outside source root")
    return {
        part["part_id"]: part
        for line in source.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
        for part in json.loads(line)["parts"]
    }


def block_hashes(blocks) -> list[str]:
    return [Path(block["asset_ref"]).stem for block in blocks if block.get("asset_ref")]


def verify_source_pixels(db: Path, raw: bytes, recipe) -> None:
    source = (db / recipe.source_asset).read_bytes()
    require(sha(source) == recipe.source_sha256, "revised source page changed")
    with Image.open(io.BytesIO(source)) as page, Image.open(io.BytesIO(raw)) as actual:
        x, y, width, height = recipe.box
        pixels = page.crop((x, y, x + width, y + height))
        require(
            actual.size == pixels.size
            and actual.mode == pixels.mode
            and actual.tobytes() == pixels.tobytes(),
            "revised image is not exact source pixels",
        )


def verify_bundle(bundle, assets: Path, expected, repairs) -> dict:
    require(bundle["publication_allowed"] is False, "export authority changed")
    require(
        bundle["preset"]["student_version"]["show_item_scores"] is False,
        "student question scores must stay hidden",
    )
    new_hashes = {row["sha256"] for row in repairs} | set(
        expected["additional_repair_hashes"]
    )
    old_hashes = {row["supersedes_crop_sha256"] for row in repairs} | set(
        expected["excluded_shared_hashes"]
    )
    asset_files = {path.stem: path for path in (assets / "images").glob("*.png")}
    require(
        new_hashes <= set(asset_files),
        "seven question and two shared repaired images missing from export assets",
    )
    require(
        not old_hashes & set(asset_files),
        "superseded or archive-only image entered assets",
    )
    for digest, path in asset_files.items():
        require(
            sha(path.read_bytes()) == digest,
            "export asset hash does not match filename",
        )
    rows_by_audience = {}
    for audience in ("student", "teacher"):
        plan = bundle[f"{audience}_plan"]
        sections = plan["visible"]["theme_sections"]
        bindings = plan["bindings"]
        require(
            [row["theme_id"] for row in bindings["theme_bindings"]]
            == [row[1] for row in THEMES],
            "theme order or identity changed",
        )
        atomic_bindings = bindings["atomic_part_bindings"]
        atoms = [
            atom
            for section in sections
            for printed in section["printed_questions"]
            for atom in printed["atomic_parts"]
        ]
        require(len(atoms) == len(atomic_bindings) == 14, "export lost atomic units")
        rows_by_audience[audience] = {}
        for binding, atom in zip(atomic_bindings, atoms, strict=True):
            node = binding["atomic_part_id"]
            require(node in expected["parts"], "unexpected atomic source in plan")
            source = expected["parts"][node]
            require(
                binding["theme_id"] == source["theme_id"],
                "atomic assigned to wrong theme",
            )
            require(
                atom["score"] == 2 and atom["answer_space"]["lines"] == 0,
                "editable sample score or no-extra-lines setting lost",
            )
            hashes = block_hashes(atom["question_blocks"])
            require(
                hashes == source["question_hashes"],
                "question image crossed atomic source",
            )
            if audience == "teacher":
                answer = atom["teacher_notes"]["source_reference_answer"]
                require(
                    answer["text_zh"] == source["answer"].strip(),
                    "teacher answer crossed source",
                )
                require(
                    answer["independently_verified"] is False,
                    "answer approval elevated",
                )
            else:
                require(
                    "teacher_notes" not in atom, "teacher answer entered student plan"
                )
            rows_by_audience[audience][node] = hashes
        for binding, section in zip(bindings["theme_bindings"], sections, strict=True):
            theme = binding["theme_id"]
            actual_shared = {
                material["source_crop_id"]: material["source_sha256"]
                for material in section["shared_materials"]
            }
            require(
                actual_shared == expected["shared"][theme],
                "shared materials lost or crossed theme",
            )
    require(
        rows_by_audience["student"] == rows_by_audience["teacher"],
        "teacher and student question images differ",
    )
    return {
        "atomic_count": 14,
        "theme_count": 2,
        "sample_points_per_atomic": 2,
        "answer_space_lines": 0,
        "student_question_scores_visible": False,
        "question_image_sha_by_atomic": rows_by_audience["student"],
        "shared_image_sha_by_theme": expected["shared"],
        "asset_count": len(asset_files),
        "new_repair_hashes": sorted(new_hashes),
        "old_repair_hashes_excluded": sorted(old_hashes),
        "answers_match_source": True,
        "student_answers_excluded": True,
    }


def renderer_bridge(report, report_path, expected, repairs):
    def render(bundle, *, output_dir, toolchain, asset_root):
        require(
            toolchain.python_exe.resolve() == BUNDLED_PYTHON.resolve(),
            "DOCX authoring must use the dependency-loader bundled Python",
        )
        require(
            toolchain.conversion_backend == "word_com",
            "only Word COM conversion permitted",
        )
        bundle_path = output_dir.parent / "render_bundle.json"
        require(
            json.loads(bundle_path.read_text(encoding="utf-8")) == bundle,
            "serialized renderer bundle changed",
        )
        report["bundle_checks"] = verify_bundle(bundle, asset_root, expected, repairs)
        report["render_bundle_path"] = str(bundle_path)
        report["asset_root"] = str(asset_root)
        report["stage"] = "bundle_verified_starting_bundled_renderer"
        write_json(report_path, report)
        print(
            json.dumps({"stage": report["stage"], "bundle": str(bundle_path)}),
            flush=True,
        )
        command = [
            str(BUNDLED_PYTHON),
            "-B",
            "-X",
            "utf8",
            "-m",
            "integrations.deeptutor_shchem_v1.paper_export_renderer",
            "render",
            "--bundle",
            str(bundle_path),
            "--output-dir",
            str(output_dir),
            "--asset-root",
            str(asset_root),
            "--python-exe",
            str(BUNDLED_PYTHON),
            "--render-docx-script",
            str(toolchain.render_docx_script),
            "--pdftoppm-exe",
            str(toolchain.pdftoppm_exe),
            "--conversion-backend",
            "word_com",
            "--dpi",
            "300",
        ]
        require(
            not any("soffice" in word.casefold() for word in command),
            "LibreOffice not permitted",
        )
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log = output_dir.parent / "bundled_renderer_process.json"
        write_json(
            log,
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
        require(
            completed.returncode == 0, f"bundled renderer failed; private log: {log}"
        )
        return json.loads(
            (output_dir / "render_qa_report.json").read_text(encoding="utf-8")
        )

    return render


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / OUTPUT_RELATIVE)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    require(
        output == (ROOT / OUTPUT_RELATIVE).resolve(),
        "QA writes restricted to assigned output",
    )
    output.mkdir(parents=True, exist_ok=True)
    attempt_number = 1
    while (output / f"attempt-{attempt_number:02d}").exists():
        attempt_number += 1
    attempt = output / f"attempt-{attempt_number:02d}"
    attempt.mkdir()
    state_root = attempt / "isolated-state"
    report_path = attempt / "verification.json"
    started = time.monotonic()
    report = {
        "schema_version": "private-archived-wechat-repairs-export-qa-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": "initializing",
        "status": "running",
        "model_calls": 0,
        "real_personal_state_accessed": False,
        "source_writes": 0,
        "visual_review_complete": False,
        "chemistry_approved": False,
        "human_reviewed": False,
        "publication_allowed": False,
        "conversion_backend": "word_com",
        "libreoffice_used": False,
        "state_root": str(state_root),
        "themes": [],
    }
    db = (ROOT / "sh-chem-db").resolve()
    repair_manifest_path = ROOT / REPAIRS_RELATIVE
    repair_manifest = json.loads(repair_manifest_path.read_text(encoding="utf-8"))
    repairs = repair_manifest["records"]
    require(len(repairs) == 5, "expected exactly five source crop repairs")
    by_repaired_node = {row["master_node_id"]: row for row in repairs}
    sources_before = {}
    facade = None
    original_renderer = paper_export_workbench.render_export_bundle
    try:
        paths = DesktopPaths.from_workspace(ROOT, state_root=state_root)
        facade = DesktopWorkbenchFacade(
            paths, provider_store=SimpleNamespace(list_metadata=list)
        )
        sj, sh = snapshot_reader_graph(
            (
                songjiang.Songjiang2025Theme2DirectVisualScanReader(db),
                east.ShanghaiHighEast2025Theme45DirectVisualScanReader(db),
            )
        )
        snapshots = (sj._snapshot(), sh._snapshot())
        originals = {**read_source_parts(db, songjiang), **read_source_parts(db, east)}
        source_paths = {repair_manifest_path, db / "catalog.csv"}
        for module, snapshot in zip((songjiang, east), snapshots, strict=True):
            source_paths.add(db / module.PRODUCT_RELATIVE / "candidate_manifest.json")
            for relative in snapshot.output_bindings:
                path = (db / relative).resolve()
                require(path.is_relative_to(db), "source binding escapes source root")
                source_paths.add(path)
        for row in repairs:
            source_paths.update(
                (
                    db / row["source_asset"],
                    db / row["supersedes_crop_asset"],
                    repair_manifest_path.parent / row["crop_path"],
                )
            )
        source_paths.update(
            db / recipe.source_asset
            for recipe in (*SHARED_RECIPES.values(), *MARGIN_RECIPES.values())
        )
        sources_before = {str(path): file_state(path) for path in sorted(source_paths)}
        report["source_files_before"] = sources_before
        report["stage"] = "source_bindings_verified_searching_native"
        write_json(report_path, report)
        print(
            json.dumps({"stage": report["stage"], "source_files": len(sources_before)}),
            flush=True,
        )
        found = facade.search_themes(scope="master", limit=50)
        cards = {card.source_identity_sha256: card for card in found.cards}
        expected = {
            "parts": {},
            "shared": {},
            "additional_repair_hashes": [],
            "excluded_shared_hashes": [
                row.archived_sha256
                for row in (*SHARED_RECIPES.values(), *MARGIN_RECIPES.values())
            ]
            + [
                snapshots[0].crop_by_id[crop_id]["sha256"]
                for crop_id in sorted(ARCHIVE_ONLY_SHARED)
            ],
        }
        verified_repairs = set()
        verified_shared_repairs = set()
        verified_margin_repairs = set()
        for paper_id, theme_id, count in THEMES:
            key = _canonical_digest(
                {"scope": "master", "paper": paper_id, "theme": theme_id}
            )
            require(
                key in cards, "required complete theme not returned by native search"
            )
            card = cards[key]
            detail = facade.library_theme_detail(card)
            require(
                len(detail.parts) == card.atomic_total == count,
                "complete theme count changed",
            )
            shared = {}
            for image in detail.shared_images:
                raw = facade.library_image(image)
                require(sha(raw) == image.sha256, "shared preview hash mismatch")
                shared[image.crop_id] = image.sha256
                if image.crop_id in SHARED_RECIPES:
                    recipe = SHARED_RECIPES[image.crop_id]
                    verify_source_pixels(db, raw, recipe)
                    verified_shared_repairs.add(image.crop_id)
                    expected["additional_repair_hashes"].append(image.sha256)
            require(
                set(shared) == EXPECTED_SHARED[theme_id],
                "shared source blocks missing or duplicated",
            )
            expected["shared"][theme_id] = shared
            for part in detail.parts:
                original = originals[part.key]
                require(
                    part.summary_zh == original["prompt_raw"],
                    "native prompt crossed source",
                )
                answer = original["answer_evidence"]["answer_text"]
                require(
                    part.reference_answer_zh == answer, "native answer crossed source"
                )
                require(part.question_images, "atomic has no native question image")
                question_hashes = []
                for image in part.question_images:
                    raw = facade.library_image(image)
                    require(sha(raw) == image.sha256, "question preview hash mismatch")
                    question_hashes.append(image.sha256)
                    if image.crop_id in MARGIN_RECIPES:
                        verify_source_pixels(db, raw, MARGIN_RECIPES[image.crop_id])
                        verified_margin_repairs.add(image.crop_id)
                        expected["additional_repair_hashes"].append(image.sha256)
                    if part.key in by_repaired_node:
                        repair = by_repaired_node[part.key]
                        require(
                            image.sha256 == repair["sha256"],
                            "native repair differs from manifest",
                        )
                        require(
                            raw
                            == (
                                repair_manifest_path.parent / repair["crop_path"]
                            ).read_bytes(),
                            "native repair differs from standalone repaired PNG",
                        )
                        verified_repairs.add(part.key)
                expected["parts"][part.key] = {
                    "theme_id": theme_id,
                    "question_hashes": question_hashes,
                    "answer": answer,
                    "prompt_sha256": sha(original["prompt_raw"].encode("utf-8")),
                    "answer_sha256": sha(answer.encode("utf-8")),
                }
            facade.add_theme_to_basket(card)
            report["themes"].append(
                {"card": asdict(card), "part_count": count, "shared_images": shared}
            )
            print(
                json.dumps(
                    {
                        "stage": "native_theme_verified",
                        "theme": theme_id,
                        "parts": count,
                    }
                ),
                flush=True,
            )
        require(
            verified_repairs == set(by_repaired_node),
            "not all five repaired previews checked",
        )
        require(
            verified_shared_repairs == set(SHARED_RECIPES),
            "not both shared repairs checked",
        )
        report["native_checks"] = {
            "complete_theme_counts": [9, 5],
            "native_prompt_and_answer_match_original_candidate": True,
            "five_repaired_previews_match_manifest_and_png": sorted(verified_repairs),
            "two_shared_previews_match_original_page_pixels": sorted(
                verified_shared_repairs
            ),
            "archive_only_whole_page_crops_excluded": sorted(ARCHIVE_ONLY_SHARED),
            "two_margin_previews_match_original_page_pixels": sorted(
                verified_margin_repairs
            ),
            "parts": expected["parts"],
            "shared_materials": expected["shared"],
        }
        require(
            verified_margin_repairs == set(MARGIN_RECIPES),
            "not both margin repairs checked",
        )
        catalog = facade.paper_theme_catalog("master")
        composer = PaperComposerModel.from_basket(
            facade.basket(),
            catalog=catalog,
            mode="daily_practice",
            title="氢氧化亚铁制备与有机合成专题练习",
            show_question_scores=False,
        )
        require(
            [len(theme.questions) for theme in composer.themes] == [9, 5],
            "composer did not retain both complete themes",
        )
        composer.duration_minutes = 40
        for theme in composer.themes:
            for question in theme.questions:
                question.score, question.answer_space = 2, 0
        facade.state_store.save_draft(
            "paper-current", {"kind": "paper", "payload": composer.draft_payload()}
        )
        preview = facade.create_paper_preview(
            {
                "mode": "daily_practice",
                "title": composer.title,
                "duration_minutes": 40,
                "show_question_scores": False,
                "assembly": composer.make_preview(),
            }
        )
        facade.approve_paper_preview(preview.preview_id, preview.preview_hash)
        report["preview"] = {
            "preview_id": preview.preview_id,
            "preview_hash": preview.preview_hash,
            "native_data_snapshot_id": catalog["data_snapshot_id"],
        }
        report["stage"] = "approved_native_preview_exporting"
        write_json(report_path, report)
        paper_export_workbench.render_export_bundle = renderer_bridge(
            report, report_path, expected, repairs
        )
        result = facade.export_paper_preview(preview.preview_id, preview.preview_hash)
        require(
            result["status"] == "completed" and len(result["artifacts"]) == 4,
            "native four-file export incomplete",
        )
        report["export"] = result
        export_root = Path(result["artifacts"][0]["path"]).parent
        render_report = json.loads(
            (export_root / "render_qa_report.json").read_text(encoding="utf-8")
        )
        report["render_qa_report"] = str(export_root / "render_qa_report.json")
        require(
            render_report["machine_blocker_count"] == 0,
            "renderer reports a machine blocker",
        )
        report["page_images"] = {}
        new_hashes = {row["sha256"] for row in repairs} | set(
            expected["additional_repair_hashes"]
        )
        old_hashes = {row["supersedes_crop_sha256"] for row in repairs} | set(
            expected["excluded_shared_hashes"]
        )
        for artifact in result["artifacts"]:
            path = Path(artifact["path"])
            require(
                sha(path.read_bytes()) == artifact["sha256"],
                "export artifact checksum changed",
            )
            if path.suffix == ".docx":
                with zipfile.ZipFile(path) as archive:
                    media_hashes = {
                        sha(archive.read(name))
                        for name in archive.namelist()
                        if name.startswith("word/media/") and not name.endswith("/")
                    }
                require(
                    new_hashes <= media_hashes and not old_hashes & media_hashes,
                    "DOCX does not embed all nine repaired images or includes excluded images",
                )
                report.setdefault("docx_media_sha256", {})[artifact["artifact_id"]] = (
                    sorted(media_hashes)
                )
        for folder in (export_root / "qa").iterdir():
            pages = sorted(folder.glob("page-*.png")) if folder.is_dir() else []
            if pages:
                report["page_images"][folder.name] = [
                    {"path": str(path), **file_state(path)} for path in pages
                ]
        require(
            len(report["page_images"]) == 4,
            "four sets of canonical/PDF page images required",
        )
        report["status"] = (
            "export_and_source_checks_passed_awaiting_full_page_visual_review"
        )
        report["stage"] = "completed"
    except Exception as exc:  # noqa: BLE001 - preserve a failed QA receipt without bypassing it.
        report["status"] = "blocked"
        report["error"] = {
            "type": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "message": str(exc),
        }
        jobs = sorted((state_root / "paper-export-workbench").glob("WBEXP-*/job.json"))
        report["export_job_failures"] = []
        for path in jobs:
            job = json.loads(path.read_text(encoding="utf-8"))
            report["export_job_failures"].append(
                {
                    "path": str(path),
                    "status": job.get("status"),
                    "error": job.get("error"),
                    "artifacts": job.get("artifacts"),
                }
            )
    finally:
        paper_export_workbench.render_export_bundle = original_renderer
        if facade is not None:
            facade.shutdown()
        after = {path: file_state(Path(path)) for path in sources_before}
        report["source_files_after"] = after
        report["source_files_unchanged"] = sources_before == after
        if sources_before != after:
            report["status"] = "blocked_source_changed"
        report["seconds"] = round(time.monotonic() - started, 3)
        write_json(report_path, report)
        write_json(
            output / "latest_report.json",
            {"report": str(report_path), "status": report["status"]},
        )
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(report_path),
                "error": report.get("error"),
                "seconds": report["seconds"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if report["status"].startswith("export_and_source_checks_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
