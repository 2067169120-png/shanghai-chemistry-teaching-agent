"""Frozen local page previews for saved visual-import batches.

The visual-import workflow has two distinct local stages:

* the teacher previews the exact source pages that would leave the machine;
* a later confirmed run sends those same page bytes to the visual provider.

This module owns the in-process snapshot between those stages. Preview and
selection changes are read-only. A confirmed run can activate a new checkpoint
attempt while holding the batch lock; old records and original archives remain.
It never borrows credentials, invokes a provider, or writes a candidate.
``discard`` releases only the memory-backed snapshot.
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
from .intake_batches_v2 import RenderedPixelPage, VisualShardRequest
from .desktop_visual_checkpoints import VisualCheckpointError, VisualShardCheckpoints, preview_shards

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
        max_input_tokens=profile.get("max_input_tokens"),
        max_output_tokens=profile.get("max_output_tokens"),
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
    requests: tuple[VisualShardRequest, ...]
    checkpoint_revision: str
    reprocess_shard_ids: tuple[str, ...] = ()
    repair_mode: bool = False
    repair_option_ids: tuple[str, ...] = ()


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
        outbound = [page for page in pages if page.get("will_send", True)]
        reused = len(pages) - len(outbound)
        if pages and not outbound:
            return (
                f"本机恢复候选：{reused} 页所属分片已保存并核验。\n"
                "本次只在本机汇总，不发送页面或调用模型；已保存结果仍待教师核对。\n"
                "下方可查看绑定的原页；页面或处理进度变化后需重新预览。"
            )
        lines = [
            f"接收模型：{model_label}。",
            f"首轮发送内容：本批次已冻结的 {len(outbound)} 张原始或本机渲染页面像素。",
            f"本机复用 {reused} 页已通过提取和裁片机器复核的分片；这些页面本次不重新发送，仍待教师核对。",
            f"每次请求输出上限 {policy['max_output_tokens']} tokens（包含模型推理）；最长等待 {policy['timeout_seconds']} 秒。",
            (f"本机输入预算 {policy['max_input_tokens']} tokens（文字及图片估算，非服务商精确用量）。"
             if policy.get("max_input_tokens") is not None else
             "未设置本机输入tokens预算（可在模型设置调整，参考64000）；仍保留文件与请求体安全限制。"),
            "输入超预算不会静默截断，请分批或调整预算；输出上限使用用户设置，实际限制以服务商为准。",
            "题目、答案、讲义分别分片；每个分片先发送 1 次提取请求。",
            f"追加图像复核：提取后，每最多 {policy['crop_review_batch_limit']} 条证据裁片分为一组，追加 1 次原页与对应实际裁片的视觉复核请求。",
            "费用与请求次数：追加复核按提取后的裁片数分组调用并计费；总请求次数在提取完成后才能确定，实际费用按服务商规则计算。",
            "当前图片预览可查看全部冻结源页；实际裁片在提取后才能生成，尚未在本次发送前预览中展示。",
            "复核只发送本次冻结源页及从这些页面生成的实际裁片，不加入任何未预览的源页。",
            "复核也会携带首轮AI观察得到的题目结构、文字和引用关系作为待核对数据，不附加其他本地资料。",
            "提取或复核失败时不完成导入，不会默认为通过；不会自动重试或自动修复，也不采用残缺结果。",
            "请在图片预览中逐页检查题面、公共材料、答案页与化学图形，并核对上述追加复核范围。",
            "标为“本次发送”的页面中全部可见内容及文件元数据会离开本机；请先核对学校授权，并确认不含未经授权的个人信息。",
        ]
        for index, page in enumerate(pages, 1):
            lines.append(
                f"{index}. {'本次发送' if page.get('will_send', True) else '本机复用'} · {page['source_name']} · {page['source_role']} · "
                f"第 {page['page_number']} 页 · {page['width']} × {page['height']} 像素"
            )
        lines.extend(
            (
                "确认前不会调用模型；本地预览不会发送页面。",
                "本次提取与追加复核均可能产生费用；以后手动重新发起可能再次计费。",
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
        if self.facade.import_batch_details(batch_id).visual_status in {"completed", "not_required"}:
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
        model_label = _model_label(profile)
        request_policy = _request_policy(profile)
        requests = preview_shards(rendered_pages, batch_id=batch_id, max_pages_per_shard=coordinator.max_pages_per_shard)
        checkpoints = VisualShardCheckpoints(
            self.facade._visual_import_root / "shard-checkpoints",
            batch_id=batch_id, sources=source_manifests,
            profile_id=profile_id, profile_revision=expected_profile_revision,
            model_label=model_label, request_policy=request_policy, requests=requests,
        )
        try:
            checkpoint_snapshot = checkpoints.inspect(diagnostics=True)
            repair_snapshot = None
        except VisualCheckpointError as exc:
            if exc.code not in {"visual_checkpoint_invalid",
                                "visual_checkpoint_repair_required", "visual_checkpoint_repair_incomplete"}:
                raise
            repair_snapshot = checkpoints.inspect_repair()
            checkpoint_snapshot = None
        if progress_callback is not None:
            progress_callback({"stage": "completed", "batch_id": batch_id, "page_count": len(pages)})
        return self._publish(
            batch_id=batch_id, profile_id=profile_id, expected_profile_revision=expected_profile_revision,
            model_label=model_label, request_policy=request_policy, source_manifests=source_manifests,
            pages=pages, requests=requests, checkpoint_snapshot=checkpoint_snapshot,
            repair_snapshot=repair_snapshot,
        )

    def _publish(self, *, batch_id, profile_id, expected_profile_revision, model_label,
                 request_policy, source_manifests, pages, requests, checkpoint_snapshot,
                 reprocess_shard_ids=(), repair_snapshot=None, repair_option_ids=()) -> dict[str, Any]:
        if repair_snapshot is not None:
            return self._publish_repair(
                batch_id=batch_id, profile_id=profile_id, expected_profile_revision=expected_profile_revision,
                model_label=model_label, request_policy=request_policy, source_manifests=source_manifests,
                pages=pages, requests=requests, repair_snapshot=repair_snapshot, option_ids=repair_option_ids,
            )
        selected = VisualShardCheckpoints.validate_selection(checkpoint_snapshot, reprocess_shard_ids)
        invalid_ids = set(checkpoint_snapshot["invalid_shard_ids"])
        blocked_ids = invalid_ids - set(selected)
        completed_ids = set(checkpoint_snapshot["completed_shard_ids"]) - set(selected)
        reuse = {(page.source_file_id, page.page_number): request.shard_id in completed_ids
                 for request in requests for page in request.pages}
        shard_for_page = {(page.source_file_id, page.page_number): request.shard_id
                          for request in requests for page in request.pages}
        pages = [_FrozenPage(deepcopy(page.manifest), page.pixels) for page in pages]
        for page in pages:
            key = (page.manifest["source_file_id"], page.manifest["page_number"])
            shard_id = shard_for_page[key]
            page.manifest.update(
                shard_id=shard_id, will_send=not reuse[key] and shard_id not in blocked_ids,
                checkpoint_state=("blocked" if shard_id in blocked_ids else
                                  "reprocess" if shard_id in selected else
                                  "saved" if shard_id in completed_ids else "pending"),
            )
        page_subject = [dict(page.manifest) for page in pages]
        revision = "ve_rev_" + _digest(
            {
                "batch_id": batch_id,
                "profile_id": profile_id,
                "profile_revision": expected_profile_revision,
                "model_label": model_label,
                "request_policy": request_policy,
                "checkpoint_revision": checkpoint_snapshot["revision"],
                "reprocess_shard_ids": selected,
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
                requests=requests,
                checkpoint_revision=checkpoint_snapshot["revision"],
                reprocess_shard_ids=selected,
            )
            self._snapshots[preview_id] = snapshot
        public_pages = [deepcopy(page.manifest) for page in pages]
        resume = {key: checkpoint_snapshot[key] for key in ("total_shards", "completed_shards", "pending_shards", "has_other_scopes")}
        resume.update(
            completed_shards=len(completed_ids), pending_shards=len(requests) - len(completed_ids) - len(blocked_ids),
            blocked_shards=len(blocked_ids), reprocess_shards=len(selected),
            reused_page_count=sum(page["checkpoint_state"] == "saved" for page in public_pages),
            send_page_count=sum(page["will_send"] for page in public_pages),
            blocked_page_count=sum(page["checkpoint_state"] == "blocked" for page in public_pages),
        )
        shards = [{
            "shard_id": request.shard_id, "shard_index": request.shard_index, "source_role": request.source_role,
            "page_ids": [page["page_id"] for page in public_pages if page["shard_id"] == request.shard_id],
            "stored_state": "invalid" if request.shard_id in invalid_ids else
                            "saved" if request.shard_id in checkpoint_snapshot["completed_shard_ids"] else "pending",
            "reprocess": request.shard_id in selected,
        } for request in requests]
        confirmation = ("已保存结果有无法核验的页组。请在“处理进度”选择重做并更新预览；当前不能发送或汇总。"
                        if blocked_ids else self._confirmation_text(model_label, public_pages, request_policy))
        if selected:
            confirmation = f"已选择重做 {len(selected)} 组；确认后才启用新记录，旧记录保留。重做会再次调用模型并可能计费。\n" + confirmation
        if resume["has_other_scopes"] and not resume["completed_shards"]:
            confirmation = "已有分片不匹配当前页面或模型配置，本次需要重新处理；旧记录保留。\n" + confirmation
        if not resume["pending_shards"] and not blocked_ids:
            confirmation = "所有分片已核验，本次只在本机汇总候选，不发送页面或调用模型。\n" + confirmation
        return {
            "preview_id": preview_id,
            "revision": revision,
            "batch_id": batch_id,
            "profile_id": profile_id,
            "profile_revision": expected_profile_revision,
            "model_label": model_label,
            "request_policy": deepcopy(request_policy),
            "pages": public_pages,
            "resume": resume,
            "shards": shards,
            "can_confirm": not blocked_ids,
            "confirmation_text": confirmation,
        }

    def revise(self, preview_id: str, revision: str, reprocess_shard_ids: Sequence[str]) -> dict[str, Any]:
        """Change sending intent only; keep all saved records untouched."""
        snapshot = self._snapshot(preview_id, revision)
        if snapshot.repair_mode:
            raise VisualCheckpointError("visual_checkpoint_repair_required")
        if self.facade.import_batch_details(snapshot.batch_id).visual_status in {"completed", "not_required"}:
            raise VisualEgressError("visual_egress_batch_completed", "该批次已完成视觉识别，不能重复发送。")
        profile = self.facade._visual_import_profile(snapshot.profile_id, snapshot.profile_revision)
        if _request_policy(profile) != snapshot.request_policy:
            raise VisualEgressError("visual_egress_policy_changed", "识别预算或处理规则已变化，请重新预览并确认。")
        descriptor = self.facade._saved_visual_import_batch(snapshot.batch_id)
        FrozenPageRenderer(snapshot).validate(self.facade._restore_visual_import_sources(descriptor))
        checkpoints = self._checkpoints(snapshot)
        current = checkpoints.inspect(diagnostics=True)
        if current["revision"] != snapshot.checkpoint_revision:
            raise VisualCheckpointError("visual_checkpoint_stale")
        return self._publish(
            batch_id=snapshot.batch_id, profile_id=snapshot.profile_id, expected_profile_revision=snapshot.profile_revision,
            model_label=snapshot.model_label, request_policy=snapshot.request_policy,
            source_manifests=snapshot.source_manifests, pages=snapshot.pages, requests=snapshot.requests,
            checkpoint_snapshot=current, reprocess_shard_ids=reprocess_shard_ids,
        )

    def _publish_repair(self, *, batch_id, profile_id, expected_profile_revision, model_label,
                        request_policy, source_manifests, pages, requests, repair_snapshot, option_ids=()):
        from .desktop_visual_checkpoint_repair import validate_options

        selected = validate_options(repair_snapshot, option_ids) if option_ids else []
        chosen = {item["shard_id"] for item in selected}
        option_ids = tuple(sorted(option_ids))
        pages = [_FrozenPage(deepcopy(page.manifest), page.pixels) for page in pages]
        shard_for_page = {(p.source_file_id, p.page_number): r.shard_id for r in requests for p in r.pages}
        for page in pages:
            shard_id = shard_for_page[(page.manifest["source_file_id"], page.manifest["page_number"])]
            page.manifest.update(shard_id=shard_id, will_send=False,
                                 checkpoint_state="repair_selected" if shard_id in chosen else "repair_pending")
        revision = "ve_rev_" + _digest({"repair": repair_snapshot["revision"], "selected": option_ids,
                                        "sources": source_manifests, "pages": [p.manifest for p in pages]})
        with self._lock:
            for key in [key for key, value in self._snapshots.items() if value.batch_id == batch_id]:
                self._snapshots.pop(key)
            if len(self._snapshots) >= MAX_ACTIVE_SNAPSHOTS:
                raise VisualEgressError("visual_egress_snapshot_limit", "请先关闭其它图片预览。")
            preview_id = _PREVIEW_PREFIX + uuid.uuid4().hex
            self._snapshots[preview_id] = _PreviewSnapshot(
                preview_id, revision, batch_id, profile_id, expected_profile_revision, model_label,
                deepcopy(request_policy), source_manifests, tuple(pages), requests,
                repair_snapshot["revision"], repair_mode=True, repair_option_ids=option_ids,
            )
        messages = {
            "scope_missing": "处理范围记录缺失。",
            "scope_unreadable": "处理范围记录损坏，无法读取。",
            "active_missing": "当前使用的页组版本记录缺失，不能自动选择旧版本。",
            "active_unreadable": "当前使用的页组版本记录损坏，不能自动选择旧版本。",
            "repair_incomplete": "上次本机修复未完成，已阻止继续识别。",
        }
        public_pages = [deepcopy(page.manifest) for page in pages]
        options = [{key: value for key, value in option.items() if key not in {"record_path", "record_sha256"}}
                   for option in repair_snapshot["options"]]
        available = {item["shard_id"] for item in options}
        missing = len(requests) - len(available)
        selected_count = len(chosen)
        confirmation = (
            "\n".join(messages[issue] for issue in repair_snapshot["issues"])
            + "\n可选记录已重新核对来源文件、页面像素、模型配置、处理规则及页组内容。"
            + "\n同组多个版本必须由你选择；不会按日期自动选用。点击记录可读摘录，切换原页可核对范围。"
            + f"\n本次选择恢复 {selected_count} 组；{len(requests) - selected_count} 组保持待处理。"
            + "\n确认只在本机修复目录，保留旧记录与原目录字节；不会调用模型。修复后返回普通预览。"
            + "\n尚未恢复的页面如需识别，必须在普通预览再次确认发送。恢复结果仍待教师核对。"
        )
        return {
            "preview_id": preview_id, "revision": revision, "batch_id": batch_id,
            "profile_id": profile_id, "profile_revision": expected_profile_revision,
            "model_label": model_label, "request_policy": deepcopy(request_policy), "pages": public_pages,
            "repair": {"issues": [messages[issue] for issue in repair_snapshot["issues"]],
                       "options": options, "selected_option_ids": list(option_ids),
                       "selected_shards": selected_count, "unavailable_shards": missing},
            "shards": [{"shard_id": r.shard_id, "shard_index": r.shard_index,
                        "page_ids": [p["page_id"] for p in public_pages if p["shard_id"] == r.shard_id],
                        "unavailable_reason": repair_snapshot["unavailable_reasons"].get(
                            r.shard_id, "未找到这组的可用旧记录；本次保持待处理。") if r.shard_id not in available else ""}
                       for r in requests],
            "can_confirm": bool(selected), "confirmation_text": confirmation,
        }

    def _revalidate_repair(self, snapshot):
        if self.facade.import_batch_details(snapshot.batch_id).visual_status in {"completed", "not_required"}:
            raise VisualEgressError("visual_egress_batch_completed", "该批次已完成识别，请重新打开批次。")
        profile = self.facade._visual_import_profile(snapshot.profile_id, snapshot.profile_revision)
        if _request_policy(profile) != snapshot.request_policy or _model_label(profile) != snapshot.model_label:
            raise VisualEgressError("visual_egress_policy_changed", "模型或处理规则已变化，请重新预览。")
        descriptor = self.facade._saved_visual_import_batch(snapshot.batch_id)
        FrozenPageRenderer(snapshot).validate(self.facade._restore_visual_import_sources(descriptor))
        if not snapshot.repair_mode:
            raise VisualCheckpointError("visual_checkpoint_repair_selection")

    def revise_repair(self, preview_id, revision, option_ids):
        snapshot = self._snapshot(preview_id, revision)
        self._revalidate_repair(snapshot)
        current = self._checkpoints(snapshot).inspect_repair()
        if current["revision"] != snapshot.checkpoint_revision:
            raise VisualCheckpointError("visual_checkpoint_stale")
        return self._publish_repair(
            batch_id=snapshot.batch_id, profile_id=snapshot.profile_id, expected_profile_revision=snapshot.profile_revision,
            model_label=snapshot.model_label, request_policy=snapshot.request_policy, source_manifests=snapshot.source_manifests,
            pages=snapshot.pages, requests=snapshot.requests, repair_snapshot=current, option_ids=option_ids,
        )

    def repair(self, preview_id, revision, *, batch_id, teacher_confirmed, should_cancel=None):
        """Caller holds the batch lock. Return an ordinary preview, never a run."""
        if teacher_confirmed is not True:
            raise VisualEgressError("teacher_confirmation_required", "本机修复前需要明确确认所选记录。")
        snapshot = self._snapshot(preview_id, revision, batch_id=batch_id)
        self._revalidate_repair(snapshot)
        current = self._checkpoints(snapshot).repair_metadata(
            snapshot.checkpoint_revision, snapshot.repair_option_ids, should_cancel=should_cancel,
            revalidate=lambda: self._revalidate_repair(snapshot),
        )
        return self._publish(
            batch_id=snapshot.batch_id, profile_id=snapshot.profile_id, expected_profile_revision=snapshot.profile_revision,
            model_label=snapshot.model_label, request_policy=snapshot.request_policy, source_manifests=snapshot.source_manifests,
            pages=snapshot.pages, requests=snapshot.requests, checkpoint_snapshot=current,
        )

    def _checkpoints(self, snapshot: _PreviewSnapshot) -> VisualShardCheckpoints:
        return VisualShardCheckpoints(
            self.facade._visual_import_root / "shard-checkpoints",
            batch_id=snapshot.batch_id, sources=snapshot.source_manifests,
            profile_id=snapshot.profile_id, profile_revision=snapshot.profile_revision,
            model_label=snapshot.model_label, request_policy=snapshot.request_policy, requests=snapshot.requests,
        )

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
        if snapshot.repair_mode:
            raise VisualCheckpointError("visual_checkpoint_repair_required")
        profile = self.facade._visual_import_profile(profile_id, expected_profile_revision)
        if _request_policy(profile) != snapshot.request_policy:
            raise VisualEgressError(
                "visual_egress_policy_changed", "识别预算或处理规则已变化，请重新预览并确认。"
            )
        renderer = FrozenPageRenderer(snapshot)
        renderer.validate(sources)
        checkpoints = self._checkpoints(snapshot)
        checkpoint_snapshot = checkpoints.select_reprocess(snapshot.checkpoint_revision, snapshot.reprocess_shard_ids)
        renderer.checkpoints = checkpoints
        renderer.pending_shards = checkpoint_snapshot["pending_shards"]
        return renderer

    def discard(self, preview_id: str) -> bool:
        with self._lock:
            return self._snapshots.pop(preview_id, None) is not None
