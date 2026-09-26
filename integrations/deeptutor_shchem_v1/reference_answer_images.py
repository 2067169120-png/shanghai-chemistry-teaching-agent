"""Explicit presentation decisions for source-bound teacher answer images.

This is a display policy, not a chemistry approval or a student-image route.
Original source records and their answer authority are never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

ANSWER_IMAGE_REVISION_ID = "sheast-source-answer-images-20260910-r1"
ANSWER_PAGE_SHA256 = "e2cc65327838363ba7e0ffa4cb984189b8274371583dff29540b0b900c9682e0"
SUPPORTED_NODES = frozenset(
    [f"SHEAST2025-M05-B-T4-Q{n}-P1" for n in range(1, 7)]
    + [f"SHEAST2025-M05-B-T5-Q{n}-P1" for n in range(1, 6)]
)
# Decisions made after inspecting the complete source question and answer page.
# Q2's structure is already printed in its question; its answer image remains
# available for inspection without duplicating it in the teacher document.
INLINE_ANSWERS = {
    "SHEAST2025-M05-B-T5-Q3-P1": {
        "crop_id": "SHEAST2025-CROP-30cff29cc20d65fe4df185b0",
        "sha256": "e175aaf55e63ec4e5298f2bcf65a652ec19894ccb1842a8f0638a5686fe4ad85",
        "caption_zh": "非官方参考答案图 · E到F的反应方程式",
        "reason_zh": "保留反应物和产物的取代位置、键线及反应条件，文字摘要不能替代。",
    },
    "SHEAST2025-M05-B-T5-Q5-P1": {
        "crop_id": "SHEAST2025-CROP-d17368fa1d2cb584cf6b1cd0",
        "sha256": "f0efd0be4a5bad9cdcc4bbd90d1b97f74047b6448fe4321514c3b924e3cb853e",
        "caption_zh": "非官方参考答案图 · 有机合成路线",
        "reason_zh": "保留中间体、目标结构及各步箭头条件，文字摘要不能替代完整路线。",
    },
}


def project_answer_image(
    node_id: str, descriptor: Mapping[str, Any], source_sha256: str
) -> dict[str, Any]:
    """Project only a reader-verified, explicitly aligned source answer crop."""
    if (
        node_id not in SUPPORTED_NODES
        or source_sha256 != ANSWER_PAGE_SHA256
        or descriptor.get("evidence_role") != "answer"
    ):
        raise ValueError("reference_answer_image_source_mismatch")
    policy = INLINE_ANSWERS.get(node_id)
    if policy and any(
        descriptor.get(key) != policy[key] for key in ("crop_id", "sha256")
    ):
        raise ValueError("reference_answer_image_policy_mismatch")
    return {
        **descriptor,
        "source_sha256": source_sha256,
        "content_type": "image/png",
        "access": "teacher_reference_answer_only",
        "display_mode": "inline_required" if policy else "preview_only",
        "presentation_revision_id": ANSWER_IMAGE_REVISION_ID,
        "caption_zh": policy["caption_zh"] if policy else "非官方参考答案原图",
        "reason_zh": policy["reason_zh"]
        if policy
        else "可独立查看对应原答案；不在正文重复贴图。",
    }


def answer_presentation_fingerprint(catalog: Mapping[str, Any]) -> str | None:
    """Invalidate relevant saved previews when the explicit display policy changes."""
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            if value in SUPPORTED_NODES:
                found.add(value)
        elif isinstance(value, Mapping):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    visit(catalog)
    if not found:
        return None
    raw = json.dumps(
        {
            "revision": ANSWER_IMAGE_REVISION_ID,
            "source_sha256": ANSWER_PAGE_SHA256,
            "nodes": sorted(found),
            "inline": {
                node: INLINE_ANSWERS[node]
                for node in sorted(found)
                if node in INLINE_ANSWERS
            },
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
