"""Exercise real local source-A previews and teacher/student export in private state.

No provider calls or edits to archived sources. The bundled artifact Python
authors DOCX; the existing Word COM backend converts it when bundled LO is absent.
Page images still require visual inspection after this script passes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1 import paper_export_workbench
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopWorkbenchFacade,
    _canonical_digest,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
    PaperComposerModel,
)
from integrations.deeptutor_shchem_v1.level_exam2025_theme3_fosinopril_direct_visual_scan import (
    EXPECTED_ATOMIC_IDS,
    PAPER_ID,
    PRODUCT_RELATIVE,
    THEME_ID,
    VISUAL_ONLY_ATOMIC_IDS,
)

BUNDLED_PYTHON = Path(
    "C:/Users/20671/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(value, message):
    if not value:
        raise AssertionError(message)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def block_hashes(blocks):
    return [Path(block["asset_ref"]).stem for block in blocks if block.get("asset_ref")]


def main():
    parent = ROOT / "runtime/deeptutor_shchem/fosinopril_native_0160"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="export-", dir=parent))
    db = ROOT / "sh-chem-db"
    package = db / PRODUCT_RELATIVE
    manifest = json.loads(
        (package / "candidate_manifest.json").read_text(encoding="utf-8")
    )
    paths = {db / "catalog.csv", package / "candidate_manifest.json"}
    paths.update(
        db / row["asset"]
        for field in ("source_assets", "crops")
        for row in manifest[field]
    )
    paths.update(db / row["path"] for row in manifest["outputs"])
    before = {str(path): sha(path.read_bytes()) for path in paths}
    report = {
        "source_files": len(before),
        "model_calls": 0,
        "personal_state_accessed": False,
        "human_reviewed": False,
        "visual_review_complete": False,
        "publication_allowed": False,
    }
    report_path = output / "verification.json"
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=output / "isolated-state"),
        provider_store=SimpleNamespace(list_metadata=list),
    )
    original_renderer = paper_export_workbench.render_export_bundle
    try:
        found = facade.search_themes(scope="master", limit=50)
        key = _canonical_digest(
            {"scope": "master", "paper": PAPER_ID, "theme": THEME_ID}
        )
        card = next(card for card in found.cards if card.source_identity_sha256 == key)
        detail = facade.library_theme_detail(card)
        require(
            tuple(part.key for part in detail.parts) == EXPECTED_ATOMIC_IDS,
            "native nine-unit identity/order",
        )
        require(
            len(detail.shared_images) == 1, "complete shared route must appear once"
        )
        questions, answers = {}, {}
        shared = set()
        for image in detail.shared_images:
            raw = facade.library_image(image)
            require(sha(raw) == image.sha256, "shared image hash")
            shared.add(image.sha256)
        for part in detail.parts:
            questions[part.key], answers[part.key] = [], []
            require(
                len(part.question_images)
                == (2 if part.key == EXPECTED_ATOMIC_IDS[6] else 1),
                "cross-page question count",
            )
            require(len(part.answer_images) == 1, "answer preview missing")
            for image in part.question_images:
                require(
                    sha(facade.library_image(image)) == image.sha256,
                    "question preview hash",
                )
                questions[part.key].append(image.sha256)
            for image in part.answer_images:
                require(
                    sha(facade.library_answer_image(image)) == image.sha256,
                    "teacher answer preview hash",
                )
                answers[part.key].append(image.sha256)
        facade.add_theme_to_basket(card)
        catalog = facade.paper_theme_catalog("master")
        composer = PaperComposerModel.from_basket(
            facade.basket(),
            catalog=catalog,
            mode="daily_practice",
            title="福辛普利中间体有机合成专题练习",
            show_question_scores=False,
        )
        require(
            len(composer.themes) == 1 and len(composer.themes[0].questions) == 9,
            "composer loses questions",
        )
        composer.duration_minutes = 40
        for question in composer.themes[0].questions:
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
        report.update(
            stage="native_preview_ready",
            question_images=questions,
            answer_images=answers,
            shared_images=sorted(shared),
        )
        write_json(report_path, report)
        print(
            json.dumps({"stage": report["stage"], "report": str(report_path)}),
            flush=True,
        )

        def render(bundle, *, output_dir, toolchain, asset_root):
            require(
                toolchain.python_exe.resolve() == BUNDLED_PYTHON.resolve(),
                "bundled artifact runtime required",
            )
            require(
                toolchain.conversion_backend == "word_com",
                "expected Word COM alternative; no desktop LO",
            )
            for audience in ("student", "teacher"):
                plan = bundle[f"{audience}_plan"]
                sections = plan["visible"]["theme_sections"]
                atoms = [
                    atom
                    for section in sections
                    for question in section["printed_questions"]
                    for atom in question["atomic_parts"]
                ]
                bindings = plan["bindings"]["atomic_part_bindings"]
                require(
                    len(sections) == 1 and len(atoms) == len(bindings) == 9,
                    "plan shape",
                )
                for binding, atom in zip(bindings, atoms, strict=True):
                    node = binding["atomic_part_id"]
                    require(
                        block_hashes(atom["question_blocks"]) == questions[node],
                        "question source/order drift",
                    )
                    require(
                        atom["answer_space"]["lines"] == 0 and atom["score"] == 2,
                        "score/space setting lost",
                    )
                    if audience == "teacher":
                        answer = atom["teacher_notes"]["source_reference_answer"]
                        require(
                            block_hashes(answer.get("content_blocks", []))
                            == (
                                answers[node] if node in VISUAL_ONLY_ATOMIC_IDS else []
                            ),
                            "teacher structure image lost/mixed",
                        )
            require(
                bundle["preset"]["student_version"]["show_item_scores"] is False,
                "student score toggle",
            )
            bundle_path = output_dir.parent / "render_bundle.json"
            require(
                json.loads(bundle_path.read_text(encoding="utf-8")) == bundle,
                "bundle serialization changed",
            )
            report.update(stage="rendering", render_bundle=str(bundle_path))
            write_json(report_path, report)
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
            result = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            write_json(
                output / "renderer-process.json",
                {
                    "command": command,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
            require(
                result.returncode == 0, "renderer failed; see private process report"
            )
            return json.loads(
                (output_dir / "render_qa_report.json").read_text(encoding="utf-8")
            )

        paper_export_workbench.render_export_bundle = render
        result = facade.export_paper_preview(preview.preview_id, preview.preview_hash)
        require(
            result["status"] == "completed" and len(result["artifacts"]) == 4,
            "four-file export incomplete",
        )
        expected_questions = {
            value for values in questions.values() for value in values
        } | shared
        all_answers = {value for values in answers.values() for value in values}
        required_answers = {
            value
            for key, values in answers.items()
            if key in VISUAL_ONLY_ATOMIC_IDS
            for value in values
        }
        for artifact in result["artifacts"]:
            path = Path(artifact["path"])
            require(sha(path.read_bytes()) == artifact["sha256"], "artifact hash drift")
            if path.suffix != ".docx":
                continue
            with zipfile.ZipFile(path) as doc:
                media = {
                    sha(doc.read(name))
                    for name in doc.namelist()
                    if name.startswith("word/media/")
                }
            expected = expected_questions | (
                required_answers if artifact["artifact_id"] == "teacher_docx" else set()
            )
            require(media == expected, "DOCX missing or unexpected source/answer image")
            if artifact["artifact_id"] == "student_docx":
                require(not all_answers & media, "answer image leaked to student")
        report.update(
            status="export_checks_passed_pending_page_review",
            stage="completed",
            export=result,
        )
        report["page_images"] = [
            str(path)
            for path in Path(result["artifacts"][0]["path"]).parent.glob(
                "qa/*/page-*.png"
            )
        ]
    except Exception as exc:
        report.update(
            status="failed", error={"type": type(exc).__name__, "message": str(exc)}
        )
        raise
    finally:
        paper_export_workbench.render_export_bundle = original_renderer
        facade.shutdown()
        report["sources_unchanged"] = before == {
            str(path): sha(path.read_bytes()) for path in paths
        }
        write_json(report_path, report)
    require(report["sources_unchanged"], "source files changed")
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(report_path),
                "pages": len(report["page_images"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
