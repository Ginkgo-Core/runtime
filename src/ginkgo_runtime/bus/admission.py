"""Inbound admission: the schema, then caller-agnosticism, then the version.

Three gates, in that order, and every one of them fails closed.

**1. The frozen schema itself.** The request is validated against
``contracts/core/capability-request.schema.json`` v1 as loaded from the pinned
snapshot -- not against a Python restatement of it. That covers the required set,
``additionalProperties: false``, the capability-name pattern, the
``fallback_policy`` and ``data_classification`` enums, the ``max_cost`` shape and
the ``deadline`` format. Reference slice N1, N5, N6, N7, N8.

**2. Caller-agnosticism, by prefix, recursively.** Invariants 17 and 18: *"A
caller never receives, selects or restricts a skill, adapter, provider, tool, raw
endpoint, credential or secret."*

Two details matter and both are easy to get wrong:

*Prefix, not exact name.* ``ai-stack#39`` is explicit: *"an exact-name check
passes ``provider_hint``, ``endpoint_url`` and ``commands``"*, and calls that a
real gap in an existing validator. Reference slice N3 is the test.

*Recursively, not top-level.* The frozen request is
``additionalProperties: false`` at the top, so a top-level ``provider`` is already
dead on gate 1. The field that is actually open is ``input``, declared
``additionalProperties: true`` -- business payload has to be. So
``input: {provider_hint: "amazon"}`` sails through the schema, and a top-level-only
scan would admit it. The scan walks the whole request.

Keys only, never values. ``input: {"note": "ask the provider"}`` is business
prose. It is the *field name* that would carry implementation authority, and
treating a value as one would reject ordinary text while catching nothing.

Case-insensitive, so ``providerHint`` and ``Provider-Id`` do not slip past a
lowercase comparison.

**3. The contract version.** ``^v[0-9]+$`` admits ``v2``; this kernel implements
``v1`` and says so by reading ``x-ginkgo-contract-version`` off the schema.
Reference slice N13.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..contracts import FrozenContracts
from .errors import BusError, rejection

__all__ = ["FORBIDDEN_FIELD_PREFIXES", "MAX_DOCUMENT_DEPTH", "admit", "forbidden_fields_in"]

#: Deepest nesting the Bus will walk in a request.
#:
#: Not a contract value -- ``input`` is ``additionalProperties: true`` and the
#: frozen schema sets no depth bound, so this is a boundary safety limit the
#: kernel adds. It exists because the forbidden-field scan and the idempotency
#: fingerprint both recurse: without it a caller could hand the Bus a legal
#: document nested a few thousand levels deep and take the boundary down with a
#: ``RecursionError``. Found by adversarial review.
#:
#: Fail-closed: a deeper document is REJECTED, never silently truncated to the
#: limit, because a truncated scan is a forbidden-field check that did not finish.
#: 64 is far past anything the frozen contract nests (three levels) and past any
#: plausible business payload.
MAX_DOCUMENT_DEPTH = 64

#: Transcribed from ``ai-stack#39`` and ``contracts/runtime/README.md``, reduced to
#: the shortest distinguishing prefixes: ``provider`` already covers
#: ``provider_id`` and ``provider_hint``, ``skill`` covers ``skill_id`` and
#: ``skill_version``, ``command`` covers ``commands``.
#:
#: This list is prose in the architecture, not a value in any schema, so it is
#: transcribed here with its citation -- the same treatment RK-07 gives the
#: lifecycle ceiling matrix. ``shell_command`` is kept in full because it does not
#: start with ``command``.
FORBIDDEN_FIELD_PREFIXES: tuple[str, ...] = (
    "access_token",
    "adapter",
    "api_key",
    "args",
    "binding",
    "command",
    "cookie",
    "credential",
    "endpoint",
    "executor",
    "node_id",
    "path",
    "provider",
    "refresh_token",
    "script",
    "secret",
    "shell_command",
    "skill",
    "tool",
    "workflow_id",
)


def document_depth(node: Any, _depth: int = 0) -> int:
    """Deepest nesting level in ``node``, stopping as soon as the limit is passed.

    Iterative rather than recursive: measuring the depth of a document that is
    too deep must not itself overflow the stack.
    """
    deepest = _depth
    stack: list[tuple[Any, int]] = [(node, _depth)]
    while stack:
        current, level = stack.pop()
        if level > deepest:
            deepest = level
            if deepest > MAX_DOCUMENT_DEPTH:
                return deepest
        if isinstance(current, Mapping):
            stack.extend((value, level + 1) for value in current.values())
        elif isinstance(current, list | tuple):
            stack.extend((item, level + 1) for item in current)
    return deepest


def _walk(node: Any, pointer: str):
    """Yield ``(pointer, key)`` for every mapping key anywhere in the document.

    Only ever called after :func:`document_depth` has cleared the request, so the
    recursion here is bounded by :data:`MAX_DOCUMENT_DEPTH`.
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = "%s/%s" % (pointer, key)
            yield here, str(key)
            yield from _walk(value, here)
    elif isinstance(node, list | tuple):
        for index, item in enumerate(node):
            yield from _walk(item, "%s/%d" % (pointer, index))


def forbidden_fields_in(request: Any) -> list[tuple[str, str]]:
    """Every ``(pointer, matched_prefix)`` where a field name claims implementation authority."""
    found: list[tuple[str, str]] = []
    for pointer, key in _walk(request, ""):
        lowered = key.lower()
        for prefix in FORBIDDEN_FIELD_PREFIXES:
            if lowered.startswith(prefix):
                found.append((pointer, prefix))
                break
    return sorted(found)


def admit(request: Any, contracts: FrozenContracts) -> BusError | None:
    """Return the rejection, or ``None`` when the request is admissible."""
    if not isinstance(request, Mapping):
        return rejection(
            "REQUEST_SCHEMA_VIOLATION",
            "a CapabilityRequest must be an object, not %s" % type(request).__name__,
        )

    depth = document_depth(request)
    if depth > MAX_DOCUMENT_DEPTH:
        return rejection(
            "REQUEST_TOO_DEEPLY_NESTED",
            "the request nests deeper than %d levels; the Bus refuses to walk it "
            "rather than risk an incomplete scan" % MAX_DOCUMENT_DEPTH,
            details={"limit": MAX_DOCUMENT_DEPTH},
        )

    violations = contracts.capability_request.violations(request)
    if violations:
        return rejection(
            "REQUEST_SCHEMA_VIOLATION",
            "the request does not satisfy CapabilityRequest v1 at: %s"
            % ", ".join(v.locator for v in violations[:8]),
            # Pointers and keywords only. A violation's offending VALUE is never
            # copied in -- it is exactly the thing that might be a credential
            # (error.schema.json; reference slice N15).
            details={
                "violations": [
                    {"keyword": v.keyword, "pointer": v.pointer} for v in violations
                ],
                "schema_ref": "contracts/core/capability-request.schema.json",
            },
        )

    forbidden = forbidden_fields_in(request)
    if forbidden:
        return rejection(
            "CALLER_SELECTED_IMPLEMENTATION",
            "a caller never selects or restricts an implementation (invariants 17 "
            "and 18); %d field(s) name one: %s"
            % (len(forbidden), ", ".join(pointer for pointer, _ in forbidden)),
            details={
                "fields": [
                    {"matched_prefix": prefix, "pointer": pointer}
                    for pointer, prefix in forbidden
                ],
                "matching": "prefix",
            },
        )

    declared = request["capability"]["contract_version"]
    if declared != contracts.supported_contract_version:
        return rejection(
            "UNSUPPORTED_CONTRACT_VERSION",
            "capability.contract_version %r is not implemented by this kernel, "
            "which implements %r" % (declared, contracts.supported_contract_version),
            details={"declared": declared, "supported": contracts.supported_contract_version},
        )

    return None
