"""The return leg: ``RuntimeExecutionResult`` in, ``CapabilityResult v1`` out.

RK-01 absorbs RK-05 on purpose. ``capability-runtime-kernel-v1.md`` section 3:
splitting normalisation out *"creates a hop where ``routing.adapter_id`` and
``routing.provider`` can leak into an unprivileged projection -- precisely the
risk invariant 17 exists to prevent."* So the code that builds the envelope and
the code that projects it for the caller are the same code, in this file.

The status mapping is **read, not written.** ``runtime-execution-result.schema.json``
carries it under ``x-ginkgo-status-mapping`` and says in its own description that
it *"is applied by the Capability Bus (RK-01), not by the executor. Leaving that
mapping undeclared is a known defect elsewhere in the ecosystem."* A table here
would be a second copy of it.

Two guards sit on top of the mapping:

**Non-terminal states emit nothing.** ``queued`` and ``running`` are not results.
The schema says so and the Bus refuses to invent one.

**``independent_validation`` can veto ``SUCCESS``.** The contract is blunt about
why: *"An executor's self-reported ``succeeded`` is not by itself sufficient
evidence of success. When required is true and satisfied is not true, RK-01 must
not emit SUCCESS."* The veto applies to the ``succeeded`` branch, because that is
the only branch whose claim it disputes -- turning a ``cancelled`` into an
``ERROR`` would discard true information rather than withhold an unproven claim.

What it becomes instead is a kernel decision, since the contract states only what
it must *not* be. ``ERROR`` is the honest answer: ``PARTIAL`` would still assert
partial success on the same unverified evidence, and ``BLOCKED`` would claim the
work never proceeded when it did.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..contracts import FrozenContracts
from ..contracts.frozen import NON_TERMINAL_STATES
from .downstream import DownstreamOutcome
from .errors import BusError, rejection

__all__ = [
    "INDEPENDENT_VALIDATION_ERROR_CODE",
    "WITHHELD_FROM_CALLER",
    "caller_projection",
    "normalize",
]

#: ``capability-result.schema.json`` on its own ``routing`` block: *"Caller-facing
#: projections MUST NOT expose adapter_id/provider unless an explicitly authorized
#: internal audit surface requires them."* Reference slice N16 asks for them
#: **absent**, not null -- and null would be worse than absent, because an honest
#: null already means "no adapter exists" for a deterministic local skill.
#: Conflating "withheld" with "not applicable" destroys the distinction.
WITHHELD_FROM_CALLER = ("adapter_id", "provider")

INDEPENDENT_VALIDATION_ERROR_CODE = "INDEPENDENT_VALIDATION_NOT_SATISFIED"


def _independent_validation_vetoes(execution: Mapping[str, Any]) -> bool:
    block = execution.get("independent_validation")
    if not isinstance(block, Mapping) or block.get("required") is not True:
        return False
    return block.get("satisfied") is not True


def normalize(
    request: Mapping[str, Any],
    outcome: DownstreamOutcome,
    contracts: FrozenContracts,
) -> tuple[dict[str, Any] | None, BusError | None]:
    """Build the ``CapabilityResult v1``, or the rejection that replaces it.

    Returns ``(result, None)`` or ``(None, error)``. Never both, never neither.
    """
    decision = outcome.decision
    execution = outcome.execution

    if not decision.routed or execution is None:
        # A blocked decision is a real outcome, but `CapabilityResult v1` requires
        # `skill_id`, `skill_version` and `execution_id` as non-empty strings, and
        # a decision that resolved to nothing has none of them. Fabricating them
        # would be an invented identity. See downstream.py for the full note --
        # N9-N12 belong to #40 and #41.
        return None, rejection(
            "DOWNSTREAM_CONTRACT_VIOLATION",
            "the routing interface returned outcome %r with no execution; RK-01 "
            "does not fabricate the skill and execution identity CapabilityResult "
            "v1 requires" % decision.outcome,
            details={"outcome": decision.outcome, "routing_reason": decision.routing_reason},
        )

    violations = contracts.runtime_execution_result.violations(execution)
    if violations:
        return None, rejection(
            "DOWNSTREAM_CONTRACT_VIOLATION",
            "the routing interface returned a document that is not a "
            "RuntimeExecutionResult v1, at: %s" % ", ".join(v.locator for v in violations[:6]),
            details={
                "violations": [{"keyword": v.keyword, "pointer": v.pointer} for v in violations],
                "schema_ref": "contracts/runtime/runtime-execution-result.schema.json",
            },
        )

    state = execution["state"]
    if state in NON_TERMINAL_STATES:
        return None, rejection(
            "EXECUTION_NOT_TERMINAL",
            "executor state %r is not terminal and produces no CapabilityResult" % state,
            details={"state": state},
        )

    status = contracts.status_mapping.status_for(state, bool(execution.get("partial")))
    error = execution.get("error")

    if status in ("SUCCESS", "PARTIAL") and _independent_validation_vetoes(execution):
        status = "ERROR"
        block = execution["independent_validation"]
        error = {
            "category": "INTERNAL",
            "code": INDEPENDENT_VALIDATION_ERROR_CODE,
            "message": (
                "the executor reported %r but required independent validation is "
                "not satisfied; a self-reported success is not sufficient evidence "
                "of success" % state
            ),
            "retryable": False,
            "details": {
                "required": True,
                "satisfied": block.get("satisfied"),
                "validator_ref": block.get("validator_ref"),
            },
        }

    if not decision.skill_id or not decision.skill_version:
        return None, rejection(
            "DOWNSTREAM_CONTRACT_VIOLATION",
            "the routing decision is ROUTED but names no skill; CapabilityResult "
            "v1 requires skill_id and skill_version and RK-01 invents neither",
            details={"outcome": decision.outcome},
        )

    result: dict[str, Any] = {
        "approval": dict(decision.approval),
        "capability": dict(request["capability"]),
        "correlation_id": request["correlation_id"],
        "cost_events": [dict(event) for event in decision.cost_events],
        "error": error if error is not None else None,
        "evidence_refs": list(decision.evidence_refs),
        "execution_id": execution["execution_id"],
        "external_gates": [dict(gate) for gate in decision.external_gates],
        "request_id": request["request_id"],
        "routing": {
            "adapter_id": decision.adapter_id,
            "domain": decision.domain or request["capability"]["name"].split(".", 1)[0],
            "fallback_used": bool(decision.fallback_used),
            "provider": decision.provider,
            "routing_reason": decision.routing_reason,
        },
        "skill_id": decision.skill_id,
        "skill_version": decision.skill_version,
        "status": status,
    }
    if "output" in execution:
        result["output"] = execution["output"]

    # The Bus validates what it built. A boundary that emits an envelope it never
    # checked is asking the caller to discover the defect.
    self_violations = contracts.capability_result.violations(result)
    if self_violations:
        return None, rejection(
            "DOWNSTREAM_CONTRACT_VIOLATION",
            "the normalized result does not satisfy CapabilityResult v1 at: %s"
            % ", ".join(v.locator for v in self_violations[:6]),
            details={
                "violations": [
                    {"keyword": v.keyword, "pointer": v.pointer} for v in self_violations
                ]
            },
        )
    return result, None


def caller_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    """The unprivileged caller's view: routing provenance withheld.

    The projection is deliberately **not** a ``CapabilityResult v1``: that schema
    lists ``adapter_id`` and ``provider`` in ``routing.required``, so removing them
    makes the document invalid against it. That is the contract instructing its
    own projection, not a deviation from it -- and reference slice N16 asks for the
    fields to be *absent*.
    """
    projected = dict(result)
    routing = dict(projected.get("routing") or {})
    for field_name in WITHHELD_FROM_CALLER:
        routing.pop(field_name, None)
    projected["routing"] = routing
    return projected
