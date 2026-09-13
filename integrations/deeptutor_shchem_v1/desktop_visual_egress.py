"""Frozen local page previews for saved visual-import batches.

The visual-import workflow has two distinct local stages:

* the teacher previews the exact source pages that would leave the machine;
* a later confirmed run sends those same page bytes to the visual provider.

This module owns the in-process snapshot between those stages.  It never
borrows credentials, invokes a provider, writes a candidate, or deletes the
original visual-import archive.  A snapshot is intentionally memory-backed;
``discard`` releases only that snapshot.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .desktop_visual_import_v2 import (
    SOURCE_ROLES,
    DesktopImportBridgeError,
    DesktopImportCoordinatorV2,
    DesktopImportRequest,
    DesktopSourceFile,
    _image_dimensions,
    _render_visual_pages,
)
from .intake_batches_v2 import RenderedPixelPage

MAX_EGRESS_PAGES = 500
MAX_EGRESS_BYTES = 512 * 1024 * 1024
MAX_ACTIVE_SNAPSHOTS = 2
_PREVIEW_PREFIX = "VIEGRESS-"
_PAGE_PREFIX = "VIEGRESSPAGE-"


class VisualEgressError(RuntimeError):
    """Sanitized, stable errors for the frozen visual-page boundary."""

    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


def _digest(value: Any) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VisualEgressError(
            "visual_egress_snapshot_invalid", "图片预览快照格式不正确。"
        ) from exc
    return hashlib.sha256(raw).hexdigest()


def _source_manifest(source: DesktopSourceFile) -> dict[str, Any]:
    """Return only safe, path-free source identity fields."""

    value = source.as_manifest()
    return {
        key: value[key]
        for key in (
            "source_file_id",
            "role",
            "order_index",
            "group_id",
            "filename",
            "mime_type",
            "size_bytes",
            "source_sha256",
            "content_addressed",
        )
    }


def _model_label(profile: Mapping[str, Any]) -> str:
    provider = str(profile.get("provider_name") or "视觉服务")
    model = str(profile.get("model_id") or "视觉模型")
    base_url = profile.get("base_url")
    if isinstance(base_url, str) and base_url.strip():
        return f"{provider} / {model}（{base_url.strip()}）"
    return f"{provider} / {model}"


def _request_policy(profile: Mapping[str, Any]) -> dict[str, Any]:
    from .desktop_visual_schema import visual_import_request_policy

    return visual_import_request_policy(
        str(profile.get("base_url") or ""),
        str(profile.get("model_id") or ""),
        profile.get("api_style"),
    )


@dataclass(frozen=True, slots=True)
class _FrozenPage:
    manifest: dict[str, Any]
    pixels: bytes


@dataclass(frozen=True, slots=True)
class _PreviewSnapshot:
    preview_id: str
    revision: str
    batch_id: str
    profile_id: str
    profile_revision: str
    model_label: str
    request_policy: dict[str, Any]
    source_manifests: tuple[dict[str, Any], ...]
    pages: tuple[_FrozenPage, ...]


class FrozenPageRenderer:
    """Renderer adapter that returns the preview's exact page bytes.

    ``MultiFileVisualIntakeV2`` invokes renderers only for document sources;
    direct image sources are handled by its identity path.  The facade checks
    all source hashes before creating this renderer, and this adapter checks
    each document source again at the render boundary.
    """

    def __init__(self, snapshot: _PreviewSnapshot) -> None:
        self._snapshot = snapshot
        self._pages_by_source: dict[str, tuple[_FrozenPage, ...]] = {}
        self._sources_by_id = {
            str(item["source_file_id"]): item
            for item in snapshot.source_manifests
        }
        grouped: dict[str, list[_FrozenPage]] = {}
        for page in snapshot.pages:
            source_id = str(page.manifest["source_file_id"])
            grouped.setdefault(source_id, []).append(page)
        self._pages_by_source = {
            source_id: tuple(values) for source_id, values in grouped.items()
        }

    @property
    def pages(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(page.manifest) for page in self._snapshot.pages)

    def validate(self, sources: Sequence[DesktopSourceFile]) -> None:
        current = tuple(_source_manifest(source) for source in sources)
        if current != self._snapshot.source_manifests:
            raise VisualEgressError(
                "visual_egress_source_changed",
                "预览后批次原文件或来源顺序已经变化，请重新预览。",
            )
        source_by_id = {
            str(source.effective_source_file_id): source for source in sources
        }
        for page in self._snapshot.pages:
            manifest = page.manifest
            raw = page.pixels
            page_id = str(manifest["page_id"])
            if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
                raise VisualEgressError(
                    "visual_egress_page_changed",
                    f"第 {page_id} 页图片内容已经变化，请重新预览。",
                )
            try:
                width, height = _image_dimensions(raw, str(manifest["mime_type"]))
            except DesktopImportBridgeError as exc:
                raise VisualEgressError(
                    "visual_egress_page_invalid", "预览页面像素无法再次核对。"
                ) from exc
            if (width, height) != (
                manifest["width"],
                manifest["height"],
            ) or len(raw) != manifest["size_bytes"]:
                raise VisualEgressError(
                    "visual_egress_page_changed",
                    "预览页面尺寸或大小已经变化，请重新预览。",
                )
            source = source_by_id.get(str(manifest["source_file_id"]))
            if source is None:
                raise VisualEgressError(
                    "visual_egress_source_changed",
                    "预览页面对应的来源已经变化，请重新预览。",
                )
            if (
                source.mime_type in {"image/png", "image/jpeg", "image/webp"}
                and (
                    manifest["page_number"] != 1
                    or hashlib.sha256(source.content).hexdigest()
                    != manifest["sha256"]
                )
            ):
                # Direct image pages are identity-rendered by the intake core.
                raise VisualEgressError(
                    "visual_egress_page_changed",
                    "原始页面内容已经变化，请重新预览。",
                )

    def render(
        self, source_file: Any, *, source_role: str
    ) -> tuple[RenderedPixelPage, ...]:
        source_id = getattr(source_file, "source_file_id", None)
        if not isinstance(source_id, str) or not source_id:
            raise VisualEgressError(
                "visual_egress_source_changed", "预览页面来源标识已经变化。"
            )
        source = self._sources_by_id.get(source_id)
        if source is None or source.get("role") != source_role:
            raise VisualEgressError(
                "visual_egress_source_changed", "预览页面来源角色已经变化。"
            )
        content = getattr(source_file, "content", None)
        if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != source.get(
            "source_sha256"
        ):
            raise VisualEgressError(
                "visual_egress_source_changed",
                "预览后批次原文件已经变化，请重新预览。",
            )
        frozen_pages = self._pages_by_source.get(source_id, ())
        if not frozen_pages:
            raise VisualEgressError(
                "visual_egress_page_missing", "预览中没有找到对应的页面像素。"
            )
        return tuple(
            RenderedPixelPage(
                pixels=page.pixels,
                mime_type=str(page.manifest["mime_type"]),
                width=int(page.manifest["width"]),
                height=int(page.manifest["height"]),
                render_recipe_sha256=str(page.manifest["render_recipe_sha256"]),
            )
            for page in frozen_pages
        )


class DesktopVisualEgressService:
    """Create and consume local frozen page previews for one facade."""

    def __init__(self, facade: Any) -> None:
        self.facade = facade
        self._lock = threading.RLock()
        self._snapshots: dict[str, _PreviewSnapshot] = {}

    @staticmethod
    def _selected_sources(
        sources: Sequence[DesktopSourceFile], plan: Any
    ) -> tuple[DesktopSourceFile, ...]:
        selected_ids = set(plan.visual_source_ids) | set(
            plan.visual_context_source_ids
        )
        role_order = {role: index for index, role in enumerate(SOURCE_ROLES)}
        selected = tuple(
            sorted(
                (
                    source
                    for source in sources
                    if source.effective_source_file_id in selected_ids
                ),
                key=lambda source: (role_order[source.role], source.order_index),
            )
        )
        if not selected:
            raise VisualEgressError(
                "visual_egress_pages_missing", "该批次没有可发送的视觉页面。"
            )
        return selected

    @staticmethod
    def _page_manifest(
        page: Any, *, batch_id: str, source_name: str
    ) -> dict[str, Any]:
        subject = {
            "batch_id": batch_id,
            "source_file_id": page.source_file_id,
            "source_role": page.source_role,
            "source_order": page.source_order,
            "page_number": page.page_number,
            "sha256": page.page_sha256,
        }
        page_id = _PAGE_PREFIX + _digest(subject)[:32]
        return {
            "page_id": page_id,
            "source_file_id": page.source_file_id,
            "source_name": source_name,
            "source_role": page.source_role,
            "source_order": page.source_order,
            "page_number": page.page_number,
            "mime_type": page.mime_type,
            "width": page.width,
            "height": page.height,
            "size_bytes": len(page.pixels),
            "sha256": page.page_sha256,
            "render_recipe_sha256": page.render_recipe_sha256,
        }

    @staticmethod
    def _confirmation_text(
        model_label: str, pages: Sequence[Mapping[str, Any]], policy: Mapping[str, Any]
    ) -> str:
        lines = [
            f"接收模型：{model_label}。",
            f"发送内容：本批次已冻结的 {len(pages)} 张原始或本机渲染页面像素。",
            f"每次请求输出上限 {policy['max_output_tokens']} tokens（包含模型推理）；最长等待 {policy['timeout_seconds']} 秒。",
            "题目、答案、讲义分别分批请求；不会自动重试，失败时不采用残缺结果。",
            "请在图片预览中逐页检查题面、公共材料、答案页与化学图形；确认后只发送这些冻结像素。",
            "图内全部可见内容及文件元数据会离开本机；请先核对学校授权，并确认不含未经授权的个人信息。",
        ]
        for index, page in enumerate(pages, 1):
            lines.append(
                f"{index}. {page['source_name']} · {page['source_role']} · "
                f"第 {page['page_number']} 页 · {page['width']} × {page['height']} 像素"
            )
        lines.extend(
            (
                "确认前不会调用模型；本地预览不会发送页面。",
                "本次调用可能产生费用，重试可能再次计费。",
            )
        )
        return "\n".join(lines)

    def preview(
        self,
        *,
        batch_id: str,
        profile_id: str,
        expected_profile_revision: str,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if should_cancel is not None and should_cancel():
            raise VisualEgressError("visual_egress_cancelled", "图片预览已取消。")
        descriptor = self.facade._saved_visual_import_batch(batch_id)
        if descriptor.get("status") == "candidate_ready_for_review":
            raise VisualEgressError(
                "visual_egress_batch_completed", "该批次已完成视觉识别，不能重复发送。"
            )
        profile = self.facade._visual_import_profile(
            profile_id, expected_profile_revision
        )
        sources = self.facade._restore_visual_import_sources(descriptor)
        source_type = str(descriptor.get("source_type") or "未分类资料")
        request = DesktopImportRequest(
            sources=sources,
            batch_id=batch_id,
            source_type=source_type,
        )
        try:
            coordinator = DesktopImportCoordinatorV2(
                renderer=self.facade._visual_import_renderer,
                archive_root=None,
            )
            plan = coordinator.plan(request)
            if plan.batch_id != batch_id:
                raise VisualEgressError(
                    "visual_egress_batch_changed", "已保存批次的来源集合无法核验。"
                )
            selected = self._selected_sources(sources, plan)
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "planned",
                        "batch_id": batch_id,
                        "source_count": len(sources),
                    }
                )
            rendered_pages = []
            frozen_bytes = 0
            for source in selected:
                if should_cancel is not None and should_cancel():
                    raise VisualEgressError(
                        "visual_egress_cancelled", "图片预览已取消。"
                    )
                source_shards, _unused_archive = _render_visual_pages(
                    (source,),
                    renderer=self.facade._visual_import_renderer,
                    batch_id=batch_id,
                    max_pages_per_shard=coordinator.max_pages_per_shard,
                    archive=None,
                )
                source_pages = [
                    page for shard in source_shards for page in shard.pages
                ]
                frozen_bytes += sum(len(page.pixels) for page in source_pages)
                if frozen_bytes > MAX_EGRESS_BYTES:
                    raise VisualEgressError(
                        "visual_egress_bytes_exceeded",
                        "本批冻结页面超过512MiB，无法安全预览；请分批处理。",
                    )
                rendered_pages.extend(source_pages)
                if len(rendered_pages) > MAX_EGRESS_PAGES:
                    raise VisualEgressError(
                        "visual_egress_pages_invalid",
                        "批次页面数量超过500页，请分批处理。",
                    )
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "rendered_source",
                            "batch_id": batch_id,
                            "source_file_id": source.effective_source_file_id,
                            "page_count": len(rendered_pages),
                        }
                    )
                if should_cancel is not None and should_cancel():
                    raise VisualEgressError(
                        "visual_egress_cancelled", "图片预览已取消。"
                    )
        except VisualEgressError:
            raise
        except DesktopImportBridgeError as exc:
            raise VisualEgressError(exc.code, exc.message_zh) from exc
        except Exception as exc:
            raise VisualEgressError(
                "visual_egress_render_failed", "批次页面暂时无法渲染，请重新打开批次重试。"
            ) from exc
        if should_cancel is not None and should_cancel():
            raise VisualEgressError("visual_egress_cancelled", "图片预览已取消。")
        pages: list[_FrozenPage] = []
        names = {
            source.effective_source_file_id: source.filename for source in selected
        }
        for visual_page in rendered_pages:
            manifest = self._page_manifest(
                visual_page,
                batch_id=batch_id,
                source_name=names[visual_page.source_file_id],
            )
            pages.append(_FrozenPage(manifest, bytes(visual_page.pixels)))
        if not 1 <= len(pages) <= MAX_EGRESS_PAGES:
            raise VisualEgressError(
                "visual_egress_pages_invalid", "批次页面数量不在可预览范围内。"
            )
        source_manifests = tuple(_source_manifest(source) for source in sources)
        page_subject = [dict(page.manifest) for page in pages]
        model_label = _model_label(profile)
        request_policy = _request_policy(profile)
        revision = "ve_rev_" + _digest(
            {
                "batch_id": batch_id,
                "profile_id": profile_id,
                "profile_revision": expected_profile_revision,
                "model_label": model_label,
                "request_policy": request_policy,
                "sources": list(source_manifests),
                "pages": page_subject,
            }
        )
        with self._lock:
            preview_id = ""
            while not preview_id or preview_id in self._snapshots:
                preview_id = _PREVIEW_PREFIX + uuid.uuid4().hex
            same_batch = [
                key
                for key, value in self._snapshots.items()
                if value.batch_id == batch_id
            ]
            for key in same_batch:
                self._snapshots.pop(key, None)
            if len(self._snapshots) >= MAX_ACTIVE_SNAPSHOTS:
                raise VisualEgressError(
                    "visual_egress_snapshot_limit",
                    "已有两个未关闭的图片预览，请先返回或关闭旧预览。",
                )
            snapshot = _PreviewSnapshot(
                preview_id=preview_id,
                revision=revision,
                batch_id=batch_id,
                profile_id=profile_id,
                profile_revision=expected_profile_revision,
                model_label=model_label,
                request_policy=deepcopy(request_policy),
                source_manifests=source_manifests,
                pages=tuple(pages),
            )
            self._snapshots[preview_id] = snapshot
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "completed",
                    "batch_id": batch_id,
                    "page_count": len(pages),
                }
            )
        public_pages = [deepcopy(page.manifest) for page in pages]
        return {
            "preview_id": preview_id,
            "revision": revision,
            "batch_id": batch_id,
            "profile_id": profile_id,
            "profile_revision": expected_profile_revision,
            "model_label": model_label,
            "request_policy": deepcopy(request_policy),
            "pages": public_pages,
            "confirmation_text": self._confirmation_text(model_label, public_pages, request_policy),
        }

    def _snapshot(
        self,
        preview_id: str,
        revision: str,
        *,
        batch_id: str | None = None,
        profile_id: str | None = None,
        profile_revision: str | None = None,
    ) -> _PreviewSnapshot:
        with self._lock:
            snapshot = self._snapshots.get(preview_id)
        if snapshot is None or snapshot.revision != revision:
            raise VisualEgressError(
                "visual_egress_preview_stale", "图片预览已失效，请重新预览。"
            )
        if batch_id is not None and snapshot.batch_id != batch_id:
            raise VisualEgressError(
                "visual_egress_batch_changed", "预览与当前批次不一致，请重新预览。"
            )
        if profile_id is not None and snapshot.profile_id != profile_id:
            raise VisualEgressError(
                "visual_egress_profile_changed", "预览与当前模型不一致，请重新预览。"
            )
        if (
            profile_revision is not None
            and snapshot.profile_revision != profile_revision
        ):
            raise VisualEgressError(
                "visual_egress_profile_changed", "视觉模型配置已变化，请重新预览。"
            )
        return snapshot

    def image(self, preview_id: str, revision: str, page_id: str) -> bytes:
        snapshot = self._snapshot(preview_id, revision)
        for page in snapshot.pages:
            if page.manifest["page_id"] == page_id:
                raw = bytes(page.pixels)
                if hashlib.sha256(raw).hexdigest() != page.manifest["sha256"]:
                    raise VisualEgressError(
                        "visual_egress_page_changed", "预览页面已经变化，请重新预览。"
                    )
                return raw
        raise VisualEgressError("visual_egress_page_missing", "未找到这张预览页面。")

    def frozen_renderer(
        self,
        *,
        batch_id: str,
        profile_id: str,
        expected_profile_revision: str,
        preview_id: str,
        revision: str,
        sources: Sequence[DesktopSourceFile],
    ) -> FrozenPageRenderer:
        snapshot = self._snapshot(
            preview_id,
            revision,
            batch_id=batch_id,
            profile_id=profile_id,
            profile_revision=expected_profile_revision,
        )
        profile = self.facade._visual_import_profile(profile_id, expected_profile_revision)
        if _request_policy(profile) != snapshot.request_policy:
            raise VisualEgressError(
                "visual_egress_policy_changed", "识别预算或处理规则已变化，请重新预览并确认。"
            )
        renderer = FrozenPageRenderer(snapshot)
        renderer.validate(sources)
        return renderer

    def discard(self, preview_id: str) -> bool:
        with self._lock:
            return self._snapshots.pop(preview_id, None) is not None
