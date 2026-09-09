"""Private, offline source-display QA for the two archived WeChat adapters.

No model, OCR, publication, build, approval, or source edit is performed. Basket
writes use a temporary isolated desktop state. Contact sheets are only indexes
for source-pixel inspection, not chemistry or classroom-quality approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

WORKSPACE = Path(__file__).resolve().parents[4]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import yaml
from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw, ImageFont

from integrations.deeptutor_shchem_v1 import (
    shanghai_high_east2025_theme45_direct_visual_scan as east,
)
from integrations.deeptutor_shchem_v1 import (
    songjiang2025_theme2_direct_visual_scan as songjiang,
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
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader

EXPECTED_THEMES = {
    songjiang.THEME_ID: (songjiang.PAPER_ID, 9),
    east.THEME_IDS[4]: (east.PAPER_ID, 6),
    east.THEME_IDS[5]: (east.PAPER_ID, 5),
}
EXPECTED_ATOMS = frozenset((*songjiang.EXPECTED_ATOMIC_IDS, *east.EXPECTED_ATOMIC_IDS))
NOTICE = (
    "仅供本机私人核查：源图展示、文本一致性和入口连通检查；不是化学全面审定、"
    "教师审核、裁片语义完整性或课堂可用性认可，不授权公开传播或教学成品复用。"
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _check(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _tree(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha(path.read_bytes()),
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    _check(path.is_relative_to(root.resolve()), "source binding escapes evidence root")
    _check(not (root / relative).is_symlink(), "source binding is a symbolic link")
    return path


def _source_rows(root: Path, module) -> tuple[dict, dict]:
    manifest_path = root / module.PRODUCT_RELATIVE / "candidate_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    rows = [
        json.loads(line)
        for line in _inside(root, manifest["candidate_file"])
        .read_text(encoding="utf-8-sig")
        .splitlines()
        if line.strip()
    ]
    parts = {}
    for question in rows:
        for part in question["parts"]:
            _check(part["part_id"] not in parts, "duplicate upstream atomic identity")
            parts[part["part_id"]] = (question, part)
    return manifest, parts


def _font(size: int):
    for name in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def _write_sheets(output: Path, theme_number: int, pictures: list[dict]) -> list[dict]:
    """Fit complete images into tiles; never crop or overwrite source bytes."""
    sheets = []
    for offset in range(0, len(pictures), 4):
        batch = pictures[offset : offset + 4]
        canvas = Image.new("RGB", (1840, 2260), "#e8edf0")
        draw = ImageDraw.Draw(canvas)
        draw.text(
            (24, 12),
            f"PRIVATE QA / theme {theme_number} / source pixels only",
            font=_font(26),
            fill="#182735",
        )
        draw.text(
            (24, 50),
            "仅核查源图展示，不代表化学审定或发布许可。完整像素副本见 images。",
            font=_font(23),
            fill="#182735",
        )
        for position, picture in enumerate(batch):
            x, y = 20 + (position % 2) * 910, 96 + (position // 2) * 1072
            draw.rectangle((x, y, x + 895, y + 1056), fill="white")
            draw.text((x + 12, y + 8), picture["node_id"], font=_font(20), fill="black")
            draw.text(
                (x + 12, y + 36),
                f"{picture['role']} / {picture['sha256'][:16]}",
                font=_font(20),
                fill="black",
            )
            with Image.open(output / picture["image_file"]) as source:
                shown = source.convert("RGB")
                shown.thumbnail((871, 970), Image.Resampling.LANCZOS)
                canvas.paste(shown, (x + 12, y + 72))
        relative = f"theme-{theme_number:02d}-sheet-{offset // 4 + 1:02d}.png"
        path = output / relative
        canvas.save(path)
        sheets.append(
            {
                "file": relative,
                "sha256": _sha(path.read_bytes()),
                "image_count": len(batch),
                "full_images_not_cropped": True,
            }
        )
    return sheets


def audit_archived(
    workspace: Path, state_root: Path, output_dir: Path | None = None
) -> dict:
    """Run the bounded three-theme audit. Callers own an isolated state root."""
    started = time.monotonic()
    workspace, state_root = workspace.resolve(), state_root.resolve()
    evidence = workspace / "sh-chem-db"
    _check(
        not state_root.is_relative_to(evidence),
        "state must not be in the source library",
    )
    _check(
        not state_root.exists() or not any(state_root.iterdir()),
        "use a fresh isolated state root",
    )
    if output_dir is not None:
        output_dir = output_dir.resolve()
        _check(
            not output_dir.is_relative_to(evidence),
            "QA output must not be in the source library",
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "images").mkdir(exist_ok=True)
    report = {
        "schema_version": "private-archived-wechat-native-qa-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "notice_zh": NOTICE,
        "model_calls": 0,
        "source_writes": 0,
        "real_personal_state_accessed": False,
        "chemistry_approved": False,
        "teacher_reviewed": False,
        "publication_allowed": False,
        "themes": [],
        "contact_sheets": [],
        "checks": {},
    }
    paths = DesktopPaths.from_workspace(workspace, state_root=state_root)
    # Inject a metadata-only stub: the test must not read the user's model store.
    facade = DesktopWorkbenchFacade(
        paths, provider_store=SimpleNamespace(list_metadata=list)
    )
    try:
        personal_before = _tree(state_root)
        native = facade._master_direct
        _check(
            native is facade._themes.direct_scans,
            "native catalog and image reader must agree",
        )
        _check(
            native is facade._paper_export_readers()[3],
            "native export must share the candidate reader",
        )
        (frozen_native,) = snapshot_reader_graph((native,))
        (legacy,) = snapshot_reader_graph((MasterDirectVisualScanReader(evidence),))
        native_catalog, legacy_catalog = frozen_native.catalog(), legacy.catalog()
        _check(native_catalog["count"] == 244, "native direct record count is not 244")
        _check(legacy_catalog["count"] == 224, "legacy direct contract changed")
        native_ids, legacy_ids = (
            set(native_catalog["master_node_ids"]),
            set(legacy_catalog["master_node_ids"]),
        )
        _check(
            native_ids - legacy_ids == EXPECTED_ATOMS,
            "native delta is not the intended 20 atoms",
        )
        _check(
            legacy_ids <= native_ids, "a legacy source disappeared from native catalog"
        )
        report["counts"] = {
            "legacy_direct": 224,
            "native_direct": 244,
            "new_atomic_parts": 20,
            "new_theme_count": 3,
            "legacy_products": len(legacy_catalog["products"]),
            "native_products": len(native_catalog["products"]),
        }
        source_readers = {
            songjiang.PRODUCT_ID: (songjiang, frozen_native.songjiang2025_theme2),
            east.PRODUCT_ID: (east, frozen_native.shanghai_high_east2025_theme45),
        }
        originals, record_sources, source_before = {}, {}, {}
        for module, reader in source_readers.values():
            _manifest, rows = _source_rows(evidence, module)
            originals.update(rows)
            snapshot = reader._snapshot()
            for atomic_id in snapshot.by_master_id:
                record_sources[atomic_id] = (reader, snapshot.by_master_id[atomic_id])
            bound_paths = set(snapshot.output_bytes) | {
                (module.PRODUCT_RELATIVE / "candidate_manifest.json").as_posix()
            }
            for relative in bound_paths:
                path = _inside(evidence, relative)
                raw = path.read_bytes()
                if relative in snapshot.output_bytes:
                    _check(
                        raw == snapshot.output_bytes[relative],
                        "reader source snapshot differs from bound file",
                    )
                source_before[relative] = {
                    "bytes": len(raw),
                    "sha256": _sha(raw),
                    "mtime_ns": path.stat().st_mtime_ns,
                }
        _check(
            set(originals) == set(record_sources) == EXPECTED_ATOMS,
            "upstream identities do not match native delta",
        )

        catalog = facade.paper_theme_catalog("master")
        contract = yaml.safe_load(
            (
                workspace
                / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
            ).read_text(encoding="utf-8")
        )

        def validate_schema(name, value):
            schema = {
                "$ref": f"#/components/schemas/{name}",
                "components": contract["components"],
            }
            errors = list(Draft202012Validator(schema).iter_errors(value))
            _check(
                not errors,
                name + ": " + "; ".join(error.message for error in errors[:3]),
            )

        # The old HTTP envelope freezes old coverage counts. Do not loosen its
        # constants or claim the native augmented catalog passes that envelope.
        (legacy_themes,) = snapshot_reader_graph(
            (ThemeWorkbenchReader(evidence, direct_scans=legacy),)
        )
        validate_schema("ThemeWorkbenchData", legacy_themes.groups("master"))
        validate_schema("ThemeWorkbenchTopCounts", catalog["counts"])
        validate_schema(
            "ThemeWorkbenchUnassigned", catalog["unassigned_pending_review"]
        )
        validate_schema("ThemeWorkbenchAuthority", catalog["authority"])
        validate_schema("ThemeWorkbenchIntegrity", catalog["integrity"])
        for paper in catalog["papers"]:
            # These nested strict schemas include each theme and atomic object.
            validate_schema("ThemeWorkbenchPaperGroup", paper)
        _check(
            catalog["counts"]["atomic_parts"] == 470
            and catalog["counts"]["display_atomic_units"] == 480
            and catalog["counts"]["unassigned_atomic_parts"] == 43,
            "native canonical denominators changed",
        )
        report["native_theme_counts"] = catalog["counts"]
        report["schema_scope"] = {
            "legacy": "full ThemeWorkbenchData including its frozen counts",
            "native": "strict existing PaperGroup/Theme/Atomic, counts shape, unassigned, authority and integrity; native counts checked separately",
        }
        found = facade.search_themes(scope="master", limit=50)
        cards = {card.source_identity_sha256: card for card in found.cards}
        groups = {
            group["theme"]["id"]: (entry["paper"], group)
            for entry in catalog["papers"]
            for group in entry["theme_groups"]
        }
        selected_cards = []
        all_image_occurrences = []
        for theme_number, (theme_id, (paper_id, count)) in enumerate(
            EXPECTED_THEMES.items(), 1
        ):
            identity = _canonical_digest(
                {"scope": "master", "paper": paper_id, "theme": theme_id}
            )
            _check(identity in cards, f"native search did not expose theme {theme_id}")
            card = cards[identity]
            selected_cards.append(card)
            _, group = groups[theme_id]
            atoms = group["atomic_chain"]
            _check(
                len(atoms) == card.atomic_total == count,
                "theme atomic denominator changed",
            )
            detail = facade.library_theme_detail(card)
            _check(
                [part.key for part in detail.parts]
                == [atom["atomic_part_id"] for atom in atoms],
                "native detail lost or reordered an atomic part",
            )
            expected_shared = set()
            theme_pictures, part_rows = [], []
            for atom, part in zip(atoms, detail.parts, strict=True):
                node_id = part.key
                reader, _record = record_sources[node_id]
                _question, original = originals[node_id]
                labels = atom["label_summary"]
                expected_k = [
                    value["id"] for value in original["classification"]["knowledge_K"]
                ]
                _check(
                    labels["status"] == "pending"
                    and labels["primary_K"] is None
                    and labels["supporting_K"] == []
                    and labels["cognitive_prelabel"] is None,
                    "unknown source labels were promoted",
                )
                _check(
                    labels["knowledge_candidates_K"] == expected_k,
                    "source knowledge candidates lost",
                )
                _check(
                    atom["dependency"]["kind"] == "blocked_pending_review"
                    and atom["dependency"]["status"] == "blocked_unknown_not_inferred",
                    "unknown source dependency was promoted",
                )
                _check(
                    part.summary_zh == original["prompt_raw"],
                    "source prompt changed in native detail",
                )
                expected_answer = original["answer_evidence"]["answer_text"]
                _check(
                    part.reference_answer_zh == expected_answer,
                    "source answer changed or cross-wired",
                )
                _check(
                    part.question_images and not part.availability_zh,
                    "new atomic is not visually available",
                )
                source_detail = reader.detail(node_id)
                _check(
                    source_detail["reference_answer"]["independently_verified"]
                    is False,
                    "answer verification was elevated",
                )
                expected = {
                    row["crop_id"]: row for row in source_detail["evidence_descriptors"]
                }
                expected_questions = {
                    key
                    for key, row in expected.items()
                    if row["evidence_role"] == "question"
                }
                expected_shared.update(
                    key
                    for key, row in expected.items()
                    if row["evidence_role"] == "shared_material"
                )
                _check(
                    {image.crop_id for image in part.question_images}
                    == expected_questions,
                    "native question descriptors differ from the source",
                )
                for image in part.question_images:
                    raw = facade.library_image(image)
                    source_payload = reader.question_crop(node_id, image.crop_id)
                    _check(
                        raw == source_payload.data
                        and _sha(raw)
                        == image.sha256
                        == expected[image.crop_id]["sha256"],
                        "native pixels do not match this atomic's original crop",
                    )
                    theme_pictures.append(
                        {
                            "node_id": node_id,
                            "crop_id": image.crop_id,
                            "role": image.role,
                            "sha256": image.sha256,
                            "bytes": len(raw),
                            "raw": raw,
                        }
                    )
                part_rows.append(
                    {
                        "atomic_id": node_id,
                        "prompt_sha256": _sha(part.summary_zh.encode()),
                        "answer_sha256": _sha(expected_answer.encode()),
                        "question_image_count": len(part.question_images),
                        "knowledge_candidates_K": expected_k,
                        "primary_K": None,
                        "supporting_K": [],
                        "cognitive_prelabel": None,
                        "dependency": atom["dependency"]["kind"],
                    }
                )
            _check(
                {image.crop_id for image in detail.shared_images} == expected_shared,
                "theme shared images were lost or cross-wired",
            )
            _check(
                {row["material_id"] for row in group["shared_context"]["materials"]}
                == expected_shared,
                "theme catalog lost a shared material binding",
            )
            for image in detail.shared_images:
                reader, _ = record_sources[image.node_id]
                raw = facade.library_image(image)
                _check(
                    raw == reader.question_crop(image.node_id, image.crop_id).data
                    and _sha(raw) == image.sha256,
                    "shared image bytes differ from their explicit source",
                )
                theme_pictures.append(
                    {
                        "node_id": image.node_id,
                        "crop_id": image.crop_id,
                        "role": image.role,
                        "sha256": image.sha256,
                        "bytes": len(raw),
                        "raw": raw,
                    }
                )
            for picture in theme_pictures:
                raw = picture.pop("raw")
                picture["image_file"] = f"images/{picture['sha256']}.png"
                if output_dir is not None:
                    target = output_dir / picture["image_file"]
                    if target.exists():
                        _check(target.read_bytes() == raw, "QA image output collision")
                    else:
                        target.write_bytes(raw)
            if output_dir is not None:
                report["contact_sheets"].extend(
                    _write_sheets(output_dir, theme_number, theme_pictures)
                )
            report["themes"].append(
                {
                    "theme_id": theme_id,
                    "paper_id": paper_id,
                    "title": card.title_zh,
                    "atomic_parts": count,
                    "shared_material_count": len(expected_shared),
                    "parts": part_rows,
                    "images": theme_pictures,
                }
            )
            all_image_occurrences.extend(theme_pictures)

        pending = facade.search_themes(scope="master", query="父链", limit=50)
        wave = facade.search_themes(scope="wave1", limit=50)
        _check(
            not pending.cards
            and pending.total_themes == 0
            and pending.pending_atomic_parts == 43
            and pending.pending_matched_atomic_parts == 43,
            "pending parentage still creates theme cards",
        )
        _check(
            wave.total_themes == len(wave.cards) == 25
            and wave.pending_atomic_parts == 0,
            "legacy Wave1 theme entry changed",
        )
        _check(
            _tree(state_root) == personal_before,
            "read-only native browsing mutated isolated personal state",
        )
        report["checks"].update(
            legacy_full_theme_schema=True,
            native_theme_structure_schema=True,
            read_phase_personal_unchanged=True,
            pending_search_zero_cards=True,
            pending_parts=43,
            wave1_themes=25,
            original_prompt_answer_and_pixels_equal=True,
            unknown_labels_preserved=True,
        )
        for card in selected_cards:
            facade.add_theme_to_basket(card)
        composer = PaperComposerModel.from_basket(
            facade.basket(),
            catalog=catalog,
            mode="daily_practice",
            title="私人源图连通核查",
        )
        preview = composer.make_preview()
        _check(
            len(composer.themes) == 3
            and sum(len(theme.questions) for theme in composer.themes) == 20,
            "basket/composer lost an original theme or atomic",
        )
        for theme in composer.themes:
            _, group = groups[theme.key]
            materials = group["shared_context"]["materials"]
            _check(
                len(theme.shared_materials) == len(materials),
                "composer lost shared materials",
            )
            _check(
                [item["text"] for item in theme.shared_materials]
                == [item["candidate_description_zh"] for item in materials],
                "composer changed material summaries",
            )
            _check(
                theme.source_ref["theme_id"] == theme.key,
                "composer theme source identity changed",
            )
            expected_ids = {atom["atomic_part_id"] for atom in group["atomic_chain"]}
            _check(
                {q.source_ref["atomic_part_id"] for q in theme.questions}
                == expected_ids,
                "composer atomic source references were lost",
            )
        report["checks"].update(
            isolated_basket_composer_preserved=True,
            composer_theme_count=len(preview["themes"]),
            composer_atomic_count=20,
        )
        for relative, expected in source_before.items():
            path = _inside(evidence, relative)
            actual = {
                "bytes": path.stat().st_size,
                "sha256": _sha(path.read_bytes()),
                "mtime_ns": path.stat().st_mtime_ns,
            }
            _check(
                actual == expected, "a bound original source changed during the audit"
            )
        report["source_files"] = source_before
        report["checks"]["bound_sources_unchanged"] = True
        report["counts"].update(
            bound_source_files=len(source_before),
            question_image_occurrences=sum(
                p["role"] == "question" for p in all_image_occurrences
            ),
            shared_image_occurrences=sum(
                p["role"] == "shared_material" for p in all_image_occurrences
            ),
            distinct_display_images=len({p["sha256"] for p in all_image_occurrences}),
        )
        report["status"] = "source_display_checks_passed_not_teaching_approval"
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return report
    finally:
        facade.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New private QA directory, never a source-library directory",
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        parser.error("output-dir must be new; existing QA outputs are not overwritten")
    if output.is_relative_to(args.workspace.resolve() / "sh-chem-db"):
        parser.error("output-dir cannot be inside the source library")
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="shchem-archived-native-qa-") as name:
        try:
            report = audit_archived(args.workspace, Path(name) / "state", output)
        except Exception as exc:  # noqa: BLE001 - record failure, never convert it to PASS
            report = {
                "status": "failed",
                "notice_zh": NOTICE,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "chemistry_approved": False,
                "publication_allowed": False,
            }
            (output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=False))
            return 1
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "counts": report["counts"],
                "report": str(output / "report.json"),
                "notice_zh": NOTICE,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
