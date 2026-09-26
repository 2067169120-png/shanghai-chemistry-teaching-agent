"""Versioned student-learning integration surface.

Synthetic regression and private machine-only real-student candidates use
separate claim scopes. Real diagnosis runs only after local privacy/media,
canonical parent-chain, live controller, preflight, and machine-governance
receipts all pass; every missing or mismatched field fails closed.
"""

from .domain import (
    AccessDenied,
    AggregationThresholdError,
    ControllerReceiptAdapter,
    FailClosedCentralAdapter,
    FormalContentContractError,
    FixtureContentProvider,
    LearningEngine,
    PrivacyViolation,
    RealDiagnosisBlocked,
    StudentScope,
    StudentStore,
    audit_weekly_schedule,
    build_public_aggregate,
    latest_grade_progress,
    validate_formal_content_export,
)

__all__ = [
    "AccessDenied",
    "AggregationThresholdError",
    "ControllerReceiptAdapter",
    "FailClosedCentralAdapter",
    "FormalContentContractError",
    "FixtureContentProvider",
    "LearningEngine",
    "PrivacyViolation",
    "RealDiagnosisBlocked",
    "StudentScope",
    "StudentStore",
    "audit_weekly_schedule",
    "build_public_aggregate",
    "latest_grade_progress",
    "validate_formal_content_export",
]
