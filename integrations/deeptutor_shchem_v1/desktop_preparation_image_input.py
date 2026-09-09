"""Explicit, non-secret image egress policy for teacher preparation."""

from collections.abc import Mapping
from typing import Any


class PreparationImageInputError(ValueError):
    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.retryable = False


def image_input_mode(payload: Mapping[str, Any]) -> str:
    """Missing fields retain the historical, metadata-only contract."""
    mode = payload.get("image_input_mode", "local_only")
    if not isinstance(mode, str) or mode not in {"local_only", "vision"}:
        raise PreparationImageInputError(
            "preparation_image_mode_invalid", "图片用法不正确，请重新选择。"
        )
    return mode


def require_preparation_vision_policy(policy: Mapping[str, Any]) -> None:
    """A text probe is not proof of vision. Never upgrade it implicitly."""
    evidence = policy.get("capability_evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}

    def values(value: Any) -> set[str]:
        if not isinstance(value, (list, tuple)):
            return set()
        return {item for item in value if isinstance(item, str)}

    declared = values(evidence.get("declared")) | values(evidence.get("catalog"))
    effective = values(policy.get("effective_capabilities"))
    if "vision" not in declared or "vision" not in effective:
        raise PreparationImageInputError(
            "vision_capability_unconfirmed",
            "所选模型未声明支持读图。请在模型设置确认视觉能力、改选多模态模型，"
            "或主动切换为“仅用于课件排版”；连接测试成功不代表已验证读图能力。",
        )
    if "structured_output" not in effective:
        raise PreparationImageInputError(
            "structured_output_capability_required", "所选模型未配置结构化输出能力。"
        )
    if "source_page_image" not in values(
        policy.get("allowed_data_classes")
    ) or policy.get("image_egress") not in {
        "teacher_confirmed_source_pages",
        "teacher_confirmed_visual_pages",
    }:
        raise PreparationImageInputError(
            "image_egress_not_allowed", "所选模型配置未允许发送所选备课图片。"
        )
