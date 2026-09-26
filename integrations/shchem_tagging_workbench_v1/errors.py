"""Fail-closed error types for the isolated tagging patch workbench."""

from __future__ import annotations


class TagPatchError(Exception):
    """Base class for tagging-workbench failures."""


class ContractError(TagPatchError, ValueError):
    """Caller input did not satisfy the closed patch contract."""


class StoreConflictError(TagPatchError):
    """Optimistic-concurrency or append-only conflict (HTTP 409 semantics)."""

    http_status = 409
    status_code = 409


class IntegrityError(TagPatchError):
    """A committed or in-flight immutable artifact failed verification."""

