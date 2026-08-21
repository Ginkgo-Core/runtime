"""RK-01 -- Capability Bus (absorbs RK-05 result normalization).

The single lawful entry point (invariant 2). It validates, identifies,
deduplicates, hands off across one seam, normalizes the answer and projects it
for the caller.

It does not route, evaluate policy, execute or persist evidence.

Specification: ``Ginkgo-Core/ai-stack#39``; ``capability-runtime-kernel-v1.md``
section 5; test matrix in ``architecture/runtime/reference-slice.md`` section 5.
"""

from __future__ import annotations

from .admission import FORBIDDEN_FIELD_PREFIXES, admit, forbidden_fields_in
from .bus import CapabilityBus, CapabilityOutcome
from .downstream import (
    DeterministicDownstream,
    DownstreamOutcome,
    RouteView,
    RuntimeDownstream,
)
from .errors import REJECTION_CODES, BusError, rejection
from .identity import IdempotencyLedger, RequestIdentity, identify
from .normalization import WITHHELD_FROM_CALLER, caller_projection, normalize

__all__ = [
    "FORBIDDEN_FIELD_PREFIXES",
    "REJECTION_CODES",
    "WITHHELD_FROM_CALLER",
    "BusError",
    "CapabilityBus",
    "CapabilityOutcome",
    "DeterministicDownstream",
    "DownstreamOutcome",
    "IdempotencyLedger",
    "RequestIdentity",
    "RouteView",
    "RuntimeDownstream",
    "admit",
    "caller_projection",
    "forbidden_fields_in",
    "identify",
    "normalize",
    "rejection",
]
