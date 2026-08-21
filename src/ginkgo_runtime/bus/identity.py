"""Request identity and idempotency.

``idempotency_key`` exists so a caller can retry without executing twice. That
requires answering one question precisely: **what counts as "the same request"?**

The fingerprint covers the *semantic* payload only:

.. code-block:: text

    IN   capability · input · constraints · governance · evidence · caller
    OUT  request_id · correlation_id · task_id · execution_id · idempotency_key

``request_id`` is excluded, and that exclusion is the whole design. A retry
legitimately carries a **new** ``request_id`` -- that is what makes it a distinct
request. Hashing it would make every retry a payload mismatch, so every retry
would return ``CONFLICT`` and the mechanism would protect nothing while appearing
to work. ``correlation_id`` and ``task_id`` are tracing and workflow handles for
the same reason.

``caller`` **is** included. Within one workspace, a second actor reusing a key
someone else claimed is a collision, not a retry, and the fail-closed answer is
``CONFLICT``.

The ledger is keyed by ``(tenant_id, workspace_id, idempotency_key)``. An
unscoped key space would let one tenant's key collide with another's -- either
leaking a result across a tenant boundary or denying a legitimate request.

**This ledger is in-process and deliberately not durable.** Invariant 9 puts
runtime state in PostgreSQL, and that is where a real deployment's ledger belongs.
:class:`IdempotencyLedger` is the interface RK-01 needs and an implementation
honest enough to test against; persisting it is not RK-01's decision to make.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..canonical import fingerprint

__all__ = ["FINGERPRINT_FIELDS", "IdempotencyLedger", "LedgerEntry", "RequestIdentity", "identify"]

#: The fields that make two requests "the same request". See the module docstring
#: for why ``request_id`` is not among them.
FINGERPRINT_FIELDS: tuple[str, ...] = (
    "caller",
    "capability",
    "constraints",
    "evidence",
    "governance",
    "input",
)

#: Excluded, and named explicitly so the omission reads as a decision rather than
#: an oversight.
VOLATILE_FIELDS: tuple[str, ...] = (
    "correlation_id",
    "execution_id",
    "idempotency_key",
    "request_id",
    "task_id",
)


@dataclass(frozen=True)
class RequestIdentity:
    """Who is asking, for what, and under which retry key."""

    request_id: str
    correlation_id: str
    idempotency_key: str
    tenant_id: str
    workspace_id: str
    payload_fingerprint: str

    @property
    def ledger_key(self) -> tuple[str, str, str]:
        return (self.tenant_id, self.workspace_id, self.idempotency_key)


def identify(request: Mapping[str, Any]) -> RequestIdentity:
    """Derive identity from an already-admitted request.

    Called only after admission, so every field it reads is present and typed --
    the frozen schema guaranteed that.
    """
    # `fingerprint`, not `hash_canonical`: `input` is caller data and may legally
    # contain floats. The catalog's stricter primitive would raise on a price.
    payload = {name: request[name] for name in FINGERPRINT_FIELDS if name in request}
    return RequestIdentity(
        request_id=request["request_id"],
        correlation_id=request["correlation_id"],
        idempotency_key=request["idempotency_key"],
        tenant_id=request["caller"]["tenant_id"],
        workspace_id=request["caller"]["workspace_id"],
        payload_fingerprint=fingerprint(payload),
    )


@dataclass(frozen=True)
class LedgerEntry:
    """What a completed request left behind for its retries."""

    payload_fingerprint: str
    result: Mapping[str, Any]


@dataclass
class IdempotencyLedger:
    """In-process record of completed requests, keyed per tenant and workspace."""

    _entries: dict[tuple[str, str, str], LedgerEntry] = field(default_factory=dict, repr=False)

    def lookup(self, identity: RequestIdentity) -> LedgerEntry | None:
        return self._entries.get(identity.ledger_key)

    def conflicts(self, identity: RequestIdentity) -> bool:
        """``True`` when the key is taken and the payload does not match."""
        entry = self.lookup(identity)
        return entry is not None and entry.payload_fingerprint != identity.payload_fingerprint

    def remember(self, identity: RequestIdentity, result: Mapping[str, Any]) -> None:
        """Record a completed result. A key is written once and never overwritten.

        Overwriting would let a later call silently change what an earlier retry
        would have returned -- which is the opposite of what the key promises.
        """
        self._entries.setdefault(
            identity.ledger_key,
            LedgerEntry(payload_fingerprint=identity.payload_fingerprint, result=result),
        )

    def __len__(self) -> int:
        return len(self._entries)
