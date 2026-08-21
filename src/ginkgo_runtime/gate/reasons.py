"""The structured reason vocabulary, in precedence order.

The shape is the one `ai-stack#41` says to reuse, already proven on ``master`` by
``architecture/policy/knowledge-corpus-classification.yaml``:

    **default: DENY.** Ordered evaluation: the FIRST rule that matches decides and
    ends. No later rule can reverse an earlier decision.

So this table *is* the algorithm's order, not documentation of it -- :func:`order`
is what the gate iterates. Reading the list top to bottom tells you exactly which
reason a given input will produce, and the reason a caller sees is the *first*
thing wrong with the request rather than an arbitrary one.

Each entry also fixes the outcome code the Domain Router records. That mapping
lives here, once, because a reason and the outcome it justifies must not be able
to drift apart: reporting ``BLOCKED_APPROVAL`` for a gate failure would name the
wrong cause and imply an approval would fix it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "BLOCKED_APPROVAL",
    "BLOCKED_EXTERNAL_GATE",
    "BLOCKED_LIFECYCLE",
    "BLOCKED_POLICY",
    "REASONS",
    "Reason",
    "order",
    "outcome_for",
]

#: ``runtime-route-decision.schema.json`` ``resolved.outcome``, the blocking half.
BLOCKED_LIFECYCLE = "BLOCKED_LIFECYCLE"
BLOCKED_POLICY = "BLOCKED_POLICY"
BLOCKED_APPROVAL = "BLOCKED_APPROVAL"
BLOCKED_EXTERNAL_GATE = "BLOCKED_EXTERNAL_GATE"


@dataclass(frozen=True)
class Reason:
    """One rule in the ordered precedence."""

    code: str
    outcome: str
    explanation: str


#: In evaluation order. The first match decides and ends.
REASONS: tuple[Reason, ...] = (
    # -- structural: input the gate cannot read at all ------------------------
    Reason(
        "MALFORMED_GATE_INPUT",
        BLOCKED_POLICY,
        "An input is not the shape the gate requires -- a record that is not a "
        "mapping, an external gate that is not an ExternalGateState, a cost "
        "ceiling with a non-numeric or absent amount. Every one of these used to "
        "raise out of `evaluate`, which produces no decision at all: a crash is "
        "not fail-closed, and a caller sees an internal error where a BLOCK "
        "belongs.",
    ),
    # -- structural: a vocabulary the gate cannot read is a denial -----------
    Reason(
        "UNKNOWN_EXECUTION_MODE",
        BLOCKED_POLICY,
        "The execution mode is outside the enum frozen in "
        "runtime-route-decision.schema.json. A mode the gate cannot recognise has "
        "no row in the matrix, and a missing row must never read as permission.",
    ),
    Reason(
        "UNKNOWN_LIFECYCLE_STATUS",
        BLOCKED_LIFECYCLE,
        "The lifecycle status is outside the frozen ladder. It is never mapped "
        "onto a legacy maturity code, and an unreadable stage denies.",
    ),
    Reason(
        "UNKNOWN_APPROVAL_STATE",
        BLOCKED_APPROVAL,
        "The approval state is outside the enum frozen in "
        "contracts/core/approval.schema.json.",
    ),
    Reason(
        "UNKNOWN_EXTERNAL_GATE_STATE",
        BLOCKED_EXTERNAL_GATE,
        "An external gate state is outside the enum frozen in "
        "contracts/core/external-gate.schema.json.",
    ),
    Reason(
        "LEGACY_APPROVAL_FIELD_PRESENT",
        BLOCKED_APPROVAL,
        "The record carries the legacy `human_approval` field. It is not accepted, "
        "and its value `supervised` has no image in the frozen approval enum, so "
        "reading it would fabricate a governance state.",
    ),
    Reason(
        "FALLBACK_POLICY_NOT_FROZEN",
        BLOCKED_POLICY,
        "The fallback policy is outside the frozen enum [none, same_semantics_only]. "
        "`explicit` lives in merged code in ginkgo-agents and is not a frozen policy.",
    ),
    Reason(
        "CALLER_OVERRIDE_REJECTED",
        BLOCKED_POLICY,
        "The context asserts a caller-supplied policy override. "
        "`caller_may_override` is false and is not a switch: eligibility is derived "
        "internally by Identity/Policy, never by the caller (invariants 17, 18).",
    ),
    # -- lifecycle ----------------------------------------------------------
    Reason(
        "LIFECYCLE_FLAGS_EXCEED_STAGE_CEILING",
        BLOCKED_LIFECYCLE,
        "The record declares an eligibility flag its stage does not permit. The "
        "stage is a ceiling; a record may be more restrictive than its stage "
        "allows and never more permissive.",
    ),
    Reason(
        "LIFECYCLE_MODE_DENIED",
        BLOCKED_LIFECYCLE,
        "The matrix marks this stage `no` in this mode, and section 3 defines `no` "
        "as `at any stage of governance`. No authorization, approval or policy "
        "reaches it.",
    ),
    Reason(
        "CERTIFICATION_AUTHORIZATION_MISSING",
        BLOCKED_LIFECYCLE,
        "The cell is `permitted, governed`, which additionally requires a recorded "
        "authorization naming the skill, the mode and the gate the run exists to "
        "satisfy. Without one this would be an unaudited bypass of the ladder.",
    ),
    Reason(
        "ROUTING_ELIGIBLE_REQUIRED",
        BLOCKED_LIFECYCLE,
        "Caller traffic consults `routing_eligible`, and the record declares false. "
        "The stage permitting it is not the record claiming it.",
    ),
    Reason(
        "PRODUCTION_ELIGIBLE_REQUIRED",
        BLOCKED_LIFECYCLE,
        "`production` additionally consults `production_eligible`, and the record "
        "declares false.",
    ),
    # -- the supplied policy verdict ----------------------------------------
    Reason(
        "POLICY_DENIED",
        BLOCKED_POLICY,
        "The policy context carries DENY. RK-04 records that verdict; it does not "
        "re-derive business policy, and it never overturns a denial.",
    ),
    # -- external gates, evaluated BEFORE approval and independently --------
    Reason(
        "EXTERNAL_GATE_NOT_CLEAR",
        BLOCKED_EXTERNAL_GATE,
        "An external gate is not CLEAR. Invariant 5: `APPROVED + BLOCKED => "
        "BLOCKED` and `APPROVED + UNKNOWN => BLOCKED`. Verbatim from "
        "PROJETO/AGENT_EXECUTION_STANDARD.md section 2: 'Reviewer is not an "
        "External Gate and cannot clear one. Human/business Approval is also "
        "separate from External Gate state. A successful review cannot convert "
        "external_gate=BLOCKED into an executable route.'",
    ),
    # -- approval -----------------------------------------------------------
    Reason(
        "DOMAIN_DECISION_IS_NOT_GOVERNANCE_APPROVAL",
        BLOCKED_APPROVAL,
        "The approval state was supplied by something that declares "
        "`decision_is_governance_approval: false`. `commerce.product.evaluate`'s "
        "economic APPROVED is the concrete case: it grants no authority to "
        "publish, purchase, mutate pricing or inventory, or fulfil orders.",
    ),
    Reason(
        "APPROVAL_NOT_GRANTED",
        BLOCKED_APPROVAL,
        "Approval is required and its state is neither NOT_REQUIRED nor APPROVED.",
    ),
    # -- ceilings -----------------------------------------------------------
    Reason(
        "COST_CEILING_CURRENCY_MISMATCH",
        BLOCKED_POLICY,
        "The request and the policy ceiling name different currencies. The gate "
        "converts nothing: an implicit rate would be an invented number in a cost "
        "ceiling that RK-08A then enforces.",
    ),
    # -- the terminal default ------------------------------------------------
    Reason(
        "UNCOVERED",
        BLOCKED_POLICY,
        "No rule permitted this input. The default is DENY, so an input nobody "
        "anticipated is refused rather than admitted by omission.",
    ),
)

_BY_CODE: Mapping[str, Reason] = {reason.code: reason for reason in REASONS}


def order() -> tuple[str, ...]:
    """The precedence, as codes. The gate iterates this."""
    return tuple(reason.code for reason in REASONS)


def outcome_for(code: str) -> str:
    """The route outcome a reason justifies.

    An unknown code maps to ``BLOCKED_POLICY`` rather than raising: this is the
    fail-closed component, and a reason it cannot classify is still a denial.
    """
    reason = _BY_CODE.get(code)
    return BLOCKED_POLICY if reason is None else reason.outcome


def explanation_for(code: str) -> str:
    reason = _BY_CODE.get(code)
    return "" if reason is None else reason.explanation
