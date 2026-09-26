"""Private, offline source-display QA for the two archived WeChat adapters.

No model, OCR, publication, build, approval, or source edit is performed. Basket
writes use a temporary isolated desktop state. Contact sheets are only indexes
for source-pixel inspection, not chemistry or classroom-quality approval.
"""

from __future__ import annotations

import argparse
import hashlib
import io
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
from integrations.deeptutor_shchem_v1.archived_wechat_crop_revision import (
    MARGIN_RECIPES,
    RECIPES,
    REVISION_ID,
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
ALL_PRESENTATION_RECIPES = {**RECIPES, **SHARED_RECIPES, **MARGIN_RECIPES}
EXPECTED_KNOWN_DEPENDENCIES = frozenset(
    (
        *songjiang.EXPECTED_ATOMIC_IDS,
        *(f"SHEAST2025-M05-B-T5-Q{i}-P1" for i in range(1, 6)),
    )
)
EXPECTED_PRIOR_IDS = {
    "SJ2025-EM-S2-Q7-P1": ["SJ2025-EM-S2-Q6-P1"],
    "SJ2025-EM-S2-Q9-P1": ["SJ2025-EM-S2-Q8-P1"],
}
EXPECTED_SHARED_IDS = {
    songjiang.THEME_ID: {
        "SJ25T2-C-783aedf98189899baf2c2909",
        "SJ25T2-C-377ff6ffad094904d6cf5ea4",
        "SJ25T2-C-bc033f159e7aba3c44d5af29",
    },
    east.THEME_IDS[4]: {"SHEAST2025-CROP-80fa74a02acc9c79eefdcaf0"},
    east.THEME_IDS[5]: {"SHEAST2025-CROP-b39d0d33cdac04884a42c934"},
}
EXPECTED_SHARED_BY_ATOM = {
    **{
        f"SJ2025-EM-S2-Q{i}-P1": crop_id
        for numbers, crop_id in (
            ((1, 2, 3), "SJ25T2-C-783aedf98189899baf2c2909"),
            ((4, 5, 6, 7), "SJ25T2-C-377ff6ffad094904d6cf5ea4"),
            ((8, 9), "SJ25T2-C-bc033f159e7aba3c44d5af29"),
        )
        for i in numbers
    },
    **{
        f"SHEAST2025-M05-B-T4-Q{i}-P1": "SHEAST2025-CROP-80fa74a02acc9c79eefdcaf0"
        for i in range(1, 7)
    },
    **{
        f"SHEAST2025-M05-B-T5-Q{i}-P1": "SHEAST2025-CROP-b39d0d33cdac04884a42c934"
        for i in range(1, 6)
    },
}
ARCHIVE_ONLY_SHARED_IDS = {
    "SJ25T2-C-cad3a95bc7739857cfe7515e",
    "SJ25T2-C-c4702d34468809ed194d0065",
}
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


def _verify_presentation(evidence, image, raw, descriptor, archived, repaired):
    """Independently compare archive binding, displayed bytes and source pixels."""
    _check(
        image.role == descriptor["evidence_role"] == archived["evidence_role"]
        and archived["sha256"] == descriptor.get("archived_crop_sha256", image.sha256),
        "presentation lost its original archived crop role or hash binding",
    )
    recipe = ALL_PRESENTATION_RECIPES.get(image.crop_id)
    if recipe is None:
        _check(
            _sha(raw) == archived["sha256"]
            and "presentation_revision_id" not in descriptor,
            "unrevised source image unexpectedly changed",
        )
        return
    _check(
        descriptor.get("presentation_revision_id") == REVISION_ID
        and descriptor["evidence_role"] == recipe.evidence_role
        and descriptor["source_asset"] == recipe.source_asset
        and descriptor["source_sha256"] == recipe.source_sha256
        and descriptor["source_page"] == recipe.page
        and descriptor["source_crop_box_convention"] == "xywh"
        and tuple(descriptor["source_crop_box"]) == recipe.box
        and tuple(descriptor["archived_source_crop_box"]) == recipe.original_box
        and archived["sha256"] == recipe.archived_sha256,
        "presentation recipe identity changed",
    )
    page_path = _inside(evidence, recipe.source_asset)
    _check(
        _sha(page_path.read_bytes()) == recipe.source_sha256,
        "repaired presentation source page changed",
    )
    with Image.open(page_path) as page, Image.open(io.BytesIO(raw)) as shown:
        x, y, width, height = recipe.box
        region = page.crop((x, y, x + width, y + height))
        expected_png = io.BytesIO()
        region.save(expected_png, format="PNG", optimize=False, compress_level=9)
        _check(
            raw == expected_png.getvalue()
            and shown.size == region.size == (width, height)
            and shown.mode == region.mode
            and shown.tobytes() == region.tobytes(),
            "repaired presentation bytes or pixels differ from source rectangle",
        )
    repaired.add(image.crop_id)


def _verify_dependency(node_id, projected, detail, source_crops, source_record):
    dependency = detail["dependency"]
    evidence = detail.get("dependency_evidence")
    _check(
        dependency["shared_material_crop_ids"] == [EXPECTED_SHARED_BY_ATOM[node_id]],
        "a question was connected to another question's shared material",
    )
    if node_id not in EXPECTED_KNOWN_DEPENDENCIES:
        _check(
            dependency["dependency_kind"] == "unknown"
            and dependency["status"] == "unknown_prior_dependency_not_recorded"
            and dependency["prior_atomic_part_ids"] == []
            and projected["kind"] == "blocked_pending_review"
            and projected["status"] == "blocked_unknown_not_inferred"
            and projected["prior_atomic_part_ids"] == []
            and evidence is None,
            "unknown source dependency was promoted",
        )
        return
    prior = EXPECTED_PRIOR_IDS.get(node_id, [])
    shared_ids = {
        row["crop_id"]
        for row in detail["evidence_descriptors"]
        if row["evidence_role"] == "shared_material"
    }
    source_pages = set()
    original_descriptors = {
        row["crop_id"]: row for row in source_record["viewed_evidence"]
    }
    for row in detail["evidence_descriptors"]:
        # Legacy East descriptors omit page SHA; use the explicit, frozen crop
        # relation, not a guessed page or the dependency claim being checked.
        crop = source_crops[row["crop_id"]]
        original_page = original_descriptors[row["crop_id"]]["source_page"]
        _check(
            row["source_page"] == original_page
            and crop.get("page_number", original_page) == original_page
            and row.get("source_sha256", crop["source_sha256"])
            == crop["source_sha256"],
            "dependency source descriptor does not match its archived crop relation",
        )
        source_pages.add((original_page, crop["source_sha256"]))
    expected_revision = (
        songjiang.DEPENDENCY_REVISION_ID
        if node_id in songjiang.EXPECTED_ATOMIC_IDS
        else east.DEPENDENCY_REVISION_ID
    )
    _check(
        dependency["status"] == "source_page_backed_candidate_dependency"
        and dependency["dependency_kind"]
        == ("one_prior_part" if prior else "shared_theme_context")
        and dependency["prior_atomic_part_ids"] == prior
        and projected["kind"] == ("one_prior_part" if prior else "shared_material_only")
        and projected["status"] == "validated_explicit"
        and projected["prior_atomic_part_ids"] == prior
        and set(dependency["shared_material_crop_ids"]) == shared_ids
        and len(shared_ids) == 1
        and not shared_ids.intersection(ARCHIVE_ONLY_SHARED_IDS),
        "source-backed dependency or required shared material changed",
    )
    _check(
        isinstance(evidence, dict)
        and evidence["revision_id"] == expected_revision
        and evidence["status"] == "source_page_visual_inspection_candidate"
        and evidence["candidate_only"] is True
        and evidence["human_checked"] is False
        and evidence["source_page_bindings"]
        == [{"page": page, "sha256": digest} for page, digest in sorted(source_pages)]
        and set(evidence["required_shared_material_crop_ids"]) == shared_ids,
        "dependency evidence lost its source binding or became human approval",
    )


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
        "schema_version": "private-archived-wechat-native-qa-v3",
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
        _check(native_catalog["count"] == 262, "native direct record count is not 262")
        _check(legacy_catalog["count"] == 224, "legacy direct contract changed")
        native_ids, legacy_ids = (
            set(native_catalog["master_node_ids"]),
            set(legacy_catalog["master_node_ids"]),
        )
        _check(
            native_ids - legacy_ids
            == EXPECTED_ATOMS
            | {f"LE2025-S3-Q{number:02}-P1" for number in range(1, 10)}
            | {f"FD2026-APR-S5-Q{number}-P1" for number in range(40, 48)}
            | {"FD2026-APR-S5-Q41-P2"},
            "native delta is not the original 20 plus fosinopril 9 and ZnS 9 atoms",
        )
        _check(
            legacy_ids <= native_ids, "a legacy source disappeared from native catalog"
        )
        report["counts"] = {
            "legacy_direct": 224,
            "native_direct": native_catalog["count"],
            "new_atomic_parts": 20,
            "new_theme_count": 3,
            "legacy_products": len(legacy_catalog["products"]),
            "native_products": len(native_catalog["products"]),
        }
        source_readers = {
            songjiang.PRODUCT_ID: (songjiang, frozen_native.songjiang2025_theme2),
            east.PRODUCT_ID: (east, frozen_native.shanghai_high_east2025_theme45),
        }
        originals, record_sources, source_before, archived_crops = {}, {}, {}, {}
        source_crops = {}
        for module, reader in source_readers.values():
            _manifest, rows = _source_rows(evidence, module)
            originals.update(rows)
            snapshot = reader._snapshot()
            for crop_id, crop in snapshot.crop_by_id.items():
                raw = snapshot.output_bytes[crop["output_path"]]
                _check(_sha(raw) == crop["sha256"], "archived crop hash changed")
                archived_crops[crop_id] = crop["sha256"]
                source_crops[crop_id] = crop
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
        for recipe in ALL_PRESENTATION_RECIPES.values():
            path = _inside(evidence, recipe.source_asset)
            raw = path.read_bytes()
            _check(_sha(raw) == recipe.source_sha256, "source page recipe hash changed")
            source_before[recipe.source_asset] = {
                "bytes": len(raw),
                "sha256": _sha(raw),
                "mtime_ns": path.stat().st_mtime_ns,
            }
        _check(len(archived_crops) == 52, "archive crop denominator changed")
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
        repaired_crop_ids = set()
        archive_only_crop_ids = set()
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
                _verify_dependency(
                    node_id, atom["dependency"], source_detail, source_crops, _record
                )
                _check(
                    source_detail["reference_answer"]["independently_verified"]
                    is False,
                    "answer verification was elevated",
                )
                _check(
                    source_detail["candidate_analysis"]["candidate_only"] is True
                    and source_detail["candidate_analysis"]["correctness_verified"]
                    is False
                    and source_detail["authority"]["formal_promotion_allowed"] is False
                    and source_detail["authority"]["publication_allowed"] is False,
                    "source candidate authority was elevated",
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
                for archived_context in source_detail.get(
                    "archived_context_evidence", []
                ):
                    crop_id = archived_context["crop_id"]
                    _check(
                        crop_id in ARCHIVE_ONLY_SHARED_IDS
                        and crop_id not in expected
                        and archived_context["display_status"]
                        == "archive_only_repeats_printed_questions"
                        and archived_context["evidence_role"] == "shared_material"
                        and _sha(reader.question_crop(node_id, crop_id).data)
                        == archived_context["sha256"]
                        == archived_crops[crop_id],
                        "archive-only full page lost its source binding or leaked into display",
                    )
                    archive_only_crop_ids.add(crop_id)
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
                        "native pixels do not match this atomic's current presentation",
                    )
                    descriptor = expected[image.crop_id]
                    archived = next(
                        row
                        for row in _record["viewed_evidence"]
                        if row["crop_id"] == image.crop_id
                    )
                    _verify_presentation(
                        evidence, image, raw, descriptor, archived, repaired_crop_ids
                    )
                    theme_pictures.append(
                        {
                            "node_id": node_id,
                            "crop_id": image.crop_id,
                            "role": image.role,
                            "sha256": image.sha256,
                            "bytes": len(raw),
                            "archived_crop_sha256": archived["sha256"],
                            "presentation_revision_id": descriptor.get(
                                "presentation_revision_id"
                            ),
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
                        "dependency_status": source_detail["dependency"]["status"],
                        "prior_atomic_part_ids": atom["dependency"][
                            "prior_atomic_part_ids"
                        ],
                        "required_shared_material_crop_ids": source_detail[
                            "dependency"
                        ]["shared_material_crop_ids"],
                        "dependency_evidence": source_detail.get("dependency_evidence"),
                    }
                )
            _check(
                {image.crop_id for image in detail.shared_images}
                == expected_shared
                == EXPECTED_SHARED_IDS[theme_id],
                "theme shared images were lost or cross-wired",
            )
            _check(
                {row["material_id"] for row in group["shared_context"]["materials"]}
                == expected_shared,
                "theme catalog lost a shared material binding",
            )
            for image in detail.shared_images:
                reader, record = record_sources[image.node_id]
                raw = facade.library_image(image)
                descriptor = next(
                    row
                    for row in reader.detail(image.node_id)["evidence_descriptors"]
                    if row["crop_id"] == image.crop_id
                )
                archived = next(
                    row
                    for row in record["viewed_evidence"]
                    if row["crop_id"] == image.crop_id
                )
                _check(
                    raw == reader.question_crop(image.node_id, image.crop_id).data
                    and _sha(raw) == image.sha256 == descriptor["sha256"],
                    "shared image bytes differ from their explicit source",
                )
                _verify_presentation(
                    evidence, image, raw, descriptor, archived, repaired_crop_ids
                )
                theme_pictures.append(
                    {
                        "node_id": image.node_id,
                        "crop_id": image.crop_id,
                        "role": image.role,
                        "sha256": image.sha256,
                        "bytes": len(raw),
                        "archived_crop_sha256": archived["sha256"],
                        "presentation_revision_id": descriptor.get(
                            "presentation_revision_id"
                        ),
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
            original_prompt_answer_equal=True,
            pixels_match_bound_presentation=True,
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
        report["archived_crop_sha256"] = archived_crops
        report["checks"]["bound_sources_unchanged"] = True
        parts = [part for theme in report["themes"] for part in theme["parts"]]
        report["counts"].update(
            bound_source_files=len(source_before),
            question_image_occurrences=sum(
                p["role"] == "question" for p in all_image_occurrences
            ),
            shared_image_occurrences=sum(
                p["role"] == "shared_material" for p in all_image_occurrences
            ),
            distinct_display_images=len({p["sha256"] for p in all_image_occurrences}),
            presentation_repaired_images=len(repaired_crop_ids),
            question_presentation_repaired_images=len(
                repaired_crop_ids.intersection(set(RECIPES) | set(MARGIN_RECIPES))
            ),
            question_margin_repaired_images=len(
                repaired_crop_ids.intersection(MARGIN_RECIPES)
            ),
            shared_presentation_repaired_images=len(
                repaired_crop_ids.intersection(SHARED_RECIPES)
            ),
            archived_crop_count=len(archived_crops),
            archive_only_shared_images=len(archive_only_crop_ids),
            source_backed_candidate_dependencies=sum(
                part["dependency_status"] == "source_page_backed_candidate_dependency"
                for part in parts
            ),
            unknown_dependencies=sum(
                part["dependency"] == "blocked_pending_review" for part in parts
            ),
        )
        _check(
            len(RECIPES) == 5
            and len(SHARED_RECIPES) == 2
            and len(MARGIN_RECIPES) == 2
            and len(ALL_PRESENTATION_RECIPES) == 9
            and repaired_crop_ids == set(ALL_PRESENTATION_RECIPES),
            "not all five boundary, two margin and two shared display repairs were exercised",
        )
        _check(
            archive_only_crop_ids == ARCHIVE_ONLY_SHARED_IDS,
            "archival shared pages were lost",
        )
        _check(
            report["counts"]["question_image_occurrences"] == 20
            and report["counts"]["shared_image_occurrences"] == 5
            and report["counts"]["distinct_display_images"] == 25
            and report["counts"]["source_backed_candidate_dependencies"] == 14
            and report["counts"]["unknown_dependencies"] == 6,
            "native presentation or dependency denominator changed",
        )
        report["checks"]["presentation_repairs_verified"] = True
        report["checks"]["source_backed_dependencies_remain_candidate"] = True
        report["checks"]["unknown_dependencies_preserved"] = True
        report["checks"]["archive_only_shared_preserved_not_displayed"] = True
        report["checks"]["unrevised_images_match_archived_sha256"] = True
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
