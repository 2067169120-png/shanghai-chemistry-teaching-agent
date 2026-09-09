from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .model_provider_probe import (
    FIXED_SYNTHETIC_PROMPT,
    FIXED_SYNTHETIC_PROMPT_BYTES,
    FIXED_SYNTHETIC_PROMPT_SHA256,
)
from .model_provider_settings import (
    DEFAULT_PROVIDER_POLICIES,
    ModelProviderSettingsError,
    ProviderPolicy,
    _normalize_custom_base_url,
)

MODEL_API_SCHEMA_VERSION = "shchem.one-time-model-api.v1"
MODEL_CANDIDATE_SCHEMA_VERSION = "shchem.model-candidate-output.v1"
CONFIG_KIND = "one_time_api_configuration"
REQUEST_PREVIEW_KIND = "prepared_provider_request_preview"
USAGE_HINT_KIND = "usage_cost_hint"

STUDENT_ANALYSIS = "student_analysis"
QUESTION_CLASSIFICATION = "question_classification"
THEME_QUESTION_GENERATION = "theme_question_generation"
VISUAL_UNDERSTANDING = "visual_understanding"
CONNECTION_TEST = "connection_test"

TASK_ROUTES = (
    STUDENT_ANALYSIS,
    QUESTION_CLASSIFICATION,
    THEME_QUESTION_GENERATION,
    VISUAL_UNDERSTANDING,
)

_ROUTE_LABELS_ZH = MappingProxyType(
    {
        STUDENT_ANALYSIS: "学生分析模型",
        QUESTION_CLASSIFICATION: "题目分类模型",
        THEME_QUESTION_GENERATION: "主题式命题模型",
        VISUAL_UNDERSTANDING: "视觉理解模型",
    }
)
_ROUTE_REQUIRED_CAPABILITIES = MappingProxyType(
    {
        STUDENT_ANALYSIS: ("text", "structured_output"),
        QUESTION_CLASSIFICATION: ("text", "structured_output"),
        THEME_QUESTION_GENERATION: ("text", "structured_output"),
        VISUAL_UNDERSTANDING: ("text", "vision", "structured_output"),
    }
)
_ROUTE_REQUIRED_INPUT_KEYS = MappingProxyType(
    {
        STUDENT_ANALYSIS: frozenset({"student_work", "evidence"}),
        QUESTION_CLASSIFICATION: frozenset({"atomic_part", "theme_context"}),
        THEME_QUESTION_GENERATION: frozenset(
            {"task_card", "evidence_pack", "theme_blueprint"}
        ),
        VISUAL_UNDERSTANDING: frozenset({"image_refs", "theme_context"}),
    }
)

_CREDENTIAL_REF = re.compile(
    r"^ShanghaiChemWorkbench/model-provider/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)
_REVISION = re.compile(r"^rev_[0-9a-f]{32}$")
_REQUEST_ID = re.compile(r"^(?:probe|request)_[0-9a-f]{32}$")
_RECEIPT_ID = re.compile(r"^probe-receipt-[0-9a-f]{32}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,239}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,239}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?Z$"
)
_APPROVED_IMAGE_ASSET_REF = re.compile(r"^asset_[0-9a-f]{64}$")
_ALLOWED_DATA_CLASSES = frozenset(
    {
        "synthetic_only",
        "question_text_redacted",
        "question_image_redacted",
        "source_page_image",
        "student_answer_image",
        "deidentified_student_text",
    }
)
_ROUTE_ALLOWED_DATA_CLASSES = MappingProxyType(
    {
        STUDENT_ANALYSIS: frozenset(
            {"deidentified_student_text", "question_text_redacted"}
        ),
        QUESTION_CLASSIFICATION: frozenset({"question_text_redacted"}),
        THEME_QUESTION_GENERATION: frozenset({"question_text_redacted"}),
        VISUAL_UNDERSTANDING: frozenset(
            {"question_text_redacted", "question_image_redacted"}
        ),
    }
)

_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "credential_value",
    }
)
_FORBIDDEN_STUDENT_KEYS = frozenset(
    {
        "student_name",
        "student_number",
        "student_no",
        "real_name",
        "phone",
        "email",
        "id_card",
        "address",
        "face_image",
        "school_name",
        "class_name",
        "local_path",
        "姓名",
        "学号",
        "学校",
        "班级",
        "手机号",
        "电话",
        "邮箱",
        "身份证",
        "住址",
        "人脸",
        "本地路径",
    }
)
_FORBIDDEN_CANDIDATE_MUTATION_KEYS = frozenset(
    {
        "source_authority",
        "answer_authority",
        "official",
        "official_status",
        "human_reviewed",
        "verified",
        "teaching_use_allowed",
        "publication_allowed",
        "usage_eligibility",
        "central_registry_write",
        "catalog_mutation",
        "source_provenance_replacement",
    }
)


class OneTimeModelApiError(ValueError):
    """Sanitized settings/adapter error suitable for a Chinese teacher UI."""

    def __init__(self, code: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.status = status

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedProviderRequest:
    """A request candidate with a credential reference, never a secret value."""

    request_id: str
    request_kind: str
    task_route: str
    provider_id: str
    base_url_policy: str
    model_id: str
    method: str
    url: str
    headers_without_authorization: Mapping[str, str]
    body: bytes
    body_sha256: str
    credential_ref: str
    auth_scheme: str
    data_classes: tuple[str, ...]
    output_schema_id: str
    requires_teacher_confirmation: bool
    execution_allowed: bool

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(request_id={self.request_id!r}, "
            f"request_kind={self.request_kind!r}, task_route={self.task_route!r}, "
            f"provider_id={self.provider_id!r}, model_id={self.model_id!r}, "
            f"url={self.url!r}, request_bytes={len(self.body)!r}, "
            "credential_reference_present=True, authorization_value_present=False)"
        )

    def public_preview(self) -> dict[str, Any]:
        """Return a body-free, log-safe preview for a confirmation dialog."""

        return {
            "schema_version": MODEL_API_SCHEMA_VERSION,
            "contract_kind": REQUEST_PREVIEW_KIND,
            "request_id": self.request_id,
            "request_kind": self.request_kind,
            "task_route": self.task_route,
            "task_route_label_zh": (
                "连接测试" if self.task_route == CONNECTION_TEST else _ROUTE_LABELS_ZH[self.task_route]
            ),
            "provider_id": self.provider_id,
            "base_url_policy": self.base_url_policy,
            "model_id": self.model_id,
            "method": self.method,
            "resolved_endpoint": self.url,
            "request_body_sha256": self.body_sha256,
            "request_body_bytes": len(self.body),
            "credential_ref": self.credential_ref,
            "authorization_value_exposed": False,
            "request_body_exposed": False,
            "data_classes": list(self.data_classes),
            "output_schema_id": self.output_schema_id,
            "requires_teacher_confirmation": self.requires_teacher_confirmation,
            "execution_allowed": self.execution_allowed,
            "candidate_only": self.task_route != CONNECTION_TEST,
            "offline_workbench_available": True,
        }


class ProviderAdapter(Protocol):
    provider_id: str
    base_url_policy: str

    def build_request(
        self,
        *,
        request_id: str,
        request_kind: str,
        task_route: str,
        model_id: str,
        credential_ref: str,
        prompt: str,
        output_schema: Mapping[str, Any],
        output_schema_id: str,
        data_classes: Sequence[str],
        max_output_tokens: int,
        requires_teacher_confirmation: bool,
        execution_allowed: bool,
    ) -> PreparedProviderRequest: ...


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _require_mapping(value: Any, code: str, message_zh: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OneTimeModelApiError(code, message_zh)
    return value


def _walk_forbidden_keys(value: Any, forbidden: frozenset[str]) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if normalized in forbidden:
                return normalized
            found = _walk_forbidden_keys(nested, forbidden)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _walk_forbidden_keys(nested, forbidden)
            if found is not None:
                return found
    return None


def _validate_no_secret_values(value: Any) -> None:
    found = _walk_forbidden_keys(value, _FORBIDDEN_SECRET_KEYS)
    if found is not None:
        raise OneTimeModelApiError(
            "secret_field_forbidden",
            "设置、请求候选和模型输出中不能出现 API Key、Authorization 或其他密钥值。",
        )
    for text_value in _walk_string_values(value):
        if (
            re.search(r"(?i)authorization\s*:\s*bearer(?:\s|$)", text_value)
            or re.search(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}", text_value)
            or re.search(r"(?i)\bsk-[A-Za-z0-9_-]{4,}", text_value)
            or re.search(r"(?i)\bapi[_ -]?key\s*[:=]\s*\S+", text_value)
        ):
            raise OneTimeModelApiError(
                "secret_value_forbidden",
                "设置、请求候选和模型输出中不能出现 API Key、Authorization 或其他密钥值。",
            )


def _walk_string_values(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, str):
        result.append(value)
    elif isinstance(value, Mapping):
        for nested in value.values():
            result.extend(_walk_string_values(nested))
    elif isinstance(value, list):
        for nested in value:
            result.extend(_walk_string_values(nested))
    return result


def _model_policy(provider: ProviderPolicy, model_id: str) -> tuple[str, ...]:
    for model in provider.models:
        if model.model_id == model_id:
            return tuple(model.capabilities)
    raise OneTimeModelApiError("model_not_allowed", "所选模型不在当前服务商允许列表中。")


def _safe_model_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not _MODEL_ID.fullmatch(value)
        or ".." in value
        or "//" in value
        or value.endswith(("/", ":"))
    ):
        raise OneTimeModelApiError("model_id_invalid", "模型标识不正确。")
    return value


def _catalog_capabilities(
    provider: ProviderPolicy | None, model_id: str
) -> tuple[str, ...] | None:
    if provider is None:
        return None
    for model in provider.models:
        if model.model_id == model_id:
            return tuple(model.capabilities)
    return None


def _declared_capabilities(value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or len(value) != len(set(value))
        or any(
            not isinstance(item, str)
            or item not in {"text", "vision", "structured_output"}
            for item in value
        )
        or "text" not in value
    ):
        raise OneTimeModelApiError("profile_invalid", "模型能力声明不正确。")
    return tuple(value)


def _provider_descriptor(
    profile: Mapping[str, Any],
    provider_policies: Mapping[str, ProviderPolicy],
) -> dict[str, Any]:
    provider_kind = profile.get("provider_kind", "preset")
    provider_id = profile.get("provider_id")
    if provider_kind == "preset":
        provider = (
            provider_policies.get(provider_id) if isinstance(provider_id, str) else None
        )
        if provider is None:
            raise OneTimeModelApiError(
                "provider_not_allowed", "所选服务商不在允许列表中。"
            )
        if profile.get("base_url_policy") != provider.base_url_policy or profile.get(
            "base_url", provider.base_url
        ) != provider.base_url:
            raise OneTimeModelApiError(
                "base_url_policy_mismatch", "服务地址策略与服务商不匹配。"
            )
        if profile.get("api_style", provider.api_style) != provider.api_style:
            raise OneTimeModelApiError(
                "api_style_mismatch", "API 调用方式与内置服务商不匹配。"
            )
        return {
            "provider_kind": "preset",
            "provider_id": provider.provider_id,
            "display_name": profile.get("display_name")
            or provider.display_name
            or provider.provider_id,
            "base_url_policy": provider.base_url_policy,
            "base_url": provider.base_url,
            "api_style": provider.api_style,
            "local_endpoint_policy": "deny",
            "provider_policy": provider,
        }
    if provider_kind != "openai_compatible" or provider_id not in {
        None,
        "openai_compatible",
    }:
        raise OneTimeModelApiError(
            "provider_not_allowed", "自定义服务商必须使用 OpenAI-compatible 合同。"
        )
    display_name = profile.get("display_name")
    api_style = profile.get("api_style")
    local_policy = profile.get("local_endpoint_policy", "deny")
    if (
        not isinstance(display_name, str)
        or not display_name.strip()
        or len(display_name.strip()) > 80
        or api_style not in {"responses", "chat_completions"}
    ):
        raise OneTimeModelApiError("profile_invalid", "自定义服务商设置不完整。")
    try:
        base_url, endpoint_scope = _normalize_custom_base_url(
            profile.get("base_url"), local_endpoint_policy=local_policy
        )
    except ModelProviderSettingsError as exc:
        raise OneTimeModelApiError(exc.code, "自定义服务地址不正确。") from None
    expected_policy = (
        "openai_compatible_loopback_v1"
        if endpoint_scope == "loopback"
        else "openai_compatible_public_https_v1"
    )
    if profile.get("base_url_policy") not in {None, expected_policy}:
        raise OneTimeModelApiError(
            "base_url_policy_mismatch", "自定义服务地址策略不匹配。"
        )
    return {
        "provider_kind": "openai_compatible",
        "provider_id": "openai_compatible",
        "display_name": display_name.strip(),
        "base_url_policy": expected_policy,
        "base_url": base_url,
        "api_style": api_style,
        "local_endpoint_policy": local_policy,
        "provider_policy": None,
    }


def _strict_json_loads(raw: str | bytes) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite number")

    text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
    return json.loads(text, object_pairs_hook=reject_duplicate, parse_constant=reject_constant)


def _prepared_request(
    *,
    request_id: str,
    request_kind: str,
    task_route: str,
    provider_id: str,
    base_url_policy: str,
    model_id: str,
    url: str,
    headers: Mapping[str, str],
    body_value: Mapping[str, Any],
    credential_ref: str,
    data_classes: Sequence[str],
    output_schema_id: str,
    requires_teacher_confirmation: bool,
    execution_allowed: bool,
) -> PreparedProviderRequest:
    body = _canonical_json_bytes(body_value)
    return PreparedProviderRequest(
        request_id=request_id,
        request_kind=request_kind,
        task_route=task_route,
        provider_id=provider_id,
        base_url_policy=base_url_policy,
        model_id=model_id,
        method="POST",
        url=url,
        headers_without_authorization=MappingProxyType(dict(headers)),
        body=body,
        body_sha256=_sha256_bytes(body),
        credential_ref=credential_ref,
        auth_scheme="bearer_from_system_credential_ref",
        data_classes=tuple(data_classes),
        output_schema_id=output_schema_id,
        requires_teacher_confirmation=requires_teacher_confirmation,
        execution_allowed=execution_allowed,
    )


class OpenAIResponsesAdapter:
    provider_id = "openai"
    base_url_policy = "openai_official_https_v1"
    resolved_endpoint = "https://api.openai.com/v1/responses"

    def build_request(
        self,
        *,
        request_id: str,
        request_kind: str,
        task_route: str,
        model_id: str,
        credential_ref: str,
        prompt: str,
        output_schema: Mapping[str, Any],
        output_schema_id: str,
        data_classes: Sequence[str],
        max_output_tokens: int,
        requires_teacher_confirmation: bool,
        execution_allowed: bool,
    ) -> PreparedProviderRequest:
        schema_name = re.sub(r"[^A-Za-z0-9_-]", "_", output_schema_id)[:64]
        body = {
            "background": False,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "max_output_tokens": max_output_tokens,
            "model": model_id,
            "store": False,
            "stream": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": deepcopy(dict(output_schema)),
                }
            },
            "tool_choice": "none",
            "tools": [],
        }
        return _prepared_request(
            request_id=request_id,
            request_kind=request_kind,
            task_route=task_route,
            provider_id=self.provider_id,
            base_url_policy=self.base_url_policy,
            model_id=model_id,
            url=self.resolved_endpoint,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "Idempotency-Key": request_id,
            },
            body_value=body,
            credential_ref=credential_ref,
            data_classes=data_classes,
            output_schema_id=output_schema_id,
            requires_teacher_confirmation=requires_teacher_confirmation,
            execution_allowed=execution_allowed,
        )


class DeepSeekChatAdapter:
    provider_id = "deepseek"
    base_url_policy = "deepseek_official_https_v1"
    resolved_endpoint = "https://api.deepseek.com/chat/completions"

    def build_request(
        self,
        *,
        request_id: str,
        request_kind: str,
        task_route: str,
        model_id: str,
        credential_ref: str,
        prompt: str,
        output_schema: Mapping[str, Any],
        output_schema_id: str,
        data_classes: Sequence[str],
        max_output_tokens: int,
        requires_teacher_confirmation: bool,
        execution_allowed: bool,
    ) -> PreparedProviderRequest:
        schema_text = json.dumps(
            output_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        body = {
            "max_tokens": max_output_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "只返回一个 JSON 对象。必须符合用户消息中的 JSON Schema；"
                        "不得输出 Markdown、解释或额外字段。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"{prompt}\n\n必须符合以下 JSON Schema：\n{schema_text}",
                },
            ],
            "model": model_id,
            "response_format": {"type": "json_object"},
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        return _prepared_request(
            request_id=request_id,
            request_kind=request_kind,
            task_route=task_route,
            provider_id=self.provider_id,
            base_url_policy=self.base_url_policy,
            model_id=model_id,
            url=self.resolved_endpoint,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "Idempotency-Key": request_id,
            },
            body_value=body,
            credential_ref=credential_ref,
            data_classes=data_classes,
            output_schema_id=output_schema_id,
            requires_teacher_confirmation=requires_teacher_confirmation,
            execution_allowed=execution_allowed,
        )


class OpenAICompatibleAdapter:
    """Adapter bound to one server-validated OpenAI-compatible profile URL."""

    def __init__(
        self,
        *,
        base_url: str,
        base_url_policy: str,
        api_style: str,
    ) -> None:
        self.provider_id = "openai_compatible"
        self.base_url_policy = base_url_policy
        self.api_style = api_style
        self.resolved_endpoint = base_url.rstrip("/") + (
            "/responses" if api_style == "responses" else "/chat/completions"
        )

    def build_request(
        self,
        *,
        request_id: str,
        request_kind: str,
        task_route: str,
        model_id: str,
        credential_ref: str,
        prompt: str,
        output_schema: Mapping[str, Any],
        output_schema_id: str,
        data_classes: Sequence[str],
        max_output_tokens: int,
        requires_teacher_confirmation: bool,
        execution_allowed: bool,
    ) -> PreparedProviderRequest:
        if self.api_style == "responses":
            schema_name = re.sub(r"[^A-Za-z0-9_-]", "_", output_schema_id)[:64]
            body = {
                "background": False,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
                "max_output_tokens": max_output_tokens,
                "model": model_id,
                "store": False,
                "stream": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": deepcopy(dict(output_schema)),
                    }
                },
                "tool_choice": "none",
                "tools": [],
            }
        else:
            schema_text = json.dumps(
                output_schema,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            body = {
                "max_tokens": max_output_tokens,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "只返回一个 JSON 对象。必须符合用户消息中的 JSON Schema；"
                            "不得输出 Markdown、解释或额外字段。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"{prompt}\n\n必须符合以下 JSON Schema：\n{schema_text}",
                    },
                ],
                "model": model_id,
                "response_format": {"type": "json_object"},
                "stream": False,
            }
        return _prepared_request(
            request_id=request_id,
            request_kind=request_kind,
            task_route=task_route,
            provider_id=self.provider_id,
            base_url_policy=self.base_url_policy,
            model_id=model_id,
            url=self.resolved_endpoint,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "Idempotency-Key": request_id,
            },
            body_value=body,
            credential_ref=credential_ref,
            data_classes=data_classes,
            output_schema_id=output_schema_id,
            requires_teacher_confirmation=requires_teacher_confirmation,
            execution_allowed=execution_allowed,
        )


DEFAULT_PROVIDER_ADAPTERS: Mapping[str, ProviderAdapter] = MappingProxyType(
    {
        "openai": OpenAIResponsesAdapter(),
        "deepseek": DeepSeekChatAdapter(),
    }
)


def build_usage_cost_hint(usage: Mapping[str, Any] | None = None) -> dict[str, Any]:
    normalized_usage = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    if usage is not None:
        for key in normalized_usage:
            value = usage.get(key)
            if type(value) is not int or not 0 <= value <= 100_000_000:
                raise OneTimeModelApiError("usage_invalid", "模型用量记录不正确。")
            normalized_usage[key] = value
        if normalized_usage["total_tokens"] != (
            normalized_usage["input_tokens"] + normalized_usage["output_tokens"]
        ):
            raise OneTimeModelApiError("usage_invalid", "模型总用量与输入、输出用量不一致。")
    return {
        "schema_version": MODEL_API_SCHEMA_VERSION,
        "contract_kind": USAGE_HINT_KIND,
        "usage": normalized_usage,
        "estimated_cost": {
            "amount": None,
            "currency": None,
            "status": "not_estimated_without_pinned_price",
        },
        "message_zh": (
            "连接或调用后可显示服务商返回的 token 用量；费用未绑定价格表，"
            "请以服务商账单为准。"
        ),
    }


def _normalize_probe(last_probe: Any) -> dict[str, Any]:
    if last_probe is None:
        return {
            "status": "never",
            "connection_state": "not_started",
            "checked_at": None,
            "error_code": None,
            "model_invoked": False,
            "probe_run_id": None,
            "receipt_id": None,
            "receipt_sha256": None,
            "latency_ms": None,
            "usage": None,
        }
    probe = _require_mapping(last_probe, "probe_invalid", "连接测试记录不正确。")
    allowed = {
        "status",
        "connection_state",
        "checked_at",
        "error_code",
        "model_invoked",
        "probe_run_id",
        "receipt_id",
        "receipt_sha256",
        "latency_ms",
        "usage",
    }
    if set(probe) - allowed:
        raise OneTimeModelApiError("probe_invalid", "连接测试记录含未知字段。")
    status = probe.get("status") or "never"
    connection_state = probe.get("connection_state") or "not_started"
    model_invoked = probe.get("model_invoked", False)
    checked_at = probe.get("checked_at")
    error_code = probe.get("error_code")
    probe_run_id = probe.get("probe_run_id")
    receipt_id = probe.get("receipt_id")
    receipt_sha256 = probe.get("receipt_sha256")
    latency_ms = probe.get("latency_ms")
    usage = probe.get("usage")
    if (
        status
        not in {"never", "ready_not_invoked", "succeeded", "failed", "cancelled", "stale"}
        or connection_state
        not in {"not_started", "connected", "failed", "cancelled", "stale"}
        or type(model_invoked) is not bool
        or (checked_at is not None and (not isinstance(checked_at, str) or not _UTC_TIMESTAMP.fullmatch(checked_at)))
        or (error_code is not None and (not isinstance(error_code, str) or not error_code))
        or (
            latency_ms is not None
            and (type(latency_ms) is not int or not 0 <= latency_ms <= 3_600_000)
        )
    ):
        raise OneTimeModelApiError("probe_invalid", "连接测试记录不正确。")
    normalized_usage = None
    if usage is not None:
        if not isinstance(usage, Mapping):
            raise OneTimeModelApiError("probe_invalid", "连接测试用量记录不正确。")
        normalized_usage = build_usage_cost_hint(usage)["usage"]
    if status == "succeeded" and (
        connection_state != "connected"
        or model_invoked is not True
        or not isinstance(checked_at, str)
        or not isinstance(probe_run_id, str)
        or not _REQUEST_ID.fullmatch(probe_run_id)
        or not probe_run_id.startswith("probe_")
        or not isinstance(receipt_id, str)
        or not _RECEIPT_ID.fullmatch(receipt_id)
        or not isinstance(receipt_sha256, str)
        or not _SHA256.fullmatch(receipt_sha256)
        or type(latency_ms) is not int
        or normalized_usage is None
        or error_code is not None
    ):
        raise OneTimeModelApiError(
            "probe_receipt_incomplete", "成功的连接测试缺少完整、可核对的本地回执。"
        )
    if status in {"never", "ready_not_invoked"} and model_invoked:
        raise OneTimeModelApiError("probe_invalid", "未执行的连接测试不能标为已调用模型。")
    return {
        "status": status,
        "connection_state": connection_state,
        "checked_at": checked_at,
        "error_code": error_code,
        "model_invoked": model_invoked,
        "probe_run_id": probe_run_id,
        "receipt_id": receipt_id,
        "receipt_sha256": receipt_sha256,
        "latency_ms": latency_ms,
        "usage": normalized_usage,
    }


def create_one_time_api_configuration(
    profile_metadata: Mapping[str, Any],
    *,
    route_models: Mapping[str, str | None],
    provider_policies: Mapping[str, ProviderPolicy] = DEFAULT_PROVIDER_POLICIES,
) -> dict[str, Any]:
    """Create one provider/credential configuration with four task routes.

    This is a pure contract builder.  It references the credential already held
    by the Windows Credential Manager store and never reads or writes the Key.
    """

    profile = _require_mapping(
        profile_metadata, "profile_invalid", "模型设置记录不正确。"
    )
    _validate_no_secret_values(profile)
    descriptor = _provider_descriptor(profile, provider_policies)
    provider = descriptor["provider_policy"]
    credential_ref = profile.get("credential_ref")
    if not isinstance(credential_ref, str) or not _CREDENTIAL_REF.fullmatch(credential_ref):
        raise OneTimeModelApiError("credential_ref_invalid", "系统密钥引用不正确。")
    revision = profile.get("revision")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise OneTimeModelApiError("profile_revision_invalid", "模型设置版本不正确。")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or not _SAFE_ID.fullmatch(profile_id):
        raise OneTimeModelApiError("profile_invalid", "模型设置标识不正确。")
    if credential_ref != f"ShanghaiChemWorkbench/model-provider/{profile_id}":
        raise OneTimeModelApiError(
            "credential_ref_profile_mismatch", "系统密钥引用与当前设置标识不匹配。"
        )
    probe_model_id = _safe_model_id(profile.get("model_id"))
    catalog_probe_capabilities = _catalog_capabilities(provider, probe_model_id)
    supplied_capabilities = profile.get("capabilities")
    if supplied_capabilities is None:
        probe_declared_capabilities = catalog_probe_capabilities or ("text",)
    else:
        probe_declared_capabilities = _declared_capabilities(supplied_capabilities)
    if (
        catalog_probe_capabilities is not None
        and probe_declared_capabilities != catalog_probe_capabilities
    ):
        raise OneTimeModelApiError(
            "profile_invalid", "连接测试模型能力与内置目录不一致。"
        )
    if set(route_models) != set(TASK_ROUTES):
        raise OneTimeModelApiError(
            "route_models_invalid", "必须分别设置学生分析、题目分类、主题式命题和视觉理解模型。"
        )
    allowed_data_classes = profile.get("allowed_data_classes")
    if (
        not isinstance(allowed_data_classes, list)
        or not allowed_data_classes
        or any(not isinstance(item, str) for item in allowed_data_classes)
        or len(allowed_data_classes) != len(set(allowed_data_classes))
        or not set(allowed_data_classes).issubset(_ALLOWED_DATA_CLASSES)
        or "synthetic_only" not in allowed_data_classes
    ):
        raise OneTimeModelApiError("data_policy_invalid", "模型出站数据范围不正确。")
    image_egress = profile.get("image_egress")
    if image_egress not in {
        "deny",
        "redacted_question_only",
        "teacher_confirmed_source_pages",
        "teacher_confirmed_student_pages",
        "teacher_confirmed_visual_pages",
    }:
        raise OneTimeModelApiError("data_policy_invalid", "题图出站策略不正确。")
    expected_image_classes = {
        "deny": set(),
        "redacted_question_only": {"question_image_redacted"},
        "teacher_confirmed_source_pages": {"source_page_image"},
        "teacher_confirmed_student_pages": {"student_answer_image"},
        "teacher_confirmed_visual_pages": {
            "source_page_image",
            "student_answer_image",
        },
    }[image_egress]
    present_image_classes = {
        item
        for item in (
            "question_image_redacted",
            "source_page_image",
            "student_answer_image",
        )
        if item in allowed_data_classes
    }
    if image_egress == "teacher_confirmed_visual_pages":
        image_policy_matches = bool(present_image_classes) and present_image_classes.issubset(
            expected_image_classes
        )
    else:
        image_policy_matches = present_image_classes == expected_image_classes
    if not image_policy_matches:
        raise OneTimeModelApiError("data_policy_invalid", "题图数据类别与出站策略不一致。")
    credential_state = profile.get("credential_state", "not_configured")
    if credential_state not in {"configured", "not_configured"}:
        raise OneTimeModelApiError("credential_state_invalid", "系统密钥状态不正确。")

    probe = _normalize_probe(profile.get("last_probe"))
    connected = (
        probe["status"] == "succeeded"
        and probe["connection_state"] == "connected"
        and probe["model_invoked"] is True
    )
    probed_capabilities = ("text", "structured_output") if connected else ()
    model_status = (
        "catalog_model"
        if catalog_probe_capabilities is not None
        else "unverified_custom_model"
    )
    if profile.get("model_status") not in {None, model_status}:
        raise OneTimeModelApiError("profile_invalid", "模型目录状态不正确。")

    routes: dict[str, Any] = {}
    for route in TASK_ROUTES:
        model_id = route_models[route]
        if model_id is None:
            if route != VISUAL_UNDERSTANDING:
                raise OneTimeModelApiError(
                    "route_model_required", f"请为“{_ROUTE_LABELS_ZH[route]}”选择模型。"
                )
            routes[route] = {
                "ui_label_zh": _ROUTE_LABELS_ZH[route],
                "model_id": None,
                "enabled": False,
                "disabled_reason_zh": "当前服务商没有允许的视觉模型；本地题库仍可浏览。",
                "required_capabilities": list(_ROUTE_REQUIRED_CAPABILITIES[route]),
                "declared_capabilities": [],
                "effective_capabilities": [],
                "capability_evidence": {
                    "declared": [],
                    "catalog": [],
                    "probed": [],
                    "unknown": ["text", "vision", "structured_output"],
                },
                "credential_ref": credential_ref,
            }
            continue
        model_id = _safe_model_id(model_id)
        catalog_route_capabilities = _catalog_capabilities(provider, model_id)
        if catalog_route_capabilities is not None:
            declared_capabilities = catalog_route_capabilities
            route_probed_capabilities = (
                probed_capabilities if model_id == probe_model_id else ()
            )
        elif model_id == probe_model_id:
            if not connected:
                raise OneTimeModelApiError(
                    "route_model_unverified",
                    f"“{_ROUTE_LABELS_ZH[route]}”使用手填模型前需先通过固定合成连接测试。",
                    409,
                )
            declared_capabilities = probe_declared_capabilities
            route_probed_capabilities = probed_capabilities
        else:
            raise OneTimeModelApiError(
                "route_model_not_declared",
                "手填用途模型必须与当前 profile 的连接测试模型一致。",
                409,
            )
        effective_capabilities = tuple(
            capability
            for capability in ("text", "vision", "structured_output")
            if capability
            in set(declared_capabilities)
            | set(catalog_route_capabilities or ())
            | set(route_probed_capabilities)
        )
        missing = set(_ROUTE_REQUIRED_CAPABILITIES[route]) - set(
            effective_capabilities
        )
        if missing:
            raise OneTimeModelApiError(
                "route_capability_missing",
                f"“{_ROUTE_LABELS_ZH[route]}”缺少所需能力：{'、'.join(sorted(missing))}。",
            )
        routes[route] = {
            "ui_label_zh": _ROUTE_LABELS_ZH[route],
            "model_id": model_id,
            "enabled": True,
            "disabled_reason_zh": None,
            "required_capabilities": list(_ROUTE_REQUIRED_CAPABILITIES[route]),
            "declared_capabilities": list(declared_capabilities),
            "effective_capabilities": list(effective_capabilities),
            "capability_evidence": {
                "declared": list(declared_capabilities),
                "catalog": list(catalog_route_capabilities or ()),
                "probed": list(route_probed_capabilities),
                "unknown": [
                    capability
                    for capability in ("text", "vision", "structured_output")
                    if capability not in effective_capabilities
                ],
            },
            "credential_ref": credential_ref,
        }

    capability_probe = {
        "declared_policy_only": {
            route: {
                "model_id": routes[route]["model_id"],
                "capabilities": list(routes[route]["declared_capabilities"]),
            }
            for route in TASK_ROUTES
        },
        "evidence_by_route": {
            route: deepcopy(routes[route]["capability_evidence"])
            for route in TASK_ROUTES
        },
        "observed": {
            "connectivity": "succeeded_on_probe_model" if connected else "not_observed",
            "minimal_structured_output": (
                "succeeded_on_probe_model" if connected else "not_observed"
            ),
            "vision": "not_tested",
            "route_task_quality": "not_tested",
        },
        "probe_binding": (
            {
                "profile_id": profile_id,
                "profile_revision": revision,
                "provider_id": descriptor["provider_id"],
                "model_id": probe_model_id,
                "probe_run_id": probe["probe_run_id"],
                "receipt_id": probe["receipt_id"],
                "receipt_sha256": probe["receipt_sha256"],
            }
            if connected
            else None
        ),
        "message_zh": (
            "连接测试只证明指定探针模型能够完成最小 JSON 往返；"
            "不等于视觉能力、学生分析质量、题目分类质量或命题质量已经验证。"
        ),
    }
    core = {
        "schema_version": MODEL_API_SCHEMA_VERSION,
        "contract_kind": CONFIG_KIND,
        "profile_id": profile_id,
        "profile_revision": revision,
        "provider_kind": descriptor["provider_kind"],
        "provider_id": descriptor["provider_id"],
        "provider_display_name": descriptor["display_name"],
        "base_url_policy": descriptor["base_url_policy"],
        "resolved_base_url": descriptor["base_url"],
        "api_style": descriptor["api_style"],
        "local_endpoint_policy": descriptor["local_endpoint_policy"],
        "credential_ref": credential_ref,
        "credential_state": credential_state,
        "probe_model_id": probe_model_id,
        "model_status": model_status,
        "routes": routes,
        "allowed_data_classes": list(allowed_data_classes),
        "image_egress": image_egress,
        "connection_test": probe,
        "capability_probe": capability_probe,
        "usage_cost_hint": build_usage_cost_hint(
            probe["usage"] if isinstance(probe.get("usage"), Mapping) else None
        ),
        "secret_storage": {
            "current_backend": "windows_credential_manager_current_user",
            "allowed_backend_family": [
                "windows_credential_manager",
                "dpapi_protected_store",
            ],
            "browser_storage": False,
            "project_file_storage": False,
            "url_storage": False,
            "log_storage": False,
            "export_storage": False,
            "secret_values_exposed": False,
        },
        "offline_workbench_available": True,
        "production_invocation_enabled": False,
        "teacher_message_zh": (
            "API Key 只需保存到 Windows 系统密钥库一次；无 Key 仍可浏览本地题库。"
        ),
    }
    core["configuration_digest"] = _sha256_json(core)
    return core


def validate_one_time_api_configuration(
    value: Mapping[str, Any],
    *,
    provider_policies: Mapping[str, ProviderPolicy] = DEFAULT_PROVIDER_POLICIES,
) -> dict[str, Any]:
    config = _require_mapping(
        value, "configuration_invalid", "一次配置记录不正确。"
    )
    _validate_no_secret_values(config)
    required = {
        "schema_version",
        "contract_kind",
        "profile_id",
        "profile_revision",
        "provider_kind",
        "provider_id",
        "provider_display_name",
        "base_url_policy",
        "resolved_base_url",
        "api_style",
        "local_endpoint_policy",
        "credential_ref",
        "credential_state",
        "probe_model_id",
        "model_status",
        "routes",
        "allowed_data_classes",
        "image_egress",
        "connection_test",
        "capability_probe",
        "usage_cost_hint",
        "secret_storage",
        "offline_workbench_available",
        "production_invocation_enabled",
        "teacher_message_zh",
        "configuration_digest",
    }
    if set(config) != required:
        raise OneTimeModelApiError("configuration_invalid", "一次配置字段不完整或含未知字段。")
    if config.get("schema_version") != MODEL_API_SCHEMA_VERSION or config.get(
        "contract_kind"
    ) != CONFIG_KIND:
        raise OneTimeModelApiError("configuration_invalid", "一次配置版本不兼容。")
    routes = config.get("routes")
    if not isinstance(routes, Mapping) or set(routes) != set(TASK_ROUTES):
        raise OneTimeModelApiError("route_models_invalid", "用途模型路由不完整。")
    route_models: dict[str, str | None] = {}
    probe_declared_capabilities: list[str] | None = None
    for route, item in routes.items():
        item = _require_mapping(item, "route_models_invalid", "用途模型路由不正确。")
        if set(item) != {
            "ui_label_zh",
            "model_id",
            "enabled",
            "disabled_reason_zh",
            "required_capabilities",
            "declared_capabilities",
            "effective_capabilities",
            "capability_evidence",
            "credential_ref",
        }:
            raise OneTimeModelApiError("route_models_invalid", "用途模型路由字段不正确。")
        route_models[route] = item.get("model_id")
        if item.get("model_id") == config.get("probe_model_id"):
            declared = item.get("declared_capabilities")
            if isinstance(declared, list):
                probe_declared_capabilities = list(declared)
    provider = provider_policies.get(config.get("provider_id"))
    catalog_probe = _catalog_capabilities(
        provider, _safe_model_id(config.get("probe_model_id"))
    )
    if catalog_probe is not None:
        probe_declared_capabilities = list(catalog_probe)
    if probe_declared_capabilities is None:
        raise OneTimeModelApiError(
            "route_models_invalid", "手填连接测试模型没有可核对的能力声明。"
        )
    profile = {
        "profile_id": config.get("profile_id"),
        "revision": config.get("profile_revision"),
        "provider_kind": config.get("provider_kind"),
        "provider_id": config.get("provider_id"),
        "display_name": config.get("provider_display_name"),
        "model_id": config.get("probe_model_id"),
        "model_status": config.get("model_status"),
        "base_url_policy": config.get("base_url_policy"),
        "base_url": config.get("resolved_base_url"),
        "api_style": config.get("api_style"),
        "local_endpoint_policy": config.get("local_endpoint_policy"),
        "credential_ref": config.get("credential_ref"),
        "credential_state": config.get("credential_state"),
        "capabilities": probe_declared_capabilities,
        "allowed_data_classes": config.get("allowed_data_classes"),
        "image_egress": config.get("image_egress"),
        "last_probe": config.get("connection_test"),
    }
    expected = create_one_time_api_configuration(
        profile,
        route_models=route_models,
        provider_policies=provider_policies,
    )
    digest = config.get("configuration_digest")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise OneTimeModelApiError("configuration_digest_invalid", "一次配置校验值不正确。")
    unsigned = dict(config)
    unsigned.pop("configuration_digest")
    if _sha256_json(unsigned) != digest:
        raise OneTimeModelApiError(
            "configuration_digest_mismatch", "一次配置内容已变化，请重新保存。", 409
        )
    if dict(config) != expected:
        raise OneTimeModelApiError(
            "configuration_invalid", "一次配置内容与服务地址、能力证据或安全边界不一致。"
        )
    return deepcopy(expected)


def _string_array_schema(*, min_items: int = 0) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": min_items,
    }


def _authority_boundary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "may_change_answer_authority": {"type": "boolean", "const": False},
            "may_change_source_provenance": {"type": "boolean", "const": False},
            "may_write_central_registry": {"type": "boolean", "const": False},
            "may_unlock_usage_eligibility": {"type": "boolean", "const": False},
            "may_claim_human_review": {"type": "boolean", "const": False},
        },
        "required": [
            "may_change_answer_authority",
            "may_change_source_provenance",
            "may_write_central_registry",
            "may_unlock_usage_eligibility",
            "may_claim_human_review",
        ],
    }


def _student_candidate_schema() -> dict[str, Any]:
    cause = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label_zh": {"type": "string", "minLength": 1},
            "evidence_refs": _string_array_schema(min_items=1),
            "counterevidence_refs": _string_array_schema(),
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["label_zh", "evidence_refs", "counterevidence_refs", "confidence"],
    }
    recommendation = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title_zh": {"type": "string", "minLength": 1},
            "reason_zh": {"type": "string", "minLength": 1},
            "theme_or_dependency_closure_ref": {"type": "string", "minLength": 1},
        },
        "required": ["title_zh", "reason_zh", "theme_or_dependency_closure_ref"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "observations_zh": _string_array_schema(min_items=1),
            "error_cause_candidates": {"type": "array", "items": cause},
            "counterevidence_zh": _string_array_schema(),
            "recommendation_candidates": {"type": "array", "items": recommendation},
        },
        "required": [
            "observations_zh",
            "error_cause_candidates",
            "counterevidence_zh",
            "recommendation_candidates",
        ],
    }


def _classification_candidate_schema() -> dict[str, Any]:
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    labels = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "item_type": nullable_string,
            "primary_knowledge_K": _string_array_schema(),
            "supporting_knowledge_K": _string_array_schema(),
            "ability_A": _string_array_schema(),
            "context_C": _string_array_schema(),
            "response_R": _string_array_schema(),
            "representation_RP": _string_array_schema(),
            "cognitive_prelabel": nullable_string,
        },
        "required": [
            "item_type",
            "primary_knowledge_K",
            "supporting_knowledge_K",
            "ability_A",
            "context_C",
            "response_R",
            "representation_RP",
            "cognitive_prelabel",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "atomic_part_id": {"type": "string", "minLength": 1},
            "labels": labels,
            "rationale_zh": _string_array_schema(min_items=1),
        },
        "required": ["atomic_part_id", "labels", "rationale_zh"],
    }


def _generation_candidate_schema() -> dict[str, Any]:
    atomic = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "atomic_part_id": {"type": "string", "minLength": 1},
            "item_type": {"type": "string", "minLength": 1},
            "prompt_candidate_zh": {"type": "string", "minLength": 1},
            "suggested_answer_candidate_zh": {"type": "string", "minLength": 1},
            "suggested_score": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": [
            "atomic_part_id",
            "item_type",
            "prompt_candidate_zh",
            "suggested_answer_candidate_zh",
            "suggested_score",
        ],
    }
    printed = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "printed_question_id": {"type": "string", "minLength": 1},
            "atomic_parts": {"type": "array", "items": atomic, "minItems": 1},
        },
        "required": ["printed_question_id", "atomic_parts"],
    }
    theme = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "theme_title_zh": {"type": "string", "minLength": 1},
            "shared_material_candidate_zh": {"type": "string", "minLength": 1},
            "printed_questions": {"type": "array", "items": printed, "minItems": 1},
        },
        "required": ["theme_title_zh", "shared_material_candidate_zh", "printed_questions"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "top_level_unit": {"type": "string", "const": "theme_big_question"},
            "standalone_choice_section_allowed": {"type": "boolean", "const": False},
            "themes": {"type": "array", "items": theme, "minItems": 1},
        },
        "required": ["top_level_unit", "standalone_choice_section_allowed", "themes"],
    }


def _visual_candidate_schema() -> dict[str, Any]:
    visual = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "visual_type": {"type": "string", "minLength": 1},
            "description_zh": {"type": "string", "minLength": 1},
            "chemistry_objects_zh": _string_array_schema(),
            "uncertainties_zh": _string_array_schema(),
            "answer_content_detected": {"type": "boolean"},
        },
        "required": [
            "visual_type",
            "description_zh",
            "chemistry_objects_zh",
            "uncertainties_zh",
            "answer_content_detected",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "visual_objects": {"type": "array", "items": visual},
            "page_level_notes_zh": _string_array_schema(),
        },
        "required": ["visual_objects", "page_level_notes_zh"],
    }


def candidate_output_schema(task_route: str) -> dict[str, Any]:
    candidate_schemas = {
        STUDENT_ANALYSIS: _student_candidate_schema,
        QUESTION_CLASSIFICATION: _classification_candidate_schema,
        THEME_QUESTION_GENERATION: _generation_candidate_schema,
        VISUAL_UNDERSTANDING: _visual_candidate_schema,
    }
    factory = candidate_schemas.get(task_route)
    if factory is None:
        raise OneTimeModelApiError("task_route_invalid", "模型用途路由不正确。")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": MODEL_CANDIDATE_SCHEMA_VERSION,
            },
            "candidate_status": {
                "type": "string",
                "const": "structured_candidate_only",
            },
            "task_route": {"type": "string", "const": task_route},
            "source_snapshot_id": {"type": "string", "minLength": 1},
            "candidate": factory(),
            "evidence_refs": _string_array_schema(),
            "uncertainties_zh": _string_array_schema(),
            "requires_teacher_review": {"type": "boolean", "const": True},
            "authority_boundary": _authority_boundary_schema(),
        },
        "required": [
            "schema_version",
            "candidate_status",
            "task_route",
            "source_snapshot_id",
            "candidate",
            "evidence_refs",
            "uncertainties_zh",
            "requires_teacher_review",
            "authority_boundary",
        ],
    }


def _connection_output_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {"status": {"type": "string", "const": "ok"}},
        "required": ["status"],
    }


def _trusted_adapter(configuration: Mapping[str, Any]) -> ProviderAdapter:
    provider_id = configuration["provider_id"]
    if configuration.get("provider_kind") == "openai_compatible":
        return OpenAICompatibleAdapter(
            base_url=configuration["resolved_base_url"],
            base_url_policy=configuration["base_url_policy"],
            api_style=configuration["api_style"],
        )
    adapter = DEFAULT_PROVIDER_ADAPTERS.get(provider_id)
    if adapter is None:
        raise OneTimeModelApiError("provider_adapter_unavailable", "当前服务商适配器不可用。", 409)
    return adapter


def _adapter(
    configuration: Mapping[str, Any],
    adapters: Mapping[str, ProviderAdapter] | None,
) -> tuple[ProviderAdapter, ProviderAdapter]:
    trusted = _trusted_adapter(configuration)
    if adapters is None:
        return trusted, trusted
    actual = adapters.get(configuration["provider_id"])
    if actual is None or actual.provider_id != configuration["provider_id"]:
        raise OneTimeModelApiError(
            "provider_adapter_unavailable", "当前服务商适配器不可用。", 409
        )
    return actual, trusted


def _build_checked_request(
    adapter: ProviderAdapter,
    *,
    trusted_adapter: ProviderAdapter,
    build_kwargs: Mapping[str, Any],
) -> PreparedProviderRequest:
    actual = adapter.build_request(**dict(build_kwargs))
    expected = trusted_adapter.build_request(**dict(build_kwargs))
    if not isinstance(actual, PreparedProviderRequest) or actual != expected:
        raise OneTimeModelApiError(
            "provider_adapter_contract_violation",
            "服务商请求未通过固定地址、确认状态与内容校验。",
            409,
        )
    if (
        actual.body_sha256 != _sha256_bytes(actual.body)
        or any(key.casefold() == "authorization" for key in actual.headers_without_authorization)
        or actual.credential_ref.encode("utf-8") in actual.body
        or len(actual.body) > 2_000_000
    ):
        raise OneTimeModelApiError(
            "provider_adapter_contract_violation", "服务商请求含不安全的地址、请求头或正文。", 409
        )
    try:
        body_value = _strict_json_loads(actual.body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise OneTimeModelApiError(
            "provider_adapter_contract_violation", "服务商请求正文不是有效 JSON。", 409
        ) from None
    _validate_no_secret_values(body_value)
    return actual


def prepare_connection_test_request(
    configuration: Mapping[str, Any],
    *,
    request_id: str,
    adapters: Mapping[str, ProviderAdapter] | None = None,
) -> PreparedProviderRequest:
    config = validate_one_time_api_configuration(configuration)
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id) or not request_id.startswith(
        "probe_"
    ):
        raise OneTimeModelApiError("request_id_invalid", "连接测试编号不正确。")
    adapter, trusted_adapter = _adapter(config, adapters)
    if adapter.base_url_policy != config["base_url_policy"]:
        raise OneTimeModelApiError("base_url_policy_mismatch", "适配器地址策略不匹配。")
    return _build_checked_request(
        adapter,
        trusted_adapter=trusted_adapter,
        build_kwargs={
            "request_id": request_id,
            "request_kind": "synthetic_connection_test",
            "task_route": CONNECTION_TEST,
            "model_id": config["probe_model_id"],
            "credential_ref": config["credential_ref"],
            "prompt": FIXED_SYNTHETIC_PROMPT,
            "output_schema": _connection_output_schema(),
            "output_schema_id": "shchem_synthetic_probe",
            "data_classes": ("synthetic_only",),
            "max_output_tokens": 64,
            "requires_teacher_confirmation": True,
            "execution_allowed": False,
        },
    )


def _normalize_input_context(value: Mapping[str, Any]) -> dict[str, Any]:
    context = _require_mapping(value, "input_context_invalid", "模型输入上下文不正确。")
    expected = {
        "source_snapshot_id",
        "data_classes",
        "deidentified",
        "theme_context_complete",
        "dependency_closure_complete",
        "teacher_confirmed_egress",
    }
    if set(context) != expected:
        raise OneTimeModelApiError("input_context_invalid", "模型输入上下文字段不完整。")
    snapshot = context.get("source_snapshot_id")
    if not isinstance(snapshot, str) or not _SAFE_ID.fullmatch(snapshot):
        raise OneTimeModelApiError("input_context_invalid", "模型输入题库版本不正确。")
    classes = context.get("data_classes")
    if (
        not isinstance(classes, list)
        or not classes
        or len(classes) != len(set(classes))
        or any(not isinstance(item, str) or not item for item in classes)
    ):
        raise OneTimeModelApiError("input_context_invalid", "模型输入数据类别不正确。")
    for key in (
        "deidentified",
        "theme_context_complete",
        "dependency_closure_complete",
        "teacher_confirmed_egress",
    ):
        if type(context.get(key)) is not bool:
            raise OneTimeModelApiError("input_context_invalid", "模型输入确认状态不正确。")
    return dict(context)


def _validate_route_input(
    *,
    task_route: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    configuration: Mapping[str, Any],
) -> None:
    if task_route not in TASK_ROUTES:
        raise OneTimeModelApiError("task_route_invalid", "模型用途路由不正确。")
    if set(payload) != set(_ROUTE_REQUIRED_INPUT_KEYS[task_route]):
        raise OneTimeModelApiError(
            "route_input_invalid", f"“{_ROUTE_LABELS_ZH[task_route]}”输入字段不完整。"
        )
    _validate_no_secret_values(payload)
    found_student = _walk_forbidden_keys(payload, _FORBIDDEN_STUDENT_KEYS)
    if found_student is not None:
        raise OneTimeModelApiError(
            "student_identifier_forbidden",
            "模型输入中不能包含姓名、学号、学校、班级、联系方式、人脸或本地路径。",
        )
    route_classes = set(context["data_classes"])
    if not route_classes.issubset(_ROUTE_ALLOWED_DATA_CLASSES[task_route]):
        raise OneTimeModelApiError("data_class_not_allowed", "当前用途不能发送这类数据。", 409)
    if not set(context["data_classes"]).issubset(configuration["allowed_data_classes"]):
        raise OneTimeModelApiError("data_class_not_allowed", "当前设置不允许发送这类数据。", 409)
    if task_route == STUDENT_ANALYSIS and (
        not context["deidentified"]
        or "deidentified_student_text" not in context["data_classes"]
    ):
        raise OneTimeModelApiError(
            "student_deidentification_required", "学生分析只能发送已脱敏的学生文本。", 409
        )
    if task_route in {QUESTION_CLASSIFICATION, THEME_QUESTION_GENERATION} and (
        not context["theme_context_complete"]
        or not context["dependency_closure_complete"]
    ):
        raise OneTimeModelApiError(
            "theme_context_required", "题目分类或主题式命题必须携带完整主题材料和依赖闭包。", 409
        )
    if task_route == THEME_QUESTION_GENERATION:
        blueprint = payload.get("theme_blueprint")
        if not isinstance(blueprint, Mapping) or blueprint.get(
            "top_level_unit"
        ) != "theme_big_question" or blueprint.get(
            "standalone_choice_section_allowed"
        ) is not False:
            raise OneTimeModelApiError(
                "generation_blueprint_invalid", "主题式命题输入不得含独立选择题板块。", 409
            )
    if task_route == VISUAL_UNDERSTANDING and (
        configuration["image_egress"] != "redacted_question_only"
        or "question_image_redacted" not in context["data_classes"]
    ):
        raise OneTimeModelApiError(
            "image_egress_not_allowed", "视觉理解只允许发送已脱敏并获准的题图。", 409
        )
    if task_route == VISUAL_UNDERSTANDING:
        image_refs = payload.get("image_refs")
        if (
            not isinstance(image_refs, list)
            or not 1 <= len(image_refs) <= 20
            or len(image_refs) != len(set(image_refs))
            or any(
                not isinstance(item, str)
                or not _APPROVED_IMAGE_ASSET_REF.fullmatch(item)
                for item in image_refs
            )
        ):
            raise OneTimeModelApiError(
                "image_asset_ref_invalid",
                "视觉理解只接受后端签发并绑定哈希的脱敏题图资产标识；不能发送本地路径或任意网址。",
                409,
            )


def _route_prompt(
    *, task_route: str, source_snapshot_id: str, payload: Mapping[str, Any]
) -> str:
    boundary = {
        "candidate_status": "structured_candidate_only",
        "may_change_answer_authority": False,
        "may_change_source_provenance": False,
        "may_write_central_registry": False,
        "may_unlock_usage_eligibility": False,
        "may_claim_human_review": False,
    }
    input_json = json.dumps(
        {
            "task_route": task_route,
            "source_snapshot_id": source_snapshot_id,
            "immutable_boundary": boundary,
            "input": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (
        "你是上海高中化学教师副驾驶。只生成结构化候选；不得改变题库来源、"
        "答案权威、人工复核、教学资格或发布状态。选择、填空、简答、计算等只能"
        "位于主题大题内部。请根据下列冻结输入返回 JSON：\n"
        + input_json
    )


def prepare_model_candidate_request(
    configuration: Mapping[str, Any],
    *,
    task_route: str,
    input_payload: Mapping[str, Any],
    input_context: Mapping[str, Any],
    request_id: str,
    dry_run: bool = True,
    adapters: Mapping[str, ProviderAdapter] | None = None,
) -> PreparedProviderRequest:
    """Prepare a structured candidate request without resolving or exposing a Key."""

    config = validate_one_time_api_configuration(configuration)
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id) or not request_id.startswith(
        "request_"
    ):
        raise OneTimeModelApiError("request_id_invalid", "模型请求编号不正确。")
    if dry_run not in {True, False}:
        raise OneTimeModelApiError("dry_run_invalid", "模型请求模式不正确。")
    route = config["routes"].get(task_route)
    if not isinstance(route, Mapping) or route.get("enabled") is not True:
        raise OneTimeModelApiError("route_disabled", "当前用途没有可用模型。", 409)
    payload = _require_mapping(
        input_payload, "route_input_invalid", "模型用途输入不正确。"
    )
    context = _normalize_input_context(input_context)
    _validate_route_input(
        task_route=task_route,
        payload=payload,
        context=context,
        configuration=config,
    )
    if not dry_run and config["production_invocation_enabled"] is not True:
        raise OneTimeModelApiError(
            "production_invocation_disabled",
            "生产模型调用尚未由共享服务接通；当前只能生成安全请求预览。",
            409,
        )
    adapter, trusted_adapter = _adapter(config, adapters)
    if adapter.base_url_policy != config["base_url_policy"]:
        raise OneTimeModelApiError("base_url_policy_mismatch", "适配器地址策略不匹配。")
    output_schema = candidate_output_schema(task_route)
    return _build_checked_request(
        adapter,
        trusted_adapter=trusted_adapter,
        build_kwargs={
            "request_id": request_id,
            "request_kind": "structured_candidate",
            "task_route": task_route,
            "model_id": route["model_id"],
            "credential_ref": config["credential_ref"],
            "prompt": _route_prompt(
                task_route=task_route,
                source_snapshot_id=context["source_snapshot_id"],
                payload=payload,
            ),
            "output_schema": output_schema,
            "output_schema_id": f"shchem_{task_route}_candidate",
            "data_classes": tuple(context["data_classes"]),
            "max_output_tokens": (
                2_400 if task_route == THEME_QUESTION_GENERATION else 1_200
            ),
            "requires_teacher_confirmation": True,
            "execution_allowed": (
                not dry_run
                and config["production_invocation_enabled"] is True
                and config["credential_state"] == "configured"
                and context["teacher_confirmed_egress"] is True
            ),
        },
    )


def validate_model_candidate_output(
    value: Mapping[str, Any],
    *,
    expected_task_route: str,
    expected_source_snapshot_id: str,
    allowed_evidence_refs: Sequence[str],
    expected_atomic_part_id: str | None = None,
) -> dict[str, Any]:
    """Fail closed when model output attempts to change authority or provenance."""

    record = _require_mapping(value, "candidate_output_invalid", "模型候选输出不是有效对象。")
    try:
        Draft202012Validator(candidate_output_schema(expected_task_route)).validate(record)
    except ValidationError:
        raise OneTimeModelApiError(
            "candidate_output_invalid", "模型候选输出未通过用途对应的结构校验。"
        ) from None
    _validate_no_secret_values(record)
    if _walk_forbidden_keys(record, _FORBIDDEN_STUDENT_KEYS) is not None:
        raise OneTimeModelApiError(
            "student_identifier_forbidden", "模型候选输出中不能出现学生身份或本地路径字段。"
        )
    if (
        not isinstance(allowed_evidence_refs, Sequence)
        or isinstance(allowed_evidence_refs, (str, bytes))
        or len(allowed_evidence_refs) != len(set(allowed_evidence_refs))
        or any(
            not isinstance(item, str) or not _SAFE_ID.fullmatch(item)
            for item in allowed_evidence_refs
        )
    ):
        raise OneTimeModelApiError("candidate_binding_invalid", "允许的证据引用列表不正确。")
    allowed_evidence = set(allowed_evidence_refs)

    def validate_evidence_bindings(nested: Any) -> None:
        if isinstance(nested, Mapping):
            for key, item in nested.items():
                if key in {"evidence_refs", "counterevidence_refs"} and (
                    not isinstance(item, list)
                    or any(
                        not isinstance(ref, str) or ref not in allowed_evidence
                        for ref in item
                    )
                ):
                    raise OneTimeModelApiError(
                        "candidate_evidence_ref_forbidden",
                        "模型候选引用了冻结输入之外的证据。",
                    )
                validate_evidence_bindings(item)
        elif isinstance(nested, list):
            for item in nested:
                validate_evidence_bindings(item)

    validate_evidence_bindings(record)
    expected_keys = {
        "schema_version",
        "candidate_status",
        "task_route",
        "source_snapshot_id",
        "candidate",
        "evidence_refs",
        "uncertainties_zh",
        "requires_teacher_review",
        "authority_boundary",
    }
    if set(record) != expected_keys:
        raise OneTimeModelApiError("candidate_output_invalid", "模型候选输出字段不完整或含未知字段。")
    if (
        record.get("schema_version") != MODEL_CANDIDATE_SCHEMA_VERSION
        or record.get("candidate_status") != "structured_candidate_only"
        or record.get("task_route") != expected_task_route
        or record.get("source_snapshot_id") != expected_source_snapshot_id
        or record.get("requires_teacher_review") is not True
    ):
        raise OneTimeModelApiError("candidate_output_invalid", "模型候选输出版本或绑定不正确。")
    boundary = _require_mapping(
        record.get("authority_boundary"),
        "candidate_authority_boundary_invalid",
        "模型候选输出缺少权限边界。",
    )
    expected_boundary = {
        "may_change_answer_authority": False,
        "may_change_source_provenance": False,
        "may_write_central_registry": False,
        "may_unlock_usage_eligibility": False,
        "may_claim_human_review": False,
    }
    if dict(boundary) != expected_boundary:
        raise OneTimeModelApiError(
            "candidate_authority_boundary_invalid",
            "模型候选不得改变答案权威、题库来源、资格或真人复核状态。",
        )
    found = _walk_forbidden_keys(record.get("candidate"), _FORBIDDEN_CANDIDATE_MUTATION_KEYS)
    if found is not None:
        raise OneTimeModelApiError(
            "candidate_authority_mutation_forbidden",
            "模型候选包含来源或答案权威修改字段，已拒绝。",
        )
    candidate = _require_mapping(
        record.get("candidate"), "candidate_output_invalid", "模型候选正文不正确。"
    )
    expected_candidate_keys = {
        STUDENT_ANALYSIS: {
            "observations_zh",
            "error_cause_candidates",
            "counterevidence_zh",
            "recommendation_candidates",
        },
        QUESTION_CLASSIFICATION: {"atomic_part_id", "labels", "rationale_zh"},
        THEME_QUESTION_GENERATION: {
            "top_level_unit",
            "standalone_choice_section_allowed",
            "themes",
        },
        VISUAL_UNDERSTANDING: {"visual_objects", "page_level_notes_zh"},
    }.get(expected_task_route)
    if expected_candidate_keys is None or set(candidate) != expected_candidate_keys:
        raise OneTimeModelApiError("candidate_output_invalid", "模型候选正文字段不符合用途合同。")
    if expected_task_route == QUESTION_CLASSIFICATION and (
        not isinstance(expected_atomic_part_id, str)
        or not _SAFE_ID.fullmatch(expected_atomic_part_id)
        or candidate.get("atomic_part_id") != expected_atomic_part_id
    ):
        raise OneTimeModelApiError(
            "candidate_target_mismatch", "分类候选未绑定当前作答单元。"
        )
    if expected_task_route == THEME_QUESTION_GENERATION and (
        candidate.get("top_level_unit") != "theme_big_question"
        or candidate.get("standalone_choice_section_allowed") is not False
        or not isinstance(candidate.get("themes"), list)
        or not candidate.get("themes")
    ):
        raise OneTimeModelApiError(
            "standalone_choice_section_forbidden",
            "主题式命题候选必须以主题大题为一级结构。",
        )
    for key in ("evidence_refs", "uncertainties_zh"):
        values = record.get(key)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise OneTimeModelApiError("candidate_output_invalid", "模型候选证据或不确定性列表不正确。")
    return deepcopy(dict(record))


def parse_model_candidate_output(
    raw: str | bytes,
    *,
    expected_task_route: str,
    expected_source_snapshot_id: str,
    allowed_evidence_refs: Sequence[str],
    expected_atomic_part_id: str | None = None,
) -> dict[str, Any]:
    try:
        value = _strict_json_loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise OneTimeModelApiError("candidate_output_invalid_json", "模型返回的 JSON 无效。") from None
    return validate_model_candidate_output(
        value,
        expected_task_route=expected_task_route,
        expected_source_snapshot_id=expected_source_snapshot_id,
        allowed_evidence_refs=allowed_evidence_refs,
        expected_atomic_part_id=expected_atomic_part_id,
    )


def synthetic_probe_fingerprint() -> dict[str, Any]:
    """Expose the existing fixed prompt fingerprint without executing a request."""

    return {
        "prompt_sha256": FIXED_SYNTHETIC_PROMPT_SHA256,
        "prompt_bytes": len(FIXED_SYNTHETIC_PROMPT_BYTES),
        "data_class": "synthetic_only",
        "question_data_sent": False,
        "student_data_sent": False,
        "image_sent": False,
    }


__all__ = [
    "CONFIG_KIND",
    "CONNECTION_TEST",
    "DEFAULT_PROVIDER_ADAPTERS",
    "MODEL_API_SCHEMA_VERSION",
    "MODEL_CANDIDATE_SCHEMA_VERSION",
    "QUESTION_CLASSIFICATION",
    "REQUEST_PREVIEW_KIND",
    "STUDENT_ANALYSIS",
    "TASK_ROUTES",
    "THEME_QUESTION_GENERATION",
    "USAGE_HINT_KIND",
    "VISUAL_UNDERSTANDING",
    "DeepSeekChatAdapter",
    "OneTimeModelApiError",
    "OpenAICompatibleAdapter",
    "OpenAIResponsesAdapter",
    "PreparedProviderRequest",
    "ProviderAdapter",
    "build_usage_cost_hint",
    "candidate_output_schema",
    "create_one_time_api_configuration",
    "parse_model_candidate_output",
    "prepare_connection_test_request",
    "prepare_model_candidate_request",
    "synthetic_probe_fingerprint",
    "validate_model_candidate_output",
    "validate_one_time_api_configuration",
]
