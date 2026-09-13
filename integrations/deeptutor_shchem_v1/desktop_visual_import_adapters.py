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
from .desktop_visual_schema import (
    VISUAL_OBSERVATION_PROMPT_VERSION,
    DesktopVisualSchemaError,
    desktop_visual_page_schema,
    desktop_visual_role_schema,
    desktop_visual_wire_schema,
    visual_import_request_policy,
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
        self._policy = visual_import_request_policy(
            context.base_url, context.model_id, context.api_style
        )
        self._transport = transport or PinnedVisualTransport(
            total_timeout_seconds=self._policy["timeout_seconds"]
        )
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
        if request.source_role == "answer":
            role_contract = {
                "source_role": "answer",
                "required_empty_arrays": {"theme_fragments": []},
                "instructions": (
                    "本分片为参考答案页；theme_fragments 必须为 []。"
                    "只在 answer_candidates 中记录页面直接可见的答案候选。"
                    "答案页标题、章节标题、分组名和题号不得创建主题大题、"
                    "printed_questions 或 atomic_parts，空题目主题也不允许。"
                    "题号记入 answer_candidates.question_number，小问标签记入 part_label；"
                    "无法唯一确定 atomic_part_id 时填 null 并写明 warning。"
                ),
            }
        elif request.source_role in {"question", "handout"}:
            role_contract = {
                "source_role": request.source_role,
                "required_empty_arrays": {"answer_candidates": []},
                "instructions": (
                    "本分片为题目或教师讲义页；answer_candidates 必须为 []。"
                    "在 theme_fragments 中记录直接可见的题面、共同材料、"
                    "主题大题、印刷小题及最小作答单元。"
                    "不得在本角色中创建独立答案候选，也不得推导答案。"
                ),
            }
        else:
            raise DesktopVisualImportAdapterError(
                "visual_request_invalid", "视觉分片的来源角色不正确。"
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
            "observation_prompt_version": VISUAL_OBSERVATION_PROMPT_VERSION,
            "instructions": payload.get("instructions"),
            "role_contract": role_contract,
            "page_identity_contract": {
                "allowed_combinations": [
                    {
                        key: page[key]
                        for key in (
                            "source_file_id",
                            "source_role",
                            "page_number",
                            "page_sha256",
                        )
                    }
                    for page in page_manifests
                ],
                "instructions": (
                    "每个 evidence 必须从 allowed_combinations 选择一条完整组合，"
                    "原样填写 source_file_id、source_role、page_number 和 page_sha256。"
                    "page_number 是该来源文件内部的页号，不是卷面印刷页码，"
                    "也不是本次附件的排列序号。多个独立 JPG/PNG 文件通常各自都是第 1 页；"
                    "一个 PDF 的不同页面则使用该 PDF 在 manifest 中列出的文件内页号。"
                    "不要把不同页面的来源标识、角色、页号或哈希混配，"
                    "不要从图片上的印刷页码推断或替换 page_number。"
                    "返回前逐条核对完整组合；本地不会改写、猜测或替换页号与哈希。"
                ),
            },
            "bbox_contract": {
                "object_with_exact_keys": ["x", "y", "width", "height"],
                "coordinate_space": "normalized_xywh",
                "origin": "top_left",
                "axes": "x 向右，y 向下；width 和 height 为框的宽度和高度。",
                "normalization_extent": (
                    "以对应 source_file_id/page_number 的整张发送页面为基准。"
                    "page_manifests 的 width、height 是像素尺寸；"
                    "输出 bbox 的四个值均为相对整页的比例。"
                ),
                "pixel_to_normalized": {
                    "x": "left / page_width",
                    "y": "top / page_height",
                    "width": "(right - left) / page_width",
                    "height": "(bottom - top) / page_height",
                },
                "bounds": [
                    "0 <= x < 1",
                    "0 <= y < 1",
                    "0 < width <= 1",
                    "0 < height <= 1",
                    "x + width <= 1.000001",
                    "y + height <= 1.000001",
                ],
                "edge_tolerance": (
                    "真实框边界必须位于整页内，坐标和应不超过 1；"
                    "额外 0.000001 仅为浮点舍入容差。"
                ),
                "synthetic_example": {
                    "purpose": "纯合成换算示例，不是当前页面证据，禁止照抄示例框。",
                    "page_pixels": {"width": 1000, "height": 2000},
                    "box_pixels": {
                        "left": 100,
                        "top": 400,
                        "right": 700,
                        "bottom": 1000,
                    },
                    "bbox": {"x": 0.1, "y": 0.2, "width": 0.6, "height": 0.3},
                },
                "self_check": (
                    "返回前逐个核对所有 evidence.bbox 的四个数值、坐标单位、"
                    "实际页面位置及全部边界条件。发现不符时重新观察对应原页。"
                    "不要返回像素坐标、百分数、xyxy 右下角坐标或四元素数组；"
                    "不要用 clamp 裁边掩盖错误。无效返回会被拒绝，"
                    "本地不会自动裁边、猜测坐标单位或转换不合规结果。"
                ),
            },
            "page_manifests": page_manifests,
        }
        return (
            "请直接观察随请求附带的原始或本机渲染页面像素，并严格返回给定 JSON "
            "Schema 的一个对象。按当前 source_role 和下方 role_contract，"
            "仅提取该角色允许且页面上直接可见的证据、试卷身份、主题大题、"
            "印刷小题、最小作答单元、原文、选项、作答要求、化学表达式、视觉对象、"
            "前序依赖和答案候选。不得使用 OCR 文本或文档文本层；不得推断教材版本或"
            "章节、题型、K/A/C/R/RP 标签、认知或实测难度、官方性、独立核验、答案"
            '解析、分值或评分点。不确定的化学表达式使用 status="uncertain"；答案'
            "对应关系不确定时将 atomic_part_id 设为 null，并在 warnings 中说明。"
            "每个 evidence.bbox 必须是归一化左上角 xywh 字典，严格遵守下方 "
            "bbox_contract。每条证据的页面标识必须使用 page_identity_contract 中的"
            "完整组合，并在返回前逐个核对。\n"
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
        try:
            wire_schema = desktop_visual_wire_schema(
                self._context,
                desktop_visual_page_schema(
                    desktop_visual_role_schema(
                        intake_batch_visual_fragment_v2_schema(), request.source_role
                    ),
                    tuple(page.public_manifest() for page in request.pages),
                ),
            )
        except DesktopVisualSchemaError as exc:
            raise DesktopVisualImportAdapterError(
                "visual_schema_unsupported", "视觉导入约束无法完整转换为模型请求。", 409
            ) from exc
        outbound = build_structured_visual_request(
            self._context,
            prompt=self._prompt(request),
            schema=wire_schema,
            schema_name="shchem_visual_observation_fragment_v2",
            pages=tuple((page.mime_type, page.pixels) for page in request.pages),
            max_output_tokens=self._policy["max_output_tokens"],
        )
        if self._should_cancel():
            cancel_event.set()
            raise DesktopVisualImportAdapterError("cancelled", "视觉导入已取消。", 409)
        response = self._transport.send(
            outbound,
            cancel_event=cancel_event,
            deadline_monotonic=time.monotonic() + self._policy["timeout_seconds"],
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
