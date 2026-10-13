"""Content-free, local model usage ledger and user-entered price snapshots.

Provider counts and local planning estimates are deliberately different fields.
Only a closed collection of identifiers, enums and numbers can reach storage.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any
from uuid import uuid4

TASK_TYPES = frozenset({
    "unknown", "connection_test", "preparation", "visual_import",
    "visual_crop_review", "textbook_knowledge", "exam_advice",
    "student_analysis", "semantic_tags", "blueprint_generation",
    "blueprint_diagnosis", "blueprint_revision",
})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,239}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_TOKEN_MAX = 100_000_000
_FIELDS = (
    "call_id", "occurred_at", "profile_id", "provider_id", "model_id",
    "api_style", "task_type", "outcome", "model_invoked", "http_status",
    "latency_ms", "local_input_estimate", "input_tokens", "output_tokens",
    "total_tokens", "cached_input_tokens", "reasoning_tokens", "usage_status",
    "estimated_cost_amount", "estimated_cost_currency", "cost_status", "price_version",
    "price_input_per_million", "price_output_per_million", "price_cached_input_per_million",
)
_FAILURES: dict[str, int] = {}
_FAILURE_LOCK = threading.Lock()


class ModelUsageError(ValueError):
    def __init__(self, code: str, message_zh: str):
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


def _safe_id(value: Any) -> str:
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else "unknown"


def _number(value: Any, maximum: int = _TOKEN_MAX) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


@dataclass(frozen=True, slots=True, repr=False)
class UsageRequestMetadata:
    root: Path
    profile_id: str
    provider_id: str
    model_id: str
    task_type: str
    local_input_estimate: int | None
    call_id: str
    price_snapshot: dict[str, Any] | None = None
    price_available: bool = True

    def __repr__(self) -> str:
        return f"UsageRequestMetadata(task_type={self.task_type!r}, content_present=False)"


def make_usage_metadata(context: Any, task_type: str, estimate: int | None) -> UsageRequestMetadata | None:
    root = getattr(context, "usage_root", None)
    if root is None:
        return None
    profile_id = _safe_id(getattr(context, "profile_id", None))
    provider_id = _safe_id(getattr(context, "provider_id", None))
    model_id = _safe_id(getattr(context, "model_id", None))
    price_snapshot, price_available = None, True
    try:
        rows = ModelUsageStore(root)._read("SELECT * FROM prices WHERE profile_id=? AND provider_id=? AND model_id=?",
                                          (profile_id, provider_id, model_id))
        price_snapshot = rows[0] if rows else None
    except Exception:  # noqa: BLE001 - a local price read must never block an authorized request
        price_available = False
        _note_failure(Path(root))
    return UsageRequestMetadata(Path(root), profile_id, provider_id, model_id,
                                task_type if task_type in TASK_TYPES else "unknown", _number(estimate),
                                uuid4().hex, price_snapshot, price_available)


def _failure_key(root: Path) -> str:
    return os.path.normcase(os.path.abspath(root))


def _stored_failure_count(root: Path) -> int:
    try:
        with (root / "recording-status.json").open("rb") as stream:
            raw = stream.read(1025)
        if len(raw) > 1024:
            return 0
        value = json.loads(raw)
        count = value.get("recording_failure_count") if isinstance(value, dict) else None
        return _number(count) or 0
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return 0


def _note_failure(root: Path) -> None:
    with _FAILURE_LOCK:
        key = _failure_key(root)
        count = max(_FAILURES.get(key, 0), _stored_failure_count(root)) + 1
        _FAILURES[key] = count
        temporary = None
        try:
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                             prefix=".recording-status-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"schema_version": "shchem.model-usage-status.v1", "recording_failure_count": count}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, root / "recording-status.json")
        except OSError:
            # If the disk itself is unavailable, the live process still reports
            # the failure. No ledger write or API request is retried here.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def _decode_usage(api_style: str, raw: bytes | None) -> dict[str, int | None]:
    result = dict.fromkeys(("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens"))
    if not isinstance(raw, bytes) or len(raw) > 2 * 1024 * 1024:
        return result

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return result
    usage = value.get("usage") if isinstance(value, Mapping) else None
    if not isinstance(usage, Mapping):
        return result
    input_key, output_key = (("input_tokens", "output_tokens") if api_style == "responses"
                             else ("prompt_tokens", "completion_tokens"))
    result["input_tokens"] = _number(usage.get(input_key))
    result["output_tokens"] = _number(usage.get(output_key))
    result["total_tokens"] = _number(usage.get("total_tokens"))
    input_details = usage.get("input_tokens_details" if api_style == "responses" else "prompt_tokens_details")
    output_details = usage.get("output_tokens_details" if api_style == "responses" else "completion_tokens_details")
    if isinstance(input_details, Mapping):
        result["cached_input_tokens"] = _number(input_details.get("cached_tokens"))
    # Some compatible providers report the cache hit count as a top-level field.
    if result["cached_input_tokens"] is None:
        result["cached_input_tokens"] = _number(usage.get("prompt_cache_hit_tokens"))
    if isinstance(output_details, Mapping):
        result["reasoning_tokens"] = _number(output_details.get("reasoning_tokens"))
    if (result["cached_input_tokens"] is not None and result["input_tokens"] is not None
            and result["cached_input_tokens"] > result["input_tokens"]):
        result["cached_input_tokens"] = None
    return result


def _price(value: Any, *, optional: bool = False) -> str | None:
    if optional and (value is None or value == ""):
        return None
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise ModelUsageError("price_invalid", "单价请填写非负数，每百万 tokens 计价。")
    try:
        decimal = Decimal(str(value))
    except InvalidOperation:
        raise ModelUsageError("price_invalid", "单价请填写非负数，每百万 tokens 计价。") from None
    if not decimal.is_finite() or decimal < 0 or decimal > Decimal(1000000000) or decimal.as_tuple().exponent < -12:
        raise ModelUsageError("price_invalid", "单价请填写有效非负数，最多 12 位小数。")
    return format(decimal, "f")


class ModelUsageStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "model-usage.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10)
        except (OSError, sqlite3.Error):
            raise ModelUsageError("ledger_unavailable", "本机用量记录暂时无法打开。") from None
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS calls (
                    call_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
                    profile_id TEXT NOT NULL, provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                    api_style TEXT NOT NULL, task_type TEXT NOT NULL, outcome TEXT NOT NULL,
                    model_invoked INTEGER, http_status INTEGER, latency_ms INTEGER,
                    local_input_estimate INTEGER, input_tokens INTEGER, output_tokens INTEGER,
                    total_tokens INTEGER, cached_input_tokens INTEGER, reasoning_tokens INTEGER,
                    usage_status TEXT NOT NULL, estimated_cost_amount TEXT,
                    estimated_cost_currency TEXT, cost_status TEXT NOT NULL, price_version TEXT,
                    price_input_per_million TEXT, price_output_per_million TEXT,
                    price_cached_input_per_million TEXT
                );
                CREATE TABLE IF NOT EXISTS prices (
                    profile_id TEXT NOT NULL, provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                    currency TEXT NOT NULL, input_price_per_million TEXT NOT NULL,
                    output_price_per_million TEXT NOT NULL, cached_input_price_per_million TEXT,
                    version TEXT NOT NULL,
                    PRIMARY KEY (profile_id, provider_id, model_id)
                );
            """)
            return connection
        except sqlite3.Error:
            connection.close()
            raise ModelUsageError("ledger_unavailable", "本机用量记录暂时无法打开。") from None

    def _read(self, sql: str, parameters=()) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        connection = self._connect()
        try:
            return [dict(row) for row in connection.execute(sql, parameters)]
        except sqlite3.Error:
            raise ModelUsageError("ledger_read_failed", "本机用量记录暂时无法读取。") from None
        finally:
            connection.close()

    def list_calls(self, *, limit: int = 500) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100_000:
            raise ModelUsageError("limit_invalid", "查询数量不正确。")
        return self._read("SELECT * FROM calls ORDER BY occurred_at DESC, rowid DESC LIMIT ?", (limit,))

    def prices(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM prices ORDER BY profile_id, provider_id, model_id")

    def set_price(self, profile_id: str, provider_id: str, model_id: str, *, currency: str,
                  input_price_per_million: str, output_price_per_million: str,
                  cached_input_price_per_million: str | None = None) -> dict[str, Any]:
        if any(_safe_id(value) != value for value in (profile_id, provider_id, model_id)):
            raise ModelUsageError("profile_invalid", "请选择已保存的模型配置。")
        currency = currency.strip().upper() if isinstance(currency, str) else ""
        if not _CURRENCY.fullmatch(currency):
            raise ModelUsageError("currency_invalid", "币种请填写三位字母代码，例如 CNY 或 USD。")
        price = {"profile_id": profile_id, "provider_id": provider_id, "model_id": model_id, "currency": currency,
                 "input_price_per_million": _price(input_price_per_million),
                 "output_price_per_million": _price(output_price_per_million),
                 "cached_input_price_per_million": _price(cached_input_price_per_million, optional=True),
                 "version": uuid4().hex}
        connection = self._connect()
        try:
            with connection:
                connection.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?)", tuple(price.values()))
        except sqlite3.Error:
            raise ModelUsageError("price_save_failed", "本机价格配置未能保存。") from None
        finally:
            connection.close()
        return price

    def remove_price(self, profile_id: str, provider_id: str, model_id: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM prices WHERE profile_id=? AND provider_id=? AND model_id=?",
                                   (profile_id, provider_id, model_id))
        finally:
            connection.close()

    def record(self, metadata: UsageRequestMetadata, *, api_style: str, raw: bytes | None,
               outcome: str, model_invoked: bool, http_status: int | None, latency_ms: int | None) -> None:
        usage = _decode_usage(api_style, raw)
        core = [usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")]
        usage_status = "reported" if all(value is not None for value in core) else (
            "partial" if any(value is not None for value in core) else "unknown")
        call = dict(call_id=metadata.call_id, occurred_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    profile_id=_safe_id(metadata.profile_id), provider_id=_safe_id(metadata.provider_id),
                    model_id=_safe_id(metadata.model_id), api_style=api_style if api_style in {"responses", "chat_completions"} else "unknown",
                    task_type=metadata.task_type if metadata.task_type in TASK_TYPES else "unknown",
                    outcome=outcome if outcome in {"response_received", "failed", "cancelled", "not_sent"} else "failed",
                    model_invoked=int(model_invoked is True), http_status=_number(http_status, 599),
                    latency_ms=_number(latency_ms, 86_400_000), local_input_estimate=_number(metadata.local_input_estimate),
                    **usage, usage_status=usage_status, estimated_cost_amount=None, estimated_cost_currency=None,
                    cost_status="not_configured" if metadata.price_available else "price_unavailable",
                    price_version=None, price_input_per_million=None,
                    price_output_per_million=None, price_cached_input_per_million=None)
        if not isinstance(metadata.call_id, str) or not re.fullmatch(r"[0-9a-f]{32}", metadata.call_id):
            raise ModelUsageError("call_id_invalid", "本机用量记录编号不正确。")
        connection = self._connect()
        try:
            with connection:
                # The price was pinned when this outbound request was built.
                # Changes while a request is in flight cannot reprice that call.
                connection.execute("BEGIN IMMEDIATE")
                if metadata.price_snapshot is not None:
                    price = dict(metadata.price_snapshot)
                    if (not _CURRENCY.fullmatch(str(price.get("currency", "")))
                            or not re.fullmatch(r"[0-9a-f]{32}", str(price.get("version", "")))):
                        raise ModelUsageError("price_snapshot_invalid", "本机价格快照不正确。")
                    for key in ("input_price_per_million", "output_price_per_million", "cached_input_price_per_million"):
                        price[key] = _price(price.get(key), optional=key == "cached_input_price_per_million")
                    call.update(price_version=price["version"], estimated_cost_currency=price["currency"],
                                price_input_per_million=price["input_price_per_million"],
                                price_output_per_million=price["output_price_per_million"],
                                price_cached_input_per_million=price["cached_input_price_per_million"],
                                cost_status="usage_unknown")
                    if usage["input_tokens"] is not None and usage["output_tokens"] is not None:
                        cached_rate = price["cached_input_price_per_million"]
                        cached = usage["cached_input_tokens"]
                        if cached_rate is not None and cached is None:
                            call["cost_status"] = "cache_usage_unknown"
                        else:
                            cached = cached or 0
                            # A single configured input rate applies to all input unless a cache rate was entered.
                            with localcontext() as arithmetic:
                                arithmetic.prec = 48
                                amount = (Decimal(usage["input_tokens"]) * Decimal(price["input_price_per_million"])
                                          + Decimal(usage["output_tokens"]) * Decimal(price["output_price_per_million"]))
                                if cached_rate is not None:
                                    amount += Decimal(cached) * (Decimal(cached_rate) - Decimal(price["input_price_per_million"]))
                                call["estimated_cost_amount"] = format(amount / Decimal(1_000_000), "f")
                            call["cost_status"] = "estimated_from_configured_price"
                connection.execute(f"INSERT OR IGNORE INTO calls ({','.join(_FIELDS)}) VALUES ({','.join('?' for _ in _FIELDS)})",
                                   tuple(call[key] for key in _FIELDS))
        finally:
            connection.close()

    def summary(self) -> dict[str, Any]:
        rows = self._read("SELECT COUNT(*) AS request_count, SUM(usage_status='reported') AS actual_usage_request_count, "
                          "SUM(usage_status!='reported') AS unknown_usage_request_count, "
                          "SUM(local_input_estimate) AS local_estimated_input_tokens, SUM(input_tokens) AS input_tokens, "
                          "SUM(output_tokens) AS output_tokens, SUM(total_tokens) AS total_tokens FROM calls")
        result = rows[0] if rows else dict.fromkeys(("request_count", "actual_usage_request_count", "unknown_usage_request_count",
                                                   "local_estimated_input_tokens", "input_tokens", "output_tokens", "total_tokens"), 0)
        for key in ("request_count", "actual_usage_request_count", "unknown_usage_request_count"):
            result[key] = result[key] or 0
        costs = self._read("SELECT estimated_cost_currency, estimated_cost_amount FROM calls WHERE estimated_cost_amount IS NOT NULL")
        totals: dict[str, Decimal] = {}
        with localcontext() as arithmetic:
            arithmetic.prec = 48
            for row in costs:
                currency = row["estimated_cost_currency"]
                totals[currency] = totals.get(currency, Decimal(0)) + Decimal(row["estimated_cost_amount"])
        result["estimated_cost_totals"] = [{"currency": key, "amount": format(value, "f")} for key, value in sorted(totals.items())]
        with _FAILURE_LOCK:
            result["recording_failure_count"] = max(_FAILURES.get(_failure_key(self.root), 0),
                                                     _stored_failure_count(self.root))
        result["last_recording_error"] = "本机记录失败；已发送请求不会自动重发，累计用量可能不完整。" if result["recording_failure_count"] else None
        return result

    def export(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        calls = self._read("SELECT * FROM calls ORDER BY occurred_at, rowid")
        # Explicit projection prevents accidental export of later internal columns.
        payload = {"schema_version": "shchem.model-usage.v1", "cost_label": "按配置价估算",
                   "calls": [{key: call[key] for key in _FIELDS} for call in calls]}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                             prefix=".model-usage-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except OSError:
            raise ModelUsageError("ledger_export_failed", "用量记录未能导出。") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination


def record_transport_call(metadata: UsageRequestMetadata | None, *, api_style: str,
                          raw: bytes | None = None, outcome: str, model_invoked: bool,
                          http_status: int | None = None, latency_ms: int | None = None) -> None:
    """Fail softly after a send: recording trouble must never trigger a resend."""
    if metadata is None:
        return
    try:
        ModelUsageStore(metadata.root).record(metadata, api_style=api_style, raw=raw, outcome=outcome,
                                             model_invoked=model_invoked, http_status=http_status, latency_ms=latency_ms)
    except Exception:  # noqa: BLE001 - recording failure cannot cause a completed API call to be retried
        _note_failure(metadata.root)
