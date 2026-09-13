"""Fail-closed exception types for the generation workbench."""


class WorkbenchError(Exception):
    """Base class for all workbench failures."""


class ContractError(WorkbenchError, ValueError):
    """An input does not satisfy an exact workbench contract."""


class IntegrityError(WorkbenchError):
    """An immutable record or hash chain failed verification."""


class StoreConflictError(WorkbenchError, FileExistsError):
    """An append-only destination already exists or changed concurrently."""


class StateTransitionError(WorkbenchError):
    """A run transition is invalid or not authorized by its preflight."""
