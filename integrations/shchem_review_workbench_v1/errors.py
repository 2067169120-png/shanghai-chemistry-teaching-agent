"""Stable fail-closed errors for the isolated theme review ledger."""

from __future__ import annotations


class ThemeReviewError(Exception):
    """Base error with a stable gateway-facing code and HTTP status."""

    code = "theme_review_error"
    status_code = 500

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class ContractError(ThemeReviewError, ValueError):
    code = "theme_review_contract_invalid"
    status_code = 400


class AuthorizationError(ThemeReviewError):
    code = "theme_review_principal_forbidden"
    status_code = 403


class NotFoundError(ThemeReviewError):
    code = "theme_review_not_found"
    status_code = 404


class ConflictError(ThemeReviewError):
    code = "theme_review_conflict"
    status_code = 409


class RevisionConflictError(ConflictError):
    code = "theme_review_revision_conflict"


class IdempotencyConflictError(ConflictError):
    code = "theme_review_idempotency_conflict"


class IntegrityError(ThemeReviewError):
    code = "theme_review_integrity_failed"
    status_code = 500

