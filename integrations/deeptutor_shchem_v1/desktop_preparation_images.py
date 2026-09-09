"""Teacher-selected local pictures, with text-only model-facing metadata.

No provider URLs, arbitrary model paths, or automatic downloads are supported.
Image bytes are copied once and checked against frozen metadata before rendering.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

MAX_IMAGES = 12
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
_ID = re.compile(r"IMG-[0-9a-f]{64}\Z")
_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_FIELDS = {
    "asset_id",
    "sha256",
    "caption",
    "source",
    "purpose",
    "width",
    "height",
    "content_type",
}


class PreparationImageError(ValueError):
    def __init__(self, message_zh: str):
        self.code = "preparation_image_invalid"
        self.message_zh = message_zh
        super().__init__(message_zh)


def normalize_image_assets(value):
    if not isinstance(value, list) or len(value) > MAX_IMAGES:
        raise PreparationImageError(f"每次备课最多选择{MAX_IMAGES}张图片。")
    result, seen = [], set()
    for row in value:
        if not isinstance(row, Mapping) or set(row) != _FIELDS:
            raise PreparationImageError("图片的图题、来源或内容标识不完整。")
        item = dict(row)
        asset_id = item["asset_id"]
        if not isinstance(asset_id, str) or not _ID.fullmatch(asset_id):
            raise PreparationImageError("图片标识不正确，请重新选择图片。")
        if item["sha256"] != asset_id[4:] or asset_id in seen:
            raise PreparationImageError("图片重复或内容摘要不一致。")
        seen.add(asset_id)
        for key, limit in (("caption", 160), ("source", 500), ("purpose", 500)):
            text = item[key]
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text) > limit
                or any(ord(c) < 32 for c in text)
            ):
                raise PreparationImageError("请填写简洁的图片图题、来源和教学用途。")
            item[key] = text.strip()
        if item["content_type"] not in _TYPES.values():
            raise PreparationImageError("只支持PNG、JPEG和WebP静态图片。")
        if any(
            type(item[k]) is not int or not 1 <= item[k] <= 16000
            for k in ("width", "height")
        ):
            raise PreparationImageError("图片尺寸不正确。")
        if item["width"] * item["height"] > MAX_IMAGE_PIXELS:
            raise PreparationImageError("图片过大，请先选择适合投影的图片版本。")
        result.append(item)
    return result


def image_info(data: bytes):
    from PIL import Image

    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise PreparationImageError("图片必须非空且不超过10MB。")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in _TYPES or getattr(image, "n_frames", 1) != 1:
                raise PreparationImageError("只支持PNG、JPEG和WebP静态图片。")
            w, h = image.size
            if w * h > MAX_IMAGE_PIXELS or max(w, h) > 16000:
                raise PreparationImageError("图片尺寸过大，请先使用较小的图片版本。")
            content_type = _TYPES[image.format]
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            # PNG getexif() can load and close the stream, so verify first.
            if image.getexif().get(274, 1) != 1:
                raise PreparationImageError(
                    "图片含旋转方向标记，请先另存为方向正确的PNG再选择。"
                )
    except PreparationImageError:
        raise
    except Exception as exc:
        raise PreparationImageError("图片无法完整解码，请重新选择有效图片。") from exc
    return {"width": w, "height": h, "content_type": content_type}


def verify_image_bytes(asset, data):
    normalized = normalize_image_assets([asset])[0]
    if hashlib.sha256(data).hexdigest() != normalized["sha256"]:
        raise PreparationImageError("本地图片内容已经变化，请重新选择后生成。")
    info = image_info(data)
    if any(info[key] != normalized[key] for key in info):
        raise PreparationImageError("图片尺寸或格式与已确认记录不一致。")
    return data


def normalize_slide_image(value, assets):
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "asset_id",
        "observation_prompt",
    }:
        raise PreparationImageError("图片页结构不正确。")
    if value["asset_id"] not in {a["asset_id"] for a in assets}:
        raise PreparationImageError("图片页引用了本次未选择的图片。")
    prompt = value["observation_prompt"]
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 400:
        raise PreparationImageError("图片页需要简洁、明确的观察任务。")
    return {"asset_id": value["asset_id"], "observation_prompt": prompt.strip()}


def slide_image_schema():
    return {
        "anyOf": [
            {"type": "null"},
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "asset_id": {"type": "string"},
                    "observation_prompt": {"type": "string"},
                },
                "required": ["asset_id", "observation_prompt"],
            },
        ]
    }


class PreparationImageStore:
    def __init__(self, root):
        raw = Path(root)
        if raw.is_symlink():
            raise PreparationImageError("本地图片目录不能是符号链接。")
        self.root = raw.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def import_image(self, file_path, caption, source, purpose):
        path = Path(file_path)
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            raise PreparationImageError("请选择不超过10MB的本地图片文件。")
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        return self.import_bytes(data, caption, source, purpose)

    def import_bytes(self, data: bytes, caption: str, source: str, purpose: str):
        """Save exact locally verified bytes without an intermediate export file."""
        info = image_info(data)
        digest = hashlib.sha256(data).hexdigest()
        asset = normalize_image_assets(
            [
                {
                    "asset_id": "IMG-" + digest,
                    "sha256": digest,
                    "caption": caption,
                    "source": source,
                    "purpose": purpose,
                    **info,
                }
            ]
        )[0]
        target = self.root / (digest + ".image")
        if target.is_symlink():
            raise PreparationImageError("本地图片副本不正确。")
        if target.is_file():
            self.load(asset)
            return asset
        temporary = self.root / (".import-" + uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
            # Concurrent valid imports have identical bytes for this digest.
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return asset

    def load(self, asset):
        asset = normalize_image_assets([asset])[0]
        path = self.root / (asset["sha256"] + ".image")
        if path.is_symlink() or not path.is_file():
            raise PreparationImageError("本地图片副本已丢失，请重新选择图片。")
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        return verify_image_bytes(asset, data)
