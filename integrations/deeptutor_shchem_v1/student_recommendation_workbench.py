from __future__ import annotations

"""Teacher-confirmed, read-only diagnosis and recommendation orchestration.

This module deliberately does not read student files, infer textbook sections,
or write mastery/profile state.  Its inputs are a minimal projection prepared
by the service layer: visual matches, the append-only teacher decision history,
and explicit exclusions.  Only textbook sections accepted or edited by the
teacher are joined to the verified 5/19/60 curriculum catalog.

Recommendations are returned as complete theme cards.  Atomic matches remain
highlights inside the original paper -> theme -> printed -> atomic chain.
"""

import math
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

CONTRACT_VERSION = "shchem.student-recommendation-preview.v1"
CLAIM_SCOPE = "teacher_confirmed_candidate_diagnosis_and_theme_recommendation"
SCOPE_ORDER = ("master", "wave1", "supplemental")
TEACHER_DIAGNOSIS_ACTIONS = frozenset({"accept", "edit", "reject", "pending"})
SCORING_CONFIRMATIONS = frozenset({"pending", "teacher_confirmed"})
EVIDENCE_SUFFICIENCY = frozenset({"insufficient_evidence", "sufficient_evidence"})
DIAGNOSIS_STATUSES = frozenset(
    {"no_weakness_evidence", "provisional_weakness", "stable_weakness"}
)

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,239}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PAYLOAD_KEYS = frozenset(
    {
        "submission_id",
        "data_snapshot_id",
        "matches",
        "scoring_decisions",
        "exclusions",
        "scopes",
        "limit_per_section",
    }
)
_MATCH_KEYS = frozenset({"match_id", "atomic_part_id", "theme_id", "paper_id"})
_DECISION_REQUIRED_KEYS = frozenset(
    {"sequence", "match_id", "teacher_score", "maximum_score", "decision"}
)
_DECISION_OPTIONAL_KEYS = frozenset(
    {
        "atomic_part_id",
        "curriculum_sections",
        "curriculum_section_keys",
        "diagnosis_action",
        "teacher_diagnosis_action",
    }
)
_SECTION_KEYS = frozenset(
    {
        "section_key",
        "volume_id",
        "volume_title",
        "chapter_id",
        "chapter_title",
        "section_number",
        "section_title",
        "display_label_zh",
    }
)
_EXCLUSION_KEYS = frozenset({"atomic_part_ids", "theme_ids", "paper_ids"})
_FORBIDDEN_CARD_KEYS = frozenset(
    {
        "answer_text",
        "reference_answer_text",
        "solution_path_zh",
        "source_path",
        "local_path",
        "absolute_path",
        "crop_path",
        "url",
        "source_url",
        "student_id",
        "profile_id",
    }
)
_SCORE_EPSILON = 1e-9


class StudentRecommendationWorkbenchError(RuntimeError):
    """Fail-closed public error from the recommendation preview core."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _Section:
    section_key: str
    volume_id: str
    volume_title: str
    chapter_id: str
    chapter_title: str
    section_number: str
    section_title: str
    display_label_zh: str

    def public(self) -> dict[str, str]:
        return {
            "section_key": self.section_key,
            "volume_id": self.volume_id,
            "volume_title": self.volume_title,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "display_label_zh": self.display_label_zh,
        }


@dataclass(frozen=True)
class _Match:
    match_id: str
    atomic_part_id: str
    theme_id: str | None
    paper_id: str | None


@dataclass(frozen=True)
class _Decision:
    sequence: int
    match_id: str
    atomic_part_id: str | None
    teacher_score: float
    maximum_score: float
    action: str
    section_keys: tuple[str, ...]


def _require_safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_identifier_invalid", f"{label} is invalid"
        )
    return value


def _optional_safe_id(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _require_safe_id(value, label)


def _require_text(value: Any, label: str, *, limit: int = 240) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > limit
    ):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_catalog_invalid", f"{label} is invalid", 409
        )
    return value


def _score(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_score_invalid", f"{label} is invalid"
        )
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_score_invalid", f"{label} is invalid"
        )
    return result


def _id_list(value: Any, label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 500:
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_request_invalid", f"{label} is invalid"
        )
    result = tuple(_require_safe_id(item, label) for item in value)
    if len(result) != len(set(result)) or (not allow_empty and not result):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_request_invalid", f"{label} is invalid"
        )
    return tuple(sorted(result))


def _catalog_sections(value: Any) -> tuple[dict[str, _Section], str]:
    if not isinstance(value, Mapping):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_catalog_invalid",
            "curriculum catalog is unavailable",
            409,
        )
    catalog_snapshot_id = value.get("data_snapshot_id")
    if not isinstance(catalog_snapshot_id, str) or not catalog_snapshot_id:
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_catalog_invalid",
            "curriculum catalog snapshot is invalid",
            409,
        )
    volumes = value.get("volumes")
    counts = value.get("counts")
    if not isinstance(volumes, list) or not isinstance(counts, Mapping):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_catalog_invalid",
            "curriculum catalog shape is invalid",
            409,
        )
    sections: dict[str, _Section] = {}
    chapter_count = 0
    for volume in volumes:
        if not isinstance(volume, Mapping):
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_catalog_invalid",
                "curriculum volume is invalid",
                409,
            )
        volume_id = _require_safe_id(volume.get("volume_id"), "volume_id")
        volume_title = _require_text(volume.get("volume_title"), "volume_title")
        chapters = volume.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_catalog_invalid",
                "curriculum chapters are invalid",
                409,
            )
        chapter_count += len(chapters)
        for chapter in chapters:
            if not isinstance(chapter, Mapping):
                raise StudentRecommendationWorkbenchError(
                    "student_recommendation_catalog_invalid",
                    "curriculum chapter is invalid",
                    409,
                )
            chapter_id = _require_safe_id(chapter.get("chapter_id"), "chapter_id")
            chapter_title = _require_text(chapter.get("chapter_title"), "chapter_title")
            chapter_sections = chapter.get("sections")
            if not isinstance(chapter_sections, list) or not chapter_sections:
                raise StudentRecommendationWorkbenchError(
                    "student_recommendation_catalog_invalid",
                    "curriculum sections are invalid",
                    409,
                )
            for section in chapter_sections:
                if not isinstance(section, Mapping):
                    raise StudentRecommendationWorkbenchError(
                        "student_recommendation_catalog_invalid",
                        "curriculum section is invalid",
                        409,
                    )
                section_key = _require_safe_id(
                    section.get("section_key"), "section_key"
                )
                if section_key in sections:
                    raise StudentRecommendationWorkbenchError(
                        "student_recommendation_catalog_invalid",
                        "curriculum section is duplicated",
                        409,
                    )
                section_number = _require_text(
                    section.get("section_number"), "section_number", limit=40
                )
                section_title = _require_text(
                    section.get("section_title"), "section_title"
                )
                display_label = _require_text(
                    section.get("display_label_zh"), "display_label_zh"
                )
                sections[section_key] = _Section(
                    section_key=section_key,
                    volume_id=volume_id,
                    volume_title=volume_title,
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    section_number=section_number,
                    section_title=section_title,
                    display_label_zh=display_label,
                )
    if (
        len(volumes) != 5
        or chapter_count != 19
        or len(sections) != 60
        or counts.get("volumes") != 5
        or counts.get("chapters") != 19
        or counts.get("sections") != 60
    ):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_catalog_invalid",
            "curriculum allowlist is not the verified 5/19/60 catalog",
            409,
        )
    return sections, catalog_snapshot_id


def _parse_matches(value: Any) -> tuple[_Match, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 200:
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_request_invalid", "matches are invalid"
        )
    result: list[_Match] = []
    seen: set[str] = set()
    for row in value:
        if not isinstance(row, Mapping) or set(row) != set(_MATCH_KEYS):
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_request_invalid", "match shape is invalid"
            )
        match_id = _require_safe_id(row.get("match_id"), "match_id")
        if match_id in seen:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_request_invalid", "match id is duplicated"
            )
        seen.add(match_id)
        result.append(
            _Match(
                match_id=match_id,
                atomic_part_id=_require_safe_id(
                    row.get("atomic_part_id"), "atomic_part_id"
                ),
                theme_id=_optional_safe_id(row.get("theme_id"), "theme_id"),
                paper_id=_optional_safe_id(row.get("paper_id"), "paper_id"),
            )
        )
    return tuple(sorted(result, key=lambda item: item.match_id))


def _decision_action(row: Mapping[str, Any]) -> str:
    values = [
        row.get(key)
        for key in ("decision", "diagnosis_action", "teacher_diagnosis_action")
        if key in row
    ]
    if not values or any(value not in TEACHER_DIAGNOSIS_ACTIONS for value in values):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_decision_invalid",
            "teacher diagnosis decision is invalid",
        )
    if len(set(values)) != 1:
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_decision_invalid",
            "teacher diagnosis decision aliases conflict",
        )
    return str(values[0])


def _section_keys_from_decision(
    row: Mapping[str, Any], sections: Mapping[str, _Section], action: str
) -> tuple[str, ...]:
    detailed_keys: list[str] | None = None
    if "curriculum_sections" in row:
        raw_sections = row.get("curriculum_sections")
        if not isinstance(raw_sections, list) or len(raw_sections) > 60:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_decision_invalid",
                "curriculum sections are invalid",
            )
        detailed_keys = []
        for raw in raw_sections:
            if not isinstance(raw, Mapping) or set(raw) - set(_SECTION_KEYS):
                raise StudentRecommendationWorkbenchError(
                    "student_recommendation_decision_invalid",
                    "curriculum section shape is invalid",
                )
            section_key = _require_safe_id(raw.get("section_key"), "section_key")
            detailed_keys.append(section_key)
            catalog = sections.get(section_key)
            if action in {"accept", "edit"} and catalog is None:
                raise StudentRecommendationWorkbenchError(
                    "student_recommendation_section_not_allowed",
                    "teacher-confirmed curriculum section is outside the allowlist",
                )
            if catalog is not None:
                for field in ("volume_id", "chapter_id"):
                    if field in raw and raw[field] != getattr(catalog, field):
                        raise StudentRecommendationWorkbenchError(
                            "student_recommendation_section_parent_mismatch",
                            "curriculum section parent conflicts with the catalog",
                        )
    minimal_keys: list[str] | None = None
    if "curriculum_section_keys" in row:
        raw_keys = row.get("curriculum_section_keys")
        if not isinstance(raw_keys, list) or len(raw_keys) > 60:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_decision_invalid",
                "curriculum section keys are invalid",
            )
        minimal_keys = [_require_safe_id(item, "section_key") for item in raw_keys]
    chosen = detailed_keys if detailed_keys is not None else minimal_keys or []
    if len(chosen) != len(set(chosen)):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_decision_invalid",
            "curriculum section is duplicated",
        )
    if (
        detailed_keys is not None
        and minimal_keys is not None
        and set(detailed_keys) != set(minimal_keys)
    ):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_decision_invalid",
            "curriculum section projections conflict",
        )
    if action not in {"accept", "edit"}:
        return ()
    for section_key in chosen:
        if section_key not in sections:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_section_not_allowed",
                "teacher-confirmed curriculum section is outside the allowlist",
            )
    return tuple(sorted(chosen))


def _parse_decisions(
    value: Any,
    *,
    matches: Mapping[str, _Match],
    sections: Mapping[str, _Section],
) -> tuple[_Decision, ...]:
    if not isinstance(value, list) or len(value) > 2000:
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_request_invalid", "scoring decisions are invalid"
        )
    result: list[_Decision] = []
    seen_sequences: set[int] = set()
    for row in value:
        if (
            not isinstance(row, Mapping)
            or not _DECISION_REQUIRED_KEYS.issubset(row)
            or set(row) - (_DECISION_REQUIRED_KEYS | _DECISION_OPTIONAL_KEYS)
        ):
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_decision_invalid",
                "teacher decision shape is invalid",
            )
        sequence = row.get("sequence")
        if type(sequence) is not int or sequence < 1 or sequence in seen_sequences:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_decision_invalid",
                "teacher decision sequence is invalid",
            )
        seen_sequences.add(sequence)
        match_id = _require_safe_id(row.get("match_id"), "match_id")
        match = matches.get(match_id)
        if match is None:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_decision_invalid",
                "teacher decision references an unknown match",
            )
        atomic_part_id = _optional_safe_id(row.get("atomic_part_id"), "atomic_part_id")
        if atomic_part_id is not None and atomic_part_id != match.atomic_part_id:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_decision_invalid",
                "teacher decision changed the matched atomic part",
            )
        maximum = _score(row.get("maximum_score"), "maximum_score", positive=True)
        teacher_score = _score(row.get("teacher_score"), "teacher_score")
        if teacher_score > maximum + _SCORE_EPSILON:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_score_invalid",
                "teacher score exceeds the maximum score",
            )
        action = _decision_action(row)
        result.append(
            _Decision(
                sequence=sequence,
                match_id=match_id,
                atomic_part_id=atomic_part_id,
                teacher_score=teacher_score,
                maximum_score=maximum,
                action=action,
                section_keys=_section_keys_from_decision(row, sections, action),
            )
        )
    return tuple(sorted(result, key=lambda item: item.sequence))


def _latest_decisions(decisions: Sequence[_Decision]) -> dict[str, _Decision]:
    latest: dict[str, _Decision] = {}
    for decision in decisions:
        previous = latest.get(decision.match_id)
        if previous is None or decision.sequence > previous.sequence:
            latest[decision.match_id] = decision
    return latest


def _parse_exclusions(
    value: Any, matches: Sequence[_Match]
) -> dict[str, frozenset[str]]:
    if not isinstance(value, Mapping) or set(value) != set(_EXCLUSION_KEYS):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_request_invalid", "exclusions are invalid"
        )
    atomic = set(_id_list(value.get("atomic_part_ids"), "atomic_part_ids"))
    themes = set(_id_list(value.get("theme_ids"), "theme_ids"))
    papers = set(_id_list(value.get("paper_ids"), "paper_ids"))
    atomic.update(item.atomic_part_id for item in matches)
    themes.update(item.theme_id for item in matches if item.theme_id is not None)
    papers.update(item.paper_id for item in matches if item.paper_id is not None)
    return {
        "atomic": frozenset(atomic),
        "theme": frozenset(themes),
        "paper": frozenset(papers),
    }


def _parse_scopes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_request_invalid", "scopes are invalid"
        )
    if any(scope not in SCOPE_ORDER for scope in value) or len(value) != len(
        set(value)
    ):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_request_invalid", "scopes are invalid"
        )
    selected = set(value)
    return tuple(scope for scope in SCOPE_ORDER if scope in selected)


def _reject_forbidden_card_projection(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_CARD_KEYS:
                raise StudentRecommendationWorkbenchError(
                    "student_recommendation_search_invalid",
                    "question search returned a forbidden field",
                    502,
                )
            _reject_forbidden_card_projection(nested)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_card_projection(item)


def _card_identity(card: Mapping[str, Any], expected_scope: str) -> tuple[str, str]:
    if card.get("scope") != expected_scope or card.get("group_kind") != "theme":
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_theme_card_invalid",
            "recommendation is not a complete theme card",
            502,
        )
    paper = card.get("paper")
    theme = card.get("theme")
    chain = card.get("atomic_chain")
    counts = card.get("counts")
    shared = card.get("shared_context")
    dependencies = card.get("dependencies")
    if (
        not isinstance(paper, Mapping)
        or not isinstance(theme, Mapping)
        or not isinstance(chain, list)
        or not chain
        or not isinstance(counts, Mapping)
        or not isinstance(shared, Mapping)
        or not isinstance(dependencies, Mapping)
    ):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_theme_card_invalid",
            "recommendation theme chain is incomplete",
            502,
        )
    paper_id = _require_safe_id(paper.get("id"), "paper_id")
    theme_id = _require_safe_id(theme.get("id"), "theme_id")
    atomic_ids = [
        _require_safe_id(item.get("atomic_part_id"), "atomic_part_id")
        if isinstance(item, Mapping)
        else ""
        for item in chain
    ]
    if (
        "" in atomic_ids
        or len(atomic_ids) != len(set(atomic_ids))
        or counts.get("atomic_total") != len(atomic_ids)
    ):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_theme_card_invalid",
            "recommendation atomic chain is invalid",
            502,
        )
    matched = card.get("matched_atomic_ids")
    if (
        not isinstance(matched, list)
        or not matched
        or any(item not in atomic_ids for item in matched)
        or len(matched) != len(set(matched))
    ):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_theme_card_invalid",
            "recommendation highlights are invalid",
            502,
        )
    _reject_forbidden_card_projection(card)
    return paper_id, theme_id


def _validate_search_response(
    value: Any, *, scope: str, data_snapshot_id: str
) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_search_invalid",
            "question search response is invalid",
            502,
        )
    authority = value.get("authority")
    integrity = value.get("integrity")
    items = value.get("items")
    if (
        value.get("scope") != scope
        or value.get("data_snapshot_id") != data_snapshot_id
        or not isinstance(authority, Mapping)
        or authority.get("candidate_only") is not True
        or authority.get("read_only") is not True
        or not isinstance(integrity, Mapping)
        or integrity.get("complete_theme_chain_returned") is not True
        or integrity.get("dependency_context_preserved") is not True
        or not isinstance(items, list)
        or len(items) > 50
    ):
        raise StudentRecommendationWorkbenchError(
            "student_recommendation_search_invalid",
            "question search did not preserve complete theme chains",
            502,
        )
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_theme_card_invalid",
                "question search theme card is invalid",
                502,
            )
        _card_identity(item, scope)
        result.append(deepcopy(dict(item)))
    return result


def _evidence_row(match: _Match, decision: _Decision) -> dict[str, Any]:
    loss = max(0.0, decision.maximum_score - decision.teacher_score)
    return {
        "match_id": match.match_id,
        "atomic_part_id": match.atomic_part_id,
        "teacher_score": decision.teacher_score,
        "maximum_score": decision.maximum_score,
        "loss_points": round(loss, 6),
    }


def _build_diagnoses(
    *,
    matches: Mapping[str, _Match],
    latest: Mapping[str, _Decision],
    sections: Mapping[str, _Section],
) -> list[dict[str, Any]]:
    evidence_by_section: dict[str, list[tuple[_Match, _Decision]]] = {}
    for match_id in sorted(latest):
        decision = latest[match_id]
        for section_key in decision.section_keys:
            evidence_by_section.setdefault(section_key, []).append(
                (matches[match_id], decision)
            )
    diagnoses: list[dict[str, Any]] = []
    for section_key in sorted(evidence_by_section):
        rows = evidence_by_section[section_key]
        supporting = [
            _evidence_row(match, decision)
            for match, decision in rows
            if decision.teacher_score < decision.maximum_score - _SCORE_EPSILON
        ]
        counter = [
            _evidence_row(match, decision)
            for match, decision in rows
            if decision.teacher_score >= decision.maximum_score - _SCORE_EPSILON
        ]
        unique_atomic = sorted({match.atomic_part_id for match, _ in rows})
        # A single uploaded visual submission is one source at most.  Even
        # three lost parts therefore cannot unlock a stable weakness.
        independent_source_count = 1 if rows else 0
        stable_gate = (
            len(unique_atomic) >= 3
            and independent_source_count >= 2
            and bool(supporting)
        )
        if stable_gate:
            status = "stable_weakness"
        elif supporting:
            status = "provisional_weakness"
        else:
            status = "no_weakness_evidence"
        section = sections[section_key]
        if status == "provisional_weakness":
            reason = (
                f"教师已确认“{section.display_label_zh}”存在失分证据；"
                "本次仅来自一份视觉作业，先作为暂定薄弱点。"
            )
        elif status == "stable_weakness":
            reason = f"教师已确认“{section.display_label_zh}”存在跨来源重复失分。"
        else:
            reason = f"“{section.display_label_zh}”当前只有满分反证，未形成薄弱证据。"
        diagnoses.append(
            {
                "section": section.public(),
                "diagnosis_status": status,
                "reason_zh": reason,
                "metrics": {
                    "valid_atomic_part_count": len(unique_atomic),
                    "independent_source_count": independent_source_count,
                    "supporting_evidence_count": len(supporting),
                    "counterevidence_count": len(counter),
                    "stable_min_atomic_parts": 3,
                    "stable_min_independent_sources": 2,
                    "stable_gate_satisfied": stable_gate,
                },
                "supporting_evidence": supporting,
                "counterevidence": counter,
            }
        )
    return diagnoses


def _merge_card_highlights(target: dict[str, Any], incoming: Mapping[str, Any]) -> None:
    merged_ids = sorted(
        set(target.get("matched_atomic_ids", []))
        | set(incoming.get("matched_atomic_ids", []))
    )
    details: dict[str, set[str]] = {}
    for card in (target, incoming):
        for detail in card.get("match_details", []):
            if not isinstance(detail, Mapping):
                continue
            atomic_id = detail.get("atomic_part_id")
            reasons = detail.get("reason_codes")
            if isinstance(atomic_id, str) and isinstance(reasons, list):
                details.setdefault(atomic_id, set()).update(
                    item for item in reasons if isinstance(item, str)
                )
    target["matched_atomic_ids"] = merged_ids
    target["match_details"] = [
        {"atomic_part_id": atomic_id, "reason_codes": sorted(details[atomic_id])}
        for atomic_id in sorted(details)
    ]
    if isinstance(target.get("counts"), dict):
        target["counts"]["atomic_matched"] = len(merged_ids)


def _basket_selection(
    card: Mapping[str, Any], *, section_labels: Sequence[str]
) -> dict[str, Any]:
    paper = card["paper"]
    theme = card["theme"]
    chain = card["atomic_chain"]
    sequence = theme.get("sequence")
    theme_title = (
        theme.get("title") if isinstance(theme.get("title"), str) else "主题待补"
    )
    paper_title = (
        paper.get("title")
        if isinstance(paper.get("title"), str)
        else card.get("display_title_zh", "来源卷待补")
    )
    shared = card["shared_context"]
    dependencies = card["dependencies"]
    return {
        "kind": "theme",
        "unit": "theme",
        "scope": card["scope"],
        "source_id": theme["id"],
        "paper_id": paper["id"],
        "paper_title": paper_title,
        "theme_id": theme["id"],
        "theme_title": theme_title,
        "title": (
            f"主题 {sequence} · {theme_title}" if type(sequence) is int else theme_title
        ),
        "order_label": (
            f"卷内主题顺序 {sequence}" if type(sequence) is int else "卷内顺序待补"
        ),
        "atomic_ids": [row["atomic_part_id"] for row in chain],
        "atomic_count": len(chain),
        "shared_material_count": (
            shared.get("material_count")
            if type(shared.get("material_count")) is int
            and shared.get("material_count") >= 0
            else 0
        ),
        "dependency_count": (
            dependencies.get("explicit_prior_edge_count")
            if type(dependencies.get("explicit_prior_edge_count")) is int
            and dependencies.get("explicit_prior_edge_count") >= 0
            else 0
        ),
        "tag_summary": "对应教材：" + "、".join(section_labels),
        "answer_summary": "参考答案属性见完整主题题链",
    }


class StudentRecommendationWorkbench:
    """Create a deterministic candidate preview without any long-term write."""

    def preview(
        self,
        payload: Mapping[str, Any],
        *,
        curriculum_catalog_loader: Callable[[], dict[str, Any]],
        question_search_loader: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != set(_PAYLOAD_KEYS):
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_request_invalid",
                "recommendation preview request is invalid",
            )
        submission_id = _require_safe_id(payload.get("submission_id"), "submission_id")
        data_snapshot_id = payload.get("data_snapshot_id")
        if (
            not isinstance(data_snapshot_id, str)
            or _HEX64.fullmatch(data_snapshot_id) is None
        ):
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_request_invalid",
                "data snapshot id is invalid",
            )
        limit = payload.get("limit_per_section")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_request_invalid",
                "limit_per_section must be an integer from 1 to 20",
            )
        scopes = _parse_scopes(payload.get("scopes"))
        try:
            catalog_value = curriculum_catalog_loader()
        except Exception as exc:
            raise StudentRecommendationWorkbenchError(
                "student_recommendation_catalog_unavailable",
                "curriculum catalog is unavailable",
                503,
            ) from exc
        sections, catalog_snapshot_id = _catalog_sections(catalog_value)
        match_rows = _parse_matches(payload.get("matches"))
        matches = {item.match_id: item for item in match_rows}
        decisions = _parse_decisions(
            payload.get("scoring_decisions"), matches=matches, sections=sections
        )
        latest = _latest_decisions(decisions)
        exclusions = _parse_exclusions(payload.get("exclusions"), match_rows)

        scoring_confirmation = (
            "teacher_confirmed" if set(latest) == set(matches) else "pending"
        )
        diagnoses = _build_diagnoses(matches=matches, latest=latest, sections=sections)
        accepted_sections = {
            diagnosis["section"]["section_key"] for diagnosis in diagnoses
        }
        # This workbench previews exactly one visual submission.  Its evidence
        # can support a useful provisional hypothesis, but it can never meet
        # the stable-diagnosis requirement of three distinct atomic parts from
        # at least two independent sources.
        evidence_sufficiency = "insufficient_evidence"
        if any(item["diagnosis_status"] == "stable_weakness" for item in diagnoses):
            diagnosis_status = "stable_weakness"
        elif any(
            item["diagnosis_status"] == "provisional_weakness" for item in diagnoses
        ):
            diagnosis_status = "provisional_weakness"
        else:
            diagnosis_status = "no_weakness_evidence"

        weak_diagnoses = [
            item
            for item in diagnoses
            if item["diagnosis_status"] in {"provisional_weakness", "stable_weakness"}
        ]
        cards_by_scope: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
            scope: {} for scope in scopes
        }
        card_sections: dict[tuple[str, str, str], set[str]] = {}
        excluded_theme_card_count = 0
        if (
            scoring_confirmation == "teacher_confirmed"
            and accepted_sections
            and weak_diagnoses
        ):
            for diagnosis in weak_diagnoses:
                section = diagnosis["section"]
                section_key = section["section_key"]
                for scope in scopes:
                    search_payload = {
                        "scope": scope,
                        "filters": {},
                        "curriculum": {
                            "volume_id": section["volume_id"],
                            "chapter_id": section["chapter_id"],
                            "section": section_key,
                        },
                        "limit": 50,
                    }
                    try:
                        search_value = question_search_loader(search_payload)
                    except StudentRecommendationWorkbenchError:
                        raise
                    except Exception as exc:
                        raise StudentRecommendationWorkbenchError(
                            "student_recommendation_search_unavailable",
                            "question search is unavailable",
                            503,
                        ) from exc
                    cards = _validate_search_response(
                        search_value,
                        scope=scope,
                        data_snapshot_id=data_snapshot_id,
                    )
                    for card in cards:
                        paper_id, theme_id = _card_identity(card, scope)
                        atomic_ids = {
                            row["atomic_part_id"] for row in card["atomic_chain"]
                        }
                        if (
                            paper_id in exclusions["paper"]
                            or theme_id in exclusions["theme"]
                            or atomic_ids & exclusions["atomic"]
                        ):
                            excluded_theme_card_count += 1
                            continue
                        identity = (paper_id, theme_id)
                        existing = cards_by_scope[scope].get(identity)
                        if existing is None:
                            cards_by_scope[scope][identity] = card
                        else:
                            _merge_card_highlights(existing, card)
                        card_sections.setdefault(
                            (scope, paper_id, theme_id), set()
                        ).add(section_key)

        scope_groups: list[dict[str, Any]] = []
        returned_count = 0
        for scope in scopes:
            items: list[dict[str, Any]] = []
            ordered = sorted(
                cards_by_scope[scope].items(),
                key=lambda pair: (
                    pair[0][0],
                    pair[1]["theme"].get("sequence")
                    if type(pair[1]["theme"].get("sequence")) is int
                    else 10_000,
                    pair[0][1],
                ),
            )
            # The cap is applied after exclusion and de-duplication.
            per_section_counts: dict[str, int] = {}
            for (paper_id, theme_id), card in ordered:
                relevant = sorted(card_sections[(scope, paper_id, theme_id)])
                if not any(per_section_counts.get(key, 0) < limit for key in relevant):
                    continue
                for key in relevant:
                    if per_section_counts.get(key, 0) < limit:
                        per_section_counts[key] = per_section_counts.get(key, 0) + 1
                labels = [sections[key].display_label_zh for key in relevant]
                recommendation = deepcopy(card)
                recommendation["diagnosis_section_keys"] = relevant
                recommendation["recommendation_reason_zh"] = (
                    "教师已确认“"
                    + "、".join(labels)
                    + "”存在失分证据；推荐整道主题大题，保留共同材料与前序依赖。"
                )
                recommendation["basket_selection"] = _basket_selection(
                    recommendation, section_labels=labels
                )
                items.append(recommendation)
            returned_count += len(items)
            scope_groups.append({"scope": scope, "items": items})

        blockers: list[dict[str, str]] = []
        missing_matches = sorted(set(matches) - set(latest))
        if missing_matches:
            blockers.append(
                {
                    "code": "teacher_scoring_pending",
                    "message_zh": (
                        f"还有 {len(missing_matches)} 个作答单元未完成教师评分确认。"
                    ),
                }
            )
        if not accepted_sections:
            blockers.append(
                {
                    "code": "teacher_curriculum_confirmation_missing",
                    "message_zh": "尚无教师接受或修改后的教材章节，不能按章节诊断。",
                }
            )
        if weak_diagnoses and all(
            item["metrics"]["independent_source_count"] < 2 for item in weak_diagnoses
        ):
            blockers.append(
                {
                    "code": "stable_weakness_requires_independent_sources",
                    "message_zh": "本次视觉作业至多算一个独立来源，不能标为稳定薄弱点。",
                }
            )
        if weak_diagnoses and returned_count == 0:
            blockers.append(
                {
                    "code": "complete_theme_recommendation_unavailable",
                    "message_zh": "当前章节暂无可返回且未与本次作业重复的完整主题题链。",
                }
            )

        supporting_count = sum(
            item["metrics"]["supporting_evidence_count"] for item in diagnoses
        )
        counter_count = sum(
            item["metrics"]["counterevidence_count"] for item in diagnoses
        )
        response = {
            "contract_version": CONTRACT_VERSION,
            "submission_id": submission_id,
            "data_snapshot_id": data_snapshot_id,
            "claim_scope": CLAIM_SCOPE,
            "candidate_only": True,
            "read_only": True,
            "long_term_update_allowed": False,
            "scoring_confirmation": scoring_confirmation,
            "evidence_sufficiency": evidence_sufficiency,
            "diagnosis_status": diagnosis_status,
            "metrics": {
                "match_count": len(matches),
                "decision_history_count": len(decisions),
                "latest_teacher_decision_count": len(latest),
                "teacher_confirmed_match_count": len(latest),
                "confirmed_curriculum_section_count": len(accepted_sections),
                "valid_atomic_part_count": len(
                    {matches[key].atomic_part_id for key in latest}
                ),
                "independent_source_count": 1 if latest else 0,
                "supporting_evidence_count": supporting_count,
                "counterevidence_count": counter_count,
                "stable_min_atomic_parts": 3,
                "stable_min_independent_sources": 2,
                "stable_weakness_allowed": False,
                "returned_theme_card_count": returned_count,
                "excluded_theme_card_count": excluded_theme_card_count,
            },
            "diagnoses": diagnoses,
            "scope_groups": scope_groups,
            "blockers": blockers,
        }
        # These checks protect future refactors from silently widening claims.
        assert response["scoring_confirmation"] in SCORING_CONFIRMATIONS
        assert response["evidence_sufficiency"] in EVIDENCE_SUFFICIENCY
        assert response["diagnosis_status"] in DIAGNOSIS_STATUSES
        assert response["candidate_only"] is True
        assert response["read_only"] is True
        assert response["long_term_update_allowed"] is False
        assert catalog_snapshot_id
        return response


__all__ = [
    "CLAIM_SCOPE",
    "CONTRACT_VERSION",
    "DIAGNOSIS_STATUSES",
    "EVIDENCE_SUFFICIENCY",
    "SCOPE_ORDER",
    "SCORING_CONFIRMATIONS",
    "StudentRecommendationWorkbench",
    "StudentRecommendationWorkbenchError",
]
