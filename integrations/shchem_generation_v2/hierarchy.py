"""Strict four-level hierarchy helpers shared by generation and publication."""

from __future__ import annotations

import re
from typing import Any


class HierarchyContractError(ValueError):
    pass


def hierarchy_ids(paper: dict[str, Any]) -> dict[str, list[str]]:
    paper_id = paper.get("paper_id")
    themes: list[str] = []
    printed: list[str] = []
    atomic: list[str] = []
    for theme in paper.get("themes", []):
        theme_id = theme.get("theme_id")
        if not isinstance(theme_id, str) or not re.fullmatch(r"T[0-9]+", theme_id):
            raise HierarchyContractError(f"invalid theme ID: {theme_id}")
        if theme_id in themes:
            raise HierarchyContractError(f"duplicate theme ID: {theme_id}")
        if theme.get("parent_paper_id") != paper_id:
            raise HierarchyContractError(f"theme parent mismatch: {theme_id}")
        themes.append(theme_id)
        for question in theme.get("printed_questions", []):
            question_id = question.get("printed_question_id")
            if not isinstance(question_id, str) or not re.fullmatch(r"Q[0-9]+", question_id):
                raise HierarchyContractError(f"invalid printed question ID: {question_id}")
            if question_id in printed:
                raise HierarchyContractError(f"duplicate printed question ID: {question_id}")
            if question.get("parent_theme_id") != theme_id:
                raise HierarchyContractError(f"printed parent mismatch: {question_id}")
            printed.append(question_id)
            parts = question.get("atomic_parts", [])
            if not parts:
                raise HierarchyContractError(f"printed question has no atomic children: {question_id}")
            for part in parts:
                part_id = part.get("part_id")
                if not isinstance(part_id, str) or not re.fullmatch(r"P[0-9]+", part_id):
                    raise HierarchyContractError(f"invalid atomic part ID: {part_id}")
                if part_id in atomic:
                    raise HierarchyContractError(f"duplicate atomic part ID: {part_id}")
                if part.get("parent_printed_question_id") != question_id:
                    raise HierarchyContractError(f"atomic parent mismatch: {part_id}")
                atomic.append(part_id)
    if not themes or not printed or not atomic:
        raise HierarchyContractError("paper hierarchy is incomplete")
    if len(atomic) <= len(printed):
        raise HierarchyContractError("atomic parts must be materially more numerous than printed questions")
    declared = paper.get("task_card", {}).get("hierarchy_counts", {})
    if declared:
        if declared.get("printed_question_count") != len(printed):
            raise HierarchyContractError("declared printed count differs from hierarchy")
        if declared.get("atomic_part_count") != len(atomic):
            raise HierarchyContractError("declared atomic count differs from hierarchy")
    return {"theme_ids": themes, "printed_question_ids": printed, "atomic_part_ids": atomic}


def validate_atomic_registry_binding(
    expected_atomic_ids: list[str], binding: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    rows = binding.get("atomic", [])
    if not isinstance(rows, list):
        return ["atomic binding is not an array"]
    actual_ids = [row.get("artifact_id") for row in rows if isinstance(row, dict)]
    if len(actual_ids) != len(rows):
        errors.append("atomic binding contains a non-object row")
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("atomic binding contains duplicate artifact IDs")
    if any(not isinstance(value, str) or not re.fullmatch(r"P[0-9]+", value) for value in actual_ids):
        errors.append("atomic binding contains a printed or malformed artifact ID")
    if actual_ids != expected_atomic_ids:
        errors.append("atomic binding IDs are not exact ordered hierarchy membership")
    if binding.get("atomic_count") != len(expected_atomic_ids):
        errors.append("atomic_count differs from hierarchy")
    return errors


def validate_paper_plus_atomic_chain_batch(
    paper_id: str, expected_atomic_ids: list[str], batch: dict[str, Any]
) -> list[str]:
    """Require one paper chain plus every frozen atomic child, exactly once."""

    errors: list[str] = []
    rows = batch.get("chains", [])
    if not isinstance(rows, list):
        return ["chain batch is not an array"]
    actual_ids = [row.get("expected_artifact_id") for row in rows if isinstance(row, dict)]
    if len(actual_ids) != len(rows):
        errors.append("chain batch contains a non-object row")
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("chain batch contains duplicate artifact IDs")
    expected_ids = [paper_id, *expected_atomic_ids]
    if actual_ids != expected_ids:
        errors.append("chain batch IDs are not exact ordered paper-plus-atomic membership")
    child_ids = actual_ids[1:] if actual_ids and actual_ids[0] == paper_id else actual_ids
    if any(
        not isinstance(value, str) or not re.fullmatch(r"P[0-9]+", value)
        for value in child_ids
    ):
        errors.append("chain batch child set contains a printed or malformed artifact ID")
    if batch.get("expected_chain_count") != len(expected_ids):
        errors.append("expected_chain_count differs from paper-plus-atomic hierarchy")
    return errors
