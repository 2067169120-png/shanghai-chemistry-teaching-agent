"""Reusable append-only foundation for Shanghai chemistry generation planning."""

from .contracts import (
    PROVIDER_PROFILE_ID,
    TASK_CARD_SCHEMA_VERSION,
    canonical_json_bytes,
    parse_json_object,
    validate_evidence_summaries,
    validate_task_card,
)
from .errors import (
    ContractError,
    IntegrityError,
    StateTransitionError,
    StoreConflictError,
    WorkbenchError,
)
from .preflight import evidence_preflight
from .provider import provider_registry_snapshot, resolve_provider_request
from .store import AppendOnlyWorkbenchStore, validate_relative_path

__all__ = [
    "AppendOnlyWorkbenchStore",
    "ContractError",
    "IntegrityError",
    "PROVIDER_PROFILE_ID",
    "StateTransitionError",
    "StoreConflictError",
    "TASK_CARD_SCHEMA_VERSION",
    "WorkbenchError",
    "canonical_json_bytes",
    "evidence_preflight",
    "parse_json_object",
    "provider_registry_snapshot",
    "resolve_provider_request",
    "validate_evidence_summaries",
    "validate_relative_path",
    "validate_task_card",
]

__version__ = "1.0.0"
