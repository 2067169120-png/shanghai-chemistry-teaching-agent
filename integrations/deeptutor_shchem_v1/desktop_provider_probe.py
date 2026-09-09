"""Native connection test: fixed synthetic prompt, existing secure transport."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .model_provider_probe import (
    ModelProviderProbeError,
    ModelProviderSyntheticProbeManager,
    ProbeTransport,
)
from .model_provider_settings import ModelProviderSettingsStore

_MESSAGES = {
    "connection_test_confirmation_required": "请先确认短文本测试的出站内容和可能产生的费用。",
    "dns_failure": "域名解析失败。请检查网络和代理软件的 DNS 设置后重试。",
    "endpoint_address_not_allowed": "域名解析到了不允许的网络地址。若使用代理软件，请检查 Fake-IP 排除设置。",
    "tls_failure": "HTTPS 证书验证失败。请检查系统时间、代理和证书配置；不要关闭证书验证。",
    "invalid_credentials": "服务端拒绝了已保存的密钥（401）。请检查密钥是否有效并重新保存。",
    "permission_denied": "服务端拒绝访问（403）。请检查账号及模型访问权限。",
    "rate_limited": "服务端限制了请求（429）。请检查账号额度或稍后手动重试。",
    "timeout": "连接测试超时。请检查网络，或稍后手动重试。",
    "provider_unavailable": "模型服务暂时不可用，请稍后手动重试。",
    "provider_rejected": "服务端拒绝了测试请求。请核对接口地址、接口格式和模型名称。",
    "response_invalid": "已收到响应，但未通过固定测试内容校验；请核对模型及结构化输出支持。",
    "redirect_refused": "接口返回了重定向；请核对服务商提供的最终接口地址。",
    "model_not_configured": "尚未保存可用的模型密钥，请先保存设置。",
    "revision_conflict": "设置已发生变化，请重新打开设置后测试。",
    "receipt_write_failed": "测试回执未能保存，不能确认测试成功；请检查本机文件权限。",
    "cancelled": "测试已停止；若请求已经发出，服务商仍可能计费。",
}


class DesktopConnectionTestError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in _MESSAGES else "connection_test_unavailable"
        self.message_zh = _MESSAGES.get(
            code, "连接测试未完成，请检查已保存的设置后重试。"
        )
        super().__init__(self.message_zh)


@dataclass(frozen=True)
class ProviderConnectionResult:
    status: str
    message_zh: str
    error_code: str | None = None
    latency_ms: int | None = None
    total_tokens: int | None = None
    model_invoked: bool = False
    receipt_id: str | None = None


def connection_result_from_state(state: Mapping[str, Any]) -> ProviderConnectionResult:
    status = state["status"]
    code = state.get("error_code")
    if status == "succeeded":
        message = "连接成功：已保存的密钥和模型完成了短文本测试。此结果不代表图片识别或命题质量已验证。"
    elif status == "stale":
        message = "测试期间设置发生变化，本次结果不适用于当前配置；请重新读取设置。"
    else:
        message = _MESSAGES.get(
            code or status, "连接测试未通过，请核对接口地址、模型和网络设置。"
        )
    usage = state.get("usage") or {}
    return ProviderConnectionResult(
        status=status,
        message_zh=message,
        error_code=code if code in _MESSAGES else None,
        latency_ms=state.get("latency_ms"),
        total_tokens=usage.get("total_tokens"),
        model_invoked=state.get("model_invoked") is True,
        receipt_id=state.get("receipt_id"),
    )


def run_connection_test(
    settings: ModelProviderSettingsStore,
    profile_id: str,
    *,
    expected_revision: str,
    confirmed: bool,
    is_cancelled: Callable[[], bool] = lambda: False,
    transport: ProbeTransport | None = None,
) -> ProviderConnectionResult:
    """Call only after a native egress confirmation, from a background worker."""
    if confirmed is not True:
        raise DesktopConnectionTestError("connection_test_confirmation_required")
    if is_cancelled():
        return ProviderConnectionResult("cancelled", _MESSAGES["cancelled"])
    manager = ModelProviderSyntheticProbeManager(
        settings, transport=transport, max_workers=1
    )
    try:
        started = manager.start(
            profile_id,
            expected_revision=expected_revision,
            idempotency_key="probe_" + uuid.uuid4().hex,
        )
        run_id = started["probe"]["probe_run_id"]
        cancel_sent = False
        while True:
            state = manager.get(run_id)
            if state["status"] in {"succeeded", "failed", "cancelled", "stale"}:
                break
            if is_cancelled() and not cancel_sent:
                manager.cancel(run_id)
                cancel_sent = True
            time.sleep(0.05)
        return connection_result_from_state(state)
    except ModelProviderProbeError as exc:
        # Never expose transport exception text, credentials, or response bodies.
        raise DesktopConnectionTestError(exc.code) from None
    finally:
        manager.shutdown(wait=True)
