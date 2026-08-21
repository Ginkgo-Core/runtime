"""The Bus's failure vocabulary, expressed in the frozen ``Error v1`` shape.

Two outcomes exist at the boundary, and the distinction is the reference slice's
own, not one invented here:

* a **rejection** -- the request was never admitted. ``reference-slice.md``
  section 5 says *"rejected, VALIDATION"* for N1-N8 and N13, and reserves the word
  ``BLOCKED`` for N9-N12, which are results. An inadmissible request cannot become
  a ``CapabilityResult v1`` without fabricating identity it does not have: the
  contract requires ``execution_id``, ``skill_id`` and ``skill_version`` as
  non-empty strings, and a request that failed its own schema has none of them.
  Inventing them to satisfy a shape would be exactly the dishonesty the kernel
  exists to prevent;
* a **result** -- the request was admitted, reached the downstream, and came back.
  That is a ``CapabilityResult v1``.

Both carry the frozen ``contracts/core/error.schema.json`` shape when they carry
an error at all, and both are validated against it before they leave.

**Nothing here may leak.** ``error.schema.json`` says it in its own description:
*"Secrets, credentials, raw endpoints and provider payloads MUST NOT appear in
message or details."* Reference slice N15 is the test. The Bus never copies a
caller-supplied value into an error message; it names the *field* and never
quotes the *value*, because the value is exactly what might be a credential.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["REJECTION_CODES", "BusError", "rejection"]

#: Every rejection code, with the frozen `error.schema.json` category it maps to
#: and the authority that puts it there. Codes match `^[A-Z0-9_]+$`, as the
#: contract requires.
REJECTION_CODES: dict[str, tuple[str, str]] = {
    "REQUEST_SCHEMA_VIOLATION": (
        "VALIDATION",
        "The request does not satisfy contracts/core/capability-request.schema.json "
        "v1 -- required set, additionalProperties: false, the capability-name "
        "pattern, an enum, a format or a length. Reference slice N1, N5, N6, N7, N8.",
    ),
    "CALLER_SELECTED_IMPLEMENTATION": (
        "VALIDATION",
        "The request carries a field naming an implementation: provider, adapter, "
        "binding, skill, tool, executor, workflow, endpoint, credential or a "
        "command surface. Invariants 17 and 18. Matching is by PREFIX, so "
        "provider_hint, endpoint_url and commands are caught. Reference slice "
        "N2, N3, N4.",
    ),
    "REQUEST_TOO_DEEPLY_NESTED": (
        "VALIDATION",
        "The request nests deeper than the Bus will walk. The forbidden-field scan "
        "and the idempotency fingerprint both recurse, so an unbounded document "
        "could take the boundary down. Rejected rather than partially scanned.",
    ),
    "UNSUPPORTED_CONTRACT_VERSION": (
        "VALIDATION",
        "capability.contract_version names a version this kernel does not "
        "implement. The pattern ^v[0-9]+$ admits it; the kernel does not. "
        "Reference slice N13.",
    ),
    "IDEMPOTENCY_CONFLICT": (
        "CONFLICT",
        "The idempotency_key was already used, with a different payload. The Bus "
        "neither replays the old result nor executes the new request. Reference "
        "slice N14.",
    ),
    "DOWNSTREAM_CONTRACT_VIOLATION": (
        "INTERNAL",
        "The internal routing interface returned something that is not a "
        "RuntimeExecutionResult v1, or a blocked decision carrying no identity the "
        "frozen CapabilityResult v1 requires. The Bus fails closed rather than "
        "fabricating the missing fields.",
    ),
    "EXECUTION_NOT_TERMINAL": (
        "INTERNAL",
        "The executor reported a non-terminal state. queued and running produce no "
        "CapabilityResult at all -- runtime-execution-result.schema.json says so.",
    ),
}


@dataclass(frozen=True)
class BusError:
    """One normalised error, in the frozen ``Error v1`` shape."""

    code: str
    message: str
    category: str
    retryable: bool
    details: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details is not None:
            body["details"] = dict(self.details)
        return body


def rejection(
    code: str, message: str, *, retryable: bool = False, details: Mapping[str, Any] | None = None
) -> BusError:
    """Build a rejection from the declared vocabulary.

    The category comes from :data:`REJECTION_CODES`, never from the call site, so
    a code cannot acquire two categories in two places.
    """
    if code not in REJECTION_CODES:
        raise KeyError(
            "%r is not a declared rejection code; adding one is a contract change "
            "and is reviewed as one" % code
        )
    category, _ = REJECTION_CODES[code]
    return BusError(
        code=code, message=message, category=category, retryable=retryable, details=details
    )
