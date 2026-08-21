"""Purity, and the adversarial-review regressions.

`ai-stack#41` calls RK-04 *"the component that makes the kernel fail-closed"* and
requires it to be a **pure function**. Both claims are load-bearing and both are
easy to hold only approximately, so they are asserted rather than asserted-about.

The five defects fixed here shared a shape: the gate **raised** on input it could
not read. Raising is not failing closed -- it produces no decision at all, so a
caller sees an internal error exactly where a ``BLOCK`` belongs.
"""

from __future__ import annotations

import io
import os
import tokenize

import pytest

from ginkgo_runtime.gate import (
    ALLOW,
    BLOCK,
    ApprovalState,
    ConstraintContext,
    ExternalGateState,
    GateRequest,
    order,
)

from .conftest import REPO_ROOT, gate_policy, gate_record, gate_request

PACKAGE = os.path.join(REPO_ROOT, "src", "ginkgo_runtime", "gate")


def gate_sources():
    """Every gate module, as executable code only."""
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(PACKAGE, name), encoding="utf-8") as handle:
            text = handle.read()
        words = (tokenize.NAME, tokenize.NUMBER)
        kept: list[str] = []
        previous = None
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING) or not token.string.strip():
                continue
            if previous in words and token.type in words:
                kept.append(" ")
            kept.append(token.string)
            previous = token.type
        yield name, "".join(kept)


def deep_document(levels=3000):
    root: dict = {}
    cursor = root
    for _ in range(levels):
        cursor["n"] = {}
        cursor = cursor["n"]
    return root


# -- purity -----------------------------------------------------------------


def test_the_same_inputs_always_produce_the_same_decision(gate):
    request = gate_request(
        approval=ApprovalState(required=True, state="APPROVED"),
        external_gates=(ExternalGateState(gate_id="g", state="CLEAR"),),
        constraints=ConstraintContext(max_cost={"amount": 5, "currency": "BRL"}),
    )
    decisions = [gate(request) for _ in range(20)]
    assert all(d == decisions[0] for d in decisions)


def test_the_verdict_does_not_depend_on_call_order(gate):
    """No state survives a call, so a denial cannot colour the next decision."""
    allowed = gate_request()
    denied = gate_request(policy=gate_policy(decision="DENY"))

    first = gate(allowed)
    gate(denied)
    gate(denied)
    assert gate(allowed) == first


def test_evaluate_mutates_none_of_its_inputs(gate):
    record = gate_record()
    snapshot = {"rules": [{"id": "r-1"}]}
    gates = (ExternalGateState(gate_id="g", state="CLEAR"),)
    constraints = ConstraintContext(max_cost={"amount": 5, "currency": "BRL"})

    gate(
        GateRequest(
            record=record,
            execution_mode="non_production",
            policy=gate_policy(snapshot=snapshot),
            external_gates=gates,
            constraints=constraints,
        )
    )

    assert record == gate_record()
    assert snapshot == {"rules": [{"id": "r-1"}]}
    assert constraints.max_cost == {"amount": 5, "currency": "BRL"}


def test_the_gate_never_transitions_an_approval_or_clears_a_gate(gate):
    """It reads two state machines and mutates neither."""
    approval = ApprovalState(required=True, state="PENDING")
    gates = (ExternalGateState(gate_id="g", state="BLOCKED"),)

    gate(gate_request(approval=approval, external_gates=gates))

    assert approval.state == "PENDING"
    assert gates[0].state == "BLOCKED"


def test_the_gate_reads_no_clock():
    """A pure function of its inputs cannot consult the time.

    ``datetime`` is imported for deadline *comparison*; ``now`` and ``utcnow``
    are what would make the verdict depend on when it was asked.
    """
    offenders = [
        (name, token)
        for name, code in gate_sources()
        for token in ("datetime.now", "datetime.utcnow", "time.time", "time.monotonic")
        if token in code
    ]
    assert offenders == []


def test_the_gate_performs_no_io():
    offenders = [
        (name, token)
        for name, code in gate_sources()
        for token in ("open(", "import socket", "urllib", "requests", "os.environ", "Path(")
        if token in code
    ]
    assert offenders == []


def test_the_gate_holds_no_mutable_module_state():
    """No module-level container a call -- or anyone else -- could write into.

    Found by this test: ``CEILINGS`` and ``MATRIX`` were plain dicts, so any
    in-process code could rewrite a cell and change what the gate permits,
    globally and silently. Both are ``MappingProxyType`` now, which makes the
    purity claim structural rather than conventional.
    """
    from ginkgo_runtime.gate import gate as gate_module
    from ginkgo_runtime.gate import matrix as matrix_module

    for module in (gate_module, matrix_module):
        mutable = [
            name
            for name, value in vars(module).items()
            if not name.startswith("__") and isinstance(value, list | dict | set)
        ]
        assert mutable == [], module.__name__


def test_the_normative_tables_cannot_be_rewritten():
    from ginkgo_runtime.gate import CEILINGS, MATRIX

    with pytest.raises(TypeError):
        MATRIX[("REJECTED", "production")] = "permitted"
    with pytest.raises(TypeError):
        CEILINGS["FEATURE"] = (True, True)


# -- malformed input denies, never raises -----------------------------------


def test_a_record_that_is_not_a_mapping_denies(gate):
    decision = gate(gate_request(record="not a record"))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "MALFORMED_GATE_INPUT"


def test_an_external_gate_of_the_wrong_type_denies(gate):
    """A host passing raw dicts is a programming error, and still gets a verdict."""
    decision = gate(gate_request(external_gates=({"gate_id": "g", "state": "CLEAR"},)))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "MALFORMED_GATE_INPUT"


@pytest.mark.parametrize(
    "cost",
    [
        {"amount": "100", "currency": "BRL"},
        {"currency": "BRL"},
        {"amount": None, "currency": "BRL"},
        {"amount": True, "currency": "BRL"},
        "twenty",
    ],
)
def test_a_malformed_cost_ceiling_denies(gate, cost):
    """Including ``True``: a boolean is an ``int`` in Python and not a number in JSON."""
    decision = gate(gate_request(constraints=ConstraintContext(max_cost=cost)))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "MALFORMED_GATE_INPUT"


def test_a_malformed_policy_ceiling_denies(gate):
    decision = gate(gate_request(policy=gate_policy(max_cost={"amount": "5", "currency": "BRL"})))
    assert decision.reason_code == "MALFORMED_GATE_INPUT"


def test_an_unhashable_policy_snapshot_denies(gate):
    """A snapshot nested past the encoder's bound raised out of ``evaluate``."""
    decision = gate(gate_request(policy=gate_policy(snapshot=deep_document())))
    assert decision.verdict == BLOCK
    assert decision.reason_code == "MALFORMED_GATE_INPUT"


def test_a_denial_on_malformed_input_invents_no_ceiling(gate):
    """It has just said it cannot read the input; it must not then report one."""
    decision = gate(gate_request(record="not a record"))
    assert decision.cost_ceiling is None
    assert decision.deadline_ceiling is None
    assert decision.policy_snapshot_hash == ""
    assert decision.policy_decision == "DENY"


def test_a_malformed_denial_is_still_deterministic(gate):
    request = gate_request(record="not a record")
    assert gate(request) == gate(request)


def test_malformed_input_is_checked_before_everything_else(gate):
    """It sits first in the precedence, so it wins over every other defect."""
    assert order()[0] == "MALFORMED_GATE_INPUT"

    decision = gate(
        gate_request(
            record="not a record",
            execution_mode="staging",
            policy=gate_policy(decision="DENY", caller_may_override=True),
        )
    )
    assert decision.reason_code == "MALFORMED_GATE_INPUT"


def test_a_policy_snapshot_with_float_thresholds_is_readable(gate):
    """``commerce-evaluation-gates-v1`` declares ``minimum_multiplier: 5.0``."""
    decision = gate(gate_request(policy=gate_policy(snapshot={"minimum_multiplier": 5.0})))
    assert decision.verdict == ALLOW
    assert len(decision.policy_snapshot_hash) == 64


def test_two_external_gates_do_not_crash_the_hash(gate):
    """``sorted()`` over the gate dicts raised ``TypeError`` on any second gate.

    A capability with two gates is ordinary -- ``commerce.orders.read`` would have
    one per marketplace -- so the gate crashed on a case the registry already
    implies.
    """
    decision = gate(
        gate_request(
            external_gates=(
                ExternalGateState(gate_id="b", state="CLEAR"),
                ExternalGateState(gate_id="a", state="CLEAR"),
            )
        )
    )
    assert decision.verdict == ALLOW


def test_gate_order_does_not_change_the_hash(gate):
    """The host's read order is not part of the decision."""
    forwards = gate(
        gate_request(
            external_gates=(
                ExternalGateState(gate_id="a", state="CLEAR"),
                ExternalGateState(gate_id="b", state="CLEAR"),
            )
        )
    )
    backwards = gate(
        gate_request(
            external_gates=(
                ExternalGateState(gate_id="b", state="CLEAR"),
                ExternalGateState(gate_id="a", state="CLEAR"),
            )
        )
    )
    assert forwards.decision_hash == backwards.decision_hash


# -- the hashes -------------------------------------------------------------


def test_the_snapshot_is_hashed_and_never_carried(gate):
    decision = gate(
        gate_request(policy=gate_policy(snapshot={"secret_rule": "AKIA-INTERNAL-RULE"}))
    )
    assert "AKIA-INTERNAL-RULE" not in repr(decision)
    assert len(decision.policy_snapshot_hash) == 64


def test_a_different_snapshot_changes_the_decision_hash(gate):
    first = gate(gate_request(policy=gate_policy(snapshot={"rules": ["a"]})))
    second = gate(gate_request(policy=gate_policy(snapshot={"rules": ["b"]})))
    assert first.policy_snapshot_hash != second.policy_snapshot_hash
    assert first.decision_inputs_hash != second.decision_inputs_hash


def test_a_different_verdict_changes_the_decision_hash(gate):
    allowed = gate(gate_request())
    blocked = gate(gate_request(record=gate_record("FEATURE", routing_eligible=False)))
    assert allowed.decision_hash != blocked.decision_hash


def test_the_decision_ref_pins_the_policy_and_the_hash(gate):
    decision = gate(gate_request())
    assert decision.decision_ref == "rk04-test-policy@1#%s" % decision.decision_hash[:16]


def test_the_gate_never_re_implements_knowledge_corpus_policy():
    """`#19` owns it, and the gate names none of its vocabulary."""
    offenders = [
        (name, token)
        for name, code in gate_sources()
        for token in ("exclusion_globs", "generic_ingestion", "corpus", "vault", "tree_paths")
        if token in code
    ]
    assert offenders == []
