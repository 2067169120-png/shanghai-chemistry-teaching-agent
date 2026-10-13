"""Explicit local lesson backups. Restore only into a new personal-state directory.

No recursive copying of the user's state tree: student domains, model settings,
credentials, logs, executables and the original question bank are never selected.
Source documents are not inferred from names; missing lesson images are rebound
only after their original content hash and dimensions have been verified.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from .desktop_editor_recovery import PreparationRecoveryStore
from .desktop_preparation_drafts import PreparationDraftService
from .desktop_preparation_images import normalize_image_assets, verify_image_bytes
from .desktop_state import STATE_SCHEMA, DesktopStateStore, _reject_sensitive_fields, utc_now
from .desktop_work_organization import TERMINAL_TASKS, metadata

SCHEMA = "shchem.lesson-backup.v1"
RESTORED_SCHEMA = "shchem.restored-profile.v1"
MARKER = "restored-profile.json"
MAX_FILES = 20000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MANIFEST_LIMIT = 16 * 1024 * 1024
PREP = "tasks/preparation-v1/"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TASK = r"PREP-[0-9a-f]{32}"
_IMAGE = re.compile(r"tasks/preparation-v1/images/[0-9a-f]{64}\.image\Z")
_CROP = re.compile(r"tasks/preparation-v1/textbook-crops/[0-9a-f]{64}\.json\Z")
_CROP_LIMIT = 128 * 1024
_TASK_FILE = re.compile(rf"tasks/preparation-v1/tasks/{_TASK}\.json\Z")
_AUX_FILE = re.compile(rf"tasks/preparation-v1/(seeds|returned)/{_TASK}(?:\.attempt-[0-9]{{4}})?\.candidate\.json\Z")
_NODE_OUTPUT = re.compile(r"tasks/preparation-v1/node-exports/OUT-[0-9a-f]{32}/(?:lesson_presentation\.pptx|lesson_plan\.docx|student_worksheet\.docx|lesson_canvas\.pdf|student_canvas\.pdf|student_presentation\.pptx)\Z")
_SUFFIXES = {".json", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf", ".docx", ".pptx", ".txt", ".md", ".html"}


class BackupError(ValueError):
    """Actionable message without exposing credentials or unrelated file paths."""
    def __init__(self, message):
        self.message_zh = message
        super().__init__(message)


class BackupCancelled(BackupError):
    pass


def _check_cancel(cancel):
    if cancel and cancel():
        raise BackupCancelled("操作已取消，原资料未改变。")


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise BackupError("备份JSON包含重复字段。")
            value[key] = item
        return value
    def constant(_value):
        raise BackupError("备份JSON含无效数字。")
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BackupError("备份JSON无法读取或格式不正确。") from exc


def _relative(name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(ord(c) < 32 for c in name) or name.startswith("/")
            or any(p in {"", ".", ".."} or p.endswith((".", " ")) for p in name.split("/"))):
        raise BackupError("备份包含不安全的相对路径。")
    for part in name.split("/"):
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
            raise BackupError("备份路径含Windows保留名称。")
    path = PurePosixPath(name)
    if path.as_posix() != name:
        raise BackupError("备份路径格式不正确。")
    return name


def _allowed(name):
    _relative(name)
    if _SOURCE_BATCH.fullmatch(name) or _SOURCE_ARCHIVE.fullmatch(name):
        return True
    if name in {"desktop-state.v1.json", "recovery/preparation.v1.json"}:
        return True
    if _IMAGE.fullmatch(name) or _CROP.fullmatch(name) or _TASK_FILE.fullmatch(name) or _AUX_FILE.fullmatch(name) or _NODE_OUTPUT.fullmatch(name):
        return True
    parts = name.split("/")
    return (name.startswith(PREP + "artifacts/") and len(parts) >= 6
            and re.fullmatch(_TASK, parts[3]) is not None and Path(name).suffix.lower() in _SUFFIXES)


def _source(root, name):
    _relative(name)
    current = root
    for part in name.split("/"):
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise BackupError("备份来源包含链接目录或链接文件，未跟随。")
    if not current.resolve().is_relative_to(root.resolve()):
        raise BackupError("来源不在当前个人目录中。")
    return current


def _hash_file(path, cancel=None):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _check_cancel(cancel)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise BackupError("单个备份文件超过128MB，请单独保管该文件。")
            digest.update(chunk)
    return digest.hexdigest(), size


@dataclass(frozen=True)
class PlannedFile:
    name: str
    sha256: str
    size: int
    source: Path | None = None
    data: bytes | None = None


@dataclass(frozen=True)
class BackupPlan:
    files: tuple[PlannedFile, ...]
    summary: dict
    references: tuple[dict, ...]
    warnings: tuple[str, ...]

    def report(self):
        return {"summary": deepcopy(self.summary), "files": [
            {"path": f.name, "sha256": f.sha256, "bytes": f.size} for f in self.files],
            "references": deepcopy(list(self.references)), "warnings": list(self.warnings)}



# Explicit whole-batch dependencies of validated course Originals. This is the
# existing visual-import protocol, not a new course or source migration schema.
_SOURCE_BATCH = re.compile(r'visual-import-v2/batches/DESKTOPBATCH-[0-9a-f]{32}\.json\Z')
_SOURCE_ARCHIVE = re.compile(r'visual-import-v2/sources/[0-9a-f]{64}\.(?:docx|pdf|png|jpg|webp)\Z')
_SOURCE_EXTENSIONS = {'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp',
    'application/pdf':'.pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document':'.docx'}

def _course_source_bindings(inputs):
    from .desktop_lesson_composition import validate_resource
    from .desktop_handout_sources import validate_handout_source
    result = {};word_hashes=set()
    def visit(value):
        if isinstance(value, Mapping):
            if isinstance(value.get('original'),dict):
                item=validate_resource(value)
                if item['original']['kind'] in ('handout_knowledge','handout_image','complete_question'):
                    word_hashes.add(item['original']['evidence']['source_sha256'])
                source=item['original']['evidence'].get('handout_source')
                if source is not None and source.get('schema')=='shchem.handout-knowledge-source.v1':
                    source=validate_handout_source(source)
                    key=(source['batch_id'],source['source_id'])
                    # Restored courses keep their immutable old provenance path;
                    # fresh selections may point at the current exact archive.
                    # Only that location may differ for the same source version.
                    if key in result and (
                            {k:v for k,v in result[key].items() if k != 'source_path'} !=
                            {k:v for k,v in source.items() if k != 'source_path'}):
                        raise BackupError('课程中同一讲义的来源版本不一致，请先核对。')
                    result.setdefault(key,source)
                return
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(inputs)
    if word_hashes-set(source['source_sha256'] for source in result.values()):
        raise BackupError('所选作品的原讲义没有完整已封存批次身份；请先关联该讲义，未从路径或相同SHA猜批次。')
    return result

def _source_descriptors(state):
    from .desktop_facade import _VISUAL_IMPORT_DRAFT_SCHEMA, _VISUAL_IMPORT_DRAFT_PREFIX
    return {identity:record for identity,record in state['drafts'].items()
        if isinstance(record,dict) and record.get('schema_version')==_VISUAL_IMPORT_DRAFT_SCHEMA
        and record.get('kind')=='desktop_visual_import_v2' and identity==_VISUAL_IMPORT_DRAFT_PREFIX+str(record.get('batch_id',''))}

def _source_closure(state,names,read,*,cancel=None):
    """Validate the same native descriptor/manifest projection and all archives.

    Byte reads can be from a checked ZIP or the selected profile. No old
    provenance path is opened; metadata and roles remain exactly as archived.
    """
    from .desktop_facade import (DesktopWorkbenchFacade, _canonical_digest,
        _visual_source_projection, _VISUAL_IMPORT_DRAFT_PREFIX, _VISUAL_IMPORT_DRAFT_SCHEMA)
    from .desktop_visual_import_v2 import DesktopSourceFile
    from .desktop_handout_sources import _current_document
    descriptors=_source_descriptors(state)
    source_names={n for n in names if _SOURCE_BATCH.fullmatch(n) or _SOURCE_ARCHIVE.fullmatch(n)}
    if not descriptors:
        if source_names:raise BackupError('备份含未绑定本课的来源归档。')
        return {'source_batches':0,'source_documents':0,'source_archive_files':0,'source_archive_bytes':0}
    bindings=_course_source_bindings([r for r in state['drafts'].values() if isinstance(r,dict) and r.get('kind')=='preparation'])
    required={source['batch_id'] for source in bindings.values()}
    actual={record['batch_id'] for record in descriptors.values()}
    if actual != required:
        raise BackupError('来源批次必须完整对应本课实际使用的讲义；未包含无关批次。')
    wanted=set();documents=0;archive_bytes=0;all_sources={}
    for identity,record in descriptors.items():
        _check_cancel(cancel)
        batch=record['batch_id'];raw_sources=record.get('sources');projection=_visual_source_projection(raw_sources)
        if (identity!=_VISUAL_IMPORT_DRAFT_PREFIX+batch or record.get('schema_version')!=_VISUAL_IMPORT_DRAFT_SCHEMA
                or projection is None or record.get('source_closure_sha256')!=_canonical_digest(raw_sources)
                or len({r['source_file_id'] for r in raw_sources})!=len(raw_sources)):
            raise BackupError('本课来源批次目录或原来源身份无法完整核验。')
        receipt=DesktopWorkbenchFacade._visual_import_receipt_from_saved(record)
        if receipt.batch_id != batch or receipt.source_count!=len(raw_sources):
            raise BackupError('本课来源批次回执与完整来源目录不一致。')
        manifest_name='visual-import-v2/batches/'+batch+'.json';wanted.add(manifest_name)
        if manifest_name not in names:raise BackupError('本课完整来源批次清单缺失。')
        manifest=_json(read(manifest_name))
        if (not isinstance(manifest,dict) or manifest.get('batch_id')!=batch
                or manifest.get('candidate_only') is not True or manifest.get('central_question_bank_write') is not False
                or not isinstance(manifest.get('plan'),Mapping)
                or _visual_source_projection(manifest['plan'].get('sources'))!=projection
                or manifest.get('source_type')!=record.get('source_type')):
            raise BackupError('本课批次处理清单与完整原来源目录不一致。')
        for row in raw_sources:
            _check_cancel(cancel)
            name='visual-import-v2/sources/'+row['source_sha256']+_SOURCE_EXTENSIONS[row['mime_type']]
            if row.get('archive_relative_path')!=name.removeprefix('visual-import-v2/') or name not in names:
                raise BackupError('本课来源批次有原文件缺失，未把不完整批次作为可复用来源。')
            raw=read(name)
            if len(raw)!=row['size_bytes'] or hashlib.sha256(raw).hexdigest()!=row['source_sha256']:
                raise BackupError('本课来源归档字节或大小已变化。')
            native=DesktopSourceFile(role=row['role'],order_index=row['order_index'],filename=row['filename'],mime_type=row['mime_type'],
                content=raw,group_id=row['group_id'],source_file_id=row['source_file_id'])
            all_sources[(batch,row['source_file_id'])]=native
            if name not in wanted:archive_bytes+=len(raw)
            wanted.add(name);documents+=1
    if wanted!=source_names:raise BackupError('备份来源文件不是本课完整批次的精确闭包。')
    class CurrentArchive:
        def _imported_word_source(self,batch,source_id):
            try:return all_sources[(batch,source_id)]
            except KeyError as exc:raise BackupError('本课绑定的原来源ID缺失。') from exc
    current=CurrentArchive()
    for source in bindings.values():
        _current_document(current,source)  # Native exact ID/name/role/content checks.
    return {'source_batches':len(descriptors),'source_documents':documents,
        'source_archive_files':len(wanted)-len(descriptors),'source_archive_bytes':archive_bytes}

def _plan_source_dependencies(root,selected,*,source_state_root=None,cancel=None):
    """Read only explicitly selected whole batches; never scan/copy a profile."""
    from .desktop_facade import DesktopWorkbenchFacade, _VISUAL_IMPORT_DRAFT_PREFIX
    from .desktop_paths import DesktopPaths
    from types import SimpleNamespace
    source_root=Path(source_state_root).resolve() if source_state_root is not None else root
    bindings=_course_source_bindings(selected)
    required={source['batch_id'] for source in bindings.values()}
    if not _source(source_root,'desktop-state.v1.json').is_file():
        raise BackupError('原讲义所在工作台的状态文件不存在，请选择已登记原资料的个人目录。')
    source_state=DesktopStateStore(source_root).snapshot()
    facade=DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths=DesktopPaths.from_workspace(source_root,state_root=source_root)
    facade._state=SimpleNamespace(snapshot=lambda:deepcopy(source_state))
    files={}
    for batch in sorted(required):
        _check_cancel(cancel)
        descriptor=facade._saved_visual_import_batch(batch)
        manifest_name='visual-import-v2/batches/'+batch+'.json';manifest_path=_source(source_root,manifest_name)
        native_manifest,revision=facade._import_batch_manifest(descriptor)
        raw=manifest_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=revision or _json(raw)!=native_manifest:
            raise BackupError('来源批次清单在备份规划期间变化，请刷新。')
        files[manifest_name]=PlannedFile(manifest_name,revision,len(raw),source=manifest_path)
        # Reuse the full native archive validation (not scoped one-attachment read).
        for row in descriptor['sources']:_source(source_root,'visual-import-v2/'+row['archive_relative_path'])
        sources=facade._restore_visual_import_sources(descriptor)
        if len(sources)!=len(descriptor['sources']):raise BackupError('完整来源批次归档数量不一致。')
        for row,native in zip(descriptor['sources'],sources,strict=True):
            _check_cancel(cancel)
            name='visual-import-v2/'+row['archive_relative_path'];path=_source(source_root,name)
            digest,size=_hash_file(path,cancel)
            if digest!=row['source_sha256'] or size!=row['size_bytes'] or native.effective_source_file_id!=row['source_file_id']:
                raise BackupError('来源批次归档在规划期间变化。')
            files[name]=PlannedFile(name,digest,size,source=path)
        selected['drafts'][_VISUAL_IMPORT_DRAFT_PREFIX+batch]=deepcopy(descriptor)
    totals=_source_closure(selected,set(files),lambda name:files[name].source.read_bytes(),cancel=cancel)
    return files,totals


def _task_reader(root):
    """Use existing read validators without initializing/recovering active tasks."""
    from .desktop_preparation import DesktopPreparationManager
    # Match the normal manager constructor: Windows TEMP may use 8.3 aliases.
    root = Path(root).resolve()
    reader = object.__new__(DesktopPreparationManager)
    reader.root = root / PREP
    reader.tasks_root = reader.root / "tasks"
    reader.seeds_root = reader.root / "seeds"
    reader.artifacts_root = reader.root / "artifacts"
    reader.returned_root = reader.root / "returned"
    return reader


def _assets(value):
    if isinstance(value, Mapping):
        if "image_assets" in value:
            for asset in normalize_image_assets(value["image_assets"]):
                yield asset
        for key, nested in value.items():
            if key != "image_assets":
                yield from _assets(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _assets(nested)


def _design_outputs(value):
    """Only bundles registered in included lesson records, never a directory walk."""
    from .desktop_lesson_design import SCHEMA as DESIGN_SCHEMA, validate_design
    from .desktop_lesson_composition import DESIGN_SCHEMA as COMPOSITION_DESIGN_SCHEMA
    if isinstance(value, Mapping):
        if value.get("schema_version") in (DESIGN_SCHEMA, COMPOSITION_DESIGN_SCHEMA):
            yield from validate_design(value)["exports"]
        else:
            for nested in value.values():
                yield from _design_outputs(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _design_outputs(nested)


def _node_output_totals(names):
    from .desktop_lesson_output import FILES, COMPOSITION_FILES
    bundles = {}
    for name in names:
        if _NODE_OUTPUT.fullmatch(name):
            folder, filename = name.rsplit("/", 1)
            bundles.setdefault(folder, set()).add(filename)
    return {"node_output_bundles": sum(files in (set(FILES),set(COMPOSITION_FILES)) for files in bundles.values()),
            "node_output_files": sum(len(files) for files in bundles.values())}


def _crop_bindings(value):
    """Only a validated resource's sealed Original can request a crop receipt."""
    from .desktop_lesson_composition import validate_resource
    if isinstance(value, Mapping):
        if isinstance(value.get("original"), dict) and value["original"].get("kind") == "textbook_image":
            card = validate_resource(value)
            original = card["original"]
            images = [row["value"] for row in original["blocks"] if row["kind"] == "image"]
            evidence = original["evidence"]
            if len(images) != 1 or len(evidence["pdf_pages"]) != 1:
                raise BackupError("教材裁片须保留单张图片与单个原页的完整绑定。")
            binding = {"asset_id": images[0], **{key: deepcopy(evidence[key]) for key in
                ("source_sha256", "source_revision", "pdf_pages", "printed_pages")}}
            if "crop_id" in evidence:
                binding["crop_id"] = evidence["crop_id"]
            yield binding
            return
        for nested in value.values():
            yield from _crop_bindings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _crop_bindings(nested)


def _crop_key(binding):
    return hashlib.sha256(_bytes(binding)).hexdigest()


def _receipt_binding(record, *, include_crop_id=False):
    printed = record.get("printed_page")
    binding = {"asset_id": "IMG-" + str(record.get("image_sha256", "")),
        "source_sha256": record.get("source_sha256"), "source_revision": record.get("revision"),
        "pdf_pages": [record.get("pdf_page")],
        "printed_pages": [printed] if type(printed) is int else "unknown"}
    if include_crop_id:
        binding["crop_id"] = record.get("crop_id")
    return binding


def _receipt_keys(record, bindings):
    """Match exact new crop identities and all legacy candidates independently."""
    return tuple(key for key in (
        _crop_key(_receipt_binding(record, include_crop_id=True)),
        _crop_key(_receipt_binding(record)),
    ) if key in bindings)


def _crop_receipt(name, raw):
    """Validate sealed receipt bytes, without reading or authorizing an original PDF.

    This is the bytes form of the crop reader's receipt contract. ZIP validation
    additionally rejects duplicate JSON keys and checks the recorded page recipe.
    """
    from .desktop_textbook_crop_reading import _origin
    from .desktop_textbook_preparation_crops import SCHEMA as CROP_SCHEMA, _bounds, _json as crop_json
    if not _CROP.fullmatch(name) or len(raw) > _CROP_LIMIT:
        raise BackupError("教材裁选凭据路径或大小不正确。")
    try:
        value = _json(raw)
        if not isinstance(value, dict) or not isinstance(value.get("created_at_utc"), str):
            raise ValueError("timestamp")
        _reject_sensitive_fields(value)
        record = {key: item for key, item in value.items() if key != "created_at_utc"}
        asset = normalize_image_assets([record.get("image_asset")])[0]
        provenance = {key: item for key, item in record.items() if key not in {"crop_id", "image_asset"}}
        if (record.get("schema_version") != CROP_SCHEMA
                or Path(name).stem != record.get("crop_id")
                or hashlib.sha256(crop_json({"provenance": provenance,
                    "caption": asset["caption"], "purpose": asset["purpose"]})).hexdigest() != record["crop_id"]
                or record.get("image_sha256") != asset["sha256"]
                or record.get("candidate_only") is not True
                or any(record.get(key) is not False for key in
                    ("human_reviewed", "retrieval_ready", "generation_allowed", "publication_allowed"))):
            raise ValueError("sealed receipt")
        origin, recipe = _origin(record), record.get("recipe")
        # The seal proves consistency, not that a self-rehashed source identity
        # is well typed. Match the locator's PDF-free identity requirements.
        if (any(not isinstance(origin[key], str) or not _HASH.fullmatch(origin[key])
                for key in ("source_sha256", "revision"))
                or type(origin["pdf_page"]) is not int or origin["pdf_page"] <= 0
                or (origin["printed_page"] is not None and
                    (type(origin["printed_page"]) is not int or origin["printed_page"] <= 0))):
            raise ValueError("source identity or page type")
        if origin.get("source_kind") == "personal_textbook_page":
            if origin["source_id"] != "BOOK-" + origin["source_sha256"] or origin["volume_id"] is not None:
                raise ValueError("personal source identity")
        elif not isinstance(origin["volume_id"], str) or not origin["volume_id"]:
            raise ValueError("registered source identity")
        if (not isinstance(recipe, dict)
                or set(recipe) != {"renderer", "scale", "width", "height", "raster_sha256"}
                or recipe["renderer"] != "pypdfium2.page.render.rgb.v1"
                or any(type(recipe[key]) is not int or recipe[key] <= 0 for key in ("width", "height"))
                or type(recipe["scale"]) not in (int, float) or not 0 < recipe["scale"] < float("inf")
                or not _HASH.fullmatch(str(recipe["raster_sha256"]))
                or record.get("page_revision") != hashlib.sha256(crop_json({"origin": origin, "recipe": recipe})).hexdigest()):
            raise ValueError("page recipe")
        left, top, right, bottom = _bounds(record.get("bounds"), recipe["width"], recipe["height"])
        if (asset["width"], asset["height"]) != (right - left, bottom - top):
            raise ValueError("crop dimensions")
        return record
    except (ValueError, TypeError, KeyError, IndexError, RuntimeError) as exc:
        raise BackupError("教材裁选凭据与原图片、原页版本或资格绑定不一致；未把它当作有效凭据。") from exc


def _crop_inputs_assets(inputs):
    bindings = {_crop_key(row): row for row in _crop_bindings(inputs)}
    assets = {}
    for asset in _assets(inputs):
        old = assets.get(asset["asset_id"])
        if old and any(old[key] != asset[key] for key in ("sha256", "width", "height", "content_type")):
            raise BackupError("同一教材图片身份的尺寸或格式不一致。")
        assets[asset["asset_id"]] = asset
    return bindings, assets


def _check_crop_asset(record, asset, raw=None):
    receipt_asset = record["image_asset"]
    if asset is None or any(receipt_asset[key] != asset[key] for key in
            ("asset_id", "sha256", "width", "height", "content_type")):
        raise BackupError("教材裁选凭据与课内图片记录不一致。")
    if raw is not None:
        verify_image_bytes(asset, raw)


def _plan_crop_receipts(root, inputs, cancel):
    bindings, assets = _crop_inputs_assets(inputs)
    selected = {}
    if not bindings:
        return selected
    folder = _source(root, PREP + "textbook-crops")
    matches = {key: [] for key in bindings}
    if folder.is_dir():
        for index, path in enumerate(sorted(folder.glob("*.json"))):
            _check_cancel(cancel)
            if index >= MAX_FILES:
                raise BackupError("教材裁选凭据过多，请先整理后备份。")
            name = path.relative_to(root).as_posix()
            if not _CROP.fullmatch(name):
                continue
            path = _source(root, name)
            if not path.is_file() or path.stat().st_size > _CROP_LIMIT:
                continue
            raw = path.read_bytes()
            try:
                candidate = _json(raw)
            except BackupError:
                continue
            if not isinstance(candidate, dict):
                continue
            keys = _receipt_keys(candidate, bindings)
            if not keys:
                continue
            record = _crop_receipt(name, raw)
            for key in keys:
                _check_crop_asset(record, assets.get(bindings[key]["asset_id"]))
                matches[key].append((name, raw))
    for key, rows in matches.items():
        if len(rows) > 1:
            raise BackupError("同一教材裁片对应多个裁选凭据，请明确核对后再备份。")
        if not rows:
            continue
        asset = assets[bindings[key]["asset_id"]]
        image = _source(root, PREP + "images/" + asset["sha256"] + ".image")
        if not image.is_file():
            continue  # The existing missing-image reference remains visible.
        if image.stat().st_size > 10 * 1024 * 1024:
            raise BackupError("教材裁片图片超过10MB，请先检查。")
        verify_image_bytes(asset, image.read_bytes())
        name, raw = rows[0]
        selected[name] = PlannedFile(name, hashlib.sha256(raw).hexdigest(), len(raw), source=_source(root, name))
    return selected


def _crop_closure(inputs, names, read, references, warnings, *, cancel=None):
    """Recheck only packaged resources/receipts; no PDF or location aliases."""
    bindings, assets = _crop_inputs_assets(inputs)
    matches = {key: [] for key in bindings}
    for name in sorted(name for name in names if _CROP.fullmatch(name)):
        _check_cancel(cancel)
        record = _crop_receipt(name, read(name))
        keys = _receipt_keys(record, bindings)
        if not keys:
            raise BackupError("备份含未被已选教材资源引用的裁选凭据。")
        image_name = PREP + "images/" + record["image_sha256"] + ".image"
        for key in keys:
            asset = assets.get(bindings[key]["asset_id"])
            _check_crop_asset(record, asset, read(image_name) if image_name in names else None)
            matches[key].append(name)
    if any(len(rows) > 1 for rows in matches.values()):
        raise BackupError("备份中同一教材裁片有多个裁选凭据，未选择其中任意一个。")
    # Derive gaps from the actual selected records, including legacy archives.
    references[:] = [row for row in references if not isinstance(row, dict)
        or row.get("kind") != "textbook_crop_receipt"]
    missing = [key for key, rows in matches.items() if not rows]
    for key in missing:
        references.append({"kind": "textbook_crop_receipt", "key": key,
            "binding": bindings[key], "reason": "未找到可核验的教材裁选凭据；课件保留，恢复后回原页仍需完整凭据和明确原书关联。"})
    note = "教材裁选凭据只校验课内图片、原书摘要、页码及版本绑定；不含原PDF或本机位置关联，不表示已核验原页像素。"
    if bindings and note not in warnings:
        warnings.append(note)
    return {"textbook_crops_referenced": len(bindings),
        "textbook_crop_receipts": sum(bool(rows) for rows in matches.values()),
        "textbook_crop_receipts_missing": len(missing)}


def _archive_crop_inputs(archive, names, state):
    inputs = [state]
    if "recovery/preparation.v1.json" in names:
        from .desktop_editor_recovery import SCHEMA as RECOVERY_SCHEMA, MAX_BYTES, validate_editor_payload
        name = "files/recovery/preparation.v1.json"
        if archive.getinfo(name).file_size > MAX_BYTES:
            raise BackupError("备份中的恢复副本过大。")
        recovery = _json(archive.read(name))
        if (not isinstance(recovery, dict) or set(recovery) != {"schema_version", "saved_at", "dirty", "payload"}
                or recovery["schema_version"] != RECOVERY_SCHEMA or type(recovery["dirty"]) is not bool
                or not isinstance(recovery["saved_at"], str)):
            raise BackupError("备份中的恢复副本格式不完整。")
        inputs.append(validate_editor_payload(recovery["payload"]))
    for name in names:
        if not _TASK_FILE.fullmatch(name):
            continue
        from .desktop_preparation import DesktopPreparationManager, PREPARATION_TASK_SCHEMA_VERSION, _sha256_json
        task = _json(archive.read("files/" + name))
        if (not isinstance(task, dict) or task.get("schema_version") != PREPARATION_TASK_SCHEMA_VERSION
                or task.get("task_id") != Path(name).stem or task.get("status") not in TERMINAL_TASKS
                or task.get("request_sha256") != _sha256_json(task.get("payload"))
                or task.get("record_sha256") != DesktopPreparationManager._record_digest(task)):
            raise BackupError("备份中的备课任务版本或结束状态无法核验。")
        # Match the persisted reader's payload, rather than re-normalizing it
        # through the generation request projection (which omits lesson_design).
        if not isinstance(task.get("payload"), Mapping):
            raise BackupError("备份中的备课任务内容不完整。")
        inputs.append(task["payload"])
    return inputs


def plan_backup(state_root, *, include_images=False, include_tasks=False, include_source_dependencies=False, source_state_root=None, cancel=None):
    """Freeze the selected records. Copy operations later recheck file versions."""
    root = Path(state_root).resolve()
    _source(root, "desktop-state.v1.json")
    snapshot = DesktopStateStore(root).snapshot()
    records, warnings, refs, images = {}, [], [], {}
    for identity, record in snapshot["drafts"].items():
        _check_cancel(cancel)
        if isinstance(record, dict) and record.get("kind") == "preparation":
            try:
                PreparationDraftService._payload(record)
            except ValueError as exc:
                raise BackupError(f"备课草稿 {identity} 无法完整读取；请先检查，未略过后报成功。") from exc
            records[identity] = deepcopy(record)
    # Other draft kinds include student analysis and private result envelopes.
    # Keep only known lesson data rather than blindly archiving the state JSON.
    selected = {"schema_version": STATE_SCHEMA, "window": {}, "drafts": records,
                "basket": deepcopy(snapshot["basket"]), "updated_at": snapshot.get("updated_at"),
                "work_organization": {}}
    if "scan_number_regions" in snapshot:
        from .desktop_number_regions import validated_region_library
        selected["scan_number_regions"] = validated_region_library(snapshot["scan_number_regions"])
    if "teaching_templates" in snapshot:
        from .desktop_teaching_templates import validate_library
        selected["teaching_templates"] = validate_library(snapshot["teaching_templates"])
    for identity, record in records.items():
        view = metadata(snapshot, "draft", identity)
        if view:
            selected["work_organization"]["draft:" + identity] = view
    files = {}
    def inline(name, data):
        files[name] = PlannedFile(name, hashlib.sha256(data).hexdigest(), len(data), data=data)
    def add(name, *, expected=None):
        if not _allowed(name):
            raise BackupError("所选文件不属于受支持的备课资料范围。")
        path = _source(root, name)
        if not path.is_file():
            refs.append({"kind": "missing_file", "path": name, "sha256": expected})
            return False
        digest, size = _hash_file(path, cancel)
        if expected and digest != expected:
            raise BackupError(f"文件 {Path(name).name} 与已登记内容不一致，请先核对。")
        files[name] = PlannedFile(name, digest, size, source=path)
        return True
    recovery_path = _source(root, "recovery/preparation.v1.json")
    recovery = None
    if recovery_path.exists():
        recovery = PreparationRecoveryStore(root).load()
        # Export the validated snapshot, not a second potentially newer read.
        inline("recovery/preparation.v1.json", _bytes(recovery))
    all_assets = [selected, recovery]
    tasks, skipped_tasks = 0, 0
    reader = _task_reader(root)
    if reader.tasks_root.exists():
        _source(root, PREP + "tasks")
        for path in sorted(reader.tasks_root.glob("PREP-*.json")):
            _check_cancel(cancel)
            name = path.relative_to(root).as_posix()
            _source(root, name)
            try:
                task = reader._read_task(path.stem)
            except Exception as exc:
                if include_tasks:
                    raise BackupError(f"任务 {path.stem} 无法完整读取；请先检查。") from exc
                skipped_tasks += 1
                continue
            if not include_tasks or task["status"] not in TERMINAL_TASKS:
                skipped_tasks += 1
                continue
            # Export task bytes captured by the read validator. A task that is
            # restarted during copying causes a version check failure below.
            digest, size = _hash_file(path, cancel)
            if _json(path.read_bytes()) != task:
                raise BackupError("任务在整理清单时发生变化，请刷新后重试。")
            files[name] = PlannedFile(name, digest, size, source=path)
            tasks += 1
            view = metadata(snapshot, "task", path.stem)
            if view:
                selected["work_organization"]["task:" + path.stem] = view
            all_assets.append(task["payload"])
            for field, folder in (("candidate_seed_file", "seeds"), ("returned_candidate_file", "returned")):
                if task.get(field):
                    add(PREP + folder + "/" + task[field])
            if task["status"] == "completed":
                prefix = PREP + "artifacts/" + _relative(task["attempt_relpath"]) + "/"
                for artifact in task["artifacts"]:
                    add(prefix + _relative(artifact["relative_path"]), expected=artifact["sha256"])
    bundles = {}
    from .desktop_lesson_output import output_names
    for record in _design_outputs(all_assets):
        _check_cancel(cancel)
        old = bundles.get(record["id"])
        if old is not None and old != record:
            raise BackupError("同一教学成品有不同版本记录，请先核对草稿。")
        try: output_names(record)
        except ValueError as exc: raise BackupError(str(exc)) from exc
        bundles[record["id"]] = record
    for identity, record in bundles.items():
        if include_tasks:
            for file in record["files"]:
                add(PREP + "node-exports/" + identity + "/" + file["name"], expected=file["sha256"])
        else:
            refs.append({"kind": "lesson_output", "key": identity,
                         "reason": "未勾选已结束任务与成品；仅保存版本引用，未打包三类文件。"})
    if bundles:
        warnings.append("教学成品按已保存草稿/恢复副本中的版本选入；预览缓存不打包。页面编排草稿及六文件成品须用支持lesson-design.v3的版本恢复；旧版三文件成品仍按原协议恢复。")
    for asset in _assets(all_assets):
        old = images.get(asset["sha256"])
        if old and any(old[k] != asset[k] for k in ("width", "height", "content_type")):
            raise BackupError("同一图片身份的尺寸信息不一致。")
        images[asset["sha256"]] = asset
    for digest, asset in images.items():
        name = PREP + "images/" + digest + ".image"
        path = _source(root, name)
        available = path.is_file()
        if include_images and available:
            if path.stat().st_size > 10 * 1024 * 1024:
                raise BackupError("图片超过10MB，请先检查原副本。")
            verify_image_bytes(asset, path.read_bytes())
            add(name, expected=digest)
        else:
            refs.append({"kind": "lesson_image", "asset": asset,
                         "reason": "not_selected" if available else "missing"})
    files.update(_plan_crop_receipts(root, all_assets, cancel))
    crop_totals = _crop_closure(all_assets, set(files), lambda name: _source(root, name).read_bytes(),
        refs, warnings, cancel=cancel)
    for item in selected["basket"]:
        refs.append({"kind": "basket_reference", "key": item.get("key", ""),
                     "reason": "原题库不在本版备份范围；题篮保存身份，不等于原题已打包。"})
    preparation_count=len(records)
    source_totals={'source_batches':0,'source_documents':0,'source_archive_files':0,'source_archive_bytes':0}
    if include_source_dependencies:
        source_files,source_totals=_plan_source_dependencies(root,selected,source_state_root=source_state_root,cancel=cancel)
        files.update(source_files)
        warnings.append('包含本课已用讲义所在完整批次的原文件；用于恢复后继续选材。原来源ID、角色与资格保留，不包括其他批次、学生档案或模型设置。')
    omitted_drafts = len(set(snapshot['drafts'])-set(selected['drafts']))
    if skipped_tasks:
        warnings.append(f"{skipped_tasks}份任务未选入或尚未结束；不会恢复/重试正在运行的请求。")
    if omitted_drafts:
        warnings.append(f"{omitted_drafts}份非备课草稿未包含（含其他业务草稿）；并非整个工作台备份。")
    warnings.append(('未选来源依赖；不包括原题库、Word/公众号导入库、学生档案、模型设置与密钥。' if not include_source_dependencies else
        '仅包含本课实际依赖的完整原文件批次，不包括其他导入库、中央题库、学生档案、模型设置与密钥。')+'正文内教师自行填写的个人信息不会自动脱敏。')
    _reject_sensitive_fields(selected)
    inline("desktop-state.v1.json", _bytes(selected))
    if len(files) > MAX_FILES or sum(f.size for f in files.values()) > MAX_TOTAL_BYTES:
        raise BackupError("本次超过2GB或2万个文件，请分批保管成品。")
    summary = {"drafts": preparation_count, "tasks": tasks, "basket_references": len(selected["basket"]),
               "recovery": recovery is not None, "images_referenced": len(images),
               "images_included": sum(bool(_IMAGE.fullmatch(n)) for n in files),
               "files": len(files), "bytes": sum(f.size for f in files.values()),
               "include_images": bool(include_images), "include_tasks": bool(include_tasks),
               "missing_files": sum(r["kind"] == "missing_file" or r.get("reason") == "missing" for r in refs)}
    summary.update(crop_totals)
    summary.update(source_totals)
    summary['include_source_dependencies']=bool(include_source_dependencies)
    if bundles:
        summary.update(_node_output_totals(files))
    if "scan_number_regions" in selected:
        from .desktop_number_regions import region_totals
        summary.update(region_totals(selected["scan_number_regions"]))
        warnings.append("包含题号区域坐标，不包括对应的原题图；恢复后须使用相同原图才能载入位置。旧版程序可能不支持此扩展。")
    if "teaching_templates" in selected:
        template_rows = selected["teaching_templates"]["records"].values()
        summary["teaching_templates"] = sum(row["deleted_at"] is None for row in template_rows)
        summary["deleted_teaching_templates"] = sum(row["deleted_at"] is not None for row in template_rows)
    return BackupPlan(tuple(files.values()), summary, tuple(refs), tuple(warnings))


def _publish_file(temporary, destination):
    if os.name == "nt":
        # Windows rename is a no-clobber atomic move, including removable media.
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)


def create_backup(plan: BackupPlan, destination, *, cancel=None):
    destination = Path(destination).absolute()
    if destination.exists():
        raise BackupError("该备份文件已存在，请使用新文件名；没有覆盖旧备份。")
    if not destination.parent.is_dir():
        raise BackupError("请先选择已存在的保存目录。")
    manifest = {"schema_version": SCHEMA, "created_at": utc_now(), **plan.report()}
    from .desktop_version import DESKTOP_VERSION
    manifest["app_version"] = DESKTOP_VERSION
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".shchem-backup-", suffix=".tmp", dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _bytes(manifest))
            for entry in plan.files:
                _check_cancel(cancel)
                digest, size = hashlib.sha256(), 0
                with archive.open("files/" + entry.name, "w") as out:
                    if entry.data is not None:
                        chunks = (entry.data,)
                        stream = None
                    else:
                        # Check links again after the user has reviewed the plan.
                        if entry.source.is_symlink():
                            raise BackupError("来源文件变为链接，请刷新清单。")
                        stream = entry.source.open("rb")
                        chunks = iter(lambda: stream.read(1024 * 1024), b"")
                    try:
                        for chunk in chunks:
                            _check_cancel(cancel)
                            size += len(chunk)
                            if size > entry.size:
                                raise BackupError("来源文件在备份期间变化，请刷新清单后重试。")
                            digest.update(chunk)
                            out.write(chunk)
                    finally:
                        if stream:
                            stream.close()
                if digest.hexdigest() != entry.sha256 or size != entry.size:
                    raise BackupError("来源文件在备份期间变化，请刷新清单后重试。")
        # Full verification happens before making the archive visible as final.
        inspect_backup(temporary, cancel=cancel)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        _check_cancel(cancel)
        # Hard-link publication is atomic and refuses an existing destination.
        # Both files are in the same directory/volume on supported Windows/Linux.
        _publish_file(temporary, destination)
        return {"path": str(destination), "manifest": manifest}
    except (OSError, BadZipFile) as exc:
        raise BackupError("备份未完成，请检查目录权限、磁盘空间和来源文件；旧备份未覆盖。") from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _manifest(archive, *, cancel=None):
    infos = archive.infolist()
    names = [i.filename for i in infos]
    if len(infos) > MAX_FILES + 1 or len({n.casefold() for n in names}) != len(names):
        raise BackupError("备份文件过多或包含重复/大小写冲突路径。")
    if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > MANIFEST_LIMIT:
        raise BackupError("备份缺少有效清单。")
    manifest = _json(archive.read("manifest.json"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
        raise BackupError("此备份版本不受支持；没有修改当前资料。")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_FILES:
        raise BackupError("备份文件清单不完整。")
    targets, total = set(), 0
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"}
                or not _allowed(row["path"]) or not isinstance(row["sha256"], str)
                or not _HASH.fullmatch(row["sha256"]) or type(row["bytes"]) is not int
                or not 0 <= row["bytes"] <= MAX_FILE_BYTES):
            raise BackupError("备份含未支持的文件类型、路径或校验字段。")
        name = "files/" + row["path"]
        if name.casefold() in targets or name not in names:
            raise BackupError("备份清单存在重复或缺失文件。")
        targets.add(name.casefold())
        info = archive.getinfo(name)
        mode = info.external_attr >> 16
        if info.is_dir() or (mode & 0o170000) not in (0, 0o100000) or info.flag_bits & 1 or info.file_size != row["bytes"]:
            raise BackupError("备份文件尺寸、类型或加密方式不受支持。")
        total += row["bytes"]
    if total > MAX_TOTAL_BYTES or {n.casefold() for n in names} != targets | {"manifest.json"}:
        raise BackupError("备份超出容量或包含清单以外的文件。")
    if "files/desktop-state.v1.json" not in names:
        raise BackupError("备份缺少个人作品状态。")
    state = _json(archive.read("files/desktop-state.v1.json"))
    if (not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA
            or set(state) - {"schema_version", "window", "drafts", "basket", "updated_at", "work_organization", "scan_number_regions", "teaching_templates"}
            or not isinstance(state.get("drafts"), dict) or not isinstance(state.get("basket"), list)
            or len(state["basket"]) > 100):
        raise BackupError("备份的作品状态格式不正确。")
    if "scan_number_regions" in state:
        from .desktop_number_regions import validated_region_library
        validated_region_library(state["scan_number_regions"])
    if "teaching_templates" in state:
        from .desktop_teaching_templates import validate_library
        try:
            validate_library(state["teaching_templates"])
        except ValueError as exc:
            raise BackupError("备份中的个人教学模板无法完整读取。") from exc
    _reject_sensitive_fields(state)
    source_records=_source_descriptors(state)
    for identity, record in state["drafts"].items():
        if identity not in source_records:PreparationDraftService._payload(record)
        metadata(state, "draft", identity)
    if not isinstance(manifest.get("references"), list) or not isinstance(manifest.get("warnings"), list):
        raise BackupError("备份缺少外部引用和范围说明。")
    if "scan_number_regions" in state:
        from .desktop_number_regions import region_totals
        manifest = deepcopy(manifest)
        manifest['summary'] = {**manifest.get('summary', {}), **region_totals(state['scan_number_regions'])}
    if "teaching_templates" in state:
        manifest = deepcopy(manifest)
        rows = list(state["teaching_templates"]["records"].values())
        manifest["summary"] = {**manifest.get("summary", {}),
            "teaching_templates": sum(row["deleted_at"] is None for row in rows),
            "deleted_teaching_templates": sum(row["deleted_at"] is not None for row in rows)}
    if "node_output_files" in manifest.get("summary", {}) or any(_NODE_OUTPUT.fullmatch(r["path"]) for r in manifest["files"]):
        manifest = deepcopy(manifest)
        manifest["summary"] = {**manifest.get("summary", {}), **_node_output_totals(r["path"] for r in manifest["files"])}
    selected_names = {row["path"] for row in manifest["files"]}
    def read(name):
        limit = _CROP_LIMIT if _CROP.fullmatch(name) else 10 * 1024 * 1024
        if archive.getinfo("files/" + name).file_size > limit:
            raise BackupError("教材裁选凭据或课内图片超过支持范围。")
        return archive.read("files/" + name)
    source_totals=_source_closure(state,selected_names,lambda name:archive.read('files/'+name),cancel=cancel)
    inputs = _archive_crop_inputs(archive, selected_names, state)
    manifest = deepcopy(manifest)
    totals = _crop_closure(inputs, selected_names, read, manifest["references"], manifest["warnings"], cancel=cancel)
    manifest["summary"] = {**manifest.get("summary", {}), **totals, **source_totals,
        "include_source_dependencies":bool(source_records),
        "drafts": sum(isinstance(r,dict) and r.get("kind")=="preparation" for r in state["drafts"].values()), "basket_references": len(state["basket"]),
        "files": len(manifest["files"]), "bytes": sum(row["bytes"] for row in manifest["files"]),
        "tasks": sum(bool(_TASK_FILE.fullmatch(row["path"])) for row in manifest["files"])}
    return manifest


def inspect_backup(filename, *, cancel=None):
    try:
        with ZipFile(filename) as archive:
            manifest = _manifest(archive, cancel=cancel)
            for row in manifest["files"]:
                _check_cancel(cancel)
                digest, size = hashlib.sha256(), 0
                with archive.open("files/" + row["path"]) as stream:
                    while chunk := stream.read(1024 * 1024):
                        _check_cancel(cancel)
                        size += len(chunk)
                        if size > row["bytes"]:
                            raise BackupError("备份实际解压尺寸超出清单。")
                        digest.update(chunk)
                if size != row["bytes"] or digest.hexdigest() != row["sha256"]:
                    raise BackupError("备份校验不符，可能损坏；没有恢复任何文件。")
            # Do not trust claimed totals for the teacher's preview.
            manifest = deepcopy(manifest)
            state = _json(archive.read("files/desktop-state.v1.json"))
            manifest["summary"] = {**manifest.get("summary", {}), "drafts": sum(isinstance(r,dict) and r.get("kind")=="preparation" for r in state["drafts"].values()),
                "basket_references": len(state["basket"]), "files": len(manifest["files"]),
                "tasks": sum(bool(_TASK_FILE.fullmatch(r["path"])) for r in manifest["files"]),
                "bytes": sum(r["bytes"] for r in manifest["files"])}
            if "node_output_files" in manifest["summary"] or any(_NODE_OUTPUT.fullmatch(r["path"]) for r in manifest["files"]):
                manifest["summary"].update(_node_output_totals(r["path"] for r in manifest["files"]))
            from .desktop_number_regions import region_totals
            if "scan_number_regions" in state:
                manifest["summary"].update(region_totals(state["scan_number_regions"]))
            return manifest
    except BackupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, BadZipFile, RuntimeError) as exc:
        raise BackupError("无法完整读取备份；请检查文件，当前资料未改变。") from exc


def restore_backup(filename, destination, *, expected_manifest: str, cancel=None):
    """A fresh profile, never merge into or replace an existing directory."""
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink():
        raise BackupError("恢复目标已存在。请选择新的子目录，不覆盖或合并任何现有资料。")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise BackupError("请在正常的本地目录下选择新子目录。")
    temporary = None
    try:
        # Use one open file/ZipFile throughout inspection and extraction, so a
        # swapped pathname cannot substitute another archive after confirmation.
        with Path(filename).open("rb") as source, ZipFile(source) as archive:
            manifest = _manifest(archive, cancel=cancel)
            if hashlib.sha256(_bytes(manifest)).hexdigest() != expected_manifest:
                raise BackupError("备份清单已变化，请重新检查并确认。")
            temporary = Path(tempfile.mkdtemp(prefix=".shchem-restore-", dir=target.parent))
            for row in manifest["files"]:
                _check_cancel(cancel)
                output = temporary / row["path"]
                output.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with archive.open("files/" + row["path"]) as stream, output.open("xb") as out:
                    while chunk := stream.read(1024 * 1024):
                        _check_cancel(cancel)
                        size += len(chunk)
                        if size > row["bytes"]:
                            raise BackupError("备份大小与清单不符。")
                        digest.update(chunk)
                        out.write(chunk)
                if size != row["bytes"] or digest.hexdigest() != row["sha256"]:
                    raise BackupError("恢复文件校验失败，未接入当前工作台。")
            # Validate actual supported records and image decoding before commit.
            state = DesktopStateStore(temporary).snapshot()
            recovery = PreparationRecoveryStore(temporary).load()
            image_inputs = [state, recovery]
            reader = _task_reader(temporary)
            for path in reader.tasks_root.glob("*.json"):
                task = reader._read_task(path.stem)
                if task["status"] not in TERMINAL_TASKS:
                    raise BackupError("备份含未结束任务，拒绝恢复以免续跑。")
                image_inputs.append(task["payload"])
            for asset in _assets(image_inputs):
                image = temporary / PREP / "images" / (asset["sha256"] + ".image")
                if image.is_file():
                    if image.stat().st_size > 10 * 1024 * 1024:
                        raise BackupError("备课图片超出支持范围。")
                    verify_image_bytes(asset, image.read_bytes())
            _crop_closure(image_inputs, {row["path"] for row in manifest["files"]},
                lambda name: _source(temporary, name).read_bytes(),
                manifest["references"], manifest["warnings"], cancel=cancel)
            _source_closure(state,{row['path'] for row in manifest['files']},lambda name:_source(temporary,name).read_bytes(),cancel=cancel)
            (temporary / MARKER).write_bytes(_bytes({"schema_version": RESTORED_SCHEMA,
                "created_at": utc_now(), "backup_manifest_sha256": expected_manifest,
                "references": manifest["references"], "warnings": manifest["warnings"]}))
            _check_cancel(cancel)
            # Windows rename refuses existing targets. Reserving the name first
            # makes the same no-clobber contract explicit on other platforms.
            target.mkdir(exist_ok=False)
            try:
                # Publish the marker last. No other launch accepts an incomplete
                # restore directory, and the current personal profile is untouched.
                for child in temporary.iterdir():
                    if child.name != MARKER:
                        os.replace(child, target / child.name)
                os.replace(temporary / MARKER, target / MARKER)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            temporary.rmdir()
            temporary = None
        return {"directory": str(target), "drafts": sum(isinstance(r,dict) and r.get("kind")=="preparation" for r in state["drafts"].values()),
                "references": len(manifest["references"]), "current_state_unchanged": True}
    except BackupError:
        raise
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as exc:
        raise BackupError("恢复未完成；当前资料未改变，请检查备份或目标目录。") from exc
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def manifest_revision(manifest):
    return hashlib.sha256(_bytes(manifest)).hexdigest()


def restored_profile(path):
    root = Path(path).resolve()
    marker = _source(root, MARKER)
    if not marker.is_file() or marker.stat().st_size > MANIFEST_LIMIT:
        raise BackupError("请选择本工作台创建的独立恢复目录。")
    if _json(marker.read_bytes()).get("schema_version") != RESTORED_SCHEMA:
        raise BackupError("独立恢复目录格式不正确。")
    DesktopStateStore(root).snapshot()
    return root


def missing_lesson_images(state_root):
    root = Path(state_root).resolve()
    state = DesktopStateStore(root).snapshot()
    inputs = [[record for record in state["drafts"].values()
               if isinstance(record, dict) and record.get("kind") == "preparation"],
              PreparationRecoveryStore(root).load()]
    reader = _task_reader(root)
    for path in reader.tasks_root.glob("PREP-*.json"):
        task = reader._read_task(path.stem)
        inputs.append(task["payload"])
    images = {asset["sha256"]: asset for asset in _assets(inputs)}
    return [asset for digest, asset in images.items()
            if not _source(root, PREP + "images/" + digest + ".image").is_file()]


def reconnect_lesson_image(state_root, asset, source):
    """Restore identical bytes into the content store; never rewrite references."""
    from .desktop_preparation_images import MAX_IMAGE_BYTES
    source = Path(source)
    if not source.is_file() or source.stat().st_size > MAX_IMAGE_BYTES:
        raise BackupError("请选择不超过10MB的原始图片文件。")
    data = source.read_bytes()
    try:
        verify_image_bytes(asset, data)
    except ValueError as exc:
        raise BackupError("所选图片与原引用的内容/尺寸不一致。请选原文件，不按同名替换。") from exc
    root = Path(state_root).resolve()
    target = _source(root, PREP + "images/" + asset["sha256"] + ".image")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verify_image_bytes(asset, target.read_bytes())
        return {"asset_id": asset["asset_id"], "already_present": True}
    fd, name = tempfile.mkstemp(prefix=".reconnect-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        _publish_file(temporary, target)
    except OSError as exc:
        raise BackupError("图片未能保存，原引用未改变。") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {"asset_id": asset["asset_id"], "already_present": False}


def summary_text(report):
    s = report["summary"]
    return (f"备课草稿 {s['drafts']} 份 · 已结束任务 {s['tasks']} 份 · 题篮引用 {s['basket_references']} 项\n"
            f"原资料依赖 {s.get('source_batches',0)} 批 / {s.get('source_documents',0)} 份原文件（完整批次）\n"
            f"文件 {s['files']} 个 · 未压缩 {s['bytes'] / (1024 * 1024):.2f} MB\n"
            f"图片打包 {s.get('images_included', 0)} / 引用 {s.get('images_referenced', 0)} 张 · "
            f"恢复副本 {'包含' if s.get('recovery') else '无'}\n"
            f"教学环节成品 {s.get('node_output_bundles', 0)} 套完整文件 / {s.get('node_output_files', 0)} 个文件\n"
            f"教材裁选凭据 {s.get('textbook_crop_receipts', 0)} / {s.get('textbook_crops_referenced', 0)} 项；"
            f"未能核验 {s.get('textbook_crop_receipts_missing', 0)} 项（原书另存）\n"
            f"已存题号位置 {s.get('number_region_images', 0)} 张图 / {s.get('number_regions', 0)} 个区域（不含原题图）\n\n" +
            "\n".join(report["warnings"]) + "\n\n外部引用/缺失项：\n" +
            ("\n".join((r.get("asset", {}).get("caption") or r.get("path") or r.get("key") or r["kind"]) +
                       " — " + r.get("reason", "文件缺失") for r in report["references"]) or "无"))
