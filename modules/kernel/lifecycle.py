"""Slice 3 Handoff, Artifact, Approval, and append-audited lifecycle authority."""
from __future__ import annotations

from ._approval_repository import ApprovalRepositoryMixin
from ._artifact_repository import ArtifactRepositoryMixin
from ._handoff_repository import HandoffRepositoryMixin
from ._lifecycle_base import (
    ApprovalAuthorization,
    ApprovalAuthorizationError,
    ApprovalRecord,
    ArtifactIntegrityError,
    ArtifactRecord,
    DomainAuditEvent,
    HandoffRecord,
    LifecycleConflictError,
    LifecycleRecordNotFoundError,
    LifecycleRepositoryBase,
    LifecycleRepositoryError,
    RECORDS_SCHEMA_VERSION,
)


class LifecycleRepository(
    HandoffRepositoryMixin,
    ArtifactRepositoryMixin,
    ApprovalRepositoryMixin,
):
    """Single public repository for all Kernel Slice 3 lifecycle records."""


__all__ = [
    "ApprovalAuthorization",
    "ApprovalAuthorizationError",
    "ApprovalRecord",
    "ArtifactIntegrityError",
    "ArtifactRecord",
    "DomainAuditEvent",
    "HandoffRecord",
    "LifecycleConflictError",
    "LifecycleRecordNotFoundError",
    "LifecycleRepository",
    "LifecycleRepositoryBase",
    "LifecycleRepositoryError",
    "RECORDS_SCHEMA_VERSION",
]
