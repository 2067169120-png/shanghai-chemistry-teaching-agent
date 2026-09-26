"""Read the current Word question's visible images from one verified source.

One transient read, not a durable image-authority cache. Each tab/opening checks
source bytes, revision and membership again; failures remain visible per image.
"""
from __future__ import annotations
from time import perf_counter
from io import BytesIO
from zipfile import ZipFile

from .desktop_explorer_index import check_cancelled
from .desktop_word_questions import WordQuestionError


def load_word_image_batch(facade, key, revision, asset_ids, *, progress=lambda value: None,
                          cancelled=lambda: False):
    if not isinstance(asset_ids, (list, tuple)) or not asset_ids or any(
        not isinstance(a, str) or not a for a in asset_ids
    ):
        raise WordQuestionError("题图清单不完整，请重新打开题目。")
    ids = list(dict.fromkeys(asset_ids))
    check_cancelled(cancelled)
    start = perf_counter()
    factory = getattr(facade, "_word_questions", None)
    if callable(factory):
        service = factory()
        rows, inventory = service._resolve([{"key": key, "revision": revision}])
        row = rows[0]
        allowed = {a["asset_id"]: a for group in ("question_blocks", "answer_blocks", "context_blocks")
                   for b in row[group] for a in b.get("assets", [])}
        if any(a not in allowed for a in ids):
            raise WordQuestionError("这幅图不属于当前题目，请重新选择。")
        source = inventory[row["source_id"]][0].content

        # The existing per-image reader parses the entire DOCX on every call.
        # Parse once for this tab, retaining the exact existing asset identities.
        from docx import Document
        from PIL import Image
        from .desktop_preparation_sources import _validate_container, _body_blocks, _word_images, PreparationSourceError
        if not isinstance(source, bytes) or len(source) > 40 * 1024 * 1024:
            raise PreparationSourceError("请选择不超过40MB的DOCX讲义。")
        with ZipFile(BytesIO(source)) as package:
            _validate_container(package)
        document = Document(BytesIO(source))
        assets = {a["asset_id"]: a for a in _word_images(
            list(_body_blocks(document._element.body)), document, include_bytes=True)
            if a["asset_id"] in ids}
        check_cancelled(cancelled)

        def read(asset_id):
            asset = assets.get(asset_id)
            if asset is None or asset["sha256"] != allowed[asset_id]["sha256"]:
                raise PreparationSourceError("原图与当前来源记录不一致，请重新预览。")
            if not asset["preview_supported"]:
                from .desktop_word_metafile_preview import can_attempt_metafile, render_word_metafile
                if not can_attempt_metafile(asset):
                    raise PreparationSourceError("此图格式暂不能预览，请在原Word中查看。")
                return {**render_word_metafile(asset["bytes"], mime_type=asset["mime_type"]), "label": asset["label"]}
            with Image.open(BytesIO(asset["bytes"])) as image:
                if image.width * image.height > 50_000_000:
                    raise PreparationSourceError("原图尺寸过大，请在原Word中查看。")
                image.verify()
            return {k: asset[k] for k in ("bytes", "mime_type", "label")}
    else:
        # Existing external facades and test doubles retain their public reader.
        def read(asset_id):
            return facade.word_question_image(key, revision, asset_id)
    source_ms = (perf_counter() - start) * 1000
    completed = failed = 0
    for asset_id in ids:
        check_cancelled(cancelled)
        try:
            value = read(asset_id)
        except Exception:
            failed += 1
            progress({"asset_id": asset_id, "failed": True})
        else:
            completed += 1
            progress({"asset_id": asset_id, "result": value})
    check_cancelled(cancelled)
    return {"images": len(ids), "completed": completed, "failed": failed,
            "source_ms": round(source_ms, 2), "total_ms": round((perf_counter() - start) * 1000, 2)}
