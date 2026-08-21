"""RK-04 -- the Policy + Lifecycle Gate. One pure function.

The component that makes the kernel fail-closed, and the **only** place invariant
16 (``FEATURE`` never executes in production) and invariant 5 (approval and
external gate are independent) are enforced.

.. code-block:: text

    catalog record + execution mode + policy context
      + approval state + external gate state + deadline/cost context
        -> ALLOW | BLOCK  +  structured reason  +  decision ref/hash

**Pure.** No mutable state, no API, no clock, no I/O. Same inputs, same verdict,
same hash -- which is what lets a decision be pinned into a routing record and
re-derived after a restart.

What it does not do
-------------------

*It holds no state.* Approval and gate **state** live in PostgreSQL (invariant 9);
the catalog carries *declarations*, not state. Both arrive as inputs.

*It mutates nothing.* It never transitions an approval and never clears a gate.
It reads two independent state machines and combines their answers.

*It does not evaluate business policy.* The policy verdict arrives already
decided, as `ai-stack#41` describes it -- "policy context" is an input. RK-04
records that verdict and adds the lifecycle, approval and gate decisions that are
its own. It never overturns a denial.

*It does not re-derive knowledge-corpus eligibility.* `#19` owns that.

*It accepts no caller override.* ``caller_may_override`` is false and is not a
switch.

Vocabulary
----------

Two words are easy to conflate and mean different things here. ``verdict`` is the
gate's overall answer, ``ALLOW`` or ``BLOCK``. ``policy_decision`` is the value
that goes into ``RuntimeRouteDecision.policy.decision``, whose frozen enum is
``ALLOW | DENY``. They differ on purpose: a record blocked by its lifecycle gets
``verdict: BLOCK`` while its ``policy_decision`` stays ``ALLOW``, because no
policy denied anything. Collapsing them would record a policy denial that never
happened.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..canonical import CanonicalisationError, fingerprint
from ..contracts import FrozenContracts
from . import reasons as vocabulary
from .matrix import CALLER_TRAFFIC_MODES, CEILINGS, DENIED, PERMITTED_GOVERNED, cell

__all__ = [
    "ALGORITHM_VERSION",
    "ALLOW",
    "BLOCK",
    "ApprovalState",
    "ConstraintContext",
    "ExternalGateState",
    "GateDecision",
    "GateRequest",
    "PolicyContext",
    "evaluate",
]

ALLOW = "ALLOW"
BLOCK = "BLOCK"

#: The frozen ``policy.decision`` enum on ``RuntimeRouteDecision``.
POLICY_ALLOW = "ALLOW"
POLICY_DENY = "DENY"

#: Bump when the gate's *meaning* changes. Part of the decision hash, so a silent
#: change in what the gate permits cannot reuse an old hash.
ALGORITHM_VERSION = "ginkgo.policy-lifecycle-gate.v1"

#: The only approval states that do not block. Kept independent of the gate set
#: by construction -- invariant 5.
PASSING_APPROVAL_STATES = ("NOT_REQUIRED", "APPROVED")

#: The only external gate state that does not block. ``UNKNOWN`` blocks: a gate
#: whose state is unknown is exactly the case fail-closed exists for.
PASSING_GATE_STATE = "CLEAR"

#: Not accepted, in any value. RK-07 already rejects records carrying it; this is
#: the second reading, because RK-04 is the component that must not trust.
LEGACY_APPROVAL_FIELD = "human_approval"


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyContext:
    """The already-decided policy verdict, plus the ceilings it declares."""

    policy_id: str
    policy_version: str
    decision: str
    #: Pinned policy state. Hashed into the decision; never carried in cleartext
    #: and never reaching a caller.
    snapshot: Mapping[str, Any] = field(default_factory=dict)
    #: Asserted, not offered. A context arriving with this true is refused.
    caller_may_override: bool = False
    max_deadline: str | None = None
    max_cost: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ApprovalState:
    """Runtime approval state, read from PostgreSQL by the host and passed in."""

    required: bool = False
    state: str = "NOT_REQUIRED"
    refs: Sequence[str] = ()
    #: False when the ``APPROVED`` came from a *domain* decision rather than the
    #: governance Approval contract. `commerce.product.evaluate` is the case
    #: `ai-stack#41` names.
    decision_is_governance_approval: bool = True


@dataclass(frozen=True)
class ExternalGateState:
    """Runtime gate state. A different machine from approval, with its own enum."""

    gate_id: str
    state: str
    gate_type: str | None = None


@dataclass(frozen=True)
class ConstraintContext:
    """The caller's provider-agnostic constraints."""

    deadline: str | None = None
    max_cost: Mapping[str, Any] | None = None
    data_classification: str | None = None


@dataclass(frozen=True)
class GateRequest:
    """Everything the gate reads. Nothing else is in scope."""

    record: Mapping[str, Any]
    execution_mode: str
    policy: PolicyContext
    approval: ApprovalState = field(default_factory=ApprovalState)
    external_gates: Sequence[ExternalGateState] = ()
    constraints: ConstraintContext = field(default_factory=ConstraintContext)
    certification_authorization_ref: str | None = None


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    """``ALLOW | BLOCK``, why, and the reference that pins it."""

    verdict: str
    #: The first matching reason code, or ``None`` on ``ALLOW``.
    reason_code: str | None
    #: Human-readable, drawn from the vocabulary and the concrete values.
    reason: str
    #: ``BLOCKED_*`` for the Domain Router to record. ``None`` on ``ALLOW``.
    outcome: str | None
    #: What goes into ``RuntimeRouteDecision.policy.decision``. See the module
    #: docstring: this is not the same question as ``verdict``.
    policy_decision: str
    policy_id: str
    policy_version: str
    policy_snapshot_hash: str
    decision_inputs_hash: str
    decision_hash: str
    decision_ref: str
    algorithm_version: str
    #: The effective ceilings RK-08A then enforces: the most restrictive of what
    #: the caller asked for and what policy permits.
    deadline_ceiling: str | None
    cost_ceiling: Mapping[str, Any] | None
    #: Lifecycle as the gate read it, for the router to record verbatim.
    lifecycle: Mapping[str, Any]

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW


# ---------------------------------------------------------------------------
# the function
# ---------------------------------------------------------------------------


def _numeric(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _malformed(request: GateRequest) -> str | None:
    """Input the gate cannot read at all. Checked before anything else.

    RK-04's inputs come from the kernel host, not from a caller, so these are
    programming errors rather than attack surface -- but this is the component
    `ai-stack#41` calls *"the component that makes the kernel fail-closed"*, and
    raising is not failing closed. A crash produces no decision, so a caller sees
    an internal error where a ``BLOCK`` belongs.
    """
    if not isinstance(request.record, Mapping):
        return "the catalog record is %s, not a mapping" % type(request.record).__name__

    for gate in request.external_gates:
        if not isinstance(gate, ExternalGateState):
            return "an external gate is %s, not an ExternalGateState" % type(gate).__name__

    for label, cost in (
        ("the request", request.constraints.max_cost),
        ("the policy ceiling", request.policy.max_cost),
    ):
        if cost is None:
            continue
        if not isinstance(cost, Mapping):
            return "%s cost ceiling is %s, not a mapping" % (label, type(cost).__name__)
        if not _numeric(cost.get("amount")):
            return "%s cost ceiling has a non-numeric or absent amount" % label

    try:
        fingerprint(dict(request.policy.snapshot))
    except CanonicalisationError as exc:
        return "the policy snapshot cannot be hashed: %s" % exc

    return None


def _lifecycle_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """Read the declaration, fail-closed on anything absent.

    A missing ``lifecycle_status`` is read as ``FEATURE`` -- `ai-stack#41` says
    so, and it is section 4 invariant 8. A missing eligibility flag is ``false``:
    section 4 invariant 5, *"a missing or unreadable production_eligible is
    false"*.
    """
    if not isinstance(record, Mapping):
        # Reported by MALFORMED_GATE_INPUT; this only has to not raise on the way.
        return {"status": "FEATURE", "routing_eligible": False, "production_eligible": False}
    status = record.get("lifecycle_status")
    routing = record.get("routing")
    routing = routing if isinstance(routing, Mapping) else {}
    return {
        "status": status if isinstance(status, str) and status else "FEATURE",
        "routing_eligible": routing.get("routing_eligible") is True,
        "production_eligible": routing.get("production_eligible") is True,
    }


def _cost_ceiling(request: GateRequest) -> tuple[Mapping[str, Any] | None, str | None]:
    """The more restrictive of the caller's and the policy's cost ceiling.

    Different currencies are refused rather than converted. An implicit rate
    would put an invented number into a ceiling RK-08A enforces.
    """
    asked = request.constraints.max_cost
    permitted = request.policy.max_cost
    if asked is None:
        return (dict(permitted) if permitted else None), None
    if permitted is None:
        return dict(asked), None
    if asked.get("currency") != permitted.get("currency"):
        return None, "COST_CEILING_CURRENCY_MISMATCH"
    lower = asked if asked.get("amount", 0) <= permitted.get("amount", 0) else permitted
    return dict(lower), None


def _deadline_ceiling(request: GateRequest) -> str | None:
    """The earlier of the caller's deadline and the policy's.

    An unparseable value loses to the other rather than being trusted; if both
    are unparseable the caller's is carried through unchanged, because the gate
    is not the component that validates the ``date-time`` format -- RK-01 already
    asserted it against the frozen schema.
    """
    asked = request.constraints.deadline
    permitted = request.policy.max_deadline
    if asked is None:
        return permitted
    if permitted is None:
        return asked
    try:
        parsed_asked = datetime.fromisoformat(asked.replace("Z", "+00:00"))
        parsed_permitted = datetime.fromisoformat(permitted.replace("Z", "+00:00"))
    except ValueError:
        return permitted
    return asked if parsed_asked <= parsed_permitted else permitted


def _first_failing(
    request: GateRequest, contracts: FrozenContracts, lifecycle: Mapping[str, Any]
) -> tuple[str, str] | None:
    """Walk the ordered rules and return the first match, or ``None``.

    The order is :data:`reasons.REASONS`, not this function's control flow: each
    branch below appears in the same sequence, and a test asserts the two agree.
    """
    record = request.record
    mode = request.execution_mode
    status = lifecycle["status"]

    malformed = _malformed(request)
    if malformed is not None:
        return "MALFORMED_GATE_INPUT", malformed

    if mode not in contracts.execution_modes:
        return "UNKNOWN_EXECUTION_MODE", "execution mode %r is not in the frozen enum %s" % (
            mode,
            list(contracts.execution_modes),
        )

    if status not in contracts.lifecycle_statuses:
        return "UNKNOWN_LIFECYCLE_STATUS", (
            "lifecycle_status %r is not in the frozen ladder %s"
            % (status, list(contracts.lifecycle_statuses))
        )

    if request.approval.state not in contracts.approval_states:
        return "UNKNOWN_APPROVAL_STATE", (
            "approval state %r is not in the frozen enum %s"
            % (request.approval.state, list(contracts.approval_states))
        )

    for gate in request.external_gates:
        if gate.state not in contracts.external_gate_states:
            return "UNKNOWN_EXTERNAL_GATE_STATE", (
                "external gate %r carries state %r, which is not in the frozen enum %s"
                % (gate.gate_id, gate.state, list(contracts.external_gate_states))
            )

    if LEGACY_APPROVAL_FIELD in record:
        return "LEGACY_APPROVAL_FIELD_PRESENT", (
            "the record declares the legacy %s field" % LEGACY_APPROVAL_FIELD
        )

    declared_fallback = record.get("fallback_policy", "none")
    if declared_fallback not in contracts.fallback_policies:
        return "FALLBACK_POLICY_NOT_FROZEN", (
            "fallback policy %r is not in the frozen enum %s"
            % (declared_fallback, list(contracts.fallback_policies))
        )

    if request.policy.caller_may_override:
        return "CALLER_OVERRIDE_REJECTED", (
            "the policy context asserts caller_may_override; eligibility is derived "
            "internally, never by the caller"
        )

    ceiling = CEILINGS.get(status, (False, False))
    if lifecycle["routing_eligible"] and not ceiling[0]:
        return "LIFECYCLE_FLAGS_EXCEED_STAGE_CEILING", (
            "stage %s permits routing_eligible: false, and the record declares true" % status
        )
    if lifecycle["production_eligible"] and not ceiling[1]:
        return "LIFECYCLE_FLAGS_EXCEED_STAGE_CEILING", (
            "stage %s permits production_eligible: false, and the record declares true" % status
        )

    permission = cell(status, mode)
    if permission == DENIED:
        return "LIFECYCLE_MODE_DENIED", (
            "the matrix marks stage %s as 'no' in mode %s, at any stage of governance"
            % (status, mode)
        )

    # Two authorities meet here and the stricter reading wins.
    #
    # Section 3 marks only FEATURE and PROTOTYPE as "permitted, governed", so on
    # the matrix alone a VALIDATED certification run would need no authorization.
    # `runtime-route-decision.schema.json` allOf rule 7 is broader: EVERY ROUTED
    # decision in certification mode requires `certification_authorization_ref`.
    #
    # Its own $comment gives the reason, and the reason does not depend on the
    # stage: certification "is the one mode whose ROUTED guard consults neither
    # lifecycle flag - without a recorded authorization it would be an unaudited
    # bypass of the ladder." That is equally true at VALIDATED.
    #
    # So the gate requires the authorization for the whole of certification, and
    # for any "permitted, governed" cell wherever one is added later. Requiring it
    # in only the matrix cells would leave RK-02 unable to emit a valid artifact
    # and raising instead of blocking -- a crash where a refusal belongs.
    governed = permission == PERMITTED_GOVERNED or mode == "certification"
    if governed and not request.certification_authorization_ref:
        return "CERTIFICATION_AUTHORIZATION_MISSING", (
            "mode %s at stage %s requires a recorded certification authorization "
            "naming the skill, the mode and the gate; without one it would be an "
            "unaudited bypass of the ladder" % (mode, status)
        )

    if mode in CALLER_TRAFFIC_MODES and not lifecycle["routing_eligible"]:
        return "ROUTING_ELIGIBLE_REQUIRED", (
            "mode %s consults routing_eligible, and the record declares false" % mode
        )

    if mode == "production" and not lifecycle["production_eligible"]:
        return "PRODUCTION_ELIGIBLE_REQUIRED", (
            "mode production additionally consults production_eligible, and the "
            "record declares false"
        )

    if request.policy.decision != POLICY_ALLOW:
        return "POLICY_DENIED", "policy %s v%s returned %s" % (
            request.policy.policy_id,
            request.policy.policy_version,
            request.policy.decision,
        )

    # Evaluated with no reference to approval whatsoever, and before it, so an
    # APPROVED approval can never reach a blocked gate. Invariant 5.
    blocking = [gate for gate in request.external_gates if gate.state != PASSING_GATE_STATE]
    if blocking:
        return "EXTERNAL_GATE_NOT_CLEAR", "external gate(s) not CLEAR: %s" % ", ".join(
            "%s=%s" % (gate.gate_id, gate.state) for gate in blocking
        )

    if request.approval.required:
        if not request.approval.decision_is_governance_approval:
            return "DOMAIN_DECISION_IS_NOT_GOVERNANCE_APPROVAL", (
                "the approval state was supplied by a domain decision, which grants "
                "no governance authority"
            )
        if request.approval.state not in PASSING_APPROVAL_STATES:
            return "APPROVAL_NOT_GRANTED", (
                "approval is required and its state is %s" % request.approval.state
            )

    _, cost_failure = _cost_ceiling(request)
    if cost_failure is not None:
        return cost_failure, (
            "the request names %s and the policy ceiling names %s"
            % (
                (request.constraints.max_cost or {}).get("currency"),
                (request.policy.max_cost or {}).get("currency"),
            )
        )

    return None


def _denied_on_malformed_input(
    request: GateRequest, failure: tuple[str, str], lifecycle: Mapping[str, Any]
) -> GateDecision:
    """A denial built from only what could be read.

    No ceilings and no snapshot hash: both would have to be invented from input
    the gate has just said it cannot read.
    """
    reason_code, detail = failure
    inputs_hash = fingerprint(
        {
            "algorithm_version": ALGORITHM_VERSION,
            "malformed": detail,
            "policy_id": request.policy.policy_id,
            "policy_version": request.policy.policy_version,
        }
    )
    return GateDecision(
        verdict=BLOCK,
        reason_code=reason_code,
        reason=detail,
        outcome=vocabulary.outcome_for(reason_code),
        policy_decision=POLICY_DENY,
        policy_id=request.policy.policy_id,
        policy_version=request.policy.policy_version,
        policy_snapshot_hash="",
        decision_inputs_hash=inputs_hash,
        decision_hash=fingerprint({"inputs": inputs_hash, "verdict": BLOCK}),
        decision_ref="%s@%s#%s"
        % (
            request.policy.policy_id,
            request.policy.policy_version,
            fingerprint({"inputs": inputs_hash, "verdict": BLOCK})[:16],
        ),
        algorithm_version=ALGORITHM_VERSION,
        deadline_ceiling=None,
        cost_ceiling=None,
        lifecycle=dict(lifecycle),
    )


def evaluate(request: GateRequest, contracts: FrozenContracts) -> GateDecision:
    """Decide. Pure: same inputs, same verdict, same hash."""
    lifecycle = _lifecycle_of(request.record)
    failure = _first_failing(request, contracts, lifecycle)

    if failure is not None and failure[0] == "MALFORMED_GATE_INPUT":
        # The input is unreadable, so the ceilings and the snapshot hash cannot be
        # computed from it. Deny with what is knowable and nothing invented.
        return _denied_on_malformed_input(request, failure, lifecycle)

    cost_ceiling, _ = _cost_ceiling(request)
    deadline_ceiling = _deadline_ceiling(request)
    snapshot_hash = fingerprint(dict(request.policy.snapshot))

    inputs = {
        "algorithm_version": ALGORITHM_VERSION,
        "approval": {
            "decision_is_governance_approval": (
                request.approval.decision_is_governance_approval
            ),
            "required": request.approval.required,
            "state": request.approval.state,
        },
        "constraints": {
            "data_classification": request.constraints.data_classification,
            "deadline": request.constraints.deadline,
            "max_cost": dict(request.constraints.max_cost)
            if request.constraints.max_cost
            else None,
        },
        "certification_authorization_ref": request.certification_authorization_ref,
        "execution_mode": request.execution_mode,
        # Sorted by gate id so the hash does not depend on the order the host
        # happened to read them in. Sorting the dicts themselves raised
        # TypeError on any request carrying two gates -- an ordinary case, and
        # the gate crashed on it.
        "external_gates": [
            {"gate_id": gate.gate_id, "gate_type": gate.gate_type, "state": gate.state}
            for gate in sorted(request.external_gates, key=lambda g: (g.gate_id, g.state))
        ],
        "fallback_policy": request.record.get("fallback_policy", "none"),
        "lifecycle": dict(lifecycle),
        # Policy IDENTITY and snapshot hash, never the snapshot itself.
        "policy": {
            "caller_may_override": request.policy.caller_may_override,
            "decision": request.policy.decision,
            "policy_id": request.policy.policy_id,
            "policy_snapshot_hash": snapshot_hash,
            "policy_version": request.policy.policy_version,
        },
        "record_id": request.record.get("record_id"),
    }
    decision_inputs_hash = fingerprint(inputs)

    if failure is None:
        verdict, reason_code, outcome = ALLOW, None, None
        detail = "permitted: stage %s in mode %s, with every governance state satisfied" % (
            lifecycle["status"],
            request.execution_mode,
        )
        policy_decision = request.policy.decision
    else:
        reason_code, detail = failure
        verdict = BLOCK
        outcome = vocabulary.outcome_for(reason_code)
        # `policy.decision` records what the POLICY said. A lifecycle, approval or
        # gate block is not a policy denial, and writing DENY there would put a
        # verdict into the audit record that no policy authority gave.
        policy_decision = (
            POLICY_DENY if outcome == vocabulary.BLOCKED_POLICY else request.policy.decision
        )

    decision_hash = fingerprint(
        {
            "decision_inputs_hash": decision_inputs_hash,
            "outcome": outcome,
            "policy_decision": policy_decision,
            "reason_code": reason_code,
            "verdict": verdict,
        }
    )

    return GateDecision(
        verdict=verdict,
        reason_code=reason_code,
        reason=detail,
        outcome=outcome,
        policy_decision=policy_decision,
        policy_id=request.policy.policy_id,
        policy_version=request.policy.policy_version,
        policy_snapshot_hash=snapshot_hash,
        decision_inputs_hash=decision_inputs_hash,
        decision_hash=decision_hash,
        decision_ref="%s@%s#%s"
        % (request.policy.policy_id, request.policy.policy_version, decision_hash[:16]),
        algorithm_version=ALGORITHM_VERSION,
        deadline_ceiling=deadline_ceiling,
        cost_ceiling=cost_ceiling,
        lifecycle=dict(lifecycle),
    )
