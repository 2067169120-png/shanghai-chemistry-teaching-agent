"""Closed provider registry: requested configuration metadata only."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .contracts import PROVIDER_PROFILE_ID, require_exact_keys
from .errors import ContractError

_PROFILE = {
    "provider_profile_id": PROVIDER_PROFILE_ID,
    "requested_model": "gpt-5.6-sol",
    "requested_reasoning_effort": "xhigh",
    "configuration_status": "requested_configuration",
    "platform_actual_reported": False,
    "signed": False,
    "human_reviewed": False,
    "publication_allowed": False,
    "official": False,
}


def resolve_provider_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve an exact profile request and reject every caller override."""

    require_exact_keys(request, frozenset({"provider_profile_id"}), "provider_request")
    if request["provider_profile_id"] != PROVIDER_PROFILE_ID:
        raise ContractError("unknown or unauthorized provider profile")
    return copy.deepcopy(_PROFILE)


def provider_registry_snapshot() -> dict[str, Any]:
    """Return the immutable registry metadata without accepting caller fields."""

    return {
        "schema_version": "generation_provider_registry_v1",
        "profiles": [copy.deepcopy(_PROFILE)],
        "registry_closed": True,
        "requested_configuration_only": True,
        "human_reviewed": False,
        "publication_allowed": False,
        "official": False,
    }
