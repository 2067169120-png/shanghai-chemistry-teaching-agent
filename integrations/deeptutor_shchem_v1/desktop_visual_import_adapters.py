from __future__ import annotations

"""Thin desktop adapters for the formal visual-import v2 core.

The adapters translate existing local path-based services into the byte/page
ports owned by :mod:`desktop_visual_import_v2`.  Network policy remains owned
by ``visual_provider_runtime`` and its injected ``VisualTransport``.
"""

import hashlib
import json
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from .desktop_visual_import_v2 import (
    DesktopImportBridgeError,
    DesktopSourceFile,
    NativeDocxInspection,
)
from .intake_batches_v2 import (
    DIRECT_PAGE_PIXEL_MODE,
    INTAKE_BATCH_VISUAL_FRAGMENT_V2_SCHEMA_VERSION,
    IntakeBatchFile,
    RenderedPixelPage,
    VisualShardRequest,
    intake_batch_visual_fragment_v2_schema,
)
from .intake_imports import LocalPageRenderer
from .model_provider_settings import ModelProviderProbeContext
from .visual_provider_runtime import (
    PinnedVisualTransport,
    VisualTransport,
    build_structured_visual_request,
    canonical_json_bytes,
    parse_structured_visual_response,
)
from .word_handout_import import WordHandoutImporter, WordHandoutImportError


class DesktopVisualImportAdapterError(RuntimeError):
    """Stable error understood by the visual-import coordinator."""

    def __init__(self, code: str, message_zh: str, status: int = 409) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.status = status


class LocalPageRendererV2Adapter:
    """Adapt ``LocalPageRenderer`` paths to immutable v2 pixel pages."""

    def __init__(
        self,
        *,
        renderer: LocalPageRenderer | None = None,
        temporary_parent: str | Path | None = None,
    ) -> None:
        self._renderer = renderer or LocalPageRenderer()
        self._temporary_parent = (
            Path(temporary_parent).resolve() if temporary_parent is not None else None
        )
        if self._temporary_parent is not None:
            self._temporary_parent.mkdir(parents=True, exist_ok=True)

    def render(
        self, source_file: IntakeBatchFile, *, source_role: str
    ) -> Sequence[RenderedPixelPage]:
        del source_role  # The existing local renderer is intentionally role-neutral.
        if not isinstance(source_file, IntakeBatchFile):
            raise DesktopVisualImportAdapterError(
                "source_file_invalid", "资料文件描述不正确。"
            )
        try:
            with tempfile.TemporaryDirectory(
                prefix="visual-import-render-",
                dir=self._temporary_parent,
            ) as raw_root:
                root = Path(raw_root)
                source_path = root / source_file.filename
                source_path.write_bytes(source_file.content)
                work_root = root / "pages"
                rendered = self._renderer.render(
                    source_path,
                    mime_type=source_file.mime_type,
                    work_root=work_root,
                )
                pages: list[RenderedPixelPage] = []
                for page_number, item in enumerate(rendered, 1):
                    pixels = Path(item.path).read_bytes()
                    with Image.open(item.path) as image:
                        width, height = image.size
                    recipe = hashlib.sha256(
                        canonical_json_bytes(
                            {
                                "adapter": "desktop-local-page-renderer-v2",
                                "page_number": page_number,
                                "source_mime_type": source_file.mime_type,
                                "output_mime_type": item.mime_type,
                            }
                        )
                    ).hexdigest()
                    pages.append(
                        RenderedPixelPage(
                            pixels=pixels,
                            mime_type=item.mime_type,
                            width=int(width),
                            height=int(height),
                            render_recipe_sha256=recipe,
                        )
                    )
                return tuple(pages)
        except DesktopVisualImportAdapterError:
            raise
        except Exception as exc:
            raise DesktopVisualImportAdapterError(
                str(getattr(exc, "code", "page_render_failed")),
                "资料无法渲染为可核对的页面图像。",
            ) from exc


class NativeWordHandoutImporterAdapter:
    """Submit eligible DOCX bytes to the existing personal candidate inventory."""

    def __init__(
        self,
        store_root: str | Path,
        *,
        temporary_parent: str | Path | None = None,
    ) -> None:
        self._importer = WordHandoutImporter(store_root)
        self._temporary_parent = (
            Path(temporary_parent).resolve() if temporary_parent is not None else None
        )
        if self._temporary_parent is not None:
            self._temporary_parent.mkdir(parents=True, exist_ok=True)

    def submit(
        self,
        sources: Sequence[DesktopSourceFile],
        inspections: Sequence[NativeDocxInspection],
    ) -> Mapping[str, Any] | None:
        inspection_ids = {item.source_file_id for item in inspections}
        selected = tuple(
            source
            for source in sources
            if source.effective_source_file_id in inspection_ids
        )
        if not selected:
            return None
        try:
            with tempfile.TemporaryDirectory(
                prefix="visual-import-native-",
                dir=self._temporary_parent,
            ) as raw_root:
                root = Path(raw_root)
                paths: list[Path] = []
                for index, source in enumerate(selected, 1):
                    # Keep the visible filename whenever possible.  Prefix only
                    # duplicate names; cache identity remains source-bound.
                    name = source.filename
                    if any(path.name.casefold() == name.casefold() for path in paths):
                        name = f"{index:03d}-{name}"
                    path = root / name
                    path.write_bytes(source.content)
                    paths.append(path)
                result = self._importer.run_batch(paths, persist=True)
        except WordHandoutImportError as exc:
            raise DesktopImportBridgeError(exc.code, exc.message_zh, 409) from exc
        except OSError as exc:
            raise DesktopImportBridgeError(
                "native_import_failed", "原生文字候选无法保存到个人库存。", 503
            ) from exc
        # Deliberately return counts only.  WordHandoutImporter records contain
        # local paths and source hashes which do not belong in a UI receipt.
        return {
            "lane": "personal_word_handout_inventory",
            "native_batch_id": result.batch_id,
            "documents_total": result.documents_total,
            "documents_completed": result.documents_completed,
            "documents_cached": result.documents_cached,
            "documents_failed": result.documents_failed,
            "quick_import_candidates": result.quick_import_candidates,
            "visual_completion_candidates": result.visual_completion_candidates,
            "candidate_only": True,
            "central_question_bank_write": False,
        }


class StructuredVisualShardProviderAdapter:
    """Run one v2 page shard through the established visual-provider runtime."""

    def __init__(
        self,
        context: ModelProviderProbeContext,
        *,
        transport: VisualTransport | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self._context = context
        self._transport = transport or PinnedVisualTransport()
        self._should_cancel = should_cancel or (lambda: False)

    @staticmethod
    def _prompt(request: VisualShardRequest) -> str:
        payload = request.transport_payload()
        pages = payload.get("pages")
        if not isinstance(pages, list):
            raise DesktopVisualImportAdapterError(
                "visual_request_invalid", "视觉页面请求格式不正确。"
            )
        page_manifests = []
        for page in pages:
            if not isinstance(page, Mapping):
                raise DesktopVisualImportAdapterError(
                    "visual_request_invalid", "视觉页面请求格式不正确。"
                )
            page_manifests.append(
                {key: value for key, value in page.items() if key != "image_data_url"}
            )
        prompt_payload = {
            "schema_version": INTAKE_BATCH_VISUAL_FRAGMENT_V2_SCHEMA_VERSION,
            "batch_id": request.batch_id,
            "shard_id": request.shard_id,
            "shard_index": request.shard_index,
            "source_role": request.source_role,
            "input_mode": DIRECT_PAGE_PIXEL_MODE,
            "source_text_layer_supplied": False,
            "fallback_allowed": False,
            "instructions": payload.get("instructions"),
            "page_manifests": page_manifests,
        }
        return (
            "请直接观察随请求附带的原始或本机渲染页面像素，并严格返回给定 JSON "
            "Schema 的一个对象。只提取页面上直接可见的证据、试卷身份、主题大题、"
            "印刷小题、最小作答单元、原文、选项、作答要求、化学表达式、视觉对象、"
            "前序依赖和答案候选。不得使用 OCR 文本或文档文本层；不得推断教材版本或"
            "章节、题型、K/A/C/R/RP 标签、认知或实测难度、官方性、独立核验、答案"
            '解析、分值或评分点。不确定的化学表达式使用 status="uncertain"；答案'
            "对应关系不确定时将 atomic_part_id 设为 null，并在 warnings 中说明。\n"
            + json.dumps(
                prompt_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )

    def analyze_shard(self, request: VisualShardRequest) -> Mapping[str, Any]:
        if self._should_cancel():
            raise DesktopVisualImportAdapterError("cancelled", "视觉导入已取消。", 409)
        cancel_event = threading.Event()
        outbound = build_structured_visual_request(
            self._context,
            prompt=self._prompt(request),
            schema=intake_batch_visual_fragment_v2_schema(),
            schema_name="shchem_visual_observation_fragment_v2",
            pages=tuple((page.mime_type, page.pixels) for page in request.pages),
        )
        if self._should_cancel():
            cancel_event.set()
            raise DesktopVisualImportAdapterError("cancelled", "视觉导入已取消。", 409)
        response = self._transport.send(
            outbound,
            cancel_event=cancel_event,
            deadline_monotonic=time.monotonic() + 90.0,
        )
        if self._should_cancel():
            raise DesktopVisualImportAdapterError("cancelled", "视觉导入已取消。", 409)
        if not 200 <= response.http_status < 300 or response.model_invoked is not True:
            raise DesktopVisualImportAdapterError(
                "visual_provider_failed", "视觉模型没有返回可用结果。", 502
            )
        decoded, _usage = parse_structured_visual_response(
            outbound.api_style, response.body
        )
        if not isinstance(decoded, Mapping):
            raise DesktopVisualImportAdapterError(
                "visual_provider_output_invalid", "视觉模型返回格式不正确。", 502
            )
        return dict(decoded)


__all__ = [
    "DesktopVisualImportAdapterError",
    "LocalPageRendererV2Adapter",
    "NativeWordHandoutImporterAdapter",
    "StructuredVisualShardProviderAdapter",
]
