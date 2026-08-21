"""RK-06 -- Evidence / Audit Writer.

Exactly one frozen ``Evidence v1`` record per completed request, plus one frozen
``Event v1`` per state transition. It never defines its own record, never writes
a secret, never promotes anything to Knowledge, and never writes a second record
for a replayed idempotency key.

Specification: ``Ginkgo-Core/ai-stack#43``.
"""

from __future__ import annotations

from .writer import (
    EVENT_VERSION,
    NEGATIVE_PROOFS,
    EvidenceLedger,
    EvidencePacket,
    EvidenceWriteError,
)

__all__ = [
    "EVENT_VERSION",
    "NEGATIVE_PROOFS",
    "EvidenceLedger",
    "EvidencePacket",
    "EvidenceWriteError",
]
