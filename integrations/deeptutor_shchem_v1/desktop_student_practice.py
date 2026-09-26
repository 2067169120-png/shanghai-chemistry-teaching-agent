"""Native, read-only practice suggestions with an explicit preview-to-basket handoff.

Current local review journals are read, but no model invocation, student-page
transmission, mastery update, or backend basket serialization is used. The
existing recommendation engine supplies complete source theme cards; each
native action rechecks the submission and its source/mapping snapshots.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .reader_cancellation import check_read_cancelled
from .student_recommendation_projection import project_visual_recommendation_payload
from .student_recommendation_workbench import StudentRecommendationWorkbench

if TYPE_CHECKING:
    from .desktop_facade import DesktopWorkbenchFacade, ThemeCard
    from .desktop_library import LibraryThemeDetail


@dataclass(frozen=True)
class StudentPracticeDiagnosis:
    title_zh: str
    status_zh: str
    evidence_zh: str


@dataclass(frozen=True)
class StudentPracticeRecommendation:
    key: str = field(repr=False)
    title_zh: str
    source_zh: str
    reason_zh: str
    matched_question_count: int
    atomic_total: int
    shared_material_count: int
    readiness_zh: str


@dataclass(frozen=True)
class StudentPracticePreview:
    student_id: str = field(repr=False)
    submission_id: str = field(repr=False)
    revision: str = field(repr=False)
    preview_hash: str = field(repr=False)
    message_zh: str
    notices_zh: tuple[str, ...]
    diagnoses: tuple[StudentPracticeDiagnosis, ...]
    recommendations: tuple[StudentPracticeRecommendation, ...]


class StudentPracticeError(RuntimeError):
    def __init__(self, code: str, message_zh: str):
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


@dataclass
class _PreviewRecord:
    preview: StudentPracticePreview
    curriculum_digest: str
    decision_digest: str
    cards: dict[str, ThemeCard]
    mapping_bindings: tuple[tuple[dict[str, str], str], ...] = ()
    viewed: set[str] = field(default_factory=set)


class DesktopStudentPractice:
    """Small, per-window memory registry. Only an explicit add writes the basket."""

    def __init__(self, facade: DesktopWorkbenchFacade):
        self.facade = facade
        self._records: dict[str, _PreviewRecord] = {}
        self._lock = threading.RLock()

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    @staticmethod
    def _digest(value: Any) -> str:
        from .desktop_facade import _canonical_digest

        return _canonical_digest(value)

    def _review_state(self, student_id, submission_id):
        manager = self.facade._student_manager_instance()
        before = manager.get_submission(student_id, submission_id)
        review = manager.get_review(student_id, submission_id)
        diagnostic = manager.get_diagnostic_review(student_id, submission_id)
        after = manager.get_submission(student_id, submission_id)
        if before.get("revision") != after.get("revision") or not after.get("revision"):
            raise StudentPracticeError(
                "student_practice_stale", "学生分析状态已变化，请刷新后重新推荐。"
            )
        return after, review, diagnostic

    @staticmethod
    def _decision_digest(review, diagnostic):
        return DesktopStudentPractice._digest(
            {
                "analysis": review.get("analysis"),
                "scoring_decisions": review.get("scoring_decisions"),
                "diagnostic_decisions": diagnostic.get("diagnostic_decisions"),
            }
        )

    def preview(self, *, student_id: str, submission_id: str) -> StudentPracticePreview:
        from .desktop_facade import ThemeCard

        facade = self.facade
        submission, review, diagnostic = self._review_state(student_id, submission_id)
        revision = str(submission["revision"])
        decision_digest = self._decision_digest(review, diagnostic)
        curriculum = facade.curriculum_catalog(force_refresh=True)
        curriculum_digest = self._digest(curriculum)
        request_snapshot = self._digest(
            {"curriculum": curriculum_digest, "submission": revision}
        )
        payload = project_visual_recommendation_payload(
            submission_id=submission_id,
            review=review,
            diagnostic=diagnostic,
            data_snapshot_id=request_snapshot,
        )
        catalogs: dict[str, dict] = {}
        source_snapshots: dict[str, str] = {}
        mappings: dict[str, dict] = {}
        mapping_bindings: dict[str, tuple[dict[str, str], str]] = {}

        def load_theme(scope):
            if scope not in catalogs:
                catalogs[scope] = facade._load_theme_scope(scope)
                source_snapshots[scope] = facade._paper_catalog_snapshot_id(
                    catalogs[scope]
                )
            return deepcopy(catalogs[scope])

        def load_mapping(selector):
            key = self._digest(selector)
            if key not in mappings:
                mappings[key] = facade._curriculum_search(selector)
                mapping_bindings[key] = (
                    deepcopy(selector),
                    self._digest(mappings[key]),
                )
            return deepcopy(mappings[key])

        def search(search_payload):
            return facade._search.search(
                search_payload,
                theme_loader=load_theme,
                curriculum_loader=load_mapping,
                snapshot_id=request_snapshot,
                complete_themes_only=True,
            )

        result = StudentRecommendationWorkbench().preview(
            payload,
            curriculum_catalog_loader=lambda: curriculum,
            question_search_loader=search,
        )
        cards = {}
        recommendations = []
        for group in result["scope_groups"]:
            scope = group["scope"]
            for item in group["items"]:
                paper, theme, counts, shared = (
                    item[key] for key in ("paper", "theme", "counts", "shared_context")
                )
                if scope not in source_snapshots:
                    raise StudentPracticeError(
                        "student_practice_source_unbound",
                        "推荐题目没有当前题库绑定，请刷新。",
                    )
                identity = self._digest(
                    {"scope": scope, "paper": paper["id"], "theme": theme["id"]}
                )
                key = self._digest(
                    {"identity": identity, "snapshot": source_snapshots[scope]}
                )
                card = ThemeCard(
                    key=identity,
                    scope=scope,
                    title_zh=str(
                        item.get("display_title_zh")
                        or theme.get("title")
                        or "未命名主题"
                    ),
                    paper_title_zh=str(paper.get("title") or "来源卷名称待核验"),
                    source_zh=facade._source_label(item),
                    atomic_total=int(counts["atomic_total"]),
                    atomic_matched=int(counts["atomic_matched"]),
                    shared_context_zh=str(
                        shared.get("context_summary_zh") or "共同材料请在完整题目中查看"
                    ),
                    page_zh=facade._page_label(item),
                    source_identity_sha256=identity,
                    data_snapshot_id=source_snapshots[scope],
                    display_atomic_units=int(
                        counts.get("display_atomic_units") or counts["atomic_total"]
                    ),
                )
                cards[key] = card
                recommendations.append(
                    StudentPracticeRecommendation(
                        key=key,
                        title_zh=card.title_zh,
                        source_zh=card.paper_title_zh + " · " + card.source_zh,
                        reason_zh=item["recommendation_reason_zh"],
                        matched_question_count=card.atomic_matched,
                        atomic_total=card.display_atomic_units,
                        shared_material_count=int(shared.get("material_count") or 0),
                        readiness_zh="按已绑定教材章节匹配；请先核对完整题面、共同材料及答案。",
                    )
                )
        diagnoses = tuple(
            StudentPracticeDiagnosis(
                title_zh=row["section"]["display_label_zh"],
                status_zh={
                    "no_weakness_evidence": "本次未见失分证据",
                    "provisional_weakness": "本次失分线索",
                    "stable_weakness": "多次证据待综合复核",
                }[row["diagnosis_status"]],
                evidence_zh=(
                    f"支持证据 {row['metrics']['supporting_evidence_count']} 项；"
                    f"相反证据 {row['metrics']['counterevidence_count']} 项。"
                ),
            )
            for row in result["diagnoses"]
        )
        notices = [str(item["message_zh"]) for item in result["blockers"]]
        projection = facade._student_review_projection(submission, review, diagnostic)
        if any(item.diagnostic_requires_reconfirmation for item in projection.items):
            notices.insert(
                0, "有题目在确认诊断后改过分，请重新确认诊断；旧诊断不参与本次推荐。"
            )
        notices.append(
            "本次作业只提供本次练习线索，不自动形成长期薄弱标签或写入长期学情。"
        )
        if recommendations:
            message = f"找到 {len(recommendations)} 道完整主题练习。先看题目与材料，再决定是否加入题篮。"
        elif diagnoses:
            message = "已整理本次教师确认的章节表现；当前没有可推荐的未重复完整主题。"
        else:
            message = (
                "尚无可用于推荐的章节失分证据。请先记录教师评分，并确认诊断及教材章节。"
            )
        preview_hash = self._digest(
            {
                "student_id": student_id,
                "submission_id": submission_id,
                "revision": revision,
                "decisions": decision_digest,
                "curriculum": curriculum_digest,
                "source_snapshots": source_snapshots,
                "mappings": mapping_bindings,
                "result": result,
            }
        )
        preview = StudentPracticePreview(
            student_id,
            submission_id,
            revision,
            preview_hash,
            message,
            tuple(dict.fromkeys(notices)),
            diagnoses,
            tuple(recommendations),
        )
        self._check_student(preview, decision_digest)
        with self._lock:
            check_read_cancelled()
            self._records = {
                key: record
                for key, record in self._records.items()
                if (record.preview.student_id, record.preview.submission_id)
                != (student_id, submission_id)
            }
            self._records[preview_hash] = _PreviewRecord(
                preview,
                curriculum_digest,
                decision_digest,
                cards,
                tuple(mapping_bindings.values()),
            )
            while len(self._records) > 8:
                self._records.pop(next(iter(self._records)))
        return preview

    def _check_student(self, preview, expected_digest):
        submission, review, diagnostic = self._review_state(
            preview.student_id, preview.submission_id
        )
        if (
            str(submission["revision"]) != preview.revision
            or self._decision_digest(review, diagnostic) != expected_digest
        ):
            raise StudentPracticeError(
                "student_practice_stale", "评分或诊断已变化，请重新推荐并查看题目。"
            )

    def _validated(self, preview: StudentPracticePreview, key: str):
        with self._lock:
            record = (
                self._records.get(preview.preview_hash)
                if isinstance(preview, StudentPracticePreview)
                else None
            )
            if record is None or record.preview != preview or key not in record.cards:
                raise StudentPracticeError(
                    "student_practice_preview_missing", "推荐预览已失效，请重新生成。"
                )
            card = record.cards[key]
        self._check_student(preview, record.decision_digest)
        if (
            self._digest(self.facade.curriculum_catalog(force_refresh=True))
            != record.curriculum_digest
        ):
            raise StudentPracticeError(
                "student_practice_curriculum_stale", "教材映射已变化，请重新推荐。"
            )
        for selector, digest in record.mapping_bindings:
            if self._digest(self.facade._curriculum_search(selector)) != digest:
                raise StudentPracticeError(
                    "student_practice_curriculum_stale", "教材映射已变化，请重新推荐。"
                )
        if (
            self.facade._paper_catalog_snapshot_id(
                self.facade._load_theme_scope(card.scope)
            )
            != card.data_snapshot_id
        ):
            raise StudentPracticeError(
                "student_practice_library_stale",
                "题库内容已变化，请重新推荐并核对题目。",
            )
        self._check_student(preview, record.decision_digest)
        return record, card

    def theme_detail(
        self, preview: StudentPracticePreview, key: str
    ) -> LibraryThemeDetail:
        record, card = self._validated(preview, key)
        detail = self.facade.library_theme_detail(card)
        self._check_student(preview, record.decision_digest)
        with self._lock:
            if self._records.get(preview.preview_hash) is not record:
                raise StudentPracticeError(
                    "student_practice_stale", "推荐已刷新，请重新打开完整题目。"
                )
            record.viewed.add(key)
        return detail

    def add_to_basket(self, preview: StudentPracticePreview, key: str) -> int:
        record, card = self._validated(preview, key)
        with self._lock:
            check_read_cancelled()
            if (
                self._records.get(preview.preview_hash) is not record
                or key not in record.viewed
            ):
                raise StudentPracticeError(
                    "student_practice_preview_required",
                    "请先查看完整题目与共同材料，再加入题篮。",
                )
            return self.facade.add_theme_to_basket(card)
