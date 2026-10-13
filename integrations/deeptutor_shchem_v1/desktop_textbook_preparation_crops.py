"""Source-bound textbook rectangles, explicitly selected for a local lesson.

Page and crop previews are read-only. Confirmation rebuilds the same pixels
and metadata before saving a PNG and a separate immutable source receipt.
It does not grant chemical review, retrieval, generation or publication rights.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .desktop_preparation_images import MAX_IMAGES, PreparationImageStore, image_info, normalize_image_assets
from .desktop_textbook_asset_catalog import TextbookAssetCatalogService
from .reader_cancellation import check_read_cancelled, publication_guard

PAGE_DPI = 240
MAX_PAGE_PIXELS = 8_000_000
MAX_PAGE_EDGE = 6000
MAX_PAGE_BYTES = 32 * 1024 * 1024
SCHEMA = "shchem.textbook-preparation-crop.v1"
MAX_BATCH_IMAGE_BYTES = 128 * 1024 * 1024
CROP_REQUEST_FIELDS = {"selection", "page_revision", "bounds", "caption", "purpose",
                       "expected_preview_revision", "expected_image_sha256", "selection_confirmed"}


class TextbookPreparationCropError(ValueError):
    code = "textbook_preparation_crop_invalid"

    def __init__(self, message):
        self.message_zh = message
        super().__init__(message)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _selection(value):
    if not isinstance(value, dict) or set(value) != {"visual_asset_id", "revision"}:
        raise TextbookPreparationCropError("素材身份不完整，请从教材素材目录重新选择。")
    return dict(value)


def _bounds(value, width, height):
    keys = ("left", "top", "right", "bottom")
    if not isinstance(value, dict) or set(value) != set(keys):
        raise TextbookPreparationCropError("请在原页内框选图表，或明确选择使用整页。")
    edges = tuple(value[key] for key in keys)
    if not all(type(edge) is int for edge in edges):
        raise TextbookPreparationCropError("裁选边界必须是原页的整数像素。")
    left, top, right, bottom = edges
    if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
        raise TextbookPreparationCropError("选框必须在原页内，且保留实际宽度和高度。")
    return edges


def _render_page(data, page_number):
    import pypdfium2 as pdfium
    from .desktop_local_pagination import _PDF_LOCK

    check_read_cancelled()
    with _PDF_LOCK, pdfium.PdfDocument(data) as document:
        page = document[page_number - 1]
        try:
            width, height = page.get_size()
            if not all(math.isfinite(x) and x > 0 for x in (width, height)) or not math.isfinite(width*height) or width*height <= 0:
                raise TextbookPreparationCropError("教材原页尺寸无法可靠读取。")
            scale = min(PAGE_DPI / 72, math.sqrt(MAX_PAGE_PIXELS / (width * height)),
                        MAX_PAGE_EDGE / max(width, height))
            while math.ceil(width * scale) * math.ceil(height * scale) > MAX_PAGE_PIXELS:
                scale *= .999
            bitmap = page.render(scale=scale, bitmap_maker=pdfium.PdfBitmap.new_foreign)
            try:
                from PIL import Image
                # Detach the complete pixels while PDFium objects and the
                # process lock are still alive. PIL never receives a view of a
                # foreign bitmap; its normal close cannot affect native memory.
                pixels = bytes(bitmap.buffer)
                mode = pdfium.internal.BitmapTypeToStrReverse[bitmap.format]
                original = Image.frombytes(mode, (bitmap.width, bitmap.height), pixels,
                                           'raw', bitmap.mode, bitmap.stride, 1)
                try:
                    image = original.convert("RGB")
                    try:
                        if image.width * image.height > MAX_PAGE_PIXELS or max(image.size) > MAX_PAGE_EDGE:
                            raise TextbookPreparationCropError("教材原页超过本地框选显示范围。")
                        target = io.BytesIO()
                        image.save(target, format="PNG")
                        raw = target.getvalue()
                        result = {"page_data": raw, "width": image.width, "height": image.height,
                                  "scale": scale, "effective_dpi": round(scale * 72, 2)}
                    finally:
                        image.close()
                finally:
                    original.close()
            finally:
                bitmap.close()
        finally:
            page.close()
    if len(raw) > MAX_PAGE_BYTES:
        raise TextbookPreparationCropError("原页图像过大，无法安全打开框选预览。")
    check_read_cancelled()
    return result


def _safe_state_path(path):
    # Do not resolve away a reparse point before checking the fixed state path.
    for node in (path, *path.parents):
        if not node.exists() and not node.is_symlink():
            continue
        details = node.lstat()
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise TextbookPreparationCropError("裁图保存位置不能是链接目录，请核对个人状态位置。")


class TextbookPreparationCropService:
    def __init__(self, workspace, task_root, *, personal_books=None):
        self.workspace = Path(workspace).absolute()
        self.catalog = TextbookAssetCatalogService(workspace)
        self.task_root = Path(task_root).absolute()
        self.personal_books = personal_books

    def _source(self, selection, *, expected_origin=None, original_source_path=None):
        selection = _selection(selection)
        if str(selection['visual_asset_id']).startswith('SECTIONPAGE-'):
            from .desktop_section_page_crops import SectionTextbookPageCatalog
            return SectionTextbookPageCatalog(self.workspace).source(**selection,
                expected_origin=expected_origin, original_source_path=original_source_path)
        elif str(selection['visual_asset_id']).startswith('CONCEPTPAGE-'):
            from .desktop_textbook_concept_cards import ConceptTextbookPageCatalog
            return ConceptTextbookPageCatalog(self.workspace).source(**selection)
        elif str(selection['visual_asset_id']).startswith('NOTEPAGE-'):
            from .desktop_note_page_crops import NoteTextbookPageCatalog
            return NoteTextbookPageCatalog(self.workspace).source(**selection)
        elif str(selection['visual_asset_id']).startswith('BOOKPAGE-'):
            if self.personal_books is None:
                raise TextbookPreparationCropError("请从已导入的个人教材选择PDF原页。")
            from .desktop_personal_textbook_crops import PersonalTextbookPageCatalog
            return PersonalTextbookPageCatalog(self.personal_books).source(**selection)
        else:
            return self.catalog.source(**selection)

    def page(self, selection):
        source = self._source(selection)
        raster = _render_page(source["pdf_bytes"], source["pdf_page"])
        origin = {key: source[key] for key in ("visual_asset_id", "revision", "volume_id",
                                              "source_title", "source_sha256", "pdf_page", "printed_page")}
        if source.get('source_kind') == 'section_textbook_page':
            from .desktop_section_page_crops import ORIGIN_FIELDS
            origin.update({key: source[key] for key in ORIGIN_FIELDS})
        elif source.get('source_kind') == 'personal_textbook_page':
            origin.update(source_kind=source['source_kind'], source_id=source['source_id'])
        elif source.get('source_kind') == 'knowledge_note_page':
            origin.update({k: source[k] for k in ('source_kind', 'record_id', 'section_key',
                                                  'note_revision', 'directory_revision')})
        elif source.get('source_kind') == 'textbook_concept_page':
            origin.update({k: source[k] for k in ('source_kind', 'record_id', 'concept_revision',
                                                  'directory_revision', 'concept_location')})
        recipe = {"renderer": "pypdfium2.page.render.rgb.v1", "scale": raster["scale"],
                  "width": raster["width"], "height": raster["height"],
                  "raster_sha256": _digest(raster["page_data"])}
        page_revision = _digest(_json({"origin": origin, "recipe": recipe}))
        return {**raster, **origin, "label": source["label"], "recipe": recipe,
                "page_revision": page_revision, "candidate_only": True, "human_reviewed": False}

    def preview(self, selection, page_revision, bounds, caption, purpose):
        from PIL import Image
        from .desktop_preparation import _reject_sensitive

        page = self.page(selection)
        if page_revision != page["page_revision"]:
            raise TextbookPreparationCropError("素材原页或显示版本已变化，请重新读取原页并框选。")
        edges = _bounds(bounds, page["width"], page["height"])
        _reject_sensitive({"caption": caption, "purpose": purpose})
        with Image.open(io.BytesIO(page["page_data"])) as image:
            with image.crop(edges) as cropped:
                target = io.BytesIO(); cropped.save(target, format="PNG")
        data = target.getvalue()
        info = image_info(data)
        provenance = {key: page[key] for key in ("visual_asset_id", "revision", "volume_id",
                                                "source_title", "source_sha256", "pdf_page", "printed_page")}
        if page.get('source_kind') == 'section_textbook_page':
            from .desktop_section_page_crops import ORIGIN_FIELDS
            provenance.update({key: page[key] for key in ORIGIN_FIELDS})
        elif page.get('source_kind') == 'personal_textbook_page':
            provenance.update(source_kind=page['source_kind'], source_id=page['source_id'])
        elif page.get('source_kind') == 'knowledge_note_page':
            provenance.update({k: page[k] for k in ('source_kind', 'record_id', 'section_key',
                                                   'note_revision', 'directory_revision')})
        elif page.get('source_kind') == 'textbook_concept_page':
            provenance.update({k: page[k] for k in ('source_kind', 'record_id', 'concept_revision',
                                                   'directory_revision', 'concept_location')})
        provenance.update({"schema_version": SCHEMA, "recipe": page["recipe"], "bounds": dict(bounds),
                           "page_revision": page_revision, "image_sha256": _digest(data),
                           "candidate_only": True, "human_reviewed": False,
                           "retrieval_ready": False, "generation_allowed": False, "publication_allowed": False})
        crop_id = _digest(_json({"provenance": provenance, "caption": caption, "purpose": purpose}))
        printed = page["printed_page"] if type(page["printed_page"]) is int else "待核对"
        title = page['source_title']
        display_title = title if len(title) <= 40 else title[:39]+'…'
        # A legible slide footnote points to the full immutable source receipt.
        # Do not put both long hashes and the full catalogue ID on every slide.
        source_text = f"{display_title} · PDF第{page['pdf_page']}页／印刷{printed} · 裁选记录 {crop_id}"
        asset = normalize_image_assets([{"asset_id": "IMG-"+_digest(data), "sha256": _digest(data),
            "caption": caption, "source": source_text, "purpose": purpose, **info}])[0]
        record = {**provenance, "crop_id": crop_id, "image_asset": asset}
        preview_revision = _digest(_json(record))
        check_read_cancelled()
        return {"image_bytes": data, "image_asset": asset, "provenance": record,
                "preview_revision": preview_revision}

    def confirm(self, selection, page_revision, bounds, caption, purpose,
                expected_preview_revision, expected_image_sha256, selection_confirmed):
        value = self._confirmed_preview(selection, page_revision, bounds, caption, purpose,
                                        expected_preview_revision, expected_image_sha256, selection_confirmed)
        with publication_guard():
            return self._save_preview(value)

    def _confirmed_preview(self, selection, page_revision, bounds, caption, purpose,
                           expected_preview_revision, expected_image_sha256, selection_confirmed):
        if selection_confirmed is not True:
            raise TextbookPreparationCropError("请查看当前裁片，再明确确认加入本次备课。")
        value = self.preview(selection, page_revision, bounds, caption, purpose)
        if value["preview_revision"] != expected_preview_revision or value["image_asset"]["sha256"] != expected_image_sha256:
            raise TextbookPreparationCropError("裁片、图题或用途已变化，请重新预览后加入备课。")
        return value

    def confirm_batch(self, requests, existing_assets=None):
        """Validate every selection and capacity before publishing any candidate.

        Only the returned complete result is eligible for a lesson handoff.
        An I/O failure can retain closed local candidate receipts/images; it
        never returns a partial lesson result or changes an existing receipt.
        """
        if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_IMAGES:
            raise TextbookPreparationCropError(f"请选取1至{MAX_IMAGES}张图表，再逐张核对裁片。")
        previous = normalize_image_assets(existing_assets if existing_assets is not None else [])
        known = {a['asset_id'] for a in previous}
        merged, prepared, size = list(previous), [], 0
        for request in requests:
            check_read_cancelled()
            if not isinstance(request, dict) or set(request) != CROP_REQUEST_FIELDS:
                raise TextbookPreparationCropError("某张图表的确认内容不完整，请重新核对。")
            value = self._confirmed_preview(**request)
            size += len(value['image_bytes'])
            if size > MAX_BATCH_IMAGE_BYTES:
                raise TextbookPreparationCropError("本批裁片总量过大，请减少图表或缩小选框后重新加入。")
            prepared.append((request, value))
            asset = value['image_asset']
            if asset['asset_id'] not in known:
                merged.append(asset); known.add(asset['asset_id'])
        merged = normalize_image_assets(merged)  # Includes the current lesson's capacity.
        # A source read early in a long batch must still match at the final gate.
        for request, value in prepared:
            check_read_cancelled()
            source = self._source(request['selection'])
            origin = value['provenance']
            fields = ('visual_asset_id', 'revision', 'volume_id', 'source_title',
                      'source_sha256', 'pdf_page', 'printed_page', 'source_kind', 'source_id')
            if any(source.get(k) != origin.get(k) for k in fields):
                raise TextbookPreparationCropError("批量核对期间某张图表来源已变化，请重读原页。")
        added = []
        seen = {a['asset_id'] for a in previous}
        images = self.task_root / 'preparation-v1' / 'images'
        _safe_state_path(images)
        store = PreparationImageStore(images) if images.exists() else None
        for request, value in prepared:
            if value['image_asset']['asset_id'] in {a['asset_id'] for a in previous}:
                if store is None:
                    raise TextbookPreparationCropError("当前备课的图片副本缺失，请恢复图片后重试。")
                store.load(next(a for a in previous if a['asset_id'] == value['image_asset']['asset_id']))
        with publication_guard():
            for request, value in prepared:
                identifier = value['image_asset']['asset_id']
                if identifier not in seen:
                    asset = self._save_preview(value)
                    if asset != value['image_asset']:
                        raise TextbookPreparationCropError("本地保存的图片与核对裁片不一致，本批未加入备课。")
                    added.append(asset); seen.add(identifier)
        return {'image_assets': merged, 'added_count': len(added),
                'duplicate_count': len(requests) - len(added), 'requested_count': len(requests),
                'candidate_only': True, 'human_reviewed': False}

    def _save_preview(self, value):
        record = value["provenance"]
        folder = self.task_root / "preparation-v1" / "textbook-crops"
        images = self.task_root / "preparation-v1" / "images"
        path = folder / (record["crop_id"] + ".json")
        _safe_state_path(path); _safe_state_path(images)
        exists = path.exists()
        if exists:
            if not path.is_file() or path.stat().st_size > 128*1024:
                raise TextbookPreparationCropError("已有裁选来源记录无法核对，未覆盖原记录。")
            previous = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict) or "created_at_utc" not in previous:
                raise TextbookPreparationCropError("已有裁选来源记录不完整，未覆盖原记录。")
            timestamp = previous.pop("created_at_utc")
            if not isinstance(timestamp, str) or previous != record:
                raise TextbookPreparationCropError("已有裁选来源记录已变化，未覆盖原记录。")
        if not exists:
            folder.mkdir(parents=True, exist_ok=True)
            _safe_state_path(path)
            saved = {**record, "created_at_utc": datetime.now(timezone.utc).isoformat()}
            temporary = folder / (".crop-"+uuid4().hex+".tmp")
            try:
                temporary.write_bytes(_json(saved)+b"\n")
                # Publish without replacing an existing provenance record.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    other = json.loads(path.read_text(encoding="utf-8"))
                    other.pop("created_at_utc", None)
                    if other != record:
                        raise TextbookPreparationCropError("并发裁选来源记录发生冲突，未覆盖原记录。")
            finally:
                temporary.unlink(missing_ok=True)
        # A later image failure can leave a closed candidate receipt, but
        # never returns an active preparation asset without its provenance.
        asset = PreparationImageStore(images).import_bytes(
            value["image_bytes"], value["image_asset"]["caption"],
            value["image_asset"]["source"], value["image_asset"]["purpose"])
        return asset
