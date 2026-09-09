"""Typed, in-process facade used by the native Windows workbench.

This module intentionally contains no Qt dependency.  It talks to the existing
read-only chemistry readers and personal-state managers directly.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from .candidate_review import Wave1CandidateReviewReader
from .curriculum_workbench import CurriculumWorkbenchReader
from .desktop_library import (
    LibraryImage,
    LibraryThemeDetail,
    build_theme_detail,
    image_descriptors,
)
from .desktop_library_session import snapshot_reader_graph
from .desktop_paths import DesktopPaths
from .desktop_preparation import DesktopPreparationError, DesktopPreparationManager
from .desktop_preparation_provider import StructuredPreparationProvider
from .desktop_provider_probe import (
    ProviderConnectionResult,
    connection_result_from_state,
    run_connection_test,
)
from .desktop_registry_cache import DesktopRegistryCache
from .desktop_state import DesktopStateStore, utc_now
from .desktop_visual_import_adapters import (
    LocalPageRendererV2Adapter,
    NativeWordHandoutImporterAdapter,
    StructuredVisualShardProviderAdapter,
)
from .desktop_visual_import_v2 import (
    DesktopImportBridgeError,
    DesktopImportCoordinatorV2,
    DesktopImportRequest,
    DesktopImportResult,
    DesktopSourceFile,
)
from .master_direct_visual_scan import MasterDirectVisualScanReader
from .master_wave1_workbench import MasterWave1WorkbenchReader
from .model_provider_settings import ModelProviderSettingsStore
from .paper_export_renderer import ARTIFACT_FILENAMES
from .paper_export_workbench import (
    PaperExportJobManager,
    PaperExportWorkbenchError,
)
from .question_search_workbench import VALUE_LABELS_ZH, QuestionSearchWorkbench
from .question_visual_scan import QuestionVisualScanReader
from .reader_cancellation import (
    ReadCancelled,
    ReaderThreadPoolExecutor,
    check_read_cancelled,
    read_cancel_scope,
)
from .student_visual_analysis import (
    StudentVisualAnalysisError,
    StudentVisualAnalysisManager,
)
from .supplemental_visual_scan import SupplementalVisualScanReader
from .theme_workbench import ThemeWorkbenchReader
from .word_handout_import import (
    ImportProgress,
    WordHandoutImporter,
    WordHandoutImportError,
    inspect_docx_native_summary,
)

PRIMARY_NAVIGATION = ("首页", "题库", "组卷", "学生分析", "备课")
DESKTOP_SCOPES = ("master", "wave1", "supplemental")
PERSONAL_HANDOUT_SCOPE = "personal_handouts"
DESKTOP_REGISTRY_SCHEMA = "shchem.desktop-product-status.v1"
_SCOPE_LABELS = {
    "master": "主索引题库",
    "wave1": "精细标注题库",
    "supplemental": "补充资料题库",
}
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PAPER_PREVIEW_ACTIVE_DRAFT = "paper-preview-active"
_PAPER_EXPORT_WAIT_SECONDS = 180.0
_VISUAL_IMPORT_DRAFT_PREFIX = "visual-import-v2:"
_VISUAL_IMPORT_DRAFT_SCHEMA = "shchem.desktop-visual-import-draft.v1"
_STUDENT_VISUAL_ROOT_NAME = "student-visual-v1"
_STUDENT_SOURCE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
}
_STUDENT_ROLES = (
    "question_pages",
    "reference_answer_pages",
    "student_work_pages",
)
_STUDENT_ROLE_LABELS = {
    "question_pages": "题目页面",
    "reference_answer_pages": "参考答案页面",
    "student_work_pages": "学生作答页面",
}
_STUDENT_ACTIVE_STATUSES = {
    "queued_for_analysis",
    "analyzing",
    "cancel_requested",
}
_STUDENT_RETRYABLE_STATUSES = {"analysis_failed", "awaiting_visual_provider"}
_STUDENT_STATUS_LABELS = {
    "awaiting_upload": "等待本机材料",
    "upload_processing_failed": "页面准备失败",
    "awaiting_privacy_review": "等待页面匹配",
    "awaiting_matching_confirmation": "等待页面匹配",
    "ready_for_analysis": "可以开始分析",
    "queued_for_analysis": "等待模型分析",
    "analyzing": "模型分析中",
    "cancel_requested": "正在停止",
    "cancelled": "已停止",
    "awaiting_visual_provider": "模型配置待处理",
    "analysis_failed": "分析未完成",
    "awaiting_teacher_review": "等待教师复核",
}
_STUDENT_ERROR_MESSAGES = {
    "student_not_found": "找不到这个匿名学生，请刷新后重试。",
    "submission_not_found": "找不到这次分析，请刷新最近任务。",
    "student_scope_denied": "这次分析不属于当前匿名学生。",
    "revision_conflict": "任务内容已经变化，已拒绝旧操作；请刷新后重试。",
    "submission_pages_incomplete": "请先添加题目页面和学生作答页面。",
    "matching_teacher_confirmation_required": "请先核对并确认页面匹配。",
    "privacy_decision_required": "页面或模型配置已经变化，请重新确认隐私与发送范围。",
    "student_page_egress_confirmation_required": "首次向这个模型发送学生页面，需要教师明确确认。",
    "awaiting_visual_provider": "视觉模型暂时不可用，请检查设置后重试。",
    "vision_capability_unconfirmed": "模型的视觉能力尚无目录或明确声明证据。",
    "structured_output_capability_required": "所选模型未确认支持结构化输出。",
    "source_image_data_class_not_allowed": "所选模型配置未允许题目或参考答案图片。",
    "student_image_data_class_not_allowed": "所选模型配置未允许学生作答图片。",
    "student_image_egress_not_allowed": "所选模型配置未允许教师确认后的全部视觉页面发送。",
    "stale_provider_revision": "模型配置已经变化，请重新选择并确认。",
    "analysis_scheduler_unavailable": "学生分析后台任务暂时不可用，请关闭并重新打开工作台后重试。",
    "analysis_already_completed": "这次分析已经返回候选，无需重复调用模型。",
    "submission_cancelled": "已停止的提交不能重新分析，请新建一次分析。",
    "analysis_not_ready": "模型候选尚未准备好。",
    "scoring_decision_required": "请先记录本题教师评分。",
    "scoring_decision_not_latest": "本题评分已经变化，请刷新后再记录诊断。",
    "curriculum_section_unknown": "所选教材章节不在当前目录中，请刷新后重选。",
    "teacher_score_invalid": "教师分数超出本题分值范围。",
    "diagnostic_evidence_required": "确认失分诊断时，请选择错误类型和教材章节。",
    "diagnostic_error_not_allowed": "正确、未评分、拒绝或待定状态不能同时记录错误分类。",
    "upload_too_large": "单个文件超过当前学生分析的大小限制。",
    "file_mime_invalid": "文件格式不受支持；请选择 PNG、JPG、WebP、PDF 或 DOCX。",
    "filename_invalid": "文件名或扩展名不符合学生分析要求。",
    "page_store_corrupt": "本机页面校验失败，请新建一次分析并重新导入。",
    "submission_page_duplicate": "检测到内容完全相同的重复页面；请从所选文件中删除重复页后新建一次分析。",
}
IMPORT_STATES = (
    "native_text_complete",
    "hybrid_visual_required",
    "visual_only_required",
)


class DesktopFacadeError(RuntimeError):
    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


class ProviderSettingsPort(Protocol):
    def list_metadata(self) -> list[dict[str, Any]]: ...

    def upsert_metadata(
        self, value: Mapping[str, Any], *, expected_revision: str | None
    ) -> dict[str, Any]: ...

    def put_credential(
        self, profile_id: str, secret: str, *, expected_revision: str
    ) -> dict[str, Any]: ...

    def delete_credential(
        self, profile_id: str, *, expected_revision: str
    ) -> dict[str, Any]: ...

    def borrow_invocation_context(
        self, profile_id: str, *, expected_revision: str
    ) -> AbstractContextManager[Any]: ...


@dataclass(frozen=True)
class ProductStatus:
    product_id: str
    label_zh: str
    loaded: bool
    papers: int | None
    themes: int | None
    atomic_parts: int | None
    pending_parent_review: int | None
    message_zh: str
    error_code: str | None = None


@dataclass(frozen=True)
class CurriculumStatus:
    loaded: bool
    volumes: int | None
    chapters: int | None
    sections: int | None
    mapped_atomic_parts: int | None
    message_zh: str
    error_code: str | None = None


@dataclass(frozen=True)
class DesktopRegistry:
    schema_version: str
    loaded_at: str
    products: tuple[ProductStatus, ...]
    curriculum: CurriculumStatus
    cross_scope_sum_allowed: bool = False

    @property
    def fully_loaded(self) -> bool:
        return self.curriculum.loaded and all(item.loaded for item in self.products)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "loaded_at": self.loaded_at,
            "products": [asdict(item) for item in self.products],
            "curriculum": asdict(self.curriculum),
            "combined_atomic_total": None,
            "cross_scope_sum_allowed": self.cross_scope_sum_allowed,
        }


@dataclass(frozen=True)
class ThemeCard:
    key: str
    scope: str
    title_zh: str
    paper_title_zh: str
    source_zh: str
    atomic_total: int
    atomic_matched: int
    shared_context_zh: str
    page_zh: str
    # Opaque identity/snapshot values are for reopening a basket and are never
    # rendered in the native teacher UI.  Defaults keep older injected cards
    # source-compatible.
    source_identity_sha256: str = ""
    data_snapshot_id: str = ""
    # Canonical inventory remains atomic_total; grouped direct/Wave aliases
    # may expose more independently answerable units in the native viewer.
    display_atomic_units: int = 0


@dataclass(frozen=True)
class ThemeSearchResult:
    scope: str
    query: str
    total_themes: int
    matched_atomic_parts: int
    cards: tuple[ThemeCard, ...]
    has_more: bool


@dataclass(frozen=True)
class ProviderProfileInput:
    provider_name: str
    base_url: str
    model_id: str
    api_style: str = "responses"
    profile_id: str = "desktop-default"
    vision_enabled: bool = True
    key_value: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class ProviderProfileSummary:
    profile_id: str
    provider_name: str
    base_url: str
    model_id: str
    api_style: str
    capabilities: tuple[str, ...]
    key_saved: bool
    revision: str
    last_connection_test: ProviderConnectionResult | None = None


@dataclass(frozen=True)
class DraftReceipt:
    draft_id: str
    kind: str
    saved_at: str
    state: str
    message_zh: str


@dataclass(frozen=True)
class PaperPreview:
    preview_id: str
    title_zh: str
    mode_zh: str
    theme_count: int
    theme_titles: tuple[str, ...]
    export_ready: bool
    blockers: tuple[str, ...]
    # Rich native projection fields were added after the first desktop shell.
    # Defaults keep existing facade callers source-compatible while allowing
    # the Qt composer to render the complete theme/question hierarchy.
    preview_model: Mapping[str, Any] = field(default_factory=dict)
    preview_hash: str = ""
    approved: bool = False


@dataclass(frozen=True)
class PreparationAvailability:
    provider_ready: bool
    renderer_ready: bool
    message_zh: str


@dataclass(frozen=True)
class PreparationTaskSummary:
    task_id: str
    status: str
    title_zh: str
    output_kind: str
    created_at: str
    updated_at: str
    progress_percent: int
    message_zh: str
    artifact_ids: tuple[str, ...] = ()
    slide_count: int = 0
    retryable: bool = False
    source_kind: str | None = None
    candidate_only: bool = True
    teacher_review_required: bool = True
    publication_allowed: bool = False
    returned_candidate_available: bool = False


@dataclass(frozen=True)
class StudentProfileSummary:
    """Teacher-facing anonymous profile; the identifier is an action token only."""

    student_id: str = field(repr=False)
    label_zh: str
    grade: str
    retention_days: int
    created_at: str


@dataclass(frozen=True)
class StudentFileSummary:
    """One locally archived source file without its former local path or name."""

    file_id: str = field(repr=False)
    role: str
    role_zh: str
    state: str
    page_count: int


@dataclass(frozen=True)
class StudentPageSummary:
    """A safe UI page descriptor.

    ``sha256`` is an opaque exact-pixel action binding.  It is intentionally
    excluded from ``repr`` and must never be rendered in the teacher UI.
    """

    file_id: str = field(repr=False)
    sha256: str = field(repr=False)
    role: str
    role_zh: str
    ordinal: int
    page_number: int
    label_zh: str
    mime_type: str
    width: int
    height: int


@dataclass(frozen=True)
class StudentMatchSummary:
    """Editable page pairing with domain-owned identifiers kept opaque."""

    match_id: str = field(repr=False)
    question_page_sha256: str = field(repr=False)
    student_work_page_sha256: str = field(repr=False)
    reference_answer_page_sha256: str | None = field(repr=False)
    label_zh: str
    question_number_hint: str
    maximum_score: float


@dataclass(frozen=True)
class StudentSubmissionSummary:
    """Closed desktop projection of a private student submission."""

    student_id: str = field(repr=False)
    submission_id: str = field(repr=False)
    revision: str = field(repr=False)
    status: str
    status_zh: str
    message_zh: str
    created_at: str
    updated_at: str
    files: tuple[StudentFileSummary, ...]
    pages: tuple[StudentPageSummary, ...]
    matches: tuple[StudentMatchSummary, ...]
    page_counts_by_role: Mapping[str, int]
    match_count: int
    scoring_confirmed_count: int
    diagnostic_confirmed_count: int
    candidate_available: bool
    can_confirm_matching: bool
    can_analyze: bool
    can_retry: bool
    can_cancel: bool


@dataclass(frozen=True)
class StudentAnalysisConfirmation:
    """Frozen, exact-page/provider confirmation prepared before any egress."""

    student_id: str = field(repr=False)
    submission_id: str = field(repr=False)
    expected_revision: str = field(repr=False)
    provider_profile_id: str = field(repr=False)
    provider_revision: str = field(repr=False)
    page_sha256: tuple[str, ...] = field(repr=False)
    provider_label_zh: str
    total_page_count: int
    page_counts_by_role: Mapping[str, int]
    student_label_zh: str
    retention_days: int
    message_zh: str


@dataclass(frozen=True)
class StudentReviewItem:
    """Teacher-visible projection of one model candidate and append-only review."""

    match_id: str = field(repr=False)
    latest_scoring_decision_id: str | None = field(default=None, repr=False)
    label_zh: str = ""
    question_number: str = ""
    maximum_score: float = 0.0
    suggested_score: float | None = None
    suggested_score_withheld: bool = False
    confidence: float | None = None
    observation_zh: str = ""
    chemistry_observations_zh: tuple[str, ...] = ()
    scoring_points_zh: tuple[str, ...] = ()
    error_hypotheses_zh: tuple[str, ...] = ()
    blockers_zh: tuple[str, ...] = ()
    latest_teacher_score: float | None = None
    latest_diagnostic_decision: str | None = None


@dataclass(frozen=True)
class StudentAnalysisReview:
    student_id: str = field(repr=False)
    submission_id: str = field(repr=False)
    revision: str = field(repr=False)
    status: str
    status_zh: str
    message_zh: str
    items: tuple[StudentReviewItem, ...]
    candidate_blockers_zh: tuple[str, ...]
    scoring_confirmed_count: int
    diagnostic_confirmed_count: int
    review_complete: bool
    candidate_only: bool = True
    long_term_update_allowed: bool = False


@dataclass(frozen=True)
class CurriculumSectionSummary:
    section_key: str = field(repr=False)
    display_label_zh: str


@dataclass(frozen=True)
class DesktopVisualImportSourceSummary:
    role: str
    order_index: int
    filename: str
    import_state: str


@dataclass(frozen=True)
class DesktopVisualImportReceipt:
    """Teacher-facing visual import summary without paths, hashes or bytes."""

    batch_id: str
    source_type: str
    status: str
    visual_status: str
    source_count: int
    native_quick_count: int
    visual_queue_count: int
    sources: tuple[DesktopVisualImportSourceSummary, ...]
    message_zh: str
    candidate_only: bool = True
    central_question_bank_write: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "source_type": self.source_type,
            "status": self.status,
            "visual_status": self.visual_status,
            "source_count": self.source_count,
            "native_quick_count": self.native_quick_count,
            "visual_queue_count": self.visual_queue_count,
            "sources": [asdict(item) for item in self.sources],
            "message_zh": self.message_zh,
            "candidate_only": self.candidate_only,
            "central_question_bank_write": self.central_question_bank_write,
        }


def _error_code(error: BaseException) -> str:
    value = getattr(error, "code", None)
    if isinstance(value, str) and _SAFE_ERROR_CODE.fullmatch(value):
        return value
    return type(error).__name__


def _safe_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class DesktopWorkbenchFacade:
    """Desktop-facing coordinator with injectable pure-Python dependencies."""

    def __init__(
        self,
        paths: DesktopPaths,
        *,
        theme_reader: Any | None = None,
        supplemental_reader: Any | None = None,
        curriculum_reader: Any | None = None,
        search_reader: Any | None = None,
        provider_store: ProviderSettingsPort | None = None,
        state_store: DesktopStateStore | None = None,
        registry_cache: DesktopRegistryCache | None = None,
        paper_export_jobs: Any | None = None,
        wave_visual_reader: Any | None = None,
        wave_crop_reader: Any | None = None,
        master_workbench_reader: Any | None = None,
        master_direct_reader: Any | None = None,
        visual_import_transport: Any | None = None,
        visual_import_renderer: Any | None = None,
        visual_import_native_importer: Any | None = None,
        preparation_manager: Any | None = None,
        preparation_renderer: Any | None = None,
        preparation_transport: Any | None = None,
        student_analysis_manager: Any | None = None,
        student_analysis_renderer: Any | None = None,
        student_analysis_transport: Any | None = None,
        connection_test_transport: Any | None = None,
        blueprint_transport: Any | None = None,
        blueprint_review_transport: Any | None = None,
    ) -> None:
        self.paths = paths
        self.paths.validate_read_roots()
        self.paths.ensure_mutable_roots()
        self._themes = theme_reader or ThemeWorkbenchReader(paths.shchem_root)
        self._supplemental = supplemental_reader or SupplementalVisualScanReader(
            paths.shchem_root
        )
        self._curriculum = curriculum_reader or CurriculumWorkbenchReader(
            paths.shchem_root
        )
        self._search = search_reader or QuestionSearchWorkbench()
        self._providers = provider_store or ModelProviderSettingsStore(
            paths.settings_root,
            project_root=paths.workspace_root,
        )
        self._state = state_store or DesktopStateStore(paths.state_root)
        self._connection_test_transport = connection_test_transport
        self._connection_test_lock = threading.Lock()
        self._blueprint_transport = blueprint_transport
        self._blueprint_review_transport = blueprint_review_transport
        self._blueprint_generation_lock = threading.Lock()
        self._registry_cache = registry_cache or DesktopRegistryCache(paths.state_root)
        self._paper_export_jobs = paper_export_jobs or PaperExportJobManager(
            paths.state_root
        )
        # The default ThemeWorkbenchReader already owns the same immutable
        # readers needed by export.  Reuse them when available, while keeping
        # narrow injection points for the native facade tests.
        self._wave_visual = wave_visual_reader or getattr(
            self._themes, "wave_scans", None
        )
        self._wave_crops = wave_crop_reader or getattr(
            self._themes, "wave_review", None
        )
        self._master_workbench = master_workbench_reader or getattr(
            self._themes, "master_workbench", None
        )
        self._master_direct = master_direct_reader or getattr(
            self._themes, "direct_scans", None
        )
        self._visual_import_transport = visual_import_transport
        self._visual_import_renderer = (
            visual_import_renderer
            or LocalPageRendererV2Adapter(
                temporary_parent=paths.state_root / "visual-import-v2" / "temporary"
            )
        )
        self._visual_import_native_importer = (
            visual_import_native_importer
            or NativeWordHandoutImporterAdapter(
                paths.state_root / "word-handout-import",
                temporary_parent=paths.state_root / "visual-import-v2" / "temporary",
            )
        )
        self._preparation_manager = preparation_manager
        self._preparation_renderer = preparation_renderer
        self._preparation_transport = preparation_transport
        self._preparation_lock = threading.RLock()
        self._student_analysis_manager = student_analysis_manager
        self._student_analysis_renderer = student_analysis_renderer
        self._student_analysis_transport = student_analysis_transport
        self._student_analysis_lock = threading.RLock()
        self._student_analysis_closed = False
        self._reader_stop_event = threading.Event()
        # The curriculum tree is immutable for the active local snapshot.  A
        # small in-process cache keeps opening the library filter and changing
        # between scopes instantaneous after the first read, while the
        # underlying CurriculumWorkbenchReader remains the sole authority for
        # explicit mapping edges.
        self._curriculum_catalog_cache: dict[str, Any] | None = None
        self._library_views: dict[str, tuple[Any, ...]] = {}
        self._library_view_lock = threading.RLock()

    def _load_theme_scope(self, scope: str) -> dict[str, Any]:
        # One catalog operation shares validated dependency snapshots just as
        # a detail view does. Never patch the app readers or cache across calls:
        # a later search must revalidate and observe changed source inputs.
        if scope == "supplemental":
            (reader,) = snapshot_reader_graph((self._supplemental,))
            return reader.theme_groups()
        if scope in {"master", "wave1"}:
            (reader,) = snapshot_reader_graph((self._themes,))
            return reader.groups(scope)
        raise DesktopFacadeError("scope_invalid", "题库范围不正确。")

    @staticmethod
    def _product_status(scope: str, value: dict[str, Any]) -> ProductStatus:
        counts = value.get("counts")
        if not isinstance(counts, dict) or value.get("scope") != scope:
            raise DesktopFacadeError(
                "product_projection_invalid", "题库状态格式不正确。"
            )
        return ProductStatus(
            product_id=scope,
            label_zh=_SCOPE_LABELS[scope],
            loaded=True,
            papers=_safe_int(counts.get("papers")),
            themes=_safe_int(counts.get("theme_groups")),
            atomic_parts=_safe_int(counts.get("atomic_parts")),
            pending_parent_review=_safe_int(counts.get("unassigned_atomic_parts")),
            message_zh="已从本地题库读取",
        )

    @staticmethod
    def _failed_product(scope: str, error: BaseException) -> ProductStatus:
        return ProductStatus(
            product_id=scope,
            label_zh=_SCOPE_LABELS[scope],
            loaded=False,
            papers=None,
            themes=None,
            atomic_parts=None,
            pending_parent_review=None,
            message_zh="本地题库暂时无法读取，可稍后重试。",
            error_code=_error_code(error),
        )

    @staticmethod
    def _registry_from_cache(value: Mapping[str, Any]) -> DesktopRegistry | None:
        if value.get("schema_version") != DESKTOP_REGISTRY_SCHEMA:
            return None
        raw_products = value.get("products")
        raw_curriculum = value.get("curriculum")
        loaded_at = value.get("loaded_at")
        if (
            not isinstance(raw_products, list)
            or not isinstance(raw_curriculum, dict)
            or not isinstance(loaded_at, str)
        ):
            return None
        products: list[ProductStatus] = []
        for raw in raw_products:
            if not isinstance(raw, dict) or raw.get("product_id") not in DESKTOP_SCOPES:
                return None
            required = {
                "label_zh",
                "loaded",
                "papers",
                "themes",
                "atomic_parts",
                "pending_parent_review",
                "message_zh",
                "error_code",
            }
            if not required.issubset(raw):
                return None
            if type(raw.get("loaded")) is not bool:
                return None
            products.append(
                ProductStatus(
                    product_id=str(raw["product_id"]),
                    label_zh=str(raw["label_zh"]),
                    loaded=raw["loaded"],
                    papers=_safe_int(raw.get("papers")),
                    themes=_safe_int(raw.get("themes")),
                    atomic_parts=_safe_int(raw.get("atomic_parts")),
                    pending_parent_review=_safe_int(raw.get("pending_parent_review")),
                    message_zh=str(raw["message_zh"]),
                    error_code=(
                        str(raw["error_code"])
                        if isinstance(raw.get("error_code"), str)
                        else None
                    ),
                )
            )
        required_curriculum = {
            "loaded",
            "volumes",
            "chapters",
            "sections",
            "mapped_atomic_parts",
            "message_zh",
            "error_code",
        }
        if (
            not required_curriculum.issubset(raw_curriculum)
            or type(raw_curriculum.get("loaded")) is not bool
        ):
            return None
        curriculum = CurriculumStatus(
            loaded=raw_curriculum["loaded"],
            volumes=_safe_int(raw_curriculum.get("volumes")),
            chapters=_safe_int(raw_curriculum.get("chapters")),
            sections=_safe_int(raw_curriculum.get("sections")),
            mapped_atomic_parts=_safe_int(raw_curriculum.get("mapped_atomic_parts")),
            message_zh=str(raw_curriculum["message_zh"]),
            error_code=(
                str(raw_curriculum["error_code"])
                if isinstance(raw_curriculum.get("error_code"), str)
                else None
            ),
        )
        if len(products) != len(DESKTOP_SCOPES):
            return None
        return DesktopRegistry(
            schema_version=DESKTOP_REGISTRY_SCHEMA,
            loaded_at=loaded_at,
            products=tuple(products),
            curriculum=curriculum,
            cross_scope_sum_allowed=False,
        )

    def load_desktop_registry(self, *, force_refresh: bool = False) -> DesktopRegistry:
        """Read real local products and curriculum without a transport layer."""

        with read_cancel_scope(self._reader_stop_event):
            return self._read_desktop_registry(force_refresh=force_refresh)

    def _read_desktop_registry(self, *, force_refresh: bool) -> DesktopRegistry:
        if not force_refresh:
            cached = self._registry_cache.read()
            if cached is not None:
                projected = self._registry_from_cache(cached)
                if projected is not None:
                    return projected

        with ReaderThreadPoolExecutor(
            max_workers=4, thread_name_prefix="shchem-desktop-read"
        ) as pool:
            product_futures = {
                scope: pool.submit(self._load_theme_scope, scope)
                for scope in DESKTOP_SCOPES
            }
            curriculum_future = pool.submit(self._curriculum.catalog)
            products: list[ProductStatus] = []
            for scope in DESKTOP_SCOPES:
                try:
                    products.append(
                        self._product_status(scope, product_futures[scope].result())
                    )
                except ReadCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - isolate product cards
                    products.append(self._failed_product(scope, exc))
            try:
                curriculum = curriculum_future.result()
                counts = curriculum.get("counts")
                if not isinstance(counts, dict):
                    raise DesktopFacadeError(
                        "curriculum_projection_invalid", "教材目录状态格式不正确。"
                    )
                curriculum_status = CurriculumStatus(
                    loaded=True,
                    volumes=_safe_int(counts.get("volumes")),
                    chapters=_safe_int(counts.get("chapters")),
                    sections=_safe_int(counts.get("sections")),
                    mapped_atomic_parts=_safe_int(counts.get("active_atomic_mappings")),
                    message_zh="已从本地教材目录读取",
                )
            except ReadCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate curriculum status
                curriculum_status = CurriculumStatus(
                    loaded=False,
                    volumes=None,
                    chapters=None,
                    sections=None,
                    mapped_atomic_parts=None,
                    message_zh="教材目录暂时无法读取，可稍后重试。",
                    error_code=_error_code(exc),
                )
        check_read_cancelled()
        registry = DesktopRegistry(
            schema_version=DESKTOP_REGISTRY_SCHEMA,
            loaded_at=utc_now(),
            products=tuple(products),
            curriculum=curriculum_status,
        )
        if registry.fully_loaded:
            try:
                self._registry_cache.write(registry.as_dict())
            except OSError:
                # A read-only or unavailable personal state directory must
                # not prevent the teacher from viewing the live projection.
                pass
        return registry

    @staticmethod
    def _source_label(item: dict[str, Any]) -> str:
        metadata = item.get("source_metadata")
        if not isinstance(metadata, dict):
            return "来源待核验"
        values = [metadata.get(key) for key in ("year", "region", "paper_type")]
        safe: list[str] = []
        for value in values:
            if not isinstance(value, (str, int)) or not value:
                continue
            text = str(value).strip()
            # Some legacy projections use the machine sentinel ``unknown``.
            # It is useful in the data contract but should never leak into a
            # teacher-facing source label; keep the whole label in Chinese.
            if text.casefold() in {"unknown", "none", "null", "pending"}:
                continue
            safe.append(VALUE_LABELS_ZH.get(text, text))
        return " · ".join(safe) if safe else "来源待核验"

    @staticmethod
    def _page_label(item: dict[str, Any]) -> str:
        theme = item.get("theme")
        span = theme.get("page_span") if isinstance(theme, dict) else None
        if not isinstance(span, dict):
            return "页码待核验"
        # Native readers expose page_numbers; older fixtures used pages.
        # Do not infer intermediate pages from a start/end range.
        pages = span.get("page_numbers", span.get("pages"))
        if (
            isinstance(pages, list)
            and pages
            and all(type(page) is int and page > 0 for page in pages)
        ):
            return "第 " + "、".join(str(page) for page in pages) + " 页"
        return "页码待核验"

    def search_themes(
        self,
        *,
        scope: str = "master",
        query: str = "",
        limit: int = 30,
        volume_id: str | None = None,
        chapter_id: str | None = None,
        section: str | None = None,
        mapping_status: str | None = None,
        curriculum: Mapping[str, str] | None = None,
    ) -> ThemeSearchResult:
        """Search complete theme cards, optionally by explicit textbook edges.

        ``volume_id``/``chapter_id``/``section`` are passed verbatim to the
        curriculum reader's public search contract.  In particular, the
        facade never derives a section from a K tag, title, filename, or
        question text.  The optional selector is intentionally unavailable to
        the personal Word-handout lane, whose native text candidates do not
        carry canonical textbook mappings.
        """
        selector: dict[str, str] = {}
        if curriculum is not None:
            if not isinstance(curriculum, Mapping):
                raise DesktopFacadeError(
                    "curriculum_selector_invalid", "教材筛选条件不正确。"
                )
            allowed_keys = {
                "volume_id",
                "chapter_id",
                "section",
                "mapping_status",
            }
            if set(curriculum) - allowed_keys:
                raise DesktopFacadeError(
                    "curriculum_selector_invalid", "教材筛选条件不正确。"
                )
            for key, value in curriculum.items():
                if not isinstance(value, str) or not value.strip():
                    raise DesktopFacadeError(
                        "curriculum_selector_invalid", "教材筛选条件不正确。"
                    )
                selector[key] = value.strip()
        for key, value in (
            ("volume_id", volume_id),
            ("chapter_id", chapter_id),
            ("section", section),
            ("mapping_status", mapping_status),
        ):
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise DesktopFacadeError(
                        "curriculum_selector_invalid", "教材筛选条件不正确。"
                    )
                if key in selector and selector[key] != value.strip():
                    raise DesktopFacadeError(
                        "curriculum_selector_conflict", "教材筛选条件存在冲突。"
                    )
                selector[key] = value.strip()
        if scope == PERSONAL_HANDOUT_SCOPE:
            if selector:
                raise DesktopFacadeError(
                    "curriculum_not_supported_for_personal_scope",
                    "我的讲义尚未建立教材目录映射，请先清除教材筛选。",
                )
            return self.search_personal_handouts(query=query, limit=limit)
        if scope not in DESKTOP_SCOPES:
            raise DesktopFacadeError("scope_invalid", "题库范围不正确。")
        normalized_query = query.strip()
        payload: dict[str, Any] = {
            "scope": scope,
            "filters": {},
            "limit": max(1, min(int(limit), 50)),
        }
        if normalized_query:
            payload["q"] = normalized_query
        if selector:
            payload["curriculum"] = selector
        source_snapshots: dict[str, str] = {}

        def load_search_scope(source_scope: str) -> dict[str, Any]:
            catalog = self._load_theme_scope(source_scope)
            source_snapshots[source_scope] = self._paper_catalog_snapshot_id(catalog)
            return catalog

        search_kwargs: dict[str, Any] = {"theme_loader": load_search_scope}
        if selector:
            search_kwargs["curriculum_loader"] = self._curriculum_search
        value = self._search.search(payload, **search_kwargs)
        cards: list[ThemeCard] = []
        snapshot_id = value.get("data_snapshot_id")
        snapshot_id = snapshot_id if isinstance(snapshot_id, str) else ""
        for item in value.get("items", []):
            if not isinstance(item, dict):
                continue
            paper = item.get("paper") if isinstance(item.get("paper"), dict) else {}
            theme = item.get("theme") if isinstance(item.get("theme"), dict) else {}
            counts = item.get("counts") if isinstance(item.get("counts"), dict) else {}
            shared = (
                item.get("shared_context")
                if isinstance(item.get("shared_context"), dict)
                else {}
            )
            key_source = {
                "scope": scope,
                "paper": paper.get("id"),
                "theme": theme.get("id"),
            }
            identity_digest = _canonical_digest(key_source)
            cards.append(
                ThemeCard(
                    key=identity_digest,
                    scope=scope,
                    title_zh=str(
                        item.get("display_title_zh")
                        or theme.get("title")
                        or "未命名主题"
                    ),
                    paper_title_zh=str(paper.get("title") or "来源卷名称待核验"),
                    source_zh=self._source_label(item),
                    atomic_total=_safe_int(counts.get("atomic_total")) or 0,
                    atomic_matched=_safe_int(counts.get("atomic_matched")) or 0,
                    display_atomic_units=(
                        _safe_int(counts.get("display_atomic_units"))
                        or _safe_int(counts.get("atomic_total"))
                        or 0
                    ),
                    shared_context_zh=str(
                        shared.get("context_summary_zh") or "共同材料摘要待整理"
                    ),
                    page_zh=self._page_label(item),
                    source_identity_sha256=identity_digest,
                    data_snapshot_id=source_snapshots.get(scope)
                    or (
                        str(item.get("data_snapshot_id"))
                        if isinstance(item.get("data_snapshot_id"), str)
                        else snapshot_id
                    ),
                )
            )
        page = value.get("page") if isinstance(value.get("page"), dict) else {}
        counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
        return ThemeSearchResult(
            scope=scope,
            query=normalized_query,
            total_themes=_safe_int(page.get("total_theme_cards")) or len(cards),
            matched_atomic_parts=_safe_int(counts.get("atomic_parts_matched")) or 0,
            cards=tuple(cards),
            has_more=page.get("has_more") is True,
        )

    def curriculum_catalog(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """Return the explicit five-volume textbook tree for the desktop UI.

        The returned dictionary is a deep copy so combo-box code cannot mutate
        the reader snapshot.  ``force_refresh`` is useful after a teacher
        changes the active local data snapshot; normal UI navigation uses the
        cheap cached path.
        """

        with read_cancel_scope(self._reader_stop_event):
            return self._read_curriculum_catalog(force_refresh=force_refresh)

    def _read_curriculum_catalog(self, *, force_refresh: bool) -> dict[str, Any]:
        if not force_refresh and self._curriculum_catalog_cache is not None:
            return json.loads(
                json.dumps(self._curriculum_catalog_cache, ensure_ascii=False)
            )
        try:
            value = self._curriculum.catalog()
        except ReadCancelled:
            raise
        except Exception as exc:
            raise DesktopFacadeError(
                "curriculum_catalog_unavailable", "教材目录暂时无法读取，请稍后重试。"
            ) from exc
        if not isinstance(value, dict) or not isinstance(value.get("volumes"), list):
            raise DesktopFacadeError(
                "curriculum_catalog_invalid", "教材目录格式不正确，请稍后重试。"
            )
        check_read_cancelled()
        self._curriculum_catalog_cache = json.loads(
            json.dumps(value, ensure_ascii=False)
        )
        return json.loads(json.dumps(value, ensure_ascii=False))

    def _curriculum_search(self, selector: Mapping[str, str]) -> dict[str, Any]:
        """Adapt the mapping reader's keyword API to the search reader port."""

        try:
            value = self._curriculum.search(**dict(selector))
        except Exception as exc:
            code = _error_code(exc)
            message = getattr(exc, "message_zh", None)
            if isinstance(message, str) and message:
                raise DesktopFacadeError(code, message) from exc
            raise DesktopFacadeError(
                code,
                "教材筛选条件无法应用，请重新选择册、章或节。",
            ) from exc
        if not isinstance(value, dict):
            raise DesktopFacadeError(
                "curriculum_mapping_invalid", "教材映射结果格式不正确。"
            )
        return value

    def search_personal_handouts(
        self, *, query: str = "", state: str | None = None, limit: int = 40
    ) -> ThemeSearchResult:
        """Project imported Word candidates as compact, theme-like groups.

        The personal handout lane is deliberately separate from the central
        Shanghai exam scopes.  A group is one source document + detected
        section, so related questions stay together while still retaining the
        atomic candidate count and visual-completion state.
        """

        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise DesktopFacadeError("limit_invalid", "题库显示数量不正确。")
        inventory = self.personal_handout_inventory(
            query=query,
            state=state,
            limit=500,
            offset=0,
        )
        raw_items = inventory.get("items") if isinstance(inventory, dict) else None
        if not isinstance(raw_items, list):
            raise DesktopFacadeError(
                "personal_inventory_invalid", "我的讲义库存格式不正确。"
            )
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            package = str(raw.get("package_id") or "来源包待核验")
            document = str(raw.get("source_document_name") or "Word 文档待核验")
            section = str(raw.get("section_title") or "未命名主题")
            groups.setdefault((package, document, section), []).append(raw)

        cards: list[ThemeCard] = []
        for index, ((package, document, section), items) in enumerate(
            sorted(groups.items())
        ):
            quick = sum(item.get("quick_import_eligible") is True for item in items)
            visual = len(items) - quick
            paired = sum(item.get("pairing_status") == "paired" for item in items)
            title = section if section != "未命名主题" else f"{package} · {document}"
            key = _canonical_digest(
                {
                    "scope": PERSONAL_HANDOUT_SCOPE,
                    "package": package,
                    "document": document,
                    "section": section,
                }
            )
            cards.append(
                ThemeCard(
                    key=key,
                    scope=PERSONAL_HANDOUT_SCOPE,
                    title_zh=title,
                    paper_title_zh=f"{package} · {document}",
                    source_zh=(
                        f"一轮复习 Word 讲义 · 可直接入库 {quick} · "
                        f"待视觉补全 {visual} · 已配对解析 {paired}"
                    ),
                    atomic_total=len(items),
                    atomic_matched=len(items),
                    shared_context_zh=(
                        "原生文字候选可直接使用；图片、公式、结构式或装置图 "
                        "需视觉补全。"
                    ),
                    page_zh="页码以原 Word 版面为准，导入记录已保留原文档定位",
                )
            )
        has_more = inventory.get("page", {}).get("has_more") is True
        visible = tuple(cards[:limit])
        return ThemeSearchResult(
            scope=PERSONAL_HANDOUT_SCOPE,
            query=query.strip(),
            total_themes=len(cards) + (1 if has_more else 0),
            matched_atomic_parts=sum(card.atomic_matched for card in visible),
            cards=visible,
            has_more=has_more or len(cards) > limit,
        )

    def basket(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._state.basket())

    def paper_theme_catalog(self, scope: str = "master") -> dict[str, Any]:
        """Return the read-only complete theme projection for the composer.

        The method is deliberately narrow: it exposes the same candidate-only
        reader used by the library page and never mutates accepted crops or
        reconstructed source papers.  The Qt page invokes it through its task
        bridge because a full aggregate read can be relatively expensive.
        """

        return self._load_theme_scope(scope)

    def _library_scan(
        self, scope: str, node_id: str, readers: tuple[Any, ...] | None = None
    ) -> tuple[str, str, Mapping[str, Any]]:
        """Resolve only explicit direct/exact bindings, retaining image scope."""
        if readers is None:
            readers = (self._themes, self._supplemental, *self._paper_export_readers())
        _, supplemental, wave_visual, _, master_workbench, master_direct = readers
        if scope == "supplemental":
            detail = supplemental.detail(node_id)
            return scope, node_id, detail.get("visual_scan", detail)
        if scope == "wave1":
            return scope, node_id, wave_visual.detail(node_id)
        if scope != "master":
            raise DesktopFacadeError("scope_invalid", "题库范围不正确。")
        try:
            return scope, node_id, master_direct.detail(node_id)
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise
        detail = master_workbench.atomic_detail(node_id)
        node = detail.get("node")
        summary = node.get("crosswalk_summary") if isinstance(node, Mapping) else None
        ids = summary.get("wave1_node_ids") if isinstance(summary, Mapping) else None
        if (
            isinstance(summary, Mapping)
            and summary.get("state") == "exact"
            and isinstance(ids, list)
            and len(ids) == 1
            and isinstance(ids[0], str)
        ):
            return "wave1", ids[0], wave_visual.detail(ids[0])
        # Unmapped/split/anchor entries retain their own source-answer boundary
        # and metadata; they never borrow another node's image or answer.
        return scope, node_id, detail

    def library_theme_detail(self, card: ThemeCard) -> LibraryThemeDetail:
        """Read a complete selected theme for the native teacher viewer."""
        if not isinstance(card, ThemeCard) or card.scope not in DESKTOP_SCOPES:
            raise DesktopFacadeError(
                "library_detail_scope_invalid", "该资料范围尚未提供主题题图详情。"
            )
        try:
            readers = snapshot_reader_graph(
                (self._themes, self._supplemental, *self._paper_export_readers())
            )
            themes, supplemental, *_ = readers
            catalog = (
                supplemental.theme_groups()
                if card.scope == "supplemental"
                else themes.groups(card.scope)
            )
            snapshot = self._paper_catalog_snapshot_id(catalog)
            if card.data_snapshot_id and card.data_snapshot_id != snapshot:
                raise DesktopFacadeError(
                    "theme_snapshot_stale", "题库已变化，请重新查找并打开这个主题。"
                )
            matches = []
            for paper_entry in catalog.get("papers", []):
                paper = paper_entry.get("paper", {})
                for group in paper_entry.get("theme_groups", []):
                    identity = _canonical_digest(
                        {
                            "scope": card.scope,
                            "paper": paper.get("id"),
                            "theme": group.get("theme", {}).get("id"),
                        }
                    )
                    if identity == (card.source_identity_sha256 or card.key):
                        matches.append((paper, group))
            if len(matches) != 1:
                raise DesktopFacadeError(
                    "library_theme_not_found",
                    "主题来源缺失或不唯一，请刷新题库后重试。",
                )
            paper, group = matches[0]
            view_id = uuid.uuid4().hex
            detail = build_theme_detail(
                card,
                paper,
                group,
                lambda scope, node: self._library_scan(scope, node, readers),
                view_id=view_id,
            )
            with self._library_view_lock:
                self._library_views[view_id] = readers
                # A current viewer plus one transitioning viewer. Old image
                # requests fail clearly, never silently reopen changed files.
                while len(self._library_views) > 2:
                    del self._library_views[next(iter(self._library_views))]
            return detail
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise DesktopFacadeError(
                "library_detail_unavailable", "本地主题详情暂时无法读取，请稍后重试。"
            ) from exc

    def library_image(self, image: LibraryImage) -> bytes:
        """Return verified local question/shared bytes; no paths or HTTP fetch."""
        if not isinstance(image, LibraryImage) or image.role not in {
            "question",
            "shared_material",
        }:
            raise DesktopFacadeError(
                "library_image_role_denied", "此入口只显示题面与共同材料图片。"
            )
        try:
            with self._library_view_lock:
                readers = self._library_views.get(image.view_id)
            if readers is None:
                raise DesktopFacadeError(
                    "library_view_expired", "题图阅读会话已结束，请重新打开主题。"
                )
            scope, node_id, scan = self._library_scan(
                image.scope, image.node_id, readers
            )
            valid = image_descriptors(scope, node_id, scan)
            if not any(
                row.crop_id == image.crop_id
                and row.sha256 == image.sha256
                and row.role == image.role
                for row in valid
            ):
                raise DesktopFacadeError(
                    "library_image_stale",
                    "这张题图已变化或不属于所选题目，请重新打开主题。",
                )
            if scope == "supplemental":
                payload = readers[1].question_crop(node_id, image.crop_id)
            else:
                _, _, _, wave_crops, _, master_direct = readers
                reader = wave_crops if scope == "wave1" else master_direct
                payload = reader.question_crop(node_id, image.crop_id)
            if (
                not isinstance(payload.data, bytes)
                or payload.content_type != "image/png"
                or payload.sha256 != image.sha256
                or hashlib.sha256(payload.data).hexdigest() != image.sha256
            ):
                raise DesktopFacadeError(
                    "library_image_integrity_failed",
                    "题图校验失败，请重新读取来源资料。",
                )
            return payload.data
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise DesktopFacadeError(
                "library_image_unavailable",
                "这张题图暂时无法读取；未使用其他图片替代。",
            ) from exc

    def add_theme_to_basket(self, card: ThemeCard) -> int:
        # Persist only opaque identity material.  Raw paper/theme IDs remain in
        # the read-only catalog and never leak into the teacher-facing basket.
        identity = card.source_identity_sha256
        if not isinstance(identity, str) or not identity:
            identity = _canonical_digest(
                {
                    "scope": card.scope,
                    "paper_title": card.paper_title_zh,
                    "title": card.title_zh,
                    "atomic_total": card.atomic_total,
                }
            )
        return self._state.add_to_basket(
            {
                "key": identity,
                "scope": card.scope,
                "title_zh": card.title_zh,
                "paper_title_zh": card.paper_title_zh,
                "source_zh": card.source_zh,
                "shared_context_zh": card.shared_context_zh,
                "page_zh": card.page_zh,
                "atomic_total": card.atomic_total,
                "source_identity_sha256": identity,
                "data_snapshot_id": card.data_snapshot_id,
                "added_at": utc_now(),
            }
        )

    def clear_basket(self) -> None:
        self._state.clear_basket()

    @staticmethod
    def _profile_summary(value: Mapping[str, Any]) -> ProviderProfileSummary:
        capabilities = (
            value.get("effective_capabilities") or value.get("capabilities") or []
        )
        last_probe = value.get("last_probe")
        last_test = (
            connection_result_from_state(last_probe)
            if isinstance(last_probe, Mapping)
            and last_probe.get("status")
            in {"succeeded", "failed", "cancelled", "stale"}
            else None
        )
        return ProviderProfileSummary(
            profile_id=str(value.get("profile_id") or ""),
            provider_name=str(value.get("display_name") or "未命名模型服务"),
            base_url=str(value.get("base_url") or ""),
            model_id=str(value.get("model_id") or ""),
            api_style=str(value.get("api_style") or "responses"),
            capabilities=tuple(item for item in capabilities if isinstance(item, str)),
            key_saved=value.get("credential_state") == "configured",
            revision=str(value.get("revision") or ""),
            last_connection_test=last_test,
        )

    def list_provider_profiles(self) -> tuple[ProviderProfileSummary, ...]:
        return tuple(
            self._profile_summary(value) for value in self._providers.list_metadata()
        )

    def save_provider_profile(
        self, request: ProviderProfileInput
    ) -> ProviderProfileSummary:
        existing = {
            str(value.get("profile_id")): value
            for value in self._providers.list_metadata()
        }.get(request.profile_id)
        capabilities = ["text"]
        data_classes = ["synthetic_only", "question_text_redacted"]
        image_egress = "deny"
        if request.vision_enabled:
            capabilities.extend(("vision", "structured_output"))
            data_classes.extend(("source_page_image", "student_answer_image"))
            image_egress = "teacher_confirmed_visual_pages"
        payload = {
            "profile_id": request.profile_id,
            "provider_kind": "openai_compatible",
            "provider_id": "openai_compatible",
            "display_name": request.provider_name,
            "model_id": request.model_id,
            "base_url": request.base_url,
            "api_style": request.api_style,
            "local_endpoint_policy": "deny",
            "capabilities": capabilities,
            "allowed_data_classes": data_classes,
            "image_egress": image_egress,
        }
        expected_revision = str(existing.get("revision")) if existing else None
        if existing and existing.get("credential_state") == "configured":
            endpoint_changed = any(
                existing.get(key) != payload.get(key)
                for key in ("base_url", "api_style", "provider_kind", "provider_id")
            )
            if endpoint_changed:
                if not request.key_value:
                    raise DesktopFacadeError(
                        "key_rotation_required",
                        "更改模型服务地址时，请同时输入新的 Key。",
                    )
                removed = self._providers.delete_credential(
                    request.profile_id,
                    expected_revision=str(expected_revision),
                )
                expected_revision = str(removed.get("revision"))
        saved = self._providers.upsert_metadata(
            payload,
            expected_revision=expected_revision,
        )
        if request.key_value:
            saved = self._providers.put_credential(
                request.profile_id,
                request.key_value,
                expected_revision=str(saved.get("revision")),
            )
        return self._profile_summary(saved)

    def has_visual_profile(self) -> bool:
        return any(
            profile.key_saved
            and {"vision", "structured_output"}.issubset(profile.capabilities)
            for profile in self.list_provider_profiles()
        )

    def test_provider_connection(
        self,
        profile_id: str,
        *,
        expected_revision: str,
        confirmed: bool,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> tuple[ProviderConnectionResult, ProviderProfileSummary | None]:
        if not self._connection_test_lock.acquire(blocking=False):
            raise DesktopFacadeError(
                "probe_in_progress", "连接测试正在进行，请等待结束。"
            )
        try:
            result = run_connection_test(
                self._providers,
                profile_id,
                expected_revision=expected_revision,
                confirmed=confirmed,
                is_cancelled=is_cancelled,
                transport=self._connection_test_transport,
            )
            # A persisted receipt changes the revision, even when the test fails.
            refreshed = next(
                (
                    item
                    for item in self.list_provider_profiles()
                    if item.profile_id == profile_id
                ),
                None,
            )
            return result, refreshed
        finally:
            self._connection_test_lock.release()

    def has_text_profile(self) -> bool:
        return any(
            profile.key_saved and "text" in profile.capabilities
            for profile in self.list_provider_profiles()
        )

    @staticmethod
    def _docx_native_summary(path: Path) -> dict[str, Any]:
        try:
            return inspect_docx_native_summary(path)
        except WordHandoutImportError as exc:
            raise DesktopFacadeError("docx_invalid", exc.message_zh) from exc

    @staticmethod
    def _file_descriptors(paths: list[str | Path]) -> list[dict[str, Any]]:
        allowed = {".jpg", ".jpeg", ".png", ".heic", ".pdf", ".docx", ".pptx"}
        result: list[dict[str, Any]] = []
        for raw_path in paths:
            path = Path(raw_path).resolve()
            if not path.is_file() or path.suffix.casefold() not in allowed:
                raise DesktopFacadeError("source_file_invalid", "所选资料文件不可用。")
            digest = hashlib.sha256()
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise DesktopFacadeError(
                    "source_file_unreadable", "所选资料文件无法读取。"
                ) from exc
            descriptor = {
                "path": str(path),
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
                "import_state": "visual_only_required",
            }
            if path.suffix.casefold() == ".docx":
                descriptor.update(DesktopWorkbenchFacade._docx_native_summary(path))
            result.append(descriptor)
        if not result:
            raise DesktopFacadeError(
                "source_file_required", "请先选择至少一个资料文件。"
            )
        return result

    @staticmethod
    def _batch_import_state(descriptors: list[dict[str, Any]]) -> str:
        states = {descriptor.get("import_state") for descriptor in descriptors}
        if states == {"native_text_complete"}:
            return "native_text_complete"
        if states == {"visual_only_required"}:
            return "visual_only_required"
        return "hybrid_visual_required"

    def create_import_draft(
        self, *, files: list[str | Path], source_type: str
    ) -> DraftReceipt:
        descriptors = self._file_descriptors(files)
        saved_at = utc_now()
        draft_id = "import-" + uuid.uuid4().hex
        import_state = self._batch_import_state(descriptors)
        visual_ready = (
            self.has_visual_profile()
            if import_state != "native_text_complete"
            else False
        )
        payload = {
            "kind": "material_import",
            "source_type": source_type.strip() or "未分类资料",
            "files": descriptors,
            "status": import_state,
            "visual_profile_ready": visual_ready,
            "created_at": saved_at,
        }
        self._state.save_draft(draft_id, payload)
        if import_state == "native_text_complete":
            message = "已读取 DOCX 原生可编辑文字；请核对题号、答案关联与页面渲染。"
        elif import_state == "hybrid_visual_required":
            message = (
                "已读取可编辑文字；图片、公式或版面关系仍需视觉补全。"
                if visual_ready
                else "已读取可编辑文字；请配置视觉模型后补全图片、公式或版面关系。"
            )
        else:
            message = (
                "资料仅包含视觉页面；确认原始页面后可创建视觉候选。"
                if visual_ready
                else "资料仅包含视觉页面；请先在设置中配置具备视觉能力的模型。"
            )
        return DraftReceipt(
            draft_id=draft_id,
            kind="material_import",
            saved_at=saved_at,
            state=import_state,
            message_zh=message,
        )

    @property
    def _visual_import_root(self) -> Path:
        return self.paths.state_root / "visual-import-v2"

    @staticmethod
    def _visual_import_draft_id(batch_id: str) -> str:
        return _VISUAL_IMPORT_DRAFT_PREFIX + batch_id

    @staticmethod
    def _visual_import_status(visual_status: str) -> str:
        if visual_status in {"completed", "not_required"}:
            return "candidate_ready_for_review"
        if visual_status == "failed":
            return "failed"
        return "pending"

    @staticmethod
    def _visual_import_message(status: str, visual_status: str) -> str:
        if status == "candidate_ready_for_review" and visual_status == "not_required":
            return (
                "原生可编辑文字候选已保存到个人库存，等待教师复核；无需调用视觉模型。"
            )
        if status == "candidate_ready_for_review":
            return "视觉候选已生成，仍需教师逐页复核后才能进入后续整理。"
        if status == "failed":
            return "视觉候选生成未完成；原文件仍在个人归档中，可修正配置后重试。"
        return "原文件与原生文字候选已保存；配置视觉模型并明确确认后可继续。"

    @staticmethod
    def _visual_import_receipt_from_result(
        result: DesktopImportResult,
    ) -> DesktopVisualImportReceipt:
        states = {
            str(row.get("source_file_id")): str(row.get("import_state"))
            for row in result.plan.source_import_states
            if isinstance(row, Mapping)
        }
        sources = tuple(
            DesktopVisualImportSourceSummary(
                role=str(source.get("role")),
                order_index=int(source.get("order_index")),
                filename=str(source.get("filename")),
                import_state=states.get(
                    str(source.get("source_file_id")), "visual_only_required"
                ),
            )
            for source in result.plan.sources
        )
        status = DesktopWorkbenchFacade._visual_import_status(result.visual_status)
        return DesktopVisualImportReceipt(
            batch_id=result.batch_id,
            source_type=result.source_type,
            status=status,
            visual_status=result.visual_status,
            source_count=len(sources),
            native_quick_count=result.plan.native_quick_count,
            visual_queue_count=result.plan.visual_queue_count,
            sources=sources,
            message_zh=DesktopWorkbenchFacade._visual_import_message(
                status, result.visual_status
            ),
        )

    @staticmethod
    def _visual_import_receipt_from_saved(
        value: Mapping[str, Any],
    ) -> DesktopVisualImportReceipt:
        receipt = value.get("receipt")
        if not isinstance(receipt, Mapping):
            raise DesktopFacadeError(
                "visual_import_state_invalid", "已保存的视觉导入批次无法读取。"
            )
        raw_sources = receipt.get("sources")
        if not isinstance(raw_sources, list):
            raise DesktopFacadeError(
                "visual_import_state_invalid", "已保存的视觉导入批次无法读取。"
            )
        try:
            sources = tuple(
                DesktopVisualImportSourceSummary(
                    role=str(item["role"]),
                    order_index=int(item["order_index"]),
                    filename=str(item["filename"]),
                    import_state=str(item["import_state"]),
                )
                for item in raw_sources
                if isinstance(item, Mapping)
            )
            result = DesktopVisualImportReceipt(
                batch_id=str(receipt["batch_id"]),
                source_type=str(receipt["source_type"]),
                status=str(receipt["status"]),
                visual_status=str(receipt["visual_status"]),
                source_count=int(receipt["source_count"]),
                native_quick_count=int(receipt["native_quick_count"]),
                visual_queue_count=int(receipt["visual_queue_count"]),
                sources=sources,
                message_zh=str(receipt["message_zh"]),
                candidate_only=receipt.get("candidate_only") is True,
                central_question_bank_write=(
                    receipt.get("central_question_bank_write") is True
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DesktopFacadeError(
                "visual_import_state_invalid", "已保存的视觉导入批次无法读取。"
            ) from exc
        if (
            result.status not in {"pending", "failed", "candidate_ready_for_review"}
            or result.visual_status
            not in {
                "not_required",
                "awaiting_visual_provider",
                "awaiting_teacher_confirmation",
                "completed",
                "failed",
            }
            or result.candidate_only is not True
            or result.central_question_bank_write is not False
            or result.source_count != len(result.sources)
            or any(
                item.role not in {"question", "answer", "handout"}
                or item.import_state not in IMPORT_STATES
                for item in result.sources
            )
        ):
            raise DesktopFacadeError(
                "visual_import_state_invalid", "已保存的视觉导入批次无法读取。"
            )
        return result

    def _saved_visual_import_batch(self, batch_id: str) -> dict[str, Any]:
        if not isinstance(batch_id, str) or not batch_id:
            raise DesktopFacadeError(
                "visual_import_batch_invalid", "视觉导入批次标识不正确。"
            )
        drafts = self._state.snapshot().get("drafts")
        value = (
            drafts.get(self._visual_import_draft_id(batch_id))
            if isinstance(drafts, Mapping)
            else None
        )
        if (
            not isinstance(value, Mapping)
            or value.get("schema_version") != _VISUAL_IMPORT_DRAFT_SCHEMA
            or value.get("kind") != "desktop_visual_import_v2"
            or value.get("batch_id") != batch_id
        ):
            raise DesktopFacadeError(
                "visual_import_batch_missing", "未找到可继续的视觉导入批次。"
            )
        return dict(value)

    @staticmethod
    def _visual_import_private_sources(
        sources: Sequence[DesktopSourceFile],
    ) -> list[dict[str, Any]]:
        extensions = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }
        result: list[dict[str, Any]] = []
        for source in sources:
            value = source.as_manifest()
            value["archive_relative_path"] = (
                f"sources/{source.source_sha256}{extensions[source.mime_type]}"
            )
            result.append(value)
        return result

    def _save_visual_import_descriptor(
        self,
        *,
        sources: Sequence[DesktopSourceFile],
        result: DesktopImportResult,
        created_at: str | None = None,
    ) -> DesktopVisualImportReceipt:
        receipt = self._visual_import_receipt_from_result(result)
        private_sources = self._visual_import_private_sources(sources)
        saved_at = utc_now()
        descriptor = {
            "schema_version": _VISUAL_IMPORT_DRAFT_SCHEMA,
            "kind": "desktop_visual_import_v2",
            "batch_id": result.batch_id,
            "source_type": result.source_type,
            "source_closure_sha256": _canonical_digest(private_sources),
            "sources": private_sources,
            "status": receipt.status,
            "visual_status": receipt.visual_status,
            "receipt": receipt.as_dict(),
            "created_at": created_at or saved_at,
            "updated_at": saved_at,
        }
        self._state.save_draft(
            self._visual_import_draft_id(result.batch_id), descriptor
        )
        return receipt

    def _build_visual_import_sources(
        self,
        *,
        question_files: Sequence[str | Path],
        answer_files: Sequence[str | Path],
        handout_files: Sequence[str | Path],
        group_id: str,
    ) -> tuple[DesktopSourceFile, ...]:
        sources: list[DesktopSourceFile] = []
        try:
            for role, paths in (
                ("question", question_files),
                ("answer", answer_files),
                ("handout", handout_files),
            ):
                sources.extend(
                    DesktopSourceFile.from_path(
                        path,
                        role=role,
                        order_index=index,
                        group_id=group_id,
                    )
                    for index, path in enumerate(paths, 1)
                )
        except DesktopImportBridgeError as exc:
            raise DesktopFacadeError(exc.code, exc.message_zh) from exc
        return tuple(sources)

    def _visual_import_coordinator(
        self, *, visual_provider: Any | None = None
    ) -> DesktopImportCoordinatorV2:
        return DesktopImportCoordinatorV2(
            visual_provider=visual_provider,
            renderer=self._visual_import_renderer,
            native_importer=self._visual_import_native_importer,
            archive_root=self._visual_import_root,
        )

    def save_visual_import_batch(
        self,
        *,
        question_files: Sequence[str | Path] = (),
        answer_files: Sequence[str | Path] = (),
        handout_files: Sequence[str | Path] = (),
        source_type: str,
        group_id: str = "default",
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DesktopVisualImportReceipt:
        """Archive and classify a batch without installing or calling a provider."""

        sources = self._build_visual_import_sources(
            question_files=question_files,
            answer_files=answer_files,
            handout_files=handout_files,
            group_id=group_id,
        )
        coordinator = self._visual_import_coordinator()
        request = DesktopImportRequest(sources=sources, source_type=source_type)
        try:
            plan = coordinator.plan(request)
            drafts = self._state.snapshot().get("drafts")
            existing = (
                drafts.get(self._visual_import_draft_id(plan.batch_id))
                if isinstance(drafts, Mapping)
                else None
            )
            if (
                isinstance(existing, Mapping)
                and existing.get("status") == "candidate_ready_for_review"
            ):
                return self._visual_import_receipt_from_saved(existing)
            result = coordinator.process(
                request,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                visual_confirmation=False,
            )
        except DesktopImportBridgeError as exc:
            raise DesktopFacadeError(exc.code, exc.message_zh) from exc
        return self._save_visual_import_descriptor(
            sources=sources,
            result=result,
            created_at=(
                str(existing.get("created_at"))
                if isinstance(existing, Mapping)
                and isinstance(existing.get("created_at"), str)
                else None
            ),
        )

    def _restore_visual_import_sources(
        self, descriptor: Mapping[str, Any]
    ) -> tuple[DesktopSourceFile, ...]:
        raw_sources = descriptor.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise DesktopFacadeError(
                "visual_import_source_archive_invalid",
                "已保存批次的原文件归档不完整。",
            )
        extensions = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }
        sources: list[DesktopSourceFile] = []
        try:
            if descriptor.get("source_closure_sha256") != _canonical_digest(
                raw_sources
            ):
                raise ValueError("closure changed")
            for item in raw_sources:
                if not isinstance(item, Mapping):
                    raise TypeError("source descriptor invalid")
                mime_type = str(item["mime_type"])
                digest = str(item["source_sha256"])
                expected_relative = f"sources/{digest}{extensions[mime_type]}"
                if item.get("archive_relative_path") != expected_relative:
                    raise ValueError("source archive path changed")
                path = self._visual_import_root / Path(expected_relative)
                if path.is_symlink() or not path.is_file():
                    raise ValueError("source archive missing")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != digest or len(
                    content
                ) != item.get("size_bytes"):
                    raise ValueError("source archive hash mismatch")
                sources.append(
                    DesktopSourceFile(
                        role=str(item["role"]),
                        order_index=int(item["order_index"]),
                        filename=str(item["filename"]),
                        mime_type=mime_type,
                        content=content,
                        group_id=str(item["group_id"]),
                        source_file_id=str(item["source_file_id"]),
                    )
                )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise DesktopFacadeError(
                "visual_import_source_archive_invalid",
                "已保存批次的原文件归档不完整或已变化。",
            ) from exc
        return tuple(sources)

    def _imported_word_source(self, batch_id: str, source_id: str):
        descriptor = self._saved_visual_import_batch(batch_id)
        sources = self._restore_visual_import_sources(descriptor)
        source = next((item for item in sources if item.effective_source_file_id == source_id), None)
        if source is None or not source.filename.casefold().endswith(".docx"):
            raise DesktopFacadeError("imported_word_missing", "未找到这份已导入的Word资料，请重新选择。")
        return source

    def imported_word_sources(self, batch_id: str) -> list[dict[str, Any]]:
        """List saved Word sources without depending on the original picker path."""
        from .desktop_visual_import_v2 import inspect_native_docx

        descriptor = self._saved_visual_import_batch(batch_id)
        return [
            {
                "source_id": source.effective_source_file_id,
                "source_name": source.filename,
                "role": source.role,
                "import_state": inspect_native_docx(source).import_state,
            }
            for source in self._restore_visual_import_sources(descriptor)
            if source.filename.casefold().endswith(".docx")
        ]

    def list_imported_word_batches(self) -> tuple[DesktopVisualImportReceipt, ...]:
        """Completed native-only imports remain reachable after a restart."""
        values = []
        for key, value in self._state.snapshot().get("drafts", {}).items():
            if (
                isinstance(key, str) and key.startswith(_VISUAL_IMPORT_DRAFT_PREFIX)
                and isinstance(value, Mapping)
                and value.get("schema_version") == _VISUAL_IMPORT_DRAFT_SCHEMA
                and any(
                    isinstance(source, Mapping)
                    and str(source.get("filename", "")).casefold().endswith(".docx")
                    for source in value.get("sources", [])
                )
            ):
                values.append(value)
        values.sort(key=lambda value: str(value.get("created_at") or ""))
        return tuple(self._visual_import_receipt_from_saved(value) for value in values)

    def imported_word_preview(self, batch_id: str, source_id: str) -> dict[str, Any]:
        from .desktop_preparation_sources import PreparationSourcesService

        source = self._imported_word_source(batch_id, source_id)
        return PreparationSourcesService(self.paths.workspace_root).word_preview_bytes(
            source.content, source.filename
        )

    def imported_word_asset(self, batch_id: str, source_id: str, asset_id: str) -> dict[str, Any]:
        from .desktop_preparation_sources import (
            PreparationSourceError,
            PreparationSourcesService,
        )

        source = self._imported_word_source(batch_id, source_id)
        try:
            return PreparationSourcesService(self.paths.workspace_root).word_asset_bytes(source.content, asset_id)
        except PreparationSourceError:
            raise
        except (OSError, ValueError) as exc:
            raise DesktopFacadeError("imported_word_image_invalid", "原图无法预览，请在Word原文件中查看；文字内容仍可使用。") from exc

    def imported_word_reference(
        self, batch_id: str, source_id: str, source_sha256: str,
        block_start: int, block_end: int, expected_revision: str,
    ) -> dict[str, Any]:
        from .desktop_preparation_sources import PreparationSourcesService

        source = self._imported_word_source(batch_id, source_id)
        service = PreparationSourcesService(self.paths.workspace_root)
        preview = service.word_preview_bytes(source.content, source.filename)
        if source_sha256 != source.source_sha256 or expected_revision != preview["revision"]:
            raise DesktopFacadeError("imported_word_changed", "Word原文或提取结果已变化，请重新预览后再确认。")
        # Reuse the same reference compiler as the existing preparation dialog;
        # it reads the archive again and validates hash/range/length without
        # truncating. The original teacher-visible filename stays intact.
        return service.reference(
            self._visual_import_root / "sources" / f"{source.source_sha256}.docx",
            source_sha256, block_start, block_end, [], word_source_name=source.filename,
        )

    def _visual_import_profile(
        self, profile_id: str, expected_revision: str
    ) -> Mapping[str, Any]:
        if not isinstance(profile_id, str) or not profile_id:
            raise DesktopFacadeError(
                "visual_profile_missing", "请选择已配置的视觉模型。"
            )
        if not isinstance(expected_revision, str) or not expected_revision:
            raise DesktopFacadeError(
                "visual_profile_revision_invalid", "视觉模型配置版本不正确。"
            )
        try:
            profile = next(
                (
                    item
                    for item in self._providers.list_metadata()
                    if item.get("profile_id") == profile_id
                ),
                None,
            )
        except Exception as exc:
            raise DesktopFacadeError(
                "visual_profile_unavailable", "视觉模型配置暂时无法读取。"
            ) from exc
        if not isinstance(profile, Mapping):
            raise DesktopFacadeError(
                "visual_profile_missing", "尚未配置可用的视觉模型。"
            )
        if profile.get("revision") != expected_revision:
            raise DesktopFacadeError(
                "visual_profile_stale", "视觉模型配置已变化，请刷新后重新确认。"
            )
        capabilities = profile.get("effective_capabilities") or profile.get(
            "capabilities"
        )
        if (
            profile.get("credential_state") != "configured"
            or not isinstance(capabilities, list)
            or not {"vision", "structured_output"}.issubset(capabilities)
        ):
            raise DesktopFacadeError(
                "visual_profile_missing", "该模型尚未配置可用 Key 或视觉结构化能力。"
            )
        return profile

    def run_saved_visual_import_batch(
        self,
        *,
        batch_id: str,
        profile_id: str,
        expected_profile_revision: str,
        teacher_confirmed: Literal[True],
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DesktopVisualImportReceipt:
        """Run a saved batch only after exact teacher and profile confirmation."""

        if teacher_confirmed is not True:
            raise DesktopFacadeError(
                "teacher_confirmation_required", "发送原始页面前需要教师明确确认。"
            )
        descriptor = self._saved_visual_import_batch(batch_id)
        if descriptor.get("status") == "candidate_ready_for_review":
            raise DesktopFacadeError(
                "visual_import_already_completed",
                "该视觉导入批次已完成，不能重复覆盖。",
            )
        self._visual_import_profile(profile_id, expected_profile_revision)
        sources = self._restore_visual_import_sources(descriptor)
        source_type = str(descriptor.get("source_type") or "未分类资料")
        request = DesktopImportRequest(
            sources=sources,
            batch_id=batch_id,
            source_type=source_type,
        )
        borrow = getattr(self._providers, "borrow_invocation_context", None)
        if not callable(borrow):
            raise DesktopFacadeError(
                "visual_profile_unavailable", "视觉模型调用配置暂时不可用。"
            )
        try:
            with borrow(
                profile_id, expected_revision=expected_profile_revision
            ) as context:
                provider = StructuredVisualShardProviderAdapter(
                    context,
                    transport=self._visual_import_transport,
                    should_cancel=should_cancel,
                )
                coordinator = self._visual_import_coordinator(visual_provider=provider)
                if coordinator.plan(request).batch_id != batch_id:
                    raise DesktopImportBridgeError(
                        "visual_import_source_closure_changed",
                        "已保存批次的来源集合无法核验。",
                        409,
                    )
                result = coordinator.process(
                    request,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                    visual_confirmation=True,
                )
        except DesktopImportBridgeError as exc:
            raise DesktopFacadeError(exc.code, exc.message_zh) from exc
        except DesktopFacadeError:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "visual_profile_unavailable"))
            message = (
                "视觉模型配置已变化，请刷新后重新确认。"
                if code in {"stale_revision", "revision_conflict"}
                else "视觉模型调用配置暂时不可用。"
            )
            raise DesktopFacadeError(code, message) from exc
        return self._save_visual_import_descriptor(
            sources=sources,
            result=result,
            created_at=(
                str(descriptor.get("created_at"))
                if isinstance(descriptor.get("created_at"), str)
                else None
            ),
        )

    def list_resumable_visual_import_batches(
        self,
    ) -> tuple[DesktopVisualImportReceipt, ...]:
        drafts = self._state.snapshot().get("drafts")
        if not isinstance(drafts, Mapping):
            return ()
        values = [
            value
            for key, value in drafts.items()
            if isinstance(key, str)
            and key.startswith(_VISUAL_IMPORT_DRAFT_PREFIX)
            and isinstance(value, Mapping)
            and value.get("status") in {"pending", "failed"}
        ]
        values.sort(key=lambda value: str(value.get("created_at") or ""))
        return tuple(self._visual_import_receipt_from_saved(value) for value in values)

    def run_word_handout_import_batch(
        self,
        *,
        files: Sequence[str | Path],
        progress_callback: Callable[[ImportProgress], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Import native Word candidates into the personal desktop state.

        The returned records remain candidate-only.  Questions with visual or
        uncertain boundaries are placed in the visual-completion queue.
        """

        importer = WordHandoutImporter(self.paths.state_root / "word-handout-import")
        try:
            return importer.run_batch(
                files,
                persist=True,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            ).as_dict()
        except WordHandoutImportError as exc:
            raise DesktopFacadeError(exc.code, exc.message_zh) from exc

    def run_one_round_review_corpus_import(
        self,
        *,
        progress_callback: Callable[[ImportProgress], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        expanded_root = (
            self.paths.shchem_root
            / ".intake"
            / "2026-07-30-user-teaching-pack"
            / "expanded"
        )
        importer = WordHandoutImporter(self.paths.state_root / "word-handout-import")
        try:
            return importer.run_expanded_corpus(
                expanded_root,
                persist=True,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            ).as_dict()
        except WordHandoutImportError as exc:
            raise DesktopFacadeError(exc.code, exc.message_zh) from exc

    def personal_handout_inventory(
        self,
        *,
        query: str = "",
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        importer = WordHandoutImporter(self.paths.state_root / "word-handout-import")
        try:
            return importer.personal_handout_inventory(
                query=query,
                state=state,
                limit=limit,
                offset=offset,
            )
        except WordHandoutImportError as exc:
            raise DesktopFacadeError(exc.code, exc.message_zh) from exc

    @staticmethod
    def _as_student_error(error: BaseException, fallback_zh: str) -> DesktopFacadeError:
        code = _error_code(error)
        return DesktopFacadeError(
            code,
            _STUDENT_ERROR_MESSAGES.get(code, fallback_zh),
        )

    def _student_manager_instance(self) -> Any:
        """Return the facade-owned, single persistent student manager."""

        with self._student_analysis_lock:
            if self._student_analysis_closed:
                raise DesktopFacadeError(
                    "student_visual_manager_closed",
                    "学生分析已经停止，请重新打开工作台。",
                )
            if self._student_analysis_manager is not None:
                return self._student_analysis_manager
            try:
                try:
                    curriculum = self.curriculum_catalog()
                except DesktopFacadeError:
                    # Page analysis remains available when the local textbook
                    # catalog is temporarily unavailable.  Diagnostic section
                    # writes then fail closed instead of inventing a mapping.
                    curriculum = None
                self._student_analysis_manager = StudentVisualAnalysisManager(
                    self.paths.state_root / _STUDENT_VISUAL_ROOT_NAME,
                    project_root=self.paths.workspace_root,
                    provider_store=self._providers,
                    renderer=self._student_analysis_renderer,
                    transport=self._student_analysis_transport,
                    curriculum_catalog=curriculum,
                    require_project_external=True,
                )
                return self._student_analysis_manager
            except (StudentVisualAnalysisError, OSError) as exc:
                raise self._as_student_error(
                    exc,
                    "学生分析的本机私有目录暂时不可用。",
                ) from exc

    @staticmethod
    def _student_profile_summary(value: Mapping[str, Any]) -> StudentProfileSummary:
        return StudentProfileSummary(
            student_id=str(value.get("student_id") or ""),
            label_zh=str(value.get("alias") or "匿名学生"),
            grade=str(value.get("grade") or "待核验"),
            retention_days=(
                int(value["retention_days"])
                if type(value.get("retention_days")) is int
                else 180
            ),
            created_at=str(value.get("created_at") or ""),
        )

    @staticmethod
    def _student_status_message(value: Mapping[str, Any]) -> str:
        status = str(value.get("status") or "")
        matching = value.get("matching")
        teacher_confirmed = (
            isinstance(matching, Mapping) and matching.get("teacher_confirmed") is True
        )
        messages = {
            "awaiting_upload": "请添加题目页面和学生作答页面；文件只会先保存到本机。",
            "upload_processing_failed": "有页面未能在本机准备，请检查文件后新建一次分析。",
            "awaiting_matching_confirmation": "请逐项核对题目页、作答页、参考答案页和分值。",
            "ready_for_analysis": "页面与发送范围已经确认，可以开始视觉分析。",
            "queued_for_analysis": "分析已经排队，请稍候。",
            "analyzing": "模型正在读取已确认的页面；结果仍需教师复核。",
            "cancel_requested": "正在请求停止；以随后读取到的持久状态为准。",
            "cancelled": "这次分析已停止，不能原地重启；需要时请新建一次分析。",
            "awaiting_visual_provider": "任务已保留，请检查模型设置后再明确重试。",
            "analysis_failed": "分析没有完成，任务已保留；重试前会再次确认发送范围。",
            "awaiting_teacher_review": "AI 候选已经返回，请逐项记录教师评分与诊断。",
        }
        if status == "awaiting_privacy_review":
            return (
                "页面匹配已确认；请选择模型并确认页面不含直接身份信息。"
                if teacher_confirmed
                else "页面已保存到本机，请先核对逐页匹配。"
            )
        return messages.get(status, "学生分析任务已保存在本机。")

    @staticmethod
    def _student_submission_summary(
        value: Mapping[str, Any],
        *,
        diagnostic_confirmed_count: int = 0,
    ) -> StudentSubmissionSummary:
        raw_files = value.get("files")
        raw_files = raw_files if isinstance(raw_files, list) else []
        files: list[StudentFileSummary] = []
        pages: list[StudentPageSummary] = []
        page_counts = {role: 0 for role in _STUDENT_ROLES}
        for raw_file in raw_files:
            if not isinstance(raw_file, Mapping):
                continue
            role = str(raw_file.get("role") or "")
            if role not in _STUDENT_ROLE_LABELS:
                continue
            file_id = str(raw_file.get("file_id") or "")
            raw_pages = raw_file.get("pages")
            raw_pages = raw_pages if isinstance(raw_pages, list) else []
            files.append(
                StudentFileSummary(
                    file_id=file_id,
                    role=role,
                    role_zh=_STUDENT_ROLE_LABELS[role],
                    state=str(raw_file.get("state") or ""),
                    page_count=len(raw_pages),
                )
            )
            for raw_page in raw_pages:
                if not isinstance(raw_page, Mapping):
                    continue
                page_counts[role] += 1
                ordinal = page_counts[role]
                page_number = (
                    int(raw_page["page"])
                    if type(raw_page.get("page")) is int
                    else ordinal
                )
                pages.append(
                    StudentPageSummary(
                        file_id=file_id,
                        sha256=str(raw_page.get("sha256") or ""),
                        role=role,
                        role_zh=_STUDENT_ROLE_LABELS[role],
                        ordinal=ordinal,
                        page_number=page_number,
                        label_zh=f"{_STUDENT_ROLE_LABELS[role]} · 第 {ordinal} 页",
                        mime_type=str(raw_page.get("mime_type") or ""),
                        width=(
                            int(raw_page["width"])
                            if type(raw_page.get("width")) is int
                            else 0
                        ),
                        height=(
                            int(raw_page["height"])
                            if type(raw_page.get("height")) is int
                            else 0
                        ),
                    )
                )

        raw_matching = value.get("matching")
        raw_matching = raw_matching if isinstance(raw_matching, Mapping) else {}
        raw_matches = raw_matching.get("matches")
        raw_matches = raw_matches if isinstance(raw_matches, list) else []
        matches: list[StudentMatchSummary] = []
        for index, raw in enumerate(raw_matches, 1):
            if not isinstance(raw, Mapping):
                continue
            hint = raw.get("question_number_hint")
            hint = hint.strip() if isinstance(hint, str) else ""
            maximum = raw.get("maximum_score")
            matches.append(
                StudentMatchSummary(
                    match_id=str(raw.get("match_id") or ""),
                    question_page_sha256=str(raw.get("question_page_sha256") or ""),
                    student_work_page_sha256=str(
                        raw.get("student_work_page_sha256") or ""
                    ),
                    reference_answer_page_sha256=(
                        str(raw["reference_answer_page_sha256"])
                        if isinstance(raw.get("reference_answer_page_sha256"), str)
                        else None
                    ),
                    label_zh=hint or f"作答单元 {index}",
                    question_number_hint=hint,
                    maximum_score=(
                        float(maximum)
                        if isinstance(maximum, (int, float))
                        and not isinstance(maximum, bool)
                        else 1.0
                    ),
                )
            )

        status = str(value.get("status") or "unknown")
        analysis = value.get("analysis")
        candidate_available = isinstance(analysis, Mapping)
        teacher_confirmed = raw_matching.get("teacher_confirmed") is True
        editable_statuses = {
            "awaiting_upload",
            "awaiting_privacy_review",
            "awaiting_matching_confirmation",
            "ready_for_analysis",
            "awaiting_visual_provider",
            "analysis_failed",
        }
        review = value.get("review")
        review = review if isinstance(review, Mapping) else {}
        scoring_count = review.get("scoring_decision_count")
        scoring_count = scoring_count if type(scoring_count) is int else 0
        return StudentSubmissionSummary(
            student_id=str(value.get("student_id") or ""),
            submission_id=str(value.get("submission_id") or ""),
            revision=str(value.get("revision") or ""),
            status=status,
            status_zh=_STUDENT_STATUS_LABELS.get(status, "状态待刷新"),
            message_zh=DesktopWorkbenchFacade._student_status_message(value),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            files=tuple(files),
            pages=tuple(pages),
            matches=tuple(matches),
            page_counts_by_role=dict(page_counts),
            match_count=len(matches),
            scoring_confirmed_count=scoring_count,
            diagnostic_confirmed_count=max(0, diagnostic_confirmed_count),
            candidate_available=candidate_available,
            can_confirm_matching=(
                bool(matches)
                and status in editable_statuses
                and not candidate_available
            ),
            can_analyze=(
                teacher_confirmed
                and status
                in {
                    "awaiting_privacy_review",
                    "ready_for_analysis",
                    "awaiting_visual_provider",
                    "analysis_failed",
                }
                and not candidate_available
            ),
            can_retry=(
                teacher_confirmed
                and status in _STUDENT_RETRYABLE_STATUSES
                and not candidate_available
            ),
            can_cancel=status in _STUDENT_ACTIVE_STATUSES,
        )

    def _student_diagnostic_count(
        self, manager: Any, student_id: str, submission_id: str
    ) -> int:
        operation = getattr(manager, "get_diagnostic_review", None)
        if not callable(operation):
            return 0
        value = operation(student_id, submission_id)
        review = value.get("review") if isinstance(value, Mapping) else None
        count = review.get("distinct_match_count") if isinstance(review, Mapping) else 0
        return count if type(count) is int and count >= 0 else 0

    def student_profiles(self) -> tuple[StudentProfileSummary, ...]:
        manager = self._student_manager_instance()
        try:
            values = manager.list_students()
            return tuple(
                self._student_profile_summary(value)
                for value in values
                if isinstance(value, Mapping)
            )
        except Exception as exc:
            raise self._as_student_error(
                exc, "匿名学生列表暂时无法读取，请稍后重试。"
            ) from exc

    def create_student_profile(
        self,
        *,
        grade: str,
        retention_days: int,
        consent_recorded: bool,
    ) -> StudentProfileSummary:
        manager = self._student_manager_instance()
        try:
            value = manager.create_student(
                {
                    "grade": grade,
                    "retention_days": retention_days,
                    "consent_recorded": consent_recorded is True,
                }
            )
            return self._student_profile_summary(value)
        except Exception as exc:
            raise self._as_student_error(
                exc,
                "匿名学生无法创建；请确认已取得授权且未填写真实身份信息。",
            ) from exc

    def student_analysis_profiles(self) -> tuple[ProviderProfileSummary, ...]:
        """List plausible visual profiles; the manager performs the final gate."""

        try:
            return tuple(
                profile
                for profile in self.list_provider_profiles()
                if profile.key_saved
                and {"vision", "structured_output"}.issubset(profile.capabilities)
            )
        except Exception as exc:
            raise self._as_student_error(exc, "学生分析模型配置暂时无法读取。") from exc

    def create_student_submission(self, student_id: str) -> StudentSubmissionSummary:
        manager = self._student_manager_instance()
        try:
            value = manager.create_submission(
                student_id,
                {"workflow": "quick_single_work", "assignment_id": None},
            )
            return self._student_submission_summary(value)
        except Exception as exc:
            raise self._as_student_error(
                exc, "无法创建这次学生分析，请刷新后重试。"
            ) from exc

    @staticmethod
    def _student_source(path_value: str | Path) -> tuple[Path, str, bytes]:
        try:
            path = Path(path_value).expanduser().resolve(strict=True)
            if not path.is_file():
                raise OSError("not a regular file")
            mime_type = _STUDENT_SOURCE_MIME.get(path.suffix.casefold())
            if mime_type is None:
                raise DesktopFacadeError(
                    "file_mime_invalid",
                    _STUDENT_ERROR_MESSAGES["file_mime_invalid"],
                )
            size = path.stat().st_size
            if not 1 <= size <= 64 * 1024 * 1024:
                raise DesktopFacadeError(
                    "upload_too_large",
                    _STUDENT_ERROR_MESSAGES["upload_too_large"],
                )
            body = path.read_bytes()
            if len(body) != size:
                raise OSError("source changed while being read")
            return path, mime_type, body
        except DesktopFacadeError:
            raise
        except OSError as exc:
            raise DesktopFacadeError(
                "source_file_unreadable", "所选学生分析文件无法读取。"
            ) from exc

    def add_student_submission_files(
        self,
        *,
        student_id: str,
        submission_id: str,
        role: str,
        files: Sequence[str | Path],
        expected_revision: str,
    ) -> StudentSubmissionSummary:
        if role not in _STUDENT_ROLES:
            raise DesktopFacadeError("file_role_invalid", "页面用途不正确。")
        if isinstance(files, (str, bytes, Path)) or not files:
            raise DesktopFacadeError(
                "source_file_required", f"请先选择{_STUDENT_ROLE_LABELS[role]}。"
            )
        manager = self._student_manager_instance()
        revision = expected_revision
        latest: Mapping[str, Any] | None = None
        seen: set[Path] = set()
        try:
            for source in files:
                path, mime_type, body = self._student_source(source)
                if path in seen:
                    raise DesktopFacadeError(
                        "source_file_duplicate", "同一组页面中不能重复添加同一个文件。"
                    )
                seen.add(path)
                digest = hashlib.sha256(body).hexdigest()
                registered = manager.register_file(
                    student_id,
                    submission_id,
                    {
                        "role": role,
                        "filename": path.name,
                        "mime_type": mime_type,
                        "expected_size_bytes": len(body),
                        "expected_sha256": digest,
                        "expected_revision": revision,
                    },
                )
                revision = str(registered.get("revision") or "")
                file_record = registered.get("file")
                file_id = (
                    str(file_record.get("file_id") or "")
                    if isinstance(file_record, Mapping)
                    else ""
                )
                latest = manager.upload_file_content(
                    student_id,
                    submission_id,
                    file_id,
                    body,
                    content_type=mime_type,
                )
                revision = str(latest.get("revision") or "")
            assert latest is not None
            return self._student_submission_summary(latest)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_student_error(
                exc,
                f"{_STUDENT_ROLE_LABELS[role]}未能在本机准备；已保存的页面不会外发。",
            ) from exc

    def student_submission(
        self, *, student_id: str, submission_id: str
    ) -> StudentSubmissionSummary:
        manager = self._student_manager_instance()
        try:
            value = manager.get_submission(student_id, submission_id)
            diagnostic_count = self._student_diagnostic_count(
                manager, student_id, submission_id
            )
            return self._student_submission_summary(
                value, diagnostic_confirmed_count=diagnostic_count
            )
        except Exception as exc:
            raise self._as_student_error(exc, "这次学生分析暂时无法读取。") from exc

    def student_submissions(
        self, *, student_id: str, limit: int = 10
    ) -> tuple[StudentSubmissionSummary, ...]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise DesktopFacadeError("limit_invalid", "最近任务数量不正确。")
        manager = self._student_manager_instance()
        try:
            values = manager.list_submissions(student_id)
            summaries: list[StudentSubmissionSummary] = []
            for value in values[:limit]:
                if not isinstance(value, Mapping):
                    continue
                submission_id = str(value.get("submission_id") or "")
                count = self._student_diagnostic_count(
                    manager, student_id, submission_id
                )
                summaries.append(
                    self._student_submission_summary(
                        value, diagnostic_confirmed_count=count
                    )
                )
            return tuple(summaries)
        except Exception as exc:
            raise self._as_student_error(exc, "最近学生分析暂时无法读取。") from exc

    def student_submission_page(
        self,
        *,
        student_id: str,
        submission_id: str,
        file_id: str,
        page_sha256: str,
    ) -> tuple[bytes, str]:
        manager = self._student_manager_instance()
        try:
            body, mime_type = manager.read_submission_page(
                student_id,
                submission_id,
                file_id=file_id,
                page_sha256=page_sha256,
            )
            return bytes(body), str(mime_type)
        except Exception as exc:
            raise self._as_student_error(exc, "这个本机页面暂时无法预览。") from exc

    def confirm_student_matching(
        self,
        *,
        student_id: str,
        submission_id: str,
        expected_revision: str,
        edits: Sequence[Mapping[str, Any]],
    ) -> StudentSubmissionSummary:
        manager = self._student_manager_instance()
        try:
            current = manager.get_matching(student_id, submission_id)
            matching = current.get("matching")
            raw_matches = (
                matching.get("matches") if isinstance(matching, Mapping) else None
            )
            if not isinstance(raw_matches, list) or not raw_matches:
                raise DesktopFacadeError(
                    "matching_not_ready", "页面尚未准备完成，不能确认匹配。"
                )
            originals = {
                str(item.get("match_id")): item
                for item in raw_matches
                if isinstance(item, Mapping)
            }
            if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes)):
                raise DesktopFacadeError("matching_invalid", "页面匹配内容不正确。")
            normalized: list[dict[str, Any]] = []
            seen: set[str] = set()
            allowed_edit_fields = {
                "match_id",
                "question_number_hint",
                "maximum_score",
                "question_page_sha256",
                "student_work_page_sha256",
                "reference_answer_page_sha256",
            }
            for edit in edits:
                if not isinstance(edit, Mapping) or set(edit) != allowed_edit_fields:
                    raise DesktopFacadeError("matching_invalid", "页面匹配内容不完整。")
                match_id = str(edit.get("match_id") or "")
                original = originals.get(match_id)
                if original is None or match_id in seen:
                    raise DesktopFacadeError(
                        "matching_invalid", "页面匹配已经变化，请刷新后重试。"
                    )
                seen.add(match_id)
                hint = edit.get("question_number_hint")
                hint = hint.strip() if isinstance(hint, str) else None
                normalized.append(
                    {
                        "match_id": match_id,
                        "atomic_part_id": original.get("atomic_part_id"),
                        "printed_question_id": original.get("printed_question_id"),
                        "question_number_hint": hint or None,
                        "question_page_sha256": edit.get("question_page_sha256"),
                        "student_work_page_sha256": edit.get(
                            "student_work_page_sha256"
                        ),
                        "reference_answer_page_sha256": edit.get(
                            "reference_answer_page_sha256"
                        ),
                        "maximum_score": edit.get("maximum_score"),
                        "allowed_scoring_point_ids": list(
                            original.get("allowed_scoring_point_ids") or []
                        ),
                        "provisional": original.get("provisional") is True,
                    }
                )
            if seen != set(originals):
                raise DesktopFacadeError(
                    "matching_invalid", "请核对并保留每一个作答单元。"
                )
            manager.update_matching(
                student_id,
                submission_id,
                {"expected_revision": expected_revision, "matches": normalized},
            )
            return self.student_submission(
                student_id=student_id, submission_id=submission_id
            )
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_student_error(
                exc, "页面匹配未能确认，请检查页码和分值。"
            ) from exc

    def prepare_student_analysis_confirmation(
        self,
        *,
        student_id: str,
        submission_id: str,
        profile_id: str,
    ) -> StudentAnalysisConfirmation:
        manager = self._student_manager_instance()
        try:
            submission = manager.get_submission(student_id, submission_id)
            matching = submission.get("matching")
            if (
                not isinstance(matching, Mapping)
                or matching.get("teacher_confirmed") is not True
                or not matching.get("matches")
            ):
                raise DesktopFacadeError(
                    "matching_teacher_confirmation_required",
                    _STUDENT_ERROR_MESSAGES["matching_teacher_confirmation_required"],
                )
            profiles = {
                item.profile_id: item for item in self.student_analysis_profiles()
            }
            profile = profiles.get(profile_id)
            if profile is None:
                raise DesktopFacadeError(
                    "awaiting_visual_provider",
                    "请选择已保存 Key、视觉能力和结构化输出能力的模型。",
                )
            page_hashes: list[str] = []
            page_counts = {role: 0 for role in _STUDENT_ROLES}
            for file_record in submission.get("files", []):
                if (
                    not isinstance(file_record, Mapping)
                    or file_record.get("state") != "stored"
                ):
                    continue
                role = str(file_record.get("role") or "")
                if role not in page_counts:
                    continue
                for page in file_record.get("pages", []):
                    if isinstance(page, Mapping) and isinstance(
                        page.get("sha256"), str
                    ):
                        page_hashes.append(str(page["sha256"]))
                        page_counts[role] += 1
            if (
                page_counts["question_pages"] < 1
                or page_counts["student_work_pages"] < 1
                or not page_hashes
            ):
                raise DesktopFacadeError(
                    "submission_pages_incomplete",
                    _STUDENT_ERROR_MESSAGES["submission_pages_incomplete"],
                )
            if len(page_hashes) != len(set(page_hashes)):
                raise DesktopFacadeError(
                    "submission_page_duplicate",
                    _STUDENT_ERROR_MESSAGES["submission_page_duplicate"],
                )
            profile_summary = next(
                (
                    item
                    for item in self.student_profiles()
                    if item.student_id == student_id
                ),
                None,
            )
            if profile_summary is None:
                raise DesktopFacadeError(
                    "student_not_found", _STUDENT_ERROR_MESSAGES["student_not_found"]
                )
            provider_label = f"{profile.provider_name} / {profile.model_id}"
            return StudentAnalysisConfirmation(
                student_id=student_id,
                submission_id=submission_id,
                expected_revision=str(submission.get("revision") or ""),
                provider_profile_id=profile.profile_id,
                provider_revision=profile.revision,
                page_sha256=tuple(page_hashes),
                provider_label_zh=provider_label,
                total_page_count=len(page_hashes),
                page_counts_by_role=dict(page_counts),
                student_label_zh=profile_summary.label_zh,
                retention_days=profile_summary.retention_days,
                message_zh=(
                    f"将 {len(page_hashes)} 页已确认图片发送给 {provider_label}；"
                    "可能产生模型费用，返回结果仅作为教师复核候选。"
                ),
            )
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_student_error(
                exc, "当前页面或模型尚未达到分析条件。"
            ) from exc

    def start_student_analysis(
        self,
        *,
        student_id: str,
        confirmation: StudentAnalysisConfirmation,
        identifiers_clear: bool,
        student_page_egress_confirmed: bool,
    ) -> StudentSubmissionSummary:
        if not isinstance(confirmation, StudentAnalysisConfirmation):
            raise DesktopFacadeError(
                "analysis_confirmation_invalid", "分析确认已经失效，请重新确认。"
            )
        if identifiers_clear is not True:
            raise DesktopFacadeError(
                "privacy_review_not_approved",
                "请先确认页面不含姓名、学号等直接身份信息，或已完成脱敏。",
            )
        if student_page_egress_confirmed is not True:
            raise DesktopFacadeError(
                "student_page_egress_confirmation_required",
                "请明确确认将这些页面发送给所选模型并可能产生费用。",
            )
        if confirmation.student_id != student_id:
            raise DesktopFacadeError(
                "student_scope_denied", _STUDENT_ERROR_MESSAGES["student_scope_denied"]
            )
        manager = self._student_manager_instance()
        try:
            current = manager.get_submission(student_id, confirmation.submission_id)
            current_hashes = tuple(
                str(page["sha256"])
                for file_record in current.get("files", [])
                if isinstance(file_record, Mapping)
                and file_record.get("state") == "stored"
                for page in file_record.get("pages", [])
                if isinstance(page, Mapping) and isinstance(page.get("sha256"), str)
            )
            if (
                current.get("revision") != confirmation.expected_revision
                or current_hashes != confirmation.page_sha256
            ):
                raise DesktopFacadeError(
                    "revision_conflict",
                    "页面或匹配已经变化，请刷新并重新确认发送范围。",
                )
            profile = next(
                (
                    item
                    for item in self.student_analysis_profiles()
                    if item.profile_id == confirmation.provider_profile_id
                ),
                None,
            )
            if profile is None or profile.revision != confirmation.provider_revision:
                raise DesktopFacadeError(
                    "stale_provider_revision",
                    _STUDENT_ERROR_MESSAGES["stale_provider_revision"],
                )
            approved = manager.record_privacy_decision(
                student_id,
                confirmation.submission_id,
                {
                    "expected_revision": confirmation.expected_revision,
                    "decision": "approved",
                    "contains_direct_identifiers": False,
                    "confirmed_page_sha256": list(confirmation.page_sha256),
                    "provider_profile_id": confirmation.provider_profile_id,
                    "provider_revision": confirmation.provider_revision,
                    "teacher_confirmed_student_page_egress": True,
                },
            )
            queued = manager.analyze(
                student_id,
                confirmation.submission_id,
                {
                    "expected_revision": approved.get("revision"),
                    "provider_profile_id": confirmation.provider_profile_id,
                    "provider_revision": confirmation.provider_revision,
                },
            )
            return self._student_submission_summary(queued)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_student_error(
                exc, "学生视觉分析未能启动；任务仍保存在本机。"
            ) from exc

    def cancel_student_analysis(
        self,
        *,
        student_id: str,
        submission_id: str,
        expected_revision: str,
    ) -> StudentSubmissionSummary:
        manager = self._student_manager_instance()
        try:
            manager.cancel(
                student_id,
                submission_id,
                {"expected_revision": expected_revision},
            )
            # Completion may win the cancellation race.  Always project the
            # durable state instead of assuming the requested outcome.
            return self.student_submission(
                student_id=student_id, submission_id=submission_id
            )
        except Exception as exc:
            raise self._as_student_error(
                exc, "停止请求未能完成，请刷新任务状态。"
            ) from exc

    @staticmethod
    def _student_string_list(value: Any) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )

    @classmethod
    def _student_review_projection(
        cls,
        submission: Mapping[str, Any],
        review: Mapping[str, Any],
        diagnostic: Mapping[str, Any],
    ) -> StudentAnalysisReview:
        analysis = review.get("analysis")
        candidate = analysis.get("candidate") if isinstance(analysis, Mapping) else None
        raw_items = candidate.get("matches") if isinstance(candidate, Mapping) else None
        raw_items = raw_items if isinstance(raw_items, list) else []
        matching = submission.get("matching")
        source_matches = (
            matching.get("matches") if isinstance(matching, Mapping) else []
        )
        source_by_id = {
            str(item.get("match_id")): item
            for item in source_matches
            if isinstance(item, Mapping)
        }
        scoring_records = review.get("scoring_decisions")
        scoring_records = scoring_records if isinstance(scoring_records, list) else []
        latest_scoring: dict[str, Mapping[str, Any]] = {}
        for record in scoring_records:
            if isinstance(record, Mapping) and isinstance(record.get("match_id"), str):
                latest_scoring[str(record["match_id"])] = record
        diagnostic_records = diagnostic.get("latest_diagnostic_decisions")
        diagnostic_records = (
            diagnostic_records if isinstance(diagnostic_records, list) else []
        )
        latest_diagnostic = {
            str(record.get("match_id")): record
            for record in diagnostic_records
            if isinstance(record, Mapping) and isinstance(record.get("match_id"), str)
        }
        quality_rows = (
            candidate.get("page_quality") if isinstance(candidate, Mapping) else []
        )
        quality_by_hash = {
            str(item.get("page_sha256")): item
            for item in quality_rows
            if isinstance(item, Mapping) and isinstance(item.get("page_sha256"), str)
        }
        global_blockers = cls._student_string_list(
            candidate.get("blockers") if isinstance(candidate, Mapping) else None
        )
        result: list[StudentReviewItem] = []
        diagnostic_labels = {
            "accept": "接受候选诊断",
            "edit": "教师已修改诊断",
            "reject": "拒绝候选诊断",
            "pending": "诊断待定",
        }
        chemistry_labels = {
            "formulas": "化学式",
            "charges": "电荷",
            "conditions": "条件",
            "units": "单位",
            "other_visible_details": "其他可见细节",
        }
        for index, raw in enumerate(raw_items, 1):
            if not isinstance(raw, Mapping):
                continue
            match_id = str(raw.get("match_id") or "")
            source = source_by_id.get(match_id, {})
            question_number = raw.get("question_number")
            if not isinstance(question_number, str) or not question_number.strip():
                question_number = source.get("question_number_hint")
            question_number = (
                question_number.strip()
                if isinstance(question_number, str) and question_number.strip()
                else ""
            )
            maximum = raw.get("maximum_score")
            maximum_score = (
                float(maximum)
                if isinstance(maximum, (int, float)) and not isinstance(maximum, bool)
                else 0.0
            )
            blockers = list(cls._student_string_list(raw.get("blockers")))
            answer_anchor = raw.get("student_answer_anchor")
            answer_hash = (
                answer_anchor.get("page_sha256")
                if isinstance(answer_anchor, Mapping)
                else None
            )
            quality = quality_by_hash.get(str(answer_hash), {})
            quality_state = quality.get("quality")
            quality_issues = cls._student_string_list(quality.get("issues"))
            if quality_state in {"blurred", "cropped", "blocked"}:
                blockers.append(
                    {
                        "blurred": "学生作答页面模糊，暂不显示模型建议分。",
                        "cropped": "学生作答页面可能裁切，暂不显示模型建议分。",
                        "blocked": "学生作答页面不可用，暂不显示模型建议分。",
                    }[str(quality_state)]
                )
            blockers.extend(quality_issues)
            blockers.extend(global_blockers)
            blockers = list(dict.fromkeys(item for item in blockers if item))
            suggested = raw.get("suggested_score")
            suggested_score = (
                float(suggested)
                if not blockers
                and isinstance(suggested, (int, float))
                and not isinstance(suggested, bool)
                else None
            )
            confidence = raw.get("confidence")
            confidence_value = (
                float(confidence)
                if isinstance(confidence, (int, float))
                and not isinstance(confidence, bool)
                else None
            )
            chemistry: list[str] = []
            observations = raw.get("chemistry_observations")
            if isinstance(observations, Mapping):
                for key, label in chemistry_labels.items():
                    values = cls._student_string_list(observations.get(key))
                    if values:
                        chemistry.append(f"{label}：{'、'.join(values)}")
            scoring_points: list[str] = []
            raw_points = raw.get("scoring_points")
            if isinstance(raw_points, list):
                for point_index, point in enumerate(raw_points, 1):
                    if not isinstance(point, Mapping):
                        continue
                    point_blockers = cls._student_string_list(point.get("blockers"))
                    if point_blockers:
                        scoring_points.append(
                            f"候选评分点 {point_index}：证据待核对（{'；'.join(point_blockers)}）"
                        )
                        continue
                    point_score = point.get("suggested_score")
                    point_maximum = point.get("maximum_score")
                    scoring_points.append(
                        f"候选评分点 {point_index}：模型建议 {point_score}/{point_maximum} 分，须教师核对。"
                    )
            hypotheses: list[str] = []
            raw_hypotheses = raw.get("error_hypotheses")
            if isinstance(raw_hypotheses, list):
                for item in raw_hypotheses:
                    if not isinstance(item, Mapping):
                        continue
                    hypothesis = item.get("hypothesis")
                    if isinstance(hypothesis, str) and hypothesis.strip():
                        counter = cls._student_string_list(item.get("counterevidence"))
                        hypotheses.append(
                            hypothesis.strip()
                            + (
                                f"；反证/不确定性：{'；'.join(counter)}"
                                if counter
                                else ""
                            )
                        )
            scoring = latest_scoring.get(match_id)
            diagnostic_item = latest_diagnostic.get(match_id)
            teacher_score = scoring.get("teacher_score") if scoring else None
            decision_id = scoring.get("decision_id") if scoring else None
            decision = diagnostic_item.get("decision") if diagnostic_item else None
            result.append(
                StudentReviewItem(
                    match_id=match_id,
                    latest_scoring_decision_id=(
                        str(decision_id) if isinstance(decision_id, str) else None
                    ),
                    label_zh=question_number or f"作答单元 {index}",
                    question_number=question_number,
                    maximum_score=maximum_score,
                    suggested_score=suggested_score,
                    suggested_score_withheld=bool(blockers),
                    confidence=confidence_value,
                    observation_zh=str(raw.get("visual_response_observation") or ""),
                    chemistry_observations_zh=tuple(chemistry),
                    scoring_points_zh=tuple(scoring_points),
                    error_hypotheses_zh=tuple(hypotheses),
                    blockers_zh=tuple(blockers),
                    latest_teacher_score=(
                        float(teacher_score)
                        if isinstance(teacher_score, (int, float))
                        and not isinstance(teacher_score, bool)
                        else None
                    ),
                    latest_diagnostic_decision=(
                        diagnostic_labels.get(str(decision), str(decision))
                        if isinstance(decision, str)
                        else None
                    ),
                )
            )
        diagnostic_complete = bool(result) and all(
            item.match_id in latest_diagnostic
            and latest_diagnostic[item.match_id].get("decision") != "pending"
            for item in result
        )
        scoring_complete = bool(result) and all(
            item.match_id in latest_scoring for item in result
        )
        status = str(submission.get("status") or review.get("status") or "unknown")
        return StudentAnalysisReview(
            student_id=str(submission.get("student_id") or ""),
            submission_id=str(submission.get("submission_id") or ""),
            revision=str(submission.get("revision") or review.get("revision") or ""),
            status=status,
            status_zh=_STUDENT_STATUS_LABELS.get(status, "状态待刷新"),
            message_zh=(
                "本次候选的教师评分和诊断已逐项记录；仍不会自动写入长期学情。"
                if scoring_complete and diagnostic_complete
                else "AI 候选必须由教师逐项核对；教师分数默认留空。"
            ),
            items=tuple(result),
            candidate_blockers_zh=global_blockers,
            scoring_confirmed_count=len(latest_scoring),
            diagnostic_confirmed_count=len(latest_diagnostic),
            review_complete=scoring_complete and diagnostic_complete,
        )

    def student_analysis_review(
        self, *, student_id: str, submission_id: str
    ) -> StudentAnalysisReview:
        manager = self._student_manager_instance()
        try:
            submission = manager.get_submission(student_id, submission_id)
            review = manager.get_review(student_id, submission_id)
            diagnostic = manager.get_diagnostic_review(student_id, submission_id)
            return self._student_review_projection(submission, review, diagnostic)
        except Exception as exc:
            raise self._as_student_error(exc, "学生分析候选暂时无法读取。") from exc

    def record_student_score(
        self,
        *,
        student_id: str,
        submission_id: str,
        expected_revision: str,
        match_id: str,
        teacher_score: float,
        reason: str,
    ) -> StudentAnalysisReview:
        manager = self._student_manager_instance()
        try:
            manager.append_scoring_decision(
                student_id,
                submission_id,
                {
                    "expected_revision": expected_revision,
                    "match_id": match_id,
                    "teacher_score": teacher_score,
                    "reason": reason,
                },
            )
            return self.student_analysis_review(
                student_id=student_id, submission_id=submission_id
            )
        except Exception as exc:
            raise self._as_student_error(
                exc, "教师评分未能记录，请检查分数和理由。"
            ) from exc

    def student_curriculum_sections(self) -> tuple[CurriculumSectionSummary, ...]:
        catalog = self.curriculum_catalog()
        result: list[CurriculumSectionSummary] = []
        for volume in catalog.get("volumes", []):
            if not isinstance(volume, Mapping):
                continue
            for chapter in volume.get("chapters", []):
                if not isinstance(chapter, Mapping):
                    continue
                for section in chapter.get("sections", []):
                    if not isinstance(section, Mapping):
                        continue
                    key = section.get("section_key")
                    label = section.get("display_label_zh")
                    if isinstance(key, str) and isinstance(label, str):
                        result.append(
                            CurriculumSectionSummary(
                                section_key=key,
                                display_label_zh=label,
                            )
                        )
        if not result:
            raise DesktopFacadeError(
                "curriculum_catalog_invalid", "当前教材目录没有可选章节。"
            )
        return tuple(result)

    def _handout_candidates(self):
        from .desktop_handout_candidates import HandoutCandidateService

        return HandoutCandidateService(self.paths.workspace_root, self._state)

    def handout_candidate_catalog(self) -> dict[str, Any]:
        return self._handout_candidates().catalog()

    def handout_candidate_detail(self, key: str) -> dict[str, Any]:
        return self._handout_candidates().detail(key)

    def record_handout_candidate_review(
        self, key: str, revision: str, **review: Any
    ) -> dict[str, Any]:
        return self._handout_candidates().record_review(key, revision, **review)

    def handout_candidate_page(
        self, key: str, revision: str, role: str, index: int
    ) -> bytes:
        return self._handout_candidates().page_bytes(key, revision, role, index)

    def handout_candidate_copy_text(self, key: str, revision: str) -> str:
        return self._handout_candidates().copy_text(key, revision)

    def _handout_practice(self):
        from .desktop_handout_practice import HandoutPracticeService

        return HandoutPracticeService(self._handout_candidates())

    def handout_practice_state(self) -> dict[str, Any]:
        service = self._handout_practice()
        return {"draft": service.draft(), "history": service.history()}

    def save_handout_practice(
        self, title: str, selections: list[dict[str, str]], answer_lines: int
    ) -> dict[str, Any]:
        return self._handout_practice().save_draft(title, selections, answer_lines)

    def export_handout_practice(
        self, title: str, selections: list[dict[str, str]], answer_lines: int
    ) -> dict[str, Any]:
        return self._handout_practice().export(title, selections, answer_lines)

    def handout_practice_artifact_path(self, export_id: str, role: str) -> Path:
        return self._handout_practice().artifact_path(export_id, role)

    def prompt_handout_references(self) -> list[dict[str, Any]]:
        from .desktop_handout_prompt_reference import reference_options

        return reference_options(self._state)

    def compile_prompt_blueprint(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Compile local textbook evidence into a prompt without provider access."""
        from .desktop_handout_prompt_reference import compile_handout_reference
        from .desktop_prompt_blueprint import compile_preview

        sections = self.prompt_curriculum_sections()
        preview = compile_preview(
            self.paths.workspace_root,
            payload,
            {section.section_key: section.display_label_zh for section in sections},
            handout_loader=lambda selection: compile_handout_reference(
                self._handout_candidates(), selection
            ),
        )
        preview["preview_id"] = "BLUEPRINT-" + uuid.uuid4().hex
        self._state.save_draft(
            preview["preview_id"],
            {
                "kind": "textbook_prompt_blueprint",
                "preview": preview,
                "status": "previewed",
                "created_at": utc_now(),
                "input": dict(payload),
            },
        )
        return preview

    def prompt_blueprint_history(self) -> tuple[dict[str, Any], ...]:
        records = [
            item
            for item in self._state.snapshot()["drafts"].values()
            if isinstance(item, dict)
            and item.get("kind") == "textbook_prompt_blueprint"
            and item.get("status") == "completed"
        ]
        return tuple(
            sorted(
                records, key=lambda item: str(item.get("created_at", "")), reverse=True
            )[:20]
        )

    def generate_prompt_blueprint(
        self,
        preview_id: str,
        profile_id: str,
        expected_profile_revision: str,
        *,
        teacher_confirmed: Literal[True],
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Run once against the saved preview; no arbitrary prompt from the UI."""
        from .desktop_blueprint_generation import (
            BlueprintGenerationError,
            generate_blueprint,
        )

        if teacher_confirmed is not True:
            raise DesktopFacadeError(
                "teacher_confirmation_required", "生成蓝图前需要确认文字出站。"
            )
        cancelled = should_cancel or (lambda: False)
        if cancelled():
            raise DesktopFacadeError("blueprint_cancelled", "已停止生成蓝图。")
        if not self._blueprint_generation_lock.acquire(blocking=False):
            raise DesktopFacadeError(
                "blueprint_busy", "已有一个蓝图正在生成，请等待完成或先停止。"
            )
        record = None
        try:
            record = self._state.snapshot()["drafts"].get(preview_id)
            if (
                not isinstance(record, dict)
                or record.get("kind") != "textbook_prompt_blueprint"
            ):
                raise DesktopFacadeError(
                    "blueprint_preview_missing", "找不到已编译的提示，请重新编译。"
                )
            if record.get("status") == "completed":
                if "error_code" in record or "response_summary" in record:
                    record.pop("error_code", None)
                    record.pop("response_summary", None)
                    self._state.save_draft(preview_id, record)
                return record["result"]
            if record.get("status") == "running":
                raise DesktopFacadeError(
                    "blueprint_interrupted",
                    "上次生成未正常结束，请重新编译并确认后再试。",
                )
            profile = self._preparation_profile(profile_id, expected_profile_revision)
            if cancelled():
                raise DesktopFacadeError("blueprint_cancelled", "已停止生成蓝图。")
            record.update(
                status="running",
                profile_id=profile_id,
                profile_revision=expected_profile_revision,
            )
            self._state.save_draft(preview_id, record)
            try:
                with self._providers.borrow_invocation_context(
                    profile_id, expected_revision=expected_profile_revision
                ) as context:
                    generated = generate_blueprint(
                        context,
                        record["preview"],
                        transport=self._blueprint_transport,
                        should_cancel=cancelled,
                    )
                generated.update(
                    preview_id=preview_id,
                    title=record["preview"]["title"],
                    profile_id=profile_id,
                    profile_revision=expected_profile_revision,
                    model_id=profile.model_id,
                    created_at=utc_now(),
                )
                record.update(status="completed", result=generated)
                record.pop("error_code", None)
                record.pop("response_summary", None)
                self._state.save_draft(preview_id, record)
                return generated
            except Exception as exc:
                stopped = (
                    cancelled() or getattr(exc, "code", "") == "blueprint_cancelled"
                )
                record["status"] = "cancelled" if stopped else "failed"
                record["error_code"] = _error_code(exc)
                if isinstance(exc, BlueprintGenerationError):
                    record["response_summary"] = exc.response_summary
                self._state.save_draft(preview_id, record)
                if isinstance(exc, BlueprintGenerationError):
                    raise DesktopFacadeError(exc.code, exc.message_zh) from exc
                raise DesktopFacadeError(
                    "blueprint_generation_failed",
                    "蓝图未完成或未能保存，请检查设置后重试。",
                ) from exc
        finally:
            self._blueprint_generation_lock.release()

    def prompt_blueprint_draft_sources(self, preview_id: str) -> list[dict[str, Any]]:
        from .desktop_blueprint_drafts import BlueprintDraftService

        return BlueprintDraftService(self._state).sources(preview_id)

    def _blueprint_handout_evidence(self, preview_id: str, evidence_id: str):
        from .desktop_blueprint_drafts import BlueprintDraftService

        binding = BlueprintDraftService(self._state).handout_binding(
            preview_id, evidence_id
        )
        service = self._handout_candidates()
        item = service.detail(binding["key"])
        if (
            item["revision"] != binding["revision"]
            or item["source_document"] != binding["source_document"]
            or item["editable_source"] != binding["editable_source"]
        ):
            raise DesktopFacadeError(
                "blueprint_evidence_source_changed",
                "讲义来源已更新，不能用新原页替代这份蓝图的旧依据。已保存的资料文字仍可查看；请重新选择讲义并编译新蓝图。",
            )
        return service, item, binding

    def prompt_blueprint_evidence_pages(
        self, preview_id: str, evidence_id: str
    ) -> list[dict[str, Any]]:
        _service, item, binding = self._blueprint_handout_evidence(
            preview_id, evidence_id
        )
        roles = ("question", "answer") if binding["include_answers"] else ("question",)
        return [
            {
                "role": role,
                "index": index,
                "caption": f"{evidence_id} · 原第 {item['printed_number']} 题 · {'题面' if role == 'question' else '非官方答案'}第 {page['page']} 页",
            }
            for role in roles
            for index, page in enumerate(item.get(role + "_pages", []))
        ]

    def prompt_blueprint_evidence_page(
        self, preview_id: str, evidence_id: str, role: str, index: int
    ) -> bytes:
        service, item, binding = self._blueprint_handout_evidence(
            preview_id, evidence_id
        )
        if role not in ("question", "answer") or (
            role == "answer" and not binding["include_answers"]
        ):
            raise DesktopFacadeError(
                "blueprint_evidence_role_unavailable", "这份蓝图未引用所选类型的原页。"
            )
        return service.page_bytes(item["key"], binding["revision"], role, index)

    def save_prompt_blueprint_draft(
        self,
        preview_id: str,
        source_id: str,
        expected_revision: str,
        candidate: Mapping[str, Any],
        *,
        note: str = "",
    ) -> dict[str, Any]:
        from .desktop_blueprint_drafts import BlueprintDraftService

        return BlueprintDraftService(self._state).save(
            preview_id,
            source_id,
            expected_revision,
            candidate,
            note,
        )

    def prompt_blueprint_reviews(
        self,
        preview_id: str,
        source_revision: str,
        *,
        source_draft_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from .desktop_blueprint_review import REVIEW_KIND

        return sorted(
            (
                record
                for record in self._state.snapshot()["drafts"].values()
                if isinstance(record, dict)
                and record.get("kind") == REVIEW_KIND
                and record.get("preview_id") == preview_id
                and record.get("source_candidate_revision") == source_revision
                and record.get("source_draft_id") == source_draft_id
                and (
                    record.get("status") == "completed"
                    or record.get("diagnosis_result")
                )
            ),
            key=lambda record: record.get("created_at", ""),
            reverse=True,
        )[:20]

    def review_prompt_blueprint(
        self,
        preview_id: str,
        source_revision: str,
        profile_id: str,
        expected_profile_revision: str,
        *,
        teacher_confirmed: Literal[True],
        focus: str = "",
        should_cancel: Callable[[], bool] | None = None,
        resume_review_id: str | None = None,
        on_progress: Callable[[Any], None] | None = None,
        source_draft_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a separately confirmed review; never overwrite the original."""
        from .desktop_blueprint_drafts import (
            DRAFT_KIND,
            BlueprintDraftError,
            BlueprintDraftService,
        )
        from .desktop_blueprint_generation import BlueprintGenerationError
        from .desktop_blueprint_review import (
            REVIEW_KIND,
            candidate_revision,
            review_blueprint,
            review_prompt,
            validate_diagnosis,
        )

        if teacher_confirmed is not True:
            raise DesktopFacadeError(
                "teacher_confirmation_required", "审校前需要确认文字出站与模型费用。"
            )
        cancelled = should_cancel or (lambda: False)
        if cancelled():
            raise DesktopFacadeError("blueprint_cancelled", "已停止审校。")
        if not self._blueprint_generation_lock.acquire(blocking=False):
            raise DesktopFacadeError(
                "blueprint_busy", "已有蓝图生成或审校正在进行，请等待或先停止。"
            )
        record = None
        try:
            source = self._state.snapshot()["drafts"].get(preview_id)
            if (
                not isinstance(source, dict)
                or source.get("kind") != "textbook_prompt_blueprint"
                or source.get("status") != "completed"
            ):
                raise DesktopFacadeError(
                    "blueprint_review_source_missing",
                    "请先生成蓝图或打开已保存的原始蓝图。",
                )
            original = source["result"]["candidate"]
            root_revision = candidate_revision(original)
            if source_draft_id is not None:
                try:
                    selected = BlueprintDraftService(self._state).load_source(
                        preview_id,
                        source_draft_id,
                        source_revision,
                    )
                except BlueprintDraftError as exc:
                    raise DesktopFacadeError(exc.code, exc.message_zh) from exc
                if (
                    selected["source_kind"] != DRAFT_KIND
                    or selected["root_revision"] != root_revision
                ):
                    raise DesktopFacadeError(
                        "blueprint_review_source_invalid",
                        "请选择当前蓝图下已保存的教师草稿。",
                    )
                original = selected["candidate"]
            if candidate_revision(original) != source_revision:
                raise DesktopFacadeError(
                    "blueprint_review_source_changed",
                    "待审稿已变化，请重新打开后审校。",
                )
            # Validate source and focus before borrowing a credential or writing a run.
            review_prompt(
                source["preview"],
                original,
                focus,
                teacher_request=source.get("input", {}),
            )
            checkpoint = None
            if resume_review_id is not None:
                checkpoint = self._state.snapshot()["drafts"].get(resume_review_id)
                if (
                    not isinstance(checkpoint, dict)
                    or checkpoint.get("kind") != REVIEW_KIND
                    or checkpoint.get("preview_id") != preview_id
                    or checkpoint.get("source_candidate_revision") != source_revision
                    or checkpoint.get("source_draft_id") != source_draft_id
                    or (
                        source_draft_id is not None
                        and checkpoint.get("root_candidate_revision") != root_revision
                    )
                    or checkpoint.get("focus") != focus
                ):
                    raise DesktopFacadeError(
                        "blueprint_review_checkpoint_invalid",
                        "续接记录与原稿或关注点不一致，请重新选择记录。",
                    )
                if checkpoint.get("status") == "completed":
                    return checkpoint["result"]
                diagnosis = checkpoint.get("diagnosis_result")
                if (
                    not isinstance(diagnosis, dict)
                    or diagnosis.get("result_kind") != "blueprint_diagnosis"
                    or diagnosis.get("source_candidate_revision") != source_revision
                    or diagnosis.get("focus") != focus
                ):
                    raise DesktopFacadeError(
                        "blueprint_review_checkpoint_invalid",
                        "没有可续接的完整诊断，请重新审校。",
                    )
                validate_diagnosis(
                    diagnosis.get("candidate"), source["preview"], original
                )
            profile = self._preparation_profile(profile_id, expected_profile_revision)
            if cancelled():
                raise DesktopFacadeError("blueprint_cancelled", "已停止审校。")
            review_id = resume_review_id or "BLUEPRINT-REVIEW-" + uuid.uuid4().hex
            record = checkpoint or {
                "kind": REVIEW_KIND,
                "review_id": review_id,
                "preview_id": preview_id,
                "source_candidate_revision": source_revision,
                "focus": focus,
                "source_draft_id": source_draft_id,
                "root_candidate_revision": root_revision,
                "profile_id": profile_id,
                "profile_revision": expected_profile_revision,
                "status": "running",
                "created_at": utc_now(),
            }
            record.update(
                status="running", stage="revision" if checkpoint else "diagnosis"
            )
            record.pop("error_code", None)
            record.pop("response_summary", None)
            record.setdefault("attempts", []).append(
                {
                    "started_at": utc_now(),
                    "stage": record["stage"],
                    "profile_id": profile_id,
                    "profile_revision": expected_profile_revision,
                }
            )
            self._state.save_draft(review_id, record)

            def save_diagnosis(diagnosis_result):
                diagnosis_result.update(
                    profile_id=profile_id,
                    profile_revision=expected_profile_revision,
                    model_id=profile.model_id,
                )
                record.update(diagnosis_result=diagnosis_result, stage="revision")
                self._state.save_draft(review_id, record)
                if on_progress is not None:
                    on_progress(
                        {
                            "stage": "revision",
                            "review_id": review_id,
                            "diagnosis_report": diagnosis_result["candidate"],
                        }
                    )

            if on_progress is not None:
                on_progress(
                    {
                        "stage": record["stage"],
                        "review_id": review_id,
                        "diagnosis_report": (record.get("diagnosis_result") or {}).get(
                            "candidate"
                        ),
                    }
                )
            with self._providers.borrow_invocation_context(
                profile_id, expected_revision=expected_profile_revision
            ) as context:
                result = review_blueprint(
                    context,
                    source["preview"],
                    original,
                    focus=focus,
                    teacher_request=source.get("input", {}),
                    diagnosis_result=record.get("diagnosis_result"),
                    on_diagnosis=save_diagnosis,
                    transport=self._blueprint_review_transport,
                    should_cancel=cancelled,
                )
            if cancelled():
                raise DesktopFacadeError("blueprint_cancelled", "已停止审校。")
            result.update(
                review_id=review_id,
                preview_id=preview_id,
                model_id=profile.model_id,
                source_draft_id=source_draft_id,
                root_candidate_revision=root_revision,
                created_at=utc_now(),
                title=source["preview"]["title"],
            )
            record["attempts"][-1].update(status="completed", finished_at=utc_now())
            record.update(status="completed", stage="completed", result=result)
            self._state.save_draft(review_id, record)
            return result
        except Exception as exc:
            if record is not None:
                record.pop("result", None)
                record.update(
                    status="cancelled"
                    if cancelled() or getattr(exc, "code", "") == "blueprint_cancelled"
                    else "failed",
                    error_code=_error_code(exc),
                )
                if isinstance(exc, BlueprintGenerationError):
                    record["response_summary"] = exc.response_summary
                if record.get("attempts"):
                    record["attempts"][-1].update(
                        status=record["status"],
                        finished_at=utc_now(),
                        failed_stage=record["stage"],
                        error_code=_error_code(exc),
                        response_summary=record.get("response_summary", {}),
                    )
                self._state.save_draft(record["review_id"], record)
            if isinstance(exc, DesktopFacadeError):
                raise
            if isinstance(exc, BlueprintGenerationError):
                raise DesktopFacadeError(exc.code, exc.message_zh) from exc
            raise DesktopFacadeError(
                "blueprint_review_failed", "审校未完成或未能保存，原蓝图未修改。"
            ) from exc
        finally:
            self._blueprint_generation_lock.release()

    def prompt_curriculum_sections(self) -> tuple[CurriculumSectionSummary, ...]:
        """Show the full textbook hierarchy so repeated section numbers are unambiguous."""
        catalog = self.curriculum_catalog()
        result: list[CurriculumSectionSummary] = []
        for volume in catalog.get("volumes", []):
            for chapter in volume.get("chapters", []):
                for section in chapter.get("sections", []):
                    key = section.get("section_key")
                    label = section.get("display_label_zh")
                    if isinstance(key, str) and isinstance(label, str):
                        result.append(
                            CurriculumSectionSummary(
                                section_key=key,
                                display_label_zh=" / ".join(
                                    filter(
                                        None,
                                        (
                                            volume.get("volume_title"),
                                            chapter.get("chapter_title"),
                                            label,
                                        ),
                                    )
                                ),
                            )
                        )
        if not result:
            raise DesktopFacadeError(
                "curriculum_catalog_invalid", "当前教材目录没有可选章节。"
            )
        return tuple(result)

    def record_student_diagnosis(
        self,
        *,
        student_id: str,
        submission_id: str,
        expected_revision: str,
        match_id: str,
        scoring_decision_id: str,
        decision: str,
        result: str,
        primary_error_type: str | None,
        secondary_error_types: Sequence[str],
        curriculum_section_keys: Sequence[str],
        teacher_note: str,
    ) -> StudentAnalysisReview:
        manager = self._student_manager_instance()
        try:
            manager.append_diagnostic_decision(
                student_id,
                submission_id,
                {
                    "expected_revision": expected_revision,
                    "match_id": match_id,
                    "scoring_decision_id": scoring_decision_id,
                    "decision": decision,
                    "result": result,
                    "primary_error_type": primary_error_type,
                    "secondary_error_types": list(secondary_error_types),
                    "curriculum_section_keys": list(curriculum_section_keys),
                    "teacher_note": teacher_note,
                },
            )
            return self.student_analysis_review(
                student_id=student_id, submission_id=submission_id
            )
        except Exception as exc:
            raise self._as_student_error(
                exc, "教师诊断未能记录，请检查结果、错误类型和教材章节。"
            ) from exc

    def stop_background_readers(self) -> None:
        """Signal before joining Qt tasks or taking the student manager lock."""

        self._reader_stop_event.set()

    def shutdown(self) -> None:
        """Stop the one facade-owned student executor exactly once."""

        self.stop_background_readers()
        with self._student_analysis_lock:
            if self._student_analysis_closed:
                return
            self._student_analysis_closed = True
            manager = self._student_analysis_manager
        if manager is not None:
            operation = getattr(manager, "shutdown", None)
            if callable(operation):
                operation()

    def create_student_analysis_draft(
        self, *, student_alias: str, files: list[str | Path]
    ) -> DraftReceipt:
        """Legacy local descriptor kept for old callers.

        The native 0.1.6 page uses the managed methods above; this descriptor
        is never treated as consent, a submission, or a visual candidate.
        """
        alias = student_alias.strip()
        if not alias:
            raise DesktopFacadeError("student_alias_required", "请填写匿名学生编号。")
        descriptors = self._file_descriptors(files)
        saved_at = utc_now()
        draft_id = "student-" + uuid.uuid4().hex
        ready = self.has_visual_profile()
        payload = {
            "kind": "student_visual_analysis",
            "student_alias": alias,
            "files": descriptors,
            "status": "awaiting_teacher_confirmation"
            if ready
            else "awaiting_visual_profile",
            "created_at": saved_at,
        }
        self._state.save_draft(draft_id, payload)
        return DraftReceipt(
            draft_id=draft_id,
            kind="student_visual_analysis",
            saved_at=saved_at,
            state=payload["status"],
            message_zh=(
                "分析草稿已保存；确认页面与学生对应关系后再创建视觉候选。"
                if ready
                else "分析草稿已保存；请先配置视觉模型，当前不会判定学生错误。"
            ),
        )

    @staticmethod
    def _paper_preview_record_id(preview_hash: str) -> str:
        return f"paper-preview-{preview_hash}"

    @staticmethod
    def _paper_selection_basis(
        state_snapshot: Mapping[str, Any], basket: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Freeze opaque source bindings without copying editable display text."""

        drafts = state_snapshot.get("drafts")
        current = drafts.get("paper-current") if isinstance(drafts, Mapping) else None
        composer = current.get("payload") if isinstance(current, Mapping) else None
        raw_themes = composer.get("themes") if isinstance(composer, Mapping) else None
        themes: list[dict[str, Any]] = []
        if isinstance(raw_themes, list):
            for raw in raw_themes:
                if not isinstance(raw, Mapping):
                    continue
                source_ref = raw.get("source_ref")
                themes.append(
                    {
                        "source_identity_sha256": raw.get("source_identity_sha256"),
                        "source_ref": (
                            dict(source_ref) if isinstance(source_ref, Mapping) else {}
                        ),
                    }
                )
        frozen_basket = json.loads(json.dumps(list(basket), ensure_ascii=False))
        return {
            "basket": frozen_basket,
            "basket_sha256": _canonical_digest(frozen_basket),
            "themes": themes,
        }

    @staticmethod
    def _paper_catalog_snapshot_id(catalog: Mapping[str, Any]) -> str:
        supplied = catalog.get("data_snapshot_id")
        if isinstance(supplied, str) and _SHA256.fullmatch(supplied):
            return supplied
        return _canonical_digest(catalog)

    @staticmethod
    def _paper_export_error(
        error: BaseException, message_zh: str
    ) -> PaperExportWorkbenchError:
        if isinstance(error, PaperExportWorkbenchError):
            return error
        status = getattr(error, "status", 409)
        return PaperExportWorkbenchError(
            _error_code(error),
            message_zh,
            status if isinstance(status, int) else 409,
        )

    def _paper_export_selections(
        self,
        frozen: Mapping[str, Any],
        *,
        theme_loader: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        basis = frozen.get("selection_basis")
        basket = basis.get("basket") if isinstance(basis, Mapping) else None
        if not isinstance(basket, list) or not basket:
            raise DesktopFacadeError(
                "paper_export_basket_missing", "已确认预览缺少题篮来源，请重新预览。"
            )
        scopes = {
            item.get("scope")
            for item in basket
            if isinstance(item, Mapping) and isinstance(item.get("scope"), str)
        }
        if len(scopes) != 1 or next(iter(scopes), None) not in DESKTOP_SCOPES:
            raise DesktopFacadeError(
                "multiple_scopes_require_dedup_review",
                "一次导出只能使用同一个题库范围，请分开组卷。",
            )
        scope = str(next(iter(scopes)))
        try:
            catalog = (theme_loader or self._load_theme_scope)(scope)
        except Exception as exc:
            raise DesktopFacadeError(
                _error_code(exc), "当前题库范围暂时无法读取，请稍后重试。"
            ) from exc
        snapshot_id = self._paper_catalog_snapshot_id(catalog)

        by_identity: dict[str, tuple[str, str]] = {}
        by_pair: set[tuple[str, str]] = set()
        for paper_entry in catalog.get("papers", []):
            if not isinstance(paper_entry, Mapping):
                continue
            paper = paper_entry.get("paper")
            paper = paper if isinstance(paper, Mapping) else {}
            paper_id = paper.get("id")
            for group in paper_entry.get("theme_groups", []):
                if not isinstance(group, Mapping):
                    continue
                theme = group.get("theme")
                theme = theme if isinstance(theme, Mapping) else {}
                theme_id = theme.get("id")
                if not isinstance(paper_id, str) or not isinstance(theme_id, str):
                    continue
                identity = _canonical_digest(
                    {"scope": scope, "paper": paper_id, "theme": theme_id}
                )
                if identity in by_identity:
                    raise DesktopFacadeError(
                        "paper_export_source_ambiguous",
                        "当前题库存在重复主题身份，请刷新题库后重试。",
                    )
                by_identity[identity] = (paper_id, theme_id)
                by_pair.add((paper_id, theme_id))

        basket_identities: list[str] = []
        for item in basket:
            if not isinstance(item, Mapping) or item.get("scope") != scope:
                raise DesktopFacadeError(
                    "paper_export_basket_invalid",
                    "已确认预览的题篮来源不完整，请重新预览。",
                )
            expected_snapshot = item.get("data_snapshot_id")
            if (
                isinstance(expected_snapshot, str)
                and expected_snapshot
                and (
                    not _SHA256.fullmatch(expected_snapshot)
                    or expected_snapshot != snapshot_id
                )
            ):
                raise DesktopFacadeError(
                    "theme_snapshot_stale", "题篮来自旧题库版本，请刷新后重新预览。"
                )
            identity = item.get("source_identity_sha256") or item.get("key")
            if not isinstance(identity, str) or identity not in by_identity:
                raise DesktopFacadeError(
                    "paper_export_source_missing",
                    "已确认主题已不在当前题库中，请刷新后重新预览。",
                )
            basket_identities.append(identity)

        frozen_themes = basis.get("themes") if isinstance(basis, Mapping) else None
        theme_rows = (
            [item for item in frozen_themes if isinstance(item, Mapping)]
            if isinstance(frozen_themes, list) and frozen_themes
            else [
                {"source_identity_sha256": identity, "source_ref": {}}
                for identity in basket_identities
            ]
        )
        selections: list[dict[str, Any]] = []
        seen: set[str] = set()
        basket_identity_set = set(basket_identities)
        for row in theme_rows:
            identity = row.get("source_identity_sha256")
            source_ref = row.get("source_ref")
            source_ref = source_ref if isinstance(source_ref, Mapping) else {}
            ref_scope = source_ref.get("scope")
            paper_id = source_ref.get("paper_id")
            theme_id = source_ref.get("theme_id")
            ref_snapshot = source_ref.get("data_snapshot_id")
            if (
                isinstance(ref_snapshot, str)
                and ref_snapshot
                and (not _SHA256.fullmatch(ref_snapshot) or ref_snapshot != snapshot_id)
            ):
                raise DesktopFacadeError(
                    "theme_snapshot_stale", "已确认预览来自旧题库版本，请重新预览。"
                )
            if (
                ref_scope == scope
                and isinstance(paper_id, str)
                and isinstance(theme_id, str)
                and (paper_id, theme_id) in by_pair
            ):
                resolved_identity = _canonical_digest(
                    {"scope": scope, "paper": paper_id, "theme": theme_id}
                )
                if isinstance(identity, str) and identity != resolved_identity:
                    raise DesktopFacadeError(
                        "paper_export_source_mismatch",
                        "已确认预览的主题身份与题库不一致，请重新预览。",
                    )
                identity = resolved_identity
            elif isinstance(identity, str) and identity in by_identity:
                paper_id, theme_id = by_identity[identity]
            else:
                raise DesktopFacadeError(
                    "paper_export_source_missing",
                    "已确认预览缺少可核对的主题来源，请重新预览。",
                )
            if identity not in basket_identity_set:
                raise DesktopFacadeError(
                    "paper_export_source_mismatch",
                    "已确认预览与题篮来源不一致，请重新预览。",
                )
            if identity in seen:
                continue
            seen.add(str(identity))
            selections.append(
                {
                    "scope": scope,
                    "selection_unit": "theme",
                    "theme_id": theme_id,
                    "target_atomic_id": None,
                    "expected_data_snapshot_id": snapshot_id,
                }
            )
        if not selections:
            raise DesktopFacadeError(
                "paper_export_basket_missing", "已确认预览没有可导出的完整主题。"
            )
        return catalog, selections

    @staticmethod
    def _paper_export_request(
        frozen: Mapping[str, Any], selections: list[dict[str, Any]]
    ) -> dict[str, Any]:
        model = frozen.get("preview_model")
        payload = frozen.get("payload")
        if not isinstance(model, Mapping) or not isinstance(payload, Mapping):
            raise DesktopFacadeError(
                "paper_export_not_ready", "当前预览缺少可导出的整卷内容。"
            )
        model_has_score_visibility = "show_question_scores" in model
        payload_has_score_visibility = "show_question_scores" in payload
        model_score_visibility = model.get("show_question_scores", False)
        payload_score_visibility = payload.get("show_question_scores", False)
        if model_has_score_visibility and type(model_score_visibility) is not bool:
            raise DesktopFacadeError(
                "paper_show_question_scores_invalid",
                "预览中的题面显示分数设置必须是布尔值，请重新预览。",
            )
        if payload_has_score_visibility and type(payload_score_visibility) is not bool:
            raise DesktopFacadeError(
                "paper_show_question_scores_invalid",
                "草稿中的题面显示分数设置必须是布尔值，请重新预览。",
            )
        if (
            model_has_score_visibility
            and payload_has_score_visibility
            and model_score_visibility != payload_score_visibility
        ):
            raise DesktopFacadeError(
                "paper_show_question_scores_mismatch",
                "预览与草稿的题面显示分数设置不一致，请重新预览。",
            )
        show_question_scores = (
            model_score_visibility
            if model_has_score_visibility
            else payload_score_visibility
        )
        score_values: set[int] = set()
        answer_lines: list[int] = []
        atomic_settings: dict[str, dict[str, int]] = {}
        question_count = 0
        for theme in model.get("themes", []):
            if not isinstance(theme, Mapping):
                continue
            for question in theme.get("questions", []):
                if not isinstance(question, Mapping):
                    continue
                question_count += 1
                score = question.get("score")
                if type(score) is not int or not 1 <= score <= 30:
                    raise DesktopFacadeError(
                        "paper_export_score_unrepresentable",
                        "预览中的小问分值不能交给四文件导出器，请调整后重新预览。",
                    )
                score_values.add(score)
                lines = question.get("answer_space")
                if type(lines) is not int or not 0 <= lines <= 20:
                    raise DesktopFacadeError(
                        "paper_export_answer_space_invalid",
                        "请为每个作答单元设置 0—20 行答题空间，再重新确认预览。",
                    )
                answer_lines.append(lines)
                source = question.get("source_ref")
                source_id = (
                    source.get("atomic_part_id")
                    if isinstance(source, Mapping)
                    else None
                )
                key = question.get("key")
                if source_id and key and source_id != key:
                    raise DesktopFacadeError(
                        "paper_export_atomic_binding_invalid",
                        "预览作答单元的来源身份不一致，请刷新题篮并重新预览。",
                    )
                atomic_id = source_id or key
                if atomic_id is not None:
                    if (
                        not isinstance(atomic_id, str)
                        or not atomic_id.strip()
                        or len(atomic_id) > 240
                        or atomic_id in atomic_settings
                    ):
                        raise DesktopFacadeError(
                            "paper_export_atomic_binding_invalid",
                            "预览作答单元的来源身份缺失或重复，请重新预览。",
                        )
                    atomic_settings[atomic_id] = {
                        "score": score,
                        "answer_space_lines": lines,
                    }
        if atomic_settings and len(atomic_settings) != question_count:
            raise DesktopFacadeError(
                "paper_export_atomic_binding_invalid",
                "部分作答单元缺少来源身份，请刷新题库并重新确认预览。",
            )
        if not atomic_settings and (
            len(score_values) > 1
            or len(set(answer_lines)) > 1
            or any(score > 20 for score in score_values)
        ):
            raise DesktopFacadeError(
                "paper_export_atomic_binding_required",
                "旧预览缺少逐题来源身份，无法精确导出不同设置，请刷新后重新预览。",
            )
        duration = model.get("duration_minutes", payload.get("duration_minutes", 60))
        if type(duration) is not int or not 1 <= duration <= 300:
            raise DesktopFacadeError(
                "paper_export_duration_unrepresentable",
                "预览中的时长不正确，请调整后重新预览。",
            )
        subtitle = model.get("subtitle")
        request = {
            "title_zh": str(model.get("title") or "组卷预览"),
            "subtitle_zh": subtitle.strip()
            if isinstance(subtitle, str) and subtitle.strip()
            else None,
            "duration_minutes": duration,
            "numbering_mode": "continuous_across_paper",
            "show_question_scores": show_question_scores,
            "score_per_atomic": 1 if atomic_settings else next(iter(score_values), 1),
            "answer_space_lines": max(answer_lines, default=3),
            "selections": selections,
        }
        if atomic_settings:
            request["atomic_settings"] = atomic_settings
        return request

    def _paper_export_readers(self) -> tuple[Any, Any, Any, Any]:
        if self._wave_visual is None:
            self._wave_visual = QuestionVisualScanReader(self.paths.shchem_root)
        if self._wave_crops is None:
            self._wave_crops = Wave1CandidateReviewReader(self.paths.shchem_root)
        if self._master_workbench is None:
            self._master_workbench = MasterWave1WorkbenchReader(self.paths.shchem_root)
        if self._master_direct is None:
            self._master_direct = MasterDirectVisualScanReader(
                self.paths.shchem_root,
                master_workbench=self._master_workbench,
            )
        return (
            self._wave_visual,
            self._wave_crops,
            self._master_workbench,
            self._master_direct,
        )

    def create_paper_preview(self, payload: Mapping[str, Any]) -> PaperPreview:
        mode = payload.get("mode")
        if mode not in {"mock_exam", "daily_practice"}:
            raise DesktopFacadeError("paper_mode_invalid", "请选择组卷模式。")
        title = str(payload.get("title") or "").strip()
        if not title:
            raise DesktopFacadeError("paper_title_required", "请填写试卷或练习名称。")
        payload_has_score_visibility = "show_question_scores" in payload
        show_question_scores = payload.get("show_question_scores", False)
        if payload_has_score_visibility and type(show_question_scores) is not bool:
            raise DesktopFacadeError(
                "paper_show_question_scores_invalid",
                "题面显示分数设置必须是布尔值。",
            )
        state_snapshot = self._state.snapshot()
        basket = [
            json.loads(json.dumps(item, ensure_ascii=False))
            for item in state_snapshot.get("basket", [])
            if isinstance(item, Mapping)
        ]
        selection_basis = self._paper_selection_basis(state_snapshot, basket)
        blockers: list[str] = []
        if not basket:
            blockers.append("题篮为空，请先从题库加入完整主题。")
        # The native page owns the confirmation gate.  A preview is useful
        # even when an answer or crop is still pending; those gaps are shown
        # as teacher-readable notes instead of a permanent migration blocker.
        assembly = payload.get("assembly")
        if isinstance(assembly, Mapping) and (
            isinstance(assembly.get("themes"), list)
            or isinstance(assembly.get("theme_groups"), list)
        ):
            assembly_has_score_visibility = "show_question_scores" in assembly
            assembly_score_visibility = assembly.get("show_question_scores", False)
            if assembly_has_score_visibility and type(assembly_score_visibility) is not bool:
                raise DesktopFacadeError(
                    "paper_show_question_scores_invalid",
                    "预览中的题面显示分数设置必须是布尔值。",
                )
            if not payload_has_score_visibility and assembly_has_score_visibility:
                show_question_scores = assembly_score_visibility
            elif (
                assembly_has_score_visibility
                and assembly_score_visibility != show_question_scores
            ):
                raise DesktopFacadeError(
                    "paper_show_question_scores_mismatch",
                    "预览与组卷设置的题面显示分数不一致，请重新预览。",
                )
            preview_model = json.loads(json.dumps(assembly, ensure_ascii=False))
        else:
            try:
                from .desktop_workbench.paper_composer import PaperComposerModel

                composer = PaperComposerModel.from_basket(
                    basket,
                    mode=str(mode),
                    title=title,
                    keywords=str(payload.get("keywords") or ""),
                    hot_topic=payload.get("hot_topic") is True,
                    show_question_scores=show_question_scores,
                )
                preview_model = composer.make_preview()
            except ImportError:
                # Keep the facade's original bounded behavior if an optional
                # projection import is unavailable in a minimal install.
                preview_model = {
                    "schema_version": "shchem.desktop-paper-preview.v1",
                    "mode": mode,
                    "mode_label": "模拟考试" if mode == "mock_exam" else "平时练习",
                    "title": title,
                    "show_question_scores": show_question_scores,
                    "themes": [],
                    "stats": {"theme_count": len(basket)},
                }
            except Exception as exc:
                raise DesktopFacadeError(
                    "paper_preview_projection_failed",
                    "整卷预览数据暂时无法整理，请检查题篮中的主题详情。",
                ) from exc
        preview_model["title"] = title
        preview_model["mode"] = mode
        preview_model["show_question_scores"] = show_question_scores
        if isinstance(payload.get("subtitle"), str):
            preview_model["subtitle"] = payload["subtitle"].strip()
        if isinstance(payload.get("duration_minutes"), int):
            preview_model["duration_minutes"] = payload["duration_minutes"]
        preview_model.setdefault(
            "mode_label", "模拟考试" if mode == "mock_exam" else "平时练习"
        )
        if (
            not (
                isinstance(preview_model.get("themes"), list)
                and preview_model.get("themes")
            )
            and not (
                isinstance(preview_model.get("theme_groups"), list)
                and preview_model.get("theme_groups")
            )
            and basket
        ):
            blockers.append("部分主题题面仍待展开；预览会保留当前题篮顺序。")
        blockers.append("确认本次整卷预览后才可导出。")
        basis = {
            "mode": mode,
            "title": title,
            "keywords": str(payload.get("keywords") or "").strip(),
            "hot_topic": payload.get("hot_topic") is True,
            "show_question_scores": show_question_scores,
            "basket": basket,
            "assembly": preview_model,
        }
        preview_id = "preview-" + _canonical_digest(basis)
        preview_hash = str(
            preview_model.get("preview_snapshot_sha256")
            or _canonical_digest(preview_model)
        )
        saved_at = utc_now()
        frozen = {
            "kind": "paper",
            "payload": basis,
            "preview_id": preview_id,
            "preview_hash": preview_hash,
            "preview_model": preview_model,
            "selection_basis": selection_basis,
            "approval": None,
            "created_at": saved_at,
        }
        self._state.save_draft("paper-current", frozen)
        self._state.save_draft(self._paper_preview_record_id(preview_hash), frozen)
        self._state.save_draft(
            _PAPER_PREVIEW_ACTIVE_DRAFT,
            {
                "kind": "paper_preview_pointer",
                "preview_id": preview_id,
                "preview_hash": preview_hash,
                "basket_sha256": selection_basis["basket_sha256"],
                "created_at": saved_at,
            },
        )
        return PaperPreview(
            preview_id=preview_id,
            title_zh=title,
            mode_zh="模拟考试" if mode == "mock_exam" else "平时练习",
            theme_count=len(basket),
            theme_titles=tuple(
                str(item.get("title_zh") or "未命名主题") for item in basket
            ),
            export_ready=False,
            blockers=tuple(blockers),
            preview_model=preview_model,
            preview_hash=preview_hash,
            approved=False,
        )

    def approve_paper_preview(
        self, preview_id: str, preview_hash: str
    ) -> dict[str, Any]:
        """Persist the native confirmation for the exact current preview."""

        if not isinstance(preview_id, str) or not preview_id:
            raise DesktopFacadeError("paper_preview_invalid", "整卷预览标识不正确。")
        if not isinstance(preview_hash, str) or not _SHA256.fullmatch(preview_hash):
            raise DesktopFacadeError("paper_preview_invalid", "整卷预览快照不完整。")
        snapshot = self._state.snapshot()
        drafts = snapshot.get("drafts")
        drafts = drafts if isinstance(drafts, Mapping) else {}
        active = drafts.get(_PAPER_PREVIEW_ACTIVE_DRAFT)
        draft = drafts.get(self._paper_preview_record_id(preview_hash))
        if (
            not isinstance(active, Mapping)
            or active.get("preview_id") != preview_id
            or active.get("preview_hash") != preview_hash
            or not isinstance(draft, Mapping)
            or draft.get("preview_id") != preview_id
        ):
            raise DesktopFacadeError(
                "paper_preview_stale", "当前预览已变化，请重新打开整卷预览。"
            )
        if draft.get("preview_hash") != preview_hash:
            raise DesktopFacadeError(
                "paper_preview_stale", "预览快照已变化，请重新打开整卷预览。"
            )
        approval = {
            "status": "approved",
            "preview_hash": preview_hash,
            "approved_at": utc_now(),
        }
        saved = dict(draft)
        saved["approval"] = approval
        self._state.save_draft(self._paper_preview_record_id(preview_hash), saved)
        current = drafts.get("paper-current")
        if (
            isinstance(current, Mapping)
            and current.get("preview_id") == preview_id
            and current.get("preview_hash") == preview_hash
        ):
            self._state.save_draft("paper-current", saved)
        return {
            "status": "approved",
            "message_zh": "本次整卷预览已确认，可以导出。",
        }

    def export_paper_preview(
        self,
        preview_id: str,
        preview_hash: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the existing four-file export chain for an approved preview.

        ``PaperPage`` invokes this method through ``DesktopTaskBridge``.  It is
        therefore intentional that this method waits for the local background
        export job and returns only when all four teacher/student artifacts are
        ready (or a bounded Chinese error is available).
        """

        del payload  # An approved export consumes only the frozen facade draft.
        if (
            not isinstance(preview_id, str)
            or not preview_id
            or not isinstance(preview_hash, str)
            or not _SHA256.fullmatch(preview_hash)
        ):
            raise DesktopFacadeError("paper_export_not_ready", "请先确认整卷预览。")
        snapshot = self._state.snapshot()
        drafts = snapshot.get("drafts")
        drafts = drafts if isinstance(drafts, Mapping) else {}
        active = drafts.get(_PAPER_PREVIEW_ACTIVE_DRAFT)
        draft = drafts.get(self._paper_preview_record_id(preview_hash))
        if (
            not isinstance(active, Mapping)
            or active.get("preview_id") != preview_id
            or active.get("preview_hash") != preview_hash
            or not isinstance(draft, Mapping)
            or draft.get("preview_id") != preview_id
            or draft.get("preview_hash") != preview_hash
        ):
            raise DesktopFacadeError(
                "paper_preview_stale", "当前预览已变化，请重新打开并确认整卷预览。"
            )
        if (
            not isinstance(draft.get("approval"), Mapping)
            or draft["approval"].get("status") != "approved"
            or draft["approval"].get("preview_hash") != preview_hash
        ):
            raise DesktopFacadeError(
                "paper_export_not_ready", "请先打开并确认与当前编排一致的整卷预览。"
            )
        basis = draft.get("selection_basis")
        current_basket = self._state.basket()
        if (
            not isinstance(basis, Mapping)
            or basis.get("basket_sha256") != _canonical_digest(current_basket)
            or active.get("basket_sha256") != basis.get("basket_sha256")
        ):
            raise DesktopFacadeError(
                "paper_preview_stale", "题篮已变化，请重新打开并确认整卷预览。"
            )

        # Keep one private validated reader graph for this export, including
        # catalog selection, source details and crop bytes. It is not shared
        # with library views and cannot be evicted by opening another theme.
        (
            themes,
            supplemental,
            wave_visual,
            wave_crops,
            master_workbench,
            master_direct,
        ) = snapshot_reader_graph(
            (self._themes, self._supplemental, *self._paper_export_readers())
        )

        def theme_loader(scope: str) -> dict[str, Any]:
            return (
                supplemental.theme_groups()
                if scope == "supplemental"
                else themes.groups(scope)
            )

        catalog, selections = self._paper_export_selections(
            draft, theme_loader=theme_loader
        )
        request = self._paper_export_request(draft, selections)
        master_crop_bindings: dict[str, str] = {}

        def detail_loader(source_scope: str, node_id: str) -> Mapping[str, Any]:
            try:
                if source_scope == "supplemental":
                    return supplemental.detail(node_id)
                if source_scope == "wave1":
                    return wave_visual.detail(node_id)
                if source_scope != "master":
                    raise PaperExportWorkbenchError(
                        "paper_export_scope_invalid", "当前题库范围不支持四文件导出。"
                    )
                try:
                    return master_direct.detail(node_id)
                except Exception as exc:
                    if getattr(exc, "status", None) != 404:
                        raise self._paper_export_error(
                            exc, "主索引题目的逐图详情暂时无法读取。"
                        ) from exc
                master_detail = master_workbench.atomic_detail(node_id)
                node = master_detail.get("node")
                summary = (
                    node.get("crosswalk_summary") if isinstance(node, Mapping) else None
                )
                wave_ids = (
                    summary.get("wave1_node_ids")
                    if isinstance(summary, Mapping)
                    else None
                )
                if (
                    not isinstance(summary, Mapping)
                    or summary.get("state") != "exact"
                    or not isinstance(wave_ids, list)
                    or len(wave_ids) != 1
                    or not isinstance(wave_ids[0], str)
                ):
                    raise PaperExportWorkbenchError(
                        "paper_export_visual_scan_unavailable",
                        "这道题尚无可导出的精确题面扫描，请先选择已逐图整理的题目。",
                        409,
                    )
                wave_id = wave_ids[0]
                master_crop_bindings[node_id] = wave_id
                return wave_visual.detail(wave_id)
            except PaperExportWorkbenchError:
                raise
            except Exception as exc:
                raise self._paper_export_error(
                    exc, "题目的逐图详情暂时无法读取，导出已停止。"
                ) from exc

        def crop_loader(source_scope: str, node_id: str, crop_id: str) -> Any:
            try:
                if source_scope == "supplemental":
                    return supplemental.question_crop(node_id, crop_id)
                if source_scope == "wave1":
                    return wave_crops.question_crop(node_id, crop_id)
                if source_scope != "master":
                    raise PaperExportWorkbenchError(
                        "paper_export_scope_invalid", "当前题库范围不支持四文件导出。"
                    )
                wave_id = master_crop_bindings.get(node_id)
                if wave_id is not None:
                    return wave_crops.question_crop(wave_id, crop_id)
                return master_direct.question_crop(node_id, crop_id)
            except PaperExportWorkbenchError:
                raise
            except Exception as exc:
                raise self._paper_export_error(
                    exc, "题面裁片暂时无法读取，导出已停止。"
                ) from exc

        try:
            job = self._paper_export_jobs.start(
                request,
                theme_catalog_loader=lambda source_scope: catalog,
                detail_loader=detail_loader,
                crop_loader=crop_loader,
            )
        except PaperExportWorkbenchError as exc:
            raise DesktopFacadeError(exc.code, str(exc)) from exc
        except Exception as exc:
            raise DesktopFacadeError(
                _error_code(exc), "四文件导出任务无法启动，请稍后重试。"
            ) from exc
        job_id = job.get("job_id") if isinstance(job, Mapping) else None
        if not isinstance(job_id, str):
            raise DesktopFacadeError(
                "paper_export_job_invalid", "四文件导出任务未返回有效标识。"
            )

        deadline = time.monotonic() + _PAPER_EXPORT_WAIT_SECONDS
        while job.get("status") not in {"completed", "failed"}:
            if time.monotonic() >= deadline:
                raise DesktopFacadeError(
                    "paper_export_timeout", "四文件导出等待超时，请稍后在本机重新导出。"
                )
            time.sleep(0.05)
            try:
                job = self._paper_export_jobs.get(job_id)
            except PaperExportWorkbenchError as exc:
                raise DesktopFacadeError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise DesktopFacadeError(
                    _error_code(exc), "四文件导出任务状态暂时无法读取。"
                ) from exc
        if job.get("status") == "failed":
            error = job.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            message = error.get("message_zh") if isinstance(error, Mapping) else None
            if not isinstance(code, str) or not _SAFE_ERROR_CODE.fullmatch(code):
                code = "paper_export_failed"
            if not isinstance(message, str) or not re.search(
                r"[\u4e00-\u9fff]", message
            ):
                message = "四文件导出失败，请检查题篮中的题面、答案和裁片后重试。"
            raise DesktopFacadeError(code, message)

        records = {
            row.get("artifact_id"): row
            for row in job.get("artifacts", [])
            if isinstance(row, Mapping) and isinstance(row.get("artifact_id"), str)
        }
        if set(records) != set(ARTIFACT_FILENAMES):
            raise DesktopFacadeError(
                "paper_export_artifact_incomplete",
                "导出未生成完整的学生版与教师版四个文件。",
            )
        artifacts: list[dict[str, Any]] = []
        for artifact_id in ARTIFACT_FILENAMES:
            record = records[artifact_id]
            try:
                artifact_path, _content_type = self._paper_export_jobs.artifact_path(
                    job_id, artifact_id
                )
            except PaperExportWorkbenchError as exc:
                raise DesktopFacadeError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise DesktopFacadeError(
                    _error_code(exc), "导出文件暂时无法读取，请重新导出。"
                ) from exc
            absolute_path = Path(artifact_path).resolve()
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "path": str(absolute_path),
                    "filename": str(record.get("filename") or absolute_path.name),
                    "sha256": record.get("sha256"),
                }
            )
        return {
            "status": "completed",
            "job_id": job_id,
            "artifacts": artifacts,
            "message_zh": "学生版与教师版 DOCX/PDF 四个文件已生成，可直接打开使用。",
        }

    def _preparation_manager_instance(self) -> Any:
        """Return the single synchronous preparation manager for this facade."""

        with self._preparation_lock:
            if self._preparation_manager is not None:
                return self._preparation_manager
            try:
                from .desktop_preparation_renderer import NativePreparationRenderer

                renderer = self._preparation_renderer or NativePreparationRenderer()
                self._preparation_manager = DesktopPreparationManager(
                    self.paths.task_root / "preparation-v1",
                    renderer,
                )
            except Exception as exc:
                raise DesktopFacadeError(
                    "preparation_renderer_unavailable",
                    "本机备课文件生成环境暂时不可用；仍可离线保存草稿。",
                ) from exc
            return self._preparation_manager

    @staticmethod
    def _preparation_summary(value: Mapping[str, Any]) -> PreparationTaskSummary:
        if not isinstance(value, Mapping):
            raise DesktopFacadeError(
                "preparation_task_invalid", "备课任务状态格式不正确。"
            )
        task_id = value.get("task_id")
        status = value.get("status")
        topic = value.get("topic")
        artifact_mode = value.get("artifact_mode")
        created_at = value.get("created_at")
        updated_at = value.get("updated_at")
        if (
            not isinstance(task_id, str)
            or not task_id
            or not isinstance(status, str)
            or status
            not in {
                "prepared",
                "running",
                "cancel_requested",
                "completed",
                "failed",
                "cancelled",
            }
            or not isinstance(topic, str)
            or not topic
            or artifact_mode not in {"ppt", "lesson_plan", "linked_bundle", "joint"}
            or not isinstance(created_at, str)
            or not isinstance(updated_at, str)
            or value.get("candidate_only") is not True
            or value.get("teacher_review_required") is not True
            or value.get("publication_allowed") is not False
        ):
            raise DesktopFacadeError(
                "preparation_task_invalid", "备课任务状态格式不正确。"
            )
        progress = value.get("progress")
        raw_percent = progress.get("percent") if isinstance(progress, Mapping) else 0
        percent = max(0, min(100, raw_percent if type(raw_percent) is int else 0))
        message = progress.get("message_zh") if isinstance(progress, Mapping) else None
        if not isinstance(message, str) or not message.strip():
            message = {
                "prepared": "备课请求已准备，可开始生成个人备课候选。",
                "running": "正在生成个人备课候选。",
                "cancel_requested": "正在安全停止备课任务。",
                "completed": "个人备课候选已生成，使用前仍需教师复核。",
                "failed": "备课候选未能生成，可检查设置后重试。",
                "cancelled": "备课任务已停止。",
            }.get(status, "备课任务状态已更新。")
        artifacts = value.get("artifacts")
        artifact_ids: list[str] = []
        if isinstance(artifacts, Sequence) and not isinstance(
            artifacts, (str, bytes, bytearray)
        ):
            for row in artifacts:
                artifact_id = (
                    row.get("artifact_id") if isinstance(row, Mapping) else None
                )
                if isinstance(artifact_id, str) and artifact_id not in artifact_ids:
                    artifact_ids.append(artifact_id)
        error = value.get("error")
        retryable = bool(
            status == "cancelled"
            or (
                isinstance(error, Mapping)
                and error.get("retryable") is True
                and status == "failed"
            )
        )
        slide_count = value.get("slide_count")
        source_kind = (
            value.get("source_kind")
            if value.get("source_kind") in {"teacher_revision"}
            else None
        )
        return PreparationTaskSummary(
            task_id=task_id,
            status=status,
            title_zh=topic,
            output_kind=(
                "joint" if artifact_mode == "linked_bundle" else artifact_mode
            ),
            created_at=created_at,
            updated_at=updated_at,
            progress_percent=(
                100 if status in {"completed", "failed", "cancelled"} else percent
            ),
            message_zh=message.strip(),
            artifact_ids=tuple(artifact_ids),
            slide_count=(
                slide_count if type(slide_count) is int and slide_count >= 0 else 0
            ),
            retryable=retryable,
            source_kind=source_kind,
            returned_candidate_available=(
                value.get("returned_candidate_available") is True
            ),
        )

    @staticmethod
    def _as_preparation_error(
        error: BaseException, fallback_message: str
    ) -> DesktopFacadeError:
        code = getattr(error, "code", None)
        if not isinstance(code, str) or _SAFE_ERROR_CODE.fullmatch(code) is None:
            code = "preparation_failed"
        message = getattr(error, "message_zh", None)
        if not isinstance(message, str) or not message.strip():
            message = fallback_message
        return DesktopFacadeError(code, message.strip())

    def preparation_profiles(self) -> tuple[ProviderProfileSummary, ...]:
        """Profiles capable of the text + strict-JSON preparation call."""

        profiles: list[ProviderProfileSummary] = []
        if not callable(getattr(self._providers, "borrow_invocation_context", None)):
            return ()
        for value in self._providers.list_metadata():
            if not isinstance(value, Mapping):
                continue
            profile = self._profile_summary(value)
            allowed_data_classes = value.get("allowed_data_classes")
            if (
                profile.key_saved
                and {"text", "structured_output"}.issubset(profile.capabilities)
                and isinstance(allowed_data_classes, list)
                and "question_text_redacted" in allowed_data_classes
            ):
                profiles.append(profile)
        return tuple(profiles)

    def _preparation_profile(
        self, profile_id: str, expected_revision: str
    ) -> ProviderProfileSummary:
        if not isinstance(profile_id, str) or not profile_id:
            raise DesktopFacadeError(
                "preparation_profile_missing", "请选择已配置的备课生成模型。"
            )
        if not isinstance(expected_revision, str) or not expected_revision:
            raise DesktopFacadeError(
                "preparation_profile_revision_invalid", "模型配置版本不正确。"
            )
        try:
            value = next(
                (
                    item
                    for item in self._providers.list_metadata()
                    if item.get("profile_id") == profile_id
                ),
                None,
            )
        except Exception as exc:
            raise DesktopFacadeError(
                "preparation_profile_unavailable", "备课生成模型暂时无法读取。"
            ) from exc
        if not isinstance(value, Mapping):
            raise DesktopFacadeError(
                "preparation_profile_missing", "尚未配置可用的备课生成模型。"
            )
        profile = self._profile_summary(value)
        allowed_data_classes = value.get("allowed_data_classes")
        if profile.revision != expected_revision:
            raise DesktopFacadeError(
                "preparation_profile_stale", "模型配置已变化，请刷新后重新确认。"
            )
        if not profile.key_saved or not {
            "text",
            "structured_output",
        }.issubset(profile.capabilities):
            raise DesktopFacadeError(
                "preparation_profile_missing",
                "该模型尚未配置可用 Key 或结构化文字输出能力。",
            )
        if not (
            isinstance(allowed_data_classes, list)
            and "question_text_redacted" in allowed_data_classes
        ):
            raise DesktopFacadeError(
                "preparation_data_policy_not_allowed",
                "该模型配置尚未允许发送本页教师备课文字。",
            )
        if not callable(getattr(self._providers, "borrow_invocation_context", None)):
            raise DesktopFacadeError(
                "preparation_profile_unavailable", "备课生成模型暂时无法调用。"
            )
        return profile

    def preparation_word_preview(self, path: str) -> dict[str, Any]:
        from .desktop_preparation_sources import PreparationSourcesService

        return PreparationSourcesService(self.paths.workspace_root).word_preview(path)

    def preparation_concept_options(self, query: str = "") -> list[dict[str, Any]]:
        from .desktop_preparation_sources import PreparationSourcesService

        return PreparationSourcesService(self.paths.workspace_root).concept_options(
            query
        )

    def preparation_textbook_source(
        self, concept_id: str, revision: str
    ) -> dict[str, Any]:
        from .desktop_preparation_sources import PreparationSourcesService

        return PreparationSourcesService(self.paths.workspace_root).textbook_source(
            concept_id, revision
        )

    def preparation_source_reference(
        self,
        word_path: str | None,
        word_sha256: str | None,
        block_start: int,
        block_end: int,
        concepts: list[dict[str, str]],
        *,
        textbook_excerpts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from .desktop_preparation_sources import PreparationSourcesService

        return PreparationSourcesService(self.paths.workspace_root).reference(
            word_path,
            word_sha256,
            block_start,
            block_end,
            concepts,
            textbook_excerpts=textbook_excerpts,
        )

    def preparation_blueprint_options(self) -> list[dict[str, Any]]:
        from .desktop_blueprint_preparation import BlueprintPreparationService

        return BlueprintPreparationService(self._state).options()

    def preparation_draft_options(self) -> list[dict[str, Any]]:
        from .desktop_preparation_drafts import PreparationDraftService

        return PreparationDraftService(self._state).options()

    def load_preparation_draft(
        self, draft_id: str, expected_revision: str
    ) -> dict[str, Any]:
        from .desktop_preparation_drafts import PreparationDraftService

        return PreparationDraftService(self._state).load(draft_id, expected_revision)

    def preparation_blueprint_reference(
        self, preview_id: str, source_id: str, expected_revision: str
    ) -> dict[str, Any]:
        from .desktop_blueprint_preparation import BlueprintPreparationService

        return BlueprintPreparationService(self._state).reference(
            preview_id, source_id, expected_revision
        )

    def create_preparation_draft(self, payload: Mapping[str, Any]) -> DraftReceipt:
        from .desktop_preparation import normalize_preparation_payload

        try:
            normalized = normalize_preparation_payload(payload)
        except DesktopPreparationError as exc:
            raise self._as_preparation_error(exc, "备课草稿字段不正确。") from exc
        output_kind = payload.get("output_kind")
        if output_kind not in {"ppt", "lesson_plan", "joint"}:
            raise DesktopFacadeError("preparation_kind_invalid", "请选择备课输出类型。")
        required = (
            "topic",
            "audience",
            "lesson_route",
            "lesson_timing",
            "objective",
            "materials",
        )
        if any(not str(payload.get(key) or "").strip() for key in required):
            raise DesktopFacadeError(
                "preparation_fields_required", "请填写六个常用备课字段。"
            )
        saved_at = utc_now()
        draft_id = "prep-" + uuid.uuid4().hex
        value = {
            "kind": "preparation",
            "contract_version": "lesson-blueprint/2.0.0",
            "output_kind": output_kind,
            "core_fields": {key: str(payload[key]).strip() for key in required},
            "advanced": dict(payload.get("advanced") or {}),
            "status": "draft",
            "created_at": saved_at,
        }
        if normalized.get("image_assets"):
            value["image_assets"] = normalized["image_assets"]
        self._state.save_draft(draft_id, value)
        return DraftReceipt(
            draft_id=draft_id,
            kind="preparation",
            saved_at=saved_at,
            state="draft",
            message_zh="备课草稿已保存；PPT 与教案将共享同一 Lesson Blueprint 和 O/A/E 编号。",
        )

    def import_preparation_image(
        self, file_path: str, caption: str, source: str, purpose: str
    ) -> dict[str, Any]:
        from .desktop_preparation import _reject_sensitive
        from .desktop_preparation_images import (
            PreparationImageError,
            PreparationImageStore,
        )

        try:
            _reject_sensitive(
                {"caption": caption, "source": source, "purpose": purpose}
            )
            store = PreparationImageStore(
                self.paths.task_root / "preparation-v1" / "images"
            )
            return store.import_image(file_path, caption, source, purpose)
        except (PreparationImageError, DesktopPreparationError) as exc:
            raise DesktopFacadeError(exc.code, exc.message_zh) from exc
        except OSError as exc:
            raise DesktopFacadeError(
                "preparation_image_unavailable", "本地图片无法读取或保存。"
            ) from exc

    def import_library_preparation_image(
        self, image: LibraryImage, caption: str, source: str, purpose: str
    ) -> dict[str, Any]:
        """Copy a teacher-selected verified library crop into local lesson assets."""
        from .desktop_preparation import _reject_sensitive
        from .desktop_preparation_images import (
            PreparationImageError,
            PreparationImageStore,
        )

        try:
            _reject_sensitive(
                {"caption": caption, "source": source, "purpose": purpose}
            )
            data = self.library_image(image)
            store = PreparationImageStore(
                self.paths.task_root / "preparation-v1" / "images"
            )
            return store.import_bytes(data, caption, source, purpose)
        except (PreparationImageError, DesktopPreparationError) as exc:
            raise DesktopFacadeError(exc.code, exc.message_zh) from exc
        except OSError as exc:
            raise DesktopFacadeError(
                "preparation_image_unavailable", "题库图片无法保存到本地备课素材库。"
            ) from exc

    def preparation_availability(self) -> PreparationAvailability:
        try:
            provider_ready = bool(self.preparation_profiles())
        except Exception:  # noqa: BLE001 - readiness probe must stay non-fatal
            provider_ready = False
        try:
            # Import the three libraries exercised by the native renderer so
            # the page does not offer generation when the packaged runtime is
            # incomplete.  The manager itself remains synchronous and lazy.
            __import__("PIL.Image")
            __import__("docx")
            __import__("pptx")
            self._preparation_manager_instance()
            renderer_ready = True
        except Exception:  # noqa: BLE001 - optional renderer readiness check
            renderer_ready = False
        if not provider_ready:
            message = "尚未配置可用文本模型；可先保存备课草稿。"
        elif not renderer_ready:
            message = "演示文稿运行环境未完整配置；草稿可用，生成与导出暂不可用。"
        else:
            message = "模型与演示文稿运行环境已就绪。"
        return PreparationAvailability(provider_ready, renderer_ready, message)

    def prepare_preparation(
        self,
        payload: Mapping[str, Any],
        profile_id: str,
        expected_profile_revision: str,
    ) -> PreparationTaskSummary:
        self._preparation_profile(profile_id, expected_profile_revision)
        try:
            value = self._preparation_manager_instance().prepare(
                payload,
                profile_id,
                expected_profile_revision,
            )
            return self._preparation_summary(value)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(
                exc, "备课任务无法准备，请检查填写内容后重试。"
            ) from exc

    def generate_preparation(
        self,
        task_id: str,
        *,
        teacher_confirmed: Literal[True],
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PreparationTaskSummary:
        if teacher_confirmed is not True:
            raise DesktopFacadeError(
                "teacher_confirmation_required", "调用模型前需要教师明确确认。"
            )
        manager = self._preparation_manager_instance()
        try:
            task = manager.get_task(task_id)
        except Exception as exc:
            raise self._as_preparation_error(exc, "找不到这个备课任务。") from exc
        profile_id = task.get("profile_id") if isinstance(task, Mapping) else None
        revision = task.get("profile_revision") if isinstance(task, Mapping) else None
        if isinstance(task, Mapping) and task.get("source_kind") == "teacher_revision":
            # A teacher revision already carries a validated frozen seed.  Keep
            # this branch before provider/profile lookup so retries remain
            # strictly local even when the original model profile is gone or
            # its policy has changed.
            try:
                value = manager.run(
                    task_id,
                    None,
                    report_progress=progress_callback,
                    is_cancelled=should_cancel,
                )
                return self._preparation_summary(value)
            except DesktopFacadeError:
                raise
            except Exception as exc:
                raise self._as_preparation_error(
                    exc, "本地修订导出未能完成，请检查候选文件后重试。"
                ) from exc
        if not isinstance(profile_id, str) or not isinstance(revision, str):
            raise DesktopFacadeError(
                "preparation_task_invalid", "备课任务的模型绑定不完整。"
            )
        borrow = getattr(self._providers, "borrow_invocation_context", None)
        if not callable(borrow):
            raise DesktopFacadeError(
                "preparation_profile_unavailable", "备课生成模型暂时无法调用。"
            )

        def lazy_provider(*args: Any, **kwargs: Any) -> Any:
            """Borrow a credential only if the manager has no frozen seed."""

            try:
                self._preparation_profile(profile_id, revision)
                with borrow(profile_id, expected_revision=revision) as context:
                    return StructuredPreparationProvider(
                        context,
                        transport=self._preparation_transport,
                    )(*args, **kwargs)
            except DesktopPreparationError:
                raise
            except Exception as exc:
                code = str(getattr(exc, "code", "preparation_provider_failed"))
                externally_cancelled = bool(should_cancel and should_cancel())
                if (
                    code in {"cancelled", "preparation_cancelled"}
                    or externally_cancelled
                ):
                    raise DesktopPreparationError(
                        "preparation_cancelled", "备课任务已取消。", True
                    ) from exc
                if code in {
                    "stale_revision",
                    "revision_conflict",
                    "preparation_profile_stale",
                }:
                    raise DesktopPreparationError(
                        "preparation_profile_stale",
                        "模型配置已变化，请重新创建备课任务。",
                        False,
                    ) from exc
                safe_code = (
                    code
                    if _SAFE_ERROR_CODE.fullmatch(code)
                    else "preparation_provider_failed"
                )
                message = getattr(exc, "message_zh", None)
                if not isinstance(message, str) or not message.strip():
                    message = "模型服务暂时没有返回可用的备课内容，请稍后重试。"
                raise DesktopPreparationError(
                    safe_code,
                    message.strip(),
                    bool(getattr(exc, "retryable", True)),
                ) from exc

        try:
            value = manager.run(
                task_id,
                lazy_provider,
                report_progress=progress_callback,
                is_cancelled=should_cancel,
            )
            return self._preparation_summary(value)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            fallback = (
                "模型配置已变化，请刷新后重新确认。"
                if code in {"stale_revision", "revision_conflict"}
                else "备课生成未能完成，请检查模型设置后重试。"
            )
            raise self._as_preparation_error(exc, fallback) from exc

    def preparation_revision_source(self, task_id: str) -> dict[str, Any]:
        """Return one completed candidate for an offline teacher edit.

        The response intentionally contains only the task id, the byte-level
        candidate revision, and the validated canonical candidate.  No provider
        profile, credential or arbitrary local path is exposed to the UI.
        """

        try:
            value = self._preparation_manager_instance().revision_source(task_id)
            return {
                "task_id": value["task_id"],
                "source_revision": value["source_revision"],
                "candidate": deepcopy(value["candidate"]),
            }
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(exc, "备课候选暂时无法打开修订。") from exc

    def preparation_returned_source(self, task_id: str) -> dict[str, Any]:
        """Return a failed task's persisted, non-canonical model response.

        This is a read-only recovery source.  It exposes no provider binding,
        credential, payload, or filesystem path; the returned candidate must
        go through the explicit comparison-table repair endpoint below before
        it can become a local rendering seed.
        """

        try:
            value = self._preparation_manager_instance().returned_source(task_id)
            return {
                "task_id": value["task_id"],
                "source_revision": value["source_revision"],
                "candidate": deepcopy(value["candidate"]),
                "error_message": value["error_message"],
            }
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(
                exc, "模型返回稿暂时无法打开修订。"
            ) from exc

    def revise_preparation(
        self,
        task_id: str,
        source_revision: str,
        edits: list[dict[str, Any]],
        *,
        note: str = "",
        structure_edits: list[dict[str, Any]] | None = None,
    ) -> PreparationTaskSummary:
        """Apply explicit teacher text/structure edits and render a local child.

        The edit module owns the field whitelist and canonical re-validation.
        This facade method never borrows a provider: the child task is seeded
        locally and rendered through the existing native renderer only.
        """

        from .desktop_preparation_structure import apply_preparation_revision

        manager = self._preparation_manager_instance()
        try:
            source = manager.revision_source(task_id)
            candidate = apply_preparation_revision(
                source["candidate"], edits, structure_edits, source["payload"]
            )
            child = manager.create_revision(
                task_id,
                source_revision,
                candidate,
                note=note,
            )
            rendered = manager.run(
                child["task_id"],
                None,
                report_progress=None,
                is_cancelled=None,
            )
            return self._preparation_summary(rendered)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(
                exc, "本地修订导出未能完成，请检查修订内容后重试。"
            ) from exc

    def repair_returned_preparation(
        self,
        task_id: str,
        source_revision: str,
        edits: list[dict[str, Any]],
        *,
        note: str = "",
    ) -> PreparationTaskSummary:
        """Repair a failed response through the narrow local table editor.

        The endpoint accepts only complete comparison-table replacements keyed
        by one-based slide number.  It normalizes the repaired response using
        the ordinary candidate validator, creates a separate teacher-revision
        child, and renders that child with ``provider=None``.
        """

        from .desktop_preparation import normalize_preparation_candidate
        from .desktop_preparation_recovery import apply_returned_comparison_edits

        manager = self._preparation_manager_instance()
        try:
            source = manager.returned_source(task_id)
            repaired = apply_returned_comparison_edits(
                source["candidate"], edits
            )
            candidate = normalize_preparation_candidate(
                repaired, source["payload"]
            )
            child = manager.create_returned_revision(
                task_id,
                source_revision,
                candidate,
                note=note,
            )
            rendered = manager.run(
                child["task_id"],
                None,
                report_progress=None,
                is_cancelled=None,
            )
            return self._preparation_summary(rendered)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(
                exc, "模型返回稿本地修订未能完成，请检查比较表后重试。"
            ) from exc

    def get_preparation(self, task_id: str) -> PreparationTaskSummary:
        try:
            return self._preparation_summary(
                self._preparation_manager_instance().get_task(task_id)
            )
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(exc, "找不到这个备课任务。") from exc

    def list_preparations(
        self, *, limit: int = 3
    ) -> tuple[PreparationTaskSummary, ...]:
        try:
            values = self._preparation_manager_instance().list_tasks(limit=limit)
            return tuple(self._preparation_summary(value) for value in values)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(exc, "最近备课任务暂时无法读取。") from exc

    def cancel_preparation(self, task_id: str) -> PreparationTaskSummary:
        try:
            value = self._preparation_manager_instance().cancel(task_id)
            return self._preparation_summary(value)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(exc, "备课任务暂时无法停止。") from exc

    def retry_preparation(self, task_id: str) -> PreparationTaskSummary:
        try:
            value = self._preparation_manager_instance().retry(task_id)
            return self._preparation_summary(value)
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(exc, "备课任务暂时无法重试。") from exc

    def preparation_classroom_review(self, task_id: str) -> dict[str, Any]:
        """Read the registered, hash-checked candidate; never invoke a provider."""
        from .desktop_preparation_review import classroom_review

        try:
            source = self._preparation_manager_instance().revision_source(task_id)
            report = classroom_review(source["candidate"])
            # Use the task's original selected reference, never the current
            # form or a model-written source summary. No provider data escapes.
            report["source_reference"] = {
                "topic": source["payload"]["topic"],
                "materials": source["payload"]["materials"],
            }
            return report
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(exc, "课堂结构暂时无法读取。") from exc

    def preparation_paper_source_snapshot(
        self, snapshot: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Enrich the current paper-composer draft with exact source text only."""
        from .desktop_paper_preparation_sources import (
            enrich_paper_preparation_sources,
        )

        return enrich_paper_preparation_sources(
            snapshot,
            detail_loader=self.library_theme_detail,
        )

    def preparation_artifact_path(self, task_id: str, artifact_id: str) -> Path:
        try:
            path, _content_type = self._preparation_manager_instance().artifact_path(
                task_id, artifact_id
            )
            return Path(path).resolve()
        except DesktopFacadeError:
            raise
        except Exception as exc:
            raise self._as_preparation_error(exc, "备课候选文件暂时无法读取。") from exc

    @property
    def state_store(self) -> DesktopStateStore:
        return self._state


def build_default_facade(paths: DesktopPaths | None = None) -> DesktopWorkbenchFacade:
    return DesktopWorkbenchFacade(paths or DesktopPaths.discover())


__all__ = [
    "DESKTOP_REGISTRY_SCHEMA",
    "DESKTOP_SCOPES",
    "IMPORT_STATES",
    "PERSONAL_HANDOUT_SCOPE",
    "PRIMARY_NAVIGATION",
    "CurriculumSectionSummary",
    "CurriculumStatus",
    "DesktopFacadeError",
    "DesktopRegistry",
    "DesktopVisualImportReceipt",
    "DesktopVisualImportSourceSummary",
    "DesktopWorkbenchFacade",
    "DraftReceipt",
    "PaperPreview",
    "PreparationAvailability",
    "PreparationTaskSummary",
    "ProductStatus",
    "ProviderProfileInput",
    "ProviderProfileSummary",
    "StudentAnalysisConfirmation",
    "StudentAnalysisReview",
    "StudentFileSummary",
    "StudentMatchSummary",
    "StudentPageSummary",
    "StudentProfileSummary",
    "StudentReviewItem",
    "StudentSubmissionSummary",
    "ThemeCard",
    "ThemeSearchResult",
    "build_default_facade",
]
