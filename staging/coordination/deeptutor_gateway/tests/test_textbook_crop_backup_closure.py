"""Private archive roundtrips; frozen source reads, no app or recovery runtime."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image

from test_desktop_preparation_drafts import _record
from test_lesson_design import sample
from integrations.deeptutor_shchem_v1 import desktop_backup as backup
from integrations.deeptutor_shchem_v1.desktop_editor_recovery import validate_editor_payload
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    add_resource, block, digest, enable_composition, new_composition, resource,
)
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationManager, PREPARATION_TASK_SCHEMA_VERSION, _normalized_payload, _sha256_json,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_images import image_info, normalize_image_assets
from integrations.deeptutor_shchem_v1.desktop_state import STATE_SCHEMA
from integrations.deeptutor_shchem_v1.desktop_textbook_crop_reading import _origin
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import SCHEMA as CROP_SCHEMA, _json


STAMP = "2026-10-09T08:00:00+00:00"


class FrozenState:
    """Read only the explicit private fixture; constructor never writes."""
    def __init__(self, root):
        self.root = Path(root)

    def snapshot(self):
        return json.loads((self.root / "desktop-state.v1.json").read_bytes())


class FrozenRecovery:
    def __init__(self, root):
        self.path = Path(root) / "recovery/preparation.v1.json"

    def load(self):
        if not self.path.exists():
            return None
        value = json.loads(self.path.read_bytes())
        value["payload"] = validate_editor_payload(value["payload"])
        return value


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def frozen_files(root):
    return {p.relative_to(root).as_posix(): (sha256(p.read_bytes()).hexdigest(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*") if p.is_file()}


def seal_receipt(value):
    value = deepcopy(value)
    provenance = {key: item for key, item in value.items() if key not in {"crop_id", "image_asset", "created_at_utc"}}
    asset = value["image_asset"]
    value["crop_id"] = sha256(_json({"provenance": provenance, "caption": asset["caption"], "purpose": asset["purpose"]})).hexdigest()
    return value


def make_material():
    stream = BytesIO()
    Image.new("RGB", (30, 20), "#549fa5").save(stream, format="PNG")
    raw = stream.getvalue()
    picture_sha = sha256(raw).hexdigest()
    asset = normalize_image_assets([{"asset_id": "IMG-" + picture_sha, "sha256": picture_sha,
        "caption": "合成备份裁片", "source": "独立测试构造的裁选凭据", "purpose": "仅验证备份身份闭包", **image_info(raw)}])[0]
    origin = {"visual_asset_id": "synthetic-page-3", "revision": "b" * 64, "volume_id": "synthetic-volume",
        "source_title": "合成原书", "source_sha256": "a" * 64, "pdf_page": 3, "printed_page": 1}
    recipe = {"renderer": "pypdfium2.page.render.rgb.v1", "scale": 1.0,
        "width": 60, "height": 40, "raster_sha256": "c" * 64}
    receipt = {**origin, "schema_version": CROP_SCHEMA, "recipe": recipe,
        "bounds": {"left": 10, "top": 10, "right": 40, "bottom": 30},
        "page_revision": sha256(_json({"origin": origin, "recipe": recipe})).hexdigest(),
        "image_sha256": picture_sha, "candidate_only": True, "human_reviewed": False,
        "retrieval_ready": False, "generation_allowed": False, "publication_allowed": False,
        "image_asset": asset, "created_at_utc": STAMP}
    receipt = seal_receipt(receipt)
    item = resource("textbook_image", "合成裁片问题", [block("image", "material", asset["asset_id"]),
        block("text", "teacher_note", "原始用途")], {"source_path": "Z:/not-opened/synthetic-original.pdf",
        "source_sha256": origin["source_sha256"], "source_revision": origin["revision"],
        "pdf_pages": [3], "printed_pages": [1]})
    payload = sample()
    payload["lesson_design"] = enable_composition(payload["lesson_design"])
    composition = new_composition()
    payload["lesson_design"]["composition"] = add_resource(composition, item,
        {"page_id": composition["pages"][0], "x": 10, "y": 10, "width": 80, "height": 80})
    payload["image_assets"] = [asset]
    return payload, item, asset, receipt, raw


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "DesktopStateStore", FrozenState)
    monkeypatch.setattr(backup, "PreparationRecoveryStore", FrozenRecovery)
    payload, item, asset, receipt, raw = make_material()
    root = tmp_path / "source"
    state = {"schema_version": STATE_SCHEMA, "window": {}, "drafts": {}, "basket": [], "updated_at": STAMP}
    f = SimpleNamespace(root=root, payload=payload, item=item, asset=asset, receipt=receipt, raw=raw, state=state)
    f.receipt_path = root / backup.PREP / "textbook-crops" / (receipt["crop_id"] + ".json")
    f.image_path = root / backup.PREP / "images" / (asset["sha256"] + ".image")
    def save_state():
        write(root / "desktop-state.v1.json", backup._bytes(state))
    def draft():
        record = _record(payload)
        record.update(lesson_design=deepcopy(payload["lesson_design"]), image_assets=deepcopy(payload["image_assets"]))
        state["drafts"]["prep-synthetic"] = record
        save_state()
    f.save_state, f.draft = save_state, draft
    draft()
    write(f.receipt_path, _json(receipt) + b"\n")
    write(f.image_path, raw)
    return f


def recovery_only(f):
    f.state["drafts"] = {}
    f.save_state()
    value = {"schema_version": "shchem.preparation-recovery.v1", "saved_at": STAMP,
        "dirty": True, "payload": f.payload}
    write(f.root / "recovery/preparation.v1.json", backup._bytes(value))


def add_task(f, status="failed", *, preserved_design=False):
    payload = deepcopy(f.payload) if preserved_design else _normalized_payload(f.payload)
    task = {"schema_version": PREPARATION_TASK_SCHEMA_VERSION, "task_id": "PREP-" + "1" * 32,
        "status": status, "attempt": 0, "payload": payload, "request_sha256": _sha256_json(payload),
        "candidate_only": True, "teacher_review_required": True, "publication_allowed": False,
        "official_claim_allowed": False, "candidate_seed_file": None, "candidate_seed_sha256": None,
        "returned_candidate_file": None, "returned_candidate_sha256": None, "artifacts": []}
    task["record_sha256"] = DesktopPreparationManager._record_digest(task)
    write(f.root / backup.PREP / "tasks" / (task["task_id"] + ".json"), backup._bytes(task))
    return task


def roundtrip(f, tmp_path, **options):
    plan = backup.plan_backup(f.root, **options)
    archive = tmp_path / "lesson.zip"
    backup.create_backup(plan, archive)
    manifest = backup.inspect_backup(archive)
    target = tmp_path / "restored"
    backup.restore_backup(archive, target, expected_manifest=backup.manifest_revision(manifest))
    return plan, archive, manifest, target


def edit_archive(source, target, edit):
    with ZipFile(source) as z:
        entries = {name: z.read(name) for name in z.namelist()}
    manifest = json.loads(entries.pop("manifest.json"))
    edit(entries, manifest)
    manifest["files"] = [{"path": name.removeprefix("files/"), "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
        for name, raw in sorted(entries.items())]
    entries["manifest.json"] = backup._bytes(manifest)
    with ZipFile(target, "w", ZIP_DEFLATED) as z:
        for name, raw in entries.items():
            z.writestr(name, raw)


def receipt_names(plan):
    return [row.name for row in plan.files if backup._CROP.fullmatch(row.name)]


def test_real_private_archive_restore_keeps_original_receipt_and_image_bytes(seeded, tmp_path, monkeypatch):
    f = seeded
    original = deepcopy(f.state)
    before = frozen_files(f.root)
    read_bytes = Path.read_bytes
    open_path = Path.open
    def no_pdf(path):
        assert path.suffix.lower() != ".pdf", "backup must not read an original PDF"
        return read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", no_pdf)
    def no_pdf_open(path, *args, **kwargs):
        assert path.suffix.lower() != ".pdf", "backup must not open an original PDF"
        return open_path(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", no_pdf_open)
    plan, archive, report, restored = roundtrip(f, tmp_path, include_images=True)
    assert report["summary"]["textbook_crop_receipts"] == 1
    assert report["summary"]["textbook_crop_receipts_missing"] == 0
    relative = f.receipt_path.relative_to(f.root)
    assert (restored / relative).read_bytes() == f.receipt_path.read_bytes()
    assert (restored / f.image_path.relative_to(f.root)).read_bytes() == f.raw
    assert FrozenState(restored).snapshot()["drafts"] == original["drafts"]
    assert frozen_files(f.root) == before
    assert not any("source_locations" in row.name or row.name.endswith(".pdf") for row in plan.files)
    assert "不表示已核验原页像素" in "".join(report["warnings"])
    assert "教材裁选凭据 1 / 1" in backup.summary_text(report)
    with ZipFile(archive) as z:
        assert z.read("files/" + relative.as_posix()) == f.receipt_path.read_bytes()


@pytest.mark.parametrize("owner", ["recovery", "task", "draft_recovery_task"])
def test_actual_selected_owner_closure_and_dedup(seeded, tmp_path, owner):
    f = seeded
    if owner == "recovery":
        recovery_only(f)
    elif owner == "task":
        f.state["drafts"] = {}; f.save_state(); add_task(f, preserved_design=True)
    else:
        recovery_only(f); f.draft(); add_task(f, preserved_design=True)
    before = frozen_files(f.root)
    plan, _, manifest, _ = roundtrip(f, tmp_path, include_images=True, include_tasks=True)
    assert len(receipt_names(plan)) == 1
    assert manifest["summary"]["textbook_crops_referenced"] == 1
    assert frozen_files(f.root) == before


@pytest.mark.parametrize("status,include_tasks", [("failed", False), ("running", True)])
def test_unselected_task_cannot_pull_in_receipt(seeded, status, include_tasks):
    f = seeded; f.state["drafts"] = {}; f.save_state(); add_task(f, status, preserved_design=True)
    before = frozen_files(f.root)
    plan = backup.plan_backup(f.root, include_images=True, include_tasks=include_tasks)
    assert receipt_names(plan) == [] and plan.summary["textbook_crops_referenced"] == 0
    assert frozen_files(f.root) == before


def test_normalized_generation_task_has_no_original_receipt_binding(seeded):
    f = seeded; f.state["drafts"] = {}; f.save_state(); add_task(f)
    plan = backup.plan_backup(f.root, include_images=True, include_tasks=True)
    assert plan.summary["tasks"] == 1 and plan.summary["images_included"] == 1
    assert not receipt_names(plan) and plan.summary["textbook_crops_referenced"] == 0


def test_private_draft_and_unrelated_receipt_cannot_expand_scope(seeded):
    f = seeded
    f.state["drafts"]["student-private"] = {"kind": "student-analysis", "payload": deepcopy(f.payload)}
    f.state["teaching_templates"] = {"schema_version": "shchem.teaching-template-library.v1", "records": {}}
    # The valid empty template library contributes no invented image references.
    from integrations.deeptutor_shchem_v1.desktop_teaching_templates import empty_library
    f.state["teaching_templates"] = empty_library(); f.save_state()
    other = deepcopy(f.receipt); other["source_sha256"] = "f" * 64
    other["page_revision"] = sha256(_json({"origin": _origin(other), "recipe": other["recipe"]})).hexdigest()
    other = seal_receipt(other)
    write(f.receipt_path.parent / (other["crop_id"] + ".json"), _json(other))
    plan = backup.plan_backup(f.root, include_images=True)
    assert receipt_names(plan) == [f.receipt_path.relative_to(f.root).as_posix()]


def test_work_copy_text_edits_do_not_change_original_or_receipt(seeded, tmp_path):
    f = seeded
    item = f.payload["lesson_design"]["composition"]["resources"][0]
    old = deepcopy(item["original"])
    item["title"] = "教师新追问"
    next(row for row in item["blocks"] if row["kind"] == "text")["value"] = "教师新备注"
    f.draft()
    _, _, _, restored = roundtrip(f, tmp_path, include_images=True)
    restored_item = FrozenState(restored).snapshot()["drafts"]["prep-synthetic"]["lesson_design"]["composition"]["resources"][0]
    assert restored_item == item and restored_item["original"] == old


def test_receipt_retained_when_picture_bytes_not_selected(seeded, tmp_path):
    plan, _, report, restored = roundtrip(seeded, tmp_path, include_images=False)
    assert len(receipt_names(plan)) == 1 and report["summary"]["images_included"] == 0
    assert any(row.get("kind") == "lesson_image" and row["reason"] == "not_selected" for row in report["references"])
    assert not (restored / seeded.image_path.relative_to(seeded.root)).exists()


@pytest.mark.parametrize("missing", ["receipt", "image"])
def test_old_missing_dependency_remains_backuppable_with_visible_gap(seeded, tmp_path, missing):
    f = seeded
    (f.receipt_path if missing == "receipt" else f.image_path).unlink()
    original = deepcopy(f.state["drafts"])
    plan, _, report, restored = roundtrip(f, tmp_path, include_images=True)
    assert not receipt_names(plan)
    assert report["summary"]["textbook_crop_receipts_missing"] == 1
    assert any(row["kind"] == "textbook_crop_receipt" for row in report["references"])
    assert FrozenState(restored).snapshot()["drafts"] == original


@pytest.mark.parametrize("field,value", [("source_sha256", "f"*64), ("revision", "f"*64),
    ("pdf_page", 4), ("printed_page", 2), ("image_sha256", "f"*64)])
def test_different_binding_is_never_carried_as_the_requested_receipt(seeded, field, value):
    f = seeded; changed = deepcopy(f.receipt); changed[field] = value
    write(f.receipt_path, _json(changed))
    plan = backup.plan_backup(f.root, include_images=True)
    assert not receipt_names(plan) and plan.summary["textbook_crop_receipts_missing"] == 1


@pytest.mark.parametrize("change", ["caption", "candidate", "human", "retrieval", "generation", "publication", "recipe", "page_revision", "bounds"])
def test_matching_tampered_receipt_blocks_before_publication(seeded, tmp_path, change):
    f = seeded; value = deepcopy(f.receipt)
    if change == "caption": value["image_asset"]["caption"] = "changed"
    elif change == "candidate": value["candidate_only"] = False
    elif change in {"human", "retrieval", "generation", "publication"}:
        value[{"human":"human_reviewed", "retrieval":"retrieval_ready", "generation":"generation_allowed", "publication":"publication_allowed"}[change]] = True
    elif change == "recipe": value["recipe"]["width"] += 1
    elif change == "page_revision": value["page_revision"] = "f" * 64
    else: value["bounds"]["right"] += 1
    write(f.receipt_path, _json(value))
    with pytest.raises(backup.BackupError, match="凭据"):
        backup.plan_backup(f.root, include_images=True)
    assert not (tmp_path / "lesson.zip").exists()


def test_two_fully_sealed_matching_receipts_are_ambiguous(seeded):
    f = seeded; other = deepcopy(f.receipt); other["image_asset"]["caption"] = "另一用途"
    other = seal_receipt(other)
    write(f.receipt_path.parent / (other["crop_id"] + ".json"), _json(other))
    with pytest.raises(backup.BackupError, match="多个"):
        backup.plan_backup(f.root, include_images=True)


@pytest.mark.parametrize("kind", ["personal_textbook_page", "knowledge_note_page", "textbook_concept_page"])
def test_supported_source_kinds_retain_exact_receipt_bytes(seeded, tmp_path, kind):
    f = seeded; value = deepcopy(f.receipt)
    value["source_kind"] = kind
    if kind == "personal_textbook_page":
        value.update(source_id="BOOK-" + value["source_sha256"], volume_id=None, printed_page=None)
        card = f.payload["lesson_design"]["composition"]["resources"][0]
        card["original"]["evidence"]["printed_pages"] = "unknown"
        card["original_sha256"] = digest(card["original"])
        f.draft()
    elif kind == "knowledge_note_page":
        value.update(record_id="note-1", section_key="section-1", note_revision="d"*64, directory_revision="e"*64)
    else:
        value.update(record_id="concept-1", concept_revision="d"*64, directory_revision="e"*64, concept_location="section-1")
    value["page_revision"] = sha256(_json({"origin": _origin(value), "recipe": value["recipe"]})).hexdigest()
    value = seal_receipt(value)
    f.receipt_path.unlink()
    path = f.receipt_path.parent / (value["crop_id"] + ".json")
    write(path, _json(value))
    _, _, report, restored = roundtrip(f, tmp_path, include_images=True)
    assert report["summary"]["textbook_crop_receipts"] == 1
    assert (restored / path.relative_to(f.root)).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("change", [
    "personal_source_id", "personal_volume_id", "registered_volume_none", "registered_volume_bool",
    "printed_true", "printed_false", "printed_string", "printed_zero", "printed_negative", "printed_float",
    "pdf_true", "pdf_zero", "revision_type", "source_sha_type",
])
def test_resealed_zip_rejects_ill_typed_source_identity_and_pages(seeded, tmp_path, change):
    f = seeded
    # A personal source has no printed-page mapping; malformed printed values
    # used to alias that same 'unknown' Original even after every seal matched.
    value = deepcopy(f.receipt)
    value.update(source_kind="personal_textbook_page", source_id="BOOK-" + value["source_sha256"],
        volume_id=None, printed_page=None)
    value["page_revision"] = sha256(_json({"origin": _origin(value), "recipe": value["recipe"]})).hexdigest()
    value = seal_receipt(value)
    f.receipt_path.unlink()
    f.receipt_path = f.receipt_path.parent / (value["crop_id"] + ".json")
    write(f.receipt_path, _json(value))
    card = f.payload["lesson_design"]["composition"]["resources"][0]
    card["original"]["evidence"]["printed_pages"] = "unknown"
    card["original_sha256"] = digest(card["original"])
    f.draft()
    good, bad = tmp_path / "valid.zip", tmp_path / "forged.zip"
    backup.create_backup(backup.plan_backup(f.root, include_images=True), good)
    assert backup.inspect_backup(good)["summary"]["textbook_crop_receipts"] == 1
    receipt_name = "files/" + f.receipt_path.relative_to(f.root).as_posix()
    forged = {}
    def edit(entries, _manifest):
        record = json.loads(entries.pop(receipt_name))
        if change == "personal_source_id": record["source_id"] = "BOOK-" + "f" * 64
        elif change == "personal_volume_id": record["volume_id"] = "wrong-volume"
        elif change.startswith("registered_volume"):
            record.pop("source_kind"); record.pop("source_id")
            record["volume_id"] = None if change.endswith("none") else False
        elif change.startswith("printed_"):
            record["printed_page"] = {"true": True, "false": False, "string": "1", "zero": 0,
                "negative": -1, "float": 1.5}[change.removeprefix("printed_")]
        elif change.startswith("pdf_"): record["pdf_page"] = True if change.endswith("true") else 0
        elif change == "revision_type": record["revision"] = 123
        else: record["source_sha256"] = 123
        record["page_revision"] = sha256(_json({"origin": _origin(record), "recipe": record["recipe"]})).hexdigest()
        record = seal_receipt(record)
        name = backup.PREP + "textbook-crops/" + record["crop_id"] + ".json"
        raw = _json(record)
        entries["files/" + name] = raw
        forged.update(name=name, raw=raw)
    edit_archive(good, bad, edit)
    with pytest.raises(backup.BackupError, match="凭据"):
        backup._crop_receipt(forged["name"], forged["raw"])
    before = frozen_files(f.root)
    with pytest.raises(backup.BackupError, match="凭据"):
        backup.inspect_backup(bad)
    with pytest.raises(backup.BackupError, match="凭据"):
        backup.restore_backup(bad, tmp_path / "restored", expected_manifest="0" * 64)
    assert frozen_files(f.root) == before
    assert not (tmp_path / "restored").exists() and not list(tmp_path.glob(".shchem-restore-*"))


def test_same_picture_in_distinct_books_keeps_both_source_identities(seeded, tmp_path):
    f = seeded; second = deepcopy(f.receipt); second["source_sha256"] = "f"*64
    second["page_revision"] = sha256(_json({"origin": _origin(second), "recipe": second["recipe"]})).hexdigest()
    second = seal_receipt(second)
    write(f.receipt_path.parent / (second["crop_id"] + ".json"), _json(second))
    evidence = deepcopy(f.item["original"]["evidence"])
    evidence.update(source_sha256="f"*64, source_path="Z:/not-opened/another-original.pdf")
    other = resource("textbook_image", "同字节另一原书", [block("image", "material", f.asset["asset_id"])], evidence)
    composition = f.payload["lesson_design"]["composition"]
    f.payload["lesson_design"]["composition"] = add_resource(composition, other,
        {"page_id": composition["pages"][0], "x": 100, "y": 10, "width": 80, "height": 80})
    f.draft()
    _, _, report, _ = roundtrip(f, tmp_path, include_images=True)
    assert report["summary"]["textbook_crop_receipts"] == 2
    assert report["summary"]["textbook_crops_referenced"] == 2
    assert report["summary"]["images_included"] == 1


def test_unpacked_receipt_is_rechecked_before_restore_marker_publication(seeded, tmp_path, monkeypatch):
    f = seeded; archive = tmp_path / "good.zip"
    backup.create_backup(backup.plan_backup(f.root, include_images=True), archive)
    manifest = backup.inspect_backup(archive)
    closure = backup._crop_closure
    def mutate_unpacked(*args, **kwargs):
        temporary = list(tmp_path.glob(".shchem-restore-*"))
        if temporary:
            path = temporary[0] / f.receipt_path.relative_to(f.root)
            value = json.loads(path.read_bytes()); value["publication_allowed"] = True
            path.write_bytes(_json(value))
        return closure(*args, **kwargs)
    monkeypatch.setattr(backup, "_crop_closure", mutate_unpacked)
    with pytest.raises(backup.BackupError, match="凭据"):
        backup.restore_backup(archive, tmp_path / "restored", expected_manifest=backup.manifest_revision(manifest))
    assert not (tmp_path / "restored").exists() and not list(tmp_path.glob(".shchem-restore-*"))


@pytest.mark.parametrize("change", ["extra_receipt", "image_bytes", "forged_gate", "forged_geometry", "duplicate_json", "source_locations"])
def test_zip_semantics_rechecked_after_attacker_rehashes_manifest(seeded, tmp_path, change):
    f = seeded; good = tmp_path / "good.zip"; bad = tmp_path / "bad.zip"
    backup.create_backup(backup.plan_backup(f.root, include_images=True), good)
    receipt_name = "files/" + f.receipt_path.relative_to(f.root).as_posix()
    def edit(entries, manifest):
        if change == "image_bytes":
            entries["files/" + f.image_path.relative_to(f.root).as_posix()] = b"not-original-image"
        elif change == "source_locations":
            entries["files/textbook-source-locations.v1.json"] = b"{}"
        elif change == "duplicate_json":
            raw = entries[receipt_name]
            entries[receipt_name] = raw.rstrip()[:-1] + b',"candidate_only":true}'
        else:
            value = json.loads(entries[receipt_name])
            if change == "extra_receipt":
                value["source_sha256"] = "f" * 64
                value["page_revision"] = sha256(_json({"origin": _origin(value), "recipe": value["recipe"]})).hexdigest()
            elif change == "forged_gate": value["generation_allowed"] = True
            else: value["bounds"]["right"] += 1
            value = seal_receipt(value)
            if change != "extra_receipt": del entries[receipt_name]
            entries["files/" + backup.PREP + "textbook-crops/" + value["crop_id"] + ".json"] = _json(value)
    edit_archive(good, bad, edit)
    with pytest.raises((backup.BackupError, ValueError)):
        backup.inspect_backup(bad)
    with pytest.raises((backup.BackupError, ValueError)):
        backup.restore_backup(bad, tmp_path / "restored", expected_manifest="0" * 64)
    assert not (tmp_path / "restored").exists() and not list(tmp_path.glob(".shchem-restore-*"))


def test_legacy_zip_without_receipt_reports_derived_gap(seeded, tmp_path):
    f = seeded; good = tmp_path / "good.zip"; legacy = tmp_path / "legacy.zip"
    backup.create_backup(backup.plan_backup(f.root, include_images=True), good)
    def edit(entries, manifest):
        for name in list(entries):
            if "textbook-crops/" in name: del entries[name]
        for key in list(manifest["summary"]):
            if key.startswith("textbook_crop"): del manifest["summary"][key]
        manifest["references"] = []; manifest["warnings"] = []
    edit_archive(good, legacy, edit)
    report = backup.inspect_backup(legacy)
    assert report["summary"]["textbook_crop_receipts_missing"] == 1
    backup.restore_backup(legacy, tmp_path / "restored", expected_manifest=backup.manifest_revision(report))


def test_cancellation_and_changed_source_receipt_leave_existing_data(seeded, tmp_path):
    f = seeded; before = frozen_files(f.root)
    plan = backup.plan_backup(f.root, include_images=True)
    with pytest.raises(backup.BackupCancelled):
        backup.create_backup(plan, tmp_path / "cancel.zip", cancel=lambda: True)
    assert frozen_files(f.root) == before and not (tmp_path / "cancel.zip").exists()
    write(f.receipt_path, f.receipt_path.read_bytes() + b" ")
    with pytest.raises(backup.BackupError, match="变化"):
        backup.create_backup(plan, tmp_path / "changed.zip")
    assert not (tmp_path / "changed.zip").exists() and not list(tmp_path.glob(".shchem-backup-*"))
