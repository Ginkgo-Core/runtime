"""The gate's own rules: N9, N10, N11, the independence rule, and every unknown.

`ai-stack#41`'s Done-when is exactly three negatives from ``reference-slice.md``
section 5 — the invariant-16 test and both invariant-5 tests. They come first.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.gate import (
    ALGORITHM_VERSION,
    ALLOW,
    BLOCK,
    REASONS,
    ApprovalState,
    ConstraintContext,
    ExternalGateState,
    order,
    outcome_for,
)

from .conftest import gate_policy, gate_record, gate_request

# -- N9, N10, N11 -----------------------------------------------------------


def test_n9_feature_asked_to_route_in_production_is_blocked(gate):
    """N9. Invariant 16, the one lifecycle rule frozen in ``master``:
    *"`FEATURE` never executes in production."*"""
    decision = gate(
        gate_request(record=gate_record("FEATURE"), execution_mode="production")
    )
    assert decision.verdict == BLOCK
    assert decision.outcome == "BLOCKED_LIFECYCLE"
    assert decision.reason_code == "LIFECYCLE_MODE_DENIED"


def test_n10_approved_plus_blocked_gate_is_blocked(gate):
    """N10. Invariant 5: ``APPROVED + BLOCKED => BLOCKED``."""
    decision = gate(
        gate_request(
            approval=ApprovalState(required=True, state="APPROVED"),
            external_gates=(
                ExternalGateState(gate_id="shopee_business_approval", state="BLOCKED"),
            ),
        )
    )
    assert decision.verdict == BLOCK
    assert decision.outcome == "BLOCKED_EXTERNAL_GATE"
    assert decision.reason_code == "EXTERNAL_GATE_NOT_CLEAR"


def test_n11_approved_plus_unknown_gate_is_blocked(gate):
    """N11. Invariant 5: ``APPROVED + UNKNOWN => BLOCKED``.

    An unknown gate state is the case fail-closed exists for. Treating it as
    "probably fine" is how a gate stops being a gate.
    """
    decision = gate(
        gate_request(
            approval=ApprovalState(required=True, state="APPROVED"),
            external_gates=(ExternalGateState(gate_id="g", state="UNKNOWN"),),
        )
    )
    assert decision.verdict == BLOCK
    assert decision.outcome == "BLOCKED_EXTERNAL_GATE"


# -- the independence rule --------------------------------------------------


@pytest.mark.parametrize("gate_state", ["BLOCKED", "UNKNOWN", "EXPIRED"])
@pytest.mark.parametrize("approval_state", ["NOT_REQUIRED", "APPROVED"])
def test_no_approval_state_clears_a_gate(gate, gate_state, approval_state):
    """*"A successful review cannot convert `external_gate=BLOCKED` into an
    executable route."* — ``PROJETO/AGENT_EXECUTION_STANDARD.md`` section 2."""
    decision = gate(
        gate_request(
            approval=ApprovalState(required=True, state=approval_state),
            external_gates=(ExternalGateState(gate_id="g", state=gate_state),),
        )
    )
    assert decision.outcome == "BLOCKED_EXTERNAL_GATE"


def test_a_gate_is_named_over_an_approval_when_both_would_block(gate):
    """The outcome names the cause that cannot be fixed by the other.

    Reporting ``BLOCKED_APPROVAL`` here would imply that granting the approval
    would unblock the route. It would not.
    """
    decision = gate(
        gate_request(
            approval=ApprovalState(required=True, state="PENDING"),
            external_gates=(ExternalGateState(gate_id="g", state="BLOCKED"),),
        )
    )
    assert decision.outcome == "BLOCKED_EXTERNAL_GATE"


def test_a_clear_gate_does_not_block(gate):
    decision = gate(
        gate_request(external_gates=(ExternalGateState(gate_id="g", state="CLEAR"),))
    )
    assert decision.verdict == ALLOW


def test_every_gate_is_evaluated_not_only_the_first(gate):
    decision = gate(
        gate_request(
            external_gates=(
                ExternalGateState(gate_id="a", state="CLEAR"),
                ExternalGateState(gate_id="b", state="BLOCKED"),
            )
        )
    )
    assert decision.outcome == "BLOCKED_EXTERNAL_GATE"
    assert "b=BLOCKED" in decision.reason
    assert "a=CLEAR" not in decision.reason


# -- approval ---------------------------------------------------------------


@pytest.mark.parametrize("state", ["PENDING", "REJECTED", "EXPIRED"])
def test_a_required_approval_that_is_not_granted_blocks(gate, state):
    decision = gate(gate_request(approval=ApprovalState(required=True, state=state)))
    assert decision.verdict == BLOCK
    assert decision.outcome == "BLOCKED_APPROVAL"
    assert decision.reason_code == "APPROVAL_NOT_GRANTED"


@pytest.mark.parametrize("state", ["NOT_REQUIRED", "APPROVED"])
def test_a_satisfied_approval_does_not_block(gate, state):
    assert gate(gate_request(approval=ApprovalState(required=True, state=state))).verdict == ALLOW


def test_an_approval_that_is_not_required_is_not_consulted(gate):
    """A stale PENDING on a record that requires no approval blocks nothing."""
    decision = gate(gate_request(approval=ApprovalState(required=False, state="PENDING")))
    assert decision.verdict == ALLOW


def test_a_domain_decision_named_approved_is_not_a_governance_approval(gate):
    """`commerce.product.evaluate`'s economic APPROVED is the concrete case.

    It grants no authority to publish, purchase, mutate pricing or inventory, or
    fulfil orders. Reading it as governance approval would let a domain
    calculation authorise a side effect.
    """
    decision = gate(
        gate_request(
            approval=ApprovalState(
                required=True,
                state="APPROVED",
                decision_is_governance_approval=False,
            )
        )
    )
    assert decision.verdict == BLOCK
    assert decision.reason_code == "DOMAIN_DECISION_IS_NOT_GOVERNANCE_APPROVAL"
    assert decision.outcome == "BLOCKED_APPROVAL"


def test_the_legacy_approval_field_is_not_accepted(gate):
    for value in (False, True, "supervised"):
        decision = gate(gate_request(record=gate_record(human_approval=value)))
        assert decision.verdict == BLOCK
        assert decision.reason_code == "LEGACY_APPROVAL_FIELD_PRESENT"


# -- unknown is DENY --------------------------------------------------------


def test_an_unknown_execution_mode_denies(gate):
    decision = gate(gate_request(execution_mode="staging"))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "UNKNOWN_EXECUTION_MODE"


def test_an_unknown_lifecycle_status_denies(gate):
    decision = gate(gate_request(record=gate_record("S2-INTEGRATED")))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "UNKNOWN_LIFECYCLE_STATUS"


def test_an_unknown_approval_state_denies(gate):
    decision = gate(gate_request(approval=ApprovalState(required=True, state="supervised")))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "UNKNOWN_APPROVAL_STATE"


def test_an_unknown_gate_state_denies(gate):
    decision = gate(
        gate_request(external_gates=(ExternalGateState(gate_id="g", state="PROBABLY"),))
    )
    assert decision.verdict == BLOCK
    assert decision.reason_code == "UNKNOWN_EXTERNAL_GATE_STATE"


def test_a_missing_lifecycle_status_is_read_as_feature(gate):
    """Section 4 invariant 8, and `ai-stack#41` repeats it.

    Not a denial in itself -- FEATURE then denies caller traffic on its own,
    which is the point of defaulting there rather than to a legacy maturity code.
    """
    # Built at FEATURE's own flags, so the block is attributable to the default
    # rather than to a VALIDATED record's flags surviving the deletion.
    record = gate_record(routing_eligible=False)
    del record["lifecycle_status"]
    decision = gate(gate_request(record=record, execution_mode="non_production"))

    assert decision.lifecycle["status"] == "FEATURE"
    assert decision.reason_code == "LIFECYCLE_MODE_DENIED"


def test_a_missing_eligibility_flag_is_false(gate):
    """Section 4 invariant 5: *"a missing or unreadable production_eligible is false"*."""
    record = gate_record("PRODUCTION")
    del record["routing"]
    decision = gate(gate_request(record=record, execution_mode="production"))

    assert decision.lifecycle["routing_eligible"] is False
    assert decision.lifecycle["production_eligible"] is False
    assert decision.reason_code == "ROUTING_ELIGIBLE_REQUIRED"


def test_a_non_frozen_fallback_policy_denies(gate):
    """``fallbacks.policy: explicit`` lives in merged code and is not frozen."""
    decision = gate(gate_request(record=gate_record(fallback_policy="explicit")))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "FALLBACK_POLICY_NOT_FROZEN"


def test_a_caller_override_is_refused(gate):
    """``caller_may_override`` is an assertion, not a switch."""
    decision = gate(gate_request(policy=gate_policy(caller_may_override=True)))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "CALLER_OVERRIDE_REJECTED"


# -- the eligibility flags --------------------------------------------------


def test_a_record_more_restrictive_than_its_stage_is_honoured(gate):
    """*"A record may be more restrictive than its stage allows, and often is."*

    ``VALIDATED`` permits ``routing_eligible``; this record declines it, and the
    gate honours the record over the stage.
    """
    decision = gate(
        gate_request(record=gate_record("VALIDATED", routing_eligible=False))
    )
    assert decision.verdict == BLOCK
    assert decision.reason_code == "ROUTING_ELIGIBLE_REQUIRED"


def test_a_record_more_permissive_than_its_stage_is_refused(gate):
    """The stage is a ceiling. A FEATURE record claiming routability is malformed."""
    decision = gate(
        gate_request(record=gate_record("FEATURE", routing_eligible=True))
    )
    assert decision.verdict == BLOCK
    assert decision.reason_code == "LIFECYCLE_FLAGS_EXCEED_STAGE_CEILING"


def test_production_eligible_above_its_ceiling_is_refused(gate):
    decision = gate(
        gate_request(
            record=gate_record("INTEGRATED", routing_eligible=True, production_eligible=True)
        )
    )
    assert decision.reason_code == "LIFECYCLE_FLAGS_EXCEED_STAGE_CEILING"


def test_certification_does_not_consult_either_flag(gate):
    """The escape from the circularity. A FEATURE record certifies.

    Without it a record would need the certificate to become routable and need to
    run to earn the certificate.
    """
    decision = gate(
        gate_request(
            record=gate_record("FEATURE"),
            execution_mode="certification",
            certification_authorization_ref="cert-auth-0001",
        )
    )
    assert decision.verdict == ALLOW
    assert decision.lifecycle["routing_eligible"] is False


def test_certification_requires_a_recorded_authorization(gate):
    """Two authorities reconciled to the stricter reading.

    Section 3 marks only FEATURE and PROTOTYPE "permitted, governed", but
    ``runtime-route-decision.schema.json`` allOf rule 7 requires the reference on
    **every** ROUTED certification decision. Its own $comment explains why, and
    the reason does not depend on the stage: certification is the one mode whose
    guard consults neither lifecycle flag.
    """
    for stage in ("FEATURE", "PROTOTYPE", "VALIDATED", "INTEGRATED", "PRODUCTION"):
        decision = gate(
            gate_request(record=gate_record(stage), execution_mode="certification")
        )
        assert decision.reason_code == "CERTIFICATION_AUTHORIZATION_MISSING", stage


# -- the supplied policy verdict --------------------------------------------


def test_a_supplied_denial_is_recorded_not_re_derived(gate):
    decision = gate(gate_request(policy=gate_policy(decision="DENY")))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "POLICY_DENIED"
    assert decision.policy_decision == "DENY"


def test_a_lifecycle_block_leaves_the_policy_decision_alone(gate):
    """No policy denied anything, so ``policy.decision`` stays ``ALLOW``.

    Writing DENY there would record a governance verdict no policy authority
    gave, in the field an auditor reads to find out what policy said.
    """
    decision = gate(
        gate_request(record=gate_record("FEATURE"), execution_mode="production")
    )
    assert decision.verdict == BLOCK
    assert decision.outcome == "BLOCKED_LIFECYCLE"
    assert decision.policy_decision == "ALLOW"


def test_a_gate_block_leaves_the_policy_decision_alone(gate):
    decision = gate(
        gate_request(external_gates=(ExternalGateState(gate_id="g", state="BLOCKED"),))
    )
    assert decision.policy_decision == "ALLOW"


def test_the_gate_never_overturns_a_denial(gate):
    """Even with every other condition perfect."""
    decision = gate(
        gate_request(
            record=gate_record("PRODUCTION", production_eligible=True),
            execution_mode="production",
            policy=gate_policy(decision="DENY"),
            approval=ApprovalState(required=True, state="APPROVED"),
            external_gates=(ExternalGateState(gate_id="g", state="CLEAR"),),
        )
    )
    assert decision.verdict == BLOCK
    assert decision.reason_code == "POLICY_DENIED"


# -- ceilings ---------------------------------------------------------------


def test_the_more_restrictive_cost_ceiling_wins(gate):
    decision = gate(
        gate_request(
            policy=gate_policy(max_cost={"amount": 100, "currency": "BRL"}),
            constraints=ConstraintContext(max_cost={"amount": 20, "currency": "BRL"}),
        )
    )
    assert decision.cost_ceiling == {"amount": 20, "currency": "BRL"}


def test_a_policy_ceiling_below_the_request_wins(gate):
    decision = gate(
        gate_request(
            policy=gate_policy(max_cost={"amount": 5, "currency": "BRL"}),
            constraints=ConstraintContext(max_cost={"amount": 20, "currency": "BRL"}),
        )
    )
    assert decision.cost_ceiling == {"amount": 5, "currency": "BRL"}


def test_mismatched_currencies_are_refused_rather_than_converted(gate):
    """An implicit rate would put an invented number into a ceiling RK-08A enforces."""
    decision = gate(
        gate_request(
            policy=gate_policy(max_cost={"amount": 100, "currency": "USD"}),
            constraints=ConstraintContext(max_cost={"amount": 20, "currency": "BRL"}),
        )
    )
    assert decision.verdict == BLOCK
    assert decision.reason_code == "COST_CEILING_CURRENCY_MISMATCH"


def test_the_earlier_deadline_wins(gate):
    decision = gate(
        gate_request(
            policy=gate_policy(max_deadline="2026-08-21T18:00:00Z"),
            constraints=ConstraintContext(deadline="2026-08-21T12:00:00Z"),
        )
    )
    assert decision.deadline_ceiling == "2026-08-21T12:00:00Z"

    decision = gate(
        gate_request(
            policy=gate_policy(max_deadline="2026-08-21T12:00:00Z"),
            constraints=ConstraintContext(deadline="2026-08-21T18:00:00Z"),
        )
    )
    assert decision.deadline_ceiling == "2026-08-21T12:00:00Z"


def test_a_ceiling_declared_by_only_one_side_is_carried_through(gate):
    assert gate(
        gate_request(constraints=ConstraintContext(deadline="2026-08-21T12:00:00Z"))
    ).deadline_ceiling == "2026-08-21T12:00:00Z"
    assert gate(
        gate_request(policy=gate_policy(max_deadline="2026-08-21T12:00:00Z"))
    ).deadline_ceiling == "2026-08-21T12:00:00Z"
    assert gate(gate_request()).deadline_ceiling is None


# -- the ordered vocabulary -------------------------------------------------


def test_the_rule_order_is_the_vocabulary_order(gate):
    """The table in ``reasons.py`` *is* the algorithm's precedence.

    Each reason is provoked alongside every reason below it, and must win. That
    is what "first match decides and ends" means, and it is why a caller sees the
    first thing wrong rather than an arbitrary one.
    """
    denial = gate_policy(decision="DENY", caller_may_override=True)
    record = gate_record("S2", fallback_policy="explicit", human_approval="supervised")
    decision = gate(
        gate_request(
            record=record,
            execution_mode="staging",
            policy=denial,
            approval=ApprovalState(required=True, state="supervised"),
            external_gates=(ExternalGateState(gate_id="g", state="PROBABLY"),),
        )
    )
    assert decision.reason_code == "UNKNOWN_EXECUTION_MODE"

    decision = gate(
        gate_request(
            record=record,
            execution_mode="non_production",
            policy=denial,
            approval=ApprovalState(required=True, state="supervised"),
            external_gates=(ExternalGateState(gate_id="g", state="PROBABLY"),),
        )
    )
    assert decision.reason_code == "UNKNOWN_LIFECYCLE_STATUS"


def test_every_reason_maps_to_a_blocked_outcome():
    for reason in REASONS:
        assert reason.outcome.startswith("BLOCKED_")
        assert outcome_for(reason.code) == reason.outcome


def test_an_unclassifiable_reason_still_denies():
    assert outcome_for("SOMETHING_NOBODY_DECLARED") == "BLOCKED_POLICY"


def test_the_vocabulary_ends_with_the_terminal_default():
    """The prior art's shape: an explicit terminal ``unknown`` rule that denies."""
    assert order()[-1] == "UNCOVERED"


def test_every_reason_code_is_unique():
    codes = [reason.code for reason in REASONS]
    assert len(codes) == len(set(codes))


def test_an_allow_carries_no_reason_code(gate):
    decision = gate(gate_request())
    assert decision.verdict == ALLOW
    assert decision.reason_code is None
    assert decision.outcome is None
    assert decision.reason


def test_the_algorithm_version_is_recorded(gate):
    assert gate(gate_request()).algorithm_version == ALGORITHM_VERSION
