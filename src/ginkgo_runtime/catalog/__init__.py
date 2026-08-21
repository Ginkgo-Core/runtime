"""RK-07 -- Runtime Catalog.

An immutable, boot-time projection of GitHub architecture truth into an
in-process catalog, pinned to explicit commit SHAs.

It **reads, validates, normalises, projects, rejects and versions a snapshot.**

It **does not** route, choose a provider, choose an executor, execute, consult a
registry during a request, hold mutable state, or repair an invalid record. It
authorises nothing.

Specification: ``Ginkgo-Core/ai-stack#37``, and
``architecture/runtime/capability-runtime-kernel-v1.md`` section 5.
"""

from __future__ import annotations

from .authority import FrozenAuthority, load_frozen_authority
from .build import build_catalog, rejection_report
from .errors import (
    AuthorityUnavailableError,
    CatalogBuildError,
    SourceIntegrityError,
    SourceUnavailableError,
)
from .projection import AdmittedRecord, RejectedRecord, Rejection
from .snapshot import RuntimeCatalog

__all__ = [
    "AdmittedRecord",
    "AuthorityUnavailableError",
    "CatalogBuildError",
    "FrozenAuthority",
    "RejectedRecord",
    "Rejection",
    "RuntimeCatalog",
    "SourceIntegrityError",
    "SourceUnavailableError",
    "build_catalog",
    "load_frozen_authority",
    "rejection_report",
]
