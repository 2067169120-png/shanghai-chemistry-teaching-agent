"""Explicit, non-secret image egress policy for teacher preparation."""

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


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


def validate_preparation_image_dimensions(
    policy: Mapping[str, Any], assets: list[dict[str, Any]]
) -> None:
    """Known vendor limits, checked without credentials, resizing or any egress.

    https://api-docs.deepseek.com/guides/vision/ (checked 2026-09-13)
    Unknown endpoints keep their configured capabilities and common byte guards;
    they are not silently assigned DeepSeek's model or dimension restrictions.
    """
    if not assets:
        return
    try:
        parsed = urlsplit(policy.get("base_url", ""))
        official = (
            parsed.scheme == "https"
            and parsed.hostname == "api.deepseek.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except (ValueError, TypeError, AttributeError):
        official = False
    if not official:
        return
    model = policy.get("model_id")
    if model in ("deepseek-v4-flash", "deepseek-v4-pro"):
        raise PreparationImageInputError(
            "vision_capability_unconfirmed",
            "该DeepSeek模型仅处理文字；请改选视觉模型或明确选择仅用于课件排版，本次未发送图片。",
        )
    if model != "deepseek-v4-flash-vision-exp":
        return
    side_limit = 4096 if len(assets) >= 15 else 8192
    for index, asset in enumerate(assets, 1):
        if max(asset["width"], asset["height"]) > side_limit:
            raise PreparationImageInputError(
                "preparation_image_dimensions_unsupported",
                f"本次{len(assets)}张图片中，第{index}张为{asset['width']}×{asset['height']}像素；"
                f"该模型要求每边不超过{side_limit}像素。请另存适合读取的版本并预览，"
                "本次未发送，未自动缩图或拆成多次调用。",
            )
